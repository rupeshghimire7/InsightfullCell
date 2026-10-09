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

from insightfulcell.ingestion.formats import FileFormat


class ArtifactStore(Protocol):
    """What a stage may do with object storage. ``storage.S3ObjectStore`` satisfies it."""

    async def download_file(self, key: str, dest: Path) -> None: ...

    async def upload_file(self, source: Path, key: str) -> None: ...


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
class StageContext:
    org_id: UUID
    run_id: UUID
    source: SourceFile
    workdir: Path
    store: ArtifactStore
    checkpoint_key: str
    #: Results of the stages before this one, in this run.
    upstream: Mapping[str, StageResult] = field(default_factory=dict)

    def artifact_key(self, stage: str, name: str) -> str:
        """``gold/{org}/{run}/{stage}/{checkpoint}-{name}``: keyed by inputs, so an identical
        re-run writes to the same place, and never chosen by a client."""
        return f"gold/{self.org_id}/{self.run_id}/{stage}/{self.checkpoint_key[:16]}-{name}"


class Stage(Protocol):
    #: Stable identifier, stored in ``run_stages.name`` (lowercase, digits, underscores).
    name: str
    #: Bump whenever the stage's output for the same inputs could change.
    version: str

    def fingerprint(self, source: SourceFile) -> Mapping[str, Any]:
        """The inputs and configuration that determine this stage's output (JSON-safe)."""
        ...

    async def run(self, ctx: StageContext) -> StageResult: ...


def checkpoint_key(stage: Stage, source: SourceFile, upstream_keys: Sequence[str]) -> str:
    canonical = json.dumps(
        {
            "stage": stage.name,
            "version": stage.version,
            "inputs": stage.fingerprint(source),
            "upstream": list(upstream_keys),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
