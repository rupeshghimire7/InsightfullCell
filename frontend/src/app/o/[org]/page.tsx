import type { Metadata } from "next";
import Link from "next/link";
import { DatasetTable } from "@/components/datasets/dataset-table";
import { UploadDropzone } from "@/components/upload/upload-dropzone";
import { EmptyState } from "@/components/ui/empty-state";
import { ProblemAlert } from "@/components/ui/problem-alert";
import type { components } from "@/lib/api/schema";
import { serverApi } from "@/lib/backend";
import type { Problem } from "@/lib/problem";

export const metadata: Metadata = { title: "Home" };

const PAGE_SIZE = 25;

type DatasetPage = components["schemas"]["DatasetPageOut"];

/**
 * One page of datasets, or the problem to show instead. Never throws for an outage, and never
 * redirects on 401: the session was just checked, so a rejected token is a configuration problem
 * (issuer, audience, keys) and redirecting to sign-in would loop.
 */
async function loadDatasets(
  cursor: string | undefined,
): Promise<
  { data: DatasetPage; error?: undefined } | { data?: undefined; error: Partial<Problem> }
> {
  try {
    const { data, error } = await serverApi().GET("/v1/datasets", {
      params: { query: { limit: PAGE_SIZE, cursor } },
    });
    if (data) return { data };
    return {
      error: {
        title: error?.title,
        detail: error?.detail ?? undefined,
        hint: error?.hint ?? (error?.status === 401 ? "Sign out, then sign in again." : undefined),
        request_id: error?.request_id ?? undefined,
      },
    };
  } catch (cause) {
    console.error(`[datasets] could not reach the API: ${String(cause)}`);
    return {
      error: {
        title: "The analysis service is unavailable",
        detail: "We couldn’t reach it to load your datasets.",
      },
    };
  }
}

export default async function HomePage({
  params,
  searchParams,
}: {
  params: Promise<{ org: string }>;
  searchParams: Promise<{ cursor?: string }>;
}) {
  // The layout has already checked the session; the API checks the token on every call.
  const { org } = await params;
  const { cursor } = await searchParams;
  const home = `/o/${org}`;
  const { data, error } = await loadDatasets(cursor);

  return (
    <div className="space-y-10">
      <h1 className="text-2xl font-semibold">Home</h1>
      <UploadDropzone />

      <section aria-labelledby="datasets-title" className="space-y-3">
        <h2 id="datasets-title" className="text-xl font-semibold">
          Your datasets
        </h2>
        {error ? (
          <ProblemAlert
            title={error.title ?? "Datasets are unavailable"}
            detail={error.detail ?? "We couldn’t load your datasets."}
            hint={error.hint ?? "Reload the page to try again."}
            requestId={error.request_id}
            action={
              <Link href={home} className="font-medium text-accent underline underline-offset-2">
                Reload
              </Link>
            }
          />
        ) : data.items.length === 0 && !cursor ? (
          <EmptyState title="No datasets yet">
            Upload a file above and it will appear here with its size, format and versions.
          </EmptyState>
        ) : (
          <>
            <DatasetTable datasets={data.items} />
            <nav aria-label="Dataset pages" className="flex items-center gap-4">
              {cursor ? (
                <Link href={home} className="font-medium text-accent underline underline-offset-2">
                  Back to newest
                </Link>
              ) : null}
              {data.next_cursor ? (
                <Link
                  href={`${home}?cursor=${encodeURIComponent(data.next_cursor)}`}
                  className="font-medium text-accent underline underline-offset-2"
                >
                  Older datasets
                </Link>
              ) : null}
            </nav>
          </>
        )}
      </section>
    </div>
  );
}
