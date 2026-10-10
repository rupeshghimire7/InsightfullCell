import Link from "next/link";
import type { ReactNode } from "react";
import { PrivacyNote } from "@/components/plans/privacy-note";
import { ReportChart } from "@/components/report/report-chart";
import {
  CaretDown,
  CheckCircle,
  Info,
  SealCheck,
  WarningCircle,
  type IconComponent,
} from "@/components/ui/icons";
import { formatCount } from "@/lib/format";
import { fallbackLabel, providerLabel } from "@/lib/plans/labels";
import { effectLabel, formatEffect, formatProbability, TIER } from "@/lib/report/format";
import type { Report, ReportFact, ReportSection, Sentence } from "@/lib/report/types";

/**
 * The finished report. Every sentence ends with links to the facts it rests on; each answer
 * lists those facts (what was computed, from which columns, over how many rows, how), so any
 * number on the page can be traced to its source without leaving it.
 */
export function ReportView({ report, orgId }: { report: Report; orgId: string }) {
  const facts = new Map(report.facts.map((f) => [f.id, f]));
  const answered = report.sections.filter((s) => !s.skipped);
  const skipped = report.sections.filter((s) => s.skipped);
  const runId = report.run.id;
  return (
    <div className="grid grid-cols-1 gap-10 lg:grid-cols-[minmax(0,1fr)_15rem]">
      <div className="min-w-0 space-y-12">
        <section
          aria-labelledby="summary-title"
          className="rounded-panel border border-border bg-surface p-5 sm:p-6"
        >
          <h2 id="summary-title" className="text-lg font-semibold">
            In short
          </h2>
          {report.summary.length > 0 ? (
            <p className="mt-3 max-w-[70ch] text-[15px] leading-relaxed">
              <Sentences sentences={report.summary} />
            </p>
          ) : (
            <p className="mt-3 text-muted">None of the questions could be answered on this data.</p>
          )}
        </section>

        {answered.length > 0 ? (
          <section aria-labelledby="answers-title" className="space-y-2">
            <h2 id="answers-title" className="text-lg font-semibold">
              Answers
            </h2>
            <div className="divide-y divide-border">
              {answered.map((section) => (
                <Answer key={section.analysis_id} section={section} facts={facts} />
              ))}
            </div>
          </section>
        ) : null}

        {skipped.length > 0 ? (
          <details className="group rounded-panel border border-border bg-surface">
            <summary className="flex min-h-11 cursor-pointer items-center gap-2 px-4 text-sm font-medium select-none">
              <CaretDown
                aria-hidden="true"
                className="size-4 shrink-0 text-muted transition-transform group-open:rotate-180 motion-reduce:transition-none"
              />
              {skipped.length === 1
                ? "1 question could not be answered"
                : `${skipped.length} questions could not be answered`}
            </summary>
            <ul className="space-y-2 border-t border-border px-4 py-3 text-sm">
              {skipped.map((s) => (
                <li key={s.analysis_id}>
                  <span className="font-medium">{s.question}</span>{" "}
                  <span className="text-muted">{s.skipped}</span>
                </li>
              ))}
            </ul>
          </details>
        ) : null}

        <HowItWasMade report={report} />
        <PrivacyNote recipeHref={`/o/${orgId}/runs/${runId}/recipe`} />
      </div>

      {answered.length > 1 ? (
        <nav aria-label="Questions in this report" className="hidden lg:block">
          <div className="sticky top-20 space-y-2">
            <p className="text-xs font-medium text-muted">On this page</p>
            <ol className="max-h-[70vh] space-y-1 overflow-y-auto pr-1 text-sm">
              {answered.map((s) => (
                <li key={s.analysis_id}>
                  <a
                    href={`#${anchor(s.analysis_id)}`}
                    className="block rounded-control px-2 py-1 text-muted hover:bg-surface hover:text-text"
                  >
                    {s.question}
                  </a>
                </li>
              ))}
            </ol>
          </div>
        </nav>
      ) : null}
    </div>
  );
}

function anchor(analysisId: string): string {
  return `q-${analysisId.replace(/[^A-Za-z0-9_-]+/g, "-")}`;
}

