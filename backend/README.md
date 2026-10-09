# InsightfulCell backend

FastAPI service. See the [root README](../README.md) for setup and `make` commands.

- `src/insightfulcell/domain` pure rules and errors · `ingestion` file → typed Parquet (library + CLI) · `storage` S3 port and adapter · `auth` token verification · `db` engine, sessions, tenancy · `services` use cases · `api` HTTP layer.
- Ingest a file without the server: `uv run python -m insightfulcell.ingestion path/to/file.csv --out result.parquet` (omit `--out` for a dry run)
- Interactive API docs at <http://localhost:8000/docs> when running locally.
- Integration tests need PostgreSQL: set `TEST_DATABASE_ADMIN_URL` (see `make up`); in CI they must run and never skip.
