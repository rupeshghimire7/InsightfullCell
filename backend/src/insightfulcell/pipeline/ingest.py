"""Stage 1, ``ingest``: verify the uploaded bytes, parse them into typed Parquet, assign each
column a meaning and flag personal data.

This is where ADR-026's promise is kept: the bytes in storage are re-hashed and must match the
SHA-256 the browser reported at upload, then parsed under the product limits. Meanings and
personal-data flags are written to the ``semantics`` artifact; from here on, flagged columns
are masked.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any, Final

from insightfulcell.engine import semantics as sem
from insightfulcell.ingestion.engine import ingest_file
from insightfulcell.ingestion.limits import IngestLimits
from insightfulcell.pipeline.artifacts import put_json, table
from insightfulcell.pipeline.stage import SourceFile, StageContext, StageResult

#: Column names are shown back to the user; a pathological file cannot bloat the summary.
MAX_COLUMNS_IN_SUMMARY: Final = 500
#: Ingest fell back to reading every column as text (ingestion.engine wording).
READ_AS_TEXT_WARNING: Final = "all columns were read as text"


class IngestStage:
    name = "ingest"
    version = "2"

    def __init__(self, limits: IngestLimits | None = None) -> None:
        self._limits = limits or IngestLimits()

    def fingerprint(self, source: SourceFile) -> Mapping[str, Any]:
        return {
            "sha256": source.sha256,
            "format": source.format.value,
            "limits": asdict(self._limits),
        }

    async def run(self, ctx: StageContext) -> StageResult:
        source = ctx.source
        local = ctx.workdir / f"source.{source.format.value}"
        await ctx.store.download_file(source.bronze_key, local)
        parquet = ctx.workdir / "data.parquet"
        # DuckDB and openpyxl block: keep them off the event loop so heartbeats keep flowing.
        result = await asyncio.to_thread(
            ingest_file,
            local,
            source.format,
            parquet,
            limits=self._limits,
            expected_sha256=source.sha256,
        )
        columns = [(c.name, c.type) for c in result.columns]

        def detect() -> list[sem.ColumnSemantics]:
            with table(parquet) as con:
                return sem.detect(con, columns)

        semantics = await asyncio.to_thread(detect)
        key = ctx.artifact_key(self.name, "data.parquet")
        await ctx.store.upload_file(parquet, key)
        semantics_key = await put_json(
            ctx, self.name, "semantics.json", [s.model_dump(mode="json") for s in semantics]
        )
        return StageResult(
            output={
                "format": result.format.value,
                "rows": result.rows,
                "column_count": len(result.columns),
                "columns": [
                    {
                        "name": s.name,
                        "type": s.physical_type,
                        "meaning": s.meaning.value,
                        "personal_data": s.personal_data.value if s.personal_data else None,
                    }
                    for s in semantics[:MAX_COLUMNS_IN_SUMMARY]
                ],
                "personal_data_columns": sum(1 for s in semantics if s.personal_data),
                "read_as_text": any(READ_AS_TEXT_WARNING in w for w in result.warnings),
                "source_bytes": result.source_bytes,
                "parquet_bytes": result.parquet_bytes,
                "warnings": list(result.warnings),
            },
            artifacts={"data": key, "semantics": semantics_key},
        )
