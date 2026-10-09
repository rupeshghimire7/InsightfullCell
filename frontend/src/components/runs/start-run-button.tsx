"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { ArrowRight } from "@/components/ui/icons";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { describeError, type Problem } from "@/lib/problem";
import { runsApi, type RunsApi } from "@/lib/runs/api";

export interface StartRunButtonProps {
  orgId: string;
  datasetId: string;
  versionId?: string;
  label?: string;
  variant?: "primary" | "secondary";
  /** Replaceable in tests. */
  api?: RunsApi;
}

/**
 * Starts an analysis and opens its live progress page. One Idempotency-Key per intent: a retry
 * after a network failure (or a double click) returns the same run instead of starting another.
 */
export function StartRunButton({
  orgId,
  datasetId,
  versionId,
  label = "Analyse",
  variant = "primary",
  api = runsApi,
}: StartRunButtonProps) {
  const router = useRouter();
  const key = useRef<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);

  async function start() {
    setBusy(true);
    setProblem(null);
    key.current ??= crypto.randomUUID();
    try {
      const run = await api.start({ datasetId, versionId, idempotencyKey: key.current });
      router.push(`/o/${orgId}/runs/${run.id}`);
    } catch (error) {
      const described = describeError(error);
      // A refused request will not succeed on retry with the same key; a network error might.
      if (described.status >= 400 && described.status < 500) key.current = null;
      setProblem(described);
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <Button variant={variant} loading={busy} onClick={() => void start()}>
        {busy ? "Starting…" : label}
        {busy ? null : <ArrowRight aria-hidden="true" className="size-4" />}
      </Button>
      {problem ? (
        <ProblemAlert
          title={problem.title}
          detail={problem.detail}
          hint={problem.hint}
          requestId={problem.request_id}
        />
      ) : null}
    </div>
  );
}
