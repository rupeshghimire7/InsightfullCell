from __future__ import annotations

import pytest

from insightfulcell.domain.errors import ContentMismatch, InvalidFilename, UnsupportedFileType
from insightfulcell.ingestion.formats import (
    FileFormat,
    dataset_name_from_filename,
    format_from_filename,
    sanitize_filename,
    sniff,
)


class TestFormatFromFilename:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("sales.csv", FileFormat.CSV),
            ("SALES.CSV", FileFormat.CSV),
            ("a.b.tsv", FileFormat.TSV),
            ("book.xlsx", FileFormat.XLSX),
            ("data.parquet", FileFormat.PARQUET),
            ("rows.json", FileFormat.JSON),
            ("rows.ndjson", FileFormat.JSON),
            ("rows.jsonl", FileFormat.JSON),
        ],
    )
    def test_supported(self, name: str, expected: FileFormat) -> None:
        assert format_from_filename(name) is expected

    def test_legacy_xls_has_actionable_hint(self) -> None:
        with pytest.raises(UnsupportedFileType) as info:
            format_from_filename("old.xls")
        assert ".xlsx" in (info.value.hint or "")

    @pytest.mark.parametrize("name", ["notes.txt", "archive.zip", "noextension", ".csv.exe"])
    def test_unsupported(self, name: str) -> None:
        with pytest.raises(UnsupportedFileType):
            format_from_filename(name)


class TestSanitizeFilename:
    def test_strips_path_components(self) -> None:
        assert sanitize_filename("../../etc/passwd.csv") == "passwd.csv"
        assert sanitize_filename("C:\\Users\\me\\data.csv") == "data.csv"

    def test_removes_control_characters_and_collapses_whitespace(self) -> None:
        assert sanitize_filename("my\x00 \n  report\t.csv") == "my report.csv"

    def test_normalises_unicode(self) -> None:
        assert sanitize_filename("cafe\u0301.csv") == "caf\u00e9.csv"

    @pytest.mark.parametrize("raw", ["", "   ", ".", "..", "/", "\x00\x01"])
    def test_rejects_empty_results(self, raw: str) -> None:
        with pytest.raises(InvalidFilename):
            sanitize_filename(raw)

    def test_truncates_but_keeps_extension(self) -> None:
        result = sanitize_filename("x" * 400 + ".csv")
        assert len(result) == 255
        assert result.endswith(".csv")


def test_dataset_name_drops_extension_and_limits_length() -> None:
    assert dataset_name_from_filename("Home sales 2026.csv") == "Home sales 2026"
    assert len(dataset_name_from_filename("y" * 300 + ".csv")) == 200


class TestSniff:
    def test_parquet_requires_magic(self) -> None:
        sniff(b"PAR1....", FileFormat.PARQUET)
        with pytest.raises(ContentMismatch):
            sniff(b"a,b\n1,2\n", FileFormat.PARQUET)

    def test_xlsx_requires_zip_magic(self) -> None:
        sniff(b"PK\x03\x04rest", FileFormat.XLSX)
        with pytest.raises(ContentMismatch):
            sniff(b"a,b\n1,2\n", FileFormat.XLSX)

    @pytest.mark.parametrize(
        "head",
        [b"PK\x03\x04zip", b"%PDF-1.7", b"\x7fELF\x02", b"\x89PNG\r\n", b"PAR1xyz", b"a,b\x00c\n"],
    )
    def test_csv_rejects_binary(self, head: bytes) -> None:
        with pytest.raises(ContentMismatch):
            sniff(head, FileFormat.CSV)

    def test_csv_accepts_text_utf8_bom_and_utf16(self) -> None:
        sniff(b"a,b\n1,2\n", FileFormat.CSV)
        sniff(b"\xef\xbb\xbfa,b\n", FileFormat.CSV)
        sniff("a,b\n".encode("utf-16"), FileFormat.CSV)  # BOM present, NULs are expected

    @pytest.mark.parametrize("head", [b'[{"a":1}]', b'{"a":1}\n{"a":2}', b"\xef\xbb\xbf  [1]"])
    def test_json_accepts(self, head: bytes) -> None:
        sniff(head, FileFormat.JSON)

    def test_json_rejects_non_json_text(self) -> None:
        with pytest.raises(ContentMismatch):
            sniff(b"hello world", FileFormat.JSON)
