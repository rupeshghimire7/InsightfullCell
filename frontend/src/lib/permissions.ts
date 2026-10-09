import { createAccessControl } from "better-auth/plugins/access";
import { adminAc, defaultStatements, ownerAc } from "better-auth/plugins/organization/access";

/**
 * Organisation roles. Fine-grained authorisation lives in the API (`domain/security.py`); these
 * only control who may manage the organisation itself (members, invitations).
 */
const statement = { ...defaultStatements } as const;

export const ac = createAccessControl(statement);

export const roles = {
  owner: ac.newRole(ownerAc.statements),
  admin: ac.newRole(adminAc.statements),
  analyst: ac.newRole({ organization: [], member: [], invitation: [] }),
  viewer: ac.newRole({ organization: [], member: [], invitation: [] }),
};
