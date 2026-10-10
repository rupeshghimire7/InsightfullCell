# InsightfulCell

Upload a CSV, get a cleaned, analysed and explained dataset. This repository holds **Phases 1 to 3**:

1. Sign-in, organisations and file upload.
2. Analysis runs on a PostgreSQL job queue, with a worker and live progress in the browser.
3. The first half of the analysis: every column is given a meaning and checked for personal data, profiled, planned (by a language model that sees only a masked description, or by built-in rules), and given a cleaning plan. Changes that could remove data wait for a person to approve them.

Findings, charts, the report and questions arrive in the next phases (see [progress.md](progress.md)).

```
backend/    FastAPI service and worker (Python 3.13, uv): auth, uploads, datasets, runs, analysis engine, LLM gateway
frontend/   Next.js 16 web app (Node 22, pnpm): sign-in (Better Auth), uploads, runs, plan and review pages
scripts/    Local and CI checks, e2e runner, DB/S3 helpers, git hooks
sample_data/ Example files, from clean to messy, in every supported format
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

Open <http://localhost:3000> and create an account with a password, or enter an email address and copy the sign-in link that appears in the `make web` terminal (real email is optional).

Try `sample_data/3_complex/field_service_jobs.csv`: the analysis stops at **Your review is needed**, the plan page shows exactly what a model would be sent, and **Approve and continue** finishes the run.

**Language model (optional).** Without a key, every run is planned by the built-in rules, which are a complete fallback. To plan with a model, set one provider in `backend/.env` and restart `make worker`:

```bash
LLM_PROVIDER=gemini          # or openai, anthropic, deepseek, none
GEMINI_API_KEY=...           # the key for the provider you chose
```

Only a masked description of the table is sent: column names, kinds and rounded ranges, never rows. Personal-data columns are reduced to their kind, and any call whose text contains an email address, an identity number, a card number or a value from a personal-data column is blocked before it leaves (ADR-031).

## Before you push

```bash
make check       # exactly what CI runs: format, lint, types, tests, migrations, contract, audits
make fix         # auto-format and auto-fix lint
make e2e         # browser test of the whole stack
```

`make check` works without Docker too: database-dependent steps are skipped with a clear message, and CI runs them. The installed git hooks run the quick checks on commit and the full gate on push. `make e2e` always plans with the rules (`LLM_PROVIDER=none`), so it never spends model credit.

After pulling a new phase: `make migrate`, then restart `make api`, `make worker` and `make web`.

## How it fits together

- **Sign-in** happens in the web app (Better Auth, emailed link, optional Google). It issues a 15-minute EdDSA token; the browser never holds it. The Next.js server calls the API on the browser's behalf (a backend-for-frontend) and the API verifies the token offline using the published public keys.
- **Tenancy** is enforced twice: the API takes the organisation only from the verified token, and PostgreSQL row-level security (forced, keyed on a transaction-local setting) stops a buggy query from crossing tenants.
- **Uploads** go from the browser straight to object storage in parallel parts using presigned URLs; the API only brokers and verifies them (ADR-026).
- **Runs** (ADR-030): `POST /v1/runs` commits a queued run and defers a job on a PostgreSQL queue (Procrastinate). The worker (same image as the API) re-hashes the stored bytes, parses them, checkpoints each stage, and appends events; the browser follows them live over Server-Sent Events from the API with a 5-minute ticket for that one run, falling back to polling.
- **Analysis Phase A** (ADR-031): `ingest` (re-hash, parse, column meanings, personal-data flags) → `profile` (statistics, missing patterns, duplicates, outliers) → `understand` (a typed plan from a model or the rules, validated item by item) → `clean_plan` (typed cleaning operations, each lossless or lossy, measured on every row). A lossy change pauses the run for review; the decision is recorded as a new, immutable plan revision, and a Python recipe that rebuilds the cleaned table can be downloaded.
- **API contract:** `backend/openapi.json` is generated from the code and the frontend's typed client is generated from it. CI fails when either is stale (`scripts/generate-contract.sh`).

## Conventions

- Conventional Commits (`feat(upload): …`), enforced by hook and CI. Pull requests describe *what changed | why* (template).
- Decisions are recorded as ADRs (ADR-025 to ADR-031; kept local with the design baseline, as are the changelog and setup manual). Architecture rules are checked by `import-linter`, not by convention.
- What is done, partial and still to do, phase by phase, is in [progress.md](progress.md).
