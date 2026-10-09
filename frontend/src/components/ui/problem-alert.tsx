import type { ReactNode } from "react";
import { WarningCircle } from "@/components/ui/icons";

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
    <div
      role="alert"
      className="enter flex gap-3 rounded-panel border border-danger/35 bg-surface p-4 text-sm"
    >
      <WarningCircle
        aria-hidden="true"
        weight="fill"
        className="mt-0.5 size-5 shrink-0 text-danger"
      />
      <div className="min-w-0 flex-1">
        <p className="font-semibold text-danger">{title}</p>
        {detail ? <p className="mt-1 text-text">{detail}</p> : null}
        {hint ? <p className="mt-1 text-muted">{hint}</p> : null}
        {requestId ? (
          <p className="mt-2 text-xs text-muted">
            Reference: <code className="font-mono break-all">{requestId}</code>
          </p>
        ) : null}
        {action ? <div className="mt-3">{action}</div> : null}
      </div>
    </div>
  );
}
