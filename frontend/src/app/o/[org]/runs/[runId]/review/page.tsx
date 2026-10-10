import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ReviewForm } from "@/components/plans/review-form";
import { BackLink } from "@/components/ui/back-link";
import { EmptyState } from "@/components/ui/empty-state";
import { CheckCircle } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { serverApi } from "@/lib/backend";
import { isUuid } from "@/lib/ids";
import { load } from "@/lib/load";
import { LocalTime } from "@/components/ui/local-time";

export const metadata: Metadata = { title: "Review changes" };

/** Reviews expire after seven days (system map: When things fail). */
const REVIEW_DAYS = 7;

export default async function ReviewPage({
  params,
}: {
  params: Promise<{ org: string; runId: string }>;
}) {
  const { org, runId } = await params;
  if (!isUuid(runId)) notFound();
  const result = await load("this review", () =>
    serverApi().GET("/v1/runs/{run_id}/plan", { params: { path: { run_id: runId } } }),
  );
  if (result.status === 404) notFound();
  const runHref = `/o/${org}/runs/${runId}`;
  const back = <BackLink href={runHref}>Back to the analysis</BackLink>;
  if (result.error) {
    return (
      <>
        <PageHeader title="Review changes" back={back} />
        <ProblemAlert
          title={result.error.title ?? "The review is unavailable"}
          detail={result.error.detail}
          hint={result.error.hint ?? "Reload the page to try again."}
          requestId={result.error.request_id}
        />
      </>
    );
  }
  const plan = result.data;
  if (plan.run.status !== "awaiting_review") {
    return (
      <>
        <PageHeader title="Review changes" back={back} />
        <EmptyState
          title="Nothing is waiting for review"
          icon={CheckCircle}
          action={
            <Link href={runHref} className="font-medium text-accent underline underline-offset-2">
              Open the analysis
            </Link>
          }
        >
          This analysis has no cleaning changes waiting for a decision.
        </EmptyState>
      </>
    );
  }
  const requested = plan.run.review_requested_at;
  const deadline = requested
    ? new Date(Date.parse(requested) + REVIEW_DAYS * 86_400_000).toISOString()
    : null;
  return (
    <>
      <PageHeader
        title={`Review changes to ${plan.run.dataset_name || "the dataset"}`}
        back={back}
        description="Decide what happens to values that could be removed or replaced. The original file stays exactly as uploaded."
        meta={
          deadline ? (
            <span className="text-muted">
              Decide by <LocalTime iso={deadline} />; after that the analysis expires.
            </span>
          ) : null
        }
      />
      <ReviewForm plan={plan} orgId={org} />
    </>
  );
}
