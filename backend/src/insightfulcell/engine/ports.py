"""Ports: the only ways the engine reaches the outside world.

Stages persist nothing directly. They call these interfaces; adapters do the I/O (PostgreSQL,
S3 and the model providers in production; in-memory fakes in tests and the CLI).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel


class ArtifactStore(Protocol):
    """Object storage for stage inputs and outputs. ``storage.S3ObjectStore`` satisfies it."""

    async def download_file(self, key: str, dest: Path) -> None: ...

    async def upload_file(self, source: Path, key: str) -> None: ...


# --------------------------------------------------------------------------- language model


class Purpose(StrEnum):
    """Why a model is called. The gateway picks the model tier from the purpose."""

    PLAN = "plan"  # understand: masked schema card -> analysis plan
    NARRATE = "narrate"
    ROUTE = "route"
    AGENT = "agent"
    JUDGE = "judge"


@dataclass(frozen=True, slots=True)
class ModelRequest:
    purpose: Purpose
    org_id: UUID
    run_id: UUID | None
    prompt_id: str
    prompt_version: str
    system: str
    user: str
    response_model: type[BaseModel]
    response_schema: Mapping[str, Any]
    #: Values from personal-data columns. A payload containing any of them is blocked, so a
    #: bug elsewhere cannot send a person's data to a provider.
    sensitive_values: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ModelReply:
    data: BaseModel
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    attempts: int


class ModelUnavailable(Exception):
    """No usable model answer: the caller falls back to its deterministic path. ``reason`` is
    a stable code (disabled, budget_run, budget_org, breaker_open, payload_blocked, timeout,
    provider_error, invalid_output, refused) and safe to show."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class LanguageModel(Protocol):
    @property
    def identity(self) -> tuple[str, str] | None:
        """(provider, model) for planning, or ``None`` when the model is off."""
        ...

    async def complete(self, request: ModelRequest) -> ModelReply: ...


# --------------------------------------------------------------------------- run records


@dataclass(frozen=True, slots=True)
class ColumnRecord:
    """One ``run_columns`` row: derived values belong to the run, not the file."""

    position: int
    name: str
    physical_type: str
    meaning: str
    confidence: str
    personal_data: str | None
    unit: str | None
    display_name: str | None
    role: str | None
    flags: Sequence[str]
    profile: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CachedPlan:
    plan: Mapping[str, Any]  # a validated ProposedPlan, as JSON
    provider: str | None
    model: str | None


@dataclass(frozen=True, slots=True)
class PlanRecord:
    kind: str  # "proposal" (a planner's validated output) or "resolved"
    source: str  # model, cache, rules, person
    schema_fingerprint: str
    prompt_version: str | None
    provider: str | None
    model: str | None
    plan: Mapping[str, Any]
    plan_hash: str
    rejected: Sequence[Mapping[str, Any]] = ()
    input_card: Mapping[str, Any] | None = None
    fallback_reason: str | None = None


@dataclass(frozen=True, slots=True)
class CleaningRecord:
    policy: str
    status: str  # proposed (waiting for a person) or approved (by policy)
    plan_hash: str
    operations: Sequence[Mapping[str, Any]] = field(default_factory=tuple)


class AnalysisRepository(Protocol):
    """The run's records in the database, scoped to one run of one organisation."""

    async def save_columns(self, columns: Sequence[ColumnRecord]) -> None: ...

    async def find_cached_plan(
        self, schema_fingerprint: str, prompt_version: str
    ) -> CachedPlan | None: ...

    async def save_plan(self, record: PlanRecord) -> None: ...

    async def save_cleaning_plan(self, record: CleaningRecord) -> None: ...


class FactRepository(Protocol):
    """Immutable facts with provenance (Phase B, ``facts`` stage)."""

    async def save_facts(self, facts: Sequence[Mapping[str, Any]]) -> None: ...


class ReferenceStore(Protocol):
    """Geographic reference sets (Phase B, ``geo`` stage)."""

    async def gazetteer(self, reference_set: str, dest: Path) -> Path: ...


class NullRepository:
    """For running stages without a database (tests, the CLI): records go nowhere."""

    async def save_columns(self, columns: Sequence[ColumnRecord]) -> None:
        return None

    async def find_cached_plan(
        self, schema_fingerprint: str, prompt_version: str
    ) -> CachedPlan | None:
        return None

    async def save_plan(self, record: PlanRecord) -> None:
        return None

    async def save_cleaning_plan(self, record: CleaningRecord) -> None:
        return None


class NoModel:
    """The language model is off: every call falls back."""

    @property
    def identity(self) -> tuple[str, str] | None:
        return None

    async def complete(self, request: ModelRequest) -> ModelReply:
        raise ModelUnavailable("disabled", "No language model is configured.")
