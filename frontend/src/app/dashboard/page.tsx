import type { Metadata } from "next";
import { cookies } from "next/headers";
import { notFound } from "next/navigation";
import { connection } from "next/server";
import { AdminDashboard } from "@/components/admin/admin-dashboard";
import { AdminGate } from "@/components/admin/admin-gate";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { loadAdminData, PERIODS } from "@/lib/admin-data";
import { adminCookieName, adminEnabled, verifyAdminToken } from "@/lib/admin-session";
import { getEnv } from "@/lib/env";

export const metadata: Metadata = { title: "Admin dashboard" };

/**
 * `/dashboard`: everyone's usage and model spend, behind the admin key (`ADMIN_DASHBOARD_KEY`).
 * Locked, the page renders no data at all: only the key dialog over a blurred placeholder.
 * Switched off (no key configured), it does not exist.
 */
export default async function AdminDashboardPage({
  searchParams,
}: {
  searchParams: Promise<{ days?: string }>;
}) {
  await connection();
  const env = getEnv();
  if (!adminEnabled(env)) notFound();
  const token = (await cookies()).get(adminCookieName(env))?.value;
  if (!verifyAdminToken(token, env)) return <Locked />;

  const requested = Number((await searchParams).days);
  const days = (PERIODS as readonly number[]).includes(requested) ? requested : 30;
  const data = await loadAdminData(env, days);
  if (data.status === "unavailable") {
    return (
      <main id="main" className="mx-auto max-w-xl px-4 py-16">
        <h1 className="sr-only">Admin dashboard</h1>
        <ProblemAlert
          title="The dashboard’s figures couldn’t be loaded"
          detail={
            data.part === "api"
              ? "The API didn’t answer the admin request. It may be down, or set up with a different ADMIN_DASHBOARD_KEY: the web app and the API need the same one."
              : "The accounts database couldn’t be read."
          }
          hint="The web server’s log says more. Try again in a minute."
          action={
            <a href={`/dashboard?days=${days}`} className="font-medium text-accent underline">
              Try again
            </a>
          }
        />
      </main>
    );
  }
  return <AdminDashboard overview={data.overview} accounts={data.accounts} />;
}

/** A placeholder of the dashboard's shape (no figures) behind the key dialog. */
function Locked() {
  return (
    <main id="main" className="mx-auto w-full max-w-[1400px] px-4 py-8 sm:px-8">
      <div aria-hidden="true" className="pointer-events-none blur-sm select-none">
        <div className="h-8 w-64 rounded-control bg-border" />
        <div className="mt-2 h-4 w-96 max-w-full rounded-control bg-border/70" />
        <div className="mt-8 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-28 rounded-panel border border-border bg-surface" />
          ))}
        </div>
        <div className="mt-8 h-64 rounded-panel border border-border bg-surface" />
        <div className="mt-8 h-96 rounded-panel border border-border bg-surface" />
      </div>
      <AdminGate />
    </main>
  );
}
