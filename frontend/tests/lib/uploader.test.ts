import { describe, expect, it, vi } from "vitest";
import { ProblemError } from "@/lib/problem";
import type { UploadApi, UploadTicket, UploadResult } from "@/lib/upload/api";
import {
  ExpiredUrlError,
  PermanentPutError,
  TransientPutError,
  type PutPart,
} from "@/lib/upload/put-part";
import {
  backoffMs,
  uploadFile,
  UploadRejectedError,
  type UploadProgress,
  type UploaderDeps,
} from "@/lib/upload/uploader";

const PART = 5 * 1024 * 1024;

function makeFile(size: number, name = "data.csv"): File {
  // Content is irrelevant to the uploader; sparse blobs keep the test fast.
  return new File([new Uint8Array(size)], name, { type: "text/csv" });
}

function ticketFor(size: number, partSize = PART): UploadTicket {
  const count = Math.max(1, Math.ceil(size / partSize));
  return {
    upload_id: "up-1",
    status: "pending",
    filename: "data.csv",
    format: "csv",
    size_bytes: size,
    part_size_bytes: partSize,
    part_count: count,
    expires_at: "2030-01-01T00:00:00Z",
    parts: Array.from({ length: count }, (_, i) => ({
      part_number: i + 1,
      url: `https://storage.test/part/${i + 1}?sig=1`,
      content_length: i + 1 < count ? partSize : size - partSize * (count - 1),
    })),
  };
}

const RESULT = {
  duplicate: false,
  dataset: {
    id: "d",
    name: "data",
    created_at: "2030-01-01T00:00:00Z",
    version_count: 1,
    latest_version: null,
  },
  version: {
    id: "v",
    dataset_id: "d",
    sha256: "a".repeat(64),
    bytes: 1,
    format: "csv",
    created_at: "2030-01-01T00:00:00Z",
  },
} as UploadResult;

function harness(size: number, overrides: { putPart?: PutPart; api?: Partial<UploadApi> } = {}) {
  const ticket = ticketFor(size);
  const api: UploadApi = {
    create: vi.fn(async () => ticket),
    presign: vi.fn(async (_id, numbers) => ({
      ...ticket,
      parts: ticket.parts
        .filter((p) => numbers.includes(p.part_number))
        .map((p) => ({ ...p, url: `${p.url}-fresh` })),
    })),
    complete: vi.fn(async () => RESULT),
    abort: vi.fn(async () => undefined),
    ...overrides.api,
  };
  const putPart: PutPart =
    overrides.putPart ??
    vi.fn(async (url, _blob, onProgress) => {
      onProgress(1);
      return `"etag-${url.split("/part/")[1]?.split("?")[0]}"`;
    });
  const hashFile = vi.fn(async () => "f".repeat(64));
  const deps: UploaderDeps = {
    api,
    putPart,
    hashFile,
    sleep: async () => undefined,
    randomId: () => "idem-key-1234",
  };
  return { api, putPart, hashFile, deps };
}

