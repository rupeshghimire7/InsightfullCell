import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { axe } from "../axe";
import { RunStatusBadge } from "@/components/runs/run-status-badge";
import { RunTable } from "@/components/runs/run-table";
import { StageList } from "@/components/runs/stage-list";
import { StartRunButton } from "@/components/runs/start-run-button";
import { BackLink } from "@/components/ui/back-link";
import { PageHeader } from "@/components/ui/page-header";
import { ProblemError } from "@/lib/problem";
import type { RunsApi } from "@/lib/runs/api";
import { RUN_STATUS } from "@/lib/runs/status";
import type { RunStatus } from "@/lib/runs/types";
import { INGEST_OUTPUT, run, stage } from "../lib/runs-fixtures";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh: vi.fn() }) }));

describe("RunStatusBadge", () => {
  it.each(Object.keys(RUN_STATUS) as RunStatus[])("says %s in words", async (status) => {
    const { container } = render(<RunStatusBadge status={status} />);
    expect(screen.getByText(RUN_STATUS[status].label)).toBeInTheDocument();
    expect(container.querySelector("[data-status]")).toHaveAttribute("data-status", status);
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe("StageList", () => {
  it("shows what ingest found, with the columns behind a disclosure", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <StageList
        runStatus="succeeded"
        stages={[
          stage({
            status: "succeeded",
            started_at: "2026-10-10T00:00:00Z",
            finished_at: "2026-10-10T00:00:03Z",
            output: INGEST_OUTPUT,
          }),
        ]}
      />,
    );
    const list = screen.getByRole("list", { name: "Analysis steps" });
    expect(within(list).getByRole("heading", { name: "Verify and read the file" })).toBeVisible();
    expect(screen.getByText(/Done/)).toHaveTextContent(/Done in 3\ss/);
    expect(screen.getByText("1,234")).toBeInTheDocument();
    expect(screen.getByText("CSV")).toBeInTheDocument();
    await user.click(screen.getByText("Show the 3 columns and what they mean"));
    expect(screen.getByText("suburb")).toBeVisible();
    expect(screen.getByText("DOUBLE")).toBeVisible();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("says a stage was stopped when its run ended early, and marks reused work", () => {
    const { rerender } = render(
      <StageList runStatus="cancelled" stages={[stage({ status: "running" })]} />,
    );
    expect(screen.getByText(/Stopped/)).toBeInTheDocument();
    rerender(
      <StageList runStatus="running" stages={[stage({ status: "succeeded", reused: true })]} />,
    );
    expect(screen.getByText(/reused, not run again/)).toBeInTheDocument();
  });

  it("lists ingest warnings and copes with a missing summary", () => {
    render(
      <StageList
        runStatus="succeeded"
        stages={[
          stage({ status: "succeeded", output: { warnings: ["The file was not UTF-8."] } }),
          stage({ name: "profile", position: 1, status: "pending" }),
        ]}
      />,
    );
    expect(screen.getByText("The file was not UTF-8.")).toBeInTheDocument();
    expect(screen.getAllByText("Unknown").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByRole("heading", { name: "Profile every column" })).toBeInTheDocument();
  });
});

describe("RunTable", () => {
  const runs = [
    run({
      status: "succeeded",
      started_at: "2026-10-10T00:00:00Z",
      finished_at: "2026-10-10T00:01:05Z",
    }),
    run({
      id: "01a120e9-cc81-7ec1-9428-29aea00bb694",
      status: "failed",
      failure: { code: "parse-failure", detail: null, hint: null },
    }),
    run({ id: "01a120e9-cc81-7ec1-9428-29aea00bb695", status: "running", stage: "ingest" }),
  ];

  it("links each run, shows status, step and duration, and passes axe", async () => {
    const { container } = render(<RunTable runs={runs} orgId="org-1" />);
    const table = screen.getByRole("table", { name: "Analysis runs, newest first" });
    expect(within(table).getAllByRole("link", { name: "Home sales 2026" })[0]).toHaveAttribute(
      "href",
      `/o/org-1/runs/${runs[0]!.id}`,
    );
    expect(screen.getByText(/^1\smin 05\ss$/)).toBeInTheDocument();
    expect(screen.getByText("parse-failure")).toBeInTheDocument();
    expect(screen.getByText("Verify and read the file")).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("names runs by id inside a dataset's page", () => {
    render(<RunTable runs={runs.slice(0, 1)} orgId="org-1" showDataset={false} />);
    expect(screen.getByRole("link", { name: `Run ${runs[0]!.id.slice(-8)}` })).toBeInTheDocument();
  });
});

describe("StartRunButton", () => {
  function api(start: RunsApi["start"]): RunsApi {
    return { start } as unknown as RunsApi;
  }

  it("starts a run and opens its page", async () => {
    const start = vi.fn(async () => run());
    const { container } = render(
      <StartRunButton orgId="org-1" datasetId="d1" versionId="v1" api={api(start)} />,
    );
    expect(await axe(container)).toHaveNoViolations();
    await userEvent.setup().click(screen.getByRole("button", { name: "Analyse" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/o/org-1/runs/${run().id}`));
    expect(start).toHaveBeenCalledWith(
      expect.objectContaining({
        datasetId: "d1",
        versionId: "v1",
        idempotencyKey: expect.any(String),
      }),
    );
  });

  it("reuses its Idempotency-Key after a network error, but not after a refusal", async () => {
    const keys: string[] = [];
    let call = 0;
    const start = vi.fn(async ({ idempotencyKey }: { idempotencyKey: string }) => {
      keys.push(idempotencyKey);
      call += 1;
      if (call === 1) throw new TypeError("offline");
      throw new ProblemError({ title: "Not allowed", status: 403, code: "forbidden" });
    });
    const user = userEvent.setup();
    render(<StartRunButton orgId="org-1" datasetId="d1" api={api(start as RunsApi["start"])} />);
    await user.click(screen.getByRole("button", { name: "Analyse" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("You appear to be offline");
    await user.click(screen.getByRole("button", { name: "Analyse" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not allowed");
    await user.click(screen.getByRole("button", { name: "Analyse" }));
    await waitFor(() => expect(keys).toHaveLength(3));
    expect(keys[0]).toBe(keys[1]);
    expect(keys[2]).not.toBe(keys[1]);
  });
});

describe("PageHeader", () => {
  it("renders the title, context, back link and actions accessibly", async () => {
    const { container } = render(
      <PageHeader
        title="Runs"
        description="Every analysis."
        back={<BackLink href="/o/1">All datasets</BackLink>}
        meta={<span>meta</span>}
        actions={<button type="button">Act</button>}
      />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Runs" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All datasets" })).toHaveAttribute("href", "/o/1");
    expect(screen.getByText("meta")).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });
});
