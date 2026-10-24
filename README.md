# InsightfulCell

<!-- [![CI](https://github.com/rupeshghimire7/InsightfullCell/actions/workflows/ci.yml/badge.svg)](https://github.com/rupeshghimire7/InsightfullCell/actions/workflows/ci.yml) -->
<!-- [![Licence: proprietary](https://img.shields.io/badge/licence-proprietary-lightgrey)](LICENSE) -->

InsightfulCell is a one-stop solution for data analysis: upload a spreadsheet and get a cleaned
dataset, statistically tested findings, checked charts and maps, and a plain-language report where
every number traces back to its test.

**Live app:** <https://app.ic.rgproject.me> · **Demo video:** [YouTube link] ·
**Usage guide:** [docs/usage/USAGE.md](docs/usage/USAGE.md) · **Try it:** [in 2 minutes](#try-it-in-2-minutes)

![InsightfulCell dashboard for a sample orders file: four headline numbers, each with a source chip such as F13, above checked scatter charts](reports/presentation/screenshots/16-dashboard-top.png)

## Contents

1. [What it does](#what-it-does)
2. [Try it in 2 minutes](#try-it-in-2-minutes)
3. [How it works (architecture overview)](#how-it-works-architecture-overview)
4. [Tech stack](#tech-stack)
5. [Source code and repository structure](#source-code-and-repository-structure)
6. [Requirements (software dependencies)](#requirements-software-dependencies)
7. [Installation, build and execution](#installation-build-and-execution)
    - [Production build](#production-build)
    - [Stop and reset](#stop-and-reset)
    - [Troubleshooting](#troubleshooting)
8. [Optional: connect an AI model](#optional-connect-an-ai-model)
9. [Testing and quality checks](#testing-and-quality-checks)
10. [Deployment](#deployment)
11. [Security and privacy](#security-and-privacy)
12. [Design decisions](#design-decisions)
13. [Limitations and roadmap](#limitations-and-roadmap)
14. [Documentation](#documentation)
15. [Team and contributions](#team-and-contributions)
    - [How we built it](#how-we-built-it)
    - [Use of AI-assisted tools](#use-of-ai-assisted-tools)
16. [Acknowledgements](#acknowledgements)
17. [Licence](#licence)
18. [Contact](#contact)

## What it does

People with a spreadsheet want answers, but AI assistants can invent numbers and hide their data
cleaning, while statistics tools need a trained analyst. In InsightfulCell, code computes every
number; an AI model (a large language model, optional) only plans the questions and writes
sentences, and every sentence is checked against the stored results before anyone sees it. Raw
rows never reach an AI model, and the whole product works without one.

- **Safe file reading:** CSV, TSV, Excel, Parquet and JSON up to 250 MB are checked byte for byte
  and parsed under strict limits, so a hostile or broken file cannot harm the system.
- **Cleaning you can see:** each proposed fix shows the number of rows it changes; apply it
  automatically or review every change yourself.
- **Tested findings:** relationships, differences and trends are tested with proper statistics,
  with false discoveries controlled across the whole analysis.
- **Traceable numbers:** every figure links to a stored, unchangeable fact showing the test, the
  rows used and how it was computed.
- **Checked charts and maps:** every chart and map value is recomputed before display; a chart that
  doesn't match is withheld.
- **Report and dashboard:** plain-language answers per question, downloadable as PDF, Word,
  Markdown or HTML.
- **Explore further:** drill into one group, or view the original rows beside the findings.
- **Privacy controls:** personal data such as names and emails is detected and masked, and you can
  delete a dataset, an analysis or your whole account at any time.

## Try it in 2 minutes

1. Open <https://app.ic.rgproject.me>.
2. Choose **Create an account**, enter your name and email, type the 6-digit code we email you,
   then choose a password. [confirm: email registration is switched on in production]
3. Download a sample file (public Kaggle datasets, CC0):
   - [Zomato restaurants](sample_data/kaggle_data/Zomato%20Restaurant%20Dataset.csv), 9,551 rows
     ([Kaggle](https://www.kaggle.com/datasets/shrutimehta/zomato-restaurants-data))
   - [AI usage by students and professionals](sample_data/kaggle_data/AI_Usage_and_Impact_on_Students_and_Professionals.csv),
     300 rows ([Kaggle](https://www.kaggle.com/datasets/eishatuzzuhra/ai-usage-and-impact-on-students-and-professionals))
   - [Data science job listings, Australia](sample_data/kaggle_data/listings.csv), 1,498 rows
     ([Kaggle](https://www.kaggle.com/datasets/nomilk/data-science-job-listings-australia-20192020?select=listings.csv))
4. Drop the file on the home page, open it, and click **Analyse newest version**.
5. When it says **Your report is ready**, open the report and click a source chip (for example
   **F12**) to see the test behind that number.

- The live site runs without an AI model, so plans and sentences come from built-in rules and
  templates.
- It runs one analysis at a time, so you may wait briefly while someone else's finishes.
- Please don't upload real personal data.

More detail: [usage guide](docs/usage/USAGE.md).

## How it works (architecture overview)

```mermaid
flowchart LR
    browser([Browser])
    web["Web app<br/>(pages, sign-in)"]
    api["API"]
    worker["Worker"]
    pg[("PostgreSQL<br/>data and job queue")]
    s3[("File storage<br/>(S3)")]
    ai[["AI model<br/>(optional)"]]

    browser -- "pages, session cookie" --> web
    web -- "requests with a 15-minute token" --> api
    web -- "accounts and sessions" --> pg
    browser -- "file parts, signed upload links" --> s3
    browser -- "live progress, one-run ticket" --> api
    api -- "records, queued jobs" --> pg
    pg -- "jobs" --> worker
    worker -- "results, events" --> pg
    api -- "signed upload links, file checks" --> s3
    worker -- "files and outputs" --> s3
    worker -- "masked table description only" --> ai
```

- One codebase runs as three processes: the web app, the API and the worker. They share two
  stores: PostgreSQL (the database, which is also the job queue) and S3-compatible file storage.
- Files go straight from the browser to storage through short-lived signed links; the API then
  checks their size and type.
- The worker runs each analysis as a queued job, saving progress after every step, so a retry
  resumes where it stopped. The browser follows progress live.
- The database itself keeps each organisation's data apart (row-level security: every query only
  sees rows of the signed-in organisation).
- An AI model, when one is configured, sees only a masked description of the table: column
  meanings and rough summaries, never rows or personal values.

Analysis steps: understand the file → clean (with optional review) → test → re-check the strongest
findings on less-cleaned data → chart → write and verify the report.

Deeper detail: [system flow](reports/system_flow.md) and the [decision records](docs/adr/).

## Tech stack

| Layer | Technology (major version) | What it does here |
|---|---|---|
| Web app | Next.js 16, React 19, TypeScript 5, Tailwind CSS 4 | Pages, server-side data fetching, the proxy that keeps API tokens off the browser |
| Sign-in | Better Auth 1.7 | Accounts, sessions, email codes, workspaces and the tokens the API checks |
| API | Python 3.13, FastAPI 0.143, Pydantic 2, SQLAlchemy 2 | Uploads, datasets, runs, reports and downloads |
| Job queue and worker | Procrastinate 3 on PostgreSQL | Runs analyses and upkeep jobs from the database |
| Database | PostgreSQL 18 | Data, job queue and sign-in tables, with row-level security |
| File storage | Amazon S3 (RustFS 1.0 locally) | Uploaded files, cleaned tables and analysis outputs |
| Analysis | DuckDB 1, NumPy 2, SciPy 1, scikit-learn 1, Shapely 2 | Parsing, statistics and map geometry |
| Reports | Jinja2 3, python-docx 1, fpdf2 2 | HTML, Word and PDF downloads |
| AI gateway | Gemini, OpenAI, Anthropic or DeepSeek client libraries | One place for budgets, privacy checks and cost records |
| Tests | pytest 9, Vitest 5, Playwright 1.56, axe | Unit, integration, accessibility and browser tests |
| Hosting | Docker Compose, Caddy 2, GitHub Actions | Containers, HTTPS and image builds |

## Source code and repository structure

This repository holds the complete source code. Its layout:

```text
backend/                Python API and worker
  src/insightfulcell/   application code: api, services, engine (analysis), llm, worker
  migrations/           database migrations (Alembic)
  tests/                unit and integration tests
  openapi.json          API contract, generated from the code
frontend/               Next.js web app
  src/                  pages, components and shared logic
  e2e/                  Playwright browser tests
  tests/                component and library tests
  public/               static files, including the Contact page's team details
deploy/aws/             production setup: Compose file, deploy and backup scripts, guide
scripts/                setup, check, test and helper scripts used by the Makefile
sample_data/            test files, clean to messy, in every supported format
reports/                system flow documents, project and test reports, presentation
docker-compose.yml      PostgreSQL and file storage for local development
Makefile                everyday commands (make help lists them)
LICENSE                 licence terms
```

## Requirements (software dependencies)

| Software | Version | Needed for |
|---|---|---|
| Git | any recent | Cloning the repository |
| Docker Engine and Docker Compose | Compose v2 (tested: Docker 29.1, Compose 2.40) | PostgreSQL and file storage |
| GNU Make | 4 or newer (tested: 4.3) | The `make` commands |
| Node.js | 22 or newer (tested: 22.23) | The web app |
| pnpm | 10.28.0 (pinned; installed by `corepack enable`) | Web app dependencies |
| uv | tested: 0.11 | Python dependencies; it installs Python 3.13 itself |
| Python | 3.13 (from `backend/.python-version`) | The API and worker |

- Tested on Ubuntu 24.04 LTS (local and continuous integration). macOS and Windows have not
  been tested.
- Disk: about 3 GB (dependencies about 1.6 GB, container images about 0.8 GB). Memory: tested on
  a 12 GB machine; we suggest at least 8 GB to run the full stack with the development server.
- Library versions are pinned in `backend/uv.lock` and `frontend/pnpm-lock.yaml` and installed by
  the commands below.

## Installation, build and execution

1. **Clone** the repository.

   ```bash
   git clone https://github.com/rupeshghimire7/InsightfullCell.git
   cd InsightfullCell
   ```

2. **Install dependencies and create the settings files** (copies each `.env.example`, generates
   a local secret, installs git hooks):

   ```bash
   make bootstrap
   ```

   You should see `• created backend/.env` and `• created frontend/.env.local`. The defaults work
   locally with no changes. The settings that matter:

   | Name | What it is | Example value |
   |---|---|---|
   | `DATABASE_URL` (backend) | Where the API and worker reach PostgreSQL | `postgresql+psycopg://ic_app_dev:ic_app_dev@localhost:5432/insightfulcell` |
   | `S3_ENDPOINT_URL`, `S3_BUCKET` (backend) | Local file storage | `http://localhost:9000`, `insightfulcell-dev` |
   | `BETTER_AUTH_URL` (web) | The app's own address | `http://localhost:3000` |
   | `BETTER_AUTH_SECRET` (web) | Signing secret, 32+ random characters | generated by `make bootstrap` |
   | `API_BASE_URL` (web) | Where the web server reaches the API | `http://localhost:8000` |
   | `EMAIL_TRANSPORT` (web) | `console` prints sign-up codes in the terminal instead of emailing | `console` |
   | `LLM_PROVIDER` (backend) | Optional AI model; unset means off | unset |

3. **Start the database and file storage** (Docker must be running):

   ```bash
   make up
   ```

   You should see both containers `Healthy` and `Bucket 'insightfulcell-dev' ready`.

4. **Create the database tables:**

   ```bash
   make migrate
   ```

   It ends with `Auth schema updated`.

5. **Start the API, the worker and the web app** together (Ctrl-C stops all three):

   ```bash
   make dev
   ```

   Lines are labelled `api`, `worker` and `web`. Without the worker, analyses stay queued.

6. **Open <http://localhost:3000> and create an account.** Locally no email is sent: the 6-digit
   code is printed in the `make dev` terminal, on the line after
   `[email:console] Verification code for <your email>`. Then choose a password.

7. **Run a sample file.** Upload `sample_data/1_simple/store_performance_2025.csv`, open it, click
   **Analyse newest version**, and wait for **Your report is ready**.

In our clean-clone test on 24 October 2026 (Ubuntu 24.04, packages already cached), steps 1–7
took 4 minutes and the sample analysis took 11 seconds. A first install also downloads about
1.6 GB of packages and 0.8 GB of container images.

### Production build

The steps above run development servers. `cd frontend && pnpm build` builds the web app; the API
and worker need no build step. Images: `backend/Dockerfile`, `frontend/Dockerfile`.

### Stop and reset

Ctrl-C stops `make dev`. `make down` stops the containers and keeps your data. To delete all local
data as well, run `docker compose down -v`, then `make up` and `make migrate`.

### Troubleshooting

| Problem | Fix |
|---|---|
| `PostgreSQL is not reachable on localhost:5432` | Run `make up`, then `make migrate` |
| `make up` fails with `port is already allocated` (5432 or 9000), or `make dev` can't use 3000 or 8000 | Stop the other program using that port, or another copy of InsightfulCell |
| `permission denied` talking to Docker on Linux | `sudo usermod -aG docker "$USER"`, then log out and back in |
| An analysis stays **Queued** | The worker isn't running: use `make dev` (or `make worker` in another terminal) |
| Can't find the sign-up code | It's in the `make dev` terminal, under `[email:console] Verification code for <your email>` |
| Errors after pulling new code | Run `make migrate`, then restart `make dev` |

## Optional: connect an AI model

Set one provider in `backend/.env` and restart the worker: `LLM_PROVIDER` is `gemini`, `openai`,
`anthropic` or `deepseek`, with that provider's key (`GEMINI_API_KEY`, `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY` or `DEEPSEEK_API_KEY`). The worker checks the key before it takes work.

A backup model (`LLM_FALLBACK_PROVIDER`, `LLM_FALLBACK_MODEL`) answers when the main one fails.
A hard spend cap in Australian dollars (`LLM_SPEND_CAP_AUD`, plus one per provider) covers both:
each call first reserves its worst-case cost, so the total can't pass the cap, and past it runs
use the built-in rules. Everything works without a model.

## Testing and quality checks

Run everything continuous integration runs (with `make up` running; without Docker, the
database steps are skipped):

```bash
make check
```

| Check | Tool | What it proves |
|---|---|---|
| Style and lint | ruff, Prettier, ESLint with accessibility rules | Consistent code, common mistakes caught |
| Types | mypy (strict), TypeScript (strict) | Type errors caught before running |
| Architecture | import-linter (6 rules) | The analysis engine stays free of web and database code |
| Backend tests | pytest on real PostgreSQL and S3 storage | API, engine, security and deletion behaviour |
| Frontend tests | Vitest, Testing Library, axe | Components work and pass accessibility checks |
| Migrations | Alembic up, down, up | Every schema change can be applied and undone |
| API contract | OpenAPI to TypeScript comparison | Web app and API agree |
| Supply chain | pip-audit, pnpm audit, secret scan | No known vulnerable packages or committed secrets |

Latest results (24 October 2026): 726 backend tests passed with 93.04% coverage; 537 frontend
tests passed; 12 of 12 browser tests passed, including a tour of 24 pages with no accessibility
violations. Details: [test completion report](reports/test_completion/test_completion.md).

Browser tests on their own start a fresh, separate stack; the ports below avoid a running copy:

```bash
E2E_API_PORT=8100 E2E_WEB_PORT=3100 scripts/e2e.sh
```

## Deployment

The live site runs on one AWS EC2 instance (t3.micro) with Docker Compose. Caddy serves HTTPS for
`app.ic.rgproject.me` and `api.ic.rgproject.me`. Uploaded files live in Amazon S3, and a nightly job
backs up the database to a second S3 bucket. GitHub Actions builds the images on every push to
`main`, so the small server only pulls them.

On the server, from `deploy/aws`:

```bash
git pull && ./deploy.sh                         # deploy the latest images
IC_IMAGE_TAG=<previous commit sha> ./deploy.sh  # roll back to an earlier build
```

Full guide: [deploy/aws/README.md](deploy/aws/README.md). Cost: the instance and its 30 GB disk
are within the AWS free tier; after it, expect about US$3.60 a month for the public IP address plus
cents for S3. [confirm against the AWS bill]

## Security and privacy

- **Organisations are kept apart by the database:** every query runs with row-level security for
  the signed-in organisation only.
- **The browser never holds an API token:** the web server signs a 15-minute token per request,
  and sign-in cookies can't be read by page scripts.
- **Personal data is found and masked** before any AI call; the gateway blocks any request that
  still contains it.
- **Hostile files are refused:** type allow-list, size cap, content checks, row and column
  limits, and parsing only in the worker.
- **Results can't be altered:** facts, tests and logs are append-only, enforced by the database.
- **Spending has a hard cap**, and wrong passwords and admin keys are rate-limited.

To report a security issue, email [Team contact email]. Please don't open a public issue.

## Design decisions

| Decision | Why we chose it | Record |
|---|---|---|
| Code computes every number; the AI fills sentences with slots the engine completes | A model can't write a wrong number | [ADR-034](docs/adr/034-narration-with-slots-and-a-verifier.md) |
| One AI gateway, four providers, built-in rules as a full fallback | Works with no model or key; privacy checks in one place | [ADR-031](docs/adr/031-llm-gateway-and-providers.md) |
| PostgreSQL is also the job queue; progress streams straight from the API | One system to run and back up | [ADR-030](docs/adr/030-runs-queue-and-live-progress.md) |
| False discoveries controlled across each analysis; places from packaged map data | Fewer false findings; no external map service | [ADR-032](docs/adr/032-phase-b-evidence-and-places.md) |
| Every chart and map value recomputed before display | A wrong chart is withheld, not shown | [ADR-035](docs/adr/035-checked-charts-maps-dashboard-exports-drilldowns.md) |
| Uploads checked twice: at upload, then fully by the worker | Fast uploads without trusting the browser | [ADR-026](docs/adr/026-upload-verification-and-phase-1-scope.md) |
| Automatic cleaning by default, manual review on request | Fast for most people, full control when needed | [ADR-036](docs/adr/036-review-modes-exports-and-hardening.md) |
| Accounts created only by registering (email code or Google), then a password | Every account has a proven email | [ADR-042](docs/adr/042-register-to-create-sign-in-to-enter.md) |
| "Delete my account" erases all data at once; files at the next sweep | People control their data | [ADR-043](docs/adr/043-profile-privacy-and-account-deletion.md) |
| Row-level security for each organisation | Isolation enforced by the database, not by each query | no record yet |
| Backup AI model behind one hard spend cap | Keeps reports flowing without overspending | no record yet |
| One EC2 host with Compose instead of a managed database | Fits a free-tier budget for the competition | no record yet |

All records: [docs/adr/](docs/adr/).

## Limitations and roadmap

| Limitation | Planned fix |
|---|---|
| Automatic cleaning can misjudge 0–100 percentage columns, decimal commas and similar-sounding category names | Use manual review for sensitive work; better detection rules [confirm date] |
| Some files fail to read: nested JSON values, quoted headers with line breaks, Excel title rows above the header | Wider parser support [confirm date] |
| One workspace per person; no invitations | Team workspaces with roles (the roles already exist) [confirm date] |
| The live site runs one analysis at a time on a small server | A larger instance or a second worker [confirm date] |
| "See the data" shows the file before cleaning, without sorting or filtering | Sorting, filtering and a cleaned view [confirm date] |

Next releases: [confirm versions and dates].

## Documentation

| Document | What it covers |
|---|---|
| This README | What InsightfulCell is, how to run, test and deploy it |
| [Usage guide](docs/usage/USAGE.md) | Every screen, step by step, with screenshots |
| [System flow](reports/system_flow.md) and [explained](reports/explained_system_flow.md) | How each part works, with data flows and fallbacks |
| [Decision records](docs/adr/) | Each significant design decision, with alternatives and consequences |
| [AWS deployment guide](deploy/aws/README.md) | Hosting setup, deploys, backups and restores |
| [Test completion report](reports/test_completion/test_completion.md) | Latest test results, with screenshots |
| `backend/openapi.json`, <http://localhost:8000/docs> | The API contract, and interactive API docs while running locally |

## Team and contributions

| Name | Role | Contributions |
|---|---|---|
| Rupesh Ghimire | Product Owner and Lead Engineer | Conceived InsightfulCell and sets its direction. Designed the architecture and built the web app, API, background worker and analysis engine. |
| Sandesh Prasad Paudel [confirm name: Sandesh Paudel on the Contact page] | Quality Assurance and Deployment Engineer | Tests the application end to end; set up the AWS hosting, domain and HTTPS, and keeps the live site running. |

### How we built it

We wrote the requirements and system design first, then built in short iterations, recording each
significant design decision before or as we made it. Continuous integration runs every check on
every push, and we test each release on the live site. [confirm]

### Use of AI-assisted tools

We used AI coding assistants to draft code, tests and documentation. The team defined the problem and the requirements, made and recorded the design
decisions, directed, reviewed and tested every change, and deployed and operates the service.
[confirm: wording to be approved]

## Acknowledgements

| Library | Used for | Licence |
|---|---|---|
| Next.js | Web app framework | MIT |
| React | User interface | MIT |
| Better Auth | Accounts and sign-in | MIT |
| FastAPI | API | MIT |
| SQLAlchemy | Database access | MIT |
| psycopg | PostgreSQL driver | LGPL-3.0 |
| Procrastinate | Job queue | MIT |
| DuckDB | File parsing and queries | MIT |
| NumPy | Numerical computing | BSD-3-Clause |
| SciPy | Statistical tests | BSD-3-Clause |
| scikit-learn | Statistical models | BSD-3-Clause |
| Shapely | Map geometry | BSD-3-Clause |
| fpdf2 | PDF reports | LGPL-3.0 |
| Playwright | Browser tests | Apache-2.0 |

- Country and state boundaries come from Natural Earth 5.1.2 (public domain).
- The Geist font is used under the SIL Open Font Licence.
- The files in `sample_data/` folders 1 to 6 are synthetic: generated, not real records. The three
  files in `sample_data/kaggle_data/` are public Kaggle datasets released as CC0 (public domain):
  [Zomato Restaurants Data](https://www.kaggle.com/datasets/shrutimehta/zomato-restaurants-data),
  [AI Usage and Impact on Students and Professionals](https://www.kaggle.com/datasets/eishatuzzuhra/ai-usage-and-impact-on-students-and-professionals)
  and [Data Science Job Listings Australia 2019–2020](https://www.kaggle.com/datasets/nomilk/data-science-job-listings-australia-20192020).

## Licence

Proprietary: **InsightfulCell Proprietary Licence**, copyright © 2026 Rupesh Ghimire, all rights
reserved. You may read the code and run it locally to evaluate it or review it in an academic or
competition setting. Any other use needs the owner's written permission, and commercial use needs
a separate agreement. Third-party libraries and data keep their own licences. Full terms:
[LICENSE](LICENSE).

## Contact

- [Rupesh Ghimire](https://www.linkedin.com/in/rupesh-ghimire7/)
- [Sandesh Paudel](https://www.linkedin.com/in/sandesh-paudel-601508149/)
- Email: [Team contact email]
