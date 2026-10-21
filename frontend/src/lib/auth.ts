import "server-only";
import { betterAuth } from "better-auth";
import { Pool } from "pg";
import { buildAuthOptions } from "@/lib/auth-options";
import type { OrganizationApi } from "@/lib/identity";
import { getEnv } from "@/lib/env";

let pool: Pool | undefined;

/** The connection pool to the `auth` schema (Better Auth's tables), shared by the app. */
export function getAuthPool(): Pool {
  pool ??= createPool();
  return pool;
}

function createPool(): Pool {
  const created = new Pool({
    connectionString: getEnv().AUTH_DATABASE_URL,
    options: "-c search_path=auth",
    max: 10,
    // Without a timeout, a database that drops packets would hang every request forever.
    connectionTimeoutMillis: 5_000,
  });
  // An idle client whose server goes away (database restart, `make down`) emits `error` on the
  // pool; unhandled, that event would crash the whole web server. The pool replaces the client.
  created.on("error", (error) => {
    console.error(`[auth] idle database connection lost: ${error.message}`);
  });
  return created;
}

function createAuth() {
  // Better Auth owns the `auth` schema; the API reads none of it (it verifies tokens instead).
  const pool = getAuthPool();
  const handle: { api?: OrganizationApi } = {};
  const auth = betterAuth(
    buildAuthOptions(pool, () => {
      if (!handle.api) throw new Error("The auth API is not ready yet.");
      return handle.api;
    }),
  );
  handle.api = auth.api as unknown as OrganizationApi;
  return auth;
}

type Auth = ReturnType<typeof createAuth>;
let instance: Auth | undefined;

/** Lazily built so importing this module never needs secrets (e.g. during `next build`). */
export function getAuth(): Auth {
  instance ??= createAuth();
  return instance;
}
