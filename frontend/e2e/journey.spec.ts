import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { register } from "./auth";

/**
 * The journeys in a real browser: register (emailed code, then a password; `auth.ts`), upload a multi-part file (Web Worker SHA-256,
 * CORS, presigned PUTs), see it listed, upload it again, sign out; then a messy file through the
 * review gate to the report and its evidence.
 */

const EMAIL = `e2e-${Date.now()}@example.com`;

function csv(rows: number, tag: string): Buffer {
  const header = "id,suburb,price,tag\n";
  const body = Array.from(
    { length: rows },
    (_, i) => `${i},Suburb ${i % 40},${500000 + i * 7},${tag}`,
  );
  return Buffer.from(header + body.join("\n") + "\n");
}

/** Our alerts only: Next.js keeps its own empty `role="alert"` route announcer in every page. */
function problemAlert(page: Page) {
  return page.locator('[role="alert"]:not(#__next-route-announcer__)');
}

async function expectNoAxeViolations(page: Page) {
  // Entrance animations fade content in; contrast is measured on the settled page.
  await page.waitForFunction(() =>
    document.getAnimations().every((a) => a.playState !== "running"),
  );
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag22aa"])
    .analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
}

test("sign in, upload, deduplicate, sign out", async ({ page }) => {
  const policyViolations: string[] = [];
  page.on("console", (message) => {
    if (/Content Security Policy|Refused to/i.test(message.text()))
      policyViolations.push(message.text());
  });
  page.on("pageerror", (error) => policyViolations.push(error.message));
  // Any other console error (hydration mismatches, React warnings, failed scripts) is a bug.
  // Allowed: "Failed to load resource" for requests the journey makes fail on purpose, and the
  // CORS refusal of the deliberate cross-origin probe below (any API path but /events).
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    const text = message.text();
    const probe = /blocked by CORS policy/.test(text) && !/\/events/.test(text);
    if (message.type() === "error" && !/Failed to load resource/.test(text) && !probe) {
      consoleErrors.push(text);
    }
  });

  // --- signed out: redirected, accessible, honest about errors
  await page.goto("/");
  await expect(page).toHaveURL(/\/sign-in$/);
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expectNoAxeViolations(page);

  await expect(
    page.getByRole("heading", { name: "Your one stop solution for data analysis." }),
  ).toBeVisible();
  await expect(page.getByText("We respect your data privacy.", { exact: false })).toBeVisible();

  await page.getByLabel("Email address").fill("not-an-email");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText("Enter your email address")).toBeVisible();
  await expect(page.getByLabel("Email address")).toBeFocused();

  // --- the sign-in link button carries no caution note, and the server refuses to sign up by
  // link or by password: accounts are created by registering only
  const linkButton = page.getByRole("link", { name: "Email me a sign-in link" });
  await linkButton.hover();
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  const direct = await page.request.post("/api/auth/sign-in/magic-link", {
    data: { email: EMAIL },
  });
  expect(direct.status()).toBe(400);
  expect((await direct.json()).code).toBe("ACCOUNT_NOT_FOUND");
  const signUp = await page.request.post("/api/auth/sign-up/email", {
    data: { name: "x", email: EMAIL, password: "Long-enough-Pass1" },
  });
  expect(signUp.status()).toBe(404);

  // --- register: emailed code, then a password
  await register(page, EMAIL);
  await expect(page.getByRole("heading", { name: "Home", level: 1 })).toBeVisible();
  await expect(page.getByText("No datasets yet")).toBeVisible();
  await expectNoAxeViolations(page);

  // --- upload: 9 MB of CSV is two parts at the default 8 MiB part size
  const upload = csv(300_000, "first");
  expect(upload.length).toBeGreaterThan(8 * 1024 * 1024);
  const input = page.locator('input[type="file"]');
  await input.setInputFiles({ name: "Home sales 2026.csv", mimeType: "text/csv", buffer: upload });
  await expect(page.getByText("Saved")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("row", { name: /Home sales 2026.*CSV/ })).toBeVisible();
  await expectNoAxeViolations(page);

  // --- same bytes again: nothing new is stored, and the user is told
  await input.setInputFiles({ name: "copy.csv", mimeType: "text/csv", buffer: upload });
  await expect(page.getByText("already uploaded this exact file")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("row")).toHaveCount(2); // header + one dataset

  // --- unsupported type is explained without leaving the page
  await input.setInputFiles({
    name: "notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("hello"),
  });
  await expect(problemAlert(page)).toContainText("This file type isn’t supported");

  // --- content that is not what its extension claims is rejected by the server
  await input.setInputFiles({
    name: "fake.csv",
    mimeType: "text/csv",
    buffer: Buffer.concat([Buffer.from("%PDF-1.7\n"), Buffer.alloc(300)]),
  });
  await expect(problemAlert(page)).toContainText(/does not match|looks like/i, {
    timeout: 30_000,
  });

  // --- the page and storage agree after a reload
  await page.reload();
  await expect(page.getByRole("row")).toHaveCount(2);
  await expect(page.getByRole("row", { name: /Home sales 2026.*Not analysed/ })).toBeVisible();

  // --- analyse it: the worker re-hashes the bytes, parses them, and the page follows live
  await page.getByRole("link", { name: "Home sales 2026" }).click();
  await expect(page.getByRole("heading", { name: "Home sales 2026", level: 1 })).toBeVisible();
  await expect(page.getByText("Not analysed yet")).toBeVisible();
  await expectNoAxeViolations(page);
  // Automatic is the default: the run applies its changes and never stops for a review.
  await expect(page.getByRole("radio", { name: "Automatic" })).toBeChecked();
  await page.getByRole("button", { name: "Analyse newest version" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}\/runs\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("heading", { name: "Analysis of Home sales 2026" })).toBeVisible();
  await expect(page.getByText("Your report is ready")).toBeVisible({
    timeout: 90_000,
  });
  await expect(page.getByTestId("connection")).toHaveCount(0); // finished: nothing to follow
  // Every row of the file: read (ingest), complete (profile), before and after cleaning.
  await expect(page.getByText("300,000")).toHaveCount(4);
  await page.getByText(/Show the 4 columns/).click();
  await expect(page.getByTitle("suburb")).toBeVisible(); // in the column list
  await expectNoAxeViolations(page);

  // --- the live stream itself, from the page: ticket from the web app, then the API directly
  // (cross-origin: proves the CSP connect-src, the API's CORS rule and the ticket audience)
  const runId = page.url().split("/").pop() ?? "";
  const stream = await page.evaluate(async (id) => {
    const ticket = await (await fetch(`/api/runs/${id}/events-ticket`, { method: "POST" })).json();
    const response = await fetch(ticket.url, {
      headers: { Authorization: `Bearer ${ticket.ticket}`, "Last-Event-ID": "1" },
    });
    return {
      status: response.status,
      type: response.headers.get("content-type"),
      body: await response.text(),
    };
  }, runId);
  expect(stream.status).toBe(200);
  expect(stream.type).toContain("text/event-stream");
  expect(stream.body).not.toContain("event: run.queued"); // replay starts after Last-Event-ID
  expect(stream.body).toContain("event: stage.completed");
  expect(stream.body).toContain("event: run.succeeded");
  // Only the stream answers cross-origin: any other API path is refused by the browser (CORS),
  // so a page can never use a ticket (or anything else) against the rest of the API.
  const crossOrigin = await page.evaluate(async (id) => {
    const ticket = await (await fetch(`/api/runs/${id}/events-ticket`, { method: "POST" })).json();
    const api = new URL(ticket.url).origin;
    try {
      await fetch(`${api}/v1/runs/${id}`, {
        headers: { Authorization: `Bearer ${ticket.ticket}` },
      });
      return "readable";
    } catch {
      return "refused";
    }
  }, runId);
  expect(crossOrigin).toBe("refused");

  // --- a finished run opens on its dashboard
  await page.getByRole("link", { name: "Open the dashboard" }).click();
  await expect(page.getByRole("heading", { name: /^Dashboard: / })).toBeVisible();
  await expect(page.getByText("Charts checked")).toBeVisible();
  await expectNoAxeViolations(page);

  // --- the run is listed, and the dataset shows its latest analysis
  await page.getByRole("link", { name: "Runs", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Runs", level: 1 })).toBeVisible();
  await expect(page.getByRole("row", { name: /Home sales 2026.*Succeeded/ })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.getByRole("link", { name: "Home", exact: true }).click();
  await expect(page.getByRole("row", { name: /Home sales 2026.*Succeeded/ })).toBeVisible();

  // --- the API token never reaches the browser: no token route, no token header
  expect((await page.request.get("/api/auth/token")).status()).toBe(404);
  const session = await page.request.get("/api/auth/get-session");
  expect(session.status()).toBe(200);
  expect(session.headers()["set-auth-jwt"]).toBeUndefined();
  expect(await session.text()).not.toMatch(/eyJ[\w-]+\.eyJ/);

  // --- sign out returns to sign-in and protects the app again, at once: the old cookies
  // are refused (no cached session outlives a sign-out)
  const cookies = await page.context().cookies();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/sign-in$/);
  await page.goto("/");
  await expect(page).toHaveURL(/\/sign-in$/);
  const replay = await page.context().browser()!.newContext({ baseURL: page.url() });
  await replay.addCookies(cookies);
  expect((await replay.request.get("/api/backend/v1/me")).status()).toBe(401);
  await replay.close();

  expect(policyViolations).toEqual([]);
  expect(consoleErrors).toEqual([]);
});

