"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Button } from "@/components/ui/button";
import { Compass } from "@/components/ui/icons";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { formatCount } from "@/lib/format";
import { describeError, type Problem } from "@/lib/problem";
import { runsApi, type RunsApi } from "@/lib/runs/api";

/** One group a person may explore on its own: a chart's bar or a map's region. */
export interface ExploreTarget {
  column: string;
  columnLabel: string;
  key: string;
  label: string;
  rows: number | null;
}

interface ExploreApi {
  request: (target: ExploreTarget, opener: HTMLElement | SVGElement) => void;
}

const ExploreContext = createContext<ExploreApi | null>(null);

/** Whether this page lets the person explore groups (analysts and above, full analyses). */
export function useExplore(): ExploreApi | null {
  return useContext(ExploreContext);
}

/**
 * Drill-downs: a bar, a table row or a map region asks to explore one group; this confirms what
 * will happen, starts the exploratory sub-run and opens it. One dialog per page.
 */
export function ExploreProvider({
  orgId,
  runId,
  enabled,
  api = runsApi,
  children,
}: {
  orgId: string;
  runId: string;
  /** False for viewers and for explorations themselves: no explore buttons at all. */
  enabled: boolean;
  api?: RunsApi;
  children: ReactNode;
}) {
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement | null>(null);
  const opener = useRef<HTMLElement | SVGElement | null>(null);
  const key = useRef<string>("");
  const [target, setTarget] = useState<ExploreTarget | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const titleId = useId();

  const request = useCallback((next: ExploreTarget, from: HTMLElement | SVGElement) => {
    opener.current = from;
    // A new key per request; a double click or a retry of the same request replays it.
    key.current = crypto.randomUUID();
    setProblem(null);
    setTarget(next);
  }, []);
  const value = useMemo(() => (enabled ? { request } : null), [enabled, request]);

  useEffect(() => {
    const node = dialog.current;
    if (!target || !node || node.open) return;
    if (typeof node.showModal === "function") node.showModal();
    else node.setAttribute("open", "");
  }, [target]);

  function close() {
    if (busy) return;
    const node = dialog.current;
    if (node && typeof node.close === "function" && node.open) node.close();
    else onClosed();
  }

  function onClosed() {
    dialog.current?.removeAttribute("open");
    setTarget(null);
    setBusy(false);
    opener.current?.focus();
  }

  async function start() {
    if (!target) return;
    setBusy(true);
    setProblem(null);
    try {
      const run = await api.drilldown(runId, {
        column: target.column,
        values: [target.key],
        idempotencyKey: key.current,
      });
      router.push(`/o/${orgId}/runs/${run.id}`);
    } catch (error) {
      setProblem(describeError(error));
      setBusy(false);
    }
  }

  return (
    <ExploreContext.Provider value={value}>
      {children}
      {enabled ? (
        <dialog
          ref={dialog}
          aria-labelledby={titleId}
          onClose={onClosed}
          onCancel={(event) => {
            if (busy) event.preventDefault();
          }}
          className="m-auto w-[min(32rem,calc(100vw-2rem))] rounded-panel border border-border bg-raised p-0 text-text shadow-xl backdrop:bg-black/40 print:hidden"
        >
          {target ? (
            <div className="space-y-4 p-5">
              <div className="flex gap-3">
                <Compass aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-accent" />
                <h2 id={titleId} className="text-base font-semibold">
                  Explore {target.label} on its own?
                </h2>
              </div>
              <p className="text-sm leading-relaxed">
                A new analysis of only the rows where {target.columnLabel} is {target.label}
                {target.rows !== null ? ` (${formatCount(target.rows)} rows here)` : ""}. It uses
                this analysis’s approved plan and cleaning, so there is nothing to review, and its
                report is written the same way.
              </p>
              <p className="text-sm text-muted">
                Its findings are marked exploratory: you chose where to look after seeing the
                results, so treat them as leads to check, not conclusions.
              </p>
              {problem ? (
                <ProblemAlert
                  title={problem.title}
                  detail={problem.detail}
                  hint={problem.hint}
                  requestId={problem.request_id}
                />
              ) : null}
              <div className="flex flex-wrap justify-end gap-2">
                <Button variant="secondary" onClick={close} disabled={busy}>
                  Cancel
                </Button>
                <Button loading={busy} onClick={() => void start()}>
                  {busy ? "Starting…" : "Start exploring"}
                </Button>
              </div>
            </div>
          ) : null}
        </dialog>
      ) : null}
    </ExploreContext.Provider>
  );
}

/** A compact "Explore" button for a table row or a list of groups. */
export function ExploreButton({ target }: { target: ExploreTarget }) {
  const explore = useExplore();
  if (!explore) return null;
  return (
    <button
      type="button"
      aria-haspopup="dialog"
      aria-label={`Explore ${target.label} on its own`}
      onClick={(event) => explore.request(target, event.currentTarget)}
      className="press inline-flex min-h-8 items-center gap-1 rounded-control px-2 text-xs font-medium text-accent hover:bg-accent/10 pointer-coarse:min-h-11"
    >
      <Compass aria-hidden="true" className="size-3.5" />
      Explore
    </button>
  );
}
