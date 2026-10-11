# InsightfulCell

Upload a CSV, get a cleaned, analysed and explained dataset. This repository holds **Phases 1 to 6**:

1. Sign-in, organisations and file upload.
2. Analysis runs on a PostgreSQL job queue, with a worker and live progress in the browser.
3. The first half of the analysis: every column is given a meaning and checked for personal data, profiled, planned (by a language model that sees only a masked description, or by built-in rules), and given a cleaning plan. Changes that could remove data wait for a person to approve them.
4. The second half: the approved cleaning is applied, places are resolved to standard regions, extra measures are kept only if they carry signal, every question is tested with the share of false findings across the whole analysis held at 5%, the strongest findings are re-tested on less-cleaned data, and the findings are ranked into a report where every number links to a stored fact and its evidence.
5. The words: each answer is written from its own facts with numbers filled in by the engine, and every sentence is checked against its sources before it is kept.
6. What you read: every chart and map value is computed again from the data before it is shown; a dashboard (headline numbers, top charts, maps of places, suggested questions already answered); a fact inspector behind every citation; the report as one self-contained HTML file (print it to save a PDF); and drill-downs that explore one group on its own as an exploratory sub-run.

Questions about the data (the next phase) and running it in production follow (see [progress.md](progress.md)).

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

Open <http://localhost:3000> and create an account with a password (or use Google, once configured). Sign-in links are off until a mail domain is verified; `EMAIL_LINK_SIGN_IN=on` in `frontend/.env.local` switches them on, printed to the `make web` terminal.

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
BEFORE RUN:
make up && make migrate && make check && make fix && make e2e

`make check` works without Docker too: database-dependent steps are skipped with a clear message, and CI runs them. The installed git hooks run the quick checks on commit and the full gate on push. `make e2e` always plans with the rules (`LLM_PROVIDER=none`), so it never spends model credit.

After pulling a new phase: `make migrate`, then restart `make api`, `make worker` and `make web`.

## How it fits together

- **Sign-in** happens in the web app (Better Auth: password, optional Google, and emailed links when switched on). It issues a 15-minute EdDSA token; the browser never holds it. The Next.js server calls the API on the browser's behalf (a backend-for-frontend) and the API verifies the token offline using the published public keys.
- **Tenancy** is enforced twice: the API takes the organisation only from the verified token, and PostgreSQL row-level security (forced, keyed on a transaction-local setting) stops a buggy query from crossing tenants.
- **Uploads** go from the browser straight to object storage in parallel parts using presigned URLs; the API only brokers and verifies them (ADR-026).
- **Runs** (ADR-030): `POST /v1/runs` commits a queued run and defers a job on a PostgreSQL queue (Procrastinate). The worker (same image as the API) re-hashes the stored bytes, parses them, checkpoints each stage, and appends events; the browser follows them live over Server-Sent Events from the API with a 5-minute ticket for that one run, falling back to polling.
- **Analysis Phase A** (ADR-031): `ingest` (re-hash, parse, column meanings, personal-data flags) → `profile` (statistics, missing patterns, duplicates, outliers) → `understand` (a typed plan from a model or the rules, validated item by item) → `clean_plan` (typed cleaning operations, each lossless or lossy, measured on every row). A lossy change pauses the run for review; the decision is recorded as a new, immutable plan revision, and a Python recipe that rebuilds the cleaned table can be downloaded.
- **Analysis Phase B** (ADR-032): `clean_apply` (cleaned data to `silver/`, plus a lossless-only copy) → `geo` (places to ISO regions against a packaged, digest-pinned reference set) → `features` (signal gate: mutual information must beat 19 shuffles) → `mine` (nine miners; every test logged; Benjamini-Hochberg at 5% across the run decides each fact's tier and wording; drivers only after held-out model skill) → `sensitivity` (top ten findings re-tested on the lossless-only data) → `rank` → `visualize` → `report`. Facts and the test log are append-only.
- **Narration and verification** (ADR-034): each question is narrated separately from its own facts, with numbers as slots the engine fills in; every sentence is checked (sources, stray numbers, direction, strength words, claims of cause, conditions), failures are rewritten once and then replaced by the fact's own statement, and the summary is written last from verified findings. A model outage gives a "narration simplified" report that can be narrated again (`POST /v1/runs/{id}/report:renarrate`). Every verdict is stored in `verification_results`.
- **Charts, dashboard, exports, drill-downs** (ADR-035): every chart point and map region carries a claim (what it measures, over which rows) that the `visualize` stage recomputes with its own query; a chart with any mismatch is withheld. Maps are projected and simplified in the worker from the packaged reference set (small areas hatched, no number). The report and its dashboard are one append-only revision (`GET /v1/runs/{id}/report`, `/dashboard`, `/report.html`). `POST /v1/runs/{id}/drilldowns` starts a sub-run on one column's groups that reuses the parent's Phase A, plan and approved cleaning; its facts are labelled exploratory.
- **API contract:** `backend/openapi.json` is generated from the code and the frontend's typed client is generated from it. CI fails when either is stale (`scripts/generate-contract.sh`).

## Conventions

- Conventional Commits (`feat(upload): …`), enforced by hook and CI. Pull requests describe *what changed | why* (template).
- Decisions are recorded as ADRs (ADR-025 to ADR-035; kept local with the design baseline, as are the changelog and setup manual). Architecture rules are checked by `import-linter`, not by convention.
- What is done, partial and still to do, phase by phase, is in [progress.md](progress.md).
