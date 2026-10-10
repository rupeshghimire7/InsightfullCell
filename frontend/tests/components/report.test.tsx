import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { axe } from "../axe";
import { ReportChart } from "@/components/report/report-chart";
import { ReportView, reportLinks } from "@/components/report/report-view";
import {
  effectLabel,
  formatChartValue,
  formatEffect,
  formatProbability,
  niceTicks,
} from "@/lib/report/format";
import { chart, report } from "../lib/report-fixtures";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));

describe("ReportView", () => {
  it("shows answers with linked sources, the review and the privacy note", async () => {
    const { container } = render(<ReportView report={report()} orgId="org-1" />);
    expect(screen.getByText("Do complaints differ by state?")).toBeInTheDocument();
    expect(screen.getByText("You asked for this")).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: "Source F1" });
    expect(links[0]).toHaveAttribute("href", "#fact-F1");
    expect(screen.getByText("NSW complaint rate is 31%.")).toBeInTheDocument();
    expect(screen.getByText("1 question could not be answered")).toBeInTheDocument();
    expect(screen.getByText("You removed 1 question.")).toBeInTheDocument();
    expect(screen.getByText("Your data stays yours")).toBeInTheDocument();
    expect(screen.queryByText(/Download/)).not.toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  }, 20_000);

  it("explains a model-written report and an empty one", () => {
    const { rerender } = render(
      <ReportView
        report={report({
          narration: {
            source: "mixed",
            provider: "gemini",
            model: "gemini-3.8-flash",
            fallback_reason: null,
            sentences_kept: 1,
            sentences_dropped: 2,
          },
          requests: [],
          removed_questions: 0,
        })}
        orgId="org-1"
      />,
    );
    expect(screen.getByText(/2 removed because/)).toBeInTheDocument();
    expect(screen.getByText("You approved the plan as proposed.")).toBeInTheDocument();
    rerender(<ReportView report={report({ summary: [], sections: [] })} orgId="org-1" />);
    expect(screen.getByText(/None of the questions could be answered/)).toBeInTheDocument();
  });

  it("shows each source's evidence, its test and whether it holds with less cleaning", () => {
    render(<ReportView report={report()} orgId="org-1" />);
    expect(screen.getByText("Strong evidence")).toBeInTheDocument();
    expect(document.body.textContent).toMatch(
      /Kruskal-Wallis test: Eta 0\.35 \(95% interval 0\.32 to 0\.38\); p-value below 0\.001 \(below 0\.001 after the false-discovery control\)/,
    );
    expect(screen.getByText("(95% interval 0.32 to 0.38)", { exact: false })).toBeInTheDocument();
    expect(screen.getAllByText("below 0.001")).toHaveLength(2);
    expect(
      screen.getByText("On minimally cleaned data its direction changes."),
    ).toBeInTheDocument();
    expect(document.body.textContent).toMatch(
      /12 comparisons and links were tested; 3 are reliable/,
    );
    expect(screen.getByText(/Weekday of Job date/)).toBeInTheDocument();
  });

  it("marks unreliable and descriptive sources in words, not only colour", () => {
    const base = report();
    const [fact] = base.facts;
    render(
      <ReportView
        report={{
          ...base,
          facts: [
            { ...fact!, tier: "inconclusive", check: null },
            { ...fact!, id: "F2", tier: "descriptive", test: null, check: null },
          ],
          sections: [{ ...base.sections[0]!, facts: ["F1", "F2"] }],
          features: [{ label: "Log of Hours", outcome: "no_signal", redundant_with: null }],
        }}
        orgId="org-1"
      />,
    );
    expect(screen.getByText("Not reliable")).toBeInTheDocument();
    expect(screen.getByText("Descriptive")).toBeInTheDocument();
    expect(screen.getByText(/None of the 1 extra measures/)).toBeInTheDocument();
  });

  it("links to the plan and the recipe", () => {
    render(reportLinks("org-1", "run-1"));
    expect(screen.getByRole("link", { name: "Show recipe" })).toHaveAttribute(
      "href",
      "/o/org-1/runs/run-1/recipe",
    );
  });
});

