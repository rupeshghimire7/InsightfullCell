"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
} from "react";
import { CheckLine, EvidenceBadge, ExploratoryBadge, TestLine } from "@/components/report/evidence";
import { X } from "@/components/ui/icons";
import { formatCount } from "@/lib/format";
import type { ReportFact, Sentence } from "@/lib/report/types";

interface FactsApi {
  open: (id: string, opener: HTMLElement) => void;
  has: (id: string) => boolean;
}

const FactsContext = createContext<FactsApi | null>(null);

/**
 * The fact inspector: every citation chip inside opens one dialog with the fact it names (what
 * was computed, from which columns and rows, how, how sure, and its content hash). Focus moves
 * into the dialog and back to the chip when it closes (Escape, the close button or a click
 * outside).
 */
export function FactsProvider({
  facts,
  sourceLinks = false,
  children,
}: {
  facts: readonly ReportFact[];
  /** On the report page every fact is also listed under its answer: offer a link there. */
  sourceLinks?: boolean;
  children: ReactNode;
}) {
  const byId = useMemo(() => new Map(facts.map((f) => [f.id, f])), [facts]);
  const dialog = useRef<HTMLDialogElement | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const [current, setCurrent] = useState<ReportFact | null>(null);
  const titleId = useId();

  const open = useCallback(
    (id: string, from: HTMLElement) => {
      const fact = byId.get(id);
      if (!fact) return;
      opener.current = from;
      setCurrent(fact);
    },
    [byId],
  );
  const api = useMemo<FactsApi>(() => ({ open, has: (id) => byId.has(id) }), [open, byId]);

  useEffect(() => {
    const node = dialog.current;
    if (!current || !node || node.open) return;
    if (typeof node.showModal === "function") node.showModal();
    else node.setAttribute("open", "");
  }, [current]);

  function close() {
    const node = dialog.current;
    if (node && typeof node.close === "function" && node.open) node.close();
    else onClosed();
  }

  function onClosed() {
    dialog.current?.removeAttribute("open");
    setCurrent(null);
    opener.current?.focus();
  }

  function onBackdrop(event: MouseEvent<HTMLDialogElement>) {
    if (event.target === event.currentTarget) close();
  }

  return (
    <FactsContext.Provider value={api}>
      {children}
      {/* eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-noninteractive-element-interactions -- the backdrop click is a pointer extra; Escape and the close button cover the keyboard */}
      <dialog
        ref={dialog}
        aria-labelledby={titleId}
        onClose={onClosed}
        onClick={onBackdrop}
        className="m-auto w-[min(36rem,calc(100vw-2rem))] rounded-panel border border-border bg-raised p-0 text-text shadow-xl backdrop:bg-black/40 print:hidden"
      >
        {current ? (
          <div className="space-y-4 p-5">
            <div className="flex items-start justify-between gap-3">
              <div className="space-y-1">
                <p className="text-xs text-muted">Source</p>
                <h2 id={titleId} className="font-mono text-base font-semibold text-accent">
                  {current.id}
                </h2>
              </div>
              <button
                type="button"
                onClick={close}
                aria-label="Close"
                className="press -mt-1 -mr-1 inline-flex size-9 items-center justify-center rounded-control text-muted hover:bg-bg hover:text-text pointer-coarse:size-11"
              >
                <X aria-hidden="true" className="size-4" />
              </button>
            </div>
            <p className="text-[15px] leading-relaxed">{current.statement}</p>
            <div className="flex flex-wrap gap-2">
              <EvidenceBadge tier={current.tier} />
              {current.exploratory ? <ExploratoryBadge /> : null}
            </div>
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
              <dt className="text-muted">{current.columns.length === 1 ? "Column" : "Columns"}</dt>
              <dd>{current.columns.map((c) => c.display_name).join(", ") || "None"}</dd>
              <dt className="text-muted">Rows</dt>
              <dd className="tabular font-mono">{formatCount(current.rows)}</dd>
              <dt className="text-muted">Method</dt>
              <dd>{current.method}</dd>
            </dl>
            {current.test ? <TestLine test={current.test} /> : null}
            {current.check && current.check.outcome !== "same_data" ? (
              <CheckLine check={current.check} />
            ) : null}
            <p className="text-xs text-muted">
              Content hash{" "}
              <span className="font-mono break-all text-text">{current.content_hash}</span>
            </p>
            {sourceLinks ? (
              <a
                href={`#fact-${current.id}`}
                onClick={() => {
                  opener.current = null;
                  close();
                }}
                className="inline-flex min-h-9 items-center text-sm font-medium text-accent underline underline-offset-2 pointer-coarse:min-h-11"
              >
                Show it with its answer
              </a>
            ) : null}
          </div>
        ) : null}
      </dialog>
    </FactsContext.Provider>
  );
}

/** A citation chip: opens the fact inspector, or reads as plain text outside a provider. */
export function Citation({ id }: { id: string }) {
  const api = useContext(FactsContext);
  const chip =
    "ml-1 inline-flex min-h-5 items-center rounded-[4px] border border-border px-1 align-[0.1em] font-mono text-[11px] leading-4 text-accent";
  if (!api?.has(id)) return <span className={chip}>{id}</span>;
  return (
    <button
      type="button"
      data-citation=""
      aria-haspopup="dialog"
      aria-label={`Source ${id}`}
      onClick={(event) => api.open(id, event.currentTarget)}
      // The hit area grows past the chip on touch screens without moving the text around it.
      className={`${chip} relative cursor-pointer hover:border-accent hover:bg-accent/10 pointer-coarse:after:absolute pointer-coarse:after:-inset-x-2 pointer-coarse:after:-inset-y-3 pointer-coarse:after:content-['']`}
    >
      {id}
    </button>
  );
}

/** Sentences with their citation chips. */
export function Sentences({ sentences }: { sentences: readonly Sentence[] }) {
  return (
    <>
      {sentences.map((sentence, i) => (
        <span key={i}>
          {i > 0 ? " " : null}
          {sentence.text}
          {sentence.citations.map((id) => (
            <Citation key={id} id={id} />
          ))}
        </span>
      ))}
    </>
  );
}
