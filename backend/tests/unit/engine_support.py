"""Builders for engine tests: a DuckDB table ``t`` from Python rows, and the samples."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

from insightfulcell.engine import semantics, sql
from insightfulcell.engine.profiling import Profile, profile_table
from insightfulcell.engine.semantics import ColumnSemantics

SAMPLES = Path(__file__).resolve().parents[3] / "sample_data"
TODAY = "2026-10-10"


@contextmanager
def table(
    columns: Sequence[tuple[str, str]], rows: Sequence[Sequence[Any]]
) -> Iterator[duckdb.DuckDBPyConnection]:
    con = sql.connect()
    try:
        con.execute("CREATE TABLE t (" + ", ".join(f"{sql.ident(n)} {t}" for n, t in columns) + ")")
        if rows:
            marks = ", ".join("?" for _ in columns)
            con.executemany(f"INSERT INTO t VALUES ({marks})", [list(r) for r in rows])
        yield con
    finally:
        con.close()


def analyse(
    con: duckdb.DuckDBPyConnection, columns: Sequence[tuple[str, str]]
) -> tuple[list[ColumnSemantics], Profile]:
    found = semantics.detect(con, list(columns))
    return found, profile_table(con, found, today=TODAY)


def by_name(items: Sequence[ColumnSemantics]) -> dict[str, ColumnSemantics]:
    return {s.name: s for s in items}
