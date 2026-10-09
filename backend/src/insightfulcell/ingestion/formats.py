"""File formats, filename hygiene and content sniffing (FR-INGEST-01).

Pure functions, no I/O: they run on the API (cheap pre-flight checks on the first bytes of an
upload) and on the worker (before parsing). Anything expensive belongs in ``engine``.
"""

from __future__ import annotations

import unicodedata
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Final

from insightfulcell.domain.errors import ContentMismatch, InvalidFilename, UnsupportedFileType

MAX_FILENAME_CHARS: Final = 255
SNIFF_BYTES: Final = 8192  # how much of the object start the API reads to sniff the type


class FileFormat(StrEnum):
    CSV = "csv"
    TSV = "tsv"
    XLSX = "xlsx"
    PARQUET = "parquet"
    JSON = "json"


EXTENSIONS: Final[dict[str, FileFormat]] = {
    ".csv": FileFormat.CSV,
    ".tsv": FileFormat.TSV,
    ".xlsx": FileFormat.XLSX,
    ".parquet": FileFormat.PARQUET,
    ".json": FileFormat.JSON,
    ".ndjson": FileFormat.JSON,
    ".jsonl": FileFormat.JSON,
}

_ACCEPTED = ", ".join(sorted(EXTENSIONS))

# Signatures of binary containers that must never be accepted as a text format.
_BINARY_SIGNATURES: Final[tuple[bytes, ...]] = (
    b"PK\x03\x04",  # zip (and therefore xlsx, docx, jar, ...)
    b"PK\x05\x06",  # empty zip
    b"\x1f\x8b",  # gzip
    b"%PDF",
    b"\x7fELF",
    b"MZ",  # Windows executable
    b"\x89PNG",
    b"\xff\xd8\xff",  # JPEG
    b"GIF8",
    b"PAR1",  # parquet
    b"\xd0\xcf\x11\xe0",  # OLE2: legacy .xls/.doc
    b"7z\xbc\xaf",
    b"Rar!",
    b"SQLite format 3",
)
_UTF16_BOMS: Final = (b"\xff\xfe", b"\xfe\xff")
_UTF8_BOM: Final = b"\xef\xbb\xbf"


def format_from_filename(filename: str) -> FileFormat:
    """Map a (sanitised) file name to a supported format by extension."""
    suffix = PurePosixPath(filename.lower()).suffix
    if suffix == ".xls":
        raise UnsupportedFileType(
            "Legacy .xls spreadsheets are not supported.",
            hint="Save the file as .xlsx (Excel Workbook) or CSV and upload it again.",
        )
    try:
        return EXTENSIONS[suffix]
    except KeyError:
        shown = suffix or "(no extension)"
        raise UnsupportedFileType(
            f"Files of type {shown} are not supported.",
            hint=f"Upload one of: {_ACCEPTED}.",
        ) from None


def sanitize_filename(raw: str) -> str:
    """Return a safe display name: base name only, NFC, no control characters, <= 255 chars.

    The name is only ever *displayed* (storage keys are derived from ids and hashes), but it
    flows into UI, logs and exports, so path parts and control characters are removed.
    """
    name = unicodedata.normalize("NFC", raw)
    name = name.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = " ".join(name.split())  # collapse runs of whitespace, trim ends
    if name in {"", ".", ".."}:
        raise InvalidFilename(
            "The file name is empty after removing unsafe characters.",
            hint="Rename the file and try again.",
        )
    if len(name) > MAX_FILENAME_CHARS:
        suffix = PurePosixPath(name).suffix
        keep = MAX_FILENAME_CHARS - len(suffix)
        name = name[:keep].rstrip() + suffix
    return name


def dataset_name_from_filename(filename: str, *, max_chars: int = 200) -> str:
    """Default dataset name: the file name without its extension."""
    stem = PurePosixPath(filename).stem or filename
    return stem[:max_chars].strip() or filename[:max_chars]


def sniff(head: bytes, fmt: FileFormat) -> None:
    """Check the first bytes of a file against its claimed format.

    Raises ``ContentMismatch`` when the content cannot be what the extension says. This is a
    cheap pre-flight, not validation: the parser is the authority on whether a file is valid.
    """
    if fmt is FileFormat.PARQUET:
        if not head.startswith(b"PAR1"):
            raise ContentMismatch(
                "The file does not look like a Parquet file.",
                hint="Check the file was exported as Parquet, or upload it as CSV.",
            )
        return
    if fmt is FileFormat.XLSX:
        if not head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
            raise ContentMismatch(
                "The file does not look like an .xlsx workbook.",
                hint="Open it in Excel and save it as an Excel Workbook (.xlsx).",
            )
        return
    _sniff_text(head, fmt)


def _sniff_text(head: bytes, fmt: FileFormat) -> None:
    if head.startswith(_UTF16_BOMS):
        return  # UTF-16 text is decoded by the ingestion engine
    for signature in _BINARY_SIGNATURES:
        if head.startswith(signature):
            raise ContentMismatch(
                f"The file looks like a binary file, not {fmt.value.upper()} text.",
                hint="Check the file type, or export your data as CSV.",
            )
    if b"\x00" in head:
        raise ContentMismatch(
            f"The file contains binary data and is not valid {fmt.value.upper()} text.",
            hint="Export the data as UTF-8 CSV and upload it again.",
        )
    if fmt is FileFormat.JSON:
        body = head.removeprefix(_UTF8_BOM).lstrip()
        if body[:1] not in {b"{", b"["}:
            raise ContentMismatch(
                "The file does not start like JSON (expected '{' or '[').",
                hint="Upload a JSON array of records or newline-delimited JSON.",
            )
