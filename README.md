# InsightfulCell

InsightfulCell is a one-stop solution for data analysis: upload a spreadsheet and get a cleaned
dataset, statistically tested findings, checked charts and maps, and a plain-language report in which
every number traces back to the test that produced it.

**Live app:** <https://app.ic.rgproject.me> · **Demo video:** [YouTube link] ·
**Try it:** [in 5 minutes](#try-it-in-5-minutes)

![InsightfulCell dashboard for the Zomato restaurants sample: headline tiles with evidence badges and source chips, above checked charts](reports/presentation/screenshots/21-dashboard.png)

## Contents

1. [What it does](#what-it-does)
2. [Try it in 5 minutes](#try-it-in-5-minutes)
3. [How it works](#how-it-works)
4. [Tech stack](#tech-stack)
5. [Repository layout](#repository-layout)
6. [Requirements (software dependencies)](#requirements-software-dependencies)
7. [Installation, build and execution](#installation-build-and-execution)
8. [Optional: connect an AI model](#optional-connect-an-ai-model)
9. [Testing and quality checks](#testing-and-quality-checks)
10. [Deployment](#deployment)
11. [Security and privacy](#security-and-privacy)
12. [Design decisions](#design-decisions)
13. [Limitations and roadmap](#limitations-and-roadmap)
14. [Documentation](#documentation)
15. [Team and contributions](#team-and-contributions)
16. [Acknowledgements](#acknowledgements)
17. [Licence](#licence)
18. [Contact](#contact)

## What it does

People with a spreadsheet want answers, but AI assistants can invent numbers and need the raw data,
while statistics tools need a trained analyst. In InsightfulCell, code computes every number. An AI
model, when one is switched on, only plans the questions and phrases sentences, and every sentence is
checked against stored results before anyone sees it. Raw rows never reach an AI model, and the whole
product works without one.

- **Safe file reading:** CSV, TSV, Excel, Parquet and JSON up to 250 MB, uploaded straight to storage
  and read under strict limits, so a hostile or broken file cannot harm the system.
- **Personal data protected:** names, email addresses, phone numbers, identity numbers, street
  addresses, dates of birth, IP addresses and card numbers are found and masked as the file is read.
- **Cleaning you can review:** Automatic by default, or Manual, where every change that could remove
  or replace values waits for your decision. The original file is never changed; a Python recipe
  rebuilds the cleaned table.
- **Tested findings:** nine analysis methods, with false alarms limited to 5% across the whole
  analysis and an evidence tier on every finding.
- **Traceable numbers:** every figure links to a stored, unchangeable fact with its test, rows and
  content hash.
- **Checked charts and maps:** every value is recomputed before display; a chart that disagrees is
  withheld. Places are matched offline to countries, states and Australian postcodes.
- **Explore and share:** see the original rows beside the report, explore one group on its own, and
  download the report as PDF, Word, Markdown or HTML.
- **You stay in control:** delete an analysis, a dataset or your whole account at any time.

## Try it in 5 minutes

1. Open <https://app.ic.rgproject.me> and choose **Create an account**.
2. Download a sample file (public Kaggle datasets, CC0):
   - [Zomato restaurants](sample_data/kaggle_data/Zomato%20Restaurant%20Dataset.csv), 9,551 rows
     ([source](https://www.kaggle.com/datasets/shrutimehta/zomato-restaurants-data))
   - [Data science job listings, Australia](sample_data/kaggle_data/listings.csv), 1,498 rows
     ([source](https://www.kaggle.com/datasets/nomilk/data-science-job-listings-australia-20192020))
   - [AI usage by students and professionals](sample_data/kaggle_data/AI_Usage_and_Impact_on_Students_and_Professionals.csv),
     300 rows ([source](https://www.kaggle.com/datasets/eishatuzzuhra/ai-usage-and-impact-on-students-and-professionals))
3. Drop the file on the home page, open it, choose **Manual** and click **Analyse newest version**.
4. At **Your review is needed**, open **Review changes**. For the Zomato file, InsightfulCell suggests
   merging "Mangalore" into "Bangalore" because the names look alike; they are different cities, so
   choose **Keep as is**, then **Approve and continue**.
5. When it says **Your report is ready**, open the report and click a source chip (for example
   **F10**) to see the test behind that number.

The live site runs without an AI model (plans and sentences come from built-in rules and templates)
and analyses one file at a time, so yours may wait briefly in the queue. Please don't upload real
personal data.

## How it works

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
    browser -- "live progress, one-analysis ticket" --> api
    api -- "records, queued jobs" --> pg
    pg -- "jobs" --> worker
    worker -- "results, events" --> pg
    api -- "signed upload links, file checks" --> s3
    worker -- "files and outputs" --> s3
    worker -- "masked table description only" --> ai
```

- One codebase runs as three processes (web app, API, worker) over two stores: PostgreSQL, which is
  also the job queue, and S3-compatible file storage.
- Files go straight from the browser to storage through short-lived signed links; the API checks
  their size and type, and the worker checks their fingerprint again before reading.
- The worker runs each analysis as 12 steps and saves after every step, so a retry resumes where it
  stopped. The browser follows progress live.
- The database keeps each workspace's data apart with row-level security: every query only sees the
  signed-in workspace's rows.
- An AI model, when configured, sees only a masked description of the table: column meanings and
  rounded summaries, never rows or personal values.

The 12 steps: verify and read the file → profile every column → plan the analysis → propose cleaning →
(review, in Manual mode) → apply the cleaning → find the places → test the extra measures → answer the
questions → check the findings hold → rank the findings → check the charts and maps → write the report.

Deeper detail: [system flow](reports/system_flow.md) and the [explained system flow](reports/explained_system_flow.md).

## Tech stack

| Layer | Technology (major version) | What it does here |
|---|---|---|
| Web app | Next.js 16, React 19, TypeScript 5, Tailwind CSS 4 | Pages, server-side data fetching, the proxy that keeps API tokens off the browser |
| Sign-in | Better Auth 1.7 | Accounts, sessions, email codes, workspaces and the tokens the API checks |
| API | Python 3.13, FastAPI 0.143, Pydantic 2, SQLAlchemy 2 | Uploads, datasets, analyses, reports and downloads |
| Job queue and worker | Procrastinate 3 on PostgreSQL | Runs analyses and upkeep jobs from the database |
| Database | PostgreSQL 18 | Data, job queue and sign-in tables, with row-level security |
| File storage | Amazon S3 (RustFS 1.0 locally) | Uploaded files, cleaned tables and analysis outputs |
| Analysis | DuckDB 1, NumPy 2, SciPy 1, scikit-learn 1, Shapely 2 | Parsing, statistics and map geometry |
| Reports | Jinja2 3, python-docx 1, fpdf2 2 | HTML, Word and PDF downloads |
| AI gateway | Gemini, OpenAI, Anthropic or DeepSeek client libraries | One place for budgets, privacy checks and cost records |
| Tests | pytest 9, Vitest 5, Playwright 1.56, axe | Unit, integration, accessibility and browser tests |
| Hosting | Docker Compose, Caddy 2, GitHub Actions | Containers, HTTPS and image builds |

## Repository layout

```text
backend/                Python API, worker and analysis engine
  src/insightfulcell/   application code: api, services, engine (analysis), llm, worker
  migrations/           database migrations (Alembic)
  tests/                unit and integration tests
  openapi.json          API contract, generated from the code
frontend/               Next.js web app
  src/                  pages, components and shared logic
  e2e/                  Playwright browser tests
  tests/                component and library tests
deploy/aws/             production setup: Compose file, deploy and backup scripts, guide
scripts/                setup, check, test and helper scripts used by the Makefile
sample_data/            synthetic test files (clean to messy, every format) and 3 public Kaggle datasets
reports/                system flow documents, project report, presentation and screenshots
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

7. **Run a sample file.** Upload `sample_data/kaggle_data/Zomato Restaurant Dataset.csv`, open it,
   click **Analyse newest version**, and wait for **Your report is ready**.

In our clean-clone test on 24 October 2026 (Ubuntu 24.04, packages already cached), steps 1–7
took 4 minutes and a sample analysis took 11 seconds. A first install also downloads about
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

Run everything continuous integration runs (with `make up` running; without Docker, the database
steps are skipped):

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

Latest results (24 October 2026): 726 backend tests passed with 93.04% coverage; 536 of 537 frontend
tests passed (the one failure checks the spelling of a team member's name in the Contact page's team
file); 12 of 12 browser tests passed, including a tour of 24 pages with no accessibility violations.
Two evaluations run with every test suite: planted-effect datasets the statistics must find, and a
corrupted narrator whose 31 planted errors the sentence verifier must all reject.

Browser tests on their own start a fresh, separate stack; the ports below avoid a running copy:

```bash
E2E_API_PORT=8100 E2E_WEB_PORT=3100 scripts/e2e.sh
```

## Deployment

The live site runs on one AWS EC2 instance (t3.micro) with Docker Compose. Caddy serves HTTPS for
`app.ic.rgproject.me` and `api.ic.rgproject.me`. Uploaded files live in Amazon S3, reached through the
instance's role (no stored keys), and a nightly job backs up the database to a second S3 bucket.
GitHub Actions builds the images on every push to `main`, so the small server only pulls them.

On the server, from `deploy/aws`:

```bash
git pull && ./deploy.sh                         # deploy the latest images
IC_IMAGE_TAG=<previous commit sha> ./deploy.sh  # roll back to an earlier build
```

Full guide: [deploy/aws/README.md](deploy/aws/README.md). Cost: the instance and its 30 GB disk are
within the AWS free tier; the public IP address costs about US$3.60 a month (estimated), plus cents
for S3.

## Security and privacy

- **Workspaces are kept apart by the database:** every query runs under row-level security for the
  signed-in workspace only.
- **The browser never holds an API token:** the web server signs a 15-minute token per request, and
  sign-in cookies can't be read by page scripts.
- **Accounts are verified:** accounts are created by registering with an emailed code (or Google), and
  every account sets a strong password checked on the server.
- **Personal data is found and masked** before any AI call; the gateway blocks any request that still
  contains it.
- **Hostile files are refused:** type allow-list, size cap, content checks, row and column limits, and
  parsing only in the worker.
- **Results can't be altered:** facts, tests and logs are append-only, enforced by the database.
- **Spending has a hard cap**, and wrong passwords and admin keys are rate-limited.

In our security test (18 October 2026), 0 of 22 attempts to read or change another workspace's data
succeeded. To report a security issue, email rupeshghimire17@gmail.com rather than opening a public
issue.

## Design decisions

| Decision | Why we chose it |
|---|---|
| Code computes every number; the writer leaves slots the engine fills from stored facts | A model can't write a wrong number |
| One AI gateway, four providers, built-in rules as a full fallback | Works with no model or key; privacy checks and budgets in one place |
| A backup model behind one hard spend cap, reserved before every call | Keeps reports flowing without overspending, even with many users |
| PostgreSQL is also the job queue; progress streams straight from the API | One system to run, secure and back up |
| False discoveries controlled across each analysis; places from packaged map data | Fewer false findings; no outside map service sees place names |
| Every chart and map value recomputed before display | A wrong chart is withheld, not shown |
| Uploads checked twice: at upload, then fully by the worker | Fast uploads without trusting the browser |
| Automatic cleaning by default, Manual review on request | Fast for most people, full control when needed |
| Accounts created only by registering (email code or Google), then a password | Every account has a proven email |
| Row-level security for each workspace | Isolation enforced by the database, not by each query |
| "Delete my account" erases all data at once; files at the next sweep | People control their data |
| One EC2 host with Compose instead of managed services | Fits a free-tier budget; HTTPS and deploys stay one command |

## Limitations and roadmap

| Limitation | Planned fix | When |
|---|---|---|
| Automatic cleaning can mis-read a few rare formats: percentages stored as 0–100, decimal commas, and similar-looking names. Manual review lets you approve every change | Better detection rules; changes that can lose data always shown | Before Release 1 alpha |
| Some file shapes can't be read yet: nested JSON values, Excel title rows above the header, quoted headers with line breaks | Wider file support | Before Release 1 alpha |
| One small server runs one analysis at a time, with no staging, monitoring or audit log yet | Larger server or second worker; staging, monitoring, audit log, rate limits | Release 1 alpha |
| One workspace per person, no invitations or shared reports | Team workspaces and roles | Release 2 beta |
| No questions in plain language yet | Questions about your data, verified like the report | Release 1 alpha |

Planned releases: Release 1 alpha (11 December 2026), Release 2 beta (12 March 2027), general
availability (9 April 2027).

## Documentation

| Document | What it covers |
|---|---|
| This README | What InsightfulCell is, and how to run, test and deploy it |
| [System flow](reports/system_flow.md) | Every component, data flow, fallback and security control |
| [Explained system flow](reports/explained_system_flow.md) | Each component in depth: exact rules, thresholds and prompts |
| [Project report](reports/InsightfulCell_Project_Report.pdf) ([Word](reports/InsightfulCell_Project_Report.docx)) | The product, its engineering, testing, deployment and roadmap |
| [Project summary](reports/InsightfulCell_Project_Summary.pdf) | One page: problem, solution, key results |
| [Presentation](reports/presentation/InsightfulCell_CodeFair_2026.pptx) and [demo run sheet](reports/presentation/InsightfulCell_CodeFair_2026.md) | The Code Fair slides and the demo video plan |
| [AWS deployment guide](deploy/aws/README.md) | Hosting setup, deploys, backups and restores |
| `backend/openapi.json`, <http://localhost:8000/docs> | The API contract, and interactive API docs while running locally |

## Team and contributions

| Name | Role | Contributions |
|---|---|---|
| Rupesh Ghimire | Product Owner and Lead Engineer | Conceived InsightfulCell and sets its direction, from the problem it solves to what ships next. Designed the system architecture and built the web app, the API, the background worker and the analysis engine, which cleans the data, runs the statistical tests and writes the verified report. |
| Sandesh Prasad Paudel | Quality Assurance and Deployment Engineer | Tests InsightfulCell end to end: test planning, functional and regression testing of every user flow, cross-browser, mobile, usability and accessibility checks, and acceptance testing on the live site. Took it from a local setup to a live public service: set up its AWS hosting, domain and secure HTTPS connection, the image build and deployment workflow, backups and storage access, and keeps the site running for users and judges. |

### How we built it

We wrote the requirements and the system design first, then built in short stages, each closed only
when the full quality gate passed. We recorded each significant design decision with its reasons and
the options we rejected. Continuous integration runs the same checks on every push, and we test each
release on the live site.

### Use of AI-assisted tools

We used AI coding assistants during development, mainly to draft code, tests and documentation from
our instructions. The team defined the problem and the requirements, designed the architecture, made
and recorded the design decisions, directed and reviewed every change, tested the system end to end,
and deployed and operates the live service. InsightfulCell's own use of AI is separate and optional:
the product runs fully without an AI model.

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
| axe-core | Accessibility tests | MPL-2.0 |

- Country and state boundaries come from Natural Earth 5.1.2 (public domain).
- Exported reports use the DejaVu Sans fonts (DejaVu fonts licence, included); the web app uses the
  Geist font (SIL Open Font Licence).
- The files in `sample_data/` folders 1 to 6 are synthetic: generated, not real records. The three
  files in `sample_data/kaggle_data/` are public Kaggle datasets released as CC0 (public domain):
  [Zomato Restaurants Data](https://www.kaggle.com/datasets/shrutimehta/zomato-restaurants-data),
  [AI Usage and Impact on Students and Professionals](https://www.kaggle.com/datasets/eishatuzzuhra/ai-usage-and-impact-on-students-and-professionals)
  and [Data Science Job Listings Australia 2019–2021](https://www.kaggle.com/datasets/nomilk/data-science-job-listings-australia-20192020).

## Licence

Proprietary: **InsightfulCell Proprietary Licence**, copyright © 2026 Rupesh Ghimire, all rights
reserved. You may read the code and run it locally to evaluate it or review it in an academic or
competition setting. Any other use needs the owner's written permission, and commercial use needs a
separate agreement. Third-party libraries and data keep their own licences. Full terms:
[LICENSE](LICENSE).

## Contact

- [Rupesh Ghimire](https://www.linkedin.com/in/rupesh-ghimire7/)
- [Sandesh Prasad Paudel](https://www.linkedin.com/in/sandesh-paudel-601508149/)
- Email: rupeshghimire17@gmail.com