describe("ReportChart", () => {
  it("lists every bar value in a table", async () => {
    const { container } = render(<ReportChart chart={chart()} />);
    expect(screen.getByRole("list", { name: /Complaint rate by state/ })).toBeInTheDocument();
    await userEvent.click(screen.getByText("Show the numbers"));
    expect(screen.getByRole("region", { name: /Numbers behind/ })).toBeInTheDocument();
    expect(screen.getAllByText("31%").length).toBeGreaterThan(0);
    expect(await axe(container)).toHaveNoViolations();
  });

  it("draws a line chart that reads out values by keyboard", async () => {
    const line = chart({
      kind: "line",
      title: "Cost over time",
      x_label: "Month",
      y_label: "Cost",
      value_format: "number",
      unit: "$",
      points: [
        { label: "Jan", value: 10, n: null, x: 1, x2: null },
        { label: "Feb", value: 25, n: null, x: 2, x2: null },
        { label: "Mar", value: 18, n: null, x: 3, x2: null },
      ],
    });
    const { container } = render(<ReportChart chart={line} />);
    const plot = screen.getByRole("img", { name: /Cost over time/ });
    plot.focus();
    await userEvent.keyboard("{ArrowRight}{ArrowRight}");
    expect(container.textContent).toMatch(/\$25|Feb/);
    await userEvent.keyboard("{Home}{End}{Escape}");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("draws scatter and histogram charts", async () => {
    const points = [
      { label: "a", value: 2, n: null, x: 1, x2: null },
      { label: "b", value: 5, n: null, x: 4, x2: null },
    ];
    const scatter = render(
      <ReportChart chart={chart({ kind: "scatter", value_format: "number", points })} />,
    );
    expect(await axe(scatter.container)).toHaveNoViolations();
    scatter.unmount();
    const hist = render(
      <ReportChart
        chart={chart({
          kind: "histogram",
          value_format: "count",
          points: [
            { label: "0 to 5", value: 3, n: null, x: 0, x2: 5 },
            { label: "5 to 10", value: 1, n: null, x: 5, x2: 10 },
          ],
        })}
      />,
    );
    expect(await axe(hist.container)).toHaveNoViolations();
  });
});

describe("chart formatting", () => {
  it("writes values the way the facts do", () => {
    expect(formatChartValue(0.314, "percent", null)).toBe("31%");
    expect(formatChartValue(0.05, "percent", null)).toBe("5.0%");
    expect(formatChartValue(0.2, "signed_percent", null)).toBe("+20%");
    expect(formatChartValue(-0.2, "signed_percent", null)).toBe("-20%");
    expect(formatChartValue(1234.4, "count", null)).toBe("1,234");
    expect(formatChartValue(0.456, "strength", null)).toBe("0.46");
    expect(formatChartValue(1234.5, "number", "$")).toBe("$1,235");
    expect(formatChartValue(-12.34, "number", "$")).toBe("-$12.3");
    expect(formatChartValue(3.14159, "number", "kg")).toBe("3.14 kg");
    expect(formatChartValue(Number.NaN, "number", null)).toBe("n/a");
  });

  it("writes test results plainly", () => {
    expect(formatProbability(0.0004)).toBe("below 0.001");
    expect(formatProbability(0.0123)).toBe("0.012");
    expect(formatEffect(-0.001)).toBe("0.00");
    expect(formatEffect(12.345)).toBe("12.3");
    expect(formatEffect(Number.NaN)).toBe("n/a");
    expect(effectLabel("cramers_v")).toBe("Cramér's V");
    expect(effectLabel("odd_name")).toBe("odd name");
  });

  it("picks round ticks", () => {
    expect(niceTicks(0, 100)).toEqual([0, 25, 50, 75, 100]);
    expect(niceTicks(5, 5)).toEqual([5]);
  });
});
