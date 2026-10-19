"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { Button } from "@/components/ui/button";
import {
  CaretLeft,
  CaretRight,
  CircleNotch,
  DotsSixVertical,
  EyeSlash,
  Table,
} from "@/components/ui/icons";
import { ProblemAlert } from "@/components/ui/problem-alert";
import type { components } from "@/lib/api/schema";
import { formatCount } from "@/lib/format";
import { describeError, ProblemError, readProblem, type Problem } from "@/lib/problem";

type DataPage = components["schemas"]["DataPageOut"];
type Cell = DataPage["rows"][number][number];

export const PAGE_ROWS = 100;
/** Share of the window the panel covers: a quarter by default (the report keeps three
 * quarters), between a fifth and nine tenths. */
export const MIN_WIDTH = 0.2;
export const MAX_WIDTH = 0.9;
export const DEFAULT_WIDTH = 0.25;
const KEY_STEP = 0.05;
const WIDTH_KEY = "insightfulcell:data-panel-width";

export function clampWidth(fraction: number): number {
  if (!Number.isFinite(fraction)) return DEFAULT_WIDTH;
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, fraction));
}

function storedWidth(): number {
  try {
    const value = window.localStorage.getItem(WIDTH_KEY);
    return value === null ? DEFAULT_WIDTH : clampWidth(Number(value));
  } catch {
    return DEFAULT_WIDTH;
  }
}

function storeWidth(fraction: number) {
  try {
    window.localStorage.setItem(WIDTH_KEY, String(fraction));
  } catch {
    // Not remembered (private window, blocked storage): the panel still works.
  }
}

async function fetchRows(runId: string, offset: number, signal: AbortSignal): Promise<DataPage> {
  const response = await fetch(
    `/api/backend/v1/runs/${encodeURIComponent(runId)}/rows?offset=${offset}&limit=${PAGE_ROWS}`,
    {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal,
    },
  );
  if (!response.ok) throw new ProblemError(await readProblem(response));
  return (await response.json()) as DataPage;
}

type Load =
  | { kind: "idle" }
  | { kind: "loading"; previous: DataPage | null }
  | { kind: "ready"; page: DataPage }
  | { kind: "error"; problem: Problem; previous: DataPage | null };

/**
 * "See the data": the rows of the table this analysis read, as read from the file (before
 * cleaning), in a side panel next to the report or dashboard. Not modal: the page stays usable
 * beside it (on wide screens the page makes room; on narrow ones the panel covers it). The arrow
 * on its edge closes and reopens it; the grip resizes it by dragging or with the arrow keys.
 * Personal-data columns are listed but their values stay hidden, as everywhere else.
 */
