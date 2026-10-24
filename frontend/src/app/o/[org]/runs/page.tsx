import type { Metadata } from "next";
import Link from "next/link";
import { RunTable } from "@/components/runs/run-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ListChecks } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { serverApi } from "@/lib/backend";
import { load } from "@/lib/load";

export const metadata: Metadata = { title: "Runs" };

const PAGE_SIZE = 25;

export default async function RunsPage({
  params,
  searchParams,
}: {
  params: Promise<{ org: string }>;
  searchParams: Promise<{ cursor?: string }>;
}) {
  const { org } = await params;
  const { cursor } = await searchParams;
  const base = `/o/${org}/runs`;
  const { data, error } = await load("your analyses", () =>
    serverApi().GET("/v1/runs", { params: { query: { limit: PAGE_SIZE, cursor } } }),
  );

  return (
    <>
      <PageHeader title="Runs" description="Every analysis in your workspace, newest first." />
      {error ? (
        <ProblemAlert
          title={error.title ?? "Runs are unavailable"}
          detail={error.detail ?? "We couldn’t load your analyses."}
          hint={error.hint ?? "Reload the page to try again."}
          requestId={error.request_id}
          action={
            <Link href={base} className="font-medium text-accent underline underline-offset-2">
              Reload
            </Link>
          }
        />
      ) : data.items.length === 0 && !cursor ? (
        <EmptyState
          title="No analyses yet"
          icon={ListChecks}
          action={
            <Link
              href={`/o/${org}`}
              className="font-medium text-accent underline underline-offset-2"
            >
              Go to your datasets
            </Link>
          }
        >
          Upload a file on the home page, then choose Analyse.
        </EmptyState>
      ) : (
        <div className="space-y-3">
          <RunTable runs={data.items} orgId={org} />
          <nav aria-label="Run pages" className="flex items-center gap-4">
            {cursor ? (
              <Link href={base} className="font-medium text-accent underline underline-offset-2">
                Back to newest
              </Link>
            ) : null}
            {data.next_cursor ? (
              <Link
                href={`${base}?cursor=${encodeURIComponent(data.next_cursor)}`}
                className="font-medium text-accent underline underline-offset-2"
              >
                Older runs
              </Link>
            ) : null}
          </nav>
        </div>
      )}
    </>
  );
}
