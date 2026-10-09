from __future__ import annotations

import json
from pathlib import Path

import pytest

from insightfulcell.api.export_openapi import build_openapi
from insightfulcell.ingestion.__main__ import main

BACKEND_DIR = Path(__file__).resolve().parents[2]


class TestIngestCli:
    def test_prints_schema_without_cell_values(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = tmp_path / "people.csv"
        source.write_text("name,age\nZebulon-Secret,41\nAda,36\n")
        assert main([str(source)]) == 0
        out = capsys.readouterr().out
        summary = json.loads(out)
        assert summary["rows"] == 2
        assert [c["name"] for c in summary["columns"]] == ["name", "age"]
        assert "Zebulon-Secret" not in out

    def test_failure_is_reported_as_json_and_nonzero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = tmp_path / "notes.txt"
        source.write_text("hello")
        assert main([str(source)]) == 1
        assert json.loads(capsys.readouterr().out)["error"] == "unsupported-file-type"


class TestOpenApiContract:
    def test_committed_contract_matches_the_code(self) -> None:
        """Fails when an endpoint or schema changed without regenerating openapi.json."""
        committed = json.loads((BACKEND_DIR / "openapi.json").read_text())
        assert committed == json.loads(json.dumps(build_openapi())), (
            "backend/openapi.json is stale: run scripts/generate-contract.sh"
        )

    def test_every_operation_declares_problem_responses_and_security(self) -> None:
        spec = build_openapi()
        paths = spec["paths"]
        assert isinstance(paths, dict)
        for path, operations in paths.items():
            if path in {"/healthz", "/readyz"}:
                continue
            for method, operation in operations.items():
                assert "401" in operation["responses"], f"{method} {path}"
                assert operation["security"], f"{method} {path}"
