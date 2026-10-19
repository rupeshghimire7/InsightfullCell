"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { RunStatusBadge } from "@/components/runs/run-status-badge";
import { StageList } from "@/components/runs/stage-list";
import { Button } from "@/components/ui/button";
import { DeleteAction } from "@/components/ui/delete-action";
import {
  ChartBar,
  Compass,
  SquaresFour,
  Hourglass,
  Info,
  Pulse,
  SealCheck,
  WarningCircle,
  WifiSlash,
} from "@/components/ui/icons";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { describeError, ProblemError, type Problem } from "@/lib/problem";
import { runsApi, type RunsApi } from "@/lib/runs/api";
import { applyEvent, initialView, REFRESH_AFTER, type RunView } from "@/lib/runs/reducer";
import { followRunEvents, type FollowOptions, type StreamResult } from "@/lib/runs/sse";
import { CANCELLABLE, formatDuration, isTerminal, stageLabel } from "@/lib/runs/status";
import type { Run, RunEvent } from "@/lib/runs/types";
import { LocalTime } from "@/components/ui/local-time";

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

  const planned = run.stages.some((s) => s.name === "understand" && s.status === "succeeded");
  const cleanPlan = run.stages.find((s) => s.name === "clean_plan");
  const waiting = Number(cleanPlan?.output?.waiting_for_review ?? 0);
  const planHref = `/o/${orgId}/runs/${runId}/plan`;
  const reportHref = `/o/${orgId}/runs/${runId}/report`;
  const dashboardHref = `/o/${orgId}/runs/${runId}/dashboard`;
  // Reports written since dashboards exist say how many tiles they assembled.
  const hasDashboard = run.stages.find((s) => s.name === "report")?.output?.tiles !== undefined;
  const scope = run.drilldown;
  return (
    <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <div className="min-w-0 space-y-6">
        {scope ? (
          <p className="flex items-start gap-2 rounded-panel border border-warning/40 bg-warning/10 p-3 text-sm">
            <Compass aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warning" />
            <span>
              Exploring a subset: the rows where {scope.column} is {scope.values.join(", ")}. It
              reuses the approved plan and cleaning of{" "}
              <Link
                href={`/o/${orgId}/runs/${scope.parent_run_id}/report`}
                className="font-medium text-accent underline underline-offset-2"
              >
                the full analysis
              </Link>
              ; its findings are exploratory.
            </span>
          </p>
        ) : null}
        {run.status === "queued" && run.waiting_for_worker ? (
          <div
            role="status"
            className="enter flex items-start gap-3 rounded-panel border border-warning/45 bg-warning/10 p-4 text-sm"
            data-testid="no-worker"
          >
            <WarningCircle
              aria-hidden="true"
              weight="fill"
              className="mt-0.5 size-5 shrink-0 text-warning"
            />
            <div>
              <p className="font-semibold">Waiting for a worker</p>
              <p className="mt-1 max-w-[62ch] text-muted">
                The analysis is queued, but no worker is running to start it, so every step is
                waiting. It starts by itself as soon as a worker runs: on a development machine, run{" "}
                <code className="font-mono text-text">make worker</code> (or{" "}
                <code className="font-mono text-text">make dev</code> for everything).
              </p>
            </div>
          </div>
        ) : null}
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
          <section
            aria-labelledby="review-title"
            className="enter flex flex-col gap-4 rounded-panel border border-warning/45 bg-warning/10 p-4 sm:flex-row sm:items-center sm:justify-between sm:p-5"
          >
            <div className="flex items-start gap-3">
              <Hourglass
                aria-hidden="true"
                weight="fill"
                className="mt-0.5 size-5 shrink-0 text-warning"
              />
              <div>
                <h2 id="review-title" className="font-semibold">
                  Your review is needed
                </h2>
                <p className="mt-1 max-w-[52ch] text-sm text-muted">
                  {waiting === 1
                    ? "1 cleaning change could remove or replace values."
                    : `${waiting > 0 ? waiting : "Some"} cleaning changes could remove or replace values.`}{" "}
                  Nothing has been changed yet.
                </p>
              </div>
            </div>
            <div className="flex shrink-0 flex-wrap gap-2">
              <Link
                href={`/o/${orgId}/runs/${runId}/review`}
                className="press inline-flex min-h-9 items-center justify-center rounded-control border border-transparent bg-accent px-4 text-sm font-medium text-accent-contrast hover:brightness-110 pointer-coarse:min-h-11"
              >
                Review changes
              </Link>
              <Link
                href={planHref}
                className="press inline-flex min-h-9 items-center justify-center rounded-control border border-border bg-surface px-4 text-sm font-medium hover:bg-bg pointer-coarse:min-h-11"
              >
                See the plan
              </Link>
            </div>
          </section>
        ) : null}

        {run.status === "succeeded" ? (
          <div className="enter flex items-start gap-3 rounded-panel border border-success/30 bg-success/10 p-4 text-sm">
            <SealCheck
              aria-hidden="true"
              weight="fill"
              className="mt-0.5 size-5 shrink-0 text-success"
            />
            <div className="space-y-3">
              <div>
                <p className="font-semibold">Your report is ready</p>
                <p className="mt-1 max-w-[60ch] text-muted">
                  Every question in the plan is answered with its figures, charts and the columns
                  they come from.
                </p>
              </div>
              {planned ? (
                <div className="flex flex-wrap gap-x-5 gap-y-2">
                  {hasDashboard ? (
                    <Link
                      href={dashboardHref}
                      className="inline-flex items-center gap-1.5 font-medium text-accent underline underline-offset-2"
                    >
                      <SquaresFour aria-hidden="true" className="size-4" />
                      Open the dashboard
                    </Link>
                  ) : null}
                  <Link
                    href={reportHref}
                    className="inline-flex items-center gap-1.5 font-medium text-accent underline underline-offset-2"
                  >
                    <ChartBar aria-hidden="true" className="size-4" />
                    Open the report
                  </Link>
                  <Link
                    href={planHref}
                    className="inline-flex items-center gap-1.5 font-medium text-accent underline underline-offset-2"
                  >
                    <Compass aria-hidden="true" className="size-4" />
                    Open the plan
                  </Link>
                </div>
              ) : null}
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
            {run.started_at ? <LocalTime iso={run.started_at} /> : "Not yet"}
          </Detail>
          {run.drilldown ? null : (
            <Detail label="Review">
              {run.review_mode === "auto" ? "Automatic" : "Manual (stops for you)"}
            </Detail>
          )}
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
        {run.status !== "running" ? (
          <DeleteAction
            kind="run"
            id={runId}
            redirectTo={`/o/${orgId}/datasets/${run.dataset_id}`}
            consequence={
              run.drilldown
                ? "Its report goes with it."
                : "Its plan, report and any explorations of it go with it."
            }
            api={api}
          />
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
  // The server's clock is not the browser's: until hydration is done an open-ended duration
  // shows a placeholder, so the server HTML and the first client render match.
  const inBrowser = useSyncExternalStore(
    noSubscription,
    () => true,
    () => false,
  );
  useEffect(() => {
    if (!ticking) return;
    const timer = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(timer);
  }, [ticking]);
  if (!from) return <span className="text-muted">Not started</span>;
  if (!to && !inBrowser) return <span className="tabular text-muted">—</span>;
  return <span className="tabular">{formatDuration(from, to ?? now)}</span>;
}

const noSubscription = () => () => {};
