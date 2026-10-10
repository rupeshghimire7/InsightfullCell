"use client";

import { useRouter } from "next/navigation";
import {
  useId,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type ReactNode,
  type Ref,
} from "react";
import { Button } from "@/components/ui/button";
import {
  ArrowClockwise,
  CaretDown,
  Check,
  EyeSlash,
  Info,
  WarningCircle,
} from "@/components/ui/icons";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { formatCount } from "@/lib/format";
import { plansApi, type PlansApi } from "@/lib/plans/api";
import { featureLabel, personalLabel } from "@/lib/plans/labels";
import type { CleaningOperation, DecideRequest, Plan } from "@/lib/plans/types";
import { describeError, type Problem } from "@/lib/problem";

export interface ReviewFormProps {
  plan: Plan;
  orgId: string;
  /** Replaceable in tests. */
  api?: PlansApi;
}

type Choice = boolean | undefined;

interface ColumnDraft {
  display_name: string;
  unit: string;
  excluded: boolean;
}

/**
 * The review gate. Changes that could remove data wait for an explicit choice; everything
 * else is shown with its current state and can be switched. Approving records one new,
 * immutable cleaning plan revision and continues the analysis.
 */
export function ReviewForm({ plan, orgId, api = plansApi }: ReviewFormProps) {
  const router = useRouter();
  const formId = useId();
  const operations = useMemo(() => plan.cleaning?.operations ?? [], [plan.cleaning]);
  const pending = operations.filter((o) => o.decision === "pending");
  const automatic = operations.filter((o) => o.decision === "accepted");
  const off = operations.filter((o) => o.decision === "rejected");

  const [choices, setChoices] = useState<Record<string, Choice>>(() =>
    Object.fromEntries(
      operations.map((o) => [
        o.id,
        o.decision === "pending" ? undefined : o.decision === "accepted",
      ]),
    ),
  );
  const [columns, setColumns] = useState<Record<string, ColumnDraft>>(() =>
    Object.fromEntries(
      plan.dictionary.map((d) => [
        d.column,
        { display_name: d.display_name, unit: d.unit ?? "", excluded: d.role === "excluded" },
      ]),
    ),
  );
  const [target, setTarget] = useState<string>(plan.target?.column ?? "");
  const [features, setFeatures] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(plan.features.map((f) => [f.id, f.enabled])),
  );
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [nudge, setNudge] = useState(false);
  const firstUndecided = useRef<HTMLFieldSetElement | null>(null);

  const decided = pending.filter((o) => choices[o.id] !== undefined).length;
  const complete = decided === pending.length;
  const personal = new Set(plan.columns.filter((c) => c.personal_data).map((c) => c.name));
  const targetOptions = plan.dictionary.filter(
    (d) =>
      !personal.has(d.column) &&
      !columns[d.column]?.excluded &&
      d.meaning !== "identifier" &&
      d.meaning !== "text",
  );

  function set(id: string, value: boolean) {
    setChoices((current) => ({ ...current, [id]: value }));
    setNudge(false);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!complete) {
      setNudge(true);
      firstUndecided.current?.querySelector<HTMLInputElement>("input")?.focus();
      return;
    }
    if (!plan.cleaning) return;
    setBusy(true);
    setProblem(null);
    const edits: DecideRequest["columns"] = [];
    for (const entry of plan.dictionary) {
      const draft = columns[entry.column];
      if (!draft) continue;
      const change: NonNullable<DecideRequest["columns"]>[number] = { name: entry.column };
      if (draft.display_name.trim() && draft.display_name.trim() !== entry.display_name) {
        change.display_name = draft.display_name.trim();
      }
      if (draft.unit.trim() !== (entry.unit ?? "")) change.unit = draft.unit.trim();
      if (draft.excluded !== (entry.role === "excluded") && !personal.has(entry.column)) {
        change.excluded = draft.excluded;
      }
      if (Object.keys(change).length > 1) edits.push(change);
    }
    const body: DecideRequest = {
      cleaning_revision: plan.cleaning.revision,
      decisions: operations.map((o) => ({ operation_id: o.id, accept: choices[o.id] === true })),
      columns: edits,
      target: target || null,
      no_target: target === "",
      disabled_features: plan.features.filter((f) => !features[f.id]).map((f) => f.id),
    };
    try {
      await api.decide(plan.run.id, plan.run.row_version, body);
      router.push(`/o/${orgId}/runs/${plan.run.id}`);
      router.refresh();
    } catch (error) {
      setProblem(describeError(error));
      setBusy(false);
    }
  }

  const stale = problem && problem.status === 412;
  const firstOpen = pending.find((o) => choices[o.id] === undefined)?.id;

  return (
    <form id={formId} onSubmit={(e) => void submit(e)} className="space-y-12 pb-28" noValidate>
      <section aria-labelledby="decide-title" className="space-y-4">
        <div className="space-y-1">
          <h2 id="decide-title" className="text-lg font-semibold">
            Needs your decision
          </h2>
          <p className="max-w-[65ch] text-muted">
            These changes could remove or replace values. Nothing changes until you approve, and the
            original file is never modified.
          </p>
        </div>
        {pending.length === 0 ? (
          <p className="flex items-center gap-2 text-muted">
            <Check aria-hidden="true" className="size-4 text-accent" />
            No change needs a decision.
          </p>
        ) : (
          <ol className="space-y-3">
            {pending.map((op, i) => {
              const undecided = choices[op.id] === undefined;
              const attach = op.id === firstOpen;
              return (
                <li key={op.id} className="enter" style={{ "--i": i } as CSSProperties}>
                  <Decision
                    op={op}
                    choice={choices[op.id]}
                    onChange={(value) => set(op.id, value)}
                    highlight={nudge && undecided}
                    ref={attach ? firstUndecided : undefined}
                    columnLabel={
                      op.column ? columns[op.column]?.display_name || op.column : "All rows"
                    }
                  />
                </li>
              );
            })}
          </ol>
        )}
      </section>

      {automatic.length > 0 ? (
        <section aria-labelledby="auto-title" className="space-y-3">
          <h2 id="auto-title" className="text-lg font-semibold">
            Applied automatically
          </h2>
          <p className="max-w-[65ch] text-sm text-muted">
            Safe changes that keep every value: trimming, reading numbers and dates, unifying
            capitals. Open a group to untick any you do not want.
          </p>
          <Toggles
            ops={automatic}
            choices={choices}
            onChange={set}
            locked={(op) =>
              op.kind === "exclude_column" && op.column !== null && personal.has(op.column)
            }
          />
        </section>
      ) : null}

      {off.length > 0 ? (
        <section aria-labelledby="off-title" className="space-y-3">
          <h2 id="off-title" className="text-lg font-semibold">
            Off unless you turn them on
          </h2>
          <Toggles ops={off} choices={choices} onChange={set} locked={() => false} />
        </section>
      ) : null}

      <section aria-labelledby="columns-title" className="space-y-4">
        <div className="space-y-1">
          <h2 id="columns-title" className="text-lg font-semibold">
            Names, outcome and columns
          </h2>
          <p className="max-w-[65ch] text-sm text-muted">
            Your choices are saved for this dataset and used in its next analyses.
          </p>
        </div>
        <div className="max-w-md space-y-1.5">
          <label htmlFor="target" className="block text-sm font-medium">
            Outcome to explain
          </label>
          <select
            id="target"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            aria-describedby="target-help"
            className="block min-h-10 w-full rounded-control border border-border bg-surface px-3 text-sm pointer-coarse:min-h-11"
          >
            <option value="">No outcome: describe the data</option>
            {targetOptions.map((d) => (
              <option key={d.column} value={d.column}>
                {columns[d.column]?.display_name || d.column}
              </option>
            ))}
          </select>
          <p id="target-help" className="text-xs text-muted">
            The column the analysis tries to explain, such as whether a customer left.
          </p>
        </div>
        <details className="group rounded-panel border border-border bg-surface">
          <summary className="flex min-h-11 cursor-pointer items-center gap-2 px-4 text-sm font-medium select-none">
            <CaretDown
              aria-hidden="true"
              className="size-4 shrink-0 text-muted transition-transform group-open:rotate-180 motion-reduce:transition-none"
            />
            Rename columns, set units or leave columns out
            <span className="tabular ml-auto font-mono text-xs font-normal text-muted">
              {formatCount(plan.dictionary.length)} columns
            </span>
          </summary>
          <div
            role="region"
            aria-label="Column names and units"
            tabIndex={0}
            className="overflow-x-auto border-t border-border"
          >
            <table className="w-full border-collapse text-left text-sm">
              <caption className="sr-only">
                Column names, units and whether each is analysed
              </caption>
              <thead>
                <tr className="border-b border-border text-xs text-muted">
                  <th scope="col" className="px-4 py-2 font-medium">
                    Column
                  </th>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Name in reports
                  </th>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Unit
                  </th>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Analyse
                  </th>
                </tr>
              </thead>
              <tbody>
                {plan.dictionary.map((d, i) => {
                  const draft = columns[d.column];
                  if (!draft) return null;
                  const isPersonal = personal.has(d.column);
                  const update = (patch: Partial<ColumnDraft>) =>
                    setColumns((current) => ({ ...current, [d.column]: { ...draft, ...patch } }));
                  return (
                    <tr key={d.column} className="border-b border-border last:border-b-0">
                      <th scope="row" className="max-w-[14rem] px-4 py-2 align-middle font-normal">
                        <span className="block truncate font-mono text-xs" title={d.column}>
                          {d.column}
                        </span>
                      </th>
                      <td className="px-4 py-1.5">
                        <input
                          aria-label={`Name in reports for ${d.column}`}
                          value={draft.display_name}
                          maxLength={60}
                          onChange={(e) => update({ display_name: e.target.value })}
                          className="block min-h-9 w-full min-w-[10rem] rounded-control border border-border bg-surface px-2 text-sm pointer-coarse:min-h-11"
                        />
                      </td>
                      <td className="px-4 py-1.5">
                        <input
                          aria-label={`Unit for ${d.column}`}
                          value={draft.unit}
                          maxLength={16}
                          onChange={(e) => update({ unit: e.target.value })}
                          className="block min-h-9 w-20 rounded-control border border-border bg-surface px-2 font-mono text-sm pointer-coarse:min-h-11"
                        />
                      </td>
                      <td className="px-4 py-1.5">
                        {isPersonal ? (
                          <span
                            className="inline-flex items-center gap-1 text-xs text-muted"
                            title={personalLabel(
                              plan.columns.find((c) => c.name === d.column)?.personal_data,
                            )}
                          >
                            <EyeSlash aria-hidden="true" className="size-4" />
                            Masked, never analysed
                          </span>
                        ) : (
                          <label className="inline-flex min-h-9 cursor-pointer items-center gap-2 text-sm pointer-coarse:min-h-11">
                            <input
                              type="checkbox"
                              checked={!draft.excluded}
                              onChange={(e) => update({ excluded: !e.target.checked })}
                              className="size-4 accent-[var(--accent)]"
                              data-row={i}
                            />
                            <span className="sr-only">Analyse {d.column}</span>
                            <span aria-hidden="true">{draft.excluded ? "Left out" : "Yes"}</span>
                          </label>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </details>
      </section>

      {plan.features.length > 0 ? (
        <section aria-labelledby="features-title" className="space-y-3">
          <h2 id="features-title" className="text-lg font-semibold">
            Features to build
          </h2>
          <ul className="grid grid-cols-1 gap-x-6 gap-y-1 md:grid-cols-2">
            {plan.features.map((f) => (
              <li key={f.id}>
                <label className="flex min-h-9 cursor-pointer items-center gap-2 text-sm pointer-coarse:min-h-11">
                  <input
                    type="checkbox"
                    checked={features[f.id] ?? true}
                    onChange={(e) => setFeatures((c) => ({ ...c, [f.id]: e.target.checked }))}
                    className="size-4 accent-[var(--accent)]"
                  />
                  <span>
                    {featureLabel(f.kind)}
                    {f.part ? ` (${f.part.replaceAll("_", " ")})` : ""} of{" "}
                    {f.columns.map((c) => columns[c]?.display_name || c).join(" and ")}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <div
        role="region"
        aria-label="Approve the plan"
        className="fixed inset-x-0 bottom-0 z-20 border-t border-border bg-surface/95 backdrop-blur-sm supports-[backdrop-filter]:bg-surface/85"
      >
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0 text-sm" aria-live="polite">
            {pending.length === 0 ? (
              <span className="text-muted">Nothing needs a decision. Approve to continue.</span>
            ) : (
              <span className={complete ? "text-text" : "text-muted"}>
                <span className="tabular font-mono font-medium text-text">{decided}</span> of{" "}
                <span className="tabular font-mono">{pending.length}</span> decided
                {nudge && !complete ? (
                  <span className="ml-2 font-medium text-warning">Decide every change first.</span>
                ) : null}
              </span>
            )}
          </div>
          <Button
            type="submit"
            loading={busy}
            aria-describedby={complete ? undefined : `${formId}-why`}
          >
            {busy ? "Approving…" : "Approve and continue"}
          </Button>
          <span id={`${formId}-why`} className="sr-only">
            Every change marked as needing your decision must be applied or skipped first.
          </span>
        </div>
      </div>

      {problem ? (
        <ProblemAlert
          title={stale ? "This analysis changed since you opened it" : problem.title}
          detail={
            stale
              ? "Someone else may have decided already, or the plan was revised."
              : problem.detail
          }
          hint={stale ? "Reload to see the latest plan, then decide again." : problem.hint}
          requestId={problem.request_id}
          action={
            stale ? (
              <Button variant="secondary" onClick={() => router.refresh()}>
                <ArrowClockwise aria-hidden="true" className="size-4" />
                Reload
              </Button>
            ) : undefined
          }
        />
      ) : null}
    </form>
  );
}

interface DecisionProps {
  op: CleaningOperation;
  choice: Choice;
  onChange: (value: boolean) => void;
  highlight: boolean;
  columnLabel: string;
  ref?: Ref<HTMLFieldSetElement>;
}

function Decision({ op, choice, onChange, highlight, columnLabel, ref }: DecisionProps) {
  const name = useId();
  return (
    <fieldset
      ref={ref}
      className={`rounded-panel border bg-surface p-4 transition-colors sm:p-5 ${
        highlight ? "border-warning" : choice === undefined ? "border-warning/45" : "border-border"
      }`}
    >
      <legend className="sr-only">
        {op.label}: {columnLabel}
      </legend>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,27rem)]">
        <div className="min-w-0 space-y-2">
          <p className="font-semibold" aria-hidden="true">
            {op.label}
            <span className="font-normal text-muted"> · {columnLabel}</span>
          </p>
          <p className="max-w-[60ch] text-sm">{op.reason}</p>
          <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
            <span>
              Affects{" "}
              <span className="tabular font-mono text-text">{formatCount(op.rows_affected)}</span>{" "}
              {op.rows_affected === 1 ? "row" : "rows"}
            </span>
            {op.values_lost > 0 ? (
              <span className="inline-flex items-center gap-1 text-warning">
                <WarningCircle aria-hidden="true" className="size-3.5" />
                <span className="tabular font-mono">{formatCount(op.values_lost)}</span>{" "}
                {op.values_lost === 1 ? "value becomes" : "values become"} empty
              </span>
            ) : null}
            {op.source === "model" ? <span>Suggested by the planning model</span> : null}
          </p>
          <div
            role="radiogroup"
            aria-label={`${op.label}: ${columnLabel}`}
            className="flex flex-wrap gap-2 pt-1"
          >
            <ChoiceButton name={name} checked={choice === true} onSelect={() => onChange(true)}>
              Apply
            </ChoiceButton>
            <ChoiceButton name={name} checked={choice === false} onSelect={() => onChange(false)}>
              Keep as is
            </ChoiceButton>
          </div>
        </div>
        {op.preview.length > 0 ? <Preview op={op} /> : null}
      </div>
    </fieldset>
  );
}

function ChoiceButton({
  name,
  checked,
  onSelect,
  children,
}: {
  name: string;
  checked: boolean;
  onSelect: () => void;
  children: ReactNode;
}) {
  return (
    <label
      className={`press inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-control border px-3 text-sm font-medium has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-[var(--focus)] pointer-coarse:min-h-11 ${
        checked
          ? "border-accent bg-accent text-accent-contrast"
          : "border-border bg-surface hover:bg-bg"
      }`}
    >
      <input type="radio" name={name} checked={checked} onChange={onSelect} className="sr-only" />
      {checked ? <Check aria-hidden="true" weight="bold" className="size-4" /> : null}
      {children}
    </label>
  );
}

function Preview({ op }: { op: CleaningOperation }) {
  return (
    <div className="min-w-0 overflow-hidden rounded-control border border-border">
      <table className="w-full border-collapse text-left text-xs">
        <caption className="sr-only">Examples of the change</caption>
        <thead>
          <tr className="border-b border-border bg-bg text-muted">
            <th scope="col" className="px-3 py-1.5 font-medium">
              Now
            </th>
            <th scope="col" className="px-3 py-1.5 font-medium">
              After
            </th>
            <th scope="col" className="px-3 py-1.5 text-right font-medium">
              Rows
            </th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {op.preview.map((p, i) => (
            <tr key={i} className="border-b border-border last:border-b-0">
              <td className="max-w-[12rem] truncate px-3 py-1.5" title={p.before ?? undefined}>
                {p.before ?? <Empty />}
              </td>
              <td className="max-w-[12rem] truncate px-3 py-1.5" title={p.after ?? undefined}>
                {p.after ?? <Empty />}
              </td>
              <td className="tabular px-3 py-1.5 text-right">{formatCount(p.rows)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Empty() {
  return <span className="font-sans text-muted italic">empty</span>;
}

/** Operations of one kind together ("Trim spaces": 4 columns), in the order they first appear. */
function byLabel(ops: readonly CleaningOperation[]): [string, CleaningOperation[]][] {
  const groups = new Map<string, CleaningOperation[]>();
  for (const op of ops) groups.set(op.label, [...(groups.get(op.label) ?? []), op]);
  return [...groups.entries()];
}

/**
 * Many small changes, grouped by kind and folded: a long flat list hides the one change a
 * person wants to switch. Each group says how many of its changes are on.
 */
function Toggles({
  ops,
  choices,
  onChange,
  locked,
}: {
  ops: readonly CleaningOperation[];
  choices: Record<string, Choice>;
  onChange: (id: string, value: boolean) => void;
  locked: (op: CleaningOperation) => boolean;
}) {
  const prefix = useId(); // operation ids hold column names (spaces): never use them as DOM ids
  return (
    <div className="divide-y divide-border rounded-panel border border-border bg-surface">
      {byLabel(ops).map(([label, items], group) => {
        const on = items.filter((op) => choices[op.id] === true).length;
        return (
          <details key={label} className="group">
            <summary className="flex min-h-11 cursor-pointer items-center justify-between gap-3 px-4 text-sm select-none">
              <span className="flex min-w-0 items-center gap-2">
                <CaretDown
                  aria-hidden="true"
                  className="size-4 shrink-0 text-muted transition-transform group-open:rotate-180 motion-reduce:transition-none"
                />
                <span className="truncate font-medium">{label}</span>
              </span>
              <span className="shrink-0 text-xs text-muted">
                <span className="tabular font-mono text-text">{on}</span> of{" "}
                <span className="tabular font-mono">{items.length}</span> on
              </span>
            </summary>
            <ul className="grid grid-cols-1 gap-x-6 border-t border-border px-4 py-2 md:grid-cols-2">
              {items.map((op, index) => {
                const fixed = locked(op);
                const id = `${prefix}-${group}-${index}`;
                return (
                  <li key={op.id} className="flex items-start gap-3 py-1.5">
                    <input
                      id={id}
                      type="checkbox"
                      checked={choices[op.id] === true}
                      disabled={fixed}
                      onChange={(e) => onChange(op.id, e.target.checked)}
                      className="mt-1 size-4 accent-[var(--accent)]"
                    />
                    <label htmlFor={id} className="min-w-0 flex-1 text-sm">
                      <span className="sr-only">{op.label}: </span>
                      <span>{op.column ?? "All rows"}</span>
                      <span className="tabular ml-2 font-mono text-xs text-muted">
                        {formatCount(op.rows_affected)} {op.rows_affected === 1 ? "row" : "rows"}
                      </span>
                      {op.notice ? (
                        <span className="mt-0.5 flex items-start gap-1 text-xs text-muted">
                          <Info aria-hidden="true" className="mt-px size-3.5 shrink-0" />
                          {fixed ? "Personal data always stays out of the analysis." : op.notice}
                        </span>
                      ) : null}
                    </label>
                  </li>
                );
              })}
            </ul>
          </details>
        );
      })}
    </div>
  );
}
