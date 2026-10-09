/** Role names shared with the API (`backend/src/insightfulcell/domain/security.py`). */
export const ROLES = ["owner", "admin", "analyst", "viewer"] as const;
export type Role = (typeof ROLES)[number];

/**
 * A member row may hold several comma-separated roles. The API receives exactly one: the most
 * privileged known role, or `viewer` (least privilege) when nothing is recognised.
 */
export function effectiveRole(stored: string | null | undefined): Role {
  const held = new Set((stored ?? "").split(",").map((part) => part.trim()));
  return ROLES.find((role) => held.has(role)) ?? "viewer";
}
