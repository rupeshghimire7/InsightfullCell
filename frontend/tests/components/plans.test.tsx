import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { axe } from "../axe";
import { ColumnCard } from "@/components/plans/column-card";
import { CategoryShares, Histogram } from "@/components/plans/distribution";
import { RecipeView } from "@/components/plans/recipe-view";
import { PlanOverview, plannedBy } from "@/components/plans/plan-overview";
import { ReviewForm } from "@/components/plans/review-form";
import type { PlansApi } from "@/lib/plans/api";
import { ProblemError } from "@/lib/problem";
import { EMAIL, column, plan } from "../lib/plans-fixtures";

const push = vi.fn();
const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh }) }));

function fakeApi(decide: PlansApi["decide"] = vi.fn().mockResolvedValue({})): PlansApi {
  return {
    decide,
    revise: vi.fn().mockResolvedValue({}),
    recipeUrl: (id: string) => `/recipe/${id}`,
  };
}

describe("micro-charts", () => {
  it("draws one bar per bin with a hover title, from the baseline", async () => {
    const { container } = render(<Histogram bins={[1, 0, 4]} range={[0, 30]} label="Hours" />);
    const svg = screen.getByRole("img", { name: "Distribution of Hours, from 0 to 30" });
    const bars = svg.querySelectorAll("path[data-count]");
    expect(bars).toHaveLength(3);
    expect(bars[2]?.querySelector("title")?.textContent).toBe("20 to 30: 4 rows");
    expect(bars[1]?.getAttribute("d")).toBe(""); // an empty bin draws nothing
    expect(await axe(container)).toHaveNoViolations();
  });

  it("shows shares in text beside bars without tracks", () => {
    render(
      <CategoryShares
        items={[
          { value: "NSW", count: 3 },
          { value: "VIC", count: 1 },
        ]}
        total={4}
      />,
    );
    const rows = within(screen.getByRole("list", { name: "Most common values" })).getAllByRole(
      "listitem",
    );
    expect(rows[0]).toHaveTextContent("NSW75%");
    expect(rows[1]).toHaveTextContent("VIC25%");
  });
});

