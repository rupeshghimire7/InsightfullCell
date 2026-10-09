import type { components } from "@/lib/api/schema";
import { ProblemError, readProblem } from "@/lib/problem";

export type UploadTicket = components["schemas"]["UploadOut"];
export type PartTarget = components["schemas"]["PartTargetOut"];
export type CompletedPart = components["schemas"]["CompletedPartIn"];
export type UploadResult = components["schemas"]["CompleteUploadResponse"];

/** The calls the uploader needs. An interface so tests can substitute an in-memory fake. */
export interface UploadApi {
  create(input: {
    filename: string;
    sizeBytes: number;
    datasetId?: string;
    idempotencyKey: string;
    signal?: AbortSignal;
  }): Promise<UploadTicket>;
  presign(uploadId: string, partNumbers: number[], signal?: AbortSignal): Promise<UploadTicket>;
  complete(
    uploadId: string,
    body: { sha256: string; parts: CompletedPart[] },
    signal?: AbortSignal,
  ): Promise<UploadResult>;
  abort(uploadId: string): Promise<void>;
}

async function call<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`/api/backend/${path}`, {
    ...init,
    credentials: "same-origin",
    headers: { Accept: "application/json", "Content-Type": "application/json", ...init.headers },
  });
  if (!response.ok) throw new ProblemError(await readProblem(response));
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export const browserUploadApi: UploadApi = {
  create: ({ filename, sizeBytes, datasetId, idempotencyKey, signal }) =>
    call<UploadTicket>("v1/uploads", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({
        filename,
        size_bytes: sizeBytes,
        ...(datasetId ? { dataset_id: datasetId } : {}),
      }),
      signal,
    }),
  presign: (uploadId, partNumbers, signal) =>
    call<UploadTicket>(`v1/uploads/${uploadId}:presign-parts`, {
      method: "POST",
      body: JSON.stringify({ part_numbers: partNumbers }),
      signal,
    }),
  complete: (uploadId, body, signal) =>
    call<UploadResult>(`v1/uploads/${uploadId}:complete`, {
      method: "POST",
      body: JSON.stringify(body),
      signal,
    }),
  abort: (uploadId) => call<void>(`v1/uploads/${uploadId}:abort`, { method: "POST" }),
};
