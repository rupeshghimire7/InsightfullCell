"""Print the OpenAPI document deterministically: ``python -m insightfulcell.api.export_openapi``.

The output is committed (``backend/openapi.json``) and the frontend's typed client is generated
from it; CI regenerates both and fails on drift, so the contract can never silently diverge.
"""

from __future__ import annotations

import json
import sys

from insightfulcell.api.app import create_app
from insightfulcell.config import Settings


def build_openapi() -> dict[str, object]:
    # OpenAPI generation does not start the lifespan, so these placeholders are never used.
    settings = Settings(
        environment="test",
        database_url="postgresql+psycopg://placeholder@localhost/placeholder",
        auth_jwks_url="http://localhost/jwks",
        auth_issuer="http://localhost",
    )
    return create_app(settings).openapi()


def main() -> int:
    sys.stdout.write(json.dumps(build_openapi(), indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
