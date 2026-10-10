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
import { fallbackLabel, meaningLabel, providerLabel } from "@/lib/plans/labels";
import type {
  CleanPlanOutput,
  IngestOutput,
  ProfileOutput,
  RunStatus,
  Stage,
  UnderstandOutput,
} from "@/lib/runs/types";

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
              {state === "succeeded" ? <Summary stage={stage} /> : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function Summary({ stage }: { stage: Stage }) {
  switch (stage.name) {
    case "ingest":
      return <IngestSummary output={stage.output as IngestOutput} />;
    case "profile":
      return <ProfileSummary output={stage.output as ProfileOutput} />;
    case "understand":
      return <UnderstandSummary output={stage.output as UnderstandOutput} />;
    case "clean_plan":
      return <CleanPlanSummary output={stage.output as CleanPlanOutput} />;
    default:
      return null;
  }
}

const count = (n: number | undefined) => (n === undefined ? "Unknown" : formatCount(n));

function ProfileSummary({ output }: { output: ProfileOutput }) {
  return (
    <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
      <Fact label="Complete rows" value={count(output.complete_rows)} />
      <Fact label="Duplicate rows" value={count(output.duplicate_rows)} />
      <Fact label="Columns to check" value={count(output.columns_with_issues)} />
      <Fact label="Masked columns" value={count(output.personal_data_columns)} />
    </dl>
  );
}

function UnderstandSummary({ output }: { output: UnderstandOutput }) {
  const who =
    output.plan_source === "model"
      ? `Planned with ${providerLabel(output.provider)}`
      : output.plan_source === "cache"
        ? "Reused a saved plan for this table's shape"
        : `Planned by the data rules${output.fallback_reason ? `: ${fallbackLabel(output.fallback_reason)}` : ""}`;
  return (
    <div className="mt-3 space-y-3">
      {output.summary ? <p className="max-w-[60ch] text-sm">{output.summary}</p> : null}
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
        <Fact label="Questions" value={count(output.analyses)} />
        <Fact label="Charts" value={count(output.charts)} />
        <Fact label="Features" value={count(output.features)} />
        <Fact label="Outcome" value={output.target ?? "None"} text />
      </dl>
      <p className="text-xs text-muted">{who}.</p>
    </div>
  );
}

function CleanPlanSummary({ output }: { output: CleanPlanOutput }) {
  return (
    <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
      <Fact label="Changes" value={count(output.operations)} />
      <Fact label="Applied automatically" value={count(output.applied_automatically)} />
      <Fact label="Waiting for you" value={count(output.waiting_for_review)} />
      <Fact label="Not applied" value={count(output.not_applied)} />
    </dl>
  );
}

function IngestSummary({ output }: { output: IngestOutput }) {
  const columns = output.columns ?? [];
  return (
    <div className="mt-3 space-y-3">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
        <Fact label="Rows" value={count(output.rows)} />
        <Fact label="Columns" value={count(output.column_count)} />
        <Fact label="Format" value={(output.format ?? "Unknown").toUpperCase()} text />
        <Fact
          label="File size"
          value={output.source_bytes !== undefined ? formatBytes(output.source_bytes) : "Unknown"}
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
            Show the {formatCount(columns.length)} columns and what they mean
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
                <span className="shrink-0 text-xs text-muted">
                  {column.personal_data ? "Masked · " : ""}
                  {column.meaning ? (
                    meaningLabel(column.meaning)
                  ) : (
                    <span className="font-mono">{column.type}</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

/** Numbers in mono (they line up); words such as a column name in the text face. */
function Fact({ label, value, text = false }: { label: string; value: string; text?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted">{label}</dt>
      <dd
        className={
          text
            ? "truncate text-[15px] font-medium"
            : "tabular truncate font-mono text-[15px] font-medium"
        }
        title={text ? value : undefined}
      >
        {value}
      </dd>
    </div>
  );
}
