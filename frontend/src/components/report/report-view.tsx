import type { ReactNode } from "react";
import { PrivacyNote } from "@/components/plans/privacy-note";
import { CheckLine, EvidenceBadge, ExploratoryBadge, TestLine } from "@/components/report/evidence";
import { ExploreProvider } from "@/components/report/explore";
import { FactsProvider, Sentences } from "@/components/report/facts";
import { ReportChart } from "@/components/report/report-chart";
import { ReportMap } from "@/components/report/report-map";
import { ScopeNotice } from "@/components/report/scope-notice";
import { SimplifiedNotice } from "@/components/report/simplified-notice";
import { CaretDown, EyeSlash } from "@/components/ui/icons";
import { formatCount } from "@/lib/format";
import { fallbackLabel, providerLabel } from "@/lib/plans/labels";
import type { Report, ReportFact, ReportSection } from "@/lib/report/types";

/**
 * The finished report. Every sentence ends with chips for the facts it rests on (each opens the
 * fact inspector); each answer also lists those facts (what was computed, from which columns,
 * over how many rows, how), so any number on the page can be traced to its source without
 * leaving it. Charts and maps were checked value by value against the data before being stored.
 */
export function ReportView({
  report,
  orgId,
  canExplore = false,
  explorations,
}: {
  report: Report;
  orgId: string;
  /** Analysts and above, on a full analysis: groups can be explored on their own. */
  canExplore?: boolean;
  /** Explorations of this analysis (drill-downs already started), shown at the end. */
  explorations?: ReactNode;
}) {
  const facts = new Map(report.facts.map((f) => [f.id, f]));
  const answered = report.sections.filter((s) => !s.skipped);
  const skipped = report.sections.filter((s) => s.skipped);
  const runId = report.run.id;
  return (
    <FactsProvider facts={report.facts} sourceLinks>
      <ExploreProvider orgId={orgId} runId={runId} enabled={canExplore && !report.scope}>
        <div className="grid grid-cols-1 gap-10 lg:grid-cols-[minmax(0,1fr)_15rem]">
          <div className="min-w-0 space-y-12">
            {report.scope ? <ScopeNotice scope={report.scope} orgId={orgId} /> : null}
            {report.narration.simplified ? (
              <SimplifiedNotice
                orgId={orgId}
                runId={runId}
                rowVersion={report.run.row_version}
                reason={report.narration.fallback_reason}
                retryable={report.narration.retryable}
              />
            ) : null}
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
                <p className="mt-3 text-muted">
                  None of the questions could be answered on this data.
                </p>
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

            {explorations}
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
      </ExploreProvider>
    </FactsProvider>
  );
}

export function anchor(analysisId: string): string {
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
      {section.chart_withheld ? (
        <p className="flex items-start gap-1.5 text-xs text-muted">
          <EyeSlash aria-hidden="true" className="mt-px size-3.5 shrink-0" />A chart for this answer
          was withheld: one of its values did not match the data when it was checked. The words and
          sources above are unaffected.
        </p>
      ) : null}
      {section.maps?.map((map) => (
        <ReportMap key={map.title} map={map} />
      ))}
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
                  <span className="flex flex-wrap gap-1.5">
                    <EvidenceBadge tier={fact.tier} />
                    {fact.exploratory ? <ExploratoryBadge /> : null}
                  </span>
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

const CHECK_LABELS: Record<string, string> = {
  form: "form",
  citation: "sources",
  stray_number: "numbers",
  direction: "direction",
  strength: "strength words",
  causal: "claims of cause",
  condition: "missing conditions",
  judge: "second opinion",
};

/** Who wrote the words and what the checks did, in one or two sentences. */
export function narrationText(n: Report["narration"]): string {
  if (n.source === "engine") {
    return `Written by our engine directly from the facts${n.fallback_reason ? `, because ${fallbackLabel(n.fallback_reason)}` : ""}.`;
  }
  const sentences = (k: number) => `${formatCount(k)} ${k === 1 ? "sentence" : "sentences"}`;
  const rejected = Object.entries(n.rejected ?? {})
    .filter(([, count]) => count > 0)
    .map(([check, count]) => `${CHECK_LABELS[check] ?? check} ${formatCount(count)}`);
  const parts = [
    `Written by ${providerLabel(n.provider)}${n.model ? ` (${n.model})` : ""} from the facts, with every number filled in by our engine. Each sentence was checked against its sources: ${sentences(n.sentences_kept)} kept`,
  ];
  if (n.sentences_repaired)
    parts.push(`${formatCount(n.sentences_repaired)} of them after one rewrite`);
  if (n.sentences_dropped) {
    parts.push(`${sentences(n.sentences_dropped)} replaced by the fact's own wording`);
  }
  let text = `${parts.join("; ")}.`;
  if (rejected.length > 0) text += ` Rejections by check: ${rejected.join(", ")}.`;
  if (n.source === "mixed") text += " Some parts were written by the engine instead.";
  return text;
}

function HowItWasMade({ report }: { report: Report }) {
  const u = report.understanding;
  const p = report.preparation;
  const n = report.narration;
  const writer = narrationText(n);
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
          <EvidenceBlock
            evidence={report.evidence}
            features={report.features ?? []}
            visuals={report.visuals ?? null}
          />
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
  visuals,
}: {
  evidence: NonNullable<Report["evidence"]>;
  features: NonNullable<Report["features"]>;
  visuals: Report["visuals"] | null;
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
      {visuals && visuals.claims > 0 ? (
        <p>
          <span className="tabular font-mono">{formatCount(visuals.claims)}</span> chart and map
          values were computed again from the data, one at a time, before they were shown
          {visuals.withheld > 0
            ? `; ${formatCount(visuals.withheld)} ${visuals.withheld === 1 ? "chart was" : "charts were"} withheld because a value did not match`
            : ""}
          .
        </p>
      ) : null}
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
