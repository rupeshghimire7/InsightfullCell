# InsightfulCell backend

FastAPI service. See the [root README](../README.md) for setup and `make` commands.

- `src/insightfulcell/domain` pure rules, errors and the run state machine · `ingestion` file → typed Parquet (library + CLI) · `pipeline` analysis stages and checkpoint keys (pure) · `storage` S3 port and adapter · `queue` job-queue port and Procrastinate adapter · `auth` token and ticket verification · `db` engine, sessions, tenancy, LISTEN broker · `services` use cases · `api` HTTP layer · `worker` job runner.
- Run the worker: `uv run python -m insightfulcell.worker` (`--queues`, `--concurrency`, `--name`). It runs analysis phases and the periodic upkeep (reconciler, stalled-job reaper, review expiry, event partitions, abandoned uploads).
- Ingest a file without the server: `uv run python -m insightfulcell.ingestion path/to/file.csv --out result.parquet` (omit `--out` for a dry run)
- Interactive API docs at <http://localhost:8000/docs> when running locally.
- Integration tests need PostgreSQL: set `TEST_DATABASE_ADMIN_URL` (see `make up`); in CI they must run and never skip.
