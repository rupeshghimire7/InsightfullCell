"use client";

import { useId, useState } from "react";
import { PasswordChecklist } from "@/components/auth/password-checklist";
import { Field, PasswordInput } from "@/components/auth/sign-in-form";
import { passwordPolicyError } from "@/lib/password-policy";

export const PASSWORDS_DIFFER = "The passwords don’t match.";
export const CONFIRM_MISSING = "Type the new password again.";

/** The new password and its confirmation, checked with the one shared policy as they are typed. */
export function useNewPassword(email?: string | null) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const problem = passwordPolicyError(password, email);
  const confirmProblem = !confirm
    ? CONFIRM_MISSING
    : confirm !== password
      ? PASSWORDS_DIFFER
      : null;
  return {
    password,
    setPassword,
    confirm,
    setConfirm,
    /** What is wrong with the new password; `null` when it meets the policy. */
    problem,
    confirmProblem,
    valid: !problem && !confirmProblem,
    reset: () => {
      setPassword("");
      setConfirm("");
    },
  };
}

export type NewPassword = ReturnType<typeof useNewPassword>;

/**
 * "New password" with its live checklist, then "Confirm new password". Errors show only once the
 * form says so (`errors`), so nobody is scolded while still typing.
 */
export function NewPasswordFields({
  state,
  email,
  errors = {},
  label = "New password",
  confirmLabel = "Confirm new password",
}: {
  state: NewPassword;
  email?: string | null;
  errors?: { next?: string; confirm?: string };
  label?: string;
  confirmLabel?: string;
}) {
  const checklistId = useId();
  return (
    <>
      <div>
        <Field label={label} error={errors.next} describedBy={checklistId}>
          {(props) => (
            <PasswordInput
              {...props}
              name="newPassword"
              autoComplete="new-password"
              value={state.password}
              onChange={(event) => state.setPassword(event.currentTarget.value)}
            />
          )}
        </Field>
        <PasswordChecklist id={checklistId} password={state.password} email={email} />
      </div>
      <Field label={confirmLabel} error={errors.confirm}>
        {(props) => (
          <PasswordInput
            {...props}
            name="confirmPassword"
            autoComplete="new-password"
            value={state.confirm}
            onChange={(event) => state.setConfirm(event.currentTarget.value)}
          />
        )}
      </Field>
    </>
  );
}
