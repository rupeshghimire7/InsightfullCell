import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

/**
 * The Phase 1 journey in a real browser: sign in with a magic link, upload a multi-part file
 * (Web Worker SHA-256, CORS, presigned PUTs), see it listed, upload it again, sign out.
 * The sign-in link is read from the web server's log (EMAIL_TRANSPORT=console).
 */

const WEB_LOG = process.env.E2E_WEB_LOG;
const EMAIL = `e2e-${Date.now()}@example.com`;

async function signInLink(email: string): Promise<string> {
  if (!WEB_LOG) throw new Error("Set E2E_WEB_LOG to the web server's log file");
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const log = await readFile(WEB_LOG, "utf8").catch(() => "");
    const lines = log.split("\n");
    const index = lines.findLastIndex((line) => line.includes(`Sign-in link for ${email}`));
    const url = index >= 0 ? lines[index + 1]?.trim() : undefined;
    if (url?.startsWith("http")) return url;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`No sign-in link for ${email} appeared in ${WEB_LOG}`);
}

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
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag22aa"])
    .analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
}

test("sign in, upload, deduplicate, sign out", async ({ page, browser }) => {
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

  await page.getByLabel("Email address").fill("not-an-email");
  await page.getByRole("button", { name: "Email me a sign-in link" }).click();
  await expect(page.getByText("Enter your email address")).toBeVisible();
  await expect(page.getByLabel("Email address")).toBeFocused();

  // --- magic link
  await page.getByLabel("Email address").fill(EMAIL);
  await page.getByRole("button", { name: "Email me a sign-in link" }).click();
  // Testing mode asks for the tester's own Resend key, with the testing notice. Left empty, the
  // link goes to the server log (EMAIL_TRANSPORT=console).
  await expect(page.getByText("Notice: this is just for the testing version.")).toBeVisible();
  await expect(page.getByText(/A registered domain is needed for sending email/)).toBeVisible();
  await expect(page.getByLabel("Your Resend API key")).toBeFocused();
  await expectNoAxeViolations(page);
  await page.getByRole("button", { name: "Send sign-in link" }).click();
  // EMAIL_TRANSPORT=console: the page says the link is in the server log, not an inbox.
  await expect(page.getByRole("heading", { name: "Check the server terminal" })).toBeVisible();
  await page.goto(await signInLink(EMAIL));

  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("heading", { name: "Home", level: 1 })).toBeVisible();
  await expect(page.getByText("No datasets yet")).toBeVisible();
  await expectNoAxeViolations(page);

  // --- a link is single-use
  const stranger = await browser.newContext(); // no session cookie
  const reuse = await stranger.newPage();
  await reuse.goto(await signInLink(EMAIL));
  await expect(reuse).toHaveURL(/\/sign-in/);
  await expect(problemAlert(reuse)).toContainText(/already used|isn.t valid|expired/);
  await stranger.close();

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
  await page.getByRole("button", { name: "Analyse newest version" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}\/runs\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("heading", { name: "Analysis of Home sales 2026" })).toBeVisible();
  await expect(page.getByText("Your data is profiled, planned and ready to clean")).toBeVisible({
    timeout: 90_000,
  });
  await expect(page.getByTestId("connection")).toHaveCount(0); // finished: nothing to follow
  // Rows read (ingest) and complete rows (profile): both are every row of the file.
  await expect(page.getByText("300,000")).toHaveCount(2);
  await page.getByText(/Show the 4 columns/).click();
  await expect(page.getByText("suburb")).toBeVisible();
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

  // --- the run is listed, and the dataset shows its latest analysis
  await page.getByRole("link", { name: "Runs", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Runs", level: 1 })).toBeVisible();
  await expect(page.getByRole("row", { name: /Home sales 2026.*Succeeded/ })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.getByRole("link", { name: "Home", exact: true }).click();
  await expect(page.getByRole("row", { name: /Home sales 2026.*Succeeded/ })).toBeVisible();

  // --- sign out returns to sign-in and protects the app again
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/sign-in$/);
  await page.goto("/");
  await expect(page).toHaveURL(/\/sign-in$/);

  expect(policyViolations).toEqual([]);
  expect(consoleErrors).toEqual([]);
});

test("another user cannot see the first user's datasets", async ({ page }) => {
  const other = `e2e-other-${Date.now()}@example.com`;
  await page.goto("/sign-in");
  await page.getByLabel("Email address").fill(other);
  await page.getByRole("button", { name: "Email me a sign-in link" }).click();
  await page.getByRole("button", { name: "Send sign-in link" }).click();
  await expect(page.getByRole("heading", { name: "Check the server terminal" })).toBeVisible();
  await page.goto(await signInLink(other));
  await expect(page.getByText("No datasets yet")).toBeVisible();

  const response = await page.request.get("/api/backend/v1/datasets");
  expect(response.status()).toBe(200);
  expect((await response.json()).items).toEqual([]);

  // the proxy exposes only the allow-listed API surface
  expect((await page.request.get("/api/backend/readyz")).status()).toBe(404);
  expect((await page.request.get("/api/backend/docs")).status()).toBe(404);
});

test("register with a password, sign out, sign back in", async ({ page }) => {
  const email = `e2e-password-${Date.now()}@example.com`;
  const password = "a long enough passphrase";
  await page.goto("/sign-in");
  await page.getByRole("link", { name: "Create an account" }).click();
  await expect(page.getByRole("heading", { name: "Create an account", level: 1 })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.getByLabel("Name").fill("E2E Person");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  await expect(page.getByText("No datasets yet")).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/sign-in$/);
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill("not the password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(problemAlert(page)).toContainText("email and password don’t match");
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
});

test("a messy file pauses for review, and approving continues the analysis", async ({ page }) => {
  test.setTimeout(240_000);
  const email = `e2e-review-${Date.now()}@example.com`;
  await page.goto("/sign-in?mode=register");
  await page.getByLabel("Name").fill("E2E Reviewer");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password").fill("a long enough passphrase");
  await page.getByRole("button", { name: "Create account" }).click();
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
  await page.getByRole("button", { name: "Analyse newest version" }).click();

  // --- the run stops at the review gate; nothing has been changed yet
  await expect(page.getByRole("heading", { name: "Your review is needed" })).toBeVisible({
    timeout: 90_000,
  });
  await expectNoAxeViolations(page);

  // --- the plan: what the model would see is masked (no email address appears)
  await page.getByRole("link", { name: "See the plan" }).click();
  await expect(page.getByRole("heading", { name: "What the model saw" })).toBeVisible();
  await page.getByText("Show the description exactly as it was sent").click();
  await expect(page.locator("pre")).not.toContainText("@");
  await expectNoAxeViolations(page);
  await page.goBack();

  // --- approving is refused until every change is decided, then continues the run
  await page.getByRole("link", { name: "Review changes" }).click();
  await expect(page.getByRole("heading", { name: "Needs your decision" })).toBeVisible();
  await expectNoAxeViolations(page);
  await page.getByRole("button", { name: "Approve and continue" }).click();
  await expect(page.getByText("Decide every change first.")).toBeVisible();
  const groups = page.getByRole("radiogroup");
  const count = await groups.count();
  expect(count).toBeGreaterThan(0);
  for (let i = 0; i < count; i += 1) {
    await groups.nth(i).getByText("Apply", { exact: true }).click();
  }
  await expect(page.getByText(`${count} of ${count} decided`)).toBeVisible();
  await page.getByRole("button", { name: "Approve and continue" }).click();

  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]{36}$/);
  await expect(page.getByText("Your data is profiled, planned and ready to clean")).toBeVisible({
    timeout: 90_000,
  });
  await expectNoAxeViolations(page);
});
