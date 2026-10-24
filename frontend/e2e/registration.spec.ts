import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { Client } from "pg";
import {
  codesFor,
  linkFor,
  nextCode,
  signIn,
  startRegistration,
  STRONG_PASSWORD,
  WEB_LOG,
} from "./auth";

/**
 * Registration and sign-in rules (ADR-042) in a real browser: register by email with a code read
 * from the web log, choose a password (a weak one is refused), sign out and back in; a sign-in
 * link goes only to an existing account; wrong, used and expired codes are refused.
 */
const AUTH_DB = process.env.E2E_AUTH_DATABASE_URL ?? "";

function problemAlert(page: Page) {
  return page.locator('[role="alert"]:not(#__next-route-announcer__)');
}

/** Pretend ten minutes passed: the stored code for `email` expires. */
async function expireCode(email: string) {
  const client = new Client({ connectionString: AUTH_DB, options: "-c search_path=auth" });
  await client.connect();
  try {
    await client.query(
      `UPDATE verification SET "expiresAt" = now() - interval '1 minute' WHERE identifier = $1`,
      [`email-verification-otp-${email}`],
    );
  } finally {
    await client.end();
  }
}

test("register by email: code, password, sign out, sign back in", async ({ page }) => {
  test.skip(!WEB_LOG, "E2E_WEB_LOG is not set for this run");
  const email = `e2e-register-${Date.now()}@example.com`;

  await startRegistration(page, email, "E2E Registrant");
  await expect(page.getByRole("status")).toContainText("Check the server terminal");
  const code = await nextCode(email);

  // --- a wrong code is refused, and the step stays open
  await page.getByLabel("6-digit code").fill(code === "000000" ? "111111" : "000000");
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(problemAlert(page)).toContainText("That code isn’t right");

  // --- the right one verifies the address and asks for a password
  await page.getByLabel("6-digit code").fill(code);
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page).toHaveURL(/\/set-password$/);
  await expect(page.getByRole("heading", { name: "Create a password" })).toBeVisible();

  // --- the app itself waits until a password is set
  await page.goto("/");
  await expect(page).toHaveURL(/\/set-password$/);

  // --- a used code is refused
  // (signed in now, so Better Auth wants the page's Origin on the request)
  const reused = await page.request.post("/api/auth/email-otp/verify-email", {
    data: { email, otp: code },
    headers: { origin: new URL(page.url()).origin },
  });
  expect(reused.status()).toBe(400);
  expect((await reused.json()).code).toBe("INVALID_OTP");

  // --- a weak password is refused: the checklist shows why and the button stays disabled
  const save = page.getByRole("button", { name: "Save password and continue" });
  await page.getByLabel("Password", { exact: true }).fill("weakpassword");
  await page.getByLabel("Confirm password").fill("weakpassword");
  await expect(save).toBeDisabled();
  await expect(page.getByRole("list", { name: "Password rules" })).toContainText(
    "An uppercase letter: not yet",
  );

  // --- a strong one is saved, and the app opens
  await page.getByLabel("Password", { exact: true }).fill(STRONG_PASSWORD);
  await page.getByLabel("Confirm password").fill(STRONG_PASSWORD);
  await save.click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  await expect(page.getByText("No datasets yet")).toBeVisible();
  await page.goto("/set-password");
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/); // has a password: sent home

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/sign-in$/);
  await page.goto("/set-password");
  await expect(page).toHaveURL(/\/sign-in$/); // signed out: sent to sign in

  // --- the same address can't register again
  await page.goto("/sign-in?mode=register");
  await page.getByLabel("Name").fill("Someone Else");
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Send me a code" }).click();
  await expect(problemAlert(page)).toContainText(
    "That email is already registered. Sign in instead.",
  );

  // --- sign in with the password
  await signIn(page, email, STRONG_PASSWORD);
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/sign-in$/);

  // --- a sign-in link: never to an unknown address, and to the account's address
  const stranger = `e2e-stranger-${Date.now()}@example.com`;
  await page.getByRole("link", { name: "Email me a sign-in link" }).click();
  await expect(page).toHaveURL(/\/sign-in\/email$/);
  await page.getByLabel("Email address").fill(stranger);
  await page.getByRole("button", { name: "Send sign-in link" }).click();
  await expect(problemAlert(page)).toContainText(
    "No account found for that email. Register first.",
  );
  await expect(page.getByRole("link", { name: "Register" })).toHaveAttribute(
    "href",
    "/sign-in?mode=register",
  );
  expect(await readFile(WEB_LOG, "utf8")).not.toContain(stranger);

  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Send sign-in link" }).click();
  await expect(page.getByRole("status")).toContainText("Check the server terminal");
  await page.goto(await linkFor(email));
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
});

test("an expired code is refused, and a new one works", async ({ page }) => {
  test.skip(!WEB_LOG || !AUTH_DB, "E2E_WEB_LOG or E2E_AUTH_DATABASE_URL is not set");
  test.setTimeout(150_000); // waits out the one-minute resend cooldown
  const email = `e2e-expired-${Date.now()}@example.com`;
  await startRegistration(page, email);
  const first = await nextCode(email);
  await expireCode(email);
  await page.getByLabel("6-digit code").fill(first);
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(problemAlert(page)).toContainText("That code has expired");

  // A new code can be asked for once the cooldown is over (one a minute per address).
  const resend = page.getByRole("button", { name: /^Send a new code/ });
  await expect(resend).toBeEnabled({ timeout: 70_000 });
  const seen = (await codesFor(email)).length;
  await resend.click();
  const second = await nextCode(email, seen);
  await page.getByLabel("6-digit code").fill(second);
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page).toHaveURL(/\/set-password$/);
});
