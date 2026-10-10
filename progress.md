# InsightfulCell progress

The single place that says what exists, what is half-built and what is next. Update it at the end of every iteration.

## Ground rules for this file

- **Baseline:** `INITIAL_DOCS/InsightfulCell System Map.html` (system design v1.1, PRD v1.2, 7 Oct 2026). It is the source of truth. It is kept local (git-ignored).
- **Status is backed by evidence.** ✅ means the code exists *and* tests cover it. Say how it was verified. If a check was not run, write "not run", never "works".
- **Phase plan is ours.** The baseline has no story-level sprint plan (the v1.0 docs that had one, including `03-implementation-plan.md`, are not in the repo). The phases below are derived from the baseline's components. Story ids (IC-xxx) are used only where the code already uses them.
- Legend: ✅ done · 🟡 partial · ⬜ not started · ↔ deliberate deviation from the baseline

## Milestones (from the baseline)

| Milestone | Date |
|---|---|
| Hosted staging | Sprint 4 (until then everything runs locally in Docker Compose) |
| Release 1 alpha | 11 Dec 2026 |
| Release 2 beta | 12 Mar 2027 |
| General availability | 9 Apr 2027 |

## Phases at a glance

| # | Phase | Baseline sections | Status |
|---|---|---|---|
| 0 | Foundation and local environment | Environments and delivery, Technology choices | ✅ local stack runs; CI not yet run on GitHub |
| 1 | Sign-in, tenancy, API, upload, ingestion engine | Web app, Identity, API service, Upload path, Ingest | ✅ built scope green locally; remaining gaps listed below |
| 2 | Runs, job queue, worker, live progress | Job queue and runs, Live progress | ✅ **done** (iteration 6): all 11 tasks built and tested locally |
| 3 | Analysis Phase A: ingest + profile in the worker, LLM gateway, understand, clean_plan, review gate | Ingest and profile, Understand, Cleaning, LLM gateway | ✅ **done** (iterations 7 and 8) |
| 4 | Analysis Phase B: clean_apply, geo, features, mine, facts, sensitivity, rank | Cleaning, Places and maps, Features and mining, Fact store | ⬜ |
| 5 | Narration and verification | Narration and verification, Guardrail layers | ⬜ |
| 6 | Charts, dashboard, report, exports, drill-downs | Charts, dashboard, report | ⬜ |
| 7 | Question data (tiered Q&A) | Question data | ⬜ |
| 8 | Running it: staging, observability, hardening | Security, When things fail, Observability, Caching | ⬜ |
| R2 | Release 2 additions: embeddings (pgvector + HNSW), fused word/meaning search | Fact store and search | ⬜ |

---

## Phase 0 · Foundation and local environment

| Task | Status | Evidence / notes |
|---|---|---|
| Repository layout `backend/` + `frontend/` + `scripts/` | ✅ ↔ | ADR-025. The baseline names `apps/web`, `apps/api`, `apps/worker`, `packages/core`, `packages/adapters`, `packages/contracts`. We keep two top-level folders; the worker will reuse the backend package and image. |
| Layer rules enforced by import-linter | ✅ | `backend/pyproject.toml`, `uv run lint-imports` |
| One check entry point (`make check`, `scripts/check.sh`) and git hooks | ✅ | |
| CI: lint, types, tests, migrations, contract drift, e2e, image builds, audits, "CI passed" | ✅ | `.github/workflows/ci.yml`. Not yet run on GitHub: the repo has no commits of this code. |
| Dockerfiles for API and web | ✅ | Built in CI. The worker needs no third Dockerfile (same image, different command). |
| Local services: PostgreSQL 18 + S3-compatible storage in Compose | ✅ ↔ | PostgreSQL 18 + RustFS 1.0.0 (ADR-027). Both containers healthy; migrations, integration tests and e2e pass on them (iteration 3). |
| Dev machine tooling | ✅ | uv, Node 22.23.3 + pnpm 10.28.0 (`~/.local`), Docker 29.1.3 + Compose 2.40.3, Playwright Chromium build 1194. |
| Migration lint for dangerous operations | ⬜ | Baseline: "linted for dangerous operations". Today `check-migrations.sh` checks up/down/up only. |
| Evaluation suites in CI (planted-effect, corrupted-narrator) | ⬜ | Arrive with Phases 4 and 5. |
| Vercel preview per PR | ⬜ | Phase 8. |

## Phase 1 · Sign-in, tenancy, API, upload, ingestion engine

### Identity and tenancy

| Task | Status | Evidence / notes |
|---|---|---|
| Email sign-in link (Better Auth magic link) | ✅ | `frontend/src/lib/auth-options.ts`, sign-in tests |
| Google sign-in (optional) | ✅ | Shown only when configured (SETUP_MANUAL part 2) |
| Email + password registration and sign-in | ✅ | ADR-028. No verification email or code (needs a mail provider). Password reset ⬜. |
| GitHub sign-in | ⬜ | Baseline lists Google and GitHub. |
| Organisations | 🟡 | One organisation per person (`organizationLimit: 1`). Members and invitations ⬜. |
| 15-minute EdDSA JWT minted server-side, browser never holds it (BFF) | ✅ | `frontend/src/app/api/backend`, `backend/src/insightfulcell/auth/tokens.py` |
| Offline verification against JWKS, algorithm pinned | ✅ | `tests/unit/test_tokens.py` |
| One policy table for role × action | ✅ | `domain/security.py`. Actions so far: upload create/read, dataset read. |
| Roles owner, admin, analyst, viewer | 🟡 | Roles exist; only the upload and dataset actions are defined yet. |
| Transaction-scoped `app.org_id` + forced RLS + composite foreign keys | ✅ | Migration 0002, `tests/integration/test_tenancy.py` |
| Database roles `ic_migrator`, `ic_app`, `ic_readonly`, `ic_maintenance` | ✅ | Migration 0001, plus login roles `ic_api` and `ic_auth`. |

