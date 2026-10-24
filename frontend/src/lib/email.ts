import "server-only";
import { OTP_TTL_SECONDS, PASSWORD_RESET_TTL_SECONDS } from "@/lib/auth-constants";
import { getEnv, resetSender } from "@/lib/env";

export interface SignInEmail {
  to: string;
  url: string;
}

export interface CodeEmail {
  to: string;
  otp: string;
}

/** What every email is made of: Resend gets the plain text and the HTML together. */
export interface EmailContent {
  subject: string;
  text: string;
  html: string;
}

const EXPIRY_MINUTES = 10;

/** Resend's shared test sender: works with any Resend key, but only delivers to the address that
 * owns the key's Resend account. Sending to anyone needs a domain verified in Resend. */
export const RESEND_TEST_SENDER = "InsightfulCell <onboarding@resend.dev>";

/** Shape of a Resend API key (`re_…`). Checked before the key is used in a header. */
const RESEND_KEY = /^re_[A-Za-z0-9_-]{8,200}$/;

export function isResendApiKey(value: unknown): value is string {
  return typeof value === "string" && RESEND_KEY.test(value);
}

/** Why a message was not sent. Safe to show: never the link, the recipient or the key. */
export type EmailFailure = "invalid-key" | "recipient-not-allowed" | "rate-limited" | "failed";

export class EmailDeliveryError extends Error {
  constructor(
    readonly reason: EmailFailure,
    readonly providerStatus?: number,
  ) {
    super(
      `Email provider did not send the message (${reason}` +
        (providerStatus ? `, HTTP ${providerStatus})` : ")"),
    );
    this.name = "EmailDeliveryError";
  }
}

