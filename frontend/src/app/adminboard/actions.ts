"use server";

import { headers } from "next/headers";
import { loadAdminData, PERIODS, type Account, type Overview } from "@/lib/admin-data";
import {
  adminEnabled,
  adminKeyMatches,
  adminLimiter,
  clientAddress,
  createAdminToken,
  verifyAdminToken,
} from "@/lib/admin-session";
import { getEnv } from "@/lib/env";

/** The dashboard's figures for one period, with the page-view token that asks for another. */
export type AdminView =
  | { status: "ok"; token: string; days: number; overview: Overview; accounts: Account[] }
  | { status: "unavailable"; token: string; days: number; part: "api" | "auth" };

export type UnlockResult = AdminView | { status: "refused"; message: string };

function period(days: number): number {
  return (PERIODS as readonly number[]).includes(days) ? days : 30;
}

async function view(token: string, days: number): Promise<AdminView> {
  const data = await loadAdminData(getEnv(), days);
  return data.status === "ok"
    ? { status: "ok", token, days, overview: data.overview, accounts: data.accounts }
    : { status: "unavailable", token, days, part: data.part };
}

/**
 * Unlock the dashboard for this page view only (ADR-044): the key is checked here, on every visit,
 * and nothing is stored in the browser: no cookie, no storage. The answer carries a short-lived
 * token that lives in the page's memory, so switching period does not ask again; a reload, a new
 * tab or a new visit does.
 */
export async function unlockAdmin(key: string, days: number): Promise<UnlockResult> {
  const env = getEnv();
  if (!adminEnabled(env)) return { status: "refused", message: "The dashboard is switched off." };
  const address = clientAddress(await headers());
  const wait = adminLimiter.retryAfter(address);
  if (wait > 0) {
    return {
      status: "refused",
      message: `Too many wrong keys. Try again in ${Math.ceil(wait / 60_000)} minutes.`,
    };
  }
  if (!adminKeyMatches(key, env)) {
    adminLimiter.fail(address);
    console.warn("[admin] wrong dashboard key", JSON.stringify({ address }));
    return { status: "refused", message: "That admin key isn’t right." };
  }
  adminLimiter.succeed(address);
  console.warn("[admin] dashboard unlocked", JSON.stringify({ address }));
  return view(createAdminToken(env), period(days));
}

/** Another period (or a refresh) for an unlocked page view; `locked` once the token is not valid. */
export async function adminPeriod(
  token: string,
  days: number,
): Promise<AdminView | { status: "locked" }> {
  if (!verifyAdminToken(token, getEnv())) return { status: "locked" };
  return view(token, period(days));
}
