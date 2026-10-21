import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { axe } from "../axe";
import {
  clampWidth,
  DataSidebar,
  DEFAULT_WIDTH,
  MAX_WIDTH,
  MIN_WIDTH,
} from "@/components/report/data-sidebar";

function page(offset: number, total = 250) {
  const count = Math.max(0, Math.min(100, total - offset));
  return {
    columns: [
      { name: "region", type: "VARCHAR", personal_data: null },
      { name: "sales", type: "DOUBLE", personal_data: null },
      { name: "Email", type: "VARCHAR", personal_data: "email" },
    ],
    rows: Array.from({ length: count }, (_, i) => [i % 2 ? "North" : null, offset + i + 0.5, null]),
    total_rows: total,
    offset,
  };
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url: string) => {
    const offset = Number(new URL(url, "http://x").searchParams.get("offset"));
    return new Response(JSON.stringify(page(offset)), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// A 100-row table in jsdom (and axe over it) is slow under coverage on a busy machine.
describe("DataSidebar", { timeout: 60_000 }, () => {
  it("opens beside the report with the rows, personal data hidden, and passes axe", async () => {
    const user = userEvent.setup();
    const { container } = render(<DataSidebar runId="run-1" />);
    const trigger = screen.getByRole("button", { name: "See the data" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(fetchMock).not.toHaveBeenCalled(); // nothing is read until it is opened

    await user.click(trigger);
    const panel = screen.getByRole("complementary", { name: "The data" });
    expect(await within(panel).findByText("Rows 1–100 of 250")).toBeInTheDocument();
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/backend/v1/runs/run-1/rows?offset=0&limit=100");
    expect(screen.getByRole("button", { name: "Hide the data" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(within(panel).getByRole("columnheader", { name: /Email.*Hidden: email/ })).toBeVisible();
    expect(within(panel).getAllByText("hidden")).toHaveLength(100);
    expect(within(panel).getAllByText("empty").length).toBeGreaterThan(0);
    expect(document.body.dataset.dataPanel).toBe("open");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("pages through the rows", async () => {
    const user = userEvent.setup();
    render(<DataSidebar runId="run-1" />);
    await user.click(screen.getByRole("button", { name: "See the data" }));
    const panel = screen.getByRole("complementary", { name: "The data" });
    await within(panel).findByText("Rows 1–100 of 250");
    expect(within(panel).getByRole("button", { name: "Previous" })).toBeDisabled();
    await user.click(within(panel).getByRole("button", { name: "Next" }));
    await within(panel).findByText("Rows 101–200 of 250");
    await user.click(within(panel).getByRole("button", { name: "Next" }));
    await within(panel).findByText("Rows 201–250 of 250");
    expect(within(panel).getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("closes with the arrow or Escape and reopens from the edge tab without reloading", async () => {
    const user = userEvent.setup();
    render(<DataSidebar runId="run-1" />);
    await user.click(screen.getByRole("button", { name: "See the data" }));
    const panel = screen.getByRole("complementary", { name: "The data" });
    await within(panel).findByText("Rows 1–100 of 250");

    await user.click(screen.getByRole("button", { name: "Close the data panel" }));
    expect(panel).toHaveAttribute("inert");
    expect(screen.getByRole("button", { name: "See the data" })).toHaveFocus();
    expect(document.body.dataset.dataPanel).toBeUndefined();

    await user.click(screen.getByRole("button", { name: /Open the data panel/ }));
    expect(panel).not.toHaveAttribute("inert");
    fireEvent.keyDown(within(panel).getByRole("button", { name: "Next" }), { key: "Escape" });
    expect(panel).toHaveAttribute("inert");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("starts at a quarter of the window and resizes from the grip, remembered", async () => {
    const user = userEvent.setup();
    render(<DataSidebar runId="run-1" />);
    await user.click(screen.getByRole("button", { name: "See the data" }));
    const grip = screen.getByRole("separator", { name: "Resize the data panel" });
    expect(grip).toHaveAttribute("aria-valuenow", String(DEFAULT_WIDTH * 100));
    grip.focus();
    await user.keyboard("{ArrowLeft}");
    expect(grip).toHaveAttribute("aria-valuenow", "30");
    await user.keyboard("{Home}");
    expect(grip).toHaveAttribute("aria-valuenow", String(MAX_WIDTH * 100));
    await user.keyboard("{End}");
    expect(grip).toHaveAttribute("aria-valuenow", String(MIN_WIDTH * 100));
    expect(window.localStorage.getItem("insightfulcell:data-panel-width")).toBe(String(MIN_WIDTH));

    // Dragging: the panel covers what lies right of the pointer.
    vi.spyOn(window, "innerWidth", "get").mockReturnValue(1000);
    act(() => {
      grip.setPointerCapture = vi.fn();
      grip.releasePointerCapture = vi.fn();
    });
    fireEvent.pointerDown(grip, { button: 0, pointerId: 1, clientX: 800 });
    fireEvent.pointerMove(grip, { pointerId: 1, clientX: 400 });
    fireEvent.pointerUp(grip, { pointerId: 1, clientX: 400 });
    expect(grip).toHaveAttribute("aria-valuenow", "60");
  });

  it("explains a failure and tries again", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          title: "The data is not available yet",
          status: 409,
          code: "data-not-available",
          detail: "The rows are shown once the analysis has read the file.",
        }),
        { status: 409, headers: { "Content-Type": "application/problem+json" } },
      ),
    );
    const user = userEvent.setup();
    render(<DataSidebar runId="run-1" />);
    await user.click(screen.getByRole("button", { name: "See the data" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The data is not available yet");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Rows 1–100 of 250")).toBeInTheDocument();
  });

  it("keeps a stored width within bounds", () => {
    expect(clampWidth(0.05)).toBe(MIN_WIDTH);
    expect(clampWidth(2)).toBe(MAX_WIDTH);
    expect(clampWidth(Number.NaN)).toBe(DEFAULT_WIDTH);
  });
});
