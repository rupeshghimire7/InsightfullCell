"use client";

import { useActionState } from "react";
import { NewPasswordFields, useNewPassword } from "@/components/auth/new-password-fields";
import { useHydrated } from "@/components/auth/sign-in-form";
import { Button } from "@/components/ui/button";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { setPasswordAction, type SetPasswordState } from "@/app/set-password/actions";

/**
 * The first password, chosen once after registering with a code, Google or a link. The live
 * checklist shows the shared policy, and the button stays disabled until the password meets it
 * and the confirmation matches. The server action checks both again.
 */
export function SetPasswordForm({ email }: { email: string }) {
  const hydrated = useHydrated();
  const state = useNewPassword(email);
  const [result, submit, pending] = useActionState<SetPasswordState, FormData>(
    setPasswordAction,
    {},
  );
  const errors = {
    next: result.field === "newPassword" ? result.error : undefined,
    confirm: result.field === "confirmPassword" ? result.error : undefined,
  };
  return (
    <form action={submit} noValidate className="space-y-4">
      {result.error && !result.field ? (
        <ProblemAlert title="The password wasn’t saved" detail={result.error} />
      ) : null}
      <NewPasswordFields
        state={state}
        email={email}
        errors={errors}
        label="Password"
        confirmLabel="Confirm password"
      />
      <Button
        type="submit"
        loading={pending}
        disabled={!hydrated || !state.valid}
        className="w-full"
      >
        {pending ? "Saving…" : "Save password and continue"}
      </Button>
    </form>
  );
}
