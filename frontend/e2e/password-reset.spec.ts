import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { register, signIn, STRONG_PASSWORD, WEB_LOG } from "./auth";

/**
 * "Forgot password?" in a real browser (ADR-041): ask for a link, read it from the web server's
 * log (`scripts/e2e.sh` runs the console transport), set a new password, then only the new one
 * signs in. An unknown address gets exactly the same answer, and a used link is refused.
 */
const OLD_PASSWORD = STRONG_PASSWORD;
const NEW_PASSWORD = "An-even-longer-Pass2";

/** The reset link printed for `email`, once it appears in the web server's log. */
async function resetLinkFor(email: string): Promise<string> {
  const pattern = new RegExp(`Password reset link for ${email.replace(/[.+]/g, "\\$&")}\\s+(\\S+)`);
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

test("forgot password: emailed link, new password, old one refused", async ({ page }) => {
  test.skip(!WEB_LOG, "E2E_WEB_LOG is not set for this run");
  const email = `e2e-reset-${Date.now()}@example.com`;

  await page.goto("/sign-in?mode=register");
  await expect(page.getByRole("link", { name: "Forgot password?" })).toHaveCount(0);
  await register(page, email, "E2E Resetter", OLD_PASSWORD);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/sign-in$/);

  // --- an unknown address: the same answer, and no link
  const stranger = `e2e-nobody-${Date.now()}@example.com`;
  await page.getByRole("link", { name: "Forgot password?" }).click();
  await expect(page).toHaveURL(/\/sign-in\/forgot$/);
  await page.getByLabel("Email address").fill(stranger);
  await page.getByRole("button", { name: "Email me a reset link" }).click();
  await expect(page.getByRole("status")).toContainText(`If an account uses ${stranger}`);

  // --- the account's address
  await page.getByRole("button", { name: "Use a different email" }).click();
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Email me a reset link" }).click();
  await expect(page.getByRole("status")).toContainText(`If an account uses ${email}`);
  const link = await resetLinkFor(email);
  expect(await readFile(WEB_LOG, "utf8")).not.toContain(`Password reset link for ${stranger}`);

  await page.goto(link);
  await expect(page).toHaveURL(/\/sign-in\/reset\?token=/);
  // the server refuses a weak password, whatever the form allows
  const token = new URL(page.url()).searchParams.get("token");
  const weak = await page.request.post("/api/auth/reset-password", {
    data: { token, newPassword: "weakpassword" },
  });
  expect(weak.status()).toBe(400);
  expect((await weak.json()).code).toBe("PASSWORD_TOO_WEAK");
  await page.getByLabel("New password", { exact: true }).fill(NEW_PASSWORD);
  await page.getByLabel("Confirm new password").fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByRole("status")).toContainText("Password changed");

  // --- the link works once
  await page.goto(link);
  await expect(page).toHaveURL(/\/sign-in\/reset\?error=INVALID_TOKEN/);
  await expect(page.getByRole("alert").filter({ hasText: "expired" })).toBeVisible();

  // --- only the new password signs in
  await signIn(page, email, OLD_PASSWORD);
  await expect(page.locator('[role="alert"]:not(#__next-route-announcer__)')).toContainText(
    "email and password don’t match",
  );
  await signIn(page, email, NEW_PASSWORD);
  await expect(page).toHaveURL(/\/o\/[0-9a-f-]{36}$/);
});
