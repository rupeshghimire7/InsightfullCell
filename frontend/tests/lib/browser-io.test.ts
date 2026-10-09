import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProblemError } from "@/lib/problem";
import { browserUploadApi } from "@/lib/upload/api";
import {
  ExpiredUrlError,
  PermanentPutError,
  putPart,
  TransientPutError,
} from "@/lib/upload/put-part";

describe("browserUploadApi", () => {
  const fetchMock = vi.fn();
  beforeEach(() => vi.stubGlobal("fetch", fetchMock));
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

  it("creates an upload through the BFF with an idempotency key and snake_case body", async () => {
    fetchMock.mockResolvedValue(json({ upload_id: "u1" }, 201));
    await browserUploadApi.create({
      filename: "a.csv",
      sizeBytes: 10,
      datasetId: "d1",
      idempotencyKey: "key-12345",
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/backend/v1/uploads");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBe("key-12345");
    expect(JSON.parse(init.body as string)).toEqual({
      filename: "a.csv",
      size_bytes: 10,
      dataset_id: "d1",
    });
    expect(init.credentials).toBe("same-origin");
  });

  it("omits dataset_id when adding a new dataset", async () => {
    fetchMock.mockResolvedValue(json({}, 201));
    await browserUploadApi.create({
      filename: "a.csv",
      sizeBytes: 10,
      idempotencyKey: "key-12345",
    });
    expect(
      JSON.parse((fetchMock.mock.calls[0]?.[1] as RequestInit).body as string),
    ).not.toHaveProperty("dataset_id");
  });

  it("targets the :action URLs", async () => {
    fetchMock.mockImplementation(async () => json({}));
    await browserUploadApi.presign("u1", [2, 3]);
    await browserUploadApi.complete("u1", { sha256: "a".repeat(64), parts: [] });
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual([
      "/api/backend/v1/uploads/u1:presign-parts",
      "/api/backend/v1/uploads/u1:complete",
    ]);
  });

  it("handles 204 responses", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(browserUploadApi.abort("u1")).resolves.toBeUndefined();
  });

  it("turns error responses into ProblemError with the server's wording", async () => {
    fetchMock.mockResolvedValue(
      json(
        { title: "File is too large", status: 422, code: "file-too-large", hint: "Try Parquet" },
        422,
      ),
    );
    const error = await browserUploadApi
      .create({ filename: "a.csv", sizeBytes: 1, idempotencyKey: "key-12345" })
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ProblemError);
    expect((error as ProblemError).problem.hint).toBe("Try Parquet");
  });
});

/** Minimal XMLHttpRequest double: the test drives its lifecycle. */
class FakeXhr {
  static last: FakeXhr;
  upload: { onprogress?: (e: { lengthComputable: boolean; loaded: number }) => void } = {};
  onload?: () => void;
  onerror?: () => void;
  ontimeout?: () => void;
  onabort?: () => void;
  status = 0;
  headers: Record<string, string> = {};
  opened?: [string, string];
  sent?: Blob;
  aborted = false;
  constructor() {
    FakeXhr.last = this;
  }
  open(method: string, url: string) {
    this.opened = [method, url];
  }
  send(body: Blob) {
    this.sent = body;
  }
  abort() {
    this.aborted = true;
    this.onabort?.();
  }
  getResponseHeader(name: string) {
    return this.headers[name.toLowerCase()] ?? null;
  }
  respond(status: number, headers: Record<string, string> = {}) {
    this.status = status;
    this.headers = headers;
    this.onload?.();
  }
}

describe("putPart", () => {
  beforeEach(() => vi.stubGlobal("XMLHttpRequest", FakeXhr));
  afterEach(() => vi.unstubAllGlobals());

  const start = (signal?: AbortSignal, onProgress: (n: number) => void = () => undefined) =>
    putPart("https://s3.test/part?sig=1", new Blob(["abc"]), onProgress, signal);

  it("PUTs the blob, reports progress and resolves with the ETag", async () => {
    const progress = vi.fn();
    const promise = start(undefined, progress);
    expect(FakeXhr.last.opened).toEqual(["PUT", "https://s3.test/part?sig=1"]);
    FakeXhr.last.upload.onprogress?.({ lengthComputable: true, loaded: 2 });
    FakeXhr.last.respond(200, { etag: '"abc"' });
    await expect(promise).resolves.toBe('"abc"');
    expect(progress).toHaveBeenCalledWith(2);
  });

  it("explains a missing ETag (storage CORS must expose it)", async () => {
    const promise = start();
    FakeXhr.last.respond(200);
    await expect(promise).rejects.toThrow(/CORS/);
    await expect(promise).rejects.toBeInstanceOf(PermanentPutError);
  });

  it.each([
    [403, ExpiredUrlError],
    [401, ExpiredUrlError],
    [500, TransientPutError],
    [503, TransientPutError],
    [429, TransientPutError],
    [408, TransientPutError],
    [400, PermanentPutError],
    [404, PermanentPutError],
  ])("classifies HTTP %i", async (status, errorType) => {
    const promise = start();
    FakeXhr.last.respond(status);
    await expect(promise).rejects.toBeInstanceOf(errorType);
  });

  it("treats a dropped connection as transient", async () => {
    const promise = start();
    FakeXhr.last.onerror?.();
    await expect(promise).rejects.toBeInstanceOf(TransientPutError);
  });

  it("aborts the request when the signal fires", async () => {
    const controller = new AbortController();
    const promise = start(controller.signal);
    controller.abort();
    await expect(promise).rejects.toMatchObject({ name: "AbortError" });
    expect(FakeXhr.last.aborted).toBe(true);
  });

  it("does not start when already aborted", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(start(controller.signal)).rejects.toMatchObject({ name: "AbortError" });
  });
});
