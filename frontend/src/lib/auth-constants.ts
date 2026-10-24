/** Shared by server and client code, so it must not import server-only modules. */
export const MAGIC_LINK_TTL_SECONDS = 10 * 60;
/** How long a password-reset link works (Better Auth's default is an hour). */
export const PASSWORD_RESET_TTL_SECONDS = 30 * 60;

/**
 * Length of a newly chosen password (the rest of the policy is in `password-policy.ts`). The cap
 * bounds hashing. Passwords chosen under an older, shorter minimum keep working.
 */
export const PASSWORD_MIN_LENGTH = 10;
export const PASSWORD_MAX_LENGTH = 128;

/** The 6-digit code emailed at registration (and to verify an older, unverified account). */
export const OTP_LENGTH = 6;
export const OTP_TTL_SECONDS = 10 * 60;
/** Wrong guesses allowed per code; one more and the code is spent. */
export const OTP_ALLOWED_ATTEMPTS = 3;
/** Time between two codes for the same address. */
export const OTP_RESEND_COOLDOWN_SECONDS = 60;

/** Registration of an address that already has an account (the form links to sign-in). */
export const EMAIL_ALREADY_REGISTERED_MESSAGE =
  "That email is already registered. Sign in instead.";
/** A sign-in link asked for an address with no account (the form links to registration). */
export const ACCOUNT_NOT_FOUND_MESSAGE = "No account found for that email. Register first.";

/**
 * Shown when a sign-in link or a registration code is asked for an address the server is set not
 * to email (EMAIL_LINK_ALLOWED_EMAILS, blank in production: everyone). Never names who is allowed.
 */
export const EMAIL_NOT_ALLOWED_MESSAGE =
  "We can’t send email to this address. Check it, or use Google instead.";
