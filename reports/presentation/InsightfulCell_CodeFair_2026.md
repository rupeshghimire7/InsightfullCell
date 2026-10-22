# InsightfulCell: every number shows its work

**CDU IT Code Fair 2026 · presentation contents, demo script and feature tour**

Rupesh Ghimire (project owner and lead developer) · Sandesh Prasad Paudel (deployment)

Live at **<https://app.ic.rgproject.me>** (AWS) · Slides: [`InsightfulCell_CodeFair_2026.pptx`](InsightfulCell_CodeFair_2026.pptx) (25 slides, speaker notes on every slide)

> Every screenshot below was captured with Playwright on the **live AWS deployment on 22 October 2026**, using a fresh account and two files from `sample_data/`: `4_analysis/01_ecommerce_orders.csv` (5,000 rows × 19 columns) and `3_complex/field_service_jobs.csv` (25 rows × 50 deliberately messy columns, with personal data). On that date the live site had **no language model configured**, so plans come from the rules planner and sentences from templates. Everything shown works without AI.

---

## Contents

1. [How to run the session](#1-how-to-run-the-session)
2. [The pitch, slide by slide](#2-the-pitch-slide-by-slide)
3. [Feature tour with screenshots](#3-feature-tour-with-screenshots)
4. [What makes it different (USPs)](#4-what-makes-it-different-usps)
5. [Questions to prepare for](#5-questions-to-prepare-for)
6. [Sources for every claim](#6-sources-for-every-claim)

---

## 1. How to run the session

| Part | Slides | Time | Who |
|---|---|---|---|
| Opening: problem, research question, approach | 1–3 | 3 min | Rupesh |
| **Live demo** (share screen to app.ic.rgproject.me) | 4, then back to 5–11 if the network fails | 5 min | Rupesh |
| How it works: architecture, pipeline, statistics, security | 12–17 | 4 min | Rupesh |
| Deployment on AWS | 18 | 1 min | Sandesh |
| Quality, business prospect, limitations, roadmap | 19–24 | 4 min | Both |
| Close and questions | 25 | 3+ min | Both |

**Before you start:** sign in to the live site in a browser tab, keep `sample_data/4_analysis/01_ecommerce_orders.csv` and `sample_data/3_complex/field_service_jobs.csv` at hand, and have the deck open in presenter view (speaker notes). Slides 5–11 repeat the demo with screenshots, so the talk still works offline.

**Demo script (slide 4):**

1. Upload `01_ecommerce_orders.csv`. Keep **Automatic** and click **Analyse newest version**. The steps stream live; the report is ready in about 15 seconds.
2. Upload `field_service_jobs.csv`, choose **Manual**, analyse. Show the 5 masked personal-data columns, then **Review changes**: rows affected, before and after, **Apply all**, **Approve and continue**.
3. Open the **Report** of the orders file. Click the **F13** chip: test, interval, p and q values, rows, content hash.
4. Open the **Dashboard**: tiles with evidence badges, checked charts, the Australia map. Click **Show the numbers and explore a region**, then **Explore** Queensland.
5. Open **See the data** and the **Download** menu (PDF, Word, Markdown, HTML).
6. Optional: **Show recipe** → *What the planning model was shown*.

---

## 2. The pitch, slide by slide

### Slide 1 · Every number shows its work

A verified, privacy-first platform that turns a spreadsheet into tested findings, checked charts and maps, and a plain-language report. Every figure links to a stored, unchangeable fact you can inspect. Live on AWS today.

### Slide 2 · Spreadsheet owners face a trust gap

| | General AI assistants | BI and statistics tools |
|---|---|---|
| Strength | Fluent, fast, no expertise needed | Accurate and reproducible |
| Weakness | Numbers can be invented with no way to check; cleaning is invisible; raw rows go to a third-party model | Need an analyst to prepare the data and to know which questions to ask; slow to set up |

**Research question:** *Can analysing your own data be as easy as asking an assistant, and as trustworthy as the work of a careful analyst?*

### Slide 3 · Our answer: a strict division of labour

- **Code computes.** Every statistic, test, chart value and map value comes from deterministic code (DuckDB, NumPy, SciPy).
- **The model only plans and writes.** A language model may suggest questions and phrase sentences. It never writes a number: it writes slots that the engine fills.
- **Everything is checked.** Plans are validated, charts recomputed, and every sentence verified against its facts.

Tenets in priority order (the higher wins on conflict): **1 Correctness over fluency › 2 Traceability › 3 Privacy: raw rows never reach a model › 4 Simplicity of operation.**

### Slide 4 · Live demo

Switch to the live site and follow the demo script above.

### Slides 5–11 · The product (demo backup)

Slide 12 is the "How it works" divider; slide 20 is the "Business prospect" divider.

See [section 3](#3-feature-tour-with-screenshots), which follows the same order with full screenshots.

### Slide 13 · Architecture: a modular monolith, three processes, two stores

```mermaid
flowchart LR
    B([Browser]) -- session cookie --> W["Web app<br/>Next.js · Better Auth · BFF"]
    W -- "15-min EdDSA token" --> A["API<br/>FastAPI"]
    B == "presigned PUT" ==> S3[("Amazon S3<br/>bronze · silver · gold")]
    A --- PG[("PostgreSQL 18<br/>data · queue · events · auth")]
    K["Worker<br/>same image · 12 stages"] --- PG
    K --- S3
    K -. "masked description only<br/>(optional)" .-> L[[Language model]]
```

- The API never sees file bytes or passwords; the browser never holds an API token.
- PostgreSQL is also the job queue and the event bus: one system to secure and back up.
- The layers are enforced by six `import-linter` contracts in CI, so the analysis engine stays free of web, database and model code.

### Slide 14 · The pipeline: understand, review, then prove

```mermaid
flowchart LR
    subgraph A["Phase A: understand and plan"]
        direction LR
        ingest --> profile --> understand --> clean_plan
    end
    gate{{"Review gate<br/>automatic: pass · manual: wait"}}
    subgraph Bp["Phase B: clean, test, explain"]
        direction LR
        clean_apply --> geo --> features --> mine --> sensitivity --> rank --> visualize --> report
    end
    A --> gate --> Bp
```

| Stage | What it guarantees |
|---|---|
| `understand` | A rules planner, or a model validated item by item, decides which questions to ask |
| `features` | A derived measure is kept only if it beats 19 random shuffles |
| `mine` | Nine miners test the questions; false discoveries controlled at 5% across the run |
| `sensitivity` | The top ten findings are re-tested on lossless-only data |
| `visualize`, `report` | Every chart value recomputed; every sentence narrated from facts and verified |

### Slide 15 · Numbers come from facts, never from the model

1. **Fact stored**: numbers, test, interval, p and q values, provenance, content hash. Append-only.
2. **Slots, not numbers**: the writer (model or template) writes `{F13.r}`, not `0.88`.
3. **Engine fills**: the number is copied from the fact.
4. **Verifier checks**: citations, stray numbers, direction, strength words against effect size, causal language, missing conditions. A failing sentence is rewritten once, then replaced by the fact's own statement.

> Model writes: *Unit price and Revenue rise together: the link is strong (rank correlation* `{F13.r}`*).*
> Reader sees: *Unit price and Revenue rise together: the link is strong (rank correlation **0.88**; 95% interval 0.87 to 0.89).* `F13`

Evaluation: the corrupted-narrator suite plants 31 errors; all 31 are rejected and 11 clean sentences kept.

### Slide 16 · Statistical rigour, by construction

| Number | Meaning |
|---|---|
| **5%** | False-discovery rate controlled across the whole run (Benjamini–Hochberg), not per test |
| **19** | Random shuffles a derived feature must beat before it is kept (signal gate) |
| **10** | Strongest findings re-tested on lossless-only data, so cleaning cannot create them |
| **9** | Miners (differences, relationships, trends, drivers, outliers…); every test is logged |

Findings are tiered honestly (Strong evidence, Supported, Not reliable, Descriptive), intervals accompany every estimate, and causal wording is rejected.

### Slide 17 · Security and privacy, enforced in the database

| Control | How |
|---|---|
| Tenant isolation | Forced row-level security on every tenant table; the organisation comes only from the verified token. In the 18 Oct system test, 0 of 22 cross-tenant attacks succeeded |
| Short-lived identity | 15-minute EdDSA tokens with a pinned algorithm; the browser never receives one |
| Personal data stays home | Detected at ingest, masked in the model card, blocked by the model gateway |
| Tamper evidence | Facts, tests, events and model calls are append-only (enforced by triggers); facts carry content hashes |
| Hostile files | Extension allow-list, magic bytes, size and shape limits, zip-bomb guard, parsing only in the worker |
| Hard spend cap | Each model call reserves its worst-case cost first; the total can never pass the cap |

### Slide 18 · Live in production on AWS

```mermaid
flowchart LR
    U([Browser]) -- HTTPS --> C[Caddy<br/>auto-HTTPS]
    subgraph EC2["EC2 t3.micro · Docker Compose"]
        C --> W[web] & A[api]
        K[worker]
        P[(postgres)]
        W & A & K --> P
    end
    U == presigned PUT ==> S3[(S3 uploads)]
    A & K -- instance role --> S3
    P -- nightly dump --> BK[(S3 backups)]
    GH[GitHub Actions → ghcr.io] -- deploy.sh pulls --> EC2
```

- One command to release: `./deploy.sh` pulls the images, migrates and waits for health; roll back by deploying an earlier commit's images.
- No storage keys anywhere: S3 access comes from the instance role.
- Cost: free-tier instance; about US$3.6 a month for the public IP address outside the free tier, plus cents for S3.
- The only code change needed for AWS: upload links signed for the bucket's regional S3 host.

### Slide 19 · Engineering quality you can measure

| Measure | Value |
|---|---|
| Backend tests (unit + integration on real PostgreSQL and S3) | **719** collected; 530 unit tests re-run on 22 Oct 2026 and pass |
| Backend line coverage | **93.02%** (full gate, 21 Oct 2026) |
| Frontend tests (each component with an axe accessibility check) | **422** (21 Oct 2026) |
| Architecture contracts (`import-linter`) | **6** |
| Other gates | ruff, prettier, eslint (jsx-a11y), mypy strict, TypeScript strict, migrations up/down/up, OpenAPI → TypeScript drift, Playwright journeys, pip-audit, pnpm audit, secret scan |

### Slide 21 · Positioned where trust matters more than flair

| | InsightfulCell | AI assistant | BI / statistics tool |
|---|---|---|---|
| Every number traceable to a test | ✓ | ✗ | Partly |
| Raw rows stay private | ✓ | ✗ | ✓ |
| No analyst needed | ✓ | ✓ | ✗ |
| False discoveries controlled | ✓ | ✗ | Manual |
| Reproducible recipe | ✓ | ✗ | Partly |
| Plain-language report | ✓ | ✓ | ✗ |

*Our qualitative assessment, not a measured benchmark.*

**Who it is for:** small and medium businesses without an analyst; NGOs and the public sector with sensitive records; students and researchers who need auditable statistics; consultants who need evidence for a client.

### Slide 22 · A proposed model: AI cost stays optional

| Tier | Price | What it adds |
|---|---|---|
| Community | Free | Rules planner and template narration; all checks, charts, maps and exports; no AI cost to serve |
| Pro | Per seat | Model-planned questions and narration; per-person hard spend cap; priority processing |
| Team / private | Per organisation | Workspaces and roles; self-hosted in the customer's cloud; audit log and SSO (roadmap) |

Unit economics: the baseline design targets about **US$0.26** of model cost per run; rules-only runs (the live site today) cost **$0** in model fees; one host runs the whole stack. *Proposed; pricing not yet validated.*

### Slide 23 · Honest limitations, and what we learned

| Limitation | Status |
|---|---|
| Automatic cleaning can still change data silently in a few cases (0–100 percentage columns, decimal commas, close-sounding names) | Open (system test findings F-02, F-03, F-05). Use **Manual** review for sensitive work |
| Asking questions in plain language | Phase 7, not built |
| Production is one small server | No staging, monitoring, alerts or point-in-time recovery; one analysis at a time |
| Accounts are basic | One workspace per person; email not verified; no invitations in the UI |

**What we learned:** trust is won or lost in data preparation, not in the statistics; a product that works without AI is cheaper, faster and easier to trust; rules enforced in the database and in CI beat conventions; testing on a real deployment found what local tests could not (the S3 regional host).

### Slide 24 · Roadmap

| When | Milestone |
|---|---|
| Done | Phases 0–6: sign-in, uploads, queue, pipeline, narration, report, dashboard, exports |
| 22 Oct 2026 | Live on AWS: one host, HTTPS, S3, nightly backups, CI-built images |
| Next | Phase 7: questions about the data, with closed and verified tools |
| Next | Phase 8: staging, monitoring, audit log, rate limits, retention |
| 11 Dec 2026 | Release 1 alpha (planned) |
| 12 Mar 2027 | Release 2 beta (planned) |
| 9 Apr 2027 | General availability (planned) |

### Slide 25 · Thank you. Questions?

*Code computes, the model only writes, and every number shows its work.* Try it at **app.ic.rgproject.me**.

---

## 3. Feature tour with screenshots

### 3.1 Sign in and register

The sign-in page carries the product's promise. Password registration and Google sign-in are supported; email sign-in links are built but switched off until a mail domain is verified, and the button says so.

![Sign-in page: "Your one stop solution for data analysis."](screenshots/01-sign-in.png)

| Sign-in link explained | Registration with confirm password |
|---|---|
| ![Disabled email-link button with its "Not available yet" note](screenshots/01b-sign-in-link-tooltip.png) | ![Create an account form](screenshots/02-register.png) |

### 3.2 Home: upload a file

Every person gets a workspace. CSV, TSV, Excel, Parquet and JSON up to 250 MB. The browser hashes the file (SHA-256 in a Web Worker) and uploads it straight to Amazon S3 in parallel parts.

![Empty home with the upload area](screenshots/03-home-empty.png)

| Saved, with the review mode chosen right away | The same bytes again: nothing new stored |
|---|---|
| ![Upload saved, Automatic or Manual](screenshots/04-upload-saved.png) | ![Duplicate detected: "You already uploaded this exact file"](screenshots/05-duplicate-detected.png) |

![Home with both datasets listed](screenshots/06-home-datasets.png)

### 3.3 Start an analysis: automatic or manual

**Automatic** (default) applies the suggested cleaning and goes straight to the report. **Manual** stops so you can check every change first.

| Automatic (default) | Manual |
|---|---|
| ![Dataset page with Automatic selected](screenshots/07-dataset-page.png) | ![Manual selected: "Stops so you can check…"](screenshots/20-manual-mode-select.png) |

### 3.4 Live progress, step by step

Twelve stages stream to the browser over Server-Sent Events, each with its own summary. A run can be cancelled, or retried from its last checkpoint.

![Run in progress: Running, Live](screenshots/09-live-progress.png)

The finished automatic run on the live site: **5,000 rows × 19 columns in 14 seconds**, 26 questions answered, 28 facts, 13 tests, 2 reliable findings, 24 charts and 2 maps with **1,444 values recomputed and none withheld**.

![Finished run with every step's summary](screenshots/10-run-complete.png)

Every column gets a meaning, not just a type:

![The 19 columns and what they mean](screenshots/11-columns-meaning.png)

### 3.5 Personal data is found first

On the messy field-service file, five columns (customer name, email, phone, licence number, street address) are detected as personal data and **masked** before anything else happens.

![Columns marked Masked · Identifier](screenshots/22-columns-personal-data.png)

### 3.6 The review gate (manual mode)

The manual run stops: *13 cleaning changes could remove or replace values. Nothing has been changed yet.*

![Your review is needed](screenshots/21-review-needed.png)

Each change shows the column, the rows it affects, how many values would become empty, and real before/after values. Decide one by one, or **Apply all** / **Keep all as is**. Undecided changes are kept as they are, and the uploaded file is never modified.

![Review changes to field_service_jobs](screenshots/25-review-decisions.png)

![Apply all: 13 of 13 to apply](screenshots/26-review-apply-all.png)

After approval, Phase B runs and the report is ready:

![Manual run complete](screenshots/27-manual-run-complete.png)

### 3.7 The plan and the recipe

The plan lists what the table is, the questions the analysis will answer, the cleaning, the features and the charts. Here it says plainly that the data rules planned it because no language model is configured.

| Plan (automatic run) | Plan (manual run, 40 questions, outcome *Complaint Raised*) |
|---|---|
| ![Plan for 01_ecommerce_orders](screenshots/18-plan.png) | ![Plan for field_service_jobs](screenshots/23-plan-manual.png) |

The **recipe** is a Python script that rebuilds the cleaned table and charts from the original file, generated from the stored plan. Nothing in it is written by a language model.

![Recipe for 01_ecommerce_orders](screenshots/19-recipe.png)

### 3.8 Exactly what a model would be shown

*A description of your table, never its rows.* Personal-data columns are reduced to their kind, numbers to rounded ranges without the lowest or highest value, and a category label appears only when several rows share it. The description is shown exactly as it would be sent (`"personal_data_masked": true`).

| The rules, in words | The description, exactly as sent |
|---|---|
| ![What the planning model was shown](screenshots/24-masked-description.png) | ![The masked JSON description](screenshots/24b-masked-description-json.png) |

### 3.9 The report: every sentence cites its facts

"In short" is written last, from verified findings only. Each answer has its sentences, sources and chart.

![Report on 01_ecommerce_orders](screenshots/12-report-top.png)

Click any source chip to open the **fact inspector**: the statement, an evidence badge, the columns and rows used, the method, the 95% interval, the p-value before and after false-discovery control, and the content hash.

![Fact F13 in the inspector](screenshots/13-fact-inspector.png)

The report of the messy file, with personal data never shown:

![Report on field_service_jobs](screenshots/28-report-manual.png)

### 3.10 The dashboard and maps

Headline tiles carry their fact chip and an evidence badge; below them, rows analysed, columns, reliable findings out of tests run, and charts checked.

![Dashboard: 01_ecommerce_orders](screenshots/16-dashboard-top.png)

Places are resolved offline to standard regions (here countries and Australian states, 100% matched). Every region's value is checked against the data.

![Share of rows by Country and by State](screenshots/17-dashboard-map.png)

![Dashboard: field_service_jobs](screenshots/29-dashboard-manual.png)

### 3.11 See the data

A resizable side panel shows the rows as read from the file, 100 at a time, next to the report or dashboard. Personal-data columns are never read.

![See the data panel beside the report](screenshots/14-see-the-data.png)

### 3.12 Drill down into one group

From a map or chart, **Show the numbers and explore a region**, then **Explore** one group. It runs as a new analysis of only those rows, reusing the approved plan and cleaning, and is marked *exploratory* because the group was chosen after seeing the results.

| The numbers behind the map | Confirm the exploration |
|---|---|
| ![Region table with Explore buttons](screenshots/30-explore-region-table.png) | ![Explore Queensland on its own?](screenshots/31-explore-confirm.png) |

| The sub-run (reused steps finish in seconds) | Its dashboard, labelled exploratory |
|---|---|
| ![Exploring a subset: State is Queensland](screenshots/32-exploration-run.png) | ![Dashboard: Exploring State: Queensland](screenshots/33-exploration-dashboard.png) |

### 3.13 Download and share

The report downloads as **PDF**, **Word**, **Markdown** or a self-contained **HTML** file that runs no scripts. All four were downloaded from the live site.

![Download menu](screenshots/15-download-menu.png)

### 3.14 Runs, deletion and housekeeping

| Every run, newest first | Deleting asks first and focuses "Keep it" |
|---|---|
| ![Runs list](screenshots/35-runs-list.png) | ![Delete confirmation](screenshots/34-delete-confirm.png) |

Deleting removes the rows from the database at once; the files leave storage after a grace period.

![Home after both analyses succeeded](screenshots/36-home-analysed.png)

### 3.15 Accessible, dark scheme and mobile

Keyboard access, visible focus, labels, 44 px touch targets, reduced motion and live regions; every component has an automated axe test.

| Dark scheme | Report in the dark scheme |
|---|---|
| ![Dark dashboard](screenshots/37-dark-dashboard.png) | ![Dark report](screenshots/37b-dark-report.png) |

| Phone: home | Phone: dashboard |
|---|---|
| ![Mobile home](screenshots/38b-mobile-home.png) | ![Mobile dashboard](screenshots/38-mobile-dashboard.png) |

---

## 4. What makes it different (USPs)

1. **Numbers that cannot be hallucinated.** The writer fills slots; the engine supplies every number from a stored fact; a verifier rejects anything that does not match.
2. **Every number shows its work.** One click from any figure to its test, interval, p and q values, rows, method and content hash. Facts are append-only and tamper-evident.
3. **Raw rows never reach an AI.** Personal data is found first, the model sees only a masked description, and a gateway blocks any payload that still contains personal data.
4. **Statistics done properly, automatically.** False discoveries controlled across the whole run, signal-gated features, sensitivity re-tests, honest evidence tiers, and no causal language.
5. **The person stays in control of the data.** Measured, reversible cleaning with a review gate and a reproducible Python recipe; the upload is never modified.
6. **AI is optional, so cost is optional.** The full product runs on deterministic code alone (as the live site does today); a model adds polish, under a hard spend cap.
7. **Secure by construction.** Tenant isolation in the database itself (forced row-level security), short-lived tokens that never reach the browser, hardened uploads.
8. **Cheap to run, simple to operate.** One codebase, PostgreSQL as database, queue and event bus, and a whole production stack on one free-tier AWS instance.

---

## 5. Questions to prepare for

**Is this just a ChatGPT wrapper?** No. The analysis is done by our own engine (DuckDB, SciPy, scikit-learn). The live site has no model configured at all and everything works. A model, when enabled, only proposes questions and phrases sentences, and it never sees rows.

**How do you know the findings are real?** Each question is one statistical test, logged; the false-discovery rate is controlled at 5% across the run; the strongest findings are re-tested without lossy cleaning; and each finding gets an honest evidence tier.

**What stops a wrong number reaching the report?** The writer cannot write numbers, only slots. The engine fills them from facts, and a verifier checks every sentence (31 of 31 planted errors rejected in our evaluation suite). Charts are recomputed point by point and withheld on any mismatch.

**What about personal data?** It is detected at ingest, masked in the model's description, never read by "See the data", and blocked at the model gateway. Tenants are isolated by row-level security in PostgreSQL.

**Can it scale?** The worker scales by instance size today (one analysis at a time on the free-tier host) and by adding workers on the PostgreSQL queue later. The baseline target is managed containers and a managed database (Phase 8).

**What are the weaknesses?** Automatic cleaning heuristics can still change data in a few cases (use Manual mode); natural-language Q&A is not built; production is one server without monitoring. We list these openly in the project and test reports.

**What does it cost?** The live stack runs on a free-tier EC2 instance plus about US$3.6 a month for its public IP and cents for S3. Model cost is zero in rules-only mode; the design target with a model is about US$0.26 per run.

---

## 6. Sources for every claim

| Claim | Source |
|---|---|
| Live journey, timings and counts (14 s, 1,444 values, 13 changes, 5 masked columns) | Playwright run on app.ic.rgproject.me, 22 Oct 2026; screenshots in `screenshots/` |
| No model configured on the live site | Run pages: "Planned by the data rules: no language model is configured" |
| Architecture, pipeline, verifier, statistics | [`../system_flow.md`](../system_flow.md), [`../explained_system_flow.md`](../explained_system_flow.md) |
| AWS deployment, cost | `deploy/aws/README.md`, `deploy/aws/compose.yml`, `.github/workflows/images.yml`; [`../system_flow.md` §2.1](../system_flow.md) |
| Test counts and coverage | Full gate 21 Oct 2026 (715 backend tests, 93.02%, 422 frontend); 22 Oct 2026: 719 collected, 530 unit tests pass |
| 0 of 22 cross-tenant attacks, open findings F-02 to F-22 | [`../InsightfulCell_System_Test_Report.docx`](../InsightfulCell_System_Test_Report.docx) |
| Roadmap dates, model-cost target | Baseline system map and PRD; [`../InsightfulCell_Project_Report.docx`](../InsightfulCell_Project_Report.docx) |