function Answer({ section, facts }: { section: ReportSection; facts: Map<string, ReportFact> }) {
  const cited = section.facts.map((id) => facts.get(id)).filter((f): f is ReportFact => !!f);
  return (
    <article
      id={anchor(section.analysis_id)}
      aria-labelledby={`${anchor(section.analysis_id)}-title`}
      className="space-y-4 py-8 first:pt-4"
    >
      <div className="space-y-1">
        <p className="flex flex-wrap items-center gap-x-3 text-xs text-muted">
          <span>{section.kind_label}</span>
          {section.requested_by_person ? (
            <span className="font-medium text-accent">You asked for this</span>
          ) : null}
        </p>
        <h3 id={`${anchor(section.analysis_id)}-title`} className="text-base font-semibold">
          {section.question}
        </h3>
      </div>
      <p className="max-w-[70ch] leading-relaxed">
        <Sentences sentences={section.answer} />
      </p>
      {section.chart ? <ReportChart chart={section.chart} /> : null}
      {cited.length > 0 ? (
        <div className="space-y-2">
          <h4 className="text-xs font-medium text-muted">Sources</h4>
          <ul className="space-y-2">
            {cited.map((fact) => (
              <li
                key={fact.id}
                id={`fact-${fact.id}`}
                className="rounded-control border border-border bg-surface px-3 py-2 text-sm transition-colors target:border-accent target:bg-accent/10 motion-reduce:transition-none"
              >
                <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
                  <p className="min-w-0 flex-1">
                    <span className="mr-2 font-mono text-xs font-semibold text-accent">
                      {fact.id}
                    </span>
                    {fact.statement}
                  </p>
                  <EvidenceBadge tier={fact.tier} />
                </div>
                <p className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted">
                  <span>
                    {fact.columns.length === 1 ? "Column" : "Columns"}:{" "}
                    {fact.columns.map((c) => c.display_name).join(", ")}
                  </span>
                  <span>
                    <span className="tabular font-mono">{formatCount(fact.rows)}</span>{" "}
                    {fact.rows === 1 ? "row" : "rows"}
                  </span>
                  <span>{fact.method}</span>
                </p>
                {fact.test ? <TestLine test={fact.test} /> : null}
                {fact.check && fact.check.outcome !== "same_data" ? (
                  <CheckLine check={fact.check} />
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </article>
  );
}

const TONE: Record<(typeof TIER)[keyof typeof TIER]["tone"], { badge: string; icon: string }> = {
  positive: { badge: "border-success/30 bg-success/10", icon: "text-success" },
  attention: { badge: "border-warning/40 bg-warning/10", icon: "text-warning" },
  neutral: { badge: "border-border bg-bg", icon: "text-muted" },
};
const TIER_ICON: Record<ReportFact["tier"], IconComponent> = {
  strong: SealCheck,
  supported: CheckCircle,
  inconclusive: WarningCircle,
  descriptive: Info,
};

/** How sure a fact is: an icon and words, from its test after the false-discovery control. */
function EvidenceBadge({ tier }: { tier: ReportFact["tier"] }) {
  const { label, tone } = TIER[tier];
  const Icon = TIER_ICON[tier];
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${TONE[tone].badge}`}
    >
      <Icon aria-hidden="true" weight="fill" className={`size-3.5 ${TONE[tone].icon}`} />
      {label}
    </span>
  );
}

function TestLine({ test }: { test: NonNullable<ReportFact["test"]> }) {
  const interval =
    test.ci_low !== null && test.ci_high !== null
      ? ` (95% interval ${formatEffect(test.ci_low)} to ${formatEffect(test.ci_high)})`
      : "";
  return (
    <p className="mt-1 text-xs text-muted">
      {test.method}: {effectLabel(test.effect_name)}{" "}
      <span className="tabular font-mono">{formatEffect(test.effect)}</span>
      {interval}; p-value{" "}
      <span className="tabular font-mono">{formatProbability(test.p_value)}</span> (
      <span className="tabular font-mono">{formatProbability(test.q_value)}</span> after the
      false-discovery control).
    </p>
  );
}

function CheckLine({ check }: { check: NonNullable<ReportFact["check"]> }) {
  const changed = check.outcome === "weakens" || check.outcome === "flips";
  const Icon = changed ? WarningCircle : CheckCircle;
  return (
    <p className={`mt-1 flex items-start gap-1 text-xs ${changed ? "text-warning" : "text-muted"}`}>
      <Icon aria-hidden="true" weight="fill" className="mt-px size-3.5 shrink-0" />
      {check.note}
    </p>
  );
}

/** Sentences with their citations: each id links to the fact it names. */
function Sentences({ sentences }: { sentences: Sentence[] }) {
  return (
    <>
      {sentences.map((sentence, i) => (
        <span key={i}>
          {i > 0 ? " " : null}
          {sentence.text}
          {sentence.citations.map((id) => (
            <a
              key={id}
              href={`#fact-${id}`}
              aria-label={`Source ${id}`}
              className="ml-1 inline-block rounded-[4px] border border-border px-1 align-[0.1em] font-mono text-[11px] leading-4 text-accent no-underline hover:border-accent"
            >
              {id}
            </a>
          ))}
        </span>
      ))}
    </>
  );
}

