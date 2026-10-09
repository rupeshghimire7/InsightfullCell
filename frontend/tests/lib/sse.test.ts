// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { followRunEvents, SseParser, type FollowOptions } from "@/lib/runs/sse";
import type { EventTicket, RunEvent } from "@/lib/runs/types";

describe("SseParser", () => {
  it("parses messages, ids, event names and multi-line data", () => {
    const parser = new SseParser();
    expect(
      parser.feed('retry: 3000\n\n: keep-alive\n\nid: 1\nevent: run.queued\ndata: {"a":1}\n\n'),
    ).toEqual([{ id: "1", event: "run.queued", data: '{"a":1}' }]);
    expect(parser.feed("data: line one\ndata:line two\n\n")).toEqual([
      { id: "1", event: undefined, data: "line one\nline two" },
    ]);
  });

  it("handles CRLF, CR and messages split across chunks", () => {
    const parser = new SseParser();
    expect(parser.feed("id: 7\r\nda")).toEqual([]);
    expect(parser.feed("ta: x\r")).toEqual([]);
    expect(parser.feed("\n\r\n")).toEqual([{ id: "7", event: undefined, data: "x" }]);
    expect(parser.feed("data\n\n")).toEqual([{ id: "7", event: undefined, data: "" }]);
  });

  it("ignores blocks without data and ids containing NUL", () => {
    const parser = new SseParser();
    expect(parser.feed("event: x\n\nid: a\u0000b\ndata: y\n\n")).toEqual([
      { id: undefined, event: undefined, data: "y" },
    ]);
  });
});

// --------------------------------------------------------------------------- followRunEvents

const ticket = (expiresInMs = 300_000): EventTicket => ({
  ticket: `t-${Math.random()}`,
  url: "https://api.test/v1/runs/r1/events",
  expires_at: new Date(Date.now() + expiresInMs).toISOString(),
});

function frame(event: Partial<RunEvent> & { seq: number; type: RunEvent["type"] }): string {
  const full = { stage: null, data: {}, at: "2026-10-10T00:00:00Z", ...event };
  return `id: ${full.seq}\nevent: ${full.type}\ndata: ${JSON.stringify(full)}\n\n`;
}

function sse(chunks: string[], { hang = false } = {}): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      if (!hang) controller.close();
    },
  });
  return new Response(body, { headers: { "content-type": "text/event-stream" } });
}

type Script = (call: number, init: RequestInit) => Response | Promise<Response>;

function harness(script: Script, overrides: Partial<FollowOptions> = {}) {
  const calls: RequestInit[] = [];
  const events: RunEvent[] = [];
  const states: string[] = [];
  const controller = new AbortController();
  const getTicket = vi.fn(async () => ticket());
  const fetchImpl = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
    calls.push(init ?? {});
    return script(calls.length, init ?? {});
  });
  const sleep = vi.fn<(ms: number) => Promise<void>>(async () => undefined);
  const run = () =>
    followRunEvents({
      getTicket,
      onEvent: (event) => events.push(event),
      onState: (state) => states.push(state),
      signal: controller.signal,
      fetchImpl: fetchImpl as unknown as typeof fetch,
      sleep,
      ...overrides,
    });
  const header = (i: number, name: string) =>
    (calls[i]?.headers as Record<string, string> | undefined)?.[name];
  return { run, calls, events, states, controller, getTicket, sleep, header };
}

describe("followRunEvents", () => {
  it("streams events until the run finishes", async () => {
    const h = harness(() =>
      sse([
        "retry: 3000\n\n",
        frame({ seq: 2, type: "run.started" }),
        ": keep-alive\n\n",
        frame({ seq: 3, type: "run.succeeded" }),
      ]),
    );
    expect(await h.run()).toBe("ended");
    expect(h.events.map((e) => e.seq)).toEqual([2, 3]);
    expect(h.states).toEqual(["connecting", "live"]);
    expect(h.header(0, "Authorization")).toMatch(/^Bearer t-/);
    expect(h.header(0, "Accept")).toBe("text/event-stream");
    expect(h.header(0, "Last-Event-ID")).toBeUndefined();
  });

  it("resumes after a closed stream with Last-Event-ID and skips what it has seen", async () => {
    const h = harness(
      (call) =>
        call === 1
          ? sse([frame({ seq: 5, type: "stage.started" })])
          : sse([frame({ seq: 5, type: "stage.started" }), frame({ seq: 6, type: "run.failed" })]),
      { lastEventId: 4 },
    );
    expect(await h.run()).toBe("ended");
    expect(h.header(0, "Last-Event-ID")).toBe("4");
    expect(h.header(1, "Last-Event-ID")).toBe("5");
    expect(h.events.map((e) => e.seq)).toEqual([5, 6]);
    expect(h.getTicket).toHaveBeenCalledTimes(1); // the ticket is still fresh
    expect(h.states).toContain("reconnecting");
  });

  it("fetches a new ticket on 401", async () => {
    const h = harness((call) =>
      call === 1
        ? new Response(null, { status: 401 })
        : sse([frame({ seq: 2, type: "run.cancelled" })]),
    );
    expect(await h.run()).toBe("ended");
    expect(h.getTicket).toHaveBeenCalledTimes(2);
  });

  it("refreshes a ticket that is about to expire before connecting", async () => {
    let first = true;
    const getTicket = vi.fn(async () => {
      const t = ticket(first ? 10_000 : 300_000);
      first = false;
      return t;
    });
    const h = harness(
      (call) =>
        call === 1
          ? sse([frame({ seq: 2, type: "run.started" })])
          : sse([frame({ seq: 3, type: "run.expired" })]),
      { getTicket },
    );
    expect(await h.run()).toBe("ended");
    expect(getTicket).toHaveBeenCalledTimes(2);
  });

  it("falls back after repeated failures, backing off between attempts", async () => {
    const h = harness(() => new Response("<html>proxy</html>", { status: 200 }), {
      maxFailures: 3,
    });
    expect(await h.run()).toBe("fallback");
    expect(h.calls).toHaveLength(3);
    expect(h.sleep.mock.calls.map(([ms]) => ms)).toEqual([500, 1000]);
  });

  it("counts network errors and failed ticket requests as failures", async () => {
    const h = harness(
      () => {
        throw new TypeError("network down");
      },
      { maxFailures: 2 },
    );
    expect(await h.run()).toBe("fallback");
    const noTicket = harness(() => sse([]), {
      maxFailures: 1,
      getTicket: async () => {
        throw new Error("401");
      },
    });
    expect(await noTicket.run()).toBe("fallback");
  });

  it("treats a stream that delivers nothing as stalled (a buffering proxy)", async () => {
    const h = harness(() => sse([], { hang: true }), { stallMs: 20, maxFailures: 1 });
    expect(await h.run()).toBe("fallback");
  });

  it("ignores data that is not a run event", async () => {
    const h = harness(() =>
      sse([
        "data: not json\n\n",
        'data: {"hello":1}\n\n',
        frame({ seq: 9, type: "run.succeeded" }),
      ]),
    );
    expect(await h.run()).toBe("ended");
    expect(h.events.map((e) => e.seq)).toEqual([9]);
  });

  it("stops when aborted", async () => {
    const h = harness(
      (_call, init) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    const result = h.run();
    await new Promise((resolve) => setTimeout(resolve, 10));
    h.controller.abort();
    expect(await result).toBe("aborted");
  });

  it("does nothing when already aborted", async () => {
    const h = harness(() => sse([]));
    h.controller.abort();
    expect(await h.run()).toBe("aborted");
    expect(h.calls).toHaveLength(0);
  });
});