export function DataSidebar({ runId }: { runId: string }) {
  const panelId = useId();
  const headingId = useId();
  const [open, setOpen] = useState(false);
  const [everOpened, setEverOpened] = useState(false);
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [offset, setOffset] = useState(0);
  const [load, setLoad] = useState<Load>({ kind: "idle" });
  const request = useRef<AbortController | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const dragging = useRef(false);

  const shown = load.kind === "ready" ? load.page : load.kind === "idle" ? null : load.previous;

  function loadPage(nextOffset: number) {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setOffset(nextOffset);
    setLoad((current) => ({
      kind: "loading",
      previous:
        current.kind === "ready" ? current.page : current.kind === "idle" ? null : current.previous,
    }));
    fetchRows(runId, nextOffset, controller.signal).then(
      (page) => {
        if (!controller.signal.aborted) setLoad({ kind: "ready", page });
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setLoad((current) => ({
          kind: "error",
          problem: describeError(error),
          previous: current.kind === "loading" ? current.previous : null,
        }));
      },
    );
  }

  function show() {
    if (!everOpened) {
      setEverOpened(true);
      setWidth(storedWidth());
      loadPage(0);
    }
    setOpen(true);
    // Into the panel, once it is rendered and no longer inert.
    requestAnimationFrame(() => panelRef.current?.focus());
  }

  function hide() {
    setOpen(false);
    triggerRef.current?.focus();
  }

  function resizeTo(fraction: number) {
    const next = clampWidth(fraction);
    setWidth(next);
    storeWidth(next);
  }

  // On wide screens the page makes room for the panel instead of hiding under it.
  useEffect(() => {
    const body = document.body;
    if (!open) return;
    body.dataset.dataPanel = "open";
    body.style.setProperty("--data-panel-width", `${width * 100}vw`);
    return () => {
      delete body.dataset.dataPanel;
      body.style.removeProperty("--data-panel-width");
    };
  }, [open, width]);

  useEffect(() => () => request.current?.abort(), []);

  // Escape inside the panel closes it and returns focus to "See the data".
  useEffect(() => {
    const panel = panelRef.current;
    if (!open || !panel) return;
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      setOpen(false);
      triggerRef.current?.focus();
    }
    panel.addEventListener("keydown", onKeyDown);
    return () => panel.removeEventListener("keydown", onKeyDown);
  }, [open]);

  function onGripDown(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragging.current = true;
  }

  function onGripMove(event: PointerEvent<HTMLDivElement>) {
    if (!dragging.current) return;
    setWidth(clampWidth((window.innerWidth - event.clientX) / window.innerWidth));
  }

  function onGripUp(event: PointerEvent<HTMLDivElement>) {
    if (!dragging.current) return;
    dragging.current = false;
    event.currentTarget.releasePointerCapture(event.pointerId);
    resizeTo((window.innerWidth - event.clientX) / window.innerWidth);
  }

  function onGripKey(event: KeyboardEvent<HTMLDivElement>) {
    const steps: Record<string, number> = {
      ArrowLeft: width + KEY_STEP,
      ArrowRight: width - KEY_STEP,
      Home: MAX_WIDTH,
      End: MIN_WIDTH,
    };
    const next = steps[event.key];
    if (next === undefined) return;
    event.preventDefault();
    resizeTo(next);
  }

  const total = shown?.total_rows ?? 0;
  const first = total === 0 ? 0 : offset + 1;
  const last = Math.min(offset + PAGE_ROWS, total);
  const busy = load.kind === "loading";

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={open ? hide : show}
        className="press inline-flex min-h-9 items-center justify-center gap-2 rounded-control border border-accent bg-accent px-4 text-sm font-medium text-accent-contrast hover:brightness-110 pointer-coarse:min-h-11"
      >
        <Table aria-hidden="true" className="size-4" />
        {open ? "Hide the data" : "See the data"}
      </button>

      {everOpened && !open ? (
        // Reopens the panel where it was (same page of rows, same width).
        <button
          type="button"
          aria-controls={panelId}
          aria-expanded={false}
          onClick={show}
          className="fixed top-1/2 right-0 z-30 flex min-h-24 w-11 -translate-y-1/2 flex-col items-center justify-center gap-1 rounded-l-panel border border-r-0 border-border bg-raised text-xs font-medium shadow-lg hover:bg-surface print:hidden"
        >
          <CaretLeft aria-hidden="true" className="size-4" />
          <span className="[writing-mode:vertical-rl]">Data</span>
          <span className="sr-only">Open the data panel</span>
        </button>
      ) : null}

      {everOpened ? (
        <aside
          id={panelId}
          ref={panelRef}
          tabIndex={-1}
          aria-labelledby={headingId}
          inert={!open}
          style={{ ["--panel-width" as string]: `${width * 100}vw` }}
          className={`fixed inset-y-0 right-0 z-40 flex w-full flex-col border-l border-border bg-surface shadow-2xl transition-transform duration-200 outline-none sm:w-[max(var(--panel-width),20rem)] print:hidden ${
            open ? "translate-x-0" : "pointer-events-none translate-x-full"
          }`}
        >
          {open ? (
            <>
              {/* The edge: drag the grip (or use the arrow keys on it) to resize. A focusable
              separator is the WAI-ARIA window splitter, an interactive widget. */}
              {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- focusable separator = window splitter widget */}
              <div
                role="separator"
                aria-orientation="vertical"
                aria-label="Resize the data panel"
                aria-controls={panelId}
                aria-valuemin={Math.round(MIN_WIDTH * 100)}
                aria-valuemax={Math.round(MAX_WIDTH * 100)}
                aria-valuenow={Math.round(width * 100)}
                aria-valuetext={`${Math.round(width * 100)}% of the window`}
                // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- a window splitter is focusable
                tabIndex={0}
                onPointerDown={onGripDown}
                onPointerMove={onGripMove}
                onPointerUp={onGripUp}
                onPointerCancel={onGripUp}
                onKeyDown={onGripKey}
                className="group absolute inset-y-0 -left-3 z-10 hidden w-6 cursor-col-resize touch-none items-center justify-center sm:flex"
              >
                <span
                  aria-hidden="true"
                  className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border group-hover:w-0.5 group-hover:bg-accent group-focus-visible:w-0.5 group-focus-visible:bg-accent"
                />
                <span
                  aria-hidden="true"
                  className="relative flex h-11 w-6 items-center justify-center rounded-control border border-border bg-raised text-muted shadow-sm group-hover:text-text"
                >
                  <DotsSixVertical weight="bold" className="size-4" />
                </span>
              </div>
            </>
          ) : null}
          <div className="flex items-center gap-3 border-b border-border px-4 py-3 sm:pl-6">
            <button
              type="button"
              onClick={hide}
              aria-label="Close the data panel"
              className="inline-flex size-9 shrink-0 items-center justify-center rounded-control border border-border bg-surface hover:bg-bg pointer-coarse:size-11"
            >
              <CaretRight aria-hidden="true" className="size-4" />
            </button>
            <div className="min-w-0 flex-1">
              <h2 id={headingId} className="text-base font-semibold">
                The data
              </h2>
              <p className="truncate text-xs text-muted">
                As read from your file, before cleaning. Personal data stays hidden.
              </p>
            </div>
            {busy ? (
              <CircleNotch aria-hidden="true" className="size-4 animate-spin text-muted" />
            ) : null}
          </div>

          <p aria-live="polite" className="sr-only">
            {load.kind === "ready" && total > 0
              ? `Showing rows ${formatCount(first)} to ${formatCount(last)} of ${formatCount(total)}.`
              : ""}
          </p>

          <div className="min-h-0 flex-1 overflow-auto sm:pl-2" aria-busy={busy}>
            {load.kind === "error" ? (
              <div className="p-4">
                <ProblemAlert
                  title={load.problem.title}
                  detail={load.problem.detail}
                  hint={load.problem.hint}
                  requestId={load.problem.request_id}
                  action={
                    <Button variant="secondary" onClick={() => loadPage(offset)}>
                      Try again
                    </Button>
                  }
                />
              </div>
            ) : null}
            {shown ? (
              <DataTable page={shown} />
            ) : load.kind === "loading" ? (
              <p className="p-4 text-sm text-muted">Loading the rows…</p>
            ) : null}
          </div>

          {shown && total > 0 ? (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-4 py-2 text-sm sm:pl-6">
              <p className="tabular text-muted">
                Rows {formatCount(first)}–{formatCount(last)} of {formatCount(total)}
              </p>
              <div className="flex gap-2">
                <Button
                  variant="secondary"
                  disabled={busy || offset === 0}
                  onClick={() => loadPage(Math.max(0, offset - PAGE_ROWS))}
                >
                  Previous
                </Button>
                <Button
                  variant="secondary"
                  disabled={busy || offset + PAGE_ROWS >= total}
                  onClick={() => loadPage(offset + PAGE_ROWS)}
                >
                  Next
                </Button>
              </div>
            </div>
          ) : null}
        </aside>
      ) : null}
    </>
  );
}

