"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { LockSimple } from "@/components/ui/icons";

/**
 * The dashboard's lock: a dialog in the middle of the screen over a blurred, 25% tinted page.
 * The key goes to this app's own server once (`POST /api/admin/session`), which answers with an
 * HttpOnly cookie; nothing is kept in the page or in browser storage.
 */
export function AdminGate() {
  const router = useRouter();
  const titleId = useId();
  const errorId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => inputRef.current?.focus(), []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const key = inputRef.current?.value ?? "";
    if (!key) {
      setError("Enter the admin key.");
      inputRef.current?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    let response: Response;
    try {
      response = await fetch("/api/admin/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key }),
        cache: "no-store",
      });
    } catch {
      setBusy(false);
      setError("We couldn’t reach the server. Check your connection, then try again.");
      return;
    }
    if (inputRef.current) inputRef.current.value = "";
    if (response.status === 204) {
      router.refresh();
      return;
    }
    setBusy(false);
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    setError(
      response.status === 401
        ? "That admin key isn’t right."
        : (body?.detail ?? "The server couldn’t check the key. Try again in a moment."),
    );
    inputRef.current?.focus();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-text/25 p-4 backdrop-blur-md">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="enter w-full max-w-[400px] rounded-panel border border-border bg-raised p-6 shadow-2xl"
      >
        <div className="flex items-center gap-2 text-muted">
          <LockSimple aria-hidden="true" weight="fill" className="size-5 text-accent" />
          <span className="text-sm font-medium">Admin dashboard</span>
        </div>
        <h1 id={titleId} className="mt-3 text-lg font-semibold">
          Please enter admin key for dashboard access.
        </h1>
        <form onSubmit={onSubmit} method="post" noValidate className="mt-5 space-y-4">
          <div>
            <label htmlFor={`${titleId}-key`} className="block text-sm font-medium">
              Admin key
            </label>
            <input
              ref={inputRef}
              id={`${titleId}-key`}
              name="key"
              type="password"
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              maxLength={512}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? errorId : undefined}
              className="mt-1.5 block min-h-10 w-full rounded-control border border-border bg-surface px-3 font-mono text-sm transition-colors hover:border-muted/60 focus-visible:border-accent aria-invalid:border-danger pointer-coarse:min-h-11"
            />
            {error ? (
              <p id={errorId} role="alert" className="mt-1.5 text-sm text-danger">
                {error}
              </p>
            ) : null}
          </div>
          <Button type="submit" loading={busy} className="w-full">
            {busy ? "Checking…" : "Unlock dashboard"}
          </Button>
        </form>
        <p className="mt-4 text-center text-sm">
          <Link href="/" className="text-accent underline underline-offset-2">
            Back to InsightfulCell
          </Link>
        </p>
      </div>
    </div>
  );
}

/** Locks the dashboard again (clears the cookie) and shows the gate. */
export function LockButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <Button
      variant="secondary"
      loading={busy}
      onClick={async () => {
        setBusy(true);
        await fetch("/api/admin/session", { method: "DELETE", cache: "no-store" }).catch(
          () => null,
        );
        router.refresh();
        setBusy(false);
      }}
    >
      <LockSimple aria-hidden="true" className="size-4" />
      Lock
    </Button>
  );
}
