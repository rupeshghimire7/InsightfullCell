"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { RunStatusBadge } from "@/components/runs/run-status-badge";
import { StageList } from "@/components/runs/stage-list";
import { Button } from "@/components/ui/button";
import { Info, Pulse, SealCheck, WifiSlash } from "@/components/ui/icons";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { formatDate } from "@/lib/format";
import { describeError, ProblemError, type Problem } from "@/lib/problem";
import { runsApi, type RunsApi } from "@/lib/runs/api";
import { applyEvent, initialView, REFRESH_AFTER, type RunView } from "@/lib/runs/reducer";
import { followRunEvents, type FollowOptions, type StreamResult } from "@/lib/runs/sse";
import { CANCELLABLE, formatDuration, isTerminal, stageLabel } from "@/lib/runs/status";
import type { Run, RunEvent } from "@/lib/runs/types";

/** How live updates reach the page while the run is active (nothing is shown once it ends). */
type Connection = "connecting" | "live" | "reconnecting" | "polling";

export interface RunProgressProps {
  initial: Run;
  orgId: string;
  /** Replaceable in tests. */
  api?: RunsApi;
  follow?: (options: FollowOptions) => Promise<StreamResult>;
  pollMs?: number;
}

const CONNECTION: Record<Connection, string> = {
  connecting: "Connecting to live progress…",
  live: "Live",
  reconnecting: "Reconnecting…",
  polling: "Live updates are blocked on this network; refreshing every few seconds",
};

function announce(event: RunEvent): string | null {
  const step = event.stage ? stageLabel(event.stage).title : "";
  switch (event.type) {
    case "stage.started":
      return `Started: ${step}.`;
    case "stage.completed":
      return `Done: ${step}.`;
    case "stage.retrying":
      return `Retrying: ${step}.`;
    case "review.required":
      return "The analysis is waiting for your review.";
    case "run.succeeded":
      return "The analysis finished.";
    case "run.failed":
      return `The analysis failed. ${typeof event.data.detail === "string" ? event.data.detail : ""}`;
    case "run.cancelled":
      return "The analysis was cancelled.";
    case "run.expired":
      return "The analysis expired while waiting for review.";
    default:
      return null;
  }
}

/** Live progress of one run: follows the event stream, falls back to polling, and offers the
 * actions the run's state allows (cancel, retry). */
