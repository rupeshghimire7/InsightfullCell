import type { Pool } from "pg";
import { effectiveRole, type Role } from "@/lib/roles";

/** The slice of the auth API the session hook needs; bound after the instance exists. */
export interface OrganizationApi {
  createOrganization(input: {
    body: { name: string; slug: string; userId: string };
  }): Promise<{ id: string }>;
}

/** Personal workspace slug: deterministic, so a retried or racing first sign-in finds it. */
export function workspaceSlug(userId: string): string {
  return `ws-${userId.replaceAll("-", "").slice(0, 12)}`;
}

export function workspaceName(email: string | null | undefined): string {
  const local = (email ?? "").split("@")[0]?.trim();
  return local ? `${local}\u2019s workspace` : "My workspace";
}

export interface TokenClaims {
  org_id: string;
  org_name: string;
  role: Role;
}

/**
 * Who a user is within the organisation model, read from Better Auth's tables. Kept separate from
 * the Better Auth configuration so the rules that decide what the API will trust are unit-tested.
 */
export function createIdentity(pool: Pool, getApi: () => OrganizationApi) {
  async function firstOrganizationId(userId: string): Promise<string | undefined> {
    const { rows } = await pool.query<{ organizationId: string }>(
      `SELECT "organizationId" FROM member WHERE "userId" = $1 ORDER BY "createdAt", id LIMIT 1`,
      [userId],
    );
    return rows[0]?.organizationId;
  }

  return {
    /** First sign-in creates a personal workspace so every session has an organisation. */
    async ensureOrganization(userId: string): Promise<string> {
      const existing = await firstOrganizationId(userId);
      if (existing) return existing;
      const user = await pool.query<{ email: string }>(`SELECT email FROM "user" WHERE id = $1`, [
        userId,
      ]);
      try {
        const created = await getApi().createOrganization({
          body: {
            name: workspaceName(user.rows[0]?.email),
            slug: workspaceSlug(userId),
            userId,
          },
        });
        return created.id;
      } catch (error) {
        // Lost a race with a parallel first sign-in: the other request created it.
        const raced = await firstOrganizationId(userId);
        if (raced) return raced;
        throw error;
      }
    },

    /**
     * The claims the API trusts. Empty when the session has no organisation or the user is not
     * (or no longer) a member of it, so a removed member's token is rejected by the API.
     */
    async tokenClaims(
      userId: string,
      organizationId: string | null | undefined,
    ): Promise<TokenClaims | Record<string, never>> {
      if (!organizationId) return {};
      const { rows } = await pool.query<{ role: string; name: string }>(
        `SELECT m.role, o.name FROM member m
           JOIN organization o ON o.id = m."organizationId"
          WHERE m."userId" = $1 AND m."organizationId" = $2`,
        [userId, organizationId],
      );
      const row = rows[0];
      if (!row) return {};
      return { org_id: organizationId, org_name: row.name, role: effectiveRole(row.role) };
    },
  };
}
