# InsightfulCell

Upload a CSV, get a cleaned, analysed and explained dataset. This repository holds **Phases 1 and 2**: sign-in, organisations and file upload; and analysis runs on a PostgreSQL job queue, with a worker that verifies and parses each upload and live progress in the browser.

```
backend/    FastAPI service and worker (Python 3.13, uv): auth, uploads, datasets, runs, ingestion
frontend/   Next.js 16 web app (Node 22, pnpm): sign-in (Better Auth), workspace shell, upload UI
scripts/    Local and CI checks, e2e runner, DB/S3 helpers, git hooks
docs/       Architecture decision records
progress.md Phases, tasks and status against the design baseline (INITIAL_DOCS/, kept local)
```

## Quick start

Needs Docker, [uv](https://docs.astral.sh/uv/), Node 22+ and pnpm (`corepack enable`). On Ubuntu 24.04:

```bash
sudo apt install docker.io docker-compose-v2 && sudo usermod -aG docker "$USER"   # then log out and in
```

```bash
make bootstrap   # install dependencies, create .env files, install git hooks
make up          # PostgreSQL + RustFS (S3-compatible object storage)
make migrate     # database schema and the web app's auth tables
make api         # terminal 1 -> http://localhost:8000/docs
make worker      # terminal 2: runs analyses and periodic upkeep from the job queue
make web         # terminal 3 -> http://localhost:3000
```

Open <http://localhost:3000> and create an account with a password, or enter an email address and copy the sign-in link that appears in the `make web` terminal (real email is optional: see [SETUP_MANUAL.md](SETUP_MANUAL.md)).

## Before you push

```bash
make check       # exactly what CI runs: format, lint, types, tests, migrations, contract, audits
make fix         # auto-format and auto-fix lint
make e2e         # browser test of the whole stack
```

`make check` works without Docker too: database-dependent steps are skipped with a clear message, and CI runs them. The installed git hooks run the quick checks on commit and the full gate on push.

## How it fits together

- **Sign-in** happens in the web app (Better Auth, emailed link, optional Google). It issues a 15-minute EdDSA token; the browser never holds it. The Next.js server calls the API on the browser's behalf (a backend-for-frontend) and the API verifies the token offline using the published public keys.
- **Tenancy** is enforced twice: the API takes the organisation only from the verified token, and PostgreSQL row-level security (forced, keyed on a transaction-local setting) stops a buggy query from crossing tenants.
- **Uploads** go from the browser straight to object storage in parallel parts using presigned URLs; the API only brokers and verifies them (ADR-026).
- **Runs** (ADR-030): `POST /v1/runs` commits a queued run and defers a job on a PostgreSQL queue (Procrastinate). The worker (same image as the API) re-hashes the stored bytes, parses them, checkpoints each stage, and appends events; the browser follows them live over Server-Sent Events from the API with a 5-minute ticket for that one run, falling back to polling.
- **API contract:** `backend/openapi.json` is generated from the code and the frontend's typed client is generated from it. CI fails when either is stale (`scripts/generate-contract.sh`).

## Conventions

- Conventional Commits (`feat(upload): …`), enforced by hook and CI. Every change is described as *what changed | why* in [CHANGELOG.md](CHANGELOG.md) and the pull request template.
- Decisions are recorded in [docs/adr](docs/adr). Architecture rules are checked by `import-linter`, not by convention.
- What is done, partial and still to do, phase by phase, is in [progress.md](progress.md).
