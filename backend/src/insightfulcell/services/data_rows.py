"""The rows of the table a run analysed, a page at a time ("See the data", ADR-040).

The rows are the ingest stage's typed Parquet: the uploaded file as read, before any cleaning.
Columns flagged as personal data at ingest stay hidden here as everywhere else (only their name
and kind are shown). The Parquet and its semantics are immutable artifacts, so the API keeps a
few recently read ones on local disk instead of downloading them for every page; each request
still checks the run in the caller's organisation first, and copies not read for a while are
removed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from decimal import Decimal
from pathlib import Path
from typing import Any, Final
from uuid import UUID

import duckdb
from sqlalchemy import select

from insightfulcell.db.models import RunStage
from insightfulcell.db.tenant import TenantDatabase
from insightfulcell.domain.errors import DataNotAvailable
from insightfulcell.domain.runs import StageStatus
from insightfulcell.domain.security import Action, Principal, require
from insightfulcell.services import run_store
from insightfulcell.storage.port import ObjectStore

MAX_PAGE_ROWS: Final = 200
#: Longest text shown in one cell; the rest is cut (the sidebar is for looking, not exporting).
MAX_CELL_CHARS: Final = 500
#: Largest JSON-safe integer; bigger ones are sent as text so the browser shows every digit.
_MAX_SAFE_INT: Final = 2**53 - 1

Cell = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class DataColumn:
    name: str
    type: str
    #: The kind of personal data when the column is hidden, else None.
    personal_data: str | None


@dataclass(frozen=True, slots=True)
class DataPage:
    columns: list[DataColumn]
    rows: list[list[Cell]]
    total_rows: int
    offset: int


class LocalArtifactCache:
    """Recently read artifacts on local disk, keyed by their (immutable) storage key."""

    def __init__(
        self,
        store: ObjectStore,
        directory: Path | None = None,
        *,
        max_files: int = 16,
        max_idle_seconds: float = 3600,
    ) -> None:
        self._store = store
        self._dir = directory or Path(tempfile.gettempdir()) / "insightfulcell-data-cache"
        self._max_files = max_files
        self._max_idle = max_idle_seconds
        self._locks: dict[str, asyncio.Lock] = {}

    async def path(self, key: str) -> Path:
        await asyncio.to_thread(self._dir.mkdir, mode=0o700, parents=True, exist_ok=True)
        name = hashlib.sha256(key.encode()).hexdigest()
        target = self._dir / name
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            if target.exists():
                await asyncio.to_thread(os.utime, target)  # last read: kept longer
            else:
                partial = self._dir / f"{name}.{os.getpid()}.partial"
                await self._store.download_file(key, partial)
                await asyncio.to_thread(partial.replace, target)
        await asyncio.to_thread(self._evict, keep=target)
        return target

    def _evict(self, *, keep: Path) -> None:
        now = time.time()
        files = sorted(
            (p for p in self._dir.iterdir() if p.is_file() and p != keep),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for index, file in enumerate(files):
            idle = now - file.stat().st_mtime > self._max_idle
            # Another request may still be writing a partial download: only an old one goes.
            if idle or (index + 1 >= self._max_files and not file.name.endswith(".partial")):
                file.unlink(missing_ok=True)


class DataRowsService:
    def __init__(self, db: TenantDatabase, cache: LocalArtifactCache) -> None:
        self._db = db
        self._cache = cache

    async def page(
        self, principal: Principal, run_id: UUID, *, offset: int, limit: int
    ) -> DataPage:
        require(principal, Action.DATA_READ)
        async with self._db.transaction(principal) as session:
            await run_store.require_run(session, principal.org_id, run_id)
            ingest = (
                await session.execute(
                    select(RunStage).where(
                        RunStage.org_id == principal.org_id,
                        RunStage.run_id == run_id,
                        RunStage.name == "ingest",
                    )
                )
            ).scalar_one_or_none()
        artifacts = dict(ingest.artifacts) if ingest is not None else {}
        if ingest is None or ingest.status != StageStatus.SUCCEEDED or "data" not in artifacts:
            raise DataNotAvailable(
                "The rows are shown once the analysis has read the file.",
                hint="Wait for the first step of the analysis to finish, then open it again.",
            )
        parquet = await self._cache.path(str(artifacts["data"]))
        hidden: dict[str, str] = {}
        if "semantics" in artifacts:
            semantics_file = await self._cache.path(str(artifacts["semantics"]))
            semantics = json.loads(await asyncio.to_thread(semantics_file.read_text, "utf-8"))
            hidden = {s["name"]: s["personal_data"] for s in semantics if s.get("personal_data")}
        return await asyncio.to_thread(
            _read_page, parquet, hidden, offset, max(1, min(limit, MAX_PAGE_ROWS))
        )


def _read_page(parquet: Path, hidden: dict[str, str], offset: int, limit: int) -> DataPage:
    con = duckdb.connect(config={"threads": 1, "memory_limit": "256MB"})
    try:
        source = f"read_parquet('{_quoted(parquet)}')"
        described = con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()  # noqa: S608
        columns = [DataColumn(str(r[0]), str(r[1]), hidden.get(str(r[0]))) for r in described]
        shown = [c.name for c in columns if c.personal_data is None]
        total = int(con.execute(f"SELECT count(*) FROM {source}").fetchone()[0])  # type: ignore[index]  # noqa: S608
        values: list[tuple[Any, ...]] = []
        if shown:
            select_list = ", ".join(f'"{name.replace(chr(34), chr(34) * 2)}"' for name in shown)
            values = con.execute(
                f"SELECT {select_list} FROM {source} LIMIT ? OFFSET ?",  # noqa: S608
                [limit, offset],
            ).fetchall()
        elif offset < total:
            values = [() for _ in range(min(limit, total - offset))]
    finally:
        con.close()
    rows: list[list[Cell]] = []
    for value in values:
        cells = iter(value)
        rows.append([None if c.personal_data else _cell(next(cells)) for c in columns])
    return DataPage(columns=columns, rows=rows, total_rows=total, offset=offset)


def _quoted(path: Path) -> str:
    return str(path).replace("'", "''")


def _cell(value: Any) -> Cell:  # noqa: PLR0911  (one branch per type)
    if value is None or isinstance(value, bool | str):
        return value[:MAX_CELL_CHARS] if isinstance(value, str) else value
    if isinstance(value, int):
        return value if abs(value) <= _MAX_SAFE_INT else str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date | dt_time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return json.dumps(value, default=str, ensure_ascii=False)[:MAX_CELL_CHARS]
