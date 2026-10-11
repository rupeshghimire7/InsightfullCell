from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from insightfulcell import __version__
from insightfulcell.domain.errors import IntegrityMismatch, ParseFailure
from insightfulcell.domain.runs import Phase
from insightfulcell.ingestion.formats import FileFormat
from insightfulcell.ingestion.limits import IngestLimits
from insightfulcell.pipeline.definition import Pipeline, default_pipeline
from insightfulcell.pipeline.ingest import IngestStage
from insightfulcell.pipeline.profile import ProfileStage
from insightfulcell.pipeline.stage import RunInputs, SourceFile, StageContext, checkpoint_key
from insightfulcell.pipeline.understand import UnderstandStage
from insightfulcell.services.run_executor import event_summary

CSV = b"id,city,price\n1,Perth,10.5\n2,Darwin,7\n"


class MemoryStore:
    """In-memory ``ArtifactStore``."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})

    async def download_file(self, key: str, dest: Path) -> None:
        await asyncio.to_thread(dest.write_bytes, self.objects[key])

    async def upload_file(self, source: Path, key: str) -> None:
        self.objects[key] = await asyncio.to_thread(source.read_bytes)


def source(data: bytes = CSV, *, sha256: str | None = None) -> SourceFile:
    return SourceFile(
        version_id=uuid4(),
        bronze_key="bronze/org/abc",
        sha256=sha256 or hashlib.sha256(data).hexdigest(),
        format=FileFormat.CSV,
        bytes=len(data),
    )


class TestCheckpointKey:
    def test_is_a_stable_hex_digest(self) -> None:
        stage, src = IngestStage(), source()
        key = checkpoint_key(stage, src, [])
        assert key == checkpoint_key(stage, src, [])
        assert len(key) == 64
        assert int(key, 16) >= 0

    def test_changes_with_inputs_config_version_and_upstream(self) -> None:
        src = source()
        base = checkpoint_key(IngestStage(), src, [])
        assert checkpoint_key(IngestStage(), source(CSV + b"3,Alice,1\n"), []) != base
        assert checkpoint_key(IngestStage(IngestLimits(max_rows=10)), src, []) != base
        assert checkpoint_key(IngestStage(), src, ["a" * 64]) != base

        class Bumped(IngestStage):
            version = "99"

        assert checkpoint_key(Bumped(), src, []) != base

    def test_a_stage_is_redone_only_when_the_inputs_it_reads_change(self) -> None:
        """A person's requests re-plan the run; they do not re-profile the file."""
        src = source()
        before = RunInputs(today="2026-10-10")
        after = RunInputs(today="2026-10-10", annotations={"requests": ["compare teams"]})
        later = RunInputs(today="2026-10-11")
        assert checkpoint_key(ProfileStage(), src, [], after) == checkpoint_key(
            ProfileStage(), src, [], before
        )
        assert checkpoint_key(ProfileStage(), src, [], later) != checkpoint_key(
            ProfileStage(), src, [], before
        )
        understand = UnderstandStage()
        assert checkpoint_key(understand, src, [], after) != checkpoint_key(
            understand, src, [], before
        )


class TestPipeline:
    def test_default_pipeline_plans_then_answers(self) -> None:
        pipeline = default_pipeline()
        assert [(p.phase, p.position, p.stage.name) for p in pipeline.planned()] == [
            (Phase.A, 0, "ingest"),
            (Phase.A, 1, "profile"),
            (Phase.A, 2, "understand"),
            (Phase.A, 3, "clean_plan"),
            (Phase.B, 4, "clean_apply"),
            (Phase.B, 5, "geo"),
            (Phase.B, 6, "features"),
            (Phase.B, 7, "mine"),
            (Phase.B, 8, "sensitivity"),
            (Phase.B, 9, "rank"),
            (Phase.B, 10, "visualize"),
            (Phase.B, 11, "report"),
        ]

    def test_the_model_does_not_change_the_stage_list(self) -> None:
        """The API plans runs without knowing the worker's model: both must agree."""
        assert (
            default_pipeline(model_identity=("gemini", "gemini-3.8-flash")).code_version
            == default_pipeline().code_version
        )

    def test_positions_run_across_both_phases(self) -> None:
        a, b = IngestStage(), type("Other", (IngestStage,), {"name": "other"})()
        pipeline = Pipeline(phase_a=(a,), phase_b=(b,))
        assert [(p.phase, p.position) for p in pipeline.planned()] == [(Phase.A, 0), (Phase.B, 1)]

    def test_stage_names_are_unique(self) -> None:
        with pytest.raises(ValueError, match="unique"):
            Pipeline(phase_a=(IngestStage(),), phase_b=(IngestStage(),))

    def test_code_version_tracks_the_stage_list(self) -> None:
        one = default_pipeline().code_version
        assert one == default_pipeline().code_version
        assert one.startswith(f"{__version__}+")
        bumped = type("Bumped", (IngestStage,), {"version": "9"})()
        assert Pipeline(phase_a=(bumped,)).code_version != one


class TestIngestStage:
    async def test_parses_uploads_and_summarises_without_values(self, tmp_path: Path) -> None:
        src = source()
        store = MemoryStore({src.bronze_key: CSV})
        org, run = uuid4(), uuid4()
        key = checkpoint_key(IngestStage(), src, [])
        ctx = StageContext(org, run, src, tmp_path, store, key)

        result = await IngestStage().run(ctx)

        assert result.output["rows"] == 2
        assert result.output["column_count"] == 3
        assert result.output["columns"][1] == {
            "name": "city",
            "type": "VARCHAR",
            "meaning": "geography",
            "personal_data": None,
        }
        assert set(result.artifacts) == {"data", "semantics"}
        artifact = result.artifacts["data"]
        assert artifact == f"gold/{org}/{run}/ingest/{key[:16]}-data.parquet"
        assert store.objects[artifact].startswith(b"PAR1")
        assert "Perth" not in str(result.output)  # summaries never carry cell values
        assert result.needs_review is False

    async def test_refuses_bytes_that_differ_from_the_recorded_digest(self, tmp_path: Path) -> None:
        src = source(sha256="0" * 64)
        ctx = StageContext(uuid4(), uuid4(), src, tmp_path, MemoryStore({src.bronze_key: CSV}), "k")
        with pytest.raises(IntegrityMismatch):
            await IngestStage().run(ctx)

    async def test_parse_failures_propagate_as_domain_errors(self, tmp_path: Path) -> None:
        data = b"only_a_header\n"
        src = source(data)
        ctx = StageContext(
            uuid4(), uuid4(), src, tmp_path, MemoryStore({src.bronze_key: data}), "k"
        )
        with pytest.raises(ParseFailure):
            await IngestStage().run(ctx)


def test_event_summary_keeps_scalars_and_counts_lists() -> None:
    output = {
        "rows": 3,
        "ratio": 0.5,
        "ok": True,
        "format": "csv",
        "long": "x" * 65,
        "columns": [{"name": "a"}, {"name": "b"}],
        "nested": {"a": 1},
    }
    assert event_summary(output) == {
        "rows": 3,
        "ratio": 0.5,
        "ok": True,
        "format": "csv",
        "columns_count": 2,
    }
