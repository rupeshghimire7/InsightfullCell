import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { readContent } from "@/lib/content";

export const metadata: Metadata = { title: "License" };

/** The licence, word for word, from `public/content/LICENSE.txt` (a copy of the repository's LICENSE). */
export default async function LicensePage() {
  const text = await readContent("LICENSE.txt");
  return (
    <>
      <PageHeader title="License" description="The terms InsightfulCell is offered under." />
      {text ? (
        <pre className="max-w-[80ch] rounded-panel border border-border bg-surface p-5 font-sans text-sm leading-relaxed break-words whitespace-pre-wrap">
          {text.trim()}
        </pre>
      ) : (
        <ProblemAlert
          title="The licence isn’t available"
          detail="The licence file couldn’t be read."
          hint="Try again later."
        />
      )}
    </>
  );
}
