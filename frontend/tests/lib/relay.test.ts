import { describe, expect, it } from "vitest";
import { eventsRelayPath, isLoopbackHost, needsRelay, partUploadUrl } from "@/lib/relay";

describe("relay", () => {
  it("recognises loopback hosts", () => {
    for (const host of ["localhost", "app.localhost", "127.0.0.1", "127.1.2.3", "[::1]"]) {
      expect(isLoopbackHost(host)).toBe(true);
    }
    for (const host of ["example.ngrok-free.dev", "10.0.0.5", "localhost.example.com"]) {
      expect(isLoopbackHost(host)).toBe(false);
    }
  });

  it("relays only a loopback target from a page that is not on loopback", () => {
    expect(needsRelay("http://localhost:9000/b/k", "abc.ngrok-free.dev")).toBe(true);
    expect(needsRelay("http://localhost:9000/b/k", "localhost")).toBe(false);
    expect(needsRelay("https://s3.example.com/b/k", "abc.ngrok-free.dev")).toBe(false);
  });

  it("keeps the signed path and query of a relayed part", () => {
    const url = "http://localhost:9000/bucket/uploads/a%20b.csv?partNumber=1&X-Amz-Signature=x";
    expect(partUploadUrl(url, "abc.ngrok-free.dev")).toBe(
      "/api/storage/bucket/uploads/a%20b.csv?partNumber=1&X-Amz-Signature=x",
    );
    expect(partUploadUrl(url, "localhost")).toBe(url);
  });

  it("names the events relay of a run", () => {
    expect(eventsRelayPath("r1")).toBe("/api/runs/r1/events");
  });
});
