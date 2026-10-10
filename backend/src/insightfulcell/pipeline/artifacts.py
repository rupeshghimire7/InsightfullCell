"""Reading and writing stage artifacts (the ArtifactStore port) and the run's DuckDB view."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

from insightfulcell.engine import sql
from insightfulcell.pipeline.stage import StageContext


def upstream_artifact(ctx: StageContext, stage: str, name: str) -> str:
    result = ctx.upstream.get(stage)
    if result is None or name not in result.artifacts:
        raise LookupError(f"stage {stage!r} produced no artifact {name!r}")
    return result.artifacts[name]


async def fetch(ctx: StageContext, stage: str, name: str, filename: str) -> Path:
    dest = ctx.workdir / filename
    await ctx.store.download_file(upstream_artifact(ctx, stage, name), dest)
    return dest


async def fetch_json(ctx: StageContext, stage: str, name: str) -> Any:
    path = await fetch(ctx, stage, name, f"{stage}-{name}.json")
    return json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))


async def put_json(ctx: StageContext, stage: str, name: str, value: Any) -> str:
    path = ctx.workdir / f"out-{stage}-{name}"
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    await asyncio.to_thread(path.write_text, text, encoding="utf-8")
    key = ctx.artifact_key(stage, name)
    await ctx.store.upload_file(path, key)
    return key


@contextmanager
def table(parquet: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """A bounded DuckDB with the run's typed data as view ``t``."""
    con = sql.connect()
    try:
        sql.open_table(con, parquet)
        yield con
    finally:
        con.close()