describe("ColumnCard", () => {
  it("summarises a measure with its range, histogram and flags", async () => {
    const { container } = render(<ColumnCard column={column()} />);
    expect(screen.getByRole("heading", { name: "Labour hours" })).toBeInTheDocument();
    expect(screen.getByText("Labour Hours")).toBeInTheDocument(); // the raw name under it
    expect(screen.getByText("3.72")).toBeInTheDocument();
    expect(screen.getByText("Placeholder codes")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Distribution of Labour hours/ })).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("shows only counts for a masked column", () => {
    render(<ColumnCard column={EMAIL} />);
    expect(screen.getByText("Masked")).toBeInTheDocument();
    expect(
      screen.getByText(/Values are hidden here and never leave the worker/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("76%")).toBeInTheDocument(); // filled share
  });
});

describe("PlanOverview", { timeout: 30_000 }, () => {
  it("says who planned it, what it will answer, and what the model saw", async () => {
    const { container } = render(<PlanOverview plan={plan()} orgId="org-1" />);
    expect(screen.getByText(/Planned with Gemini \(gemini-3\.8-flash\)/)).toBeInTheDocument();
    expect(screen.getByText("Do complaints follow longer jobs?")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review 2 changes" })).toHaveAttribute(
      "href",
      expect.stringMatching(/\/o\/org-1\/runs\/.+\/review$/),
    );
    expect(screen.getByRole("link", { name: "Show recipe" })).toHaveAttribute(
      "href",
      expect.stringMatching(/\/o\/org-1\/runs\/.+\/recipe$/),
    );
    expect(screen.queryByRole("link", { name: /Download recipe/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/exactly as it was sent/)).not.toBeInTheDocument();
    expect(screen.queryByText(/suggestion from the model was not used/)).not.toBeInTheDocument();
    expect(screen.getByText("Your data stays yours")).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("explains a rules fallback in plain words", () => {
    expect(plannedBy(plan({ source: "rules", fallback_reason: "disabled" }))).toBe(
      "Planned by the data rules, because no language model is configured",
    );
    expect(plannedBy(plan({ source: "cache" }))).toMatch(/^Reused a plan Gemini/);
  });
});

// The form re-renders on every keystroke and click; under a full parallel run these
// interaction-heavy tests can exceed the 5 s default, so they get a wider budget.
describe("ReviewForm", { timeout: 30_000 }, () => {
  it("holds approval until every lossy change is decided", async () => {
    const user = userEvent.setup();
    const decide = vi.fn().mockResolvedValue({});
    const { container } = render(<ReviewForm plan={plan()} orgId="org-1" api={fakeApi(decide)} />);
    const progress = screen.getByRole("region", { name: "Approve the plan" });
    expect(progress).toHaveTextContent("0 of 2 decided");

    await user.click(screen.getByRole("button", { name: "Approve and continue" }));
    expect(decide).not.toHaveBeenCalled();
    expect(screen.getByText("Decide every change first.")).toBeInTheDocument();
    const first = screen.getByRole("radiogroup", { name: /Set impossible values to empty/ });
    expect(within(first).getAllByRole("radio")[0]).toHaveFocus();

    await user.click(within(first).getByText("Apply"));
    const second = screen.getByRole("radiogroup", { name: /Drop exact duplicate rows/ });
    await user.click(within(second).getByText("Keep as is"));
    expect(progress).toHaveTextContent("2 of 2 decided");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("sends every decision, the outcome, names and features, then opens the run", async () => {
    const user = userEvent.setup();
    const decide = vi.fn().mockResolvedValue({});
    const current = plan();
    render(<ReviewForm plan={current} orgId="org-1" api={fakeApi(decide)} />);
    for (const group of screen.getAllByRole("radiogroup")) {
      await user.click(within(group).getByText("Apply"));
    }
    await user.click(screen.getByLabelText(/Cap extreme values/)); // turn an opt-in on
    await user.clear(screen.getByLabelText("Name in reports for Labour Hours"));
    await user.type(screen.getByLabelText("Name in reports for Labour Hours"), "Hours on site");
    await user.selectOptions(screen.getByLabelText("Outcome to explain"), "");
    await user.click(screen.getByRole("checkbox", { name: /Log scale of/ }));
    await user.click(screen.getByRole("button", { name: "Approve and continue" }));

    await waitFor(() => expect(decide).toHaveBeenCalledTimes(1), { timeout: 5_000 });
    const [runId, version, body] = decide.mock.calls[0]!;
    expect([runId, version]).toEqual([current.run.id, current.run.row_version]);
    expect(body.cleaning_revision).toBe(1);
    const decisions = Object.fromEntries(
      body.decisions.map((d: { operation_id: string; accept: boolean }) => [
        d.operation_id,
        d.accept,
      ]),
    );
    expect(decisions).toMatchObject({
      "set_invalid_to_null:Labour Hours": true,
      "drop_duplicates:*": true,
      "winsorize:Labour Hours": true,
      "exclude_column:Email": true,
    });
    expect(body.columns).toEqual([{ name: "Labour Hours", display_name: "Hours on site" }]);
    expect([body.target, body.no_target]).toEqual([null, true]);
    expect(body.disabled_features).toEqual(["log:Labour Hours"]);
    expect(push).toHaveBeenCalledWith(`/o/org-1/runs/${current.run.id}`);
  });

  it("keeps personal-data exclusions locked", () => {
    render(<ReviewForm plan={plan()} orgId="org-1" api={fakeApi()} />);
    expect(screen.getByLabelText(/Leave out of the analysis: Email/)).toBeDisabled();
    expect(screen.getByText("Masked, never analysed")).toBeInTheDocument();
    const outcome = screen.getByLabelText("Outcome to explain");
    expect(within(outcome).queryByText("Email")).not.toBeInTheDocument();
  });

  it("explains a stale view and offers a reload", async () => {
    const user = userEvent.setup();
    const decide = vi
      .fn()
      .mockRejectedValue(
        new ProblemError({ title: "The run changed", status: 412, code: "run-changed" }),
      );
    render(<ReviewForm plan={plan()} orgId="org-1" api={fakeApi(decide)} />);
    for (const group of screen.getAllByRole("radiogroup")) {
      await user.click(within(group).getByText("Keep as is"));
    }
    await user.click(screen.getByRole("button", { name: "Approve and continue" }));
    expect(
      await screen.findByText("This analysis changed since you opened it", {}, { timeout: 5_000 }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reload" }));
    expect(refresh).toHaveBeenCalled();
  });
});

describe("RecipeView", () => {
  it("keeps the download and the exact model input on the recipe page", async () => {
    const user = userEvent.setup();
    const { container } = render(<RecipeView plan={plan()} source="print('hi')" />);
    expect(screen.getByRole("link", { name: /Download recipe/ })).toHaveAttribute("download");
    await user.click(screen.getByText("Show the description exactly as it was sent"));
    expect(screen.getByText(/"card_version": "card-v1"/)).toBeVisible();
    expect(await axe(container)).toHaveNoViolations();
  });
});
