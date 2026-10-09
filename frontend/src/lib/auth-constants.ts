/** Shared by server and client code, so it must not import server-only modules. */
export const MAGIC_LINK_TTL_SECONDS = 10 * 60;

/** Password length accepted at registration (NIST SP 800-63B minimum; the cap bounds hashing). */
export const PASSWORD_MIN_LENGTH = 8;
export const PASSWORD_MAX_LENGTH = 128;