function HowItWasMade({ report }: { report: Report }) {
  const u = report.understanding;
  const p = report.preparation;
  const n = report.narration;
  const writer =
    n.source === "engine"
      ? `Written by our engine directly from the facts${n.fallback_reason ? `, because ${fallbackLabel(n.fallback_reason)}` : ""}.`
      : `Written by ${providerLabel(n.provider)}${n.model ? ` (${n.model})` : ""} from the facts, then checked by our engine: ${formatCount(n.sentences_kept)} ${n.sentences_kept === 1 ? "sentence was" : "sentences were"} kept${n.sentences_dropped ? ` and ${formatCount(n.sentences_dropped)} removed because a number, source or wording did not match the facts` : ""}.${n.source === "mixed" ? " Some answers were written by the engine instead." : ""}`;
  return (
    <section aria-labelledby="how-title" className="space-y-6">
      <h2 id="how-title" className="text-lg font-semibold">
        How this report was made
      </h2>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
        <Stat label="Rows analysed">{formatCount(u.rows)}</Stat>
        <Stat label="Columns">{formatCount(u.columns)}</Stat>
        <Stat label="Masked columns">{formatCount(u.masked_columns)}</Stat>
        <Stat label="Outcome">{u.target ?? "None chosen"}</Stat>
      </dl>
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <Block title="The table">
          {u.summary ? <p>{u.summary}</p> : null}
          {u.grain ? <p className="text-muted">One row is: {u.grain}</p> : null}
          <p className="text-muted">
            {u.planned_by === "rules"
              ? "Planned by our engine."
              : "Planned by our engine, then refined by the planning model."}
          </p>
        </Block>
        <Block title="Cleaning">
          <p>
            {p.applied.length === 1
              ? "1 change was applied"
              : `${formatCount(p.applied.length)} changes were applied`}
            {p.reviewed_by_person ? " after your review" : " by your organisation's policy"};{" "}
            <span className="tabular font-mono">{formatCount(p.rows_before)}</span> rows went in and{" "}
            <span className="tabular font-mono">{formatCount(p.rows_after)}</span> were analysed.
          </p>
          {p.applied.length > 0 ? (
            <details className="group">
              <summary className="inline-flex min-h-9 cursor-pointer items-center text-xs font-medium text-accent underline underline-offset-2 select-none pointer-coarse:min-h-11">
                Show the changes
              </summary>
              <ul className="mt-1 space-y-1 text-xs">
                {p.applied.map((step, i) => (
                  <li key={i}>
                    {step.label}
                    <span className="text-muted">: {step.column ?? "all rows"}</span>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </Block>
        <Block title="Your review">
          {report.requests.length === 0 && report.removed_questions === 0 ? (
            <p className="text-muted">You approved the plan as proposed.</p>
          ) : (
            <ul className="space-y-1">
              {report.requests.map((r) => (
                <li key={r.request}>
                  &ldquo;{r.request}&rdquo; <span className="text-muted">{r.note}</span>
                </li>
              ))}
              {report.removed_questions > 0 ? (
                <li className="text-muted">
                  {report.removed_questions === 1
                    ? "You removed 1 question."
                    : `You removed ${report.removed_questions} questions.`}
                </li>
              ) : null}
            </ul>
          )}
        </Block>
        <Block title="Writing">
          <p>{writer}</p>
        </Block>
        {report.evidence ? (
          <EvidenceBlock evidence={report.evidence} features={report.features ?? []} />
        ) : null}
      </div>
      <ul className="max-w-[75ch] space-y-1.5 text-sm text-muted">
        {report.method_notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </section>
  );
}

function EvidenceBlock({
  evidence: e,
  features,
}: {
  evidence: NonNullable<Report["evidence"]>;
  features: NonNullable<Report["features"]>;
}) {
  const kept = features.filter((f) => f.outcome === "kept").map((f) => f.label);
  const tested = features.filter((f) => f.outcome !== "no_target").length;
  return (
    <Block title="Evidence">
      <p>
        <span className="tabular font-mono">{formatCount(e.tests)}</span>{" "}
        {e.tests === 1 ? "comparison or link was" : "comparisons and links were"} tested;{" "}
        <span className="tabular font-mono">{formatCount(e.reliable)}</span>{" "}
        {e.reliable === 1 ? "is" : "are"} reliable, with false discoveries held at{" "}
        {Math.round(e.alpha * 100)}% across the whole analysis.
        {e.rechecked > 0
          ? ` The ${e.rechecked === 1 ? "strongest finding was" : `${formatCount(e.rechecked)} strongest findings were`} re-tested without the cleaning that removed or merged data.`
          : ""}
      </p>
      {tested > 0 ? (
        <p className="text-muted">
          {kept.length > 0
            ? `Extra measures kept because they carry signal: ${kept.join(", ")}.`
            : `None of the ${formatCount(tested)} extra measures told us more than chance, so none was added.`}
        </p>
      ) : null}
    </Block>
  );
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mt-0.5 truncate font-medium">{children}</dd>
    </div>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5 text-sm">
      <h3 className="font-medium">{title}</h3>
      {children}
    </div>
  );
}

export function reportLinks(orgId: string, runId: string) {
  return (
    <>
      <Link
        href={`/o/${orgId}/runs/${runId}/plan`}
        className="press inline-flex min-h-9 items-center justify-center rounded-control border border-border bg-surface px-4 text-sm font-medium hover:bg-bg pointer-coarse:min-h-11"
      >
        Open the plan
      </Link>
      <Link
        href={`/o/${orgId}/runs/${runId}/recipe`}
        className="press inline-flex min-h-9 items-center justify-center rounded-control border border-border bg-surface px-4 text-sm font-medium hover:bg-bg pointer-coarse:min-h-11"
      >
        Show recipe
      </Link>
    </>
  );
}
