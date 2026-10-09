import type { Pool } from "pg";
import { describe, expect, it, vi } from "vitest";
import { createIdentity, type OrganizationApi } from "@/lib/identity";

type Row = Record<string, unknown>;

/** A pool whose answers are scripted per SQL fragment. */
function fakePool(answers: { match: RegExp; rows: Row[] }[]) {
  const query = vi.fn(async (sql: string) => {
    const hit = answers.find((a) => a.match.test(sql));
    return { rows: hit?.rows ?? [] };
  });
  return { pool: { query } as unknown as Pool, query };
}

const USER = "0198d8f0-1111-7222-8333-444455556666";
const ORG = "0198d8f0-aaaa-7bbb-8ccc-ddddeeeeffff";

describe("ensureOrganization", () => {
  it("reuses the organisation the user already belongs to", async () => {
    const { pool } = fakePool([{ match: /FROM member/, rows: [{ organizationId: ORG }] }]);
    const api = { createOrganization: vi.fn() } satisfies OrganizationApi;
    expect(await createIdentity(pool, () => api).ensureOrganization(USER)).toBe(ORG);
    expect(api.createOrganization).not.toHaveBeenCalled();
  });

  it("creates a personal workspace on first sign-in", async () => {
    const { pool } = fakePool([{ match: /FROM "user"/, rows: [{ email: "ada@lovelace.dev" }] }]);
    const api = { createOrganization: vi.fn(async () => ({ id: ORG })) };
    expect(await createIdentity(pool, () => api).ensureOrganization(USER)).toBe(ORG);
    expect(api.createOrganization).toHaveBeenCalledWith({
      body: { name: "ada’s workspace", slug: "ws-0198d8f01111", userId: USER },
    });
  });

  it("recovers when a parallel sign-in created the workspace first", async () => {
    let lookups = 0;
    const query = vi.fn(async (sql: string) => {
      if (/FROM member/.test(sql)) {
        lookups += 1;
        return { rows: lookups > 1 ? [{ organizationId: ORG }] : [] };
      }
      return { rows: [{ email: "ada@lovelace.dev" }] };
    });
    const api = {
      createOrganization: vi.fn(async () => {
        throw new Error("duplicate slug");
      }),
    };
    const identity = createIdentity({ query } as unknown as Pool, () => api);
    expect(await identity.ensureOrganization(USER)).toBe(ORG);
  });

  it("surfaces the original error when creation really failed", async () => {
    const { pool } = fakePool([{ match: /FROM "user"/, rows: [{ email: "a@b.co" }] }]);
    const boom = new Error("database down");
    const api = { createOrganization: vi.fn(async () => Promise.reject(boom)) };
    await expect(createIdentity(pool, () => api).ensureOrganization(USER)).rejects.toBe(boom);
  });
});

describe("tokenClaims", () => {
  const api: OrganizationApi = { createOrganization: vi.fn() };

  it("returns the organisation, its name and the most privileged role", async () => {
    const { pool, query } = fakePool([
      { match: /FROM member m/, rows: [{ role: "viewer,admin", name: "Acme" }] },
    ]);
    const claims = await createIdentity(pool, () => api).tokenClaims(USER, ORG);
    expect(claims).toEqual({ org_id: ORG, org_name: "Acme", role: "admin" });
    // the lookup is keyed on both the user and the organisation
    expect(query).toHaveBeenCalledWith(expect.any(String), [USER, ORG]);
  });

  it("falls back to the least privileged role for unknown role names", async () => {
    const { pool } = fakePool([
      { match: /FROM member m/, rows: [{ role: "superuser", name: "Acme" }] },
    ]);
    const claims = await createIdentity(pool, () => api).tokenClaims(USER, ORG);
    expect(claims).toMatchObject({ role: "viewer" });
  });

  it("issues no tenant claims without an active organisation", async () => {
    const { pool, query } = fakePool([]);
    expect(await createIdentity(pool, () => api).tokenClaims(USER, null)).toEqual({});
    expect(query).not.toHaveBeenCalled();
  });

  it("issues no tenant claims to someone who is not a member (the API then rejects the token)", async () => {
    const { pool } = fakePool([{ match: /FROM member m/, rows: [] }]);
    expect(await createIdentity(pool, () => api).tokenClaims(USER, ORG)).toEqual({});
  });
});
