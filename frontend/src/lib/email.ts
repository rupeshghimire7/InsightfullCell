import "server-only";
import { getEnv } from "@/lib/env";

export interface SignInEmail {
  to: string;
  url: string;
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

export function renderSignInEmail({ url }: Pick<SignInEmail, "url">): {
  subject: string;
  text: string;
  html: string;
} {
  const subject = "Your InsightfulCell sign-in link";
  const text = [
    "Use this link to sign in to InsightfulCell:",
    "",
    url,
    "",
    `The link expires in ${EXPIRY_MINUTES} minutes and works once.`,
    "If you didn't request it, you can ignore this email.",
  ].join("\n");
  const safeUrl = url
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
  const html =
    `<p>Use this link to sign in to InsightfulCell:</p>` +
    `<p><a href="${safeUrl}">Sign in to InsightfulCell</a></p>` +
    `<p>The link expires in ${EXPIRY_MINUTES} minutes and works once.<br>` +
    `If you didn't request it, you can ignore this email.</p>`;
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

async function sendWithResend(
  apiKey: string,
  from: string,
  to: string,
  content: ReturnType<typeof renderSignInEmail>,
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
