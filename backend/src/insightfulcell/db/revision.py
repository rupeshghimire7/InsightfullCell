"""The schema revision this build of the API requires (checked by ``/readyz``).

Bump together with every new migration; ``tests/unit/test_migrations_head.py`` fails if they
drift, so a forgotten bump is caught in CI rather than in production.
"""

EXPECTED_SCHEMA_REVISION = "0005"
