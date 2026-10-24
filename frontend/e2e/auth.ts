import { expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

/**
 * Registration as people do it (ADR-042): name and email, the 6-digit code from the email, then a
 * password. `scripts/e2e.sh` runs the console transport, so codes and links are read from the web
 * server's log.
 */
export const WEB_LOG = process.env.E2E_WEB_LOG ?? "";
/** Meets the password policy (10+ characters, both cases, a number, a symbol, no spaces). */
export const STRONG_PASSWORD = "Long-enough-Pass1";

const escape = (value: string) => value.replace(/[.+*?^$()[\]{}|\\]/g, "\\$&");

/** Every code printed for `email` so far, oldest first. */
export async function codesFor(email: string): Promise<string[]> {
  const pattern = new RegExp(`Verification code for ${escape(email)}\\s+(\\d{6})`, "g");
  return [...(await readFile(WEB_LOG, "utf8")).matchAll(pattern)].map((match) => match[1]!);
}

/** The newest code for `email`, once there are more than `seen` of them. */
export async function nextCode(email: string, seen = 0): Promise<string> {
  let codes: string[] = [];
  await expect
    .poll(
      async () => {
        codes = await codesFor(email);
        return codes.length;
      },
      { timeout: 15_000 },
    )
    .toBeGreaterThan(seen);
  return codes.at(-1)!;
}

/** The sign-in link printed for `email`, once it appears in the web server's log. */
export async function linkFor(email: string): Promise<string> {
  const pattern = new RegExp(`Sign-in link for ${escape(email)}\\s+(\\S+)`);
  let link = "";
  await expect
    .poll(
      async () => {
        link = (await readFile(WEB_LOG, "utf8")).match(pattern)?.[1] ?? "";
        return link;
      },
      { timeout: 15_000 },
    )
    .not.toBe("");
  return link;
}

/** Name and email on the register page, up to the code step. */
export async function startRegistration(page: Page, email: string, name = "E2E Person") {
  await page.goto("/sign-in?mode=register");
  await page.getByLabel("Name").fill(name);
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Send me a code" }).click();
  await expect(page.getByLabel("6-digit code")).toBeVisible();
}

/** A whole registration: code, password, into the new (empty) workspace. */
export async function register(
  page: Page,
  email: string,
  name = "E2E Person",
  password = STRONG_PASSWORD,
) {
  await startRegistration(page, email, name);
  await page.getByLabel("6-digit code").fill(await nextCode(email));
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page).toHaveURL(/\/set-password$/);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByLabel("Confirm password").fill(password);
  await page.getByRole("button", { name: "Save password and continue" }).click();
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
}

export async function signIn(page: Page, email: string, password: string) {
  await page.goto("/sign-in");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
}
