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
| 3 | Analysis Phase A: ingest + profile in the worker, LLM gateway, understand, clean_plan, review gate | Ingest and profile, Understand, Cleaning, LLM gateway | ✅ **done** (iterations 7 and 8), plus plan revision with the person's requests (iteration 9 audit) |
| 4 | Analysis Phase B: clean_apply, geo, features, mine, facts, sensitivity, rank | Cleaning, Places and maps, Features and mining, Fact store | ✅ **done** (iteration 9); maps arrived in Phase 6 (rates per resident and spatial clustering not in v1, ADR-035) |
| 5 | Narration and verification | Narration and verification, Guardrail layers | ✅ **done** (iteration 10): slots, verifier, repair, fallbacks, judge (off by default), re-narration, corrupted-narrator suite |
| 6 | Charts, dashboard, report, exports, drill-downs | Charts, dashboard, report | ✅ **done** (iteration 11): checked charts and maps, dashboard, fact inspector, HTML export (PDF by printing), drill-downs; deviations in ADR-035 |
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
| Evaluation suites in CI (planted-effect, corrupted-narrator) | ✅ | Planted-effect: `test_engine_evidence.py`. Corrupted-narrator: `test_narration.py` (31 planted errors across every check must all be rejected, 11 clean sentences all kept); both run with every unit suite in CI. |
| Vercel preview per PR | ⬜ | Phase 8. |

## Phase 1 · Sign-in, tenancy, API, upload, ingestion engine

### Identity and tenancy

| Task | Status | Evidence / notes |
|---|---|---|
| Email sign-in link (Better Auth magic link) | ✅ ↔ | Built and tested, but **off by default** (`EMAIL_LINK_SIGN_IN=off`, ADR-033): the server has no endpoint and the button is shown inert with a note, after "or", below Google. `auth-options.test.ts`, sign-in tests, e2e checks the 404. |
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
| Two jobs: Phase A (ends at the review gate), Phase B (ends with the report) | ✅ ↔ | `services/run_executor.py`. Phase A ships the real `ingest` stage; Phase B runs as its own job (its seven stages arrived in Phases 3 and 4). Review gate (`awaiting_review`, `approve_review`) tested with fake stages; the decision endpoint is Phase 3. |
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
| Object layout | ✅ ↔ | `gold/{org}/{run}/{stage}/{checkpoint}-{artifact}` (keyed by inputs); `clean_apply` writes cleaned data to `silver/{org}/{version}/{hash}.parquet`, the hash covering the plan and the accepted operations (Phase 4). |
| ingest stage in the worker: re-hash, parse, meanings, personal-data flags, `run_columns` | ✅ | `pipeline/ingest.py`, `engine/semantics.py`; `test_engine_semantics.py`, `test_run_execution.py` |
| profile stage: per-column statistics, missing patterns, duplicates, outliers | ✅ | `engine/profiling.py` (DuckDB); `test_phase_a_stages.py` |
| LLM gateway: purpose → model, budgets (120k input tokens per run, monthly per org), PII payload block, prompt registry, retries, breaker, `llm_calls` | ✅ ↔ | `llm/gateway.py`; providers Gemini (default), OpenAI, Anthropic, DeepSeek (ADR-031; the baseline names Claude only). `test_llm_gateway.py`, ledger in `test_plans_api.py`. Model default ids must be re-checked before release. |
| understand stage: masked schema card → typed plan, plan cache by schema fingerprint, validator, rule planner fallback, `analysis_plans` append-only | ✅ | `pipeline/understand.py`, `engine/schema_card.py`, `engine/planning/`; append-only triggers tested |
| clean_plan: typed operation catalogue, lossless/lossy, policies always_review / review_lossy / auto_safe | ✅ | `engine/cleaning/`; `test_engine_cleaning.py` (policies, previews, the cleaned SELECT, the recipe rebuilds the table) |
| Review gate UI + `GET /v1/runs/{id}/plan` + `POST …/cleaning-plan:decide` (If-Match) + `GET …/recipe` | ✅ | Plan page, review page, run-page callout; `test_plans_api.py`, `plans.test.tsx`, e2e "a messy file pauses for review" |
| Saved choices per dataset (names, units, outcome, exclusions) reused by the next run | ✅ | `dataset_preferences`; `test_plans_api.py` |
| Plan again from the review: requests in the person's own words, removed questions, `POST …/plan:revise` (If-Match) | ✅ | Found uncommitted in the iteration 9 audit and checked: `engine/planning/requests.py` (the engine's reading of a request; anything it cannot map is reported as not understood), the model reads requests when configured, Phase A re-runs reusing unchanged checkpoints, every request gets an outcome. `test_engine_analysis.py` (requests), `test_plans_api.py`. |
| Recipe page in the UI (the Python recipe, with the privacy note) | ✅ | `/o/{org}/runs/{id}/recipe`, `components/plans/recipe-view.tsx`, `privacy-note.tsx` |

## Phase 4 · Analysis Phase B · done

Decision record: ADR-032. Verified in iteration 9 (see "Last check run"). Phase B runs `clean_apply` → `geo` → `features` → `mine` → `sensitivity` → `rank` → `report`, each checkpointed.

