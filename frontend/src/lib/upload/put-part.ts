import { partUploadUrl } from "@/lib/relay";

/** Raised for failures worth retrying (network drop, 5xx, throttling). */
export class TransientPutError extends Error {}
/** The presigned URL was rejected (expired or invalid): fetch a fresh one. */
export class ExpiredUrlError extends Error {}
/** Not retryable: surfaces to the user. */
export class PermanentPutError extends Error {}

export type PutPart = (
  url: string,
  body: Blob,
  onProgress: (loadedBytes: number) => void,
  signal?: AbortSignal,
) => Promise<string>;

/**
 * PUT one part straight to object storage (through the web app's relay when storage is only on
 * localhost and the page is not) and return its ETag. XMLHttpRequest, not fetch, because only XHR
 * reports upload progress.
 */
export const putPart: PutPart = (url, body, onProgress, signal) =>
  new Promise<string>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Cancelled", "AbortError"));
      return;
    }
    const xhr = new XMLHttpRequest();
    const onAbort = () => xhr.abort();
    signal?.addEventListener("abort", onAbort, { once: true });
    const finish = () => signal?.removeEventListener("abort", onAbort);

    xhr.open("PUT", partUploadUrl(url, window.location.hostname));
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded);
    };
    xhr.onload = () => {
      finish();
      if (xhr.status >= 200 && xhr.status < 300) {
        const etag = xhr.getResponseHeader("ETag");
        if (!etag) {
          reject(
            new PermanentPutError(
              "Storage did not return an ETag. Its CORS policy must expose the ETag header.",
            ),
          );
          return;
        }
        resolve(etag);
      } else if (xhr.status === 403 || xhr.status === 401) {
        reject(new ExpiredUrlError("The upload link expired."));
      } else if (xhr.status === 408 || xhr.status === 429 || xhr.status >= 500) {
        reject(new TransientPutError(`Storage responded with HTTP ${xhr.status}.`));
      } else {
        reject(new PermanentPutError(`Storage rejected the upload (HTTP ${xhr.status}).`));
      }
    };
    xhr.onerror = () => {
      finish();
      reject(new TransientPutError("The connection to storage dropped."));
    };
    xhr.ontimeout = xhr.onerror;
    xhr.onabort = () => {
      finish();
      reject(new DOMException("Cancelled", "AbortError"));
    };
    xhr.send(body);
  });
