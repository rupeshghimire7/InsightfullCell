"""The spend cap in the gateway, the backup model, their settings, the admin key check and the
OpenAI usage report (fakes only: no database, no network)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from insightfulcell.engine.ports import ModelUnavailable
from insightfulcell.llm.breaker import CircuitBreaker
from insightfulcell.llm.fallback import FallbackModel
from insightfulcell.llm.gateway import CallRecord, GatewayConfig, LLMGateway, SpendHold
from insightfulcell.llm.providers import build_fallback_transport, fallback_models
from insightfulcell.llm.transport import PermanentProviderError, TransientProviderError
from insightfulcell.services.admin import admin_key_matches
from insightfulcell.services.provider_usage import OpenAIUsage
from tests.unit.test_llm_gateway import FakeLedger, FakeTransport, ok, request, settings

PRICED = {"strong": "gemini-3.5-flash-lite", "fast": "gemini-3.1-flash-lite"}


@dataclass
class FakeSpend:
    refuse: str | None = None
    broken: bool = False
    reserved: list[Decimal] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    settled: list[tuple[CallRecord, Decimal, bool]] = field(default_factory=list)

    async def reserve(
        self,
        org_id: UUID,
        run_id: UUID | None,
        provider: str,
        amount_usd: Decimal,
        input_usd: Decimal,
    ) -> SpendHold:
        if self.broken:
            raise RuntimeError("database down")
        self.reserved.append(amount_usd)
        self.providers.append(provider)
        if self.refuse:
            return SpendHold(None, amount_usd, input_usd, refused=self.refuse)
        return SpendHold(uuid4(), amount_usd, input_usd)

    async def settle(
        self, hold: SpendHold, call: CallRecord, charged_usd: Decimal, *, estimated: bool
    ) -> None:
        self.settled.append((call, charged_usd, estimated))


def capped(
    transport: FakeTransport | None,
    spend: FakeSpend | None = None,
    *,
    models: dict[str, str] | None = None,
    role: str = "primary",
) -> tuple[LLMGateway, FakeLedger, FakeSpend]:
    ledger, spend = FakeLedger(), spend or FakeSpend()

    async def no_sleep(seconds: float) -> None:
        del seconds

    gw = LLMGateway(
        transport,
        ledger,
        GatewayConfig(models=models or PRICED, max_output_tokens=1_000, role=role),
        CircuitBreaker(threshold=3, open_seconds=60),
        spend=spend,
        sleep=no_sleep,
    )
    return gw, ledger, spend


class TestSpendCap:
    async def test_reserves_the_worst_case_and_charges_what_was_used(self) -> None:
        gw, _, spend = capped(FakeTransport([ok()]))
        await gw.complete(request(run_id=uuid4()))
        # input estimate x 1.5 + 200 tokens at $0.30/M, plus the 1,000 token output limit at
        # $2.50/M: never less than the call can cost.
        (worst,) = spend.reserved
        assert worst >= Decimal("0.0025")
        ((call, charged, estimated),) = spend.settled
        assert call.outcome == "ok"
        assert not call.fallback
        assert charged == Decimal("0.000800")  # 1,000 in x $0.30/M + 200 out x $2.50/M
        assert not estimated
        assert charged <= worst

    async def test_no_room_under_the_cap_means_no_call(self) -> None:
        transport = FakeTransport([ok()])
        gw, ledger, spend = capped(transport, FakeSpend(refuse="spend_cap"))
        with pytest.raises(ModelUnavailable) as caught:
            await gw.complete(request())
        assert caught.value.reason == "spend_cap"
        assert transport.calls == []
        assert ledger.calls == []
        assert spend.settled == []

    async def test_a_model_without_a_price_is_never_called(self) -> None:
        transport = FakeTransport([ok()])
        gw, _, spend = capped(transport, models={"strong": "mystery", "fast": "mystery"})
        with pytest.raises(ModelUnavailable) as caught:
            await gw.complete(request())
        assert caught.value.reason == "price_unknown"
        assert transport.calls == []
        assert spend.reserved == []

    async def test_fails_closed_when_the_cap_cannot_be_checked(self) -> None:
        transport = FakeTransport([ok()])
        gw, _, _ = capped(transport, FakeSpend(broken=True))
        with pytest.raises(ModelUnavailable) as caught:
            await gw.complete(request())
        assert caught.value.reason == "spend_unavailable"
        assert transport.calls == []

    async def test_failed_attempts_are_charged_conservatively(self) -> None:
        transport = FakeTransport(
            [
                TransientProviderError("timeout"),
                TransientProviderError("provider_error"),
                TransientProviderError("rate_limited", retry_after=1),
                ok(),
            ]
        )
        gw, _, spend = capped(transport)
        await gw.complete(request())
        charges = {call.outcome: (charged, est) for call, charged, est in spend.settled}
        worst = spend.reserved[0]
        assert charges["timeout"] == (worst, True)  # it may have run, and been billed
        assert Decimal(0) < charges["provider_error"][0] < worst
        assert charges["provider_error"][1]
        assert charges["rate_limited"] == (Decimal(0), False)  # refused before any work
        assert len(spend.reserved) == len(spend.settled) == 4  # one reservation per attempt

    async def test_a_bad_key_costs_nothing(self) -> None:
        gw, _, spend = capped(FakeTransport([PermanentProviderError("auth_failed")]))
        with pytest.raises(ModelUnavailable):
            await gw.complete(request())
        ((_, charged, _),) = spend.settled
        assert charged == 0

    async def test_a_blocked_payload_reserves_nothing(self) -> None:
        transport = FakeTransport([ok()])
        gw, _, spend = capped(transport)
        with pytest.raises(ModelUnavailable) as caught:
            await gw.complete(request(user="mail ada@example.com"))
        assert caught.value.reason == "payload_blocked"
        assert spend.reserved == []
        assert transport.calls == []


class TestFallbackModel:
    async def test_the_main_model_answers_when_it_can(self) -> None:
        main, _, _ = capped(FakeTransport([ok()]))
        backup_transport = FakeTransport([ok()], provider="openai")
        backup, _, _ = capped(backup_transport, models=_MINI, role="backup")
        reply = await FallbackModel(main, backup).complete(request())
        assert reply.provider == "gemini"
        assert backup_transport.calls == []

    @pytest.mark.parametrize(
        "failure",
        [
            PermanentProviderError("quota_exhausted"),
            PermanentProviderError("auth_failed"),
            PermanentProviderError("refused"),
        ],
    )
    async def test_any_failure_of_the_main_model_goes_to_the_backup(
        self, failure: Exception
    ) -> None:
        main, _, _ = capped(FakeTransport([failure]))
        backup, ledger, spend = capped(
            FakeTransport([ok()], provider="openai"), models=_MINI, role="backup"
        )
        reply = await FallbackModel(main, backup).complete(request())
        assert (reply.provider, reply.model) == ("openai", "gpt-4o-mini")
        assert [c.fallback for c in ledger.calls] == [True]
        assert spend.settled[0][0].fallback

    async def test_unusable_answers_and_outages_go_to_the_backup(self) -> None:
        main, _, _ = capped(
            FakeTransport([ok("not json"), TransientProviderError("timeout"), ok("{}")])
        )
        backup, _, _ = capped(FakeTransport([ok()], provider="openai"), models=_MINI)
        assert (await FallbackModel(main, backup).complete(request())).provider == "openai"

    async def test_a_missing_main_key_goes_straight_to_the_backup(self) -> None:
        main, _, _ = capped(None)
        backup, _, _ = capped(FakeTransport([ok()], provider="openai"), models=_MINI)
        model = FallbackModel(main, backup)
        assert model.identity == ("openai", "gpt-4o-mini")
        assert (await model.complete(request())).provider == "openai"

    @pytest.mark.parametrize("reason", ["spend_cap", "spend_cap_user"])
    async def test_never_past_the_spend_cap(self, reason: str) -> None:
        main, _, _ = capped(FakeTransport([ok()]), FakeSpend(refuse=reason))
        backup_transport = FakeTransport([ok()], provider="openai")
        backup, _, _ = capped(backup_transport, models=_MINI)
        with pytest.raises(ModelUnavailable) as caught:
            await FallbackModel(main, backup).complete(request())
        assert caught.value.reason == reason
        assert backup_transport.calls == []

    async def test_a_main_provider_at_its_own_cap_hands_over_to_the_backup(self) -> None:
        main, _, main_spend = capped(FakeTransport([ok()]), FakeSpend(refuse="spend_cap_provider"))
        backup, _, backup_spend = capped(FakeTransport([ok()], provider="openai"), models=_MINI)
        assert (await FallbackModel(main, backup).complete(request())).provider == "openai"
        # Each reservation names its provider, so each counts against that provider's cap.
        assert (main_spend.providers, backup_spend.providers) == (["gemini"], ["openai"])

    async def test_never_with_data_the_privacy_check_blocked(self) -> None:
        main, _, _ = capped(FakeTransport([ok()]))
        backup_transport = FakeTransport([ok()], provider="openai")
        backup, _, _ = capped(backup_transport, models=_MINI)
        with pytest.raises(ModelUnavailable, match="payload_blocked"):
            await FallbackModel(main, backup).complete(request(user="ssn 123-45-6789"))
        assert backup_transport.calls == []

    async def test_both_failing_falls_back_to_the_rules(self) -> None:
        main, _, _ = capped(FakeTransport([PermanentProviderError("quota_exhausted")]))
        backup, _, _ = capped(
            FakeTransport([PermanentProviderError("auth_failed")], provider="openai"),
            models=_MINI,
        )
        with pytest.raises(ModelUnavailable) as caught:
            await FallbackModel(main, backup).complete(request())
        assert caught.value.reason == "auth_failed"

    async def test_without_a_backup_it_is_the_main_model(self) -> None:
        main, _, _ = capped(FakeTransport([PermanentProviderError("quota_exhausted")]))
        with pytest.raises(ModelUnavailable, match="quota_exhausted"):
            await FallbackModel(main, None).complete(request())


_MINI = {"strong": "gpt-4o-mini", "fast": "gpt-4o-mini"}


class TestStartupCheckWithABackup:
    @staticmethod
    def _checked(error: Exception | None, provider: str, models: dict[str, str]) -> LLMGateway:
        class Checked(FakeTransport):
            async def check(self, model: str) -> None:
                if error is not None:
                    raise error

        return capped(Checked([], provider=provider), models=models)[0]

    async def test_starts_when_one_of_the_two_works(self) -> None:
        from insightfulcell.worker.runtime import check_model  # noqa: PLC0415

        chosen = _with_backup()
        bad_main = self._checked(PermanentProviderError("auth_failed"), "gemini", PRICED)
        good_backup = self._checked(None, "openai", _MINI)
        await check_model(FallbackModel(bad_main, good_backup), chosen)

    async def test_stops_when_neither_works(self) -> None:
        from insightfulcell.worker.runtime import ModelNotUsable, check_model  # noqa: PLC0415

        chosen = _with_backup()
        bad_main = self._checked(PermanentProviderError("auth_failed"), "gemini", PRICED)
        bad_backup = self._checked(PermanentProviderError("model_not_found"), "openai", _MINI)
        with pytest.raises(ModelNotUsable, match="LLM_FALLBACK_MODEL"):
            await check_model(FallbackModel(bad_main, bad_backup), chosen)


def _with_backup(**overrides: Any) -> Any:
    return settings(
        llm_provider="gemini",
        gemini_api_key=SecretStr("g"),
        llm_fallback_provider="openai",
        openai_api_key=SecretStr("o"),
        llm_fallback_model="gpt-4o-mini",
        **overrides,
    )


class TestSettings:
    def test_the_backup_needs_its_key_and_model_and_a_different_provider(self) -> None:
        with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
            settings(llm_fallback_provider="openai", llm_fallback_model="gpt-4o-mini")
        with pytest.raises(ValidationError, match="LLM_FALLBACK_MODEL"):
            settings(llm_fallback_provider="openai", openai_api_key=SecretStr("o"))
        with pytest.raises(ValidationError, match="must differ"):
            settings(
                llm_provider="gemini",
                gemini_api_key=SecretStr("g"),
                llm_fallback_provider="gemini",
                llm_fallback_model="x",
            )

    def test_the_backup_is_built_from_its_settings(self) -> None:
        chosen = _with_backup()
        transport = build_fallback_transport(chosen)
        assert transport is not None
        assert transport.provider == "openai"
        assert fallback_models(chosen) == _MINI
        assert build_fallback_transport(settings()) is None

    def test_the_cap_is_converted_with_the_configured_rate(self) -> None:
        chosen = settings(llm_spend_cap_aud=Decimal("4.00"), llm_usd_to_aud=Decimal("1.60"))
        assert chosen.llm_spend_cap_usd == Decimal("2.500000")
        assert chosen.llm_user_spend_cap_usd == 0
        # A cap per provider, and "none" (any spelling of no limit) for no limit at all.
        mixed = settings(
            llm_spend_cap_aud="NA",
            llm_spend_cap_gemini_aud="none",
            llm_spend_cap_openai_aud="4.50",
            llm_usd_to_aud=Decimal("1.50"),
        )
        assert mixed.llm_spend_cap_usd is None
        assert mixed.provider_spend_caps_usd == {
            "gemini": None,
            "openai": Decimal("3.000000"),
            "anthropic": None,
            "deepseek": None,
        }
        with pytest.raises(ValidationError):
            settings(llm_spend_cap_openai_aud="-1")
        with pytest.raises(ValidationError):
            settings(llm_spend_cap_aud=Decimal(-1))
        with pytest.raises(ValidationError):
            settings(llm_usage_label="Has Spaces")

    def test_the_admin_key_must_be_long_and_blank_means_off(self) -> None:
        with pytest.raises(ValidationError, match="ADMIN_DASHBOARD_KEY"):
            settings(admin_dashboard_key=SecretStr("short"))
        assert settings(admin_dashboard_key="  ").admin_dashboard_key is None
        assert settings(openai_admin_key="").openai_admin_key is None


def test_the_admin_key_check() -> None:
    key = SecretStr("k" * 40)
    assert admin_key_matches("k" * 40, key)
    assert not admin_key_matches("k" * 39, key)
    assert not admin_key_matches("", key)
    assert not admin_key_matches(None, key)
    assert not admin_key_matches("k" * 40, None)  # switched off: nothing matches


class TestOpenAIUsage:
    @staticmethod
    def _client(handler: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def test_not_configured_without_an_admin_key(self) -> None:
        assert await OpenAIUsage(None).report(30) == {"status": "not_configured"}

    async def test_sums_costs_and_usage_and_caches_them(self) -> None:
        seen: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            if req.url.path.endswith("/costs"):
                body = {
                    "data": [
                        {
                            "start_time": 1,
                            "results": [{"amount": {"value": 0.0123, "currency": "usd"}}],
                        },
                        {"start_time": 2, "results": []},
                    ],
                    "has_more": False,
                }
            else:
                body = {
                    "data": [
                        {
                            "start_time": 1,
                            "results": [
                                {
                                    "model": "gpt-4o-mini-2024-07-18",
                                    "input_tokens": 10,
                                    "output_tokens": 5,
                                    "num_model_requests": 2,
                                }
                            ],
                        }
                    ],
                    "has_more": False,
                }
            return httpx.Response(200, json=body)

        usage = OpenAIUsage("sk-admin-x", client=self._client(handler), clock=lambda: 1_000_000)
        report = await usage.report(7)
        assert report["status"] == "ok"
        assert report["total_cost"] == "0.0123"
        assert report["by_model"] == [
            {
                "model": "gpt-4o-mini-2024-07-18",
                "input_tokens": 10,
                "output_tokens": 5,
                "requests": 2,
            }
        ]
        assert seen[0].headers["authorization"] == "Bearer sk-admin-x"
        await usage.report(7)
        assert len(seen) == 2  # cached: no second fetch

    async def test_a_failure_is_reported_never_raised_and_never_leaks_the_key(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "bad key"})

        report = await OpenAIUsage("sk-admin-secret", client=self._client(handler)).report(7)
        assert report == {"status": "unavailable", "http_status": 401}
        assert "sk-admin-secret" not in caplog.text
        assert "sk-admin-secret" not in json.dumps(report)
