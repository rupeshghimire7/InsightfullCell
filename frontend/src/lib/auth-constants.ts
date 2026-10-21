/** Shared by server and client code, so it must not import server-only modules. */
export const MAGIC_LINK_TTL_SECONDS = 10 * 60;

/** Password length accepted at registration (NIST SP 800-63B minimum; the cap bounds hashing). */
export const PASSWORD_MIN_LENGTH = 8;
export const PASSWORD_MAX_LENGTH = 128;

/** Shown when a sign-in link is asked for an address that is not allowed one (never names who is). */
export const EMAIL_NOT_ALLOWED_MESSAGE =
  "This email isn’t set up with this project. Email sign-in is only for testing this demo: for now it works with the project owner’s email only, and won’t work with other addresses until an organisation’s email domain is set up. Please register with your email and a password instead.";