| Task | Status | Evidence / notes |
|---|---|---|
| clean_apply: approved plan applied to the original bytes; export as a Python recipe | ✅ | `pipeline/clean_apply.py` (v2): accepted operations only, same SQL as the recipe; cleaned data to silver; a minimally cleaned table (lossless operations only) for the sensitivity check. Recipe from Phase 3. |
| geo: gazetteer resolution, ≥80% match rule, spatial index for points, `ref.geo_reference_sets` | ✅ ↔ | `engine/geo/`: exact, alias, then fuzzy (RapidFuzz, full names only, unique best) matching per scope (all countries, or one country's states), the scope most values match wins ("WA", "CA", "GA" resolve correctly); Australian postcodes by Australia Post ranges; coordinates through a Shapely STRtree, with a 5 km coast margin. Reference set packaged (Natural Earth 5.1.2 1:50m, public domain, SHA-256 pinned and in `ref.geo_reference_sets`, migration 0007). Regions become standard groups (`NSW` and `New South Wales` are one) and add place findings. Not in v1: suburbs/LGAs, US ZIP codes, rates per resident, spatial clustering (move to Phase 6 maps). |
| features: candidates + signal gate (bias-corrected MI beats 19 shuffles), redundancy removal | ✅ | `engine/analysis/signal.py`: Miller-Madow MI against 19 shuffles, redundancy at rank correlation 0.95 against own columns and kept features; no outcome, no features. Kept features are driver candidates. |
| mine: nine miners in a process pool, `test_log`, Benjamini-Hochberg at 5% per run, driver skill check, leakage flag, small-group suppression (n < 10) | ✅ ↔ | `engine/analysis/miners.py` + `stats.py`: one primary test per hypothesis (keyed by the data it reads, so a segment and a benchmark of the same columns are one test), BH across the run, tiers strong/supported/inconclusive/descriptive, wording chosen after BH. Drivers only after a gradient-boosting model shows held-out skill; leakage at 0.95. Groups under 10 rows pooled. Sequential, not a process pool: measured slower with threads (ADR-032). Planted-effect evaluation passes. |
| facts table: immutable (trigger), provenance, content hash, full-text index | ✅ | `run_facts` (append-only, RLS) gains tier, test columns, provenance and a generated `tsvector` with a GIN index (migration 0007). Retries store nothing new; different facts are refused (`FactsChanged`). `test_plans_api.py::TestFactStore`. |
| sensitivity: top ten findings re-tested on minimally cleaned data | ✅ | `engine/analysis/sensitivity.py`: holds / weakens / flips (sign, or which group is highest) / not re-tested; recorded in `test_log` (purpose `sensitivity`), shown on each fact. |
| rank: significance, effect size, relevance, novelty, de-duplication | ✅ | `engine/analysis/rank.py`: evidence, size, plan relevance, novelty, sensitivity; duplicates marked; highlights (at most two per kind) lead the summary. |
| test_log: every test with effect size, interval, p and q | ✅ | `app.test_log`, monthly partitions (`ensure_test_log_partitions`, run by upkeep), append-only, forced RLS. |
| Report shows the evidence | ✅ | API `GET /v1/runs/{id}/report`: each fact's tier, test, check and content hash; evidence and feature summaries. UI: evidence badge (icon and words), test line, check line, "Evidence" block. |

## Phase 5 · Narration and verification · done

Decision record: ADR-034. Verified in iteration 10 (see "Last check run").

| Task | Status | Evidence / notes |
|---|---|---|
| Section-scoped narration with numeric slots | ✅ ↔ | `engine/analysis/narrate.py` + `prompt.py` (`narrate-v3`): one call per question, concurrently (`LLM_NARRATION_CONCURRENCY`, default 4), with only that question's facts; numbers as positional slots `{F3.1}` filled from the facts. The baseline names Sonnet; the strong tier of the configured provider is used (ADR-031). |
| Verifier: citations, stray digits, direction, wording strength, causal language, conditions | ✅ | `engine/analysis/verifier.py`: also form (length, braces, links, emails) and numbers in words; wording lexicon tied to effect-size bands; conditions from the labels in each slot's clause and "within". |
| Repair once, then template; 30% section fallback, prompt flagged; "narration simplified" | ✅ | One repair call with each failure; a sentence failing again becomes its fact's statement; over 30%: the part from templates, flagged in the log and the report. An outage (or a repair lost to one) marks the report "Narration simplified" with a "Write the answers again" button. |
| Judge (can only reject), off until its spike passes | ✅ | Purpose `judge` (fast tier, `judge-v1`), one sentence and its facts; skipped when unavailable; `LLM_JUDGE=false` by default. The spike itself is not run. |
| Summary last, from verified body facts, verified again | ✅ | Built from the ranked highlights that the verified answers cite, with the same checks and repair. |
| Template narrator as a complete fallback | ✅ | `engine_narration`, and per sentence and per part. |
| `verification_results` table; rejection rate measured | ✅ | Migration 0008: every verdict per sentence and attempt, with report revision and prompt version; append-only, monthly partitions, forced RLS. Rejections per check also in the report document and the stage output. A metrics dashboard is Phase 8. |
| Re-narrate after an outage | ✅ ↔ | `POST /v1/runs/{id}/report:renarrate` (202): only when the latest report is simplified for a reason that can pass; new edge succeeded → queued; only `report` re-runs, every other stage reuses its checkpoint; a new report revision. `test_plans_api.py::TestRenarrate`. |
| Corrupted-narrator evaluation in CI | ✅ | `test_narration.py`. |

## Phase 6 · Charts, dashboard, report · done

Decision record: ADR-035. Verified in iteration 11 (see "Last check run"). Phase B gains `visualize` between `rank` and `report`.

| Task | Status | Evidence / notes |
|---|---|---|
| Catalogue chart builders → spec + checkable claims; every claimed value recomputed from the cleaned data, a mismatch rejects the chart | ✅ ↔ | Every chart point carries a claim (`engine/analysis/claims.py`, ten kinds: group mean or gap with the miners' pooling, eta, rank correlation, share, histogram bin, period, scatter point, outliers, missing-value average, small-region rows). `visualize` (`engine/analysis/charts.py`) recomputes each with its own query; counts exactly, others within 1e-7; a failure withholds the chart (logged by claim type only). `test_charts.py`: every miner's chart verifies; five kinds of tampering are caught. Charts are drawn by our components; Vega-Lite is a per-chart download built when read (ADR-035). |
| Report and dashboard documents (versioned JSON), suggested questions pre-answered and verified | ✅ ↔ | Report v4 and dashboard v1 in one append-only `run_reports` row (migration 0009 `dashboard`). Dashboard: headline tiles (one of a highlighted fact's own numbers), top findings (reliable tests only), maps, gallery, up to six suggested questions answered with the report's verified sentences. `GET /v1/runs/{id}/dashboard`. |
| Web rendering: citation chips → fact inspector | ✅ | Chips are buttons opening one native dialog per page (statement, tier, columns, rows, method, test, check, hash; focus returns). Dashboard page and Dashboard/Report tabs; run page links to the dashboard. Streaming/`vega-embed` not used (ADR-035). |
| HTML export (Jinja) and PDF | ✅ ↔ | `GET /v1/runs/{id}/report.html`: autoescaped Jinja, inline SVG charts and maps, every fact with its hash, no script or external resource, `Content-Security-Policy: default-src 'none'`, attachment. PDF = print (stylesheets for the export and the pages; collapsed tables open before printing); no headless Chromium in the worker (ADR-035). |
| Maps: regions by parent, small areas hatched | ✅ ↔ | `engine/geo/maps.py` projects and simplifies the packaged Natural Earth outlines into SVG paths (Equal Earth cropped to the data; states with US insets and Russia on one side of the antimeridian). Share of rows per region, and the outcome's average when numeric; under ten rows hatched with no number; checked like charts. No rates per resident, smoothing or clustering test (no population or neighbour data; ADR-035). |
| Drill-downs: `POST /v1/runs/{id}/drilldowns`, exploratory facts | ✅ | A sub-run (`runs.parent_run_id`) of the same version starting at Phase B with the parent's Phase A checkpoints, plan and approved cleaning (no review, no planning call); subset kept in `clean_apply` (place columns by standard region name), keys every later stage and its own silver key; facts' provenance `exploratory`; report scope and method note; ≤ 20 groups, ≥ 30 rows, ≤ 25 per run, not nested; analysts and above; `Idempotency-Key`. UI: Explore from table rows and map regions with a confirmation, explorations listed on the parent. `test_phase6_api.py`, e2e "explore one group". |

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
| Metrics: endpoint RED, queue depth/age, stage durations, model cost, verifier rejection rate | ⬜ (rejections are stored per sentence in `verification_results`; no metrics pipeline yet) |
| Retention: bronze 90 days, lifecycle rules, partition dropping | 🟡 deleting a run or dataset purges its files after `STORAGE_PURGE_AFTER_DAYS` (ADR-037); no age-based retention yet |
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
| Phase B: eleven stages | Seven stages; narrate, verify, visualize and assemble are inside `report` | ADR-032; narration and verification are their own modules (ADR-034) but one stage, so a re-narration reruns one checkpoint |
| Named numeric slots (`{f_0107.diff}`) | Positional slots (`{F107.2}`, the fact's n-th number) | ADR-034: works for every miner without naming each number |
| A succeeded run is final | succeeded → queued only for `report:renarrate` | ADR-034: re-narration reuses the worker and every other checkpoint |
| Miners in a process pool | Sequential | ADR-032: threads measured slower; a pool copies the table per process |
| Charts rendered with Vega-Lite (`vega-embed`) | Our own accessible chart components; Vega-Lite spec offered as a download | ADR-035: `vega-embed` needs `unsafe-eval` under our CSP |
| PDF by headless Chromium in the worker | The self-contained HTML export (and the pages) print to PDF | ADR-035: no browser in the worker image; revisit for unattended PDFs |
| Map rates per resident, smoothing, spatial clustering | Shares of rows and outcome averages per region | ADR-035: no population or neighbour data in the reference set |
| `charts` / `chart_facts` tables; `reports` with two document kinds | Charts live in the report document and cite their answer's facts; report and dashboard are one row | ADR-035 |
| Reference data under `reference/geo/` in object storage, ABS ASGS + US Census | Natural Earth countries and states packaged with the code (0.55 MB), digest pinned in `ref.geo_reference_sets` | ADR-032: no setup step for 0.55 MB; small areas arrive with maps |
| Facts saved after mining, then sensitivity flags them | Facts are immutable, so sensitivity outcomes are stored in `test_log` beside them | ADR-032 |
| Sign-in links offered | Off by default (`EMAIL_LINK_SIGN_IN`) | ADR-033: no verified mail domain yet |
| The run pauses only if the plan would lose data | Every plan pauses for review under `review_lossy` and `always_review`; `auto_safe` never pauses | Found in the uncommitted Phase 3 extras (iteration 9 audit) and kept: the review is where a person adds or removes questions. **To confirm** (no ADR yet). |
| Claude for planning and narration | One gateway, four providers (Gemini default); rules when no key | ADR-031: development runs with whichever key is at hand; runs never depend on a model |
| Sign-in by link or Google | Also email + password | ADR-028: lets people in without a mail provider |

## Open questions

- Should `INITIAL_DOCS/` (the baseline) be committed so collaborators and CI see it? Today it is git-ignored.
- Where do the ADRs 001–024 referenced by the baseline live? Only 025–033 are in `docs/adr/`.
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

### Iteration 9 · 2026-10-10 · Phase 4: Analysis Phase B; audit of uncommitted work; sign-in page

**Why:** compare what is built with this file up to Phase 3, build Phase 4 to the baseline, check everything for a tagged release, and change the sign-in page (links disabled but explained, marketing copy).

**Audit (uncommitted work found at the start, checked against this file).** It held Phase 3 extras not recorded here (plan revision from the person's own requests, `POST …/plan:revise`, a recipe page, every plan reviewed) and a first, descriptive Phase B (`clean_apply` → `analyze` → `report`: miners without tests, facts with a content hash, a narrated report page). Its 353 unit tests passed. Recorded above; Phase 5 and 6 rows marked partial where that work reaches them.

**Built (ADR-032):** `geo`, `features`, `mine` (replacing `analyze`), `sensitivity`, `rank` stages; `engine/analysis/stats.py` (tests, intervals, Benjamini-Hochberg, Miller-Madow MI), miners rewritten to return tests and word facts after BH, data-keyed hypotheses, driver skill check, signal gate, places (`engine/geo/`, packaged Natural Earth set, `scripts/build_geo_reference.py`), sensitivity, ranking with diverse highlights; migration 0007 (`test_log`, fact evidence columns and full-text search, `ref.geo_reference_sets`); repository `save_findings` / `save_rechecks`; report API and page show each fact's evidence; stage summaries for Phase B; narration prompt v2 (tiers and checks), verifier checks wording strength and rejects links; upkeep creates `test_log` partitions. Planted-effect evaluation added. Version 0.4.0.

**Sign-in page (ADR-033):** "Email me a sign-in link" is inert (`aria-disabled`, focusable) with its hover/focus/info note ("Not available yet"), after "or", below "Continue with Google"; the server offers no link endpoint while `EMAIL_LINK_SIGN_IN=off` (default). Panel copy: brand, "Your one stop solution for data analysis.", one supporting line, and a one-line footer "We respect your data privacy. Raw rows never reach an AI model." (checked one line at 1024, 1280 and 1440 px; the panel is hidden on phones, where the tagline sits under the brand). e2e journeys now sign in with passwords.

| Found while testing | Fix |
|---|---|
| **The API image could not import the app:** `uv sync` installed the project editable and the runtime stage copies only `.venv` (pre-existing since Phase 1; CI builds the image but never ran it) | `--no-editable` in `backend/Dockerfile`; CI now imports the engine and the reference data inside the built image. Verified by reproducing the build steps (no buildx on this machine). |
| A segment and a benchmark of the same columns ran the same test twice in the false-discovery family | Hypotheses keyed by the data they read; one test per hypothesis; bootstrap seeded by hypothesis |
| "Average X ranges from 1,460 for High Street to 1,460 for High Street" when only one group has 10 rows | Compare that group with the pooled other groups |
| Summary of a run with no reliable finding was four outlier counts | Highlights: reliable first, at most two per kind; outliers and missing shares weigh less |
| DuckDB `DESCRIBE` column is `column_type`; `Result.tuples()` deprecated in SQLAlchemy 2.1 (tests treat warnings as errors) | Fixed (both caught by the new tests) |
| e2e expected the old plan page (masked model input moved to the recipe page) and a run without a review step | Journeys updated |
| A label from the data could get a link into a narrated sentence | Verifier rejects sentences with a URL or email address |

**For your local setup:** pull, `make migrate` (0006 → 0007), restart `make api`, `make worker` and `make web`. Your running dev servers serve the new code but the database is still at 0006 until you migrate.

**Not changed (noted):** suburbs/LGAs, US ZIP codes, rates per resident and spatial clustering (Phase 6 maps); the narrator is one call with number checks, not slots (Phase 5).

### Iteration 10 · 2026-10-11 · Phase 5: Narration and verification

**Why:** build Phase 5 to the baseline for a stable release, and try the narrator on a real model (two sample runs with a free Gemini key, then the key is not used again).

**Built (ADR-034):** `engine/analysis/verifier.py` (positional numeric slots, six checks plus form, wording lexicon by effect-size band, conditions per slot); `narrate.py` rewritten (one call per question, concurrent; one repair with feedback; template sentence; 30% part fallback with a flagged prompt; judge, off by default; summary last from verified answers; every verdict recorded); prompts `narrate-v3` and `judge-v1`; report document v3; migration 0008 `verification_results`; `POST /v1/runs/{id}/report:renarrate` (succeeded → queued, only `report` re-runs); report page "Narration simplified" notice with "Write the answers again"; `LLM_NARRATION_CONCURRENCY`, `LLM_JUDGE`. Corrupted-narrator evaluation in CI. Version 0.5.0.

**Live runs (Gemini, `gemini-3.8-flash`, exactly two sample runs: `store_performance_2025.csv` and `field_service_jobs.csv`; the key was read from `backend/.env`, never printed, and not used again).** The key works: 15 calls answered. The free tier also returned timeouts, provider errors and rate limits; every one fell back as designed (plans by rules, parts from templates, report marked simplified), no run failed. Of 32 model sentences, 17 were accepted on first draft, with numbers filled from the facts ("Floor area m2 and Part time staff tend to rise together, showing a strong association."). The rejections found the bug below.

| Found while testing | Fix |
|---|---|
| **Live run:** 7 of 28 sentences were wrongly rejected: restating an inconclusive fact's exact values ("Average Floor area m2 went from 1,524 m² in Jan 2025 to 1,520 m² in Oct 2025") counted as stating an unreliable finding | The rule now rejects only a claimed difference, trend or link (comparison, direction, association or strength words) without "not reliable"; regression tests with the live statement |
| **Live run:** a repair call lost to a rate limit flagged the prompt as failing | A repair lost to an outage simplifies the part instead of flagging it |
| A trend whose ends fall while its Kendall trend rises would reject either direction word | Direction is not judged when the two disagree |
| e2e: axe measured colour contrast on review cards still fading in (the 11th card starts 440 ms late); reproducible | The e2e helper waits for running animations; the entrance stagger is capped at 8 items (320 ms) |

**For your local setup:** `make migrate` (0007 → 0008), restart `make api`, `make worker` and `make web`. On a provider's free tier set `LLM_NARRATION_CONCURRENCY=1` or `2` in `backend/.env`.

**Not changed (noted):** the judge's spike (it stays off); a metrics pipeline for the rejection rate (Phase 8; the data is in `verification_results`); in the second live run the planner's answer failed validation once (`invalid`, then rate-limited), so that run planned by rules. This was not investigated further because it would mean using the key again. Planning is Phase 3, and the rules fallback worked. `graphify update` was not run (`backend/.venv/bin/graphify` is missing).

### Iteration 11 · 2026-10-11 · Phase 6: checked charts and maps, dashboard, exports, drill-downs

**Why:** complete Phase 6 for a release (v0.6.0): the whole flow up to (not including) questions about the data.

**Built (ADR-035):** chart claims on every miner chart (`engine/analysis/claims.py`) and the independent check (`charts.py`); stage `visualize`; region maps (`engine/geo/maps.py` outlines, `engine/analysis/regions.py` values and claims); report document v4 (verified charts, maps, withheld flags, drill-down scope, visuals summary) and dashboard document v1 (`dashboard.py`), saved as one revision (migration 0009); `GET /v1/runs/{id}/dashboard`, `GET /v1/runs/{id}/report.html` (`exports/`: Jinja + static SVG, strict CSP, attachment), Vega-Lite spec per chart in the API; drill-downs (`engine/subset.py`, `services/drilldowns.py`, `POST /v1/runs/{id}/drilldowns`, `runs.parent_run_id`, `GET /v1/runs?parent_run_id=`); `report:renarrate` and drill-downs refuse runs of another pipeline version (`409 report-outdated`). UI: dashboard page, Dashboard/Report tabs, fact inspector dialog, region maps (keyboard read-out, hatched small areas, table), Explore confirmation from tables and maps, explorations list, exploration notices (run, report, dashboard, runs table), Download HTML and Print or save as PDF, print stylesheet, "Check the charts and maps" stage summary. Version 0.6.0.

| Found while building and testing | Fix |
|---|---|
| `stats.eta` computed the between-group sum of squares as "sum of squares minus n times the mean squared", which cancels for large values with little spread | Deviations from the grand mean (exact same value otherwise; all evidence tests unchanged) |
| A scatter or trend over a column holding `inf` could put a non-finite value in a chart | Scatter points and trend averages use finite values only |
| Live run: "Top findings" showed trend charts whose tests were not reliable | Top findings need a strong or supported test; others go to the gallery |
| Live run (phone, 390 px): bar value labels ran past the chart, so the page scrolled sideways by 31 to 48 px (also on the report page, before this phase) | Bars leave room for their longest label; checked at 390 px on both pages |
| An exploration's dashboard repeated the parent's title | It says what it explores |
| A finished run's dataset would have shown its exploration as its latest analysis | A dataset's latest analysis is a full run |
| The planner's answer was "invalid" twice in the live run (as once in Phase 5), but the reason is not logged (answers are never logged) | The gateway now logs why without content: truncated, empty, not JSON or schema mismatch, with token counts. The cause shows on the next live run |

**Live run (Gemini, `gemini-3.8-flash`, one run plus one exploration, in a throwaway stack: fresh database, ports 8200/3200, `LLM_NARRATION_CONCURRENCY=2`; your dev database was not touched).** The free tier answered 429 (rate limit) and 503, and the planner's two 200 answers failed validation: planning fell back to the rules, narration to templates, the report and dashboard were marked "Narration simplified" with "Write the answers again". No run failed. 9 model calls in all. The rest of the flow worked in a real browser: review, dashboard (light, dark, phone), report, fact inspector, HTML download, PDF (15 pages from the export, 18 from the report page), exploration of one state (44 rows) and its dashboard; no console or page errors. Model-written narration was **not** seen in this iteration.

**For your local setup:** `make migrate` (0008 → 0009), then restart `make api`, `make worker` and `make web`. Your two runs queued under 0.5.0 (field_service_jobs, customer_subscriptions) will fail with "pipeline-changed" when a worker picks them up: start new analyses of those datasets.

**Not changed (noted):** rates per resident, small-area smoothing and spatial clustering (no population data); unattended PDFs; the judge's spike; question data (Phase 7). `graphify update` not run (graphify is not installed).

### Iteration 12a · 2026-10-16 · Practical test, usage guide, security check (no code changed)

**Why:** check in a real browser that everything built through Phase 6 works on real files, document it with screenshots, and look for key or data leaks and vulnerabilities.

**Done:** isolated stack (fresh database `ic_live_test`, API :8200, production web build :3200, real worker, RustFS), driven by Playwright + Chromium (the Playwright MCP needs Google Chrome, not installed). One live Gemini run (`field_service_jobs`); everything else with `LLM_PROVIDER=none`. Test database and test objects deleted afterwards. Written (local, `docs/` is ignored): `docs/usage/USAGE.md` (guide, 38 screenshots in `docs/usage/screenshots/`) and `docs/usage/TEST_REPORT.md` (results and findings).

**Passed:** every upload format, dedup, empty and fake files, Latin-1 and semicolon CSVs, live progress, review gate, requests without a model, Phase B, chart checks, exploration, cancel, integrity mismatch on a tampered object, HTML export, recipe rerun, phone and dark layouts. First model-written report: 38 sentences accepted, 1 rightly rejected. Security: no keys in the bundle, git history or logs; no personal values in pages, API responses or exports; tenant isolation (all 404); forged tokens 401; CSRF 403; traversal 404; storage anonymous 403; XSS and recipe injection neutralised; sign-in rate limit.

| Found (not fixed, details in TEST_REPORT.md) | Severity |
|---|---|
| One malformed CSV row makes DuckDB read the whole file as one column, with no warning | High |
| Gemini planner answers always fail validation (`cause: schema`), so every run uses the rules planner, which picks no outcome and only the first measures | High |
| Browser can get the API JWT: `GET /api/auth/token` and the `set-auth-jwt` header (Better Auth `jwt()`); breaks "browser never receives the API token" | Medium |
| Signed-out session works for up to 5 minutes (`cookieCache`) | Low–Medium |
| React #418 hydration error on live run pages (`run-progress.tsx:423`, `Date.now()` in state) | Low |
| Events ticket minted for another workspace's run (API refuses the stream) | Low |

### Iteration 12b · 2026-10-16 · Fixes from the test, review modes, report exports (ADR-036)

**Why:** the person asked to fix findings 1–4 of the practical test (malformed CSV, the model must work when configured, hide the API token, end sessions on sign-out), to add automatic and manual review modes (automatic by default), "Apply all" / "Keep all as is" with undecided changes kept as is, and to download the report as Markdown, Word and PDF.

**Built (ADR-036):** structural CSV/TSV check in `ingestion/engine.py`; `review_mode` on `POST /v1/runs`, run inputs, `clean_plan` (auto applies pending changes, no pause), `RunOut`, report wording; `cleaning-plan:decide` treats missing decisions as "keep as is" (`DecisionsIncomplete` removed); exports `exports/document.py` (shared blocks), `markdown.py`, `word.py` (python-docx), `pdf.py` (fpdf2), `images.py` (resvg-py, bundled DejaVu Sans) and `GET /v1/runs/{id}/report.{md,docx,pdf}`; LLM: `retry_after`/`quota_exhausted`/`auth_failed` in the adapters, gateway waits and quota pause, `LLM_REQUESTS_PER_MINUTE`, `LLM_RATE_LIMIT_WAITS`, `LLM_MAX_RETRY_WAIT_SECONDS`, schema problems logged, lenient `ProposedPlan`, `LLM_PROVIDER` requires its key, worker start-up check (`check_model`); auth: `disabledPaths: ["/token"]`, `disableSettingJwtHeader`, cookie cache off. UI: Automatic / Manual on the start control and "Review" on the run page, "Apply all" / "Keep all as is" on the review, a Download menu (PDF, Word, Markdown, HTML), "Print". Docs: ADR-036, CHANGELOG, `.env.example`, `docs/usage/TEST_REPORT.md` status.

| Found while building | Fix |
|---|---|
| The Gemini key's free tier allows 20 requests a day for `gemini-3.8-flash`; it was used up, and other models answered 503 "high demand" | Quota exhaustion is now recognised and named; the lenient parser could not be confirmed on a live answer this iteration |
| Changing the mode after a failed start would have replayed the old Idempotency-Key with a different body | The key is reset when the mode changes (component test) |

**For your local setup:** `cd backend && uv sync` (three new packages), `make contract` is already applied; restart `make api`, `make worker` and `make web`. No migration. Your `backend/.env` sets `LLM_PROVIDER=gemini` with a key, so the worker now checks that key at start-up. For the free tier, consider `LLM_REQUESTS_PER_MINUTE=5` and `LLM_NARRATION_CONCURRENCY=1`.

### Iteration 13 · 2026-10-16 · Deleting runs and datasets; analyses stuck in "queued" (ADR-037)

**Why:** the person asked to delete runs and datasets (gone from the database at once, from storage after X days, never shown again), and why an analysis started from a dataset stayed "queued" with every step waiting.

**Diagnosis of "queued forever":** no worker was running. Only `make api` (uvicorn) and `make web` (next dev) were up; the last worker heartbeat in `procrastinate_workers` was 2026-10-10; the run's phase job sat in `procrastinate_jobs` as `todo`. The API only queues work; the worker runs it. The worker's start-up check (`check_model`) was run on its own against the configured Gemini key and passed, so a worker would start. Fix: the run page says so (`RunOut.waiting_for_worker`, no heartbeat for 60 s) and `make dev` starts all three.

**Built (ADR-037):** migration 0010 (`storage_purges`, `app.delete_run`, `app.delete_dataset`, `app.erase_runs`, `app.storage_purges_due`, append-only triggers allow deletes while `app.erasing` is on); `services/deletion.py`; `DELETE /v1/runs/{id}` and `DELETE /v1/datasets/{id}` (`200 DeletedOut`, `409 still-running`); `ObjectStore.delete_prefix`; queue port `discard_phase_jobs` and `workers_alive`; worker task `purge_deleted_files` (hourly, minute 29); `STORAGE_PURGE_AFTER_DAYS` (default 7); uploads cancel a pending purge of the same original file; actions `run:delete` / `dataset:delete` (analysts and above). UI: `DeleteAction` (in-place confirmation, focus on "Keep it") on the run page (not while running) and the dataset page; "Waiting for a worker" notice; danger button style; the BFF forwards `DELETE`. `scripts/dev.sh` / `make dev`. Docs: ADR-037, CHANGELOG, README, `.env.example`.

| Found while building | Fix |
|---|---|
| Procrastinate's delete trigger uses unqualified table names, so jobs cannot be deleted from a definer function with a pinned `search_path` | Waiting jobs are removed through Procrastinate's own API after the commit |
| `SELECT … FOR UPDATE` on `storage_purges` needs `UPDATE`, which the app role does not have | The sweep deletes the row with `RETURNING` and removes the files in the same transaction (rolled back if storage fails) |
| The browser test's delete showed "Something went wrong" | The BFF route exported only GET and POST; it now exports DELETE |

**For your local setup:** migration 0010 was applied to your dev database (`make migrate`). Start the worker (`make worker`, or stop the others and use `make dev`): your queued run `01a14463…` then starts on its own. The three older queued jobs belong to cancelled runs and finish as `skipped`.

### Iteration 14 · 2026-10-16 · Test-suite datasets (no app code changed)

**Why:** the person asked for many tabular files of different shapes to test every feature and judge result quality.

**Built:** `sample_data/generate_test_suite.py` (seeded) writes 33 files to `sample_data/4_analysis` (13 files with planted, documented patterns, among them pure noise, Simpson's paradox, leakage, a rare target, nested JSON, long format and 150k rows), `5_shapes` (3×2 up to 60×500, plus 501 columns) and `6_ingestion_edge_cases` (cp1252 with `;`, pipe, UTF-8 BOM, UTF-16, duplicate headers, ragged rows, tricky values, xlsx, empty files). `sample_data/TEST_SUITE.md` is the answer key. Every file was run through `ingest_file`.

| Found while building | Status |
|---|---|
| An xlsx with title rows above the header ingests as 9 × 1: the title becomes the only column | Open: no header-row detection for Excel |
| A 0-byte file says "The file has a header but no data rows." | Open: misleading message |

### Iteration 15 · 2026-10-17 · Gemini errors explained, cheaper default models, documentation

**Why:** the person saw 404, 429, 503 and 504 errors in their Gemini usage and asked why, which model is used (to be switched to a good, very cheap one), how prompts are built and answers parsed, a code trace file, and `system_flow.md` / `explained_system_flow.md` updated without leaving out any feature.

**Diagnosis (from `app.llm_calls` in the dev database and the code; no model call made):** 31 attempts on 16 Oct, all `gemini-3.8-flash`: 7 ok, 14 `provider_error` (fast 503 "overloaded" answers, 1–7 s), 10 `rate_limited` (13 calls in one minute: narration runs 4 questions at once and `LLM_REQUESTS_PER_MINUTE` is off; this key's free tier also allows 20 requests a day for that model). 504 is Google's `DEADLINE_EXCEEDED`: `google-genai` sends the 90 s client timeout as `X-Server-Timeout`, and a plan answer took 44 s with about 7,400 output tokens. No 404 was recorded by the app's runs; they come from requests outside the ledger (start-up model lookups in earlier sessions, model-id probing or other tools on the same key) for an id the key cannot use.

**Changed:** Gemini defaults `gemini-3.5-flash-lite` (strong) and `gemini-3.1-flash-lite` (fast), checked against Google's models and pricing pages today; Gemini prices in `llm/pricing.py`; `.env.example` note; pricing test updated. Docs: `system_flow.md`, `explained_system_flow.md` (rewritten in their structure: prompts verbatim, schemas, parsing, error table, Phase B in full), new `code_trace.md`, CHANGELOG.

| Found while checking | Status |
|---|---|
| Changing `LLM_PROVIDER` or a model id changes the `understand` (and `report`) checkpoint key, so a run planned before the change fails `pipeline-changed` when it continues to Phase B (pending manual review, `report:renarrate`, drill-down of an older run). `409 report-outdated` checks the code version only. | Open: documented; start new analyses after a model change. Your dev database has no run awaiting review (5 succeeded, 3 cancelled). |

**For your local setup:** restart `make worker` (or `make dev`) to pick up the new defaults; its start-up check looks the two models up (free). On the free tier, consider `LLM_REQUESTS_PER_MINUTE=5`, `LLM_NARRATION_CONCURRENCY=1` and `LLM_TIMEOUT_SECONDS=180` in `backend/.env`.

### Iteration 16 · 2026-10-18 · Deep system test and two reports (no app code changed)

**Why:** the person asked for a deep test of the whole system (functional, security, privacy, UI, reliability) with the sample data, judged from user, stakeholder, developer, tester and attacker views, and two Word reports: a test report with screenshots and a verdict, and an industry-style project report with diagrams.

**Delivered:** `reports/InsightfulCell_System_Test_Report.docx` (47 pages) and `reports/InsightfulCell_Project_Report.docx` (34 pages). Test drivers, logs and screenshots stay in the session scratchpad (isolated stack: database `ic_qa`, API 8200, web 3200, RustFS shared).

**Verdict:** not ready for real users' sensitive data in production; closed pilot after fixes. Security core held (0 cross-tenant leaks in 22 attacks). Open findings (IDs from the test report):

| ID | Severity | Finding |
|---|---|---|
| F-01 | Critical | With a model configured, `understand` fails every new table: the model plan is saved with `input_card=None` → JSON `null` violates `analysis_plans_input_card_check` (3 paid calls, then internal-error) |
| F-02 | High | `*_pct` columns on a 0–100 scale emptied by `set_invalid_to_null [0,1]`; automatic mode applies it |
| F-03 | High | Decimal commas misparsed (`122,97` → 12297, `1.000,81` → 1.00081), classed lossless |
| F-04 | High | Nested JSON (STRUCT/LIST) crashes `semantics._meaning` (KeyError `avg_length`) |
| F-05 | High | Category merge Austria → Australia |
| F-06 | Medium | Better Auth invitation endpoints live; acceptance needs no verified email (attacker joined an org in the test) |
| F-07…F-22 | Medium–Info | XFF throttle bypass, no rate limits/BFF body cap, leading-zero ids parsed, rules-planner roles, no outcome chosen, auto mode applies lossy changes, duplicate removal on 1-column data, Excel title rows, multi-line headers, retry policy, Explore below 30 rows, set-active side effect, sign-up enumeration, Unicode names, mixed-config runs, export CSP |

### Iteration 17 · 2026-10-19 · Sign-up through ngrok; deployment address checks (ADR-038)

**Why:** `git push` ran the whole check suite (the pre-push hook) and the person wants push to only push; they asked to see the users in the local database; and an ngrok link could not create a user while `localhost:3000` could. They asked to fix it so deployment has no such issues.

**Diagnosis:** reproduced on the dev server: `POST /api/auth/sign-up/email` with `Origin: https://….ngrok-free.app` and browser fetch headers answers `403 INVALID_ORIGIN` (Better Auth trusts only `BETTER_AUTH_URL`). The backend proxy and the ticket route had the same single-origin check, `next dev` blocks unknown hosts, and `make up` reset bucket CORS to `localhost:3000`.

**Changed:** `AUTH_TRUSTED_ORIGINS` + `appOrigins()` (web), used by Better Auth, the proxy, the ticket route and `allowedDevOrigins`; `make allow-origin` (`scripts/allow-origin.sh`); `s3_setup.py --web-env` used by `make up`; production checks for localhost/http browser-facing addresses (web) and an http `AUTH_ISSUER` (API); pre-push hook deleted; `scripts/db-users.sh`. Docs: ADR-038, SETUP_MANUAL 1.1, 6.4 and troubleshooting, both `.env.example`, CHANGELOG.

| Note | Status |
|---|---|
| On another device, uploads go to `S3_PUBLIC_ORIGIN` and live progress to `API_PUBLIC_URL`, both `localhost` locally | By design: tunnel ports 9000 and 8000 as well (SETUP_MANUAL 1.1); live progress falls back to polling |
| Free ngrok URLs change on restart; each new one needs `make allow-origin` (old entries stay) | Use ngrok's free static domain |

### Iteration 18 · 2026-10-19 · Friends sign in, upload and watch runs through one ngrok tunnel (ADR-039)

**Why:** through `https://…ngrok-free.dev` sign-up created no user and sign-in did nothing, with no error; the email and password appeared in the address bar. The person asked for it fixed at once, plus anything else that would break the shared or deployed system, and for better error handling.

**Diagnosis:** `AUTH_TRUSTED_ORIGINS` was never set for the tunnel (`make allow-origin` not run), so `next dev` refused the host (`/_next/hmr` 503) and React never hydrated; the form then submitted natively as `GET /sign-in?email=…&password=…`. Beyond sign-in: on a friend's device uploads (`localhost:9000`) and live progress (`localhost:8000`) could not work, and with Gemini live every new analysis failed at `understand` (F-01: JSON `null` input card vs `analysis_plans_input_card_check`).

**Changed:** ran `make allow-origin` for the tunnel and restarted the web app. Sign-in form: disabled submit until hydrated, `method="post"`, CSS-delayed "still loading" notice, messages for unreachable server / refused address / server error (`transportError`). New `lib/relay.ts`, `PUT /api/storage/[...path]`, `GET /api/runs/[runId]/events`; `putPart` and the event-ticket route use them only for a loopback target from a non-loopback page; proxy matcher skips `/api/storage/`. Backend: `NULLABLE_JSONB` (`none_as_null=True`) on the four nullable JSONB columns, with a test. `agentRules: false`. Docs: ADR-039, SETUP_MANUAL 1.1, CHANGELOG, `allow-origin.sh` hint.

| Note | Status |
|---|---|
| Iteration 17's "tunnel ports 9000 and 8000 as well" | Superseded: one tunnel is enough |
| F-01 (test report) | Fixed |
| Friends use `next dev` through the tunnel (first visit to each page compiles, a few seconds) | Open: acceptable for testing; a production build is the faster option |

### Iteration 19 · 2026-10-19 · "See the data" side panel on the report and dashboard (ADR-040)

**Why:** the person asked for a "See the data" button on the report and dashboard that opens the original table in a side panel, with an arrow to open and close it and a ⋮⋮ grip to drag its width; then asked for 25 % table / 75 % report by default.

**Changed:** backend `services/data_rows.py` (`DataRowsService`, `LocalArtifactCache`), `GET /v1/runs/{id}/rows`, `Action.DATA_READ`, `DataNotAvailable`, schemas, container; integration test (409 before ingest, paging, limit, personal columns null and absent from the body, other organisation 404); role-matrix row. Frontend `components/report/data-sidebar.tsx` in `ReportActions`, push-layout rule in `globals.css`, `CaretLeft`/`CaretRight`/`DotsSixVertical` icons, `PageHeader` container query; component tests incl. axe. Contract regenerated. Docs: ADR-040, CHANGELOG.

| Note | Status |
|---|---|
| The table is the file as read (e.g. 25 rows) while the dashboard says rows analysed after cleaning (e.g. 23) | By design: the panel says "before cleaning" |
| No sorting, filtering or search in the panel; cleaned table not offered | Open (ADR-040, revisit) |
| A deleted run's cached Parquet stays on the API host up to an hour (never served) | Accepted (ADR-040) |

## Last check run

**Iteration 19 (2026-10-19), PostgreSQL 18 + RustFS running.** Frontend: 31 files / 391 tests (axe included), lint, typecheck, format check, `pnpm build` pass. Backend: 496 unit tests, ruff, ruff format, mypy, import contracts pass; integration: 179 passed (including the new rows test). Real browser on the local stack (1440×900 and 390×844): panel opens at 25 % (360 px), drags to 50 %, closes and reopens from the edge tab, page header stacks when narrowed. `make check` / `make e2e`: not run.


**Iteration 18 (2026-10-19), PostgreSQL 18 + RustFS running.** Frontend: typecheck, lint, format check, 30 files / 385 tests, `pnpm build` pass. Backend: 492 unit tests, 178 integration tests, ruff, mypy, import contracts pass. Through the real ngrok tunnel (headless Chromium): register, 10.6 MB two-part upload via the storage relay, analysis with live Gemini to "Your report is ready" with progress via the events relay (`text/event-stream`, `run.succeeded`); without JavaScript the form is POST, submit disabled, notice shown after 8 s. `make check` as a whole and `make e2e`: not run.


**Iteration 16 (2026-10-18), PostgreSQL 18 + RustFS running.** `scripts/check.sh` (full): pass, 668 backend tests (92.88% coverage), 369 frontend tests, build and audits clean. `scripts/e2e.sh` on 8100/3100: 5/5. Deep run of 52 sample files: 46 succeeded, 3 refused as designed, 1 crash (F-04), 2 uploads refused as designed. Security battery 92/99 (7 findings). axe on 15 pages: 0 violations. Live Gemini: one run (6 plan calls in total, isolated `ic_qa`), failed by F-01.

**Iteration 15 (2026-10-17):** backend unit tests 490/490 pass; ruff, ruff format and mypy clean on `llm/` and the changed test. Integration, frontend and e2e suites not re-run (only model defaults, prices and docs changed). Live Gemini: not run.

**Iteration 12b (2026-10-16), PostgreSQL 18 + RustFS running. Run on the final code.**

| Step | Result |
|---|---|
| `scripts/check.sh` backend: ruff, mypy strict (195 files), import-linter (6 contracts), pytest unit + integration, migrations up/down/up, OpenAPI contract, pip-audit (with fpdf2, python-docx, resvg-py) | pass: 652 tests, 92.92% coverage (minimum 85%) |
| `scripts/check.sh` frontend: prettier, eslint, tsc strict, vitest + axe, API types match the contract, `next build`, pnpm audit | pass: 365 tests |
| Secret scan | pass (built-in patterns) |
| `scripts/e2e.sh` on 8100/3100 (`LLM_PROVIDER=none`) | pass: 5/5 journeys, now also: automatic by default, manual with Keep all / Apply all, `/api/auth/token` 404 and no `set-auth-jwt`, cookies replayed after sign-out → 401, PDF/Word/Markdown/HTML downloads |
| Browser check on an isolated stack (no model) | new screens captured for `docs/usage/USAGE.md`; a real report exported as a 23-page PDF, a Word file (21 pictures, 22 tables) and Markdown; a malformed CSV refused with its line |
| Live Gemini | not run: today's free quota (20 requests) was used up; other models answered 503 |
| GitHub CI | not run: nothing pushed |

**Iteration 12a (2026-10-16):** practical browser test only; no code changed, so the unit, integration and e2e suites were not re-run. Built-in secret scan: pass.

**Iteration 11 (2026-10-11), PostgreSQL 18 + RustFS running. Release candidate v0.6.0. Run on the final code.**

| Step | Result |
|---|---|
| `scripts/check.sh` backend: ruff, mypy strict (190 files), import-linter (6 contracts), pytest unit + integration, migrations up/down/up (0009), OpenAPI contract, pip-audit | pass: 619 tests, 93.21% coverage (minimum 85%) |
| `scripts/check.sh` frontend: prettier, eslint (jsx-a11y), tsc strict, vitest + axe, API types match the contract, `next build`, pnpm audit | pass: 358 tests |
| Secret scan | pass (built-in patterns; gitleaks not installed); `backend/.env` is git-ignored |
| `scripts/e2e.sh` on 8100/3100 (`LLM_PROVIDER=none`) | pass: 5/5 journeys, including the new "explore one group" and the inspector, dashboard map and HTML download checks in the review journey |
| Live Gemini check (isolated stack, see iteration 11) | flow complete; the model was rate-limited, so narration fell back to templates as designed |
| Phone layout at 390 px (dashboard and report) | no sideways scroll after the fix |
| GitHub CI | not run: nothing pushed |

**Iteration 10 (2026-10-11), PostgreSQL 18 + RustFS running. Release candidate v0.5.0.**

| Step | Result |
|---|---|
| `scripts/check.sh --backend` (final backend code): ruff, mypy strict (174 files), import-linter (6 contracts), pytest unit + integration, migrations up/down/up (0008), OpenAPI contract, pip-audit | pass: 582 tests, 91.98% coverage (minimum 85%) |
| `scripts/check.sh --frontend`: prettier, eslint, tsc strict, vitest + axe, API types match the contract, `next build`, pnpm audit | pass: 344 tests. After it, only `globals.css` (stagger cap) and the e2e helper changed; prettier, eslint and tsc re-run: pass |
| `scripts/e2e.sh` on 8100/3100 (`LLM_PROVIDER=none`) | pass: 4/4 journeys, twice, after the fix above |
| Echo check: every engine statement of the three sample datasets sent back as a model sentence | 99 sentences, 0 rejected |
| Live Gemini sample runs | 2 runs (see iteration 10); the key is not used again |
| Secret scan | pass (built-in patterns); `backend/.env` is git-ignored |
| GitHub CI | not run: nothing pushed |

**Iteration 9 (2026-10-10), PostgreSQL 18 + RustFS running. Release candidate v0.4.0.**

| Step | Result |
|---|---|
| Backend ruff, mypy strict (171 files), import-linter (6 contracts) | pass |
| Backend pytest unit + integration (final code) | pass: 545 tests, 91.81% coverage (minimum 85%) |
| Migrations up / down / up (0007); OpenAPI contract; pip-audit | pass (previous full gate, before the last wording-only changes) |
| Frontend prettier, eslint, tsc strict, vitest + axe (342 tests), `next build`, pnpm audit | pass in the previous full gate; after the final wording changes, prettier, eslint, tsc and the report tests were re-run and pass. Full vitest and `next build` were not re-run after that |
| `scripts/e2e.sh` on 8100/3100 | pass: 4/4 journeys (three runs); not re-run after the final wording change to the report's Evidence text |
| Secret scan | pass (built-in patterns) |
| Docker image | not built locally (no buildx); build steps reproduced with `uv sync --no-editable`: the engine and reference data import without `src/` |
| GitHub CI | not run: nothing pushed |

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
