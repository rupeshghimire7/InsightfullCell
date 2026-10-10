import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { RunProgress } from "@/components/runs/run-progress";
import { BackLink } from "@/components/ui/back-link";
import { PageHeader } from "@/components/ui/page-header";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { serverApi } from "@/lib/backend";
import { isUuid } from "@/lib/ids";
import { load } from "@/lib/load";
import { LocalTime } from "@/components/ui/local-time";

export const metadata: Metadata = { title: "Analysis" };

export default async function RunPage({
  params,
}: {
  params: Promise<{ org: string; runId: string }>;
}) {
  const { org, runId } = await params;
  if (!isUuid(runId)) notFound();
  const result = await load("this analysis", () =>
    serverApi().GET("/v1/runs/{run_id}", { params: { path: { run_id: runId } } }),
  );
  if (result.status === 404) notFound();
  const back = <BackLink href={`/o/${org}/runs`}>All runs</BackLink>;

  if (result.error) {
    return (
      <>
        <PageHeader title="Analysis" back={back} />
        <ProblemAlert
          title={result.error.title ?? "This analysis is unavailable"}
          detail={result.error.detail}
          hint={result.error.hint ?? "Reload the page to try again."}
          requestId={result.error.request_id}
        />
      </>
    );
  }

  const run = result.data;
  return (
    <>
      <PageHeader
        title={`Analysis of ${run.dataset_name || "dataset"}`}
        back={back}
        meta={
          <span className="text-muted">
            Requested <LocalTime iso={run.created_at} />
            <span className="mx-2" aria-hidden="true">
              ·
            </span>
            <span className="font-mono text-xs">Run {run.id}</span>
          </span>
        }
      />
      {/* Keyed by run: a different run starts a fresh live view. */}
      <RunProgress key={run.id} initial={run} orgId={org} />
    </>
  );
}