test("another user cannot see the first user's datasets", async ({ page }) => {
  await register(page, `e2e-other-${Date.now()}@example.com`, "E2E Other");
  await expect(page.getByText("No datasets yet")).toBeVisible();

  const response = await page.request.get("/api/backend/v1/datasets");
  expect(response.status()).toBe(200);
  expect((await response.json()).items).toEqual([]);

  // the proxy exposes only the allow-listed API surface
  expect((await page.request.get("/api/backend/readyz")).status()).toBe(404);
  expect((await page.request.get("/api/backend/docs")).status()).toBe(404);
});

test("the register page is accessible and links back to sign-in", async ({ page }) => {
  await page.goto("/sign-in");
  await page.getByRole("link", { name: "Create an account" }).click();
  await expect(page.getByRole("heading", { name: "Create an account", level: 1 })).toBeVisible();
  await expect(page.getByLabel("Password", { exact: true })).toHaveCount(0);
  await expectNoAxeViolations(page);
  await expect(page.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/sign-in");
});

test("a messy file pauses for review, and approving continues the analysis", async ({ page }) => {
  test.setTimeout(240_000);
  await register(page, `e2e-review-${Date.now()}@example.com`, "E2E Reviewer");
  await expect(page.getByText("No datasets yet")).toBeVisible();

  // Duplicates, placeholder codes, mixed date orders, personal data: see sample_data/README.
  const messy = await readFile(
    new URL("../../sample_data/3_complex/field_service_jobs.csv", import.meta.url),
  );
  await page.locator('input[type="file"]').setInputFiles({
    name: "Field service jobs.csv",
    mimeType: "text/csv",
    buffer: messy,
  });
  await expect(page.getByText("Saved")).toBeVisible({ timeout: 60_000 });
  await page.getByRole("link", { name: "Field service jobs" }).click();
  await page.getByText("Manual", { exact: true }).click();
  await expect(page.getByText(/Stops so you can check/)).toBeVisible();
  await page.getByRole("button", { name: "Analyse newest version" }).click();

  // --- a manual run stops at the review gate; nothing has been changed yet
  await expect(page.getByRole("heading", { name: "Your review is needed" })).toBeVisible({
    timeout: 90_000,
  });
  await expectNoAxeViolations(page);

  // --- the plan, and on the recipe page what the model would see: masked (no email address)
  await page.getByRole("link", { name: "See the plan" }).click();
  await expect(page.getByRole("heading", { name: /^Plan for / })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.getByRole("link", { name: "Show recipe" }).first().click();
  await expect(
    page.getByRole("heading", { name: "What the planning model was shown" }),
  ).toBeVisible();
  await page.getByText("Show the description exactly as it was sent").click();
  await expect(
    page.getByRole("region", { name: "The description sent to the model" }),
  ).not.toContainText("@");
  await expectNoAxeViolations(page);
  await page.goBack();
  await page.goBack();

  // --- every change at once: keep all, apply all (one switched back by hand), then approve
  await page.getByRole("link", { name: "Review changes" }).click();
  await expect(page.getByRole("heading", { name: "Needs your decision" })).toBeVisible();
  await expectNoAxeViolations(page);
  const groups = page.getByRole("radiogroup");
  const count = await groups.count();
  expect(count).toBeGreaterThan(1);
  await expect(page.getByText(`${count} undecided, kept as is`)).toBeVisible();
  await page.getByRole("button", { name: "Keep all as is" }).click();
  await expect(page.getByText(`0 of ${count} to apply`)).toBeVisible();
  await page.getByRole("button", { name: "Apply all" }).click();
  await expect(page.getByText(`${count} of ${count} to apply`)).toBeVisible();
  await groups.nth(0).getByText("Keep as is", { exact: true }).click();
  await expect(page.getByText(`${count - 1} of ${count} to apply`)).toBeVisible();
  await page.getByRole("button", { name: "Approve and continue" }).click();

  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]{36}$/);
  await expect(page.getByText("Your report is ready")).toBeVisible({
    timeout: 90_000,
  });
  // Phase B's stages, each with its summary.
  await expect(page.getByRole("heading", { name: "Check the findings hold" })).toBeVisible();
  await expectNoAxeViolations(page);

  // --- the report: every source says how sure it is; the evidence is summarised
  await page.getByRole("link", { name: "Open the report" }).click();
  await expect(page.getByRole("heading", { name: "In short" })).toBeVisible();
  await expect(
    page.getByText(/(comparisons and links were|comparison or link was) tested/),
  ).toBeVisible();
  await expect(
    page.getByText(/^(Strong evidence|Supported|Not reliable|Descriptive)$/).first(),
  ).toBeVisible();
  await expect(page.locator("main")).not.toContainText("@"); // personal data never shown
  await expectNoAxeViolations(page);

  // --- a citation chip opens its fact in the inspector; Escape closes it
  await page
    .getByRole("button", { name: /^Source F\d+$/ })
    .first()
    .click();
  const inspector = page.getByRole("dialog");
  await expect(inspector).toContainText("Content hash");
  await expectNoAxeViolations(page);
  await page.keyboard.press("Escape");
  await expect(inspector).toBeHidden();

  // --- the dashboard: tiles, checked charts and the map of states, no personal data
  await page
    .getByRole("navigation", { name: "Views of this analysis" })
    .getByRole("link", { name: "Dashboard", exact: true })
    .click();
  await expect(page.getByRole("heading", { name: /^Dashboard: / })).toBeVisible();
  await expect(page.getByRole("img", { name: /by State/ }).first()).toBeVisible();
  await expect(page.locator("main")).not.toContainText("@");
  await expectNoAxeViolations(page);

  // --- the report downloads as PDF, Word, Markdown and one HTML file that runs nothing
  const files: Record<string, Buffer> = {};
  for (const [name, ext] of [
    [/^PDF/, "pdf"],
    [/^Word/, "docx"],
    [/^Markdown/, "md"],
    [/^HTML/, "html"],
  ] as const) {
    await page.getByText("Download", { exact: true }).click();
    const download = page.waitForEvent("download");
    await page
      .getByRole("list", { name: "Download the report as" })
      .getByRole("link", { name })
      .click();
    const file = await download;
    expect(file.suggestedFilename()).toMatch(new RegExp(`^report-[a-z0-9-]+\\.${ext}$`));
    files[ext] = await readFile((await file.path()) ?? "");
  }
  expect(files.pdf!.subarray(0, 5).toString()).toBe("%PDF-");
  expect(files.docx!.subarray(0, 2).toString()).toBe("PK");
  expect(files.md!.toString("utf8")).toMatch(/^# /);
  expect(files.md!.toString("utf8")).not.toContain("@");
  const html = files.html!.toString("utf8");
  expect(html.toLowerCase()).not.toContain("<script");
  expect(html).toContain("<svg");
});

test("explore one group of a finished analysis", async ({ page }) => {
  test.setTimeout(300_000);
  await register(page, `e2e-explore-${Date.now()}@example.com`, "E2E Explorer");
  const regions = ["North", "South", "East", "West"];
  const rows = Array.from({ length: 240 }, (_, i) => {
    const region = regions[i % 4];
    const visits = 5 + ((i * 7) % 11);
    const sales = 100 + 6 * visits + (region === "North" ? 60 : 0) + ((i * 13) % 17);
    return `O${i},${region},${i % 3 ? "Online" : "Store"},${visits},${sales}`;
  });
  const file = Buffer.from(`order_id,region,channel,visits,sales\n${rows.join("\n")}\n`);
  await page.locator('input[type="file"]').setInputFiles({
    name: "Regional sales.csv",
    mimeType: "text/csv",
    buffer: file,
  });
  await expect(page.getByText("Saved")).toBeVisible({ timeout: 60_000 });
  await page.getByRole("link", { name: "Regional sales" }).click();
  await page.getByRole("button", { name: "Analyse newest version" }).click();
  await expect(page.getByText("Your report is ready")).toBeVisible({ timeout: 90_000 });

  // --- one group explored on its own (an exploratory sub-run), then its dashboard
  await page.getByRole("link", { name: "Open the report" }).click();
  await expect(page.getByRole("heading", { name: "In short" })).toBeVisible();
  await page.getByText("Show the numbers and explore a group").first().click();
  await page
    .getByRole("button", { name: /^Explore .+ on its own$/ })
    .first()
    .click();
  const confirm = page.getByRole("dialog");
  await expect(confirm).toContainText("marked exploratory");
  await expectNoAxeViolations(page);
  await confirm.getByRole("button", { name: "Start exploring" }).click();
  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]{36}$/);
  const explorationUrl = page.url();
  await expect(page.getByText(/^Exploring a subset/)).toBeVisible();
  await expect(page.getByText("Your report is ready")).toBeVisible({ timeout: 90_000 });
  await page.getByRole("link", { name: "Open the dashboard" }).click();
  await expect(page.getByText("Exploratory analysis of a subset")).toBeVisible();
  await expectNoAxeViolations(page);
  await page.getByRole("link", { name: "Open the full analysis" }).click();
  await expect(page.getByRole("heading", { name: "Explorations of this analysis" })).toBeVisible();

  // The runs list says which run explores what.
  await page.getByRole("link", { name: "Runs", exact: true }).click();
  await expect(page.getByRole("row", { name: /Exploring region/ })).toHaveCount(1);

  // --- deleting: the exploration from its page, then the whole dataset. Gone at once.
  await page.goto(explorationUrl);
  await page.getByRole("button", { name: "Delete analysis…" }).click();
  const sure = page.getByRole("group", { name: "Confirm deleting this analysis" });
  await expect(sure).toContainText("cannot be undone");
  await expect(sure.getByRole("button", { name: "Keep it" })).toBeFocused();
  await expectNoAxeViolations(page);
  await sure.getByRole("button", { name: "Delete analysis" }).click();
  await expect(page).toHaveURL(/\/datasets\/[0-9a-f-]{36}$/);
  const datasetUrl = page.url();
  await page.goto(explorationUrl);
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  await page.goto(datasetUrl.replace(/\/datasets\/[^/]+$/, "/runs"));
  await expect(page.getByRole("row", { name: /Exploring region/ })).toHaveCount(0);
  await expect(page.getByRole("row", { name: /Regional sales/ })).toHaveCount(1);

  await page.goto(datasetUrl);
  await page.getByRole("button", { name: "Delete dataset…" }).click();
  await page
    .getByRole("group", { name: "Confirm deleting this dataset" })
    .getByRole("button", { name: "Delete dataset" })
    .click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  await expect(page.getByText("No datasets yet")).toBeVisible();
  await page.getByRole("link", { name: "Runs", exact: true }).click();
  await expect(page.getByRole("row", { name: /Regional sales/ })).toHaveCount(0);
});
