/**
 * Telling "a service we depend on is down" apart from a bug, so pages can say so instead of
 * crashing (or, worse, treating the outage as "signed out" and redirecting in a loop).
 */

/** Node socket errors and PostgreSQL SQLSTATEs that mean the database cannot serve us right now.
 * Includes set-up problems (wrong password, missing database) that only an operator can fix. */
const UNAVAILABLE_CODES = new Set([
  "ECONNREFUSED",
  "ECONNRESET",
  "ENOTFOUND",
  "EAI_AGAIN",
  "ETIMEDOUT",
  "EHOSTUNREACH",
  "ENETUNREACH",
  "EPIPE",
  "28000", // invalid authorization
  "28P01", // invalid password
  "3D000", // database does not exist
  "3F000", // schema does not exist (`make migrate` not run)
  "42P01", // table does not exist (`make migrate` not run)
  "53300", // too many connections
  "57P01", // admin shutdown
  "57P02", // crash shutdown
  "57P03", // cannot connect now (starting up)
]);

/** node-postgres's own messages for a dropped or timed-out connection (they carry no code). */
const UNAVAILABLE_MESSAGE = /Connection terminated|timeout exceeded when trying to connect/i;

/** True when `error` (or anything in its `cause` chain) is a database connection failure. */
export function isConnectionFailure(error: unknown, depth = 0): boolean {
  if (depth > 5 || typeof error !== "object" || error === null) return false;
  const { code, message, cause, errors } = error as {
    code?: unknown;
    message?: unknown;
    cause?: unknown;
    errors?: unknown;
  };
  if (typeof code === "string" && (UNAVAILABLE_CODES.has(code) || code.startsWith("08"))) {
    return true; // SQLSTATE class 08: connection exception
  }
  if (typeof message === "string" && UNAVAILABLE_MESSAGE.test(message)) return true;
  // Node reports a refused dual-stack connect (::1 and 127.0.0.1) as an AggregateError.
  if (Array.isArray(errors) && errors.some((inner) => isConnectionFailure(inner, depth + 1))) {
    return true;
  }
  return isConnectionFailure(cause, depth + 1);
}

/** Thrown instead of a raw driver error, so callers can render "unavailable" deliberately. */
export class ServiceUnavailableError extends Error {
  constructor(message = "The sign-in database is unavailable.", options?: { cause?: unknown }) {
    super(message, options);
    this.name = "ServiceUnavailableError";
  }
}
