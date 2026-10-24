"use client";

import { useState } from "react";
import { adminPeriod, unlockAdmin, type AdminView } from "@/app/adminboard/actions";
import { AdminDashboard } from "@/components/admin/admin-dashboard";
import { AdminGate } from "@/components/admin/admin-gate";
import { Button } from "@/components/ui/button";
import { ProblemAlert } from "@/components/ui/problem-alert";

/**
 * `/adminboard` in the browser (ADR-044): locked until the key is given, on every visit. The
 * figures and the page's token live in this component's state only, so a reload, a new tab or
 * "Lock" asks for the key again.
 */
export function AdminBoard({ initialDays }: { initialDays: number }) {
  const [view, setView] = useState<AdminView | null>(null);
  const [busy, setBusy] = useState(false);

  if (!view) {
    return (
      <Locked
        onUnlock={async (key) => {
          const result = await unlockAdmin(key, initialDays);
          if (result.status === "refused") return result.message;
          setView(result);
          return null;
        }}
      />
    );
  }

  async function show(days: number) {
    if (!view) return;
    setBusy(true);
    try {
      const next = await adminPeriod(view.token, days);
      setView(next.status === "locked" ? null : next);
    } finally {
      setBusy(false);
    }
  }

  if (view.status === "unavailable") {
    return (
      <main id="main" className="mx-auto max-w-xl px-4 py-16">
        <h1 className="sr-only">Admin dashboard</h1>
        <ProblemAlert
          title="The dashboard’s figures couldn’t be loaded"
          detail={
            view.part === "api"
              ? "The API didn’t answer. It may be down, or have a different ADMIN_DASHBOARD_KEY from the web app."
              : "The accounts database couldn’t be read."
          }
          action={
            <Button variant="secondary" loading={busy} onClick={() => void show(view.days)}>
              Try again
            </Button>
          }
        />
      </main>
    );
  }
  return (
    <AdminDashboard
      overview={view.overview}
      accounts={view.accounts}
      busy={busy}
      onPeriod={(days) => void show(days)}
      onLock={() => setView(null)}
    />
  );
}

/** A placeholder of the dashboard's shape (no figures) behind the key dialog. */
function Locked({ onUnlock }: { onUnlock: (key: string) => Promise<string | null> }) {
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
      <AdminGate onUnlock={onUnlock} />
    </main>
  );
}
