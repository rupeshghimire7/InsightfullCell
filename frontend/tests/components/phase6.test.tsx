import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { axe } from "../axe";
import { DashboardView } from "@/components/report/dashboard-view";
import { Explorations } from "@/components/report/explorations";
import { ExploreProvider } from "@/components/report/explore";
import { FactsProvider, Sentences } from "@/components/report/facts";
import { ReportChart } from "@/components/report/report-chart";
import { ReportMap } from "@/components/report/report-map";
import { ReportTabs } from "@/components/report/report-nav";
import { ReportView } from "@/components/report/report-view";
import { ProblemError } from "@/lib/problem";
import type { RunsApi } from "@/lib/runs/api";
import type { RunSummary } from "@/lib/runs/types";
import { chart, dashboard, regionMap, report } from "../lib/report-fixtures";
import { run } from "../lib/runs-fixtures";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh: vi.fn() }) }));

describe("fact inspector", () => {
  it("opens a citation's fact and returns focus to the chip", async () => {
    const facts = report().facts;
    render(
      <FactsProvider facts={facts} sourceLinks>
        <p>
          <Sentences sentences={[{ text: "NSW leads.", citations: ["F1", "F9"] }]} />
        </p>
      </FactsProvider>,
    );
    // A citation of a fact the page does not hold reads as plain text.
    expect(screen.queryByRole("button", { name: "Source F9" })).not.toBeInTheDocument();
    const chip = screen.getByRole("button", { name: "Source F1" });
    await userEvent.click(chip);
    const dialog = await screen.findByRole("dialog", { name: "F1" });
    expect(within(dialog).getByText("NSW complaint rate is 31%.")).toBeInTheDocument();
    expect(within(dialog).getByText("Strong evidence")).toBeInTheDocument();
    expect(within(dialog).getByText("a".repeat(64))).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Show it with its answer" })).toHaveAttribute(
      "href",
      "#fact-F1",
    );
    expect(await axe(dialog)).toHaveNoViolations();
    await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(chip).toHaveFocus();
  });
});

describe("explore a group", () => {
  function api(drilldown: ReturnType<typeof vi.fn>): RunsApi {
    return { drilldown } as unknown as RunsApi;
  }

  it("confirms, starts the exploration and opens it", async () => {
    const drilldown = vi.fn().mockResolvedValue({ id: "child-1" });
    render(
      <ExploreProvider orgId="org-1" runId="run-1" enabled api={api(drilldown)}>
        <ReportChart chart={chart({ drill_column: "State", points: withKeys() })} />
      </ExploreProvider>,
    );
    await userEvent.click(screen.getByText("Show the numbers and explore a group"));
    await userEvent.click(screen.getByRole("button", { name: "Explore NSW on its own" }));
    const dialog = await screen.findByRole("dialog", { name: "Explore NSW on its own?" });
    expect(
      within(dialog).getByText(/only the rows where State is NSW \(120 rows here\)/),
    ).toBeInTheDocument();
    expect(await axe(dialog)).toHaveNoViolations();
    await userEvent.click(within(dialog).getByRole("button", { name: "Start exploring" }));
    expect(drilldown).toHaveBeenCalledWith("run-1", {
      column: "State",
      values: ["NSW"],
      idempotencyKey: expect.any(String),
    });
    expect(push).toHaveBeenCalledWith("/o/org-1/runs/child-1");
  });

  it("shows why an exploration was refused, and offers nothing to viewers", async () => {
    const drilldown = vi.fn().mockRejectedValue(
      new ProblemError({
        title: "This analysis cannot be explored further",
        status: 409,
        code: "drilldown-not-allowed",
        detail: "Only a finished analysis can be explored.",
      }),
    );
    const { unmount } = render(
      <ExploreProvider orgId="o" runId="r" enabled api={api(drilldown)}>
        <ReportChart chart={chart({ drill_column: "State", points: withKeys() })} />
      </ExploreProvider>,
    );
    await userEvent.click(screen.getByText("Show the numbers and explore a group"));
    await userEvent.click(screen.getByRole("button", { name: "Explore VIC on its own" }));
    await userEvent.click(await screen.findByRole("button", { name: "Start exploring" }));
    expect(
      await screen.findByText("Only a finished analysis can be explored."),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    unmount();
    render(
      <ExploreProvider orgId="o" runId="r" enabled={false}>
        <ReportChart chart={chart({ drill_column: "State", points: withKeys() })} />
      </ExploreProvider>,
    );
    expect(screen.getByText("Show the numbers")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Explore/ })).not.toBeInTheDocument();
  });
});

function withKeys() {
  return chart().points.map((p) => ({ ...p, key: p.label }));
}

