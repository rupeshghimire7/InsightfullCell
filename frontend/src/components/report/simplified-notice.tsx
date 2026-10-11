"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Info } from "@/components/ui/icons";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { fallbackLabel } from "@/lib/plans/labels";
import { describeError, type Problem } from "@/lib/problem";
import { runsApi, type RunsApi } from "@/lib/runs/api";

export interface SimplifiedNoticeProps {
  orgId: string;
  runId: string;
  rowVersion: number;
  reason: string | null;
  retryable: boolean;
  /** Replaceable in tests. */
  api?: RunsApi;
}

/**
 * Shown when the language model was unavailable while the report was written: the answers are
 * the facts' own sentences. When the outage may be over, the person can have the words written
 * again; the facts, tests and charts are kept, and the run page shows the progress.
 */
export function SimplifiedNotice({
  orgId,
  runId,
  rowVersion,
  reason,
  retryable,
  api = runsApi,
}: SimplifiedNoticeProps) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);

  async function renarrate() {
    setBusy(true);
    setProblem(null);
    try {
      await api.renarrate(runId, rowVersion);
      router.push(`/o/${orgId}/runs/${runId}`);
    } catch (error) {
      setProblem(describeError(error));
      setBusy(false);
    }
  }

  return (
    <section
      aria-labelledby="simplified-title"
      className="space-y-3 rounded-panel border border-border bg-surface p-4 text-sm"
    >
      <div className="flex gap-3">
        <Info aria-hidden="true" weight="fill" className="mt-0.5 size-5 shrink-0 text-accent" />
        <div className="space-y-1">
          <h2 id="simplified-title" className="font-semibold">
            Narration simplified
          </h2>
          <p className="max-w-[70ch] text-muted">
            The writing model was unavailable{reason ? ` (${fallbackLabel(reason)})` : ""}, so the
            answers use each finding’s own sentence. Every number is still exact and sourced.
          </p>
        </div>
      </div>
      {retryable ? (
        <Button variant="secondary" loading={busy} onClick={() => void renarrate()}>
          {busy ? "Starting…" : "Write the answers again"}
        </Button>
      ) : null}
      {problem ? (
        <ProblemAlert
          title={problem.title}
          detail={problem.detail}
          hint={problem.hint}
          requestId={problem.request_id}
        />
      ) : null}
    </section>
  );
}
