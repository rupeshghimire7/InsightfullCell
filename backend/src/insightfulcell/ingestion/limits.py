"""Ingestion limits (FR-INGEST-01: 250 MB / 1M rows / 500 columns) and the archive guard."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from insightfulcell.domain.errors import ArchiveBomb, ContentMismatch

MIB: Final = 1024 * 1024


@dataclass(frozen=True, slots=True)
class IngestLimits:
    """Hard limits enforced before and during parsing. Defaults are the v1 product limits."""

    max_file_bytes: int = 250 * MIB
    max_rows: int = 1_000_000
    max_columns: int = 500
    max_column_name_chars: int = 255
    parse_timeout_seconds: float = 300.0
    duckdb_memory_limit: str = "2GB"
    duckdb_threads: int = 2
    # Spreadsheet archive guard
    max_zip_entries: int = 2_000
    max_zip_uncompressed_bytes: int = 2 * 1024 * MIB
    max_zip_compression_ratio: float = 200.0


def check_zip_archive(path: Path, limits: IngestLimits) -> None:
    """Reject zip bombs before openpyxl touches the workbook (xlsx is a zip archive).

    Checks entry count, declared uncompressed total and per-entry compression ratio. Declared
    sizes in the central directory can lie, but ``zipfile`` stops reading an entry at its
    declared size and verifies the CRC, so a lying header cannot inflate more than declared.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise ContentMismatch(
            "The .xlsx file is corrupt or is not an Excel workbook.",
            hint="Open it in Excel and save it again as .xlsx.",
        ) from exc

    if len(infos) > limits.max_zip_entries:
        raise ArchiveBomb(
            f"The workbook contains {len(infos):,} internal files; the limit is "
            f"{limits.max_zip_entries:,}.",
            hint="Remove embedded objects or export the data as CSV.",
        )
    total = sum(info.file_size for info in infos)
    if total > limits.max_zip_uncompressed_bytes:
        raise ArchiveBomb(
            "The workbook expands to more data than we accept.",
            hint="Export the data as CSV or Parquet.",
        )
    for info in infos:
        if info.compress_size > 0 and info.file_size / info.compress_size > (
            limits.max_zip_compression_ratio
        ):
            raise ArchiveBomb(
                "The workbook is compressed at an unsafe ratio.",
                hint="Export the data as CSV or Parquet.",
            )
