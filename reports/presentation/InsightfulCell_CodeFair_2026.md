# InsightfulCell demo video: run sheet

**Target length:** 9:25 (limit 10:00). **Slides:** 1:50 (19%). **Live demo:** 7:35 (81%).
**Demo file:** `Zomato Restaurant Dataset.csv` (9,551 restaurants, 21 columns, public domain).
**Speakers:** Rupesh (product, analysis, innovation) and Sandesh (testing, deployment, reliability).
The demo follows the order a new user takes through the product. Record on the production site; every number below was produced by InsightfulCell for this file.

## 1. Timing plan

| Time | Speaker | On screen | What is said and shown | Rubric point |
|---|---|---|---|---|
| 0:00–0:08 | Rupesh | Slide A1 | Who we are; one-stop solution | Introduction |
| 0:08–0:35 | Rupesh | Slide A2 | The trust gap; the guiding question | Problem |
| 0:35–1:00 | Rupesh | Slide A3 | Code computes, AI only writes, everything checked | Innovation |
| 1:00–1:25 | Sandesh | Slide A4 | One codebase, three processes, two stores | Technical |
| 1:25–1:30 | Rupesh | Slide A5 | What you will see | — |
| 1:30–1:45 | Rupesh | Live: sign-in | Register or sign in | Functionality |
| 1:45–2:35 | Rupesh | Live: home | Upload the Zomato file; upload it again (duplicate notice); try a PDF (refused) | Functionality, common interactions |
| 2:35–3:20 | Sandesh | Live: analysis page | Choose Manual, start; narrate the steps as they run | Technical |
| 3:20–4:30 | Rupesh | Live: review step | The Mangalore → Bangalore merge, Now/After, Keep as is; Ask for something else; plan; recipe and masked description | Functionality, UX, privacy |
| 4:30–5:45 | Rupesh | Live: report | In short; evidence tiers; open source chip F10; slots filled from facts; written by our engine | Innovation |
| 5:45–6:35 | Rupesh | Live: dashboard | At a glance tiles; "All 12 values were checked"; map from coordinates | Innovation, functionality |
| 6:35–7:25 | Rupesh | Live: See the data, Explore | Masked columns hidden; explore one group, marked exploratory | Functionality, privacy |
| 7:25–7:50 | Rupesh | Live: Download | Download the PDF and open it | Completeness |
| 7:50–8:25 | Sandesh | Live: accessibility | Keyboard (Tab, Escape), dark theme, phone width | UX |
| 8:25–9:05 | Sandesh | GitHub, AWS | Repository, Actions (CI and image builds), HTTPS site, deployment kit | Technical, documentation |
| 9:05–9:20 | Sandesh | Slide A6 | Limitations and what comes next | Required content |
| 9:20–9:25 | Rupesh | Slide A7 | Thank you and links | Close |

## 2. Script (about 140 words a minute)

**0:00 Rupesh (A1).** Hi, we're Rupesh and Sandesh from Charles Darwin University. This is InsightfulCell: a one-stop solution for data analysis. Bring a spreadsheet, and leave with clear answers, charts and a report your team can check.

**0:08 Rupesh (A2).** Australia has 2.8 million actively trading businesses, and almost none of them employs an analyst. When they want answers from a spreadsheet, they have two options. A general AI assistant is easy, but it can invent numbers, its cleaning is invisible, and your raw rows leave your hands. A BI or statistics tool is accurate, but it needs someone who knows which test to run. That's the trust gap. So we asked: can analysing your own data be as easy as asking an assistant, and as trustworthy as a careful analyst?

**0:35 Rupesh (A3).** InsightfulCell splits the work strictly. Code computes every number. An AI model, if one is switched on, only plans questions and phrases sentences, and it can never write a number. Every sentence is checked and every chart value recomputed before you see it. Raw rows never reach an AI model, and the product works fully without one. The live site runs with no AI model at all.

**1:00 Sandesh (A4).** It's one codebase running as three processes: a web app, an API and a worker, sharing two stores. PostgreSQL holds the data and also runs the job queue and live progress; Amazon S3 holds the files. Uploads go straight from your browser to storage, and the browser never holds an API token. Fewer moving parts: simpler, safer and cheaper to run.

**1:25 Rupesh (A5).** Let's do it live, with a public dataset of 9,551 restaurants.

**1:30 Rupesh (sign in).** I'm signing in to my workspace. Every workspace is private: the database itself stops anyone else reading it.

**1:45 Rupesh (upload).** I drop in the Zomato file. It goes straight to secure storage in parts while my browser fingerprints it. Saved. If I upload the same file again, InsightfulCell recognises it and stores nothing new. And a PDF is refused before it even uploads, with the formats it accepts.

**2:35 Sandesh (analysis).** I open the dataset, choose Manual review, and start. These are the 12 steps, each saved as it finishes, so a retry resumes rather than starting over. The first step re-checks the file's fingerprint and reads all 9,551 rows in a few seconds; it has already masked two columns, restaurant names and street addresses, so they're never analysed. Then it profiles every column, plans 20 questions with built-in rules, and proposes cleaning. Because I chose Manual, it stops for me.

