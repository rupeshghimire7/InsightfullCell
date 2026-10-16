import Link from "next/link";
import { DownloadMenu } from "@/components/report/download-menu";
import { PrintButton } from "@/components/report/print-button";

const SECONDARY =
  "press inline-flex min-h-9 items-center justify-center gap-2 rounded-control border border-border bg-surface px-4 text-sm font-medium hover:bg-bg pointer-coarse:min-h-11";

/** Switch between a finished analysis's dashboard and report. */
export function ReportTabs({
  orgId,
  runId,
  current,
  hasDashboard = true,
}: {
  orgId: string;
  runId: string;
  current: "dashboard" | "report";
  hasDashboard?: boolean;
}) {
  const tabs = [
    ...(hasDashboard ? [{ id: "dashboard", label: "Dashboard" } as const] : []),
    { id: "report", label: "Report" } as const,
  ];
  if (tabs.length < 2) return null;
  return (
    <nav aria-label="Views of this analysis" className="-mt-4 mb-8 print:hidden">
      <ul className="inline-flex rounded-control border border-border bg-surface p-0.5">
        {tabs.map((tab) => {
          const active = tab.id === current;
          return (
            <li key={tab.id}>
              <Link
                href={`/o/${orgId}/runs/${runId}/${tab.id}`}
                aria-current={active ? "page" : undefined}
                className={`inline-flex min-h-8 items-center rounded-[5px] px-4 text-sm font-medium pointer-coarse:min-h-11 ${
                  active ? "bg-accent text-accent-contrast" : "text-muted hover:text-text"
                }`}
              >
                {tab.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** Download the report (PDF, Word, Markdown or HTML) or print it, and open the plan or recipe. */
export function ReportActions({ orgId, runId }: { orgId: string; runId: string }) {
  return (
    <div className="flex flex-wrap gap-2 print:hidden">
      <DownloadMenu runId={runId} />
      <PrintButton />
      <Link href={`/o/${orgId}/runs/${runId}/plan`} className={SECONDARY}>
        Open the plan
      </Link>
      <Link href={`/o/${orgId}/runs/${runId}/recipe`} className={SECONDARY}>
        Show recipe
      </Link>
    </div>
  );
}
