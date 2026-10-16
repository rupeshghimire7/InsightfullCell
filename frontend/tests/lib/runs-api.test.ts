import { afterEach, describe, expect, it, vi } from "vitest";
import { ProblemError } from "@/lib/problem";
import { runsApi } from "@/lib/runs/api";

function mockFetch(status: number, body: unknown) {
  const fetchMock = vi.fn(
    async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": status < 400 ? "application/json" : "application/problem+json" },
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function lastCall(fetchMock: ReturnType<typeof mockFetch>) {
  const [url, init] = fetchMock.mock.calls.at(-1) as unknown as [string, RequestInit];
  return { url, init, headers: init.headers as Record<string, string> };
}

afterEach(() => vi.unstubAllGlobals());

describe("runsApi", () => {
  it("starts a run through the BFF with an Idempotency-Key", async () => {
    const fetchMock = mockFetch(202, { id: "r1" });
    await runsApi.start({ datasetId: "d1", versionId: "v1", idempotencyKey: "key-12345678" });
    const { url, init, headers } = lastCall(fetchMock);
    expect(url).toBe("/api/backend/v1/runs");
    expect(init.method).toBe("POST");
    expect(headers["Idempotency-Key"]).toBe("key-12345678");
    expect(JSON.parse(String(init.body))).toEqual({
      dataset_id: "d1",
      dataset_version_id: "v1",
      review_mode: "auto",
    });
    expect(init.credentials).toBe("same-origin");
  });

  it("omits the version when the newest one is meant", async () => {
    const fetchMock = mockFetch(202, { id: "r1" });
    await runsApi.start({ datasetId: "d1", idempotencyKey: "key-12345678" });
    expect(JSON.parse(String(lastCall(fetchMock).init.body))).toEqual({
      dataset_id: "d1",
      review_mode: "auto",
    });
  });

  it("asks for a manual review when chosen", async () => {
    const fetchMock = mockFetch(202, { id: "r1" });
    await runsApi.start({ datasetId: "d1", idempotencyKey: "key-12345678", reviewMode: "manual" });
    expect(JSON.parse(String(lastCall(fetchMock).init.body))).toMatchObject({
      review_mode: "manual",
    });
  });

  it.each([
    ["cancel", "/api/backend/v1/runs/r1:cancel"],
    ["retry", "/api/backend/v1/runs/r1:retry"],
  ] as const)("%s sends If-Match with the version the user saw", async (action, path) => {
    const fetchMock = mockFetch(200, { id: "r1" });
    await runsApi[action]("r1", 7);
    const { url, headers } = lastCall(fetchMock);
    expect(url).toBe(path);
    expect(headers["If-Match"]).toBe('"7"');
  });

  it("reads a run and asks the web app for an event ticket", async () => {
    const fetchMock = mockFetch(200, { ticket: "t" });
    await runsApi.get("r 1");
    expect(lastCall(fetchMock).url).toBe("/api/backend/v1/runs/r%201");
    await runsApi.ticket("r1");
    expect(lastCall(fetchMock)).toMatchObject({
      url: "/api/runs/r1/events-ticket",
      init: { method: "POST" },
    });
  });

  it("throws the API's problem", async () => {
    mockFetch(409, { title: "Not now", status: 409, code: "run-state-conflict" });
    await expect(runsApi.retry("r1", 1)).rejects.toBeInstanceOf(ProblemError);
  });
});
