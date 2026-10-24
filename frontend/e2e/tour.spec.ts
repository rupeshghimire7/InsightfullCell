import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { nextCode, STRONG_PASSWORD, WEB_LOG } from "./auth";

/**
 * A tour of every page, for the test-completion report (`reports/test_completion/`). Runs only
 * when E2E_SCREENSHOTS_DIR is set (`scripts/e2e.sh` with it exported). On each page: a
 * screenshot (desktop, phone, dark for some), axe (WCAG 2.2 AA), no sideways scrolling at 390 px,
 * load time, and console errors. Then security headers, cookie flags and the sign-in rate limit.
 * Findings are soft assertions, so the report lists every one; they still fail the test.
 */
const OUT = process.env.E2E_SCREENSHOTS_DIR ?? "";
const KEY = process.env.ADMIN_DASHBOARD_KEY ?? "";

interface PageResult {
  name: string;
  path: string;
  status: number | null;
  loadMs: number | null;
  axe: string[];
  phoneOverflow: boolean;
  screenshots: string[];
}
const results = {
  startedAt: new Date().toISOString(),
  pages: [] as PageResult[],
  consoleErrors: [] as string[],
  security: {} as Record<string, unknown>,
  flows: [] as { name: string; ok: boolean; detail?: string }[],
};

async function settle(page: Page) {
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await page.waitForFunction(() =>
    document.getAnimations().every((a) => a.playState !== "running"),
  );
}

async function shot(page: Page, file: string, fullPage = true): Promise<string> {
  const name = `${file}.png`;
  await page.screenshot({ path: path.join(OUT, "screenshots", name), fullPage });
  return `screenshots/${name}`;
}

