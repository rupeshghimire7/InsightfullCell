/// <reference lib="webworker" />
import { createSHA256 } from "hash-wasm";

/**
 * Streams a File through SHA-256 off the main thread. Web Crypto cannot hash incrementally, and
 * reading a 250 MB file into one buffer would freeze the tab, so hash-wasm hashes it in slices.
 */
const SLICE_BYTES = 4 * 1024 * 1024;

export type HashRequest = { file: File };
export type HashMessage =
  | { type: "progress"; hashedBytes: number }
  | { type: "done"; sha256: string }
  | { type: "error"; message: string };

self.onmessage = async (event: MessageEvent<HashRequest>) => {
  const { file } = event.data;
  try {
    const hasher = await createSHA256();
    hasher.init();
    for (let offset = 0; offset < file.size; offset += SLICE_BYTES) {
      const chunk = await file.slice(offset, offset + SLICE_BYTES).arrayBuffer();
      hasher.update(new Uint8Array(chunk));
      self.postMessage({
        type: "progress",
        hashedBytes: Math.min(offset + SLICE_BYTES, file.size),
      } satisfies HashMessage);
    }
    self.postMessage({ type: "done", sha256: hasher.digest("hex") } satisfies HashMessage);
  } catch (error) {
    self.postMessage({
      type: "error",
      message: error instanceof Error ? error.message : "Hashing failed",
    } satisfies HashMessage);
  }
};
