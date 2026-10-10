import Link from "next/link";
import type { ReactNode } from "react";
import { ColumnCard } from "@/components/plans/column-card";
import {
  CaretDown,
  ChartBar,
  ChartLine,
  ChartScatter,
  Check,
  Compass,
  DownloadSimple,
  GridFour,
  ShieldCheck,
  Target,
  type IconComponent,
} from "@/components/ui/icons";
import { formatCount } from "@/lib/format";
import {
  chartLabel,
  fallbackLabel,
  featureLabel,
  minerLabel,
  providerLabel,
  sourceLabel,
} from "@/lib/plans/labels";
import { plansApi } from "@/lib/plans/api";
import type { CleaningOperation, Plan } from "@/lib/plans/types";

const CHART_ICON: Record<string, IconComponent> = {
  histogram: ChartBar,
  bar: ChartBar,
  line: ChartLine,
  scatter: ChartScatter,
  box: ChartBar,
  heatmap: GridFour,
  map: GridFour,
};

/** Who planned this analysis, in one line a non-technical reader understands. */
export function plannedBy(plan: Plan): string {
  if (plan.source === "person") return "Revised by you";
  if (plan.source === "rules") {
    return plan.fallback_reason
      ? `Planned by the data rules, because ${fallbackLabel(plan.fallback_reason)}`
      : "Planned by the data rules";
  }
  const who = `${providerLabel(plan.provider)}${plan.model ? ` (${plan.model})` : ""}`;
  return plan.source === "cache"
    ? `Reused a plan ${who} made for this table's shape`
    : `Planned with ${who}`;
}