export function RunProgress({
  initial,
  orgId,
  api = runsApi,
  follow = followRunEvents,
  pollMs = 3_000,
}: RunProgressProps) {
  const [view, setView] = useState<RunView>(() => initialView(initial));
  const [connection, setConnection] = useState<Connection>("connecting");
  const [announcement, setAnnouncement] = useState("");
  const [actionError, setActionError] = useState<Problem | null>(null);
  const [busy, setBusy] = useState<"cancel" | "retry" | null>(null);
  const [confirming, setConfirming] = useState(false);
  const viewRef = useRef(view);
  useEffect(() => {
    viewRef.current = view;
  }, [view]);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const { run } = view;
  const active = !isTerminal(run.status);
  const runId = run.id;

  /** Replace the view with a snapshot, unless events already took it further. */
  const adopt = useCallback((fresh: Run) => {
    setView((current) =>
      fresh.last_event_seq >= current.run.last_event_seq ? { ...current, run: fresh } : current,
    );
  }, []);

  const refresh = useCallback(() => {
    clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(() => {
      api.get(runId).then(adopt, () => undefined); // the stream keeps the view current anyway
    }, 250);
  }, [api, adopt, runId]);

  useEffect(() => () => clearTimeout(refreshTimer.current), []);

  // Follow the stream while the run is active; poll if the stream cannot get through.
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    const { signal } = controller;

    async function poll() {
      setConnection("polling");
      while (!signal.aborted) {
        try {
          const fresh = await api.get(runId, signal);
          adopt(fresh);
          if (isTerminal(fresh.status)) return;
        } catch {
          if (signal.aborted) return;
        }
        await new Promise((resolve) => setTimeout(resolve, pollMs));
      }
    }

    follow({
      getTicket: () => api.ticket(runId),
      lastEventId: viewRef.current.run.last_event_seq,
      signal,
      onState: (state) => setConnection(state),
      onEvent: (event) => {
        setView((current) => applyEvent(current, event));
        const message = announce(event);
        if (message) setAnnouncement(message);
        if (REFRESH_AFTER.has(event.type)) refresh();
      },
    }).then(
      (result) => {
        if (result === "fallback") void poll();
      },
      () => {
        if (!signal.aborted) void poll();
      },
    );
    return () => controller.abort();
  }, [active, runId, api, follow, adopt, refresh, pollMs]);

  async function act(kind: "cancel" | "retry") {
    setBusy(kind);
    setActionError(null);
    try {
      const updated =
        kind === "cancel"
          ? await api.cancel(runId, run.row_version)
          : await api.retry(runId, run.row_version);
      adopt(updated);
      setAnnouncement(
        kind === "cancel" ? "The analysis was cancelled." : "The analysis was queued again.",
      );
    } catch (error) {
      const problem = describeError(error);
      setActionError(problem);
      if (error instanceof ProblemError && (problem.status === 409 || problem.status === 412)) {
        refresh(); // someone else changed it: show the current state
      }
    } finally {
      setBusy(null);
      setConfirming(false);
    }
  }

  return (
    <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <div className="min-w-0 space-y-6">
        {view.note ? (
          <p className="enter flex items-start gap-2 rounded-panel border border-border bg-surface p-3 text-sm">
            <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-accent" />
            {view.note}
          </p>
        ) : null}

        {run.status === "failed" && run.failure ? (
          <ProblemAlert
            title="The analysis failed"
            detail={run.failure.detail ?? undefined}
            hint={run.failure.hint ?? undefined}
            requestId={`${run.failure.code} · run ${run.id}`}
            action={
              <Button loading={busy === "retry"} onClick={() => void act("retry")}>
                {busy === "retry" ? "Retrying…" : "Try again"}
              </Button>
            }
          />
        ) : null}

        {run.status === "awaiting_review" ? (
          <p className="flex items-start gap-2 rounded-panel border border-warning/40 bg-warning/10 p-3 text-sm">
            <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warning" />
            This analysis is paused until someone decides on its proposed cleaning changes.
          </p>
        ) : null}

        {run.status === "succeeded" ? (
          <div className="enter flex items-start gap-3 rounded-panel border border-success/30 bg-success/10 p-4 text-sm">
            <SealCheck
              aria-hidden="true"
              weight="fill"
              className="mt-0.5 size-5 shrink-0 text-success"
            />
            <div>
              <p className="font-semibold">Your file is verified and ready</p>
              <p className="mt-1 text-muted">
                The stored bytes match your upload, and every row was read into typed columns.
                Column profiles, cleaning and findings are not available in this release yet.
              </p>
            </div>
          </div>
        ) : null}

        <section
          aria-labelledby="steps-title"
          className="rounded-panel border border-border bg-surface p-4 sm:p-5"
        >
          <h2 id="steps-title" className="mb-2 text-base font-semibold">
            Steps
          </h2>
          <StageList stages={run.stages} runStatus={run.status} />
        </section>

        {actionError ? (
          <ProblemAlert
            title={actionError.title}
            detail={actionError.detail}
            hint={actionError.hint}
            requestId={actionError.request_id}
          />
        ) : null}
      </div>

      <aside aria-label="Run details" className="space-y-5">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm lg:grid-cols-1">
          <Detail label="Status">
            <RunStatusBadge status={run.status} />
          </Detail>
          <Detail label="Dataset">
            <Link
              href={`/o/${orgId}/datasets/${run.dataset_id}`}
              className="font-medium text-accent underline underline-offset-2"
            >
              {run.dataset_name || "Dataset"}
            </Link>
          </Detail>
          <Detail label="Started">
            {run.started_at ? (
              <time dateTime={run.started_at}>{formatDate(run.started_at)}</time>
            ) : (
              "Not yet"
            )}
          </Detail>
          <Detail label={run.finished_at ? "Took" : "Running for"}>
            <Elapsed
              from={run.started_at}
              to={run.finished_at}
              ticking={run.status === "running"}
            />
          </Detail>
          {run.queue === "large" ? <Detail label="Worker">Large (more memory)</Detail> : null}
        </dl>

        {active ? (
          <p className="flex items-center gap-2 text-xs text-muted" data-testid="connection">
            {connection === "polling" ? (
              <WifiSlash aria-hidden="true" className="size-4" />
            ) : connection === "live" ? (
              <Pulse aria-hidden="true" className="size-4 text-accent" />
            ) : null}
            {CONNECTION[connection]}
          </p>
        ) : null}

        {CANCELLABLE.has(run.status) ? (
          confirming ? (
            <div
              className="space-y-2 rounded-panel border border-border bg-surface p-3"
              role="group"
              aria-label="Confirm cancelling"
            >
              <p className="text-sm">Cancel this analysis? Finished steps are kept.</p>
              <div className="flex flex-wrap gap-2">
                <Button loading={busy === "cancel"} onClick={() => void act("cancel")}>
                  {busy === "cancel" ? "Cancelling…" : "Cancel analysis"}
                </Button>
                <Button
                  variant="secondary"
                  disabled={busy !== null}
                  onClick={() => setConfirming(false)}
                >
                  Keep running
                </Button>
              </div>
            </div>
          ) : (
            <Button variant="secondary" className="w-full" onClick={() => setConfirming(true)}>
              Cancel analysis…
            </Button>
          )
        ) : null}
      </aside>

      <p className="sr-only" role="status" aria-live="polite" data-testid="run-announcer">
        {announcement}
      </p>
    </div>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mt-1">{children}</dd>
    </div>
  );
}

function Elapsed({
  from,
  to,
  ticking,
}: {
  from: string | null;
  to: string | null;
  ticking: boolean;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ticking) return;
    const timer = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, [ticking]);
  if (!from) return <span className="text-muted">–</span>;
  return <span className="tabular">{formatDuration(from, to ?? now)}</span>;
}
