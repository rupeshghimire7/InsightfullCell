from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError

from insightfulcell.config import Settings
from insightfulcell.db.revision import EXPECTED_SCHEMA_REVISION

BACKEND = Path(__file__).parents[2]
BASE = {
    "database_url": "postgresql+psycopg://u@h/db",
    "auth_jwks_url": "https://web.example/api/auth/jwks",
    "auth_issuer": "https://web.example",
}


def test_readiness_revision_matches_alembic_head() -> None:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    assert ScriptDirectory.from_config(config).get_current_head() == EXPECTED_SCHEMA_REVISION


def test_database_url_must_use_the_psycopg_driver() -> None:
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "database_url": "postgresql://u@h/db"})  # type: ignore[arg-type]


def test_part_size_cannot_go_below_the_s3_minimum() -> None:
    with pytest.raises(ValidationError):
        Settings(**BASE, upload_part_bytes=1024)  # type: ignore[arg-type]


def test_production_requires_https_jwks() -> None:
    with pytest.raises(ValidationError, match="https"):
        Settings(**{**BASE, "auth_jwks_url": "http://web.example/jwks"}, environment="production")  # type: ignore[arg-type]


def test_production_rejects_a_custom_s3_endpoint() -> None:
    with pytest.raises(ValidationError, match="S3_ENDPOINT_URL"):
        Settings(**BASE, environment="production", s3_endpoint_url="http://s3:9000")  # type: ignore[arg-type]


def test_security_critical_settings_have_no_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DATABASE_URL", "AUTH_JWKS_URL", "AUTH_ISSUER"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
