import type { Metadata } from "next";
import Link from "next/link";
import { DatasetTable } from "@/components/datasets/dataset-table";
import { UploadDropzone } from "@/components/upload/upload-dropzone";
import { LogoMark } from "@/components/ui/brand";
import { EmptyState } from "@/components/ui/empty-state";
import { Database } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { serverApi } from "@/lib/backend";
import { load } from "@/lib/load";

export const metadata: Metadata = { title: "Home" };

const PAGE_SIZE = 25;

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
  const { data, error } = await load("your datasets", () =>
    serverApi().GET("/v1/datasets", { params: { query: { limit: PAGE_SIZE, cursor } } }),
  );

  return (
    <>
      <PageHeader
        leading={<LogoMark size={72} className="hidden drop-shadow-sm sm:block" />}
        title="Home"
        description="Upload a table, then analyse it."
      />
      <div className="space-y-10">
        <UploadDropzone orgId={org} />

        <section aria-labelledby="datasets-title" className="space-y-3">
          <h2 id="datasets-title" className="text-lg font-semibold">
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
            <EmptyState title="No datasets yet" icon={Database}>
              Upload a file above and it will appear here with its size, format, versions and latest
              analysis.
            </EmptyState>
          ) : (
            <>
              <DatasetTable datasets={data.items} orgId={org} />
              <nav aria-label="Dataset pages" className="flex items-center gap-4">
                {cursor ? (
                  <Link
                    href={home}
                    className="font-medium text-accent underline underline-offset-2"
                  >
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
    </>
  );
}
