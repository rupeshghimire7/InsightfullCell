import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PlanOverview } from "@/components/plans/plan-overview";
import { BackLink } from "@/components/ui/back-link";
import { PageHeader } from "@/components/ui/page-header";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { serverApi } from "@/lib/backend";
import { isUuid } from "@/lib/ids";
import { load } from "@/lib/load";

export const metadata: Metadata = { title: "Analysis plan" };

export default async function PlanPage({
  params,
}: {
  params: Promise<{ org: string; runId: string }>;
}) {
  const { org, runId } = await params;
  if (!isUuid(runId)) notFound();
  const result = await load("this plan", () =>
    serverApi().GET("/v1/runs/{run_id}/plan", { params: { path: { run_id: runId } } }),
  );
  if (result.status === 404) notFound();
  const back = <BackLink href={`/o/${org}/runs/${runId}`}>Back to the analysis</BackLink>;
  if (result.error) {
    return (
      <>
        <PageHeader title="Analysis plan" back={back} />
        <ProblemAlert
          title={result.error.title ?? "The plan is unavailable"}
          detail={result.error.detail}
          hint={result.error.hint ?? "Reload the page to try again."}
          requestId={result.error.request_id}
        />
      </>
    );
  }
  const plan = result.data;
  return (
    <>
      <PageHeader
        title={`Plan for ${plan.run.dataset_name || "dataset"}`}
        back={back}
        description="What the analysis will look at, how the data will be cleaned, and exactly what the planning model was shown."
      />
      <PlanOverview plan={plan} orgId={org} />
    </>
  );
}
