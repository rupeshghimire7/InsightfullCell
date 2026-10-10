"""Quoting for the SQL the engine writes. Column names and category labels come from user
files, so every one is quoted here and nowhere else."""

from __future__ import annotations

import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import duckdb


def ident(name: str) -> str:
    """A DuckDB identifier: double quotes, embedded quotes doubled."""
    return '"' + name.replace('"', '""') + '"'


def literal(value: str) -> str:
    """A DuckDB string literal. NUL cannot appear in a DuckDB string, so it is dropped."""
    return "'" + value.replace("\x00", "").replace("'", "''") + "'"


def number(value: float) -> str:
    if math.isnan(value) or math.isinf(value):
        raise ValueError("only finite numbers can be written into SQL")
    return repr(float(value))


def literal_list(values: Iterable[str]) -> str:
    return "[" + ", ".join(literal(v) for v in values) + "]"


def connect(memory_limit: str = "1GB", threads: int = 2) -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB with bounded resources and no extension downloads."""
    return duckdb.connect(
        ":memory:",
        config={
            "memory_limit": memory_limit,
            "threads": threads,
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
        },
    )


def open_table(con: duckdb.DuckDBPyConnection, parquet: Path, name: str = "t") -> None:
    # A view cannot hold prepared parameters, so the (worker-chosen) path is a literal.
    con.execute(
        f"CREATE OR REPLACE VIEW {ident(name)} AS "
        f"SELECT * FROM read_parquet({literal(str(parquet))})"
    )


def scalar(con: duckdb.DuckDBPyConnection, query: str, params: list[Any] | None = None) -> Any:
    row = con.execute(query, params or []).fetchone()
    return row[0] if row else None
