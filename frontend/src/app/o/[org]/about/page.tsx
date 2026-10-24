import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "About InsightfulCell" };

const PRINCIPLES = [
  {
    title: "Code computes",
    text: "Every statistic, chart value and map value is calculated by deterministic code.",
  },
  {
    title: "A language model only plans and writes",
    text: "It never writes a number: every number comes from a stored fact.",
  },
  {
    title: "Everything is checked",
    text: "Plans are validated, charts recomputed and every sentence verified before anyone sees it.",
  },
];

export default function AboutPage() {
  return (
    <>
      <PageHeader
        title="About InsightfulCell"
        description="Your one stop solution for data analysis."
      />
      <ul className="max-w-[70ch] space-y-3">
        {PRINCIPLES.map((p) => (
          <li key={p.title} className="rounded-panel border border-border bg-surface p-4">
            <p className="font-medium">{p.title}</p>
            <p className="mt-1 text-muted">{p.text}</p>
          </li>
        ))}
      </ul>
    </>
  );
}
