import type { CSSProperties } from "react";
import {
  CheckCircle,
  Circle,
  CircleNotch,
  StopCircle,
  XCircle,
  type IconComponent,
} from "@/components/ui/icons";
import { formatBytes, formatCount } from "@/lib/format";
import { formatDuration, isTerminal, STAGE_STATUS, stageLabel } from "@/lib/runs/status";
import type { IngestOutput, RunStatus, Stage } from "@/lib/runs/types";

type Display = "pending" | "running" | "succeeded" | "failed" | "stopped";

const ICON: Record<Display, { icon: IconComponent; className: string }> = {
  pending: { icon: Circle, className: "text-muted" },
  running: { icon: CircleNotch, className: "text-accent animate-spin motion-reduce:animate-none" },
  succeeded: { icon: CheckCircle, className: "text-success" },
  failed: { icon: XCircle, className: "text-danger" },
  stopped: { icon: StopCircle, className: "text-muted" },
};

/** A running stage of a finished run was interrupted (cancelled, expired): say "Stopped". */
function display(stage: Stage, runStatus: RunStatus): Display {
  if (isTerminal(runStatus) && (stage.status === "running" || stage.status === "pending")) {
    return runStatus === "succeeded" ? "pending" : "stopped";
  }
  return stage.status;
}

const DISPLAY_LABEL: Record<Display, string> = { ...STAGE_STATUS, stopped: "Stopped" };

/** The run's stages in order, with what each one found. */
export function StageList({ stages, runStatus }: { stages: Stage[]; runStatus: RunStatus }) {
  return (
    <ol className="relative space-y-1" aria-label="Analysis steps">
      {stages.map((stage, index) => {
        const state = display(stage, runStatus);
        const { icon: Icon, className } = ICON[state];
        const { title, description } = stageLabel(stage.name);
        return (
          <li
            key={stage.name}
            className="enter relative flex gap-4 rounded-panel px-1 py-3"
            style={{ "--i": index } as CSSProperties}
            data-stage={stage.name}
            data-state={state}
          >
            <Icon
              aria-hidden="true"
              weight={state === "running" ? "bold" : "fill"}
              className={`mt-0.5 size-5 shrink-0 ${className}`}
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <h3 className="font-medium">{title}</h3>
                <p className="tabular text-xs text-muted">
                  <span className="sr-only">Status: </span>
                  {DISPLAY_LABEL[state]}
                  {stage.reused ? " (reused from an earlier attempt)" : ""}
                  {stage.started_at && stage.finished_at && state === "succeeded" ? (
                    <> in {formatDuration(stage.started_at, stage.finished_at)}</>
                  ) : null}
                </p>
              </div>
              {description ? <p className="mt-0.5 text-muted">{description}</p> : null}
              {stage.name === "ingest" && state === "succeeded" ? (
                <IngestSummary output={stage.output as IngestOutput} />
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function IngestSummary({ output }: { output: IngestOutput }) {
  const columns = output.columns ?? [];
  return (
    <div className="mt-3 space-y-3">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
        <Fact label="Rows" value={output.rows !== undefined ? formatCount(output.rows) : "–"} />
        <Fact
          label="Columns"
          value={output.column_count !== undefined ? formatCount(output.column_count) : "–"}
        />
        <Fact label="Format" value={(output.format ?? "–").toUpperCase()} />
        <Fact
          label="File size"
          value={output.source_bytes !== undefined ? formatBytes(output.source_bytes) : "–"}
        />
      </dl>
      {output.warnings && output.warnings.length > 0 ? (
        <ul className="space-y-1 text-sm text-warning">
          {output.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
      {columns.length > 0 ? (
        <details className="group rounded-panel border border-border bg-bg/60">
          <summary className="flex min-h-9 cursor-pointer items-center px-3 text-sm font-medium select-none pointer-coarse:min-h-11">
            Show the {formatCount(columns.length)} columns and their types
          </summary>
          <ul className="grid grid-cols-1 gap-x-6 border-t border-border px-3 py-2 sm:grid-cols-2 lg:grid-cols-3">
            {columns.map((column, i) => (
              <li
                key={`${column.name}-${i}`}
                className="flex min-w-0 items-baseline justify-between gap-3 py-1 text-[13px]"
              >
                <span className="truncate" title={column.name}>
                  {column.name}
                </span>
                <span className="shrink-0 font-mono text-xs text-muted">{column.type}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="tabular font-mono text-[15px] font-medium">{value}</dd>
    </div>
  );
}
