import { ProblemError } from "@/lib/problem";
import type { CompletedPart, PartTarget, UploadApi, UploadResult } from "@/lib/upload/api";
import { validateFile } from "@/lib/upload/constraints";
import type { HashFile } from "@/lib/upload/hash";
import {
  ExpiredUrlError,
  PermanentPutError,
  TransientPutError,
  type PutPart,
} from "@/lib/upload/put-part";

export type UploadPhase = "preparing" | "uploading" | "verifying" | "done";

export interface UploadProgress {
  phase: UploadPhase;
  uploadedBytes: number;
  totalBytes: number;
  partsDone: number;
  partCount: number;
}

export interface UploaderDeps {
  api: UploadApi;
  putPart: PutPart;
  hashFile: HashFile;
  /** Injectable for tests; defaults to a real timer. */
  sleep?: (ms: number) => Promise<void>;
  randomId?: () => string;
}

export interface UploadOptions {
  datasetId?: string;
  signal?: AbortSignal;
  onProgress?: (progress: UploadProgress) => void;
  concurrency?: number;
  maxAttempts?: number;
}

const DEFAULT_CONCURRENCY = 3;
const DEFAULT_ATTEMPTS = 4;

const realSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Exponential backoff with jitter: 0.5 s, 1 s, 2 s ... capped at 8 s. */
export function backoffMs(attempt: number, random: () => number = Math.random): number {
  return Math.min(8000, 500 * 2 ** (attempt - 1)) * (0.5 + random() / 2);
}

export class UploadRejectedError extends Error {
  constructor(
    readonly title: string,
    detail: string,
    readonly hint: string,
  ) {
    super(detail);
    this.name = "UploadRejectedError";
  }
}

/**
 * Direct-to-storage multipart upload (ADR-009):
 * create -> PUT parts in parallel (resumable, retried) while hashing in a worker -> complete.
 * Throws `UploadRejectedError` for pre-flight problems and `ProblemError` for API failures.
 */
export async function uploadFile(
  file: File,
  deps: UploaderDeps,
  options: UploadOptions = {},
): Promise<UploadResult> {
  const { api, putPart, hashFile } = deps;
  const sleep = deps.sleep ?? realSleep;
  const randomId = deps.randomId ?? (() => crypto.randomUUID());
  const concurrency = options.concurrency ?? DEFAULT_CONCURRENCY;
  const maxAttempts = options.maxAttempts ?? DEFAULT_ATTEMPTS;
  const { signal } = options;

  const rejection = validateFile(file);
  if (rejection) throw new UploadRejectedError(rejection.title, rejection.detail, rejection.hint);

  const report = (patch: Partial<UploadProgress> & { phase: UploadPhase }) => {
    state = { ...state, ...patch };
    options.onProgress?.(state);
  };
  let state: UploadProgress = {
    phase: "preparing",
    uploadedBytes: 0,
    totalBytes: file.size,
    partsDone: 0,
    partCount: 0,
  };
  report({ phase: "preparing" });

  const ticket = await api.create({
    filename: file.name,
    sizeBytes: file.size,
    datasetId: options.datasetId,
    idempotencyKey: randomId(),
    signal,
  });
  const uploadId = ticket.upload_id;
  report({ phase: "uploading", partCount: ticket.part_count });

  const abortController = new AbortController();
  const onOuterAbort = () => abortController.abort();
  signal?.addEventListener("abort", onOuterAbort, { once: true });

  try {
    const sha256Promise = hashFile(file, abortController.signal);
    // A rejected hash must not surface as unhandled while parts are still uploading.
    sha256Promise.catch(() => undefined);

    const targets = new Map<number, PartTarget>(ticket.parts.map((p) => [p.part_number, p]));
    const loaded = new Map<number, number>();
    const completed = new Map<number, CompletedPart>();
    const queue = [...targets.keys()].sort((a, b) => a - b);

    const publish = () => {
      let bytes = 0;
      for (const value of loaded.values()) bytes += value;
      report({ phase: "uploading", uploadedBytes: bytes, partsDone: completed.size });
    };

    const uploadOne = async (partNumber: number) => {
      const start = (partNumber - 1) * ticket.part_size_bytes;
      let target = targets.get(partNumber);
      for (let attempt = 1; ; attempt += 1) {
        if (!target) throw new Error(`Missing upload target for part ${partNumber}`);
        const blob = file.slice(start, start + target.content_length);
        try {
          const etag = await putPart(
            target.url,
            blob,
            (bytes) => {
              loaded.set(partNumber, bytes);
              publish();
            },
            abortController.signal,
          );
          loaded.set(partNumber, target.content_length);
          completed.set(partNumber, { part_number: partNumber, etag });
          publish();
          return;
        } catch (error) {
          loaded.set(partNumber, 0);
          if (error instanceof PermanentPutError || abortController.signal.aborted) throw error;
          if (attempt >= maxAttempts) throw error;
          if (error instanceof ExpiredUrlError) {
            const fresh = await api.presign(uploadId, [partNumber], abortController.signal);
            target = fresh.parts.find((p) => p.part_number === partNumber) ?? target;
          } else if (!(error instanceof TransientPutError)) {
            throw error;
          }
          await sleep(backoffMs(attempt));
        }
      }
    };

    // Small worker pool: at most `concurrency` parts in flight; first failure stops the rest.
    const failure: { error?: unknown } = {};
    const worker = async () => {
      while (queue.length > 0 && failure.error === undefined) {
        const next = queue.shift();
        if (next === undefined) return;
        try {
          await uploadOne(next);
        } catch (error) {
          failure.error ??= error;
          abortController.abort();
          return;
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(concurrency, queue.length) }, worker));
    if (failure.error !== undefined) throw failure.error;

    report({ phase: "verifying" });
    const sha256 = await sha256Promise;
    const parts = [...completed.values()].sort((a, b) => a.part_number - b.part_number);
    const result = await api.complete(uploadId, { sha256, parts }, signal);
    report({ phase: "done", uploadedBytes: file.size, partsDone: ticket.part_count });
    return result;
  } catch (error) {
    // Release the stored parts unless the server already closed the upload itself.
    const alreadyClosed =
      error instanceof ProblemError &&
      ["upload-not-pending", "upload-expired", "content-mismatch", "upload-size-mismatch"].includes(
        error.problem.code,
      );
    if (!alreadyClosed) await api.abort(uploadId).catch(() => undefined);
    throw error;
  } finally {
    signal?.removeEventListener("abort", onOuterAbort);
  }
}
