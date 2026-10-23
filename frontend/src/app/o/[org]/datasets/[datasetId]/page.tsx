import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { FORMAT_LABEL } from "@/components/datasets/dataset-table";
import { RunTable } from "@/components/runs/run-table";
import { StartRunButton } from "@/components/runs/start-run-button";
import { BackLink } from "@/components/ui/back-link";
import { DeleteAction } from "@/components/ui/delete-action";
import { EmptyState } from "@/components/ui/empty-state";
import { ListChecks } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { serverApi } from "@/lib/backend";
import { formatBytes } from "@/lib/format";
import { load } from "@/lib/load";
import { isUuid } from "@/lib/ids";
import { LocalTime } from "@/components/ui/local-time";

export const metadata: Metadata = { title: "Dataset" };

export default async function DatasetPage({
  params,
}: {
  params: Promise<{ org: string; datasetId: string }>;
}) {
  const { org, datasetId } = await params;
  if (!isUuid(datasetId)) notFound();
  const api = serverApi();
  const [dataset, runs] = await Promise.all([
    load("this dataset", () =>
      api.GET("/v1/datasets/{dataset_id}", { params: { path: { dataset_id: datasetId } } }),
    ),
    load("its analyses", () =>
      api.GET("/v1/runs", { params: { query: { dataset_id: datasetId, limit: 10 } } }),
    ),
  ]);
  if (dataset.status === 404) notFound();
  const back = <BackLink href={`/o/${org}`}>All datasets</BackLink>;

  if (dataset.error) {
    return (
      <>
        <PageHeader title="Dataset" back={back} />
        <ProblemAlert
          title={dataset.error.title ?? "This dataset is unavailable"}
          detail={dataset.error.detail}
          hint={dataset.error.hint ?? "Reload the page to try again."}
          requestId={dataset.error.request_id}
        />
      </>
    );
  }

  const { dataset: info, versions } = dataset.data;
  const latest = versions[0];
  return (
    <>
      <PageHeader
        title={info.name}
        back={back}
        description={
          <>
            Added <LocalTime iso={info.created_at} />.{" "}
            {versions.length === 1 ? "1 version" : `${versions.length} versions`}; analyses use the
            newest.
          </>
        }
        actions={
          latest ? (
            <StartRunButton orgId={org} datasetId={info.id} label="Analyse newest version" />
          ) : null
        }
      />

      <div className="grid grid-cols-1 gap-10 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <section aria-labelledby="runs-title" className="min-w-0 space-y-3">
          <h2 id="runs-title" className="text-lg font-semibold">
            Analyses
          </h2>
          {runs.error ? (
            <ProblemAlert
              title={runs.error.title ?? "Analyses are unavailable"}
              detail={runs.error.detail}
              hint={runs.error.hint ?? "Reload the page to try again."}
              requestId={runs.error.request_id}
            />
          ) : runs.data.items.length === 0 ? (
            <EmptyState title="Not analysed yet" icon={ListChecks}>
              Start an analysis to verify the file and read it into typed columns. Progress shows
              live while it runs.
            </EmptyState>
          ) : (
            <RunTable runs={runs.data.items} orgId={org} showDataset={false} />
          )}
        </section>

        <section aria-labelledby="versions-title" className="space-y-3">
          <h2 id="versions-title" className="text-lg font-semibold">
            Versions
          </h2>
          <ol className="space-y-2">
            {versions.map((version, index) => (
              <li
                key={version.id}
                className="rounded-panel border border-border bg-surface p-3 text-sm"
              >
                <div className="flex items-baseline justify-between gap-3">
                  <p className="font-medium">
                    Version {versions.length - index}
                    {index === 0 ? <span className="ml-2 text-xs text-muted">newest</span> : null}
                  </p>
                  <p className="tabular text-muted">{formatBytes(version.bytes)}</p>
                </div>
                <p className="mt-1 text-muted">
                  {FORMAT_LABEL[version.format] ?? version.format}, uploaded{" "}
                  <LocalTime iso={version.created_at} />
                </p>
              </li>
            ))}
          </ol>
          <DeleteAction
            kind="dataset"
            id={info.id}
            redirectTo={`/o/${org}`}
            consequence="Its versions and every analysis of it, with their reports, go with it."
            className="pt-3"
          />
        </section>
      </div>
    </>
  );
}