describe("uploadFile", () => {
  it("uploads every part, then completes with sorted parts and the checksum", async () => {
    const { deps, api } = harness(PART * 2 + 100);
    const result = await uploadFile(makeFile(PART * 2 + 100), deps);

    expect(result).toBe(RESULT);
    expect(api.create).toHaveBeenCalledWith(
      expect.objectContaining({
        filename: "data.csv",
        sizeBytes: PART * 2 + 100,
        idempotencyKey: "idem-key-1234",
      }),
    );
    expect(api.complete).toHaveBeenCalledWith(
      "up-1",
      {
        sha256: "f".repeat(64),
        parts: [1, 2, 3].map((n) => ({ part_number: n, etag: `"etag-${n}"` })),
      },
      undefined,
    );
    expect(api.abort).not.toHaveBeenCalled();
  });

  it("sends exactly the bytes each part declares", async () => {
    const sizes: number[] = [];
    const { deps } = harness(PART + 7, {
      putPart: async (_url, blob) => {
        sizes.push(blob.size);
        return '"e"';
      },
    });
    await uploadFile(makeFile(PART + 7), deps);
    expect(sizes.sort((a, b) => a - b)).toEqual([7, PART]);
  });

  it("never has more parts in flight than the concurrency limit", async () => {
    let inFlight = 0;
    let peak = 0;
    const { deps } = harness(PART * 8, {
      putPart: async () => {
        inFlight += 1;
        peak = Math.max(peak, inFlight);
        await new Promise((resolve) => setTimeout(resolve, 5));
        inFlight -= 1;
        return '"e"';
      },
    });
    await uploadFile(makeFile(PART * 8), deps, { concurrency: 3 });
    expect(peak).toBe(3);
  });

  it("reports monotonic progress ending in done", async () => {
    const seen: UploadProgress[] = [];
    const { deps } = harness(PART * 2);
    await uploadFile(makeFile(PART * 2), deps, { onProgress: (p) => seen.push(p) });

    expect(seen[0]?.phase).toBe("preparing");
    expect(seen.map((p) => p.phase)).toContain("verifying");
    expect(seen.at(-1)).toMatchObject({ phase: "done", uploadedBytes: PART * 2, partsDone: 2 });
    const bytes = seen.map((p) => p.uploadedBytes);
    expect(bytes.at(-1)).toBeGreaterThanOrEqual(Math.max(...bytes));
  });

  describe("failure handling", () => {
    it("retries transient failures with backoff and then succeeds", async () => {
      let calls = 0;
      const sleep = vi.fn(async () => undefined);
      const { deps } = harness(100, {
        putPart: async () => {
          calls += 1;
          if (calls < 3) throw new TransientPutError("dropped");
          return '"ok"';
        },
      });
      await uploadFile(makeFile(100), { ...deps, sleep });
      expect(calls).toBe(3);
      expect(sleep).toHaveBeenCalledTimes(2);
    });

    it("gives up after the maximum attempts and aborts the upload", async () => {
      const { deps, api } = harness(100, {
        putPart: async () => {
          throw new TransientPutError("down");
        },
      });
      await expect(uploadFile(makeFile(100), deps, { maxAttempts: 2 })).rejects.toBeInstanceOf(
        TransientPutError,
      );
      expect(api.abort).toHaveBeenCalledWith("up-1");
      expect(api.complete).not.toHaveBeenCalled();
    });

    it("fetches a fresh URL when the presigned URL expired", async () => {
      const urls: string[] = [];
      const { deps, api } = harness(100, {
        putPart: async (url) => {
          urls.push(url);
          if (!url.endsWith("-fresh")) throw new ExpiredUrlError("expired");
          return '"ok"';
        },
      });
      await uploadFile(makeFile(100), deps);
      expect(api.presign).toHaveBeenCalledWith("up-1", [1], expect.any(AbortSignal));
      expect(urls.at(-1)).toMatch(/-fresh$/);
    });

    it("does not retry permanent storage errors", async () => {
      const put = vi.fn(async () => {
        throw new PermanentPutError("no ETag");
      });
      const { deps, api } = harness(100, { putPart: put });
      await expect(uploadFile(makeFile(100), deps)).rejects.toBeInstanceOf(PermanentPutError);
      expect(put).toHaveBeenCalledTimes(1);
      expect(api.abort).toHaveBeenCalled();
    });

    it("stops the remaining parts after the first permanent failure", async () => {
      let started = 0;
      const { deps } = harness(PART * 10, {
        putPart: async () => {
          started += 1;
          throw new PermanentPutError("rejected");
        },
      });
      await expect(uploadFile(makeFile(PART * 10), deps, { concurrency: 2 })).rejects.toBeTruthy();
      expect(started).toBeLessThanOrEqual(2);
    });

    it("leaves a server-closed upload alone (nothing to abort)", async () => {
      const closed = new ProblemError({ title: "x", status: 422, code: "content-mismatch" });
      const { deps, api } = harness(100, {
        api: {
          complete: vi.fn(async () => {
            throw closed;
          }),
        },
      });
      await expect(uploadFile(makeFile(100), deps)).rejects.toBe(closed);
      expect(api.abort).not.toHaveBeenCalled();
    });

    it("aborts the upload when completion fails for another reason", async () => {
      const failure = new ProblemError({ title: "x", status: 503, code: "storage-unavailable" });
      const { deps, api } = harness(100, {
        api: {
          complete: vi.fn(async () => {
            throw failure;
          }),
        },
      });
      await expect(uploadFile(makeFile(100), deps)).rejects.toBe(failure);
      expect(api.abort).toHaveBeenCalledWith("up-1");
    });

    it("cancels in-flight parts when the caller aborts", async () => {
      const controller = new AbortController();
      const { deps, api } = harness(PART * 2, {
        putPart: (_url, _blob, _progress, signal) =>
          new Promise<string>((_resolve, reject) => {
            signal?.addEventListener("abort", () =>
              reject(new DOMException("Cancelled", "AbortError")),
            );
            setTimeout(() => controller.abort(), 1);
          }),
      });
      await expect(
        uploadFile(makeFile(PART * 2), deps, { signal: controller.signal }),
      ).rejects.toMatchObject({
        name: "AbortError",
      });
      expect(api.abort).toHaveBeenCalledWith("up-1");
    });

    it("surfaces a hashing failure without completing", async () => {
      const { deps, api } = harness(100);
      deps.hashFile = async () => {
        throw new Error("worker crashed");
      };
      await expect(uploadFile(makeFile(100), deps)).rejects.toThrow("worker crashed");
      expect(api.complete).not.toHaveBeenCalled();
    });
  });

  it("rejects bad files before contacting the API", async () => {
    const { deps, api } = harness(100);
    await expect(uploadFile(makeFile(100, "notes.txt"), deps)).rejects.toBeInstanceOf(
      UploadRejectedError,
    );
    await expect(uploadFile(makeFile(0), deps)).rejects.toBeInstanceOf(UploadRejectedError);
    expect(api.create).not.toHaveBeenCalled();
  });

  it("passes the target dataset through for new versions", async () => {
    const { deps, api } = harness(100);
    await uploadFile(makeFile(100), deps, { datasetId: "dataset-9" });
    expect(api.create).toHaveBeenCalledWith(expect.objectContaining({ datasetId: "dataset-9" }));
  });
});

describe("backoffMs", () => {
  it("grows exponentially, is jittered and is capped", () => {
    expect(backoffMs(1, () => 0)).toBe(250);
    expect(backoffMs(1, () => 1)).toBe(500);
    expect(backoffMs(3, () => 1)).toBe(2000);
    expect(backoffMs(20, () => 1)).toBe(8000);
  });
});
