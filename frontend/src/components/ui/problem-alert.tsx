import type { ReactNode } from "react";

export interface ProblemAlertProps {
  title: string;
  detail?: string;
  hint?: string;
  requestId?: string;
  action?: ReactNode;
}

/** Renders a problem in plain words: what happened, what to do, and a trace id for support. */
export function ProblemAlert({ title, detail, hint, requestId, action }: ProblemAlertProps) {
  return (
    <div role="alert" className="rounded-panel border border-danger/40 bg-surface p-4 text-sm">
      <p className="flex items-center gap-2 font-semibold text-danger">
        <svg aria-hidden="true" viewBox="0 0 20 20" className="size-4 shrink-0" fill="currentColor">
          <path d="M10 2 1 18h18L10 2Zm-1 6h2v5H9V8Zm0 6h2v2H9v-2Z" />
        </svg>
        {title}
      </p>
      {detail ? <p className="mt-1 text-text">{detail}</p> : null}
      {hint ? <p className="mt-1 text-muted">{hint}</p> : null}
      {requestId ? (
        <p className="mt-2 text-xs text-muted">
          Reference: <code className="font-mono">{requestId}</code>
        </p>
      ) : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}
