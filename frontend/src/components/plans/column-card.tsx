import { CategoryShares, Histogram } from "@/components/plans/distribution";
import { MaskedBadge, MeaningBadge } from "@/components/plans/meaning-badge";
import { WarningCircle } from "@/components/ui/icons";
import { formatCount, formatNumber, formatPercent } from "@/lib/format";
import { flagLabel } from "@/lib/plans/labels";
import type { ColumnProfile, PlanColumn } from "@/lib/plans/types";

/** One column of the table: what it means, how full it is, its shape, and what is off. */
export function ColumnCard({ column }: { column: PlanColumn }) {
  const profile = column.profile as ColumnProfile;
  const title = column.display_name || column.name;
  const rows = profile.rows ?? 0;
  const present = rows - (profile.missing ?? 0);
  const masked = Boolean(column.personal_data);
  return (
    <article
      aria-labelledby={`col-${column.position}`}
      className="flex min-w-0 flex-col gap-3 rounded-panel border border-border bg-surface p-4"
    >
      <header className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id={`col-${column.position}`} className="truncate font-semibold" title={title}>
            {title}
          </h3>
          {column.display_name && column.display_name !== column.name ? (
            <p className="truncate font-mono text-xs text-muted" title={column.name}>
              {column.name}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
          {masked ? <MaskedBadge kind={column.personal_data ?? ""} /> : null}
          <MeaningBadge meaning={column.meaning} />
        </div>
      </header>

      <dl className="grid grid-cols-3 gap-2 text-xs">
        <Stat label="Filled" value={rows ? formatPercent(present / rows) : "None"} />
        <Stat label="Distinct" value={formatCount(profile.distinct ?? 0)} />
        <Stat
          label={column.unit ? "Unit" : "Unreadable"}
          value={column.unit ?? formatCount(profile.unparsed ?? 0)}
        />
      </dl>

      {masked ? (
        <p className="text-xs text-muted">
          Values are hidden here and never leave the worker. Only counts are kept.
        </p>
      ) : (
        <Shape column={column} profile={profile} present={present} />
      )}

      {column.flags.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5" aria-label="Things to check">
          {column.flags.map((flag) => (
            <li
              key={flag}
              className="inline-flex items-center gap-1 rounded-full border border-warning/35 bg-warning/10 px-2 py-0.5 text-xs"
            >
              <WarningCircle aria-hidden="true" className="size-3.5 text-warning" />
              {flagLabel(flag)}
            </li>
          ))}
        </ul>
      ) : null}
    </article>
  );
}

function Shape({
  column,
  profile,
  present,
}: {
  column: PlanColumn;
  profile: ColumnProfile;
  present: number;
}) {
  const numeric = profile.numeric;
  if (numeric && numeric.count > 0 && numeric.p50 !== null) {
    return (
      <div className="flex items-end justify-between gap-4">
        <dl className="grid grid-cols-3 gap-x-3 text-xs">
          <Stat
            label="Low (5%)"
            value={numeric.p05 === null ? "None" : formatNumber(numeric.p05)}
          />
          <Stat label="Median" value={formatNumber(numeric.p50)} />
          <Stat
            label="High (95%)"
            value={numeric.p95 === null ? "None" : formatNumber(numeric.p95)}
          />
        </dl>
        {numeric.histogram && numeric.histogram.length > 0 && numeric.histogram_range ? (
          <Histogram
            bins={numeric.histogram}
            range={numeric.histogram_range}
            label={column.display_name || column.name}
          />
        ) : null}
      </div>
    );
  }
  const dates = profile.datetime;
  if (dates && dates.count > 0) {
    return (
      <p className="text-xs text-muted">
        <span className="font-mono text-text">{(dates.p05 ?? "").slice(0, 10)}</span> to{" "}
        <span className="font-mono text-text">{(dates.p95 ?? "").slice(0, 10)}</span>, recorded by
        the {dates.granularity}
      </p>
    );
  }
  const top = profile.categorical?.top;
  if (top && top.length > 0 && column.meaning !== "identifier") {
    return <CategoryShares items={top} total={present} />;
  }
  if (profile.text) {
    return (
      <p className="text-xs text-muted">
        Free text, about{" "}
        <span className="font-mono text-text">{formatNumber(profile.text.words_p50 ?? 0)}</span>{" "}
        words per value.
      </p>
    );
  }
  return null;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-muted">{label}</dt>
      <dd className="tabular truncate font-mono text-[13px] font-medium">{value}</dd>
    </div>
  );
}
