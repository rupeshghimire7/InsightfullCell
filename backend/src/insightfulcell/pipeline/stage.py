"""The stage contract and checkpoint keys (IC-007).

A checkpoint key is the SHA-256 of everything that determines a stage's output: its name and
code version, its own inputs and configuration, and the keys of every stage before it. When a
run is retried, a stage whose stored key matches is not run again.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from insightfulcell.engine.ports import (
    AnalysisRepository,
    ArtifactStore,
    LanguageModel,
    NoModel,
    NullRepository,
)
from insightfulcell.engine.subset import Subset
from insightfulcell.ingestion.formats import FileFormat

__all__ = [
    "ArtifactStore",
    "RunInputs",
    "SourceFile",
    "Stage",
    "StageContext",
    "StageResult",
    "checkpoint_key",
]


@dataclass(frozen=True, slots=True)
class SourceFile:
    """The dataset version a run analyses: the exact uploaded bytes and what they claim to be."""

    version_id: UUID
    bronze_key: str
    sha256: str  # hex, recorded when the upload completed; verified by the ingest stage
    format: FileFormat
    bytes: int


@dataclass(frozen=True, slots=True)
class StageResult:
    """What a stage produced.

    ``output`` is a small JSON summary shown to the user and stored with the checkpoint: it may
    hold counts, column names and types, never cell values. ``artifacts`` maps a name to the
    object key of a file the stage wrote. ``needs_review`` asks for the human review gate (only
    meaningful at the end of Phase A).
    """

    output: Mapping[str, Any] = field(default_factory=dict)
    artifacts: Mapping[str, str] = field(default_factory=dict)
    needs_review: bool = False


@dataclass(frozen=True, slots=True)
class RunInputs:
    """What a run brings besides the file: the dataset's name, the organisation's cleaning
    policy, a person's saved choices for the dataset, and the run's reference date (which
    dates count as "in the future"). Fixed when the run is created, so a retry sees the same;
    replaced only when a person asks for the plan to be revised."""

    dataset_id: UUID | None = None
    dataset_name: str = ""
    cleaning_policy: str = "review_lossy"
    annotations: Mapping[str, Any] = field(default_factory=dict)
    today: str = "1970-01-01"
    #: Cleaning decisions a person already made in this run's review (operation id -> apply),
    #: kept when they ask for the plan to be revised.
    cleaning_decisions: Mapping[str, bool] = field(default_factory=dict)
    #: A drill-down's subset. Not part of ``fingerprint``: a drill-down reuses its parent's
    #: Phase A checkpoints, and only stages that set ``uses_subset`` are keyed by it.
    drilldown: Subset | None = None
    #: ``auto``: changes that would wait for a review are applied and the run never stops;
    #: ``manual``: the run stops at the review gate.
    review_mode: str = "manual"

    def fingerprint(self) -> dict[str, Any]:
        return {
            "today": self.today,
            "dataset_name": self.dataset_name,
            "cleaning_policy": self.cleaning_policy,
            "annotations": dict(self.annotations),
            "cleaning_decisions": dict(sorted(self.cleaning_decisions.items())),
            # Only when it differs from what every earlier run did, so their checkpoints still
            # match their keys.
            **({"review_mode": "auto"} if self.review_mode == "auto" else {}),
        }


@dataclass(frozen=True, slots=True)
class StageContext:
    org_id: UUID
    run_id: UUID
    source: SourceFile
    workdir: Path
    store: ArtifactStore
    checkpoint_key: str
    #: Results of the stages before this one, in this run.
    upstream: Mapping[str, StageResult] = field(default_factory=dict)
    inputs: RunInputs = field(default_factory=RunInputs)
    repository: AnalysisRepository = field(default_factory=NullRepository)
    model: LanguageModel = field(default_factory=NoModel)

    def artifact_key(self, stage: str, name: str) -> str:
        """``gold/{org}/{run}/{stage}/{checkpoint}-{name}``: keyed by inputs, so an identical
        re-run writes to the same place, and never chosen by a client."""
        return f"gold/{self.org_id}/{self.run_id}/{stage}/{self.checkpoint_key[:16]}-{name}"

    def silver_key(self, data_hash: str) -> str:
        """``silver/{org}/{version}/{hash}.parquet``: cleaned data, keyed by the file and the
        cleaning applied to it, so it can always be rebuilt from the upload and the plan."""
        if len(data_hash) != 64 or not all(c in "0123456789abcdef" for c in data_hash):
            raise ValueError("a silver key needs a SHA-256 hex digest")
        return f"silver/{self.org_id}/{self.source.version_id}/{data_hash}.parquet"


class Stage(Protocol):
    #: Stable identifier, stored in ``run_stages.name`` (lowercase, digits, underscores).
    name: str
    #: Bump whenever the stage's output for the same inputs could change.
    version: str
    # A stage may also set ``uses_run_inputs``: ``True`` makes all of the run's inputs (policy,
    # a person's choices, the reference date) part of its checkpoint key; a tuple of field
    # names includes only those, so a change elsewhere does not redo the stage. And
    # ``uses_subset = True`` keys it by a drill-down's subset (the first stage that reads it:
    # every later key follows through the upstream keys).

    def fingerprint(self, source: SourceFile) -> Mapping[str, Any]:
        """The inputs and configuration that determine this stage's output (JSON-safe)."""
        ...

    async def run(self, ctx: StageContext) -> StageResult: ...


def checkpoint_key(
    stage: Stage,
    source: SourceFile,
    upstream_keys: Sequence[str],
    inputs: RunInputs | None = None,
) -> str:
    fingerprint: dict[str, Any] = {
        "stage": stage.name,
        "version": stage.version,
        "inputs": stage.fingerprint(source),
        "upstream": list(upstream_keys),
    }
    uses = getattr(stage, "uses_run_inputs", False)
    if inputs is not None and uses:
        everything = inputs.fingerprint()
        fingerprint["run_inputs"] = (
            everything if uses is True else {name: everything[name] for name in uses}
        )
    # Only when set, so a full run's keys are what they always were.
    if inputs is not None and inputs.drilldown is not None and getattr(stage, "uses_subset", False):
        fingerprint["subset"] = inputs.drilldown.fingerprint()
    canonical = json.dumps(
        fingerprint,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