describe("ReportChart extras", () => {
  it("says the chart was checked and offers its Vega-Lite spec", async () => {
    const createObjectURL = vi.fn(() => "blob:spec");
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const { container } = render(
      <ReportChart
        chart={chart({
          check: { claims: 2, verified: true },
          vega_lite: { $schema: "https://vega.github.io/schema/vega-lite/v5.json" },
        })}
      />,
    );
    expect(screen.getByText("All 2 values were checked against the data")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Download Vega-Lite spec" }));
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(await axe(container)).toHaveNoViolations();
    click.mockRestore();
  });
});

describe("ReportMap", () => {
  it("colours regions, hatches small ones and reads values by keyboard", async () => {
    const { container } = render(<ReportMap map={regionMap()} />);
    const map = screen.getByRole("img", { name: /Share of rows by State/ });
    expect(container.querySelectorAll("path[data-code]")).toHaveLength(4);
    const tas = container.querySelector<SVGPathElement>('path[data-code="AU-TAS"]');
    expect(tas?.style.fill).toMatch(/^url\(/);
    expect(screen.getByRole("list", { name: "Legend" })).toHaveTextContent("Fewer than ten rows");
    expect(screen.getByText("All 3 regions were checked against the data")).toBeInTheDocument();
    map.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(container.textContent).toMatch(/60%.*New South Wales, 60 rows/);
    await userEvent.click(screen.getByText("Show the numbers"));
    const table = screen.getByRole("region", { name: /Numbers behind/ });
    expect(within(table).queryByText("Western Australia")).not.toBeInTheDocument();
    expect(within(table).getByText("Tasmania")).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("explores a region from its row or by clicking it", async () => {
    const drilldown = vi.fn().mockResolvedValue({ id: "child-2" });
    const { container } = render(
      <ExploreProvider orgId="o" runId="r" enabled api={{ drilldown } as unknown as RunsApi}>
        <ReportMap map={regionMap()} />
      </ExploreProvider>,
    );
    const victoria = container.querySelector('path[data-code="AU-VIC"]') as SVGPathElement;
    await userEvent.click(victoria);
    const dialog = await screen.findByRole("dialog", { name: "Explore Victoria on its own?" });
    expect(within(dialog).getByText(/rows where State is Victoria/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Start exploring" }));
    expect(drilldown).toHaveBeenCalledWith("r", {
      column: "state",
      values: ["Victoria"],
      idempotencyKey: expect.any(String),
    });
  });
});

describe("DashboardView", () => {
  it("shows tiles, checked charts, maps and answered questions", async () => {
    const { container } = render(<DashboardView dashboard={dashboard()} orgId="org-1" />);
    expect(screen.getByText("Highest group share: NSW")).toBeInTheDocument();
    expect(screen.getAllByText("31%").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "Top findings" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Places" })).toBeInTheDocument();
    expect(screen.getByText("(1 withheld)")).toBeInTheDocument();
    await userEvent.click(screen.getAllByText("Do complaints differ by state?").at(-1)!);
    expect(screen.getByRole("link", { name: /The full answer, with its chart/ })).toHaveAttribute(
      "href",
      "/o/org-1/runs/" + dashboard().run.id + "/report#q-segment-State-Complaint",
    );
    expect(await axe(container)).toHaveNoViolations();
  }, 20_000);

  it("says plainly when there is nothing to chart, and marks an exploration", () => {
    render(
      <DashboardView
        dashboard={dashboard({
          tiles: [],
          insights: [],
          maps: [],
          gallery: [],
          questions: [],
          exploratory: true,
          scope: {
            parent_run_id: "parent-1",
            column: "state",
            display_name: "State",
            values: ["Victoria"],
            rows: 40,
          },
        })}
        orgId="org-1"
      />,
    );
    expect(screen.getByText("No notable patterns to chart")).toBeInTheDocument();
    expect(screen.getByText("Exploratory analysis of a subset")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the full analysis" })).toHaveAttribute(
      "href",
      "/o/org-1/runs/parent-1/report",
    );
  });
});

describe("report extras", () => {
  it("shows maps, withheld charts and exploratory facts", async () => {
    const base = report();
    const section = {
      ...base.sections[0]!,
      chart: null,
      chart_withheld: true,
      maps: [regionMap()],
    };
    const { container } = render(
      <ReportView
        report={{
          ...base,
          sections: [section],
          facts: base.facts.map((f) => ({ ...f, exploratory: true })),
          visuals: { charts: 3, withheld: 1, maps: 1, claims: 42 },
        }}
        orgId="org-1"
      />,
    );
    expect(screen.getByText(/A chart for this answer was withheld/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Share of rows by State/ })).toBeInTheDocument();
    expect(screen.getAllByText("Exploratory").length).toBeGreaterThan(0);
    expect(container.textContent).toMatch(/42 chart and map values were computed again/);
    expect(await axe(container)).toHaveNoViolations();
  }, 20_000);

  it("switches between the dashboard and the report", () => {
    render(<ReportTabs orgId="o" runId="r" current="report" />);
    expect(screen.getByRole("link", { name: "Report" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute(
      "href",
      "/o/o/runs/r/dashboard",
    );
  });

  it("lists explorations with their subset and status", () => {
    const child: RunSummary = {
      ...run({ status: "succeeded" }),
      drilldown: { parent_run_id: "p", column: "state", values: ["Victoria"] },
    };
    render(<Explorations orgId="o" runs={[child]} />);
    expect(screen.getByRole("link", { name: "state: Victoria" })).toHaveAttribute(
      "href",
      `/o/o/runs/${child.id}/dashboard`,
    );
  });
});
