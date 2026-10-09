import { render, screen, within } from "@testing-library/react";
import { axe } from "../axe";
import { describe, expect, it } from "vitest";
import { DatasetTable, DatasetTableSkeleton } from "@/components/datasets/dataset-table";
import { EmptyState } from "@/components/ui/empty-state";

const datasets = [
  {
    id: "1",
    name: "Home sales 2026",
    created_at: "2026-10-01T10:00:00Z",
    version_count: 2,
    latest_version: {
      id: "v",
      dataset_id: "1",
      sha256: "a".repeat(64),
      bytes: 2_500_000,
      format: "xlsx",
      created_at: "2026-10-02T10:00:00Z",
    },
  },
  {
    id: "2",
    name: "x".repeat(300),
    created_at: "2026-09-01T10:00:00Z",
    version_count: 1,
    latest_version: null,
  },
];

describe("DatasetTable", () => {
  it("renders a labelled table with row headers and no a11y violations", async () => {
    const { container } = render(<DatasetTable datasets={datasets} />);
    const table = screen.getByRole("table", { name: "Datasets, newest first" });
    expect(within(table).getAllByRole("columnheader")).toHaveLength(5);
    expect(within(table).getByRole("rowheader", { name: "Home sales 2026" })).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("formats size and format for humans and tolerates a missing version", () => {
    render(<DatasetTable datasets={datasets} />);
    expect(screen.getByText("Excel")).toBeInTheDocument();
    expect(screen.getByText(/2\.5\sMB/)).toBeInTheDocument();
    expect(screen.getAllByText("–").length).toBeGreaterThanOrEqual(2);
  });

  it("truncates very long names but keeps the full text available", () => {
    render(<DatasetTable datasets={datasets} />);
    expect(screen.getByTitle("x".repeat(300))).toHaveClass("truncate");
  });

  it("skeleton is hidden from assistive technology", () => {
    render(<DatasetTableSkeleton rows={2} />);
    expect(screen.getByTestId("dataset-skeleton")).toHaveAttribute("aria-hidden", "true");
  });
});

describe("EmptyState", () => {
  it("gives a title, an explanation and no dead end", async () => {
    const { container } = render(
      <EmptyState title="No datasets yet" action={<a href="#upload">Upload a file</a>}>
        Upload a file above.
      </EmptyState>,
    );
    expect(screen.getByRole("heading", { name: "No datasets yet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upload a file" })).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });
});
