import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * The admin dashboard in a real browser: locked behind the key (nothing rendered behind the
 * dialog), a wrong key refused, the right one unlocking it with an HttpOnly cookie, and Lock.
 * `scripts/e2e.sh` gives the API and the web app the same throwaway ADMIN_DASHBOARD_KEY.
 */
const KEY = process.env.ADMIN_DASHBOARD_KEY ?? "";

test("the admin dashboard opens only with the key", async ({ page, context }) => {
  test.skip(!KEY, "ADMIN_DASHBOARD_KEY is not set for this run");
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error" && !/Failed to load resource/.test(message.text())) {
      errors.push(message.text());
    }
  });
  page.on("pageerror", (error) => errors.push(error.message));

  await page.goto("/dashboard");
  const dialog = page.getByRole("dialog", { name: "Please enter admin key for dashboard access." });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Admin key")).toBeFocused();
  // Locked: no figures on the page, not even hidden behind the dialog.
  await expect(page.getByText("Model spend per day")).toHaveCount(0);
  expect(await page.content()).not.toContain(KEY);
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(axe.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);

  await dialog.getByLabel("Admin key").fill("not the key");
  await dialog.getByRole("button", { name: "Unlock dashboard" }).click();
  await expect(dialog.getByRole("alert")).toContainText("isn’t right");

  await dialog.getByLabel("Admin key").fill(KEY);
  await dialog.getByRole("button", { name: "Unlock dashboard" }).click();
  await expect(page.getByRole("heading", { name: "Admin dashboard", level: 1 })).toBeVisible();
  await expect(page.getByRole("meter", { name: "Model spend against the cap" })).toBeVisible();
  // The people list: a table, or (when this runs before anyone has registered) its empty note.
  await expect(page.getByRole("heading", { name: "People", level: 2 })).toBeVisible();
  await expect(
    page
      .getByRole("table", { name: "People and their usage" })
      .or(page.getByText("Nobody has signed up yet.")),
  ).toBeVisible();

  const cookie = (await context.cookies()).find((c) => c.name.endsWith("ic_admin"));
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.sameSite).toBe("Strict");
  expect(cookie?.value).not.toContain(KEY);
  expect(await page.content()).not.toContain(KEY);

  // The data endpoint is not reachable through the web app's proxy.
  const proxied = await page.request.get("/api/backend/v1/admin/overview", {
    headers: { "X-Admin-Key": KEY },
  });
  expect(proxied.status()).toBe(404);

  await page.getByRole("button", { name: "Lock" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  expect(errors).toEqual([]);
});

test("the email-link page explains itself while links are off", async ({ page }) => {
  await page.goto("/sign-in/email");
  await expect(page.getByRole("heading", { name: "Email me a sign-in link" })).toBeVisible();
  await expect(page.getByText("Sign-in links aren’t available yet.")).toBeVisible();
  await page.getByRole("link", { name: "Back to sign in" }).click();
  await expect(page).toHaveURL(/\/sign-in$/);
});
