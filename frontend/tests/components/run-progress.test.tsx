import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { axe } from "../axe";
import { RunProgress } from "@/components/runs/run-progress";
import { ProblemError } from "@/lib/problem";
import type { RunsApi } from "@/lib/runs/api";
import type { FollowOptions, StreamResult } from "@/lib/runs/sse";
import type { Run } from "@/lib/runs/types";
import { event, INGEST_OUTPUT, run, stage } from "../lib/runs-fixtures";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));

/** A stream the test drives: it records the options and resolves when told to. */
function controllableStream() {
  let options: FollowOptions | undefined;
  let finish: (result: StreamResult) => void = () => undefined;
  const follow = vi.fn(
    (opts: FollowOptions) =>
      new Promise<StreamResult>((resolve) => {
        options = opts;
        finish = resolve;
      }),
  );
  return {
    follow,
    emit: (...events: Parameters<FollowOptions["onEvent"]>[0][]) =>
      act(() => {
        for (const e of events) options?.onEvent(e);
      }),
    state: (s: Parameters<NonNullable<FollowOptions["onState"]>>[0]) =>
      act(() => options?.onState?.(s)),
    finish: (result: StreamResult) => act(() => finish(result)),
    options: () => options,
  };
}

function fakeApi(overrides: Partial<RunsApi> = {}): RunsApi {
  return {
    start: vi.fn(),
    get: vi.fn(async () => run()),
    cancel: vi.fn(async () =>
      run({
        status: "cancelled",
        finished_at: "2026-10-10T00:01:00Z",
        row_version: 2,
        last_event_seq: 9,
      }),
    ),
    retry: vi.fn(async () => run({ status: "queued", row_version: 4, last_event_seq: 9 })),
    ticket: vi.fn(),
    ...overrides,
  } as RunsApi;
}

