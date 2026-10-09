import { describe, expect, it, vi } from "vitest";
import { load } from "@/lib/load";

const response = (status: number) => new Response(null, { status });

describe("load", () => {
  it("returns data with its status", async () => {
    expect(await load("x", async () => ({ data: { a: 1 }, response: response(200) }))).toEqual({
      data: { a: 1 },
      status: 200,
    });
  });

  it("turns a problem response into a renderable problem", async () => {
    const result = await load("x", async () => ({
      error: { title: "Nope", detail: "Gone", hint: null, request_id: "r1" },
      response: response(404),
    }));
    expect(result).toEqual({
      status: 404,
      error: { title: "Nope", detail: "Gone", hint: undefined, request_id: "r1" },
    });
  });

  it("suggests signing in again on 401 instead of redirecting", async () => {
    const result = await load("x", async () => ({ error: {}, response: response(401) }));
    expect(result.error?.hint).toBe("Sign out, then sign in again.");
  });

  it("reports an unreachable API as unavailable without throwing", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const result = await load("your runs", async () => {
      throw new TypeError("fetch failed");
    });
    expect(result.status).toBe(503);
    expect(result.error?.detail).toContain("your runs");
    expect(spy).toHaveBeenCalled();
  });
});
