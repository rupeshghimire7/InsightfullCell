"""The LLM gateway, its breaker and pricing, and the four provider adapters (fake SDK clients:
no network calls)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import anthropic
import httpx2
import openai
import pytest
from google.genai import errors as genai_errors
from pydantic import BaseModel, SecretStr

from insightfulcell.config import Settings
from insightfulcell.engine.ports import ModelRequest, ModelUnavailable, Purpose
from insightfulcell.llm.breaker import CircuitBreaker
from insightfulcell.llm.gateway import CallRecord, GatewayConfig, LLMGateway, payload_blocked
from insightfulcell.llm.pricing import cost_usd
from insightfulcell.llm.providers import DEFAULT_MODELS, build_transport, models_for
from insightfulcell.llm.providers.anthropic import AnthropicTransport
from insightfulcell.llm.providers.gemini import GeminiTransport
from insightfulcell.llm.providers.openai_compatible import OpenAICompatibleTransport
from insightfulcell.llm.transport import PermanentProviderError, RawReply, TransientProviderError

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


class Answer(BaseModel):
    answer: str


def request(
    *, run_id: UUID | None = None, user: str = "card", sensitive: frozenset[str] = frozenset()
) -> ModelRequest:
    return ModelRequest(
        purpose=Purpose.PLAN,
        org_id=uuid4(),
        run_id=run_id,
        prompt_id="understand",
        prompt_version="understand-v1",
        system="system",
        user=user,
        response_model=Answer,
        response_schema=SCHEMA,
        sensitive_values=sensitive,
    )


@dataclass
class FakeTransport:
    replies: list[RawReply | Exception]
    provider: str = "gemini"
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def generate(self, **kwargs: Any) -> RawReply:
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@dataclass
class FakeLedger:
    run_tokens: int = 0
    month: tuple[int, int] = (0, 1_000_000)
    calls: list[CallRecord] = field(default_factory=list)

    async def run_input_tokens(self, org_id: UUID, run_id: UUID) -> int:
        return self.run_tokens

    async def month_usage(self, org_id: UUID) -> tuple[int, int]:
        return self.month

    async def record(self, call: CallRecord) -> None:
        self.calls.append(call)


def ok(text: str = '{"answer": "yes"}') -> RawReply:
    return RawReply(text=text, input_tokens=1_000, output_tokens=200, request_id="req-1")


def gateway(
    transport: FakeTransport | None, ledger: FakeLedger | None = None, **config: Any
) -> tuple[LLMGateway, FakeLedger, list[float]]:
    ledger = ledger or FakeLedger()
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    gw = LLMGateway(
        transport,
        ledger,
        GatewayConfig(
            models={"strong": "gemini-3.8-flash", "fast": "gemini-3.5-flash-lite"}, **config
        ),
        CircuitBreaker(threshold=3, open_seconds=60),
        sleep=sleep,
    )
    return gw, ledger, slept


class TestGateway:
    async def test_validates_the_answer_and_accounts_for_the_call(self) -> None:
        transport = FakeTransport([ok()])
        gw, ledger, _ = gateway(transport)
        reply = await gw.complete(request(run_id=uuid4()))
        assert reply.data == Answer(answer="yes")
        assert (reply.provider, reply.model, reply.attempts) == ("gemini", "gemini-3.8-flash", 1)
        assert transport.calls[0]["schema"] == SCHEMA
        (call,) = ledger.calls
        assert (call.outcome, call.input_tokens, call.output_tokens, call.prompt_version) == (
            "ok",
            1_000,
            200,
            "understand-v1",
        )
        assert gw.identity == ("gemini", "gemini-3.8-flash")

    async def test_transient_errors_retry_with_backoff(self) -> None:
        transport = FakeTransport(
            [TransientProviderError("rate_limited"), TransientProviderError("timeout"), ok()]
        )
        gw, ledger, slept = gateway(transport)
        reply = await gw.complete(request())
        assert reply.attempts == 3
        assert [c.outcome for c in ledger.calls] == ["rate_limited", "timeout", "ok"]
        assert len(slept) == 2
        assert 1.0 <= slept[0] <= 1.5
        assert 2.0 <= slept[1] <= 3.0

    async def test_an_invalid_answer_is_another_attempt_then_a_fallback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        transport = FakeTransport(
            [ok("not json"), ok('{"wrong": "secret"}'), ok("```json\n{}\n```")]
        )
        gw, ledger, _ = gateway(transport)
        with (
            caplog.at_level("WARNING", logger="insightfulcell.llm.gateway"),
            pytest.raises(ModelUnavailable) as caught,
        ):
            await gw.complete(request())
        assert caught.value.reason == "invalid_output"
        assert [c.outcome for c in ledger.calls] == ["invalid"] * 3
        # Each unusable answer says why, never what it said.
        causes = [r.cause for r in caplog.records if r.getMessage() == "model answer unusable"]  # type: ignore[attr-defined]
        assert causes == ["not_json", "schema", "schema"]
        assert "secret" not in caplog.text

    async def test_a_truncated_or_empty_answer_is_named(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        truncated = RawReply(text='{"answer": "ye', input_tokens=1, output_tokens=16_000,
                             request_id="r", truncated=True)  # fmt: skip
        transport = FakeTransport([truncated, ok(""), ok()])
        gw, _, _ = gateway(transport)
        with caplog.at_level("WARNING", logger="insightfulcell.llm.gateway"):
            await gw.complete(request())
        causes = [r.cause for r in caplog.records if r.getMessage() == "model answer unusable"]  # type: ignore[attr-defined]
        assert causes == ["truncated", "empty"]

    async def test_a_fenced_answer_is_accepted(self) -> None:
        gw, _, _ = gateway(FakeTransport([ok('```json\n{"answer": "fenced"}\n```')]))
        assert (await gw.complete(request())).data == Answer(answer="fenced")

    async def test_a_refusal_is_not_retried(self) -> None:
        transport = FakeTransport([PermanentProviderError("refused"), ok()])
        gw, ledger, _ = gateway(transport)
        with pytest.raises(ModelUnavailable) as caught:
            await gw.complete(request())
        assert caught.value.reason == "refused"
        assert len(transport.calls) == 1
        assert [c.outcome for c in ledger.calls] == ["refused"]

    async def test_the_breaker_opens_after_repeated_failures(self) -> None:
        transport = FakeTransport([TransientProviderError()] * 3)
        gw, _, _ = gateway(transport)
        with pytest.raises(ModelUnavailable):
            await gw.complete(request())
        with pytest.raises(ModelUnavailable) as caught:
            await gw.complete(request())
        assert caught.value.reason == "breaker_open"
        assert len(transport.calls) == 3

    async def test_personal_values_in_the_payload_block_the_call(self) -> None:
        transport = FakeTransport([ok()])
        gw, ledger, _ = gateway(transport)
        with pytest.raises(ModelUnavailable) as caught:
            await gw.complete(
                request(user='{"name": "Olivia Harris"}', sensitive=frozenset({"Olivia Harris"}))
            )
        assert caught.value.reason == "payload_blocked"
        assert transport.calls == []
        assert [c.outcome for c in ledger.calls] == ["blocked"]

    async def test_budgets_fall_back_before_calling(self) -> None:
        transport = FakeTransport([ok()])
        gw, _, _ = gateway(transport, FakeLedger(run_tokens=119_999))
        with pytest.raises(ModelUnavailable) as caught:
            await gw.complete(request(run_id=uuid4(), user="x" * 400))
        assert caught.value.reason == "budget_run"
        gw, _, _ = gateway(transport, FakeLedger(month=(999_990, 1_000_000)))
        with pytest.raises(ModelUnavailable) as caught:
            await gw.complete(request(user="x" * 400))
        assert caught.value.reason == "budget_org"
        assert transport.calls == []

    async def test_no_provider_means_the_model_is_off(self) -> None:
        gw, _, _ = gateway(None)
        assert gw.identity is None
        with pytest.raises(ModelUnavailable) as caught:
            await gw.complete(request())
        assert caught.value.reason == "disabled"


@pytest.mark.parametrize(
    ("text", "blocked"),
    [
        ('{"x": "contact olivia@example.com"}', "email_address"),
        ("ssn 123-45-6789", "national_id"),
        ("card 4111 1111 1111 1111", "payment_card"),
        ('{"trimmed_range": [1234567890123, 2]}', None),  # 13 digits that fail Luhn
        ('{"meaning": "currency"}', None),
    ],
)
def test_payload_checks(text: str, blocked: str | None) -> None:
    assert payload_blocked(text, frozenset()) == blocked


def test_breaker_half_opens_after_the_wait() -> None:
    now = [0.0]
    breaker = CircuitBreaker(threshold=2, open_seconds=60, clock=lambda: now[0])
    breaker.failure()
    breaker.failure()
    assert breaker.state == "open"
    assert not breaker.allow()
    now[0] = 61
    assert breaker.allow()
    assert not breaker.allow()  # exactly one trial
    breaker.success()
    assert breaker.state == "closed"


def test_cost_is_known_only_for_priced_models() -> None:
    assert cost_usd("claude-sonnet-5", 1_000_000, 100_000) == Decimal("3.000000")
    assert cost_usd("gemini-3.8-flash", 1_000, 1_000) is None
    assert cost_usd("gemini-3.8-flash", 1_000_000, 0, {"gemini-3.8-flash": (0.5, 2.0)}) == Decimal(
        "0.500000"
    )


def settings(**overrides: Any) -> Settings:
    base = {
        "database_url": "postgresql+psycopg://u@h/db",
        "auth_jwks_url": "https://web.example/api/auth/jwks",
        "auth_issuer": "https://web.example",
        "_env_file": None,
    }
    return Settings(**{**base, **overrides})


class TestProviderRegistry:
    def test_gemini_is_the_default_and_off_without_a_key(self) -> None:
        plain = settings()
        assert plain.llm_provider == "gemini"
        assert build_transport(plain) is None
        assert models_for(plain) == DEFAULT_MODELS["gemini"]

    @pytest.mark.parametrize(
        ("provider", "key", "kind"),
        [
            ("gemini", "gemini_api_key", GeminiTransport),
            ("openai", "openai_api_key", OpenAICompatibleTransport),
            ("anthropic", "anthropic_api_key", AnthropicTransport),
            ("deepseek", "deepseek_api_key", OpenAICompatibleTransport),
        ],
    )
    def test_each_provider_builds_its_adapter(self, provider: str, key: str, kind: type) -> None:
        transport = build_transport(settings(llm_provider=provider, **{key: SecretStr("k")}))
        assert isinstance(transport, kind)
        assert transport is not None
        assert transport.provider == provider

    def test_models_can_be_overridden(self) -> None:
        chosen = settings(llm_provider="anthropic", llm_model_strong="claude-opus-5-5")
        assert models_for(chosen) == {"strong": "claude-opus-5-5", "fast": "claude-haiku-5-5"}

    def test_prices_must_be_well_formed(self) -> None:
        with pytest.raises(ValueError, match="LLM_PRICES_JSON"):
            settings(llm_prices_json='{"m": [1]}')
        assert settings(llm_prices_json='{"m": [1, 2]}').llm_prices == {"m": (1.0, 2.0)}


# --------------------------------------------------------------------------- adapters


async def generate(transport: Any) -> RawReply:
    result: RawReply = await transport.generate(
        model="m", system="sys", user="usr", schema=SCHEMA, max_output_tokens=1000
    )
    return result


class TestGeminiAdapter:
    async def test_structured_output_and_usage(self) -> None:
        captured: dict[str, Any] = {}

        async def generate_content(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return SimpleNamespace(
                text='{"answer": "ok"}',
                candidates=[SimpleNamespace(finish_reason="STOP")],
                usage_metadata=SimpleNamespace(
                    prompt_token_count=10, candidates_token_count=4, thoughts_token_count=6
                ),
                response_id="g-1",
            )

        transport = GeminiTransport("k", timeout_seconds=5)
        transport._client = SimpleNamespace(
            aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
        )  # type: ignore[assignment]
        reply = await generate(transport)
        config = captured["config"]
        assert (
            config.response_mime_type,
            config.response_json_schema,
            config.system_instruction,
        ) == ("application/json", SCHEMA, "sys")
        assert (reply.text, reply.input_tokens, reply.output_tokens, reply.truncated) == (
            '{"answer": "ok"}',
            10,
            10,
            False,
        )

    async def test_errors_are_classified(self) -> None:
        async def rate_limited(**kwargs: Any) -> Any:
            raise genai_errors.ClientError(
                429,
                {"error": {"code": 429, "message": "slow down", "status": "RESOURCE_EXHAUSTED"}},
            )

        async def bad_key(**kwargs: Any) -> Any:
            raise genai_errors.ClientError(
                403, {"error": {"code": 403, "message": "no", "status": "PERMISSION_DENIED"}}
            )

        transport = GeminiTransport("k", timeout_seconds=5)
        for fake, error in (
            (rate_limited, TransientProviderError),
            (bad_key, PermanentProviderError),
        ):
            transport._client = SimpleNamespace(
                aio=SimpleNamespace(models=SimpleNamespace(generate_content=fake))
            )  # type: ignore[assignment]
            with pytest.raises(error):
                await generate(transport)


def openai_completion(content: str, finish: str = "stop") -> Any:
    return SimpleNamespace(
        id="o-1",
        choices=[
            SimpleNamespace(
                finish_reason=finish, message=SimpleNamespace(content=content, refusal=None)
            )
        ],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=5),
    )


class TestOpenAICompatibleAdapters:
    async def test_openai_uses_strict_json_schema(self) -> None:
        captured: dict[str, Any] = {}

        async def create(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return openai_completion('{"answer": "ok"}')

        transport = OpenAICompatibleTransport(provider="openai", api_key="k", timeout_seconds=5)
        transport._client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )  # type: ignore[assignment]
        reply = await generate(transport)
        assert captured["response_format"] == {
            "type": "json_schema",
            "json_schema": {"name": "response", "strict": True, "schema": SCHEMA},
        }
        assert captured["max_completion_tokens"] == 1000
        assert (reply.input_tokens, reply.output_tokens) == (11, 5)

    async def test_deepseek_uses_json_mode_with_the_schema_in_the_prompt(self) -> None:
        captured: dict[str, Any] = {}

        async def create(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return openai_completion('{"answer": "ok"}', finish="length")

        transport = OpenAICompatibleTransport(
            provider="deepseek",
            api_key="k",
            timeout_seconds=5,
            base_url="https://api.deepseek.com",
            strict_schema=False,
        )
        transport._client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )  # type: ignore[assignment]
        reply = await generate(transport)
        assert captured["response_format"] == {"type": "json_object"}
        assert json.dumps(SCHEMA) in captured["messages"][0]["content"]
        assert "max_tokens" in captured
        assert reply.truncated is True

    async def test_openai_errors_are_classified(self) -> None:
        request_ = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")

        async def limited(**kwargs: Any) -> Any:
            raise openai.RateLimitError(
                "slow", response=httpx2.Response(429, request=request_), body=None
            )

        async def unauthorized(**kwargs: Any) -> Any:
            raise openai.AuthenticationError(
                "no", response=httpx2.Response(401, request=request_), body=None
            )

        transport = OpenAICompatibleTransport(provider="openai", api_key="k", timeout_seconds=5)
        for fake, error in (
            (limited, TransientProviderError),
            (unauthorized, PermanentProviderError),
        ):
            transport._client = SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=fake))
            )  # type: ignore[assignment]
            with pytest.raises(error):
                await generate(transport)


class TestAnthropicAdapter:
    async def test_structured_output_and_refusals(self) -> None:
        captured: dict[str, Any] = {}

        def message(stop: str) -> Any:
            return SimpleNamespace(
                stop_reason=stop,
                content=[SimpleNamespace(type="text", text='{"answer": "ok"}')],
                usage=SimpleNamespace(
                    input_tokens=7,
                    cache_read_input_tokens=3,
                    cache_creation_input_tokens=0,
                    output_tokens=2,
                ),
            )

        replies = [message("end_turn"), message("refusal")]

        async def create(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return replies.pop(0)

        transport = AnthropicTransport("k", timeout_seconds=5)
        transport._client = SimpleNamespace(messages=SimpleNamespace(create=create))  # type: ignore[assignment]
        reply = await generate(transport)
        assert captured["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}
        assert captured["system"] == "sys"
        assert captured["max_tokens"] == 1000
        assert (reply.input_tokens, reply.output_tokens) == (10, 2)
        with pytest.raises(PermanentProviderError):
            await generate(transport)

    async def test_overload_is_transient(self) -> None:
        response = httpx2.Response(
            529, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        )

        async def overloaded(**kwargs: Any) -> Any:
            raise anthropic.OverloadedError("busy", response=response, body=None)

        transport = AnthropicTransport("k", timeout_seconds=5)
        transport._client = SimpleNamespace(messages=SimpleNamespace(create=overloaded))  # type: ignore[assignment]
        with pytest.raises(TransientProviderError):
            await generate(transport)