### API service

| Task | Status | Evidence / notes |
|---|---|---|
| RFC 9457 problems from `DomainError`, generic 500 with request id | ✅ | `api/problems.py` |
| Idempotency-Key on mutations | ✅ | Uploads and `POST /v1/runs` (shared `services/idempotency.py`). `:cancel`/`:retry` are state transitions guarded by `If-Match` instead. |
| Keyset pagination | ✅ | Dataset list, run list |
| Health endpoints `/healthz`, `/readyz` | ✅ | |
| OpenAPI → generated TypeScript client, drift check | ✅ | `scripts/generate-contract.sh` |
| Per-organisation rate limits (429 + Retry-After) | ⬜ | |
| If-Match / 412 on edits | ✅ | Runs `:cancel` and `:retry` (`row_version` is the ETag). Phase 3's plan review reuses it. |
| Trace id in problems | 🟡 | Request id today; OpenTelemetry trace id in Phase 8. |

### Upload path

| Task | Status | Evidence / notes |
|---|---|---|
| `POST /v1/uploads`, presigned multipart URLs (1 hour), server-chosen keys | ✅ | `services/uploads.py` |
| Browser: SHA-256 in a Web Worker, parallel parts, retry, expired-link recovery | ✅ ↔ | 8 MiB parts. Concurrency is 3; baseline says 4 at a time. e2e uploads 9 MB (two signed parts) against RustFS with signature checks. |
| `:complete`: part list, size, magic bytes vs extension | ✅ | |
| Checksum verified | ✅ ↔ | ADR-026: the browser digest is recorded at `complete`; the worker's `ingest` stage re-hashes the stored bytes and fails the run with `integrity-mismatch` otherwise (`test_run_execution.py`). |
| Content-addressed `bronze/{org}/{sha256}`, same bytes stored once | ✅ | |
| Abort, expiry, replay-safe completion | ✅ | `tests/integration/test_uploads_api.py`, `test_failure_modes.py` |
| Bucket CORS: PUT from our origin only | ✅ | A bucket rule from `scripts/s3_setup.py`. Verified against RustFS: other origins are refused and `ETag` is exposed. |
| Clean up abandoned uploads and expired idempotency keys | ✅ | Maintenance queue (hourly / daily); multipart parts are aborted (`test_maintenance.py`). |

### Datasets and UI

| Task | Status | Evidence / notes |
|---|---|---|
| Dataset list and detail API | ✅ | `api/routers/datasets.py` |
| Workspace shell, dataset list, upload dropzone | ✅ | Axe tests in `frontend/tests/components` |
| Dataset detail page | ✅ | `/o/{org}/datasets/{id}`: versions, analyses, "Analyse newest version" (iteration 6). |
| Ingestion status visible to the user | ✅ | Each dataset shows its latest run's status; the run page shows verification and parse results. Derived from runs, not a dataset column. |
| End-to-end journey (sign in → upload) | ✅ | `frontend/e2e/journey.spec.ts`: 2/2 pass locally (iteration 3). Covers upload, dedup, rejected files, sign-out and a cross-tenant check. |

### Ingestion engine (IC-102)

| Task | Status | Evidence / notes |
|---|---|---|
| CSV, TSV, JSON, Parquet, Excel → typed Parquet with DuckDB | ✅ | `ingestion/engine.py`, `tests/unit/test_ingest_engine.py` |
| Limits: 250 MB, 1M rows, 500 columns, parse timeout, Excel zip-bomb guard | ✅ | `ingestion/limits.py` |
| Re-hash and compare with the recorded digest (`IntegrityMismatch`) | ✅ | Library only |
| Error messages never echo cell values | ✅ | |
| Library + CLI, no HTTP/DB/S3 dependency | ✅ | `python -m insightfulcell.ingestion` |
| Called by the worker on real uploads | ✅ | `pipeline/ingest.py` (iteration 6) |
| Personal-data detection (emails, phones, names, national ids) | ✅ | Phase 3: `engine/semantics.py`, before any model call |
| Column meanings (continuous, nominal, datetime, currency, percentage, identifier, text, geography) | ✅ | Phase 3: `engine/semantics.py` |

### Phase 1 exit criteria

1. ✅ Docker installed, `make up && make migrate` succeed on the dev machine (iteration 3).
2. ✅ `make check` and `make e2e` green locally, with no skipped steps (iteration 3).
3. ⬜ Code committed on a branch, pull request open, "CI passed" green on GitHub.
4. ⬜ Decide whether the remaining Phase 1 gaps (GitHub sign-in, members/invitations, rate limits) stay in Phase 1 or move later. The dataset detail page and wiring ingestion to uploads were done with Phase 2 (iteration 6).

## Phase 2 · Runs, job queue, worker, live progress (IC-007, IC-008) · done

Decision record: ADR-030. Verified locally in iteration 6 (see "Last check run").