export function PlanOverview({ plan, orgId }: { plan: Plan; orgId: string }) {
  const names = new Map(plan.dictionary.map((d) => [d.column, d.display_name || d.column]));
  const label = (column: string) => names.get(column) ?? column;
  const masked = plan.columns.filter((c) => c.personal_data).length;
  const first = plan.analyses.filter((a) => a.priority === 1);
  const later = plan.analyses.filter((a) => a.priority !== 1);
  const runId = plan.run.id;
  const pending = plan.cleaning?.operations.filter((o) => o.decision === "pending") ?? [];

  return (
    <div className="space-y-12">
      <section
        aria-labelledby="about-title"
        className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]"
      >
        <div className="min-w-0 space-y-4">
          <h2 id="about-title" className="text-lg font-semibold">
            What this table is
          </h2>
          {plan.dataset_summary ? (
            <p className="max-w-[65ch] text-[15px] leading-relaxed">{plan.dataset_summary}</p>
          ) : (
            <p className="max-w-[65ch] text-muted">
              The data rules planned this analysis from the columns alone, so there is no written
              summary. Connect a language model to have one written from the masked description.
            </p>
          )}
          {plan.domain || plan.grain ? (
            <dl className="flex flex-wrap gap-x-8 gap-y-2 text-sm">
              {plan.domain ? <Fact label="Subject">{plan.domain}</Fact> : null}
              {plan.grain ? <Fact label="One row is">{plan.grain}</Fact> : null}
            </dl>
          ) : null}
          <p className="flex items-start gap-2 text-sm text-muted">
            <Compass aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-accent" />
            {plannedBy(plan)}.
          </p>
        </div>
        <aside
          aria-label="Plan at a glance"
          className="space-y-4 rounded-panel border border-border bg-surface p-4"
        >
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <Fact label="Columns">{formatCount(plan.columns.length)}</Fact>
            <Fact label="Questions">{formatCount(plan.analyses.length)}</Fact>
            <Fact label="Outcome">
              {plan.target ? (
                <span className="inline-flex items-center gap-1">
                  <Target aria-hidden="true" className="size-4 text-accent" />
                  {label(plan.target.column)}
                </span>
              ) : (
                "None chosen"
              )}
            </Fact>
            <Fact label="Masked">
              <span className="inline-flex items-center gap-1">
                <ShieldCheck aria-hidden="true" className="size-4 text-accent" />
                {masked === 1 ? "1 column" : `${formatCount(masked)} columns`}
              </span>
            </Fact>
          </dl>
          <a
            href={plansApi.recipeUrl(runId)}
            download
            className="press inline-flex min-h-9 w-full items-center justify-center gap-2 rounded-control border border-border bg-surface px-4 text-sm font-medium hover:bg-bg pointer-coarse:min-h-11"
          >
            <DownloadSimple aria-hidden="true" className="size-4" />
            Download recipe
          </a>
          <p className="text-xs text-muted">
            A Python script that rebuilds the cleaned table, the planned features and the charts
            from your original file.
          </p>
        </aside>
      </section>

      {plan.analyses.length > 0 ? (
        <section aria-labelledby="questions-title" className="space-y-4">
          <h2 id="questions-title" className="text-lg font-semibold">
            Questions this analysis will answer
          </h2>
          <QuestionList title="First" items={first} label={label} />
          {later.length > 0 ? <LaterQuestions items={later} label={label} /> : null}
        </section>
      ) : null}

      {plan.cleaning ? (
        <section aria-labelledby="cleaning-title" className="space-y-4">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 id="cleaning-title" className="text-lg font-semibold">
              Cleaning
            </h2>
            {pending.length > 0 && plan.run.status === "awaiting_review" ? (
              <Link
                href={`/o/${orgId}/runs/${runId}/review`}
                className="font-medium text-accent underline underline-offset-2"
              >
                Review {pending.length === 1 ? "1 change" : `${pending.length} changes`}
              </Link>
            ) : null}
          </div>
          <CleaningTable operations={plan.cleaning.operations} label={label} />
        </section>
      ) : null}

      <div className="grid grid-cols-1 gap-10 lg:grid-cols-2">
        <section aria-labelledby="features-title" className="space-y-3">
          <h2 id="features-title" className="text-lg font-semibold">
            Features to build
          </h2>
          {plan.features.length === 0 ? (
            <p className="text-muted">None planned for this table.</p>
          ) : (
            <ul className="space-y-2">
              {plan.features.map((f) => (
                <li key={f.id} className="flex items-start gap-2 text-sm">
                  <Check
                    aria-hidden="true"
                    className={`mt-0.5 size-4 shrink-0 ${f.enabled ? "text-accent" : "text-muted opacity-40"}`}
                  />
                  <span className={f.enabled ? "" : "text-muted line-through"}>
                    <span className="font-medium">{featureLabel(f.kind)}</span>
                    {f.part ? ` (${f.part.replaceAll("_", " ")})` : ""} of{" "}
                    {f.columns.map(label).join(" and ")}
                    {f.enabled ? "" : <span className="sr-only"> (switched off)</span>}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p className="text-xs text-muted">
            Each is kept only if it shows a real signal; that check arrives with the next release.
          </p>
        </section>
        <section aria-labelledby="charts-title" className="space-y-3">
          <h2 id="charts-title" className="text-lg font-semibold">
            Charts planned
          </h2>
          <ul className="space-y-2">
            {plan.charts.map((c) => {
              const Icon = CHART_ICON[c.kind] ?? ChartBar;
              return (
                <li key={c.id} className="flex items-start gap-2 text-sm">
                  <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted" />
                  <span>
                    <span className="font-medium">{c.title}</span>{" "}
                    <span className="text-muted">({chartLabel(c.kind)})</span>
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      </div>

      <section aria-labelledby="columns-title" className="space-y-4">
        <h2 id="columns-title" className="text-lg font-semibold">
          Columns
        </h2>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {plan.columns.map((column) => (
            <ColumnCard key={column.position} column={column} />
          ))}
        </div>
      </section>

      <ModelInput plan={plan} />
    </div>
  );
}

function QuestionList({
  title,
  items,
  label,
}: {
  title: string;
  items: Plan["analyses"];
  label: (column: string) => string;
}) {
  if (items.length === 0) return null;
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium text-muted">{title}</h3>
      <ol className="grid grid-cols-1 gap-2 md:grid-cols-2">
        {items.map((a) => (
          <li key={a.id} className="rounded-panel border border-border bg-surface px-4 py-3">
            <p className="font-medium">{a.question}</p>
            <p className="mt-1 text-xs text-muted">
              {minerLabel(a.kind)}: {a.columns.map(label).join(", ")}
              <span className="sr-only">. Suggested by {sourceLabel(a.source)}</span>
            </p>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** The follow-up questions, folded: dozens of cards would bury the ones asked first. */
function LaterQuestions({
  items,
  label,
}: {
  items: Plan["analyses"];
  label: (column: string) => string;
}) {
  return (
    <details className="group rounded-panel border border-border bg-surface">
      <summary className="flex min-h-11 cursor-pointer items-center gap-2 px-4 text-sm font-medium select-none">
        <CaretDown
          aria-hidden="true"
          className="size-4 shrink-0 text-muted transition-transform group-open:rotate-180 motion-reduce:transition-none"
        />
        {items.length === 1 ? "Then 1 more question" : `Then ${items.length} more questions`}
      </summary>
      <ol className="grid grid-cols-1 gap-x-8 border-t border-border px-4 py-3 md:grid-cols-2">
        {items.map((a) => (
          <li key={a.id} className="py-1.5">
            <p className="text-sm">{a.question}</p>
            <p className="text-xs text-muted">
              {minerLabel(a.kind)}: {a.columns.map(label).join(", ")}
            </p>
          </li>
        ))}
      </ol>
    </details>
  );
}

const DECISION: Record<string, { text: string; className: string }> = {
  accepted: { text: "Applied", className: "text-text" },
  rejected: { text: "Not applied", className: "text-muted" },
  pending: { text: "Needs your decision", className: "text-warning font-medium" },
};

function CleaningTable({
  operations,
  label,
}: {
  operations: readonly CleaningOperation[];
  label: (column: string) => string;
}) {
  if (operations.length === 0) {
    return <p className="text-muted">The data needed no cleaning.</p>;
  }
  return (
    <div
      role="region"
      aria-label="Cleaning changes"
      tabIndex={0}
      className="overflow-x-auto rounded-panel border border-border bg-surface"
    >
      <table className="w-full border-collapse text-left text-[13px]">
        <caption className="sr-only">Cleaning changes and their state</caption>
        <thead>
          <tr className="border-b border-border text-muted">
            <th scope="col" className="px-4 py-2 font-medium">
              Change
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Column
            </th>
            <th scope="col" className="px-4 py-2 text-right font-medium">
              Rows
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              State
            </th>
          </tr>
        </thead>
        <tbody>
          {operations.map((op) => {
            const state = DECISION[op.decision] ?? DECISION.rejected!;
            return (
              <tr key={op.id} className="border-b border-border last:border-b-0">
                <th scope="row" className="px-4 py-2.5 font-medium">
                  {op.label}
                  {op.lossy ? (
                    <span className="ml-2 text-xs font-normal text-muted">can remove data</span>
                  ) : null}
                </th>
                <td className="max-w-[16rem] truncate px-4 py-2.5 text-muted">
                  {op.column ? label(op.column) : "All rows"}
                </td>
                <td className="tabular px-4 py-2.5 text-right font-mono">
                  {formatCount(op.rows_affected)}
                </td>
                <td className={`px-4 py-2.5 whitespace-nowrap ${state.className}`}>{state.text}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ModelInput({ plan }: { plan: Plan }) {
  return (
    <section aria-labelledby="model-input-title" className="space-y-3">
      <h2 id="model-input-title" className="text-lg font-semibold">
        What the model saw
      </h2>
      <p className="max-w-[65ch] text-sm text-muted">
        The planning model is shown a description of your table, never its rows. Personal-data
        columns are reduced to their kind, numbers to rounded ranges without the lowest or highest
        value, and a category label appears only when several rows share it.
      </p>
      {plan.model_input ? (
        <details className="group rounded-panel border border-border bg-surface">
          <summary className="flex min-h-11 cursor-pointer items-center gap-2 px-4 text-sm font-medium select-none">
            Show the description exactly as it was sent
          </summary>
          <pre
            role="region"
            tabIndex={0}
            aria-label="The description sent to the model"
            className="max-h-[28rem] overflow-auto border-t border-border px-4 py-3 font-mono text-xs leading-relaxed"
          >
            {JSON.stringify(plan.model_input, null, 2)}
          </pre>
        </details>
      ) : null}
      {plan.rejected.length > 0 ? (
        <details className="rounded-panel border border-border bg-surface">
          <summary className="flex min-h-11 cursor-pointer items-center gap-2 px-4 text-sm font-medium select-none">
            {plan.rejected.length === 1
              ? "1 suggestion from the model was not used"
              : `${plan.rejected.length} suggestions from the model were not used`}
          </summary>
          <ul className="space-y-1.5 border-t border-border px-4 py-3 text-sm">
            {plan.rejected.map((r, i) => (
              <li key={i}>
                <span className="font-medium">{r.item}</span>
                <span className="text-muted">: {r.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mt-0.5 font-medium">{children}</dd>
    </div>
  );
}
