import Link from "next/link";
import { Compass } from "@/components/ui/icons";
import { formatCount } from "@/lib/format";
import type { Scope } from "@/lib/report/types";

/** Shown on an exploration's report and dashboard: which rows, and why its findings are leads. */
export function ScopeNotice({ scope, orgId }: { scope: Scope; orgId: string }) {
  return (
    <section
      aria-labelledby="scope-title"
      className="space-y-2 rounded-panel border border-warning/40 bg-warning/10 p-4 text-sm"
    >
      <div className="flex gap-3">
        <Compass aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-warning" />
        <div className="space-y-1">
          <h2 id="scope-title" className="font-semibold">
            Exploratory analysis of a subset
          </h2>
          <p className="max-w-[70ch]">
            Only the rows where {scope.display_name} is {scope.values.join(", ")} (
            <span className="tabular font-mono">{formatCount(scope.rows)}</span> rows after
            cleaning). This group was chosen after seeing the full results, so treat its findings as
            leads to check, not conclusions.
          </p>
          {scope.parent_run_id ? (
            <Link
              href={`/o/${orgId}/runs/${scope.parent_run_id}/report`}
              className="inline-flex min-h-9 items-center font-medium text-accent underline underline-offset-2 pointer-coarse:min-h-11"
            >
              Open the full analysis
            </Link>
          ) : null}
        </div>
      </div>
    </section>
  );
}
