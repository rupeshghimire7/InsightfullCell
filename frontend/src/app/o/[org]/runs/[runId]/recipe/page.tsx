import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { RecipeView } from "@/components/plans/recipe-view";
import { BackLink } from "@/components/ui/back-link";
import { PageHeader } from "@/components/ui/page-header";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { serverApi } from "@/lib/backend";
import { isUuid } from "@/lib/ids";
import { load } from "@/lib/load";

export const metadata: Metadata = { title: "Recipe" };

export default async function RecipePage({
  params,
}: {
  params: Promise<{ org: string; runId: string }>;
}) {
  const { org, runId } = await params;
  if (!isUuid(runId)) notFound();
  const path = { params: { path: { run_id: runId } } };
  const [plan, recipe] = await Promise.all([
    load("this plan", () => serverApi().GET("/v1/runs/{run_id}/plan", path)),
    load("this recipe", () =>
      serverApi().GET("/v1/runs/{run_id}/recipe", { ...path, parseAs: "text" }),
    ),
  ]);
  if (plan.status === 404) notFound();
  const back = <BackLink href={`/o/${org}/runs/${runId}/plan`}>Back to the plan</BackLink>;
  if (plan.error) {
    return (
      <>
        <PageHeader title="Recipe" back={back} />
        <ProblemAlert
          title={plan.error.title ?? "The recipe is unavailable"}
          detail={plan.error.detail}
          hint={plan.error.hint ?? "Reload the page to try again."}
          requestId={plan.error.request_id}
        />
      </>
    );
  }
  return (
    <>
      <PageHeader
        title={`Recipe for ${plan.data.run.dataset_name || "dataset"}`}
        back={back}
        description="How this analysis can be reproduced on your own computer, and exactly what the planning model was shown."
      />
      <RecipeView plan={plan.data} source={typeof recipe.data === "string" ? recipe.data : null} />
    </>
  );
}
