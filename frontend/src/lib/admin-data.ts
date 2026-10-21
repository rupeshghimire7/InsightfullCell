import "server-only";
import { getAuthPool } from "@/lib/auth";
import type { Env } from "@/lib/env";

/**
 * The admin dashboard's figures. Usage comes from the API's admin endpoint (counts and sums
 * across every organisation; called over the private network with the admin key, which never
 * leaves this server); names and emails come from the sign-in tables, which only the web app
 * holds. Both are read per request and never cached.
 */

export interface SpendRow {
  provider: string;
  model: string;
  calls: number;
  ok_calls: number;
  backup_calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
}

export interface UsageUser {
  user_id: string;
  first_seen: string;
  uploads: number;
  uploads_completed: number;
  upload_bytes: number;
  runs_started: number;
  last_at: string | null;
  datasets: number;
  stored_bytes: number;
  runs_succeeded: number;
  runs_failed: number;
  runs_active: number;
  last_run_at: string | null;
  cost_usd: number;
  model_calls: number;
  backup_calls: number;
  tokens: number;
}

export interface OpenAIReport {
  status: "ok" | "not_configured" | "unavailable";
  http_status?: number | null;
  currency?: string;
  total_cost?: string;
  cost_by_day?: { start_time: number; cost: string }[];
  by_model?: { model: string; input_tokens: number; output_tokens: number; requests: number }[];
}

export interface Overview {
  generated_at: string;
  days: number;
  spend: {
    total_usd: number;
    held_usd: number;
    estimated_usd: number;
    calls: number;
    backup_calls: number;
    input_tokens: number;
    output_tokens: number;
    by_day: {
      day: string;
      cost_usd: number;
      calls: number;
      backup_calls: number;
      tokens: number;
    }[];
    by_model: SpendRow[];
    by_outcome: { provider: string; outcome: string; calls: number }[];
    period_by_provider: Record<string, number>;
    by_purpose: { purpose: string; calls: number; cost_usd: number }[];
    by_label: {
      label: string;
      calls: number;
      cost_usd: number;
      first_at: string;
      last_at: string;
    }[];
  };
  users: UsageUser[];
  runs: {
    by_status: Record<string, number>;
    by_day: { day: string; runs: number; succeeded: number; failed: number }[];
    duration_p50_seconds: number | null;
    duration_p95_seconds: number | null;
    failures: { code: string; runs: number }[];
  };
  storage: {
    datasets: number;
    stored_bytes: number;
    uploads_by_status: Record<string, number>;
    purges_pending: number;
  };
  queue: Record<string, number>;
  organizations: number;
  config: {
    environment: string;
    label: string;
    provider: string;
    models: Record<string, string>;
    fallback_provider: string;
    fallback_model: string | null;
    spend_cap_aud: string;
    spend_cap_usd: string;
    user_spend_cap_aud: string;
    usd_to_aud: string;
    requests_per_minute: number;
    max_output_tokens: number;
    worker_concurrency: number;
    openai_admin_key_set: boolean;
  };
  provider_reported: { openai: OpenAIReport };
}

export interface Account {
  id: string;
  name: string;
  email: string;
  emailVerified: boolean;
  createdAt: string;
  providers: string[];
  workspace: string | null;
  lastActiveAt: string | null;
  liveSessions: number;
}

export type AdminData =
  | { status: "ok"; overview: Overview; accounts: Account[] }
  | { status: "unavailable"; part: "api" | "auth" };

export const PERIODS = [7, 30, 90] as const;

export async function loadAdminData(env: Env, days: number): Promise<AdminData> {
  const [overview, accounts] = await Promise.all([
    loadOverview(env, days),
    loadAccounts().catch((error: unknown) => {
      console.error(`[admin] could not read accounts: ${(error as Error).message}`);
      return null;
    }),
  ]);
  if (overview === null) return { status: "unavailable", part: "api" };
  if (accounts === null) return { status: "unavailable", part: "auth" };
  return { status: "ok", overview, accounts };
}

async function loadOverview(env: Env, days: number): Promise<Overview | null> {
  try {
    const response = await fetch(`${env.API_BASE_URL}/v1/admin/overview?days=${days}`, {
      headers: { "X-Admin-Key": env.ADMIN_DASHBOARD_KEY ?? "", Accept: "application/json" },
      cache: "no-store",
      signal: AbortSignal.timeout(20_000),
    });
    if (!response.ok) {
      // 404 also when the API has a different key (or none): set the same one on both.
      console.error(`[admin] overview request failed with HTTP ${response.status}`);
      return null;
    }
    return (await response.json()) as Overview;
  } catch (error) {
    console.error(`[admin] overview request failed: ${(error as Error).name}`);
    return null;
  }
}

/** Accounts from Better Auth's tables: who they are and how they sign in (never tokens). */
async function loadAccounts(): Promise<Account[]> {
  const { rows } = await getAuthPool().query<{
    id: string;
    name: string;
    email: string;
    emailVerified: boolean;
    createdAt: Date;
    providers: string[] | null;
    workspace: string | null;
    lastActiveAt: Date | null;
    liveSessions: string;
  }>(
    `SELECT u.id, u.name, u.email, u."emailVerified", u."createdAt",
            (SELECT array_agg(DISTINCT a."providerId" ORDER BY a."providerId")
               FROM account a WHERE a."userId" = u.id) AS providers,
            (SELECT o.name FROM member m JOIN organization o ON o.id = m."organizationId"
              WHERE m."userId" = u.id ORDER BY m."createdAt" LIMIT 1) AS workspace,
            (SELECT max(s."updatedAt") FROM session s WHERE s."userId" = u.id) AS "lastActiveAt",
            (SELECT count(*) FROM session s
              WHERE s."userId" = u.id AND s."expiresAt" > now()) AS "liveSessions"
       FROM "user" u
      ORDER BY u."createdAt" DESC
      LIMIT 2000`,
  );
  return rows.map((row) => ({
    id: row.id,
    name: row.name,
    email: row.email,
    emailVerified: row.emailVerified,
    createdAt: row.createdAt.toISOString(),
    providers: row.providers ?? [],
    workspace: row.workspace,
    lastActiveAt: row.lastActiveAt ? row.lastActiveAt.toISOString() : null,
    liveSessions: Number(row.liveSessions),
  }));
}
