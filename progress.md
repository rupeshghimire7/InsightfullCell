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
| 1 | Sign-in, tenancy, API, upload, ingestion engine | Web app, Identity, API service, Upload path, Ingest | 🟡 **current phase**: built scope is green locally; gaps listed below |
| 2 | Runs, job queue, worker, live progress | Job queue and runs, Live progress | ⬜ |
| 3 | Analysis Phase A: ingest + profile in the worker, LLM gateway, understand, clean_plan, review gate | Ingest and profile, Understand, Cleaning, LLM gateway | ⬜ |
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

## Phase 1 · Sign-in, tenancy, API, upload, ingestion engine (current)

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
| Idempotency-Key on mutations | ✅ | Uploads. New mutations must use it too. |
| Keyset pagination | ✅ | Dataset list |
| Health endpoints `/healthz`, `/readyz` | ✅ | |
| OpenAPI → generated TypeScript client, drift check | ✅ | `scripts/generate-contract.sh` |
| Per-organisation rate limits (429 + Retry-After) | ⬜ | |
| If-Match / 412 on edits | ⬜ | Nothing editable yet; needed from Phase 3 (plan review). |
| Trace id in problems | 🟡 | Request id today; OpenTelemetry trace id in Phase 8. |

### Upload path

| Task | Status | Evidence / notes |
|---|---|---|
| `POST /v1/uploads`, presigned multipart URLs (1 hour), server-chosen keys | ✅ | `services/uploads.py` |
| Browser: SHA-256 in a Web Worker, parallel parts, retry, expired-link recovery | ✅ ↔ | 8 MiB parts. Concurrency is 3; baseline says 4 at a time. e2e uploads 9 MB (two signed parts) against RustFS with signature checks. |
| `:complete`: part list, size, magic bytes vs extension | ✅ | |
| Checksum verified | 🟡 ↔ | ADR-026: the browser digest is recorded at `complete` and verified by the ingestion engine. Nothing runs ingestion yet (Phase 2), so today the claim is unverified. |
| Content-addressed `bronze/{org}/{sha256}`, same bytes stored once | ✅ | |
| Abort, expiry, replay-safe completion | ✅ | `tests/integration/test_uploads_api.py`, `test_failure_modes.py` |
| Bucket CORS: PUT from our origin only | ✅ | A bucket rule from `scripts/s3_setup.py`. Verified against RustFS: other origins are refused and `ETag` is exposed. |
| Clean up abandoned uploads and expired idempotency keys | ⬜ | Needs the maintenance queue (Phase 2). |

### Datasets and UI

| Task | Status | Evidence / notes |
|---|---|---|
| Dataset list and detail API | ✅ | `api/routers/datasets.py` |
| Workspace shell, dataset list, upload dropzone | ✅ | Axe tests in `frontend/tests/components` |
| Dataset detail page | ⬜ | The API exists; no page yet. |
| Ingestion status visible to the user | ⬜ | ADR-026 expects a pending state. No status column exists yet; add it with Phase 2. |
| End-to-end journey (sign in → upload) | ✅ | `frontend/e2e/journey.spec.ts`: 2/2 pass locally (iteration 3). Covers upload, dedup, rejected files, sign-out and a cross-tenant check. |

### Ingestion engine (IC-102)

| Task | Status | Evidence / notes |
|---|---|---|
| CSV, TSV, JSON, Parquet, Excel → typed Parquet with DuckDB | ✅ | `ingestion/engine.py`, `tests/unit/test_ingest_engine.py` |
| Limits: 250 MB, 1M rows, 500 columns, parse timeout, Excel zip-bomb guard | ✅ | `ingestion/limits.py` |
| Re-hash and compare with the recorded digest (`IntegrityMismatch`) | ✅ | Library only |
| Error messages never echo cell values | ✅ | |
| Library + CLI, no HTTP/DB/S3 dependency | ✅ | `python -m insightfulcell.ingestion` |
| Called by the worker on real uploads | ⬜ | Phase 2 (IC-007) |
| Personal-data detection (emails, phones, names, national ids) | ⬜ | Phase 3. Required before any LLM call. |
| Column meanings (continuous, nominal, datetime, currency, percentage, identifier, text, geography) | ⬜ | Phase 3. The engine gives physical types only. |

### Phase 1 exit criteria

1. ✅ Docker installed, `make up && make migrate` succeed on the dev machine (iteration 3).
2. ✅ `make check` and `make e2e` green locally, with no skipped steps (iteration 3).
3. ⬜ Code committed on a branch, pull request open, "CI passed" green on GitHub.
4. ⬜ Decide whether the remaining Phase 1 gaps (GitHub sign-in, members/invitations, rate limits, dataset detail page) stay in Phase 1 or move later. Wiring ingestion to uploads is Phase 2 by design (ADR-026).

## Phase 2 · Runs, job queue, worker, live progress (IC-007, IC-008)

| Task | Status |
|---|---|
| `runs` table and state machine: queued, running, awaiting_review, succeeded, failed, cancelled, expired; compare-and-set transitions | ⬜ |
| `POST /v1/runs` → 202 with run id and events URL in under 0.5 s | ⬜ |
| Procrastinate on PostgreSQL, `queueing_lock = run_id`; queues standard, large, maintenance | ⬜ |
| Worker entry point (same image as the API) | ⬜ |
| Two jobs: Phase A (ends at the review gate), Phase B (ends with the report) | ⬜ |
| `run_stages` checkpoints keyed by hash of inputs, config and code version | ⬜ |
| Reconciler and stalled-job reaper; two out-of-memory failures → large queue; three parser crashes → fail with reason | ⬜ |
| Cancel at the next stage boundary; 7-day review expiry with a reminder after 24 hours | ⬜ |
| `run_events` (monthly partitions) + NOTIFY; one LISTEN per API instance | ⬜ |
| 5-minute run-scoped ticket JWT (audience `events`); `GET /v1/runs/{id}/events` SSE with Last-Event-ID replay | ⬜ |
| Browser fetch-based SSE reader with polling fallback | ⬜ |

## Phase 3 · Analysis Phase A

| Task | Status |
|---|---|
| Engine package layout (domain, stages, planning, ports) with ports: ArtifactStore, FactRepository, ReferenceStore, LLMTransport | ⬜ |
| Silver/gold object layout (`silver/{org}/{version}/{plan_hash}.parquet`, `gold/{org}/{run}/{artifact}`) | ⬜ |
| ingest stage in the worker: re-hash, parse, assign meanings, flag personal data, `run_columns` | ⬜ |
| profile stage: per-column statistics, missing patterns, duplicates, outliers | ⬜ |
| LLM gateway: purpose → model, budgets (120k input tokens per run, monthly per org), PII payload block, prompt registry, retries, circuit breaker, `llm_calls` accounting | ⬜ |
| understand stage: masked schema card → typed plan (Sonnet 5), plan cache by schema fingerprint, validator, rule-based planner fallback, `analysis_plans` append-only | ⬜ |
| clean_plan: typed operation catalogue, lossless/lossy, policies always_review / review_lossy / auto_safe | ⬜ |
| Review gate UI + `GET /v1/runs/{id}/plan` + `POST …/cleaning-plan:decide` (If-Match) | ⬜ |

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
| Sign-in by link or Google | Also email + password | ADR-028: lets people in without a mail provider |

## Open questions

- Should `INITIAL_DOCS/` (the baseline) be committed so collaborators and CI see it? Today it is git-ignored.
- Where do the ADRs 001–024 referenced by the baseline live? Only 025 and 026 are in `docs/adr/`.

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

## Last check run

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