describe("RunProgress", () => {
  it("follows the stream from the last event it has, and passes axe", async () => {
    const stream = controllableStream();
    const { container } = render(
      <RunProgress
        initial={run({ last_event_seq: 4 })}
        orgId="org-1"
        api={fakeApi()}
        follow={stream.follow}
      />,
    );
    expect(stream.follow).toHaveBeenCalledTimes(1);
    expect(stream.options()?.lastEventId).toBe(4);
    expect(screen.getByTestId("connection")).toHaveTextContent("Connecting to live progress");
    stream.state("live");
    expect(screen.getByTestId("connection")).toHaveTextContent("Live");
    expect(screen.getByRole("link", { name: "Home sales 2026" })).toHaveAttribute(
      "href",
      `/o/org-1/datasets/${run().dataset_id}`,
    );
    expect(await axe(container)).toHaveNoViolations();
  });

  it("applies events as they arrive and announces them", async () => {
    const stream = controllableStream();
    const api = fakeApi({
      get: vi.fn(async () =>
        run({
          status: "running",
          last_event_seq: 4,
          stages: [stage({ status: "succeeded", output: INGEST_OUTPUT })],
        }),
      ),
    });
    render(<RunProgress initial={run()} orgId="org-1" api={api} follow={stream.follow} />);
    stream.emit(event(2, "run.started"), event(3, "stage.started", { stage: "ingest" }));
    expect(screen.getAllByText("Running").length).toBeGreaterThan(0);
    expect(screen.getByTestId("run-announcer")).toHaveTextContent(
      "Started: Verify and read the file.",
    );

    stream.emit(event(4, "stage.completed", { stage: "ingest" }));
    expect(screen.getByTestId("run-announcer")).toHaveTextContent(
      "Done: Verify and read the file.",
    );
    // The whole stage output arrives with a re-read of the run.
    await waitFor(() => expect(api.get).toHaveBeenCalledWith(run().id));
    expect(await screen.findByText("1,234")).toBeInTheDocument();
  });

  it("stops following when the run finishes and says so", async () => {
    const stream = controllableStream();
    render(
      <RunProgress
        initial={run({ status: "running" })}
        orgId="org-1"
        api={fakeApi()}
        follow={stream.follow}
      />,
    );
    stream.emit(event(2, "run.succeeded"));
    expect(
      screen.getByText("Your data is profiled, planned and ready to clean"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("connection")).not.toBeInTheDocument();
    expect(screen.getByTestId("run-announcer")).toHaveTextContent("The analysis finished.");
    expect(stream.options()?.signal.aborted).toBe(true);
  });

  it("does not connect for a finished run", () => {
    const follow = vi.fn();
    render(
      <RunProgress
        initial={run({
          status: "succeeded",
          finished_at: "2026-10-10T01:00:09Z",
          started_at: "2026-10-10T01:00:00Z",
        })}
        orgId="org-1"
        api={fakeApi()}
        follow={follow}
      />,
    );
    expect(follow).not.toHaveBeenCalled();
    expect(screen.getByText(/^9\ss$/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Cancel/ })).not.toBeInTheDocument();
  });

  it("polls when the stream cannot get through", async () => {
    const stream = controllableStream();
    let finished = false;
    const api = fakeApi({
      get: vi.fn(async () =>
        finished
          ? run({ status: "succeeded", last_event_seq: 6, finished_at: "2026-10-10T01:00:06Z" })
          : run({ status: "running", last_event_seq: 3 }),
      ),
    });
    render(
      <RunProgress initial={run()} orgId="org-1" api={api} follow={stream.follow} pollMs={5} />,
    );
    stream.finish("fallback");
    expect(await screen.findByText(/refreshing every few seconds/)).toBeInTheDocument();
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
    finished = true;
    expect(
      await screen.findByText("Your data is profiled, planned and ready to clean"),
    ).toBeInTheDocument();
    const reads = vi.mocked(api.get).mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(vi.mocked(api.get).mock.calls.length).toBe(reads); // polling stops once finished
  });

  it("asks before cancelling, then cancels with the version it saw", async () => {
    const user = userEvent.setup();
    const stream = controllableStream();
    const api = fakeApi();
    render(
      <RunProgress
        initial={run({ row_version: 1 })}
        orgId="org-1"
        api={api}
        follow={stream.follow}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Cancel analysis…" }));
    const group = screen.getByRole("group", { name: "Confirm cancelling" });
    expect(group).toHaveTextContent("Finished steps are kept.");
    await user.click(screen.getByRole("button", { name: "Keep running" }));
    expect(api.cancel).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Cancel analysis…" }));
    await user.click(screen.getByRole("button", { name: "Cancel analysis" }));
    await waitFor(() => expect(api.cancel).toHaveBeenCalledWith(run().id, 1));
    expect(await screen.findAllByText("Cancelled")).not.toHaveLength(0);
    expect(screen.getByTestId("run-announcer")).toHaveTextContent("The analysis was cancelled.");
  });

  it("shows a conflict and re-reads the run when someone else changed it", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      cancel: vi.fn(async () => {
        throw new ProblemError({
          title: "The run changed since you last loaded it",
          status: 412,
          code: "run-changed",
        });
      }),
    });
    render(
      <RunProgress initial={run()} orgId="org-1" api={api} follow={controllableStream().follow} />,
    );
    await user.click(screen.getByRole("button", { name: "Cancel analysis…" }));
    await user.click(screen.getByRole("button", { name: "Cancel analysis" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The run changed since you last loaded it",
    );
    await waitFor(() => expect(api.get).toHaveBeenCalled());
  });

  it("explains a failure and retries it", async () => {
    const user = userEvent.setup();
    const stream = controllableStream();
    const failed: Run = run({
      status: "failed",
      row_version: 3,
      finished_at: "2026-10-10T01:00:05Z",
      failure: {
        code: "integrity-mismatch",
        detail: "The stored file does not match.",
        hint: "Upload the file again.",
      },
      stages: [stage({ status: "failed" })],
    });
    const api = fakeApi();
    const { container } = render(
      <RunProgress initial={failed} orgId="org-1" api={api} follow={stream.follow} />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The analysis failed");
    expect(alert).toHaveTextContent("The stored file does not match.");
    expect(alert).toHaveTextContent("integrity-mismatch");
    expect(await axe(container)).toHaveNoViolations();

    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(api.retry).toHaveBeenCalledWith(failed.id, 3));
    // Queued again: following resumes.
    await waitFor(() => expect(stream.follow).toHaveBeenCalledTimes(1));
  });

  it("shows notes for waiting and review", () => {
    const stream = controllableStream();
    render(<RunProgress initial={run()} orgId="org-1" api={fakeApi()} follow={stream.follow} />);
    stream.emit(event(2, "run.waiting"));
    expect(screen.getByText(/Waiting for a free slot/)).toBeInTheDocument();
    stream.emit(event(3, "run.started"), event(4, "review.required"));
    expect(screen.getByRole("heading", { name: "Your review is needed" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review changes" })).toHaveAttribute(
      "href",
      expect.stringMatching(/\/o\/org-1\/runs\/.+\/review$/),
    );
    expect(screen.getByTestId("run-announcer")).toHaveTextContent("waiting for your review");
  });
});