/** A value made safe for a double-quoted HTML attribute (and for text). */
export function escapeAttribute(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

export function renderSignInEmail({ url }: Pick<SignInEmail, "url">): EmailContent {
  const subject = "Your InsightfulCell sign-in link";
  const text = [
    "Use this link to sign in to InsightfulCell:",
    "",
    url,
    "",
    `The link expires in ${EXPIRY_MINUTES} minutes and works once.`,
    "If you didn't request it, you can ignore this email.",
  ].join("\n");
  const html =
    `<p>Use this link to sign in to InsightfulCell:</p>` +
    `<p><a href="${escapeAttribute(url)}">Sign in to InsightfulCell</a></p>` +
    `<p>The link expires in ${EXPIRY_MINUTES} minutes and works once.<br>` +
    `If you didn't request it, you can ignore this email.</p>`;
  return { subject, text, html };
}

export function renderPasswordResetEmail({ url }: Pick<SignInEmail, "url">): EmailContent {
  const minutes = PASSWORD_RESET_TTL_SECONDS / 60;
  const subject = "Reset your InsightfulCell password";
  const text = [
    "Someone asked to reset the password for your InsightfulCell account.",
    "",
    "Choose a new password here:",
    url,
    "",
    `The link expires in ${minutes} minutes and works once.`,
    "If you didn't ask for this, ignore this email: your password stays the same.",
  ].join("\n");
  const html =
    `<p>Someone asked to reset the password for your InsightfulCell account.</p>` +
    `<p><a href="${escapeAttribute(url)}">Choose a new password</a></p>` +
    `<p>The link expires in ${minutes} minutes and works once.<br>` +
    `If you didn't ask for this, ignore this email: your password stays the same.</p>`;
  return { subject, text, html };
}

/** The 6-digit code that verifies an address at registration (or an older, unverified account). */
export function renderOtpEmail({ otp }: Pick<CodeEmail, "otp">): EmailContent {
  const minutes = OTP_TTL_SECONDS / 60;
  const subject = "Your InsightfulCell verification code";
  const text = [
    "Your InsightfulCell verification code is:",
    "",
    otp,
    "",
    `Type it on the page that asked for it. It expires in ${minutes} minutes.`,
    "If you didn't ask for this code, ignore this email: nobody can use it without your inbox.",
  ].join("\n");
  const html =
    `<p>Your InsightfulCell verification code is:</p>` +
    `<p style="font-size:24px;font-weight:600;letter-spacing:4px">${escapeAttribute(otp)}</p>` +
    `<p>Type it on the page that asked for it. It expires in ${minutes} minutes.<br>` +
    `If you didn't ask for this code, ignore this email: nobody can use it without your inbox.</p>`;
  return { subject, text, html };
}

/**
 * Deliver a sign-in link.
 * - `apiKey` given (testing only): sent through Resend with *that* key, from Resend's test sender.
 *   The key is used for this one request and never stored or logged.
 * - otherwise `console` prints it (local development only, blocked in production by the env
 *   schema), and `resend` sends it with the server's own key and sender.
 */
export async function sendSignInEmail(
  message: SignInEmail,
  options: { apiKey?: string } = {},
): Promise<void> {
  const env = getEnv();
  const content = renderSignInEmail(message);

  if (options.apiKey) {
    await sendWithResend(options.apiKey, RESEND_TEST_SENDER, message.to, content);
    return;
  }
  if (env.EMAIL_TRANSPORT === "console") {
    console.warn(`\n[email:console] Sign-in link for ${message.to}\n${message.url}\n`);
    return;
  }
  // The env schema guarantees a key whenever EMAIL_TRANSPORT=resend.
  await sendWithResend(env.RESEND_API_KEY ?? "", env.EMAIL_FROM, message.to, content);
}

/**
 * Deliver a password-reset link, from EMAIL_RESET_FROM (a no-reply address) or EMAIL_FROM.
 * `console` prints it (local development only, blocked in production by the env schema).
 */
export async function sendPasswordResetEmail(message: SignInEmail): Promise<void> {
  const env = getEnv();
  const content = renderPasswordResetEmail(message);
  if (env.EMAIL_TRANSPORT === "console") {
    console.warn(`\n[email:console] Password reset link for ${message.to}\n${message.url}\n`);
    return;
  }
  await sendWithResend(env.RESEND_API_KEY ?? "", resetSender(env), message.to, content);
}

/**
 * Deliver a verification code from EMAIL_FROM (login@…). `console` prints it (local development
 * and e2e only: production refuses that transport), so no code is ever logged in production.
 */
export async function sendOtpEmail(message: CodeEmail): Promise<void> {
  const env = getEnv();
  const content = renderOtpEmail(message);
  if (env.EMAIL_TRANSPORT === "console") {
    console.warn(`\n[email:console] Verification code for ${message.to}\n${message.otp}\n`);
    return;
  }
  await sendWithResend(env.RESEND_API_KEY ?? "", env.EMAIL_FROM, message.to, content);
}

async function sendWithResend(
  apiKey: string,
  from: string,
  to: string,
  content: EmailContent,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
      body: JSON.stringify({ from, to: [to], ...content }),
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    throw new EmailDeliveryError("failed"); // network error or timeout
  }
  if (response.ok) return;
  // Never log the link, the recipient or the key; the status and Resend's error name suffice.
  throw new EmailDeliveryError(await classifyResendError(response), response.status);
}

/** Map a Resend error response to a reason, from its status and its stable `name` field. */
async function classifyResendError(response: Response): Promise<EmailFailure> {
  let name = "";
  try {
    const body = (await response.json()) as { name?: unknown };
    if (typeof body.name === "string") name = body.name;
  } catch {
    // not JSON: decide on the status alone
  }
  if (response.status === 429) return "rate-limited";
  if (["invalid_api_key", "restricted_api_key", "missing_api_key"].includes(name)) {
    return "invalid-key";
  }
  if (response.status === 401) return "invalid-key";
  // Resend answers 403 `validation_error` when the test sender is asked to email anyone other
  // than the account owner (and when the sender's domain is not verified).
  if (response.status === 403) return "recipient-not-allowed";
  return "failed";
}
