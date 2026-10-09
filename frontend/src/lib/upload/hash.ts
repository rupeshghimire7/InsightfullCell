import type { HashMessage } from "@/lib/upload/hash.worker";

export type HashFile = (file: File, signal?: AbortSignal) => Promise<string>;

/** SHA-256 of a file, computed in a Web Worker so the interface stays responsive. */
export const hashFile: HashFile = (file, signal) =>
  new Promise<string>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Cancelled", "AbortError"));
      return;
    }
    const worker = new Worker(new URL("./hash.worker.ts", import.meta.url), { type: "module" });
    const cleanup = () => {
      signal?.removeEventListener("abort", onAbort);
      worker.terminate();
    };
    const onAbort = () => {
      cleanup();
      reject(new DOMException("Cancelled", "AbortError"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });

    worker.onmessage = (event: MessageEvent<HashMessage>) => {
      const message = event.data;
      if (message.type === "done") {
        cleanup();
        resolve(message.sha256);
      } else if (message.type === "error") {
        cleanup();
        reject(new Error(message.message));
      }
    };
    worker.onerror = () => {
      cleanup();
      reject(new Error("Could not compute the file checksum"));
    };
    worker.postMessage({ file });
  });
