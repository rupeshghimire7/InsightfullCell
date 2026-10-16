import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { axe } from "../axe";
import { ReportChart } from "@/components/report/report-chart";
import { ReportActions } from "@/components/report/report-nav";
import { ReportView } from "@/components/report/report-view";
import { SimplifiedNotice } from "@/components/report/simplified-notice";
import type { RunsApi } from "@/lib/runs/api";
import {
  effectLabel,
  formatChartValue,
  formatEffect,
  formatProbability,
  niceTicks,
} from "@/lib/report/format";
import { chart, report } from "../lib/report-fixtures";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh: vi.fn() }) }));

describe("ReportView", () => {
  it("shows answers with linked sources, the review and the privacy note", async () => {
    const { container } = render(<ReportView report={report()} orgId="org-1" />);
    expect(screen.getByText("Do complaints differ by state?")).toBeInTheDocument();
    expect(screen.getByText("You asked for this")).toBeInTheDocument();
    const chips = screen.getAllByRole("button", { name: "Source F1" });
    expect(chips[0]).toHaveAttribute("aria-haspopup", "dialog");
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
            sentences_kept: 3,
            sentences_dropped: 2,
            sentences_repaired: 1,
            simplified: false,
            retryable: false,
            judge: "off",
            rejected: { stray_number: 2, causal: 1 },
          },
          requests: [],
          removed_questions: 0,
        })}
        orgId="org-1"
      />,
    );
    expect(document.body.textContent).toMatch(
      /3 sentences kept; 1 of them after one rewrite; 2 sentences replaced by the fact's own wording\. Rejections by check: numbers 2, claims of cause 1\./,
    );
    expect(screen.queryByText("Narration simplified")).not.toBeInTheDocument();
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

  it("links to the plan and the recipe, and exports the report", () => {
    render(<ReportActions orgId="org-1" runId="run-1" />);
    expect(screen.getByRole("link", { name: "Show recipe" })).toHaveAttribute(
      "href",
      "/o/org-1/runs/run-1/recipe",
    );
    const formats = within(screen.getByRole("list", { name: "Download the report as" }));
    for (const [name, ext] of [
      [/^PDF/, "pdf"],
      [/^Word/, "docx"],
      [/^Markdown/, "md"],
      [/^HTML/, "html"],
    ] as const) {
      const link = formats.getByRole("link", { name });
      expect(link).toHaveAttribute("href", `/api/backend/v1/runs/run-1/report.${ext}`);
      expect(link).toHaveAttribute("download");
    }
    expect(screen.getByRole("button", { name: "Print" })).toBeInTheDocument();
  });

  it("opens the download menu and closes it with Escape", async () => {
    const user = userEvent.setup();
    const { container } = render(<ReportActions orgId="org-1" runId="run-1" />);
    const menu = container.querySelector("details")!;
    const summary = screen.getByText("Download").closest("summary")!;
    await user.click(summary); // what Enter or Space on a summary does in a browser
    expect(menu.open).toBe(true);
    expect(await axe(container)).toHaveNoViolations();
    await user.keyboard("{Escape}");
    expect(menu.open).toBe(false);
    expect(summary).toHaveFocus();
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

describe("SimplifiedNotice", () => {
  const simplified = {
    source: "engine" as const,
    provider: null,
    model: null,
    fallback_reason: "breaker_open",
    sentences_kept: 0,
    sentences_dropped: 0,
    sentences_repaired: 0,
    simplified: true,
    retryable: true,
    judge: "off" as const,
  };

  it("says the narration was simplified, and why, at the top of the report", async () => {
    const { container } = render(
      <ReportView report={report({ narration: simplified })} orgId="org-1" />,
    );
    expect(screen.getByRole("heading", { name: "Narration simplified" })).toBeInTheDocument();
    const notice = screen.getByRole("region", { name: "Narration simplified" });
    expect(within(notice).getByText(/the model provider has been failing/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Write the answers again" })).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  }, 20_000);

  it("writes the answers again and opens the run's progress", async () => {
    const renarrate = vi.fn().mockResolvedValue({ id: "run-1" });
    const api = { renarrate } as unknown as RunsApi;
    render(
      <SimplifiedNotice
        orgId="org-1"
        runId="run-1"
        rowVersion={7}
        reason="timeout"
        retryable
        api={api}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Write the answers again" }));
    expect(renarrate).toHaveBeenCalledWith("run-1", 7);
    expect(push).toHaveBeenCalledWith("/o/org-1/runs/run-1");
  });

  it("offers no retry when narrating again cannot help, and shows a refusal", async () => {
    const { rerender } = render(
      <SimplifiedNotice orgId="o" runId="r" rowVersion={1} reason="budget_run" retryable={false} />,
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    const renarrate = vi.fn().mockRejectedValue(new Error("offline"));
    rerender(
      <SimplifiedNotice
        orgId="o"
        runId="r"
        rowVersion={1}
        reason="timeout"
        retryable
        api={{ renarrate } as unknown as RunsApi}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Write the answers again" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
