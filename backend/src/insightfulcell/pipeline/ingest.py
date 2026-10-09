"""Stage 1, ``ingest``: verify the uploaded bytes and parse them into typed Parquet.

This is where ADR-026's promise is kept: the bytes in storage are re-hashed and must match the
SHA-256 the browser reported at upload, then parsed under the product limits. Meanings,
personal-data flags and per-column profiles are added by later stages (Phase 3).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any, Final

from insightfulcell.ingestion.engine import ingest_file
from insightfulcell.ingestion.limits import IngestLimits
from insightfulcell.pipeline.stage import SourceFile, StageContext, StageResult

#: Column names are shown back to the user; a pathological file cannot bloat the summary.
MAX_COLUMNS_IN_SUMMARY: Final = 500


class IngestStage:
    name = "ingest"
    version = "1"

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
        key = ctx.artifact_key(self.name, "data.parquet")
        await ctx.store.upload_file(parquet, key)
        return StageResult(
            output={
                "format": result.format.value,
                "rows": result.rows,
                "column_count": len(result.columns),
                "columns": [
                    {"name": column.name, "type": column.type}
                    for column in result.columns[:MAX_COLUMNS_IN_SUMMARY]
                ],
                "source_bytes": result.source_bytes,
                "parquet_bytes": result.parquet_bytes,
                "warnings": list(result.warnings),
            },
            artifacts={"data": key},
        )