function DataTable({ page }: { page: DataPage }) {
  if (page.total_rows === 0) {
    return <p className="p-4 text-sm text-muted">The file has no rows.</p>;
  }
  return (
    <table className="min-w-full border-separate border-spacing-0 text-left text-[13px] leading-[1.35]">
      <caption className="sr-only">
        Rows {formatCount(page.offset + 1)} to {formatCount(page.offset + page.rows.length)} of the
        data, as read from the file
      </caption>
      <thead>
        <tr>
          <th
            scope="col"
            className="sticky top-0 left-0 z-20 border-b border-border bg-raised px-3 py-2 text-right font-medium text-muted"
          >
            <span className="sr-only">Row</span>#
          </th>
          {page.columns.map((column) => (
            <th
              key={column.name}
              scope="col"
              className="sticky top-0 z-10 border-b border-border bg-raised px-3 py-2 align-bottom font-medium whitespace-nowrap"
            >
              <span className="flex items-center gap-1">
                {column.personal_data ? (
                  <EyeSlash aria-hidden="true" className="size-3.5 text-muted" />
                ) : null}
                {column.name}
              </span>
              <span className="block text-[11px] font-normal text-muted">
                {column.personal_data
                  ? `Hidden: ${column.personal_data.replaceAll("_", " ")}`
                  : column.type.toLowerCase()}
              </span>
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {page.rows.map((row, index) => (
          <tr key={page.offset + index} className="hover:bg-bg/60">
            <th
              scope="row"
              className="tabular sticky left-0 border-b border-border bg-surface px-3 py-1.5 text-right font-normal text-muted"
            >
              {formatCount(page.offset + index + 1)}
            </th>
            {row.map((cell, column) => (
              <td
                key={column}
                className="max-w-[24rem] truncate border-b border-border px-3 py-1.5 whitespace-nowrap"
                title={typeof cell === "string" && cell.length > 40 ? cell : undefined}
              >
                <CellValue value={cell} hidden={Boolean(page.columns[column]?.personal_data)} />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CellValue({ value, hidden }: { value: Cell; hidden: boolean }) {
  if (hidden) {
    return (
      <span className="text-muted">
        <span aria-hidden="true">•••</span>
        <span className="sr-only">hidden</span>
      </span>
    );
  }
  if (value === null || value === "") {
    return (
      <span className="text-muted">
        <span aria-hidden="true">—</span>
        <span className="sr-only">empty</span>
      </span>
    );
  }
  if (typeof value === "number") return <span className="tabular">{String(value)}</span>;
  return <>{String(value)}</>;
}
