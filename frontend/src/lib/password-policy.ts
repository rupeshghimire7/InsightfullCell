import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from "@/lib/auth-constants";

/**
 * The one password policy, for every place a password is chosen: setting a first password,
 * resetting it, changing it, and the server hooks that refuse a hand-made request. Pure and
 * shared by server and client code. It applies to *new* passwords only: one chosen under an older
 * rule keeps working until its owner changes it.
 */
export type PasswordRuleId =
  "length" | "lowercase" | "uppercase" | "digit" | "special" | "no-spaces" | "no-email-name";

export interface PasswordRule {
  id: PasswordRuleId;
  /** Checklist wording: what a good password has. */
  label: string;
  passes: (password: string, emailName: string | null) => boolean;
}

/** Shorter local parts ("jo@…") are too common inside words to refuse. */
const EMAIL_NAME_MIN_LENGTH = 3;

export const PASSWORD_RULES: readonly PasswordRule[] = [
  {
    id: "length",
    label: `At least ${PASSWORD_MIN_LENGTH} characters`,
    passes: (password) => [...password].length >= PASSWORD_MIN_LENGTH,
  },
  { id: "lowercase", label: "A lowercase letter", passes: (password) => /\p{Ll}/u.test(password) },
  { id: "uppercase", label: "An uppercase letter", passes: (password) => /\p{Lu}/u.test(password) },
  { id: "digit", label: "A number", passes: (password) => /\p{Nd}/u.test(password) },
  {
    id: "special",
    label: "A symbol, like ! # or %",
    passes: (password) => /[^\p{L}\p{N}\s]/u.test(password),
  },
  { id: "no-spaces", label: "No spaces", passes: (password) => !/\s/u.test(password) },
  {
    id: "no-email-name",
    label: "Not the part of your email before the @",
    passes: (password, emailName) =>
      !emailName || !password.toLowerCase().includes(emailName.toLowerCase()),
  },
];

/** Lower-cased part of the address before the @, when long enough to check against. */
export function emailName(email: string | null | undefined): string | null {
  const local = (email ?? "").trim().split("@")[0] ?? "";
  return local.length >= EMAIL_NAME_MIN_LENGTH ? local.toLowerCase() : null;
}

/** Ids of the rules `password` breaks, in checklist order; empty when it is acceptable. */
export function passwordProblems(password: string, email?: string | null): PasswordRuleId[] {
  const name = emailName(email);
  return PASSWORD_RULES.filter((rule) => !rule.passes(password, name)).map((rule) => rule.id);
}

/** Whether a new password is too long to hash (checked apart from the checklist). */
export function passwordTooLong(password: string): boolean {
  return password.length > PASSWORD_MAX_LENGTH;
}

export const PASSWORD_TOO_LONG_MESSAGE = `Use at most ${PASSWORD_MAX_LENGTH} characters.`;

/** One sentence naming what is missing, for forms and the server's refusal; `null` when fine. */
export function passwordPolicyError(password: string, email?: string | null): string | null {
  if (passwordTooLong(password)) return PASSWORD_TOO_LONG_MESSAGE;
  const broken = new Set(passwordProblems(password, email));
  if (broken.size === 0) return null;
  const missing = PASSWORD_RULES.filter((rule) => broken.has(rule.id)).map(
    (rule) => rule.label.charAt(0).toLowerCase() + rule.label.slice(1),
  );
  return `Choose a stronger password: ${missing.join("; ")}.`;
}
