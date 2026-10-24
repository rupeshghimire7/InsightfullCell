import { expect, test } from "@playwright/test";
import { Client } from "pg";
import { register, signIn, STRONG_PASSWORD, WEB_LOG } from "./auth";

/**
 * Profile and "Delete my account" (ADR-043) in a real browser: the name changes, the email
 * cannot; deleting needs the password, removes the datasets and the sign-in account, and the
 * address can register again into a new, empty workspace.
 */
const AUTH_DB = process.env.E2E_AUTH_DATABASE_URL ?? "";

async function authRows(email: string) {
  const client = new Client({ connectionString: AUTH_DB, options: "-c search_path=auth" });
  await client.connect();
  try {
    const users = await client.query(`SELECT id FROM "user" WHERE email = $1`, [email]);
    const codes = await client.query(
      `SELECT count(*)::int AS n FROM verification WHERE right(identifier, length($1)) = $1`,
      [email],
    );
    const workspaces = await client.query(
      `SELECT count(*)::int AS n FROM organization WHERE name = $1`,
      [`${email.split("@")[0]}’s workspace`],
    );
    return {
      users: users.rowCount,
      codes: codes.rows[0].n as number,
      workspaces: workspaces.rows[0].n as number,
    };
  } finally {
    await client.end();
  }
}

test("edit the name, then delete the account and everything in it", async ({ page }) => {
  test.skip(!WEB_LOG || !AUTH_DB, "E2E_WEB_LOG or E2E_AUTH_DATABASE_URL is not set");
  const email = `e2e-leaver-${Date.now()}@example.com`;
  await register(page, email, "E2E Leaver");

  // --- a dataset to delete with the account
  await page.locator('input[type="file"]').setInputFiles({
    name: "to-delete.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("id,price\n1,10\n2,20\n3,30\n"),
  });
  await expect(page.getByText("Saved")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("row", { name: /to-delete/ })).toBeVisible();

  // --- profile: the name changes, the email is read-only
  await page.locator(`summary[aria-label="Menu"]`).click();
  await page.getByRole("link", { name: "Profile" }).click();
  await expect(page.getByLabel("Email address")).toHaveValue(email);
  await expect(page.getByLabel("Email address")).toHaveAttribute("readonly", "");
  await page.getByLabel("Name").fill("E2E Renamed");
  await page.getByRole("button", { name: "Save name" }).click();
  await expect(page.getByText("Saved.")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Name")).toHaveValue("E2E Renamed");

  // --- Data & privacy, from the menu
  await page.locator(`summary[aria-label="Menu"]`).click();
  await page.getByRole("link", { name: "Data & privacy" }).click();
  await expect(page.getByRole("heading", { name: "Data & privacy", level: 1 })).toBeVisible();
  await expect(page.getByText("Raw rows never reach an AI model")).toBeVisible();
  await page.getByRole("button", { name: "Delete my account…" }).click();
  await page.getByLabel("Password", { exact: true }).fill("not the password");
  await page.getByRole("button", { name: "Delete my account permanently" }).click();
  await expect(page.getByLabel("Password", { exact: true })).toHaveAccessibleDescription(
    "That password isn’t right.",
  );
  expect((await authRows(email)).users).toBe(1); // nothing deleted yet

  await page.getByLabel("Password", { exact: true }).fill(STRONG_PASSWORD);
  await page.getByRole("button", { name: "Delete my account permanently" }).click();
  await expect(page.getByRole("status").first()).toContainText("Your account and everything");
  // Shown once: the flag leaves the address, so a reload shows nothing.
  await expect(page).toHaveURL(/\/sign-in$/);
  await page.reload();
  await expect(page.getByText("Your account and everything")).toHaveCount(0);
  expect(await authRows(email)).toEqual({ users: 0, codes: 0, workspaces: 0 });

  // --- signed out for good, and the address is free again
  await page.goto("/");
  await expect(page).toHaveURL(/\/sign-in$/);
  await signIn(page, email, STRONG_PASSWORD);
  await expect(page.locator('[role="alert"]:not(#__next-route-announcer__)')).toContainText(
    "email and password don’t match",
  );
  await register(page, email, "E2E Returning");
  await expect(page.getByText("No datasets yet")).toBeVisible();
});
