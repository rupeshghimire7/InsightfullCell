import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { DashboardView } from "@/components/report/dashboard-view";
import { Explorations } from "@/components/report/explorations";
import { ReportActions, ReportTabs } from "@/components/report/report-nav";
import { BackLink } from "@/components/ui/back-link";
import { EmptyState } from "@/components/ui/empty-state";
import { Hourglass } from "@/components/ui/icons";
import { LocalTime } from "@/components/ui/local-time";
import { PageHeader } from "@/components/ui/page-header";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { serverApi } from "@/lib/backend";
import { isUuid } from "@/lib/ids";
import { load } from "@/lib/load";
import { reportContext } from "@/lib/report/context";

export const metadata: Metadata = { title: "Dashboard" };

export default async function DashboardPage({
  params,
}: {
  params: Promise<{ org: string; runId: string }>;
}) {
  const { org, runId } = await params;
  if (!isUuid(runId)) notFound();
  const result = await load("this dashboard", () =>
    serverApi().GET("/v1/runs/{run_id}/dashboard", { params: { path: { run_id: runId } } }),
  );
  if (result.status === 404) notFound();
  const runHref = `/o/${org}/runs/${runId}`;
  const back = <BackLink href={runHref}>Back to the analysis</BackLink>;
  if (result.status === 409) {
    // A report written before dashboards existed: the report is the place to read it.
    if (result.error?.code === "dashboard-not-available") {
      redirect(`/o/${org}/runs/${runId}/report`);
    }
    return (
      <>
        <PageHeader title="Dashboard" back={back} />
        <EmptyState
          title="The dashboard is not ready yet"
          icon={Hourglass}
          action={
            <Link href={runHref} className="font-medium text-accent underline underline-offset-2">
              Follow the analysis
            </Link>
          }
        >
          It is assembled with the report when the analysis has finished. If the plan is waiting for
          your review, approve it first.
        </EmptyState>
      </>
    );
  }
  if (result.error) {
    return (
      <>
        <PageHeader title="Dashboard" back={back} />
        <ProblemAlert
          title={result.error.title ?? "The dashboard is unavailable"}
          detail={result.error.detail}
          hint={result.error.hint ?? "Reload the page to try again."}
          requestId={result.error.request_id}
        />
      </>
    );
  }
  const dashboard = result.data;
  const context = await reportContext(runId, dashboard.exploratory);
  return (
    <>
      <PageHeader
        title={dashboard.title}
        back={back}
        meta={
          <span className="text-muted">
            Assembled <LocalTime iso={dashboard.created_at} />
          </span>
        }
        actions={<ReportActions orgId={org} runId={runId} />}
      />
      <ReportTabs orgId={org} runId={runId} current="dashboard" />
      <DashboardView
        dashboard={dashboard}
        orgId={org}
        canExplore={context.canExplore}
        explorations={<Explorations orgId={org} runs={context.explorations} />}
      />
    </>
  );
}
