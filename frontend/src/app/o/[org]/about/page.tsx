import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "About us" };

const PRINCIPLES = [
  {
    title: "Code computes",
    text: "Every statistic, test, chart value and map value is calculated by deterministic code.",
  },
  {
    title: "A language model only plans and writes",
    text: "It may suggest which questions to ask and phrase sentences. It never writes a number: the engine fills every number in from a stored fact.",
  },
  {
    title: "Everything is checked",
    text: "Plans are validated, charts are recomputed and every sentence is verified against its facts before anyone sees it.",
  },
];

export default function AboutPage() {
  return (
    <>
      <PageHeader
        title="About us"
        description="Your one stop solution for data analysis. Bring a spreadsheet. Leave with clear answers, charts and a report your team can check."
      />
      <div className="max-w-[70ch] space-y-8 leading-relaxed">
        <section aria-labelledby="idea-title" className="space-y-3">
          <h2 id="idea-title" className="text-lg font-semibold">
            The idea
          </h2>
          <p>
            Language models can handle almost any text, and newer systems can make predictions on
            tabular data without being retrained each time. So why not a system that gives a
            detailed analysis of your own data, keeps that data private, and needs no data analytics
            specialist?
          </p>
          <p>
            People with a spreadsheet want answers, and most of them have no statistics background.
            General-purpose AI assistants give fluent analyses, but their numbers can be made up and
            their data cleaning is invisible. Traditional BI and statistics tools are accurate, but
            they need a skilled analyst. InsightfulCell sets out to be as easy as asking an
            assistant and as trustworthy as a careful analyst.
          </p>
        </section>

        <section aria-labelledby="how-title" className="space-y-3">
          <h2 id="how-title" className="text-lg font-semibold">
            How it works
          </h2>
          <ul className="space-y-3">
            {PRINCIPLES.map((p) => (
              <li key={p.title} className="rounded-panel border border-border bg-surface p-4">
                <p className="font-medium">{p.title}</p>
                <p className="mt-1 text-muted">{p.text}</p>
              </li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="privacy-title" className="space-y-3">
          <h2 id="privacy-title" className="text-lg font-semibold">
            Your data
          </h2>
          <p>
            We respect your data privacy. Raw rows never reach an AI model: personal data is found
            first, a model sees only a masked description of the table, and every figure in a report
            links back to a stored fact you can open.
          </p>
        </section>
      </div>
    </>
  );
}
