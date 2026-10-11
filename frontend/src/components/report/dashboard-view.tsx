import Link from "next/link";
import type { ReactNode } from "react";
import { EvidenceBadge } from "@/components/report/evidence";
import { ExploreProvider } from "@/components/report/explore";
import { Citation, FactsProvider, Sentences } from "@/components/report/facts";
import { ReportChart } from "@/components/report/report-chart";
import { ReportMap } from "@/components/report/report-map";
import { anchor } from "@/components/report/report-view";
import { ScopeNotice } from "@/components/report/scope-notice";
import { SimplifiedNotice } from "@/components/report/simplified-notice";
import { ArrowRight, CaretDown, ChatText } from "@/components/ui/icons";
import { formatCount } from "@/lib/format";
import type { Dashboard, Insight, Tile } from "@/lib/report/types";

/**
 * The dashboard: the findings at a glance. Headline numbers are each one fact's own number (a
 * chip opens the fact), the charts and maps were checked against the data, and the suggested
 * questions are answered with the report's verified sentences. Nothing here is new: it is the
 * report, arranged for a first look.
 */
export function DashboardView({
  dashboard,
  orgId,
  canExplore = false,
  explorations,
}: {
  dashboard: Dashboard;
  orgId: string;
  canExplore?: boolean;
  explorations?: ReactNode;
}) {
  const runId = dashboard.run.id;
  const reportHref = `/o/${orgId}/runs/${runId}/report`;
  const g = dashboard.glance;
  const empty =
    dashboard.tiles.length === 0 &&
    dashboard.insights.length === 0 &&
    dashboard.maps.length === 0 &&
    dashboard.gallery.length === 0;
  return (
    <FactsProvider facts={dashboard.facts}>
      <ExploreProvider orgId={orgId} runId={runId} enabled={canExplore && !dashboard.scope}>
        <div className="space-y-12">
          {dashboard.scope ? <ScopeNotice scope={dashboard.scope} orgId={orgId} /> : null}
          {dashboard.narration.simplified ? (
            <SimplifiedNotice
              orgId={orgId}
              runId={runId}
              rowVersion={dashboard.run.row_version}
              reason={dashboard.narration.fallback_reason}
              retryable={dashboard.narration.retryable}
            />
          ) : null}

          <section aria-labelledby="glance-title" className="space-y-4">
            <h2 id="glance-title" className="sr-only">
              At a glance
            </h2>
            {dashboard.tiles.length > 0 ? (
              <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {dashboard.tiles.map((tile) => (
                  <TileCard key={tile.fact_id} tile={tile} reportHref={reportHref} />
                ))}
              </ul>
            ) : null}
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3 border-y border-border py-3 text-sm sm:grid-cols-4">
              <Stat label="Rows analysed">{formatCount(g.rows)}</Stat>
              <Stat label="Columns">{formatCount(g.columns)}</Stat>
              <Stat label="Reliable findings">
                {formatCount(g.reliable)}
                <span className="font-sans font-normal text-muted">
                  {" "}
                  of {formatCount(g.tests)} tested
                </span>
              </Stat>
              <Stat label="Charts checked">
                {formatCount(g.charts_verified)}
                {g.charts_withheld > 0 ? (
                  <span className="font-sans font-normal text-muted">
                    {" "}
                    ({formatCount(g.charts_withheld)} withheld)
                  </span>
                ) : null}
              </Stat>
            </dl>
          </section>

          {empty ? (
            <section className="rounded-panel border border-border bg-surface p-6 text-sm">
              <h2 className="font-semibold">No notable patterns to chart</h2>
              <p className="mt-1 max-w-[65ch] text-muted">
                Nothing in this data passed the 5% false-discovery check strongly enough to put on a
                dashboard. The report still describes each column and every test that was run.
              </p>
              <Link
                href={reportHref}
                className="mt-3 inline-flex min-h-9 items-center font-medium text-accent underline underline-offset-2 pointer-coarse:min-h-11"
              >
                Read the report
              </Link>
            </section>
          ) : null}

          {dashboard.insights.length > 0 ? (
            <InsightGrid
              id="insights"
              title="Top findings"
              items={dashboard.insights}
              reportHref={reportHref}
            />
          ) : null}

          {dashboard.maps.length > 0 ? (
            <section aria-labelledby="maps-title" className="space-y-4">
              <h2 id="maps-title" className="text-lg font-semibold">
                Places
              </h2>
              <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
                {dashboard.maps.map((item) =>
                  item.map ? (
                    <article
                      key={`${item.analysis_id}-${item.map.title}`}
                      className="min-w-0 rounded-panel border border-border bg-surface p-5"
                    >
                      <ReportMap map={item.map} />
                    </article>
                  ) : null,
                )}
              </div>
            </section>
          ) : null}

          {dashboard.questions.length > 0 ? (
            <section aria-labelledby="questions-title" className="space-y-3">
              <div className="flex items-center gap-2">
                <ChatText aria-hidden="true" className="size-5 text-accent" />
                <h2 id="questions-title" className="text-lg font-semibold">
                  Suggested questions
                </h2>
              </div>
              <p className="max-w-[70ch] text-sm text-muted">
                Already answered: each answer is the report’s checked wording, with its sources.
              </p>
              <ul className="divide-y divide-border rounded-panel border border-border bg-surface">
                {dashboard.questions.map((q) => (
                  <li key={q.analysis_id}>
                    <details className="group">
                      <summary className="flex min-h-11 cursor-pointer items-center gap-3 px-4 py-2 font-medium select-none">
                        <CaretDown
                          aria-hidden="true"
                          className="size-4 shrink-0 text-muted transition-transform group-open:rotate-180 motion-reduce:transition-none"
                        />
                        {q.question}
                      </summary>
                      <div className="space-y-2 px-4 pb-4 pl-11">
                        <p className="max-w-[70ch] leading-relaxed">
                          <Sentences sentences={q.answer} />
                        </p>
                        <Link
                          href={`${reportHref}#${anchor(q.analysis_id)}`}
                          className="inline-flex min-h-9 items-center gap-1 text-sm font-medium text-accent underline-offset-2 hover:underline pointer-coarse:min-h-11"
                        >
                          The full answer, with its chart
                          <ArrowRight aria-hidden="true" className="size-3.5" />
                        </Link>
                      </div>
                    </details>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {dashboard.gallery.length > 0 ? (
            <InsightGrid
              id="gallery"
              title="More charts"
              items={dashboard.gallery}
              reportHref={reportHref}
            />
          ) : null}

          {explorations}
        </div>
      </ExploreProvider>
    </FactsProvider>
  );
}

function TileCard({ tile, reportHref }: { tile: Tile; reportHref: string }) {
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-panel border border-border bg-surface p-4">
      <p className="text-xs text-muted">{tile.label}</p>
      <p className="tabular font-mono text-[28px] leading-none font-semibold tracking-tight break-words">
        {tile.value}
        <Citation id={tile.fact_id} />
      </p>
      <Link
        href={`${reportHref}#${anchor(tile.analysis_id)}`}
        className="line-clamp-2 text-sm text-muted underline-offset-2 hover:text-text hover:underline"
      >
        {tile.question}
      </Link>
      <div className="mt-auto pt-1">
        <EvidenceBadge tier={tile.tier} />
      </div>
    </li>
  );
}

function InsightGrid({
  id,
  title,
  items,
  reportHref,
}: {
  id: string;
  title: string;
  items: readonly Insight[];
  reportHref: string;
}) {
  return (
    <section aria-labelledby={`${id}-title`} className="space-y-4">
      <h2 id={`${id}-title`} className="text-lg font-semibold">
        {title}
      </h2>
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        {items.map((item) =>
          item.chart ? (
            <article
              key={item.analysis_id}
              aria-labelledby={`${id}-${anchor(item.analysis_id)}`}
              className="flex min-w-0 flex-col gap-3 rounded-panel border border-border bg-surface p-5"
            >
              <div className="space-y-1">
                <p className="text-xs text-muted">{item.kind_label}</p>
                <h3 id={`${id}-${anchor(item.analysis_id)}`} className="font-semibold">
                  {item.question}
                </h3>
              </div>
              <ReportChart chart={item.chart} />
              <div className="mt-auto flex flex-wrap items-center justify-between gap-2 pt-1 text-sm">
                <span className="inline-flex flex-wrap items-center text-xs text-muted">
                  Sources
                  {item.facts.map((fid) => (
                    <Citation key={fid} id={fid} />
                  ))}
                </span>
                <Link
                  href={`${reportHref}#${anchor(item.analysis_id)}`}
                  className="inline-flex min-h-9 items-center gap-1 font-medium text-accent underline-offset-2 hover:underline pointer-coarse:min-h-11"
                >
                  Read the answer
                  <ArrowRight aria-hidden="true" className="size-3.5" />
                </Link>
              </div>
            </article>
          ) : null,
        )}
      </div>
    </section>
  );
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="tabular mt-0.5 truncate font-mono font-medium">{children}</dd>
    </div>
  );
}