**3:20 Rupesh (review).** Six safe changes, like trimming spaces, were applied automatically. One needs a decision: a rule noticed that "Mangalore" and "Bangalore" are spelt alike and suggests merging them, 20 rows. They're two different cities, so I keep it as it is. This is why Manual mode exists: anything that could remove or replace values waits for a person. I can also ask for something else in my own words. Here's the plan, with the masked columns, and the recipe: a Python script that rebuilds the cleaned table, plus exactly what an AI model would be shown. No rows, no personal values. I approve and continue.

**4:30 Rupesh (report).** The report is ready. It opens with "In short". Each number is followed by a source chip. "Price range and Aggregate rating rise together: the link is moderate, rank correlation 0.50." I click F10, and here's the fact behind it: the test, the rows used, the interval, the p-value after false-alarm control, and a content hash. That sentence was written with slots, and the engine filled the numbers from this fact, so no writer could type a wrong number. Every finding carries an evidence tier, and all ten tests here passed the 5% false-alarm control. Down here it says the report was written by our engine, because no AI model is configured.

**5:45 Rupesh (dashboard).** The dashboard: headline tiles with their evidence and sources. Under each chart: "All 12 values were checked against the data." Before showing anything, InsightfulCell recomputed 1,439 values with separate queries, and none had to be withheld. The city names didn't match enough known places, but the restaurants' coordinates matched Indian states for 85% of rows, so we get this map, matched offline.

**6:35 Rupesh (See the data, Explore).** See the data opens the original rows beside the report, so I can check any finding; the masked columns stay hidden. And I can explore one group on its own: a new analysis of just that group, marked exploratory, because I chose where to look after seeing the results.

**7:25 Rupesh (download).** Download gives PDF, Word, Markdown or HTML, all with the same checked sentences and sources. Here's the PDF.

**7:50 Sandesh (accessibility).** Everything works from the keyboard: Tab to move, Escape to close. There's a dark theme, and on a phone every page fits without sideways scrolling. Our automated accessibility checks found zero violations across 24 pages.

**8:25 Sandesh (GitHub and AWS).** The code is public on GitHub. GitHub Actions runs our checks and builds the container images on every change: 726 backend tests at 93% coverage, 537 frontend tests and 12 full-stack browser tests. The site runs on one AWS server behind automatic HTTPS, with files in private S3 and nightly backups; deploying or rolling back is one command. In our security test, zero of 22 attempts to read another workspace's data succeeded.

**9:05 Sandesh (A6).** Honest limits: automatic cleaning can mis-read a few rare formats, as you saw with Mangalore, so use Manual review for sensitive work. The live site runs one analysis at a time, and plain-language questions aren't built yet. Next: questions in plain language, safer cleaning and monitoring in Release 1 alpha on 11 December, team workspaces in March, and general availability in April 2027.

**9:20 Rupesh (A7).** Thank you. Try it at app.ic.rgproject.me; the code is linked below.

## 3. Recording checklist

**Before recording**
- Warm the server with one analysis of the Zomato file; confirm `https://api.ic.rgproject.me/healthz` answers `{"status":"ok"}`.
- Use a fresh demo account with no real personal data; delete earlier analyses of the file so the duplicate notice is a surprise only where intended.
- Keep a finished Zomato analysis open in a second tab as a backup.

**Screen setup**
- 1920×1080 at 30 fps; browser zoom 110–125%; clean browser profile, bookmarks bar hidden; notifications off; cursor highlight on.

**Audio**
- External or headset microphone in a quiet room; record a 10-second test first.

**Editing**
- Cut silences only. Never speed up or fake processing time; if a wait is shortened, show "(shortened)" on screen.

**YouTube**
- Visibility **Public**.
- Title: "InsightfulCell: CDU IT Code Fair Sydney 2026 (Coding Competition)".
- Description: "InsightfulCell turns a spreadsheet into a checked report: code computes every number, AI only writes, and raw rows never reach an AI model." Then links: live site and repository; the team; chapters: 0:00 Problem · 1:00 Architecture · 1:30 Upload · 2:35 Analysis · 3:20 Review · 4:30 Report · 5:45 Dashboard and map · 6:35 See the data and Explore · 7:25 Downloads · 7:50 Accessibility · 8:25 GitHub and AWS · 9:05 Limits and next steps.
- Captions: auto-generated, then corrected. Thumbnail: slide A1.

**Final checks**
- Length ≤ 10:00; slides ≤ 20% of the time; every major feature shown; architecture explained; limitations and future improvements discussed; all links work.

## 4. Competition-day variants

| Slot | Slides | Live demo |
|---|---|---|
| 3 minutes | A1, A2, A3 | Upload the Zomato file (Automatic) → report → open source chip F10 → A7 |
| 5 minutes | A1–A5 | Upload → Manual → Keep Mangalore as is → report and source chip → dashboard "All values checked" → A7 |
| 10 minutes | A1–A7 | The full run sheet above |

- **Use Part B** (hidden fallback slides B1–B6) if the Wi-Fi or the live site fails: unhide and step through them with the same script.
- **Use Part C** (backup slides C1–C10, after Thank you) to answer judges' questions: analysis steps, numbers from facts, statistics, security, quality, deployment, comparison, business model, team, roadmap.