/** Visit a page: load it, then check it at desktop and phone width (and dark, if asked). */
async function visit(
  page: Page,
  name: string,
  url: string,
  options: { dark?: boolean; check?: (page: Page) => Promise<void> } = {},
): Promise<PageResult> {
  const file = name.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  const response = await page.goto(url);
  await settle(page);
  if (options.check) await options.check(page);
  const loadMs = await page.evaluate(() => {
    const nav = performance.getEntriesByType("navigation")[0] as
      PerformanceNavigationTiming | undefined;
    return nav ? Math.round(nav.loadEventEnd - nav.startTime) : null;
  });
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
  const violations = axe.violations.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`);
  expect.soft(violations, `axe on ${name}`).toEqual([]);
  const screenshots = [await shot(page, `${file}-desktop`)];

  await page.setViewportSize({ width: 390, height: 844 });
  await settle(page);
  const phoneOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  );
  expect.soft(phoneOverflow, `sideways scrolling at 390 px on ${name}`).toBe(false);
  screenshots.push(await shot(page, `${file}-phone`));
  await page.setViewportSize({ width: 1280, height: 800 });

  if (options.dark) {
    await page.emulateMedia({ colorScheme: "dark" });
    await settle(page);
    const darkAxe = await new AxeBuilder({ page }).withTags(["wcag2aa"]).analyze();
    expect
      .soft(
        darkAxe.violations.map((v) => v.id),
        `axe (dark) on ${name}`,
      )
      .toEqual([]);
    screenshots.push(await shot(page, `${file}-dark`));
    await page.emulateMedia({ colorScheme: "light" });
  }
  const result: PageResult = {
    name,
    path: new URL(page.url()).pathname,
    status: response?.status() ?? null,
    loadMs,
    axe: violations,
    phoneOverflow,
    screenshots,
  };
  results.pages.push(result);
  return result;
}

async function flow(name: string, run: () => Promise<void>) {
  try {
    await run();
    results.flows.push({ name, ok: true });
  } catch (error) {
    results.flows.push({ name, ok: false, detail: String(error).slice(0, 300) });
    throw error;
  }
}

test.describe.configure({ mode: "serial" });

test("tour: every page, accessibility, phone width, security", async ({ page, context }) => {
  test.skip(!OUT || !WEB_LOG, "E2E_SCREENSHOTS_DIR or E2E_WEB_LOG is not set");
  test.setTimeout(15 * 60_000);
  await mkdir(path.join(OUT, "screenshots"), { recursive: true });
  await page.setViewportSize({ width: 1280, height: 800 });
  page.on("console", (message) => {
    const text = message.text();
    if (message.type() === "error" && !/Failed to load resource/.test(text)) {
      results.consoleErrors.push(`${page.url()}: ${text.slice(0, 200)}`);
    }
  });
  page.on("pageerror", (error) => results.consoleErrors.push(`${page.url()}: ${error.message}`));

  // ------------------------------------------------------------------ signed out
  const signIn = await page.goto("/sign-in");
  const headers = signIn?.headers() ?? {};
  results.security.headers = {
    "content-security-policy": headers["content-security-policy"] ?? null,
    "x-content-type-options": headers["x-content-type-options"] ?? null,
    "referrer-policy": headers["referrer-policy"] ?? null,
    "x-frame-options": headers["x-frame-options"] ?? null,
    "permissions-policy": headers["permissions-policy"] ?? null,
    "strict-transport-security": headers["strict-transport-security"] ?? null,
  };
  const csp = headers["content-security-policy"] ?? "";
  expect.soft(csp, "CSP with a per-request nonce").toMatch(/script-src[^;]*'nonce-/);
  // ('wasm-unsafe-eval' is allowed: the upload hashes files in a Web Worker with WebAssembly.)
  expect.soft(csp, "no 'unsafe-eval' in production").not.toContain("'unsafe-eval'");
  expect.soft(csp, "no framing").toContain("frame-ancestors 'none'");

  await visit(page, "Sign in", "/sign-in", { dark: true });
  await visit(page, "Register", "/sign-in?mode=register", { dark: true });
  await visit(page, "Email me a sign-in link", "/sign-in/email");
  await visit(page, "Forgot password", "/sign-in/forgot");
  await visit(page, "Not found", "/no-such-page");
  const redirect = await page.goto("/o/00000000-0000-4000-8000-000000000000");
  results.security.signedOutRedirect = new URL(page.url()).pathname;
  expect
    .soft(new URL(page.url()).pathname, "signed out: the app redirects to sign-in")
    .toBe("/sign-in");
  void redirect;

  // ------------------------------------------------------------------ registration
  const email = `e2e-tour-${Date.now()}@example.com`;
  await flow("Register by emailed code, then a password", async () => {
    await page.goto("/sign-in?mode=register");
    await page.getByLabel("Name").fill("Tour Person");
    await page.getByLabel("Email address").fill(email);
    await page.getByRole("button", { name: "Send me a code" }).click();
    await expect(page.getByLabel("6-digit code")).toBeVisible();
    await settle(page);
    results.pages.push({
      name: "Registration code step",
      path: "/sign-in?mode=register",
      status: 200,
      loadMs: null,
      axe: [],
      phoneOverflow: false,
      screenshots: [await shot(page, "registration-code-step")],
    });
    await page.getByLabel("6-digit code").fill(await nextCode(email));
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page).toHaveURL(/\/set-password$/);
    await page.getByLabel("Password", { exact: true }).fill("weakpass");
    await settle(page);
    await visit(page, "Create a password (weak, button disabled)", "/set-password", {
      check: async (p) => {
        await p.getByLabel("Password", { exact: true }).fill("weakpass");
        await expect(p.getByRole("button", { name: "Save password and continue" })).toBeDisabled();
      },
    });
    await page.getByLabel("Password", { exact: true }).fill(STRONG_PASSWORD);
    await page.getByLabel("Confirm password").fill(STRONG_PASSWORD);
    await page.getByRole("button", { name: "Save password and continue" }).click();
    await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  });
  const home = new URL(page.url()).pathname;

  const cookies = await context.cookies();
  const session = cookies.find((c) => c.name.endsWith("session_token"));
  results.security.sessionCookie = session
    ? { httpOnly: session.httpOnly, sameSite: session.sameSite, secure: session.secure }
    : null;
  expect.soft(session?.httpOnly, "session cookie is HttpOnly").toBe(true);
  expect.soft(session?.sameSite, "session cookie is SameSite=Lax").toBe("Lax");
  results.security.apiTokenRoute = (await page.request.get("/api/auth/token")).status();
  results.security.passwordSignUpRoute = (
    await page.request.post("/api/auth/sign-up/email", {
      data: { name: "x", email: "x@example.com", password: STRONG_PASSWORD },
    })
  ).status();

  // ------------------------------------------------------------------ signed in
  await visit(page, "Home (empty)", home, { dark: true });
  let runUrl = "";
  await flow("Upload a file, analyse it, open the report and dashboard", async () => {
    const csv = await readFile(
      new URL("../../sample_data/1_simple/store_performance_2025.csv", import.meta.url),
    );
    await page.locator('input[type="file"]').setInputFiles({
      name: "store_performance_2025.csv",
      mimeType: "text/csv",
      buffer: csv,
    });
    await expect(page.getByText("Saved")).toBeVisible({ timeout: 60_000 });
    await page.getByRole("link", { name: "store_performance_2025" }).first().click();
    await expect(page.getByRole("button", { name: "Analyse newest version" })).toBeVisible();
    const datasetPath = new URL(page.url()).pathname;
    await page.getByRole("button", { name: "Analyse newest version" }).click();
    await expect(page).toHaveURL(/\/runs\/[0-9a-f-]{36}$/);
    runUrl = new URL(page.url()).pathname;
    await settle(page);
    results.pages.push({
      name: "Analysis while running",
      path: runUrl,
      status: 200,
      loadMs: null,
      axe: [],
      phoneOverflow: false,
      screenshots: [await shot(page, "analysis-running")],
    });
    await expect(page.getByText("Your report is ready")).toBeVisible({ timeout: 180_000 });
    await visit(page, "Analysis (finished)", runUrl);
    await visit(page, "Report", `${runUrl}/report`, { dark: true });
    await visit(page, "Dashboard", `${runUrl}/dashboard`, { dark: true });
    await visit(page, "Analysis plan", `${runUrl}/plan`);
    await visit(page, "Recipe", `${runUrl}/recipe`);
    await visit(page, "Dataset", datasetPath);
  });
  await visit(page, "Home (with a dataset)", home);
  await visit(page, "Runs", `${home}/runs`, {
    check: async (p) => {
      await expect(p.getByRole("columnheader", { name: "Progress" })).toBeVisible();
      await expect(p.getByText("All steps done").first()).toBeVisible();
    },
  });
  await visit(page, "Profile", `${home}/profile`);
  await visit(page, "Data & privacy", `${home}/privacy`, { dark: true });
  await visit(page, "About InsightfulCell", `${home}/about`);
  await visit(page, "Contact", `${home}/contact`, {
    check: async (p) => {
      await expect(p.getByRole("heading", { name: "Sandesh Paudel" })).toBeVisible();
      await expect(p.getByRole("heading", { name: "Rupesh Ghimire" })).toBeVisible();
    },
  });
  await visit(page, "License", `${home}/license`);

  // ------------------------------------------------------------------ admin dashboard
  if (KEY) {
    await flow("Admin dashboard asks for the key on every visit", async () => {
      await visit(page, "Admin dashboard (locked)", "/adminboard");
      await page.getByLabel("Admin key", { exact: true }).fill(KEY);
      await page.getByRole("button", { name: "Unlock dashboard" }).click();
      await expect(page.getByRole("heading", { name: "Admin dashboard", level: 1 })).toBeVisible();
      await settle(page);
      const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
      const violations = axe.violations.map((v) => `${v.id}: ${v.help}`);
      expect.soft(violations, "axe on the unlocked dashboard").toEqual([]);
      results.pages.push({
        name: "Admin dashboard (unlocked)",
        path: "/adminboard",
        status: 200,
        loadMs: null,
        axe: violations,
        phoneOverflow: false,
        screenshots: [await shot(page, "admin-dashboard-unlocked")],
      });
      expect((await context.cookies()).some((c) => /admin/.test(c.name))).toBe(false);
      await page.reload();
      await expect(page.getByRole("dialog")).toBeVisible();
    });
  }

  // ------------------------------------------------------------------ sign out, then limits
  await flow("Sign out ends the session at once", async () => {
    const before = await context.cookies();
    await page.goto(home);
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/sign-in$/);
    const replay = await page.context().browser()!.newContext({ baseURL: page.url() });
    await replay.addCookies(before);
    results.security.replayedCookiesAfterSignOut = (
      await replay.request.get("/api/backend/v1/me")
    ).status();
    await replay.close();
    expect(results.security.replayedCookiesAfterSignOut).toBe(401);
  });

  await flow("Repeated wrong passwords are rate-limited", async () => {
    const statuses: number[] = [];
    for (let i = 0; i < 6; i++) {
      statuses.push(
        (
          await page.request.post("/api/auth/sign-in/email", {
            data: { email, password: "wrong password" },
            headers: { origin: new URL(page.url()).origin },
          })
        ).status(),
      );
    }
    results.security.wrongPasswordStatuses = statuses;
    expect(statuses).toContain(429);
  });

  expect.soft(results.consoleErrors, "console errors").toEqual([]);
});

test.afterAll(async () => {
  if (!OUT) return;
  await writeFile(
    path.join(OUT, "tour-results.json"),
    JSON.stringify({ ...results, finishedAt: new Date().toISOString() }, null, 2),
  );
});
