"use client";

import { useRouter } from "next/navigation";
import { useId, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { ArrowRight, Check } from "@/components/ui/icons";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { describeError, type Problem } from "@/lib/problem";
import { runsApi, type RunsApi } from "@/lib/runs/api";
import type { ReviewMode } from "@/lib/runs/types";

export interface StartRunButtonProps {
  orgId: string;
  datasetId: string;
  versionId?: string;
  label?: string;
  variant?: "primary" | "secondary";
  /** Replaceable in tests. */
  api?: RunsApi;
}

const MODES: { value: ReviewMode; label: string; hint: string }[] = [
  {
    value: "auto",
    label: "Automatic",
    hint: "Applies every suggested change and goes straight on to the report.",
  },
  {
    value: "manual",
    label: "Manual",
    hint: "Stops so you can check the changes, questions and plan first.",
  },
];

/**
 * Starts an analysis and opens its live progress page. The person chooses how it runs:
 * automatic (the default) never stops for a review; manual stops at the review gate.
 * One Idempotency-Key per intent: a retry after a network failure (or a double click) returns
 * the same run instead of starting another; changing the mode is a new intent.
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
  const name = useId();
  const key = useRef<string | null>(null);
  const [mode, setMode] = useState<ReviewMode>("auto");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);

  function choose(value: ReviewMode) {
    if (value === mode) return;
    setMode(value);
    key.current = null;
  }

  async function start() {
    setBusy(true);
    setProblem(null);
    key.current ??= crypto.randomUUID();
    try {
      const run = await api.start({
        datasetId,
        versionId,
        idempotencyKey: key.current,
        reviewMode: mode,
      });
      router.push(`/o/${orgId}/runs/${run.id}`);
    } catch (error) {
      const described = describeError(error);
      // A refused request will not succeed on retry with the same key; a network error might.
      if (described.status >= 400 && described.status < 500) key.current = null;
      setProblem(described);
      setBusy(false);
    }
  }

  const hint = MODES.find((m) => m.value === mode)?.hint;
  return (
    <div className="space-y-3">
      <fieldset className="space-y-1.5" disabled={busy}>
        <legend className="mb-1.5 text-xs font-medium text-muted">Review</legend>
        <div className="inline-flex rounded-control border border-border bg-surface p-0.5">
          {MODES.map((m) => {
            const checked = m.value === mode;
            return (
              <label
                key={m.value}
                className={`inline-flex min-h-8 cursor-pointer items-center gap-1.5 rounded-[5px] px-3 text-sm font-medium has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-[var(--focus)] pointer-coarse:min-h-11 ${
                  checked ? "bg-accent text-accent-contrast" : "text-muted hover:text-text"
                }`}
              >
                <input
                  type="radio"
                  name={name}
                  value={m.value}
                  checked={checked}
                  onChange={() => choose(m.value)}
                  className="sr-only"
                />
                {checked ? <Check aria-hidden="true" weight="bold" className="size-3.5" /> : null}
                {m.label}
              </label>
            );
          })}
        </div>
        <p className="max-w-[44ch] text-xs text-muted" aria-live="polite">
          {hint}
        </p>
      </fieldset>
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
