"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Trash } from "@/components/ui/icons";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { describeError, type Problem } from "@/lib/problem";
import { runsApi, type RunsApi } from "@/lib/runs/api";

export interface DeleteActionProps {
  kind: "run" | "dataset";
  id: string;
  /** Where to go once it is deleted (it no longer exists to be shown). */
  redirectTo: string;
  /** What goes with it, one sentence. */
  consequence: ReactNode;
  className?: string;
  /** Replaceable in tests. */
  api?: RunsApi;
}

const NOUN = { run: "analysis", dataset: "dataset" } as const;

/**
 * Deletes a run or a dataset after a confirmation in place. The rows go at once and the page
 * moves on; the stored files are removed later by the worker. A running analysis is refused
 * by the API (`still-running`) and the reason is shown here.
 */
export function DeleteAction({
  kind,
  id,
  redirectTo,
  consequence,
  className = "",
  api = runsApi,
}: DeleteActionProps) {
  const router = useRouter();
  const noun = NOUN[kind];
  const descriptionId = useId();
  const keepRef = useRef<HTMLButtonElement>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);

  // The safe choice has focus when the question appears.
  useEffect(() => {
    if (confirming) keepRef.current?.focus();
  }, [confirming]);

  async function remove() {
    setBusy(true);
    setProblem(null);
    try {
      if (kind === "run") await api.remove(id);
      else await api.removeDataset(id);
      setDone(true);
      router.replace(redirectTo);
      router.refresh();
    } catch (error) {
      setProblem(describeError(error));
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <div className={`space-y-2 ${className}`}>
      {confirming ? (
        <div
          role="group"
          aria-label={`Confirm deleting this ${noun}`}
          aria-describedby={descriptionId}
          className="space-y-2 rounded-panel border border-danger/40 bg-surface p-3"
        >
          <p id={descriptionId} className="text-sm">
            Delete this {noun}? {consequence} This cannot be undone.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button variant="danger" loading={busy} onClick={() => void remove()}>
              {busy ? "Deleting…" : `Delete ${noun}`}
            </Button>
            <Button
              ref={keepRef}
              variant="secondary"
              disabled={busy}
              onClick={() => setConfirming(false)}
            >
              Keep it
            </Button>
          </div>
        </div>
      ) : (
        <Button variant="danger" className="w-full" onClick={() => setConfirming(true)}>
          <Trash aria-hidden="true" className="size-4" />
          Delete {noun}…
        </Button>
      )}
      {problem ? (
        <ProblemAlert
          title={problem.title}
          detail={problem.detail}
          hint={problem.hint}
          requestId={problem.request_id}
        />
      ) : null}
      <p className="sr-only" role="status" aria-live="polite">
        {done ? `The ${noun} was deleted.` : ""}
      </p>
    </div>
  );
}
