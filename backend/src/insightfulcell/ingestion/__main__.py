"""Developer CLI: ``python -m insightfulcell.ingestion FILE`` prints how a file would ingest.

Prints the schema and row count only, never cell values, so it is safe to paste into issues.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from insightfulcell.domain.errors import DomainError
from insightfulcell.ingestion.engine import ingest_file
from insightfulcell.ingestion.formats import format_from_filename


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run the ingestion engine on a file.")
    parser.add_argument("file", type=Path)
    parser.add_argument(
        "--out", type=Path, help="Where to keep the Parquet output (default: temp)."
    )
    args = parser.parse_args(argv)

    try:
        fmt = format_from_filename(args.file.name)
        with tempfile.TemporaryDirectory() as tmp:
            dest = args.out or Path(tmp) / "out.parquet"
            result = ingest_file(args.file, fmt, dest)
            summary = {
                "format": result.format.value,
                "rows": result.rows,
                "columns": [{"name": c.name, "type": c.type} for c in result.columns],
                "sha256": result.source_sha256,
                "source_bytes": result.source_bytes,
                "parquet_bytes": result.parquet_bytes,
                "warnings": list(result.warnings),
            }
    except DomainError as exc:
        print(json.dumps({"error": exc.code, "detail": exc.detail, "hint": exc.hint}, indent=2))
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
