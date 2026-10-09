/**
 * Create or update Better Auth's tables in the `auth` schema: `pnpm auth:migrate`.
 *
 * Runs as a role that may create tables (AUTH_MIGRATION_DATABASE_URL, falling back to
 * AUTH_DATABASE_URL). Idempotent: running it again changes nothing. Pass `--print` to show the
 * SQL without executing it, so it can be reviewed in a pull request.
 */
import { getMigrations } from "better-auth/db/migration";
import { Pool } from "pg";
import nextEnv from "@next/env";

nextEnv.loadEnvConfig(process.cwd());

const url = process.env.AUTH_MIGRATION_DATABASE_URL ?? process.env.AUTH_DATABASE_URL;
if (!url) {
  console.error("Set AUTH_DATABASE_URL (or AUTH_MIGRATION_DATABASE_URL).");
  process.exit(1);
}

const pool = new Pool({ connectionString: url, max: 1 });
try {
  // A role that owns the schema (see SETUP_MANUAL.md) cannot run CREATE SCHEMA, even when it
  // already exists, so only create it when it is missing.
  const { rowCount } = await pool.query("SELECT 1 FROM pg_namespace WHERE nspname = 'auth'");
  if (!rowCount) await pool.query("CREATE SCHEMA auth");
  // The app's pool uses the same search_path, so tables land where the app looks for them.
  const scoped = new Pool({ connectionString: url, options: "-c search_path=auth", max: 1 });
  try {
    // Imported here so the module-level env check runs after .env files are loaded.
    process.env.BETTER_AUTH_URL ??= "http://localhost:3000";
    process.env.BETTER_AUTH_SECRET ??= "migration-only-secret-not-used-at-runtime-0000";
    process.env.AUTH_DATABASE_URL = url;
    process.env.API_BASE_URL ??= "http://localhost:8000";
    process.env.S3_PUBLIC_ORIGIN ??= "http://localhost:9000";
    const { buildAuthOptions } = await import("@/lib/auth-options");
    const { toBeCreated, toBeAdded, runMigrations, compileMigrations } = await getMigrations(
      buildAuthOptions(scoped),
    );
    if (toBeCreated.length === 0 && toBeAdded.length === 0) {
      console.log("Auth schema is up to date.");
    } else if (process.argv.includes("--print")) {
      console.log(await compileMigrations());
    } else {
      await runMigrations();
      console.log(
        `Auth schema updated: ${toBeCreated.length} tables created, ${toBeAdded.length} changed.`,
      );
    }
  } finally {
    await scoped.end();
  }
} finally {
  await pool.end();
}