| Task | Status | Evidence / notes |
|---|---|---|
| `runs` table and state machine: queued, running, awaiting_review, succeeded, failed, cancelled, expired; compare-and-set transitions | ✅ | Migration 0004; `domain/runs.py` (transition table tested edge by edge, `test_run_state_machine.py`); `services/run_store.transition` (`UPDATE … WHERE status IN …`, bumps `row_version`). |
| `POST /v1/runs` → 202 with run id and events URL in under 0.5 s | ✅ | `api/routers/runs.py`: 202 + `Location` + `ETag` + `events_url`; Idempotency-Key; commits the run first, then defers (a failed defer leaves it for the reconciler). About 0.3 s in the integration tests' access log. `test_runs_api.py` |
| Procrastinate on PostgreSQL, `queueing_lock = run_id`; queues standard, large, maintenance | ✅ ↔ | Migration 0003: Procrastinate 3.10.0 schema vendored and digest-checked, in its own `procrastinate` schema, `search_path` pinned on its functions. `lock` = run id too. `queue/procrastinate_queue.py` |
| Worker entry point (same image as the API) | ✅ | `python -m insightfulcell.worker` / `make worker`; `test_worker.py` runs a real worker through both phases. |
| Two jobs: Phase A (ends at the review gate), Phase B (ends with the report) | ✅ ↔ | `services/run_executor.py`. Phase A ships the real `ingest` stage; Phase B has no stages until Phase 4 but runs as its own job. Review gate (`awaiting_review`, `approve_review`) tested with fake stages; the decision endpoint is Phase 3. |
| `run_stages` checkpoints keyed by hash of inputs, config and code version | ✅ | `pipeline/stage.checkpoint_key`; reused stages emit `stage.reused`; a run planned by another pipeline fails `pipeline-changed`. |
| Reconciler and stalled-job reaper; two out-of-memory failures → large queue; three parser crashes → fail with reason | ✅ | `services/maintenance.py` (every minute). `MemoryError` and reaped jobs count as crashes; `worker-crashed` after `run_max_attempts` (3). `test_maintenance.py`, `test_run_execution.py` |
| Cancel at the next stage boundary; 7-day review expiry with a reminder after 24 hours | ✅ | `:cancel` (any of queued/running/awaiting_review); the worker stops at the next boundary and keeps finished checkpoints. `sweep_reviews` every 15 minutes. The reminder is an event shown on the run page (no email: needs a mail provider). |
| `run_events` (monthly partitions) + NOTIFY; one LISTEN per API instance | ✅ | Append-only (trigger), partitioned by month with a default partition and `ensure_run_event_partitions` (moves stray rows); insert trigger `NOTIFY ic_run_events` (ids only); `db/notifications.EventBroker`. |
| 5-minute run-scoped ticket JWT (audience `events`); `GET /v1/runs/{id}/events` SSE with Last-Event-ID replay | ✅ | Web: `POST /api/runs/{id}/events-ticket` (Better Auth `signJWT`, audience `insightfulcell-events`, must differ from the API's). API: ticket-only auth, CORS on this path only, keep-alives, ends when the run finishes or the ticket expires. `test_event_stream.py` includes a live stream on a real server woken by NOTIFY. |
| Browser fetch-based SSE reader with polling fallback | ✅ ↔ | `lib/runs/sse.ts`: reconnect with Last-Event-ID, ticket refresh, stall detection, falls back to polling `GET /v1/runs/{id}` every 3 s (plain loop instead of TanStack Query, ADR-030). `tests/lib/sse.test.ts`, `run-progress.test.tsx`, e2e. |

UI delivered with Phase 2: run page (live steps, ingest summary, cancel with confirmation, retry, failure reasons), runs list, dataset page, "Analyse this file" after upload, redesign per `INITIAL_DOCS/design-taste-frontend/SKILL.md` (iteration 6).

## Phase 3 · Analysis Phase A · done

Decision record: ADR-031. Verified in iteration 8 (see "Last check run").

| Task | Status | Evidence / notes |
|---|---|---|
| Engine package with ports: ArtifactStore, FactRepository (plans), ReferenceStore, LLMTransport | ✅ ↔ | `engine/` (semantics, profiling, schema card, planning, cleaning, recipe), `engine/ports.py`. Import-linter keeps the engine free of frameworks. Fact and reference stores arrive with Phase 4. |
| Object layout | 🟡 ↔ | `gold/{org}/{run}/{stage}/{checkpoint}-{artifact}` (keyed by inputs). `silver/{org}/{version}/{plan_hash}.parquet` is written by `clean_apply` (Phase 4). |
| ingest stage in the worker: re-hash, parse, meanings, personal-data flags, `run_columns` | ✅ | `pipeline/ingest.py`, `engine/semantics.py`; `test_engine_semantics.py`, `test_run_execution.py` |
| profile stage: per-column statistics, missing patterns, duplicates, outliers | ✅ | `engine/profiling.py` (DuckDB); `test_phase_a_stages.py` |
| LLM gateway: purpose → model, budgets (120k input tokens per run, monthly per org), PII payload block, prompt registry, retries, breaker, `llm_calls` | ✅ ↔ | `llm/gateway.py`; providers Gemini (default), OpenAI, Anthropic, DeepSeek (ADR-031; the baseline names Claude only). `test_llm_gateway.py`, ledger in `test_plans_api.py`. Model default ids must be re-checked before release. |
| understand stage: masked schema card → typed plan, plan cache by schema fingerprint, validator, rule planner fallback, `analysis_plans` append-only | ✅ | `pipeline/understand.py`, `engine/schema_card.py`, `engine/planning/`; append-only triggers tested |
| clean_plan: typed operation catalogue, lossless/lossy, policies always_review / review_lossy / auto_safe | ✅ | `engine/cleaning/`; `test_engine_cleaning.py` (policies, previews, the cleaned SELECT, the recipe rebuilds the table) |
| Review gate UI + `GET /v1/runs/{id}/plan` + `POST …/cleaning-plan:decide` (If-Match) + `GET …/recipe` | ✅ | Plan page, review page, run-page callout; `test_plans_api.py`, `plans.test.tsx`, e2e "a messy file pauses for review" |
| Saved choices per dataset (names, units, outcome, exclusions) reused by the next run | ✅ | `dataset_preferences`; `test_plans_api.py` |

## Phase 4 · Analysis Phase B

| Task | Status |
|---|---|
| clean_apply: approved plan applied to the original bytes; export as a Python recipe | ⬜ |
| geo: gazetteer resolution, ≥80% match rule, spatial index for points, `ref.geo_reference_sets` | ⬜ |
| features: candidates + signal gate (bias-corrected MI beats 19 shuffles), redundancy removal | ⬜ |
| mine: nine miners in a process pool, `test_log`, Benjamini-Hochberg at 5% per run, driver skill check, leakage flag, small-group suppression (n < 10) | ⬜ |
| facts table: immutable (trigger), provenance, content hash, full-text index | ⬜ |
| sensitivity: top ten findings re-tested on minimally cleaned data | ⬜ |
| rank: significance, effect size, relevance, novelty, de-duplication | ⬜ |

## Phase 5 · Narration and verification

| Task | Status |
|---|---|
| Section-scoped narration with numeric slots (Sonnet 5) | ⬜ |
| Verifier: citations, stray digits, direction, wording strength, causal language, conditions | ⬜ |
| Repair once, then template; section falls back at 30% failure; "narration simplified" marker | ⬜ |
| Haiku judge (can only reject), off until its spike passes | ⬜ |
| Summary last, built only from verified body facts | ⬜ |
| Template narrator as a complete fallback | ⬜ |

## Phase 6 · Charts, dashboard, report

| Task | Status |
|---|---|
| Catalogue chart builders → Vega-Lite spec + checkable claims; recomputed against cleaned data | ⬜ |
| Report and dashboard documents (versioned JSON), suggested questions pre-answered | ⬜ |
| Web rendering: streamed Server Components, lazy vega-embed, citation chips → fact inspector | ⬜ |
| HTML export (Jinja) and PDF (headless Chromium) | ⬜ |
| Maps: TopoJSON by parent region, small areas hatched | ⬜ |
| Drill-downs: `POST /v1/runs/{id}/drilldowns`, exploratory facts | ⬜ |

## Phase 7 · Question data

| Task | Status |
|---|---|
| Split view beside the dashboard | ⬜ |
| Tier 0: suggestion, cache, single fact (no model, no quota) | ⬜ |
| Quota: 5 questions per report; shared budget of 20 tests per report | ⬜ |
| Tier 1: one Haiku call → exactly one strict tool call | ⬜ |
| Tier 2: Sonnet agent, ≤ 3 calls, ≤ 4 tools | ⬜ |
| Six closed tools: search_facts, get_fact, describe_columns, aggregate, compare_groups, request_drilldown | ⬜ |
| Answer cache with entity signature; verified sentences streamed | ⬜ |

## Phase 8 · Running it

| Task | Status |
|---|---|
| Staging on AWS (ECS Fargate, RDS single-AZ, S3) + Vercel, from Sprint 4 | ⬜ |
| OpenTelemetry traces across web, API, queue and worker; Sentry | 🟡 structured JSON logs only |
| Metrics: endpoint RED, queue depth/age, stage durations, model cost, verifier rejection rate | ⬜ |
| Retention: bronze 90 days, lifecycle rules, partition dropping | ⬜ |
| Audit log (append-only) | ⬜ |
| Release flow: tagged release with manual approval | ⬜ |

---

## Known deviations from the baseline

| Baseline says | We do | Why / decision |
|---|---|---|
| `apps/*` + `packages/*` layout | `backend/` + `frontend/` | ADR-025 |
| `:complete` checks size and checksum | Size and magic bytes at `complete`; checksum when ingesting | ADR-026 (reading 250 MB on the request path is slow) |
| 4 parts in flight | 3 | Not decided; align when tuning uploads |
| Google and GitHub sign-in | Google only | GitHub not started |
| MinIO locally | RustFS 1.0.0 (S3-compatible) | ADR-027: the `minio/minio` image is gone from Docker Hub |
| `procrastinate` schema (implied by the schema table) | Procrastinate's SQL vendored into migration 0003, functions' `search_path` pinned | ADR-030: Alembic owns migrations; Procrastinate names its tables without a schema |
| TanStack Query polls when SSE is blocked | A fixed 3-second polling loop | ADR-030: one poll did not justify a provider and dependency |
| The API is not reached by browsers (SETUP_MANUAL 6.5 before) | Browsers reach exactly one API path, the event stream, with CORS | ADR-030 and the baseline's own "Live progress" design; production routes only that path publicly |
| Phase B: eleven stages | Phase B runs as a job with no stages yet | Phase A's four stages are built (Phase 3); Phase B's arrive in Phase 4 |
| Claude for planning and narration | One gateway, four providers (Gemini default); rules when no key | ADR-031: development runs with whichever key is at hand; runs never depend on a model |
| Sign-in by link or Google | Also email + password | ADR-028: lets people in without a mail provider |

## Open questions

- Should `INITIAL_DOCS/` (the baseline) be committed so collaborators and CI see it? Today it is git-ignored.
- Where do the ADRs 001–024 referenced by the baseline live? Only 025–030 are in `docs/adr/`.
- Review reminders are events on the run page only; emailing them needs a verified mail domain (ADR-028/029).

## Iteration log

### Iteration 1 · 2026-10-09 · environment fixes and clean-up

**Why:** `make bootstrap`, `make up` and `make migrate` all failed on the dev machine.

| Cause | Fix |
|---|---|
| `pnpm` missing; system Node was 18 (Next.js 16 needs 22+), no corepack | Installed Node 22.23.3 (checksum-verified) and pnpm 10.28.0 into `~/.local`, no sudo. `bootstrap.sh` now checks the Node version. |
| `docker: command not found` | Needs sudo, so not installed by us: see README "Quick start". `make up` now says so in one line. |
| `make migrate` connection refused | Consequence of no Docker (no PostgreSQL). `make migrate` now checks the port first and says "Run 'make up' first". |
| `make migrate`: Better Auth warns `lastRequest … Expected number but got int8` | False positive: the column is `bigint`, but Better Auth 1.7.7's type map lacks the `int8` alias that PostgreSQL reports. Fixed with `pnpm patch` (`frontend/patches/better-auth@1.7.7.patch`); the Dockerfile copies `patches/` before install. |
| `VIRTUAL_ENV … does not match` warning | A stray empty `.venv` at the repo root was activated. Deleted it; the Makefile now ignores `VIRTUAL_ENV`. |

**Other changes:**
- Merged `.gitignore copy` into `.gitignore`. The committed `.gitignore` ignored `.env.*`, which also hid `backend/.env.example` and `frontend/.env.example`, so a fresh clone could not bootstrap. `INITIAL_DOCS/` stays ignored.
- PostgreSQL 16 → 18 in Compose and CI, to match the baseline. Volume mount changed to `/var/lib/postgresql`, as the 18 images require. Not yet verified (no Docker on the dev machine).
- `CLAUDE.md`, `README.md` and `CHANGELOG.md` now point to this file instead of the missing `docs/03-implementation-plan.md`.
- Clean-up review: a dead-code scan found nothing removable, and every frontend module is imported. `scripts/dev-s3.py` is kept: it is the no-Docker S3 fallback used by `scripts/e2e.sh`.

**Verification:** see "Last check run" below.

### Iteration 2 · 2026-10-09 · `make up` failures

**Why:** `make up` failed twice: without sudo, and then with it.

| Cause | Fix |
|---|---|
| `permission denied … /var/run/docker.sock`: Docker was installed, but the user is not in the `docker` group (`getent group docker` lists no members) | User action: `sudo usermod -aG docker $USER`, then log out and in (or `newgrp docker`). Don't use `sudo make up`: it would create root-owned files. Added to SETUP_MANUAL troubleshooting. |
| `pull access denied for minio/minio`: the repository no longer exists on Docker Hub (HTTP 404), and Quay refuses anonymous pulls | Switched Compose and CI to `rustfs/rustfs:1.0.0` (ADR-027). CORS is now a bucket rule from `s3_setup.py`, as on AWS. Removed the MinIO-only server-wide CORS fallback. Docs updated. |

**How RustFS was checked:** before choosing it, a conformance script (`s3_conformance.py`, kept in the session scratchpad, not the repo) ran every S3 call the backend makes against the RustFS 1.0.0 binary: 13/13 passed. `make up` with the Docker image itself is **not yet run** (blocked by the group membership).

### Iteration 3 · 2026-10-09 · first full local run

**Why:** `make up` succeeded; the user asked for a full check.

- `make up`'s last line ("Bucket … ready … (browser CORS must expose ETag)") is the success message, not an error.
- `make migrate`: both Alembic migrations and the 9 Better Auth tables applied cleanly on PostgreSQL 18.
- **Fixed:** `scripts/e2e.sh` refused to run while `make up` was running ("Port 9000 is already in use"). It now reuses RustFS on port 9000 when that answers `/health`, and falls back to the emulator otherwise.
- The first e2e run failed only because Playwright 1.56.1 needs Chromium build 1194 and the cache had 1243. Installed with `pnpm exec playwright install chromium`.
- `SETUP_MANUAL.md` Part 4 (prove signed multipart uploads and `ETag` CORS against real storage) is now covered automatically by the e2e journey.

### Iteration 4 · 2026-10-09 · password registration; guest tried and removed

**Why:** the user wanted to sign in without receiving a link, and visible registration.

- Sign-in page, three ways in: **Sign in** with email + password (or **Create an account** at `/sign-in?mode=register`: name, email, password), **Email me a sign-in link**, and **Continue with Google**.
- "Testing only" note on the link button, shown on hover, keyboard focus or the ⓘ button, and closed with Escape. It explains the current limit: with `console`, links go to the server terminal; with Resend's `@resend.dev` test sender, they only reach the Resend account owner. When the configured sender is on a verified domain, there is no note.
- Google: shown disabled, with setup steps, until `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` are set (hidden in production).
- Guest sign-in (Better Auth `anonymous`) was added, then removed at the user's request. Its `user.isAnonymous` column was dropped from the local database.
- **Fixed:** "Database schema mismatch, missing column user.isAnonymous" in `make web`. Better Auth validates the schema once, when the auth instance is created; the running dev server built it before the column existed. After a schema change, run `make migrate`, then restart `make web`.
- Known risk (ADR-028): password sign-up does not verify the email.
- Checked: `scripts/check.sh --e2e` (full, nothing skipped) exit 0. Backend 218 tests, frontend 176 tests, `next build`, pip-audit and pnpm audit clean, 3 browser journeys including password registration.

### Iteration 5 · 2026-10-09 · QA pass before tagging; testers' own Resend key

**Why:** the web app crashed with `connect ECONNREFUSED 127.0.0.1:5432` (`session.ts`). The user asked for a clean, tag-ready Phase 1 and a full review.

| Finding | Fix |
|---|---|
| **Cause of the crash:** the Compose containers had stopped (reboot / `make down`), and nothing handled an unreachable auth database | `make up`. Pages now render "Sign-in is unavailable right now" with *Try again* (locally: "start it with `make up`"). `lib/unavailable.ts`, `components/ui/service-unavailable.tsx` |
| Auth pool had no `error` listener: a database restart while idle could crash the web server; no connect timeout (could hang forever) | `pool.on("error")`, `connectionTimeoutMillis: 5000` |
| `getApiToken` turned every error into "signed out"; with a cached session cookie, an outage looped `/sign-in` ⇄ `/o/…`. The home page also redirected to sign-in on any API 401 (loops on an issuer/audience misconfiguration) | Only a 401/403 from Better Auth means signed out; outages raise `ServiceUnavailableError` (BFF: `503 auth-unavailable`). The home page shows the problem instead of redirecting. |
| API answered a database outage with a generic 500 | `DatabaseUnavailable` → `503 database-unavailable` problem (`db/tenant.py`) |
| Expired uploads left their multipart parts in storage forever | Abort the multipart upload (integration test asserts nothing is left) |
| Storage upload errors (e.g. the CORS/`ETag` hint) were shown as "Something went wrong" | `describeUploadError` in the dropzone |
| Sign-in email HTML escaped `&` and `"` only | Also `<` and `>` |
| After an e2e run on port 3100, every upload from `make web` failed ("The connection to storage dropped"): `e2e.sh` reused the `make up` bucket and **replaced** its CORS rule with its own origin | `s3_setup.py --add` keeps existing origins; `e2e.sh` uses it. Verified: e2e 3/3 on :3100, and a PUT preflight from `http://localhost:3000` is still allowed afterwards |
| **Feature (ADR-029):** testers can paste their own Resend API key after pressing **Email me a sign-in link**, under the notice "this is just for the testing version; a registered domain is needed for sending email to any user". Used once, never stored; refused once a verified sender is configured. | `sign-in-form.tsx` (`ResendKeyStep`), `auth-options.ts` (`userResendKey`), `email.ts` |

Not changed (noted for later): the dedup path ignores `dataset_id` when the same bytes already exist in another dataset; uploads still have 3 parts in flight vs. the baseline's 4.

### Iteration 6 · 2026-10-10 · Phase 2: runs, queue, worker, live progress; UI redesign

**Why:** Phase 1 done; build Phase 2 to the checklist above and improve the UI with the design skill.

- **Backend:** migrations 0003 (job queue) and 0004 (runs, stages, events, maintenance functions); `domain/runs.py`, `pipeline/` (stage contract, checkpoint keys, `ingest`), `queue/` (port + Procrastinate adapter), `services/` (`runs`, `run_store`, `run_executor`, `maintenance`, shared `idempotency`), `db/notifications.py`, `api/routers/runs.py` (+ SSE, path-scoped CORS), `worker/`. Import-linter now has 5 contracts (pipeline pure; Procrastinate only in queue and worker; services see ports only; API and worker independent).
- **Frontend:** `lib/runs/` (types, status, reducer, SSE client, API), ticket route, run / runs / dataset pages with loading states, BFF allows `v1/runs` but refuses the stream path, CSP `connect-src` includes `API_PUBLIC_URL`.
- **Design (skill):** read as a redesign-preserve of a B2B, trust-first analysis workspace; dials VARIANCE 4, MOTION 3, DENSITY 5 (the skill marks dashboards and tables out of scope, so its landing rules were applied only to sign-in). Geist + Geist Mono via `next/font`; Phosphor icons replace every hand-drawn SVG; one accent and the existing radii kept; status badges always pair an icon with a word; motion only on state changes and off under reduced motion; split sign-in page; nav `aria-current` bug fixed. Checked in light, dark and at 390 px wide.
- **Found and fixed while testing:** Procrastinate's triggers failed from any non-queue connection (pinned `search_path`); a stalled SSE body could hang a reader whose `fetch` ignores abort (reader now cancelled, stall counted as a failure); runs planned by a different pipeline would have written nowhere (now `pipeline-changed`); copy that promised a version picker the UI lacks was removed.
- **For your local setup:** pull, then `make migrate` (database goes 0002 → 0004), restart `make api`, and start `make worker`.

### Iteration 7 · 2026-10-10 · Phase 3: Analysis Phase A (stopped at the usage limit)

**Built:** `engine/` (semantics + personal-data flags, DuckDB profiling, the masked schema card with token-budget tiers, plan language v1, validator, rule planner, merge, cleaning catalogue + proposals with measured previews, feature and chart catalogues, deterministic Python recipe), `llm/` (gateway: budgets, payload guard, retries, breaker, accounting; providers Gemini (default), OpenAI, Anthropic, DeepSeek via official SDKs), stages `profile`, `understand`, `clean_plan` (ingest v2), migration 0005 (`run_columns`, append-only `analysis_plans` / `cleaning_plans` / `cleaning_ops`, `dataset_preferences`, partitioned `llm_calls`, `usage_counters`, `runs.inputs`), `GET /v1/runs/{id}/plan`, `POST …/cleaning-plan:decide` (If-Match, revision check), `GET …/recipe`. UI: plan page, review page, review callout, stage summaries, column cards with validated chart colour, design fixes (dark sign-in panel, no en dashes, no list spinners, off-white surfaces).

**Verified:** backend ruff, mypy strict, import-linter (6 contracts), unit 322+ pass, integration suite pass (incl. 8 new plan API tests); frontend typecheck, eslint, the four changed test files and the new plan tests (10) pass.

**Not run / left:** full `make check` and `next build`; full frontend vitest run after the last edits; `make e2e` (journey needs a review step for messy files); ADR-031 (multi-provider gateway), CHANGELOG, `.env.example` LLM keys; tracker task rows for Phase 3; performance (~0.15 s per text column in detection).

### Iteration 8 · 2026-10-10 · Phase 3 QA pass, release v0.3.0

**Why:** finish Phase 3 to a taggable state with every check green, fix all bugs, update the README, and apply the design skill to the new screens.

| Finding | Fix |
|---|---|
| **`backend/migrations/` was git-ignored** (the `.gitignore` "extras" block), so CI, the Docker image and any fresh clone could not create the database | `migrations/` removed from `.gitignore` (your choice: CHANGELOG, CLAUDE.md, SETUP_MANUAL and `docs/` stay local) |
| **Wrong merge proposals:** labels that differ only by a number or a one-letter code (`Store 1`/`Store 2`, `item0`/`item1`, `Group A`/`Group B`) were proposed as spellings of one category, so such tables paused for review with a wrong merge. 3 event-stream tests failed on it | `_enumerated()` in `engine/cleaning/proposals.py`; unit test. All sample files re-checked: only real variants remain (`N.T.`/`NT`, `Mt Gambier`/`Mount Gambier`, `Electrician`/`Electrical`, `S. Brooks`/`Sam Brooks`) |
| **Code injection through the recipe:** a file name containing `"""` closed the recipe's docstring, and a column name with a line break escaped a comment, so the downloaded script could run code. A column named `a"` made the download fail (500). Data values could break out of the recipe's chart page (`</script>`) | `_text()` sanitises names in comments and the docstring; SQL with `"""` falls back to a plain literal; `<` escaped in the chart page. Test proves names stay data (and fails without the fix) |
| **Times in the server's zone and locale:** server-rendered dates showed the server clock ("11:27 AM" next to "2:12 pm" on one page), and a client component re-rendering them caused a hydration mismatch | `LocalTime`: UTC on the server and during hydration, the viewer's local time after (`useSyncExternalStore`); used for every date |
| **Accessibility:** scrollable regions (the "exactly as sent" description, wide tables on phones) could not be reached by keyboard (axe `scrollable-region-focusable`, found by the new e2e test) | Focusable, labelled `region`s; jsx-a11y allows `tabIndex` on `region` |
| e2e expected the Phase 2 success text, and "300,000" now appears twice | Assertions updated; new journey **a messy file pauses for review, and approving continues the analysis** (masked model input, approval refused until every change is decided) |
| `make e2e` would call a real model if `backend/.env` had a key | `scripts/e2e.sh` sets `LLM_PROVIDER=none` |
| Review-form component tests timed out at 5 s under the full parallel run (pass alone) | 30 s budget for those interaction-heavy tests |
| LLM settings missing from `backend/.env.example`; ADR-031 referenced but missing | Both added |
| Anthropic fast-tier default `claude-haiku-4-5` (a generation behind) | `claude-haiku-5-5`; prices in `llm/pricing.py` checked against Anthropic's current table. `claude-sonnet-5` kept: the baseline names Sonnet 5 |
| Lint/type errors in `test_plans_api.py` (line length, unused `noqa`, untyped JSON index) | Fixed |

**Design (taste skill, redesign-preserve, B2B trust-first; dials VARIANCE 4, MOTION 3, DENSITY 5 as in iteration 6).** Reviewed in light, dark and at 390 px from e2e screenshots. Long lists now use the skill's alternatives to a flat list: automatic cleaning changes are grouped by kind in folding groups ("3 of 4 on"); the 50-row column editor folds behind "Rename columns, set units or leave columns out"; follow-up questions fold behind "Then N more questions". Mono is for numbers only (column names and formats in the text face). Wider before/after previews (dates no longer cut off). Plainer copy for the features note. No em or en dashes in UI strings; icons are Phosphor only; one accent.

**Not changed (noted):** type detection runs one DuckDB query per text column (~0.15 s each); fine for the 250 MB limit, worth batching later. Integration tests take ~10 minutes (each Phase A run is real).

## Last check run

**Iteration 8 (2026-10-10), PostgreSQL 18 + RustFS running. Release candidate v0.3.0.**

| Step | Result |
|---|---|
| `scripts/check.sh` (everything CI runs, nothing skipped) | pass, except two formatting findings fixed straight after: ruff now also checks `backend/migrations/` (tracked for the first time; reformatted, ASTs proven identical) and a temporary screenshot spec (deleted) |
| Backend ruff, mypy strict (143 files), import-linter (6 contracts) | pass |
| Backend pytest unit + integration | pass: 483 tests, 93.31% coverage (minimum 85%) |
| Migrations up / down / up; OpenAPI contract; pip-audit | pass |
| Frontend prettier, eslint (jsx-a11y), tsc strict, `next build`, pnpm audit | pass |
| Frontend vitest + axe | pass: 324 tests |
| `scripts/e2e.sh` on ports 8100/3100 (real worker, RustFS, `LLM_PROVIDER=none`) | pass: 4/4 journeys, including the new review-gate journey |
| Secret scan | pass (built-in patterns; gitleaks not installed) |
| After the version bump to 0.3.0 | ruff, mypy, import-linter, migrations up/down/up, contract, prettier, eslint, tsc, secret scan and 328 backend unit tests re-run: pass. Integration, vitest and e2e not re-run (only the version string changed for them) |
| GitHub CI | not run: nothing pushed yet |

**Iteration 6 (2026-10-10), PostgreSQL 18 + RustFS running.**

| Step | Result |
|---|---|
| Backend ruff, mypy strict (97 files), import-linter (5 contracts) | pass |
| Backend pytest unit + integration | pass: 390 tests, 95.48% coverage (minimum 85%) |
| Migrations up / down / up | pass |
| OpenAPI contract (backend and generated TS) | pass, up to date |
| Frontend prettier, eslint (jsx-a11y), tsc strict | pass |
| Frontend vitest + axe | pass: 311 tests, 94.9% lines |
| `scripts/e2e.sh` (ports 8100/3100, real worker and RustFS) | pass: 3/3, including upload → analyse → Succeeded, the ticket stream cross-origin, CORS refusal elsewhere, axe on new pages. Run before the last two cosmetic UI tweaks (finished runs hide the connection line; durations in sans); those are covered by component tests. |
| Secret scan | pass |
| `next build`, pip-audit, pnpm audit (new deps: procrastinate, geist, @phosphor-icons/react) | **not run** this iteration (the full non-`--fast` gate was stopped at your request); run `make check` before pushing. |
| GitHub CI | not run: nothing committed or pushed |


**Iteration 5 (2026-10-09), PostgreSQL 18 + RustFS running. Nothing skipped.**

| Step | Result |
|---|---|
| `scripts/check.sh` (everything CI runs) | pass, exit 0 |
| Backend pytest unit + integration | pass: 221 tests |
| Frontend vitest + axe | pass: 214 tests; `next build`, pnpm audit and pip-audit clean |
| `scripts/e2e.sh` (Playwright) | pass: 3/3, including the new key step and its notice (run on ports 3100/8100) |
| Manual: production build with the database unreachable | `/sign-in` and `/o/…` show the unavailable page; BFF returns `503 auth-unavailable` |
| GitHub CI | not run: nothing is committed or pushed |


**Iteration 3 (2026-10-09), dev machine with PostgreSQL 18 + RustFS 1.0.0 running. Nothing skipped.**

| Step | Result |
|---|---|
| `make check`: backend ruff, mypy strict, import-linter | pass |
| Backend pytest unit + integration (real PostgreSQL, RLS, tenancy, uploads, failure modes) | pass: 218 tests, 95.16% coverage (minimum 85%) |
| Migrations up / down / up | pass |
| OpenAPI contract, pip-audit | pass, no known vulnerabilities |
| Frontend prettier, eslint, tsc strict, vitest + axe (155 tests), API types, `next build`, pnpm audit | pass |
| Secret scan | pass (built-in patterns; gitleaks not installed) |
| `scripts/e2e.sh` (Playwright, Chromium) | pass: 2/2 |
| GitHub CI | not run: nothing is committed or pushed |

**Iteration 2 (2026-10-09):** `scripts/check.sh --backend --fast` passed: ruff, mypy, import-linter, 140 unit tests, contract. Database steps were skipped (no database). Frontend not re-run: no frontend files changed. `docker compose config` is valid, and `ci.yml` parses.

**Iteration 1, 2026-10-09, `make check-fast`, dev machine without Docker. Exit 0.**

| Step | Result |
|---|---|
| Backend: ruff format, ruff check, mypy strict, import-linter | pass |
| Backend: pytest unit | pass (140 tests) |
| Backend: pytest integration (PostgreSQL + RLS), migrations up/down/up | **skipped: no database** |
| Backend + frontend: OpenAPI contract up to date | pass |
| Frontend: prettier, eslint (jsx-a11y), tsc strict | pass |
| Frontend: vitest with axe | pass (155 tests, 94.5% lines) |
| Frontend: next build, pip-audit, pnpm audit | skipped (`--fast`) |
| Secret scan | pass (built-in patterns; gitleaks not installed) |
| e2e (`make e2e`) | not run |

So the tenancy, upload and migration tests, and the PostgreSQL 18 switch, are **unverified** until Docker is installed.
