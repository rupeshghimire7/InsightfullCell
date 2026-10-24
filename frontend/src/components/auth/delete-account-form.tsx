"use client";

import { useActionState, useEffect, useId, useRef, useState } from "react";
import { Field, PasswordInput, useHydrated } from "@/components/auth/sign-in-form";
import { Button } from "@/components/ui/button";
import { Trash } from "@/components/ui/icons";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { deleteAccountAction, type DeleteAccountState } from "@/app/o/[org]/privacy/actions";

/**
 * "Delete my account": a confirmation in place (focus on the safe choice), with the password,
 * before the server action erases everything. Nothing can be undone afterwards.
 */
export function DeleteAccountForm() {
  const hydrated = useHydrated();
  const descriptionId = useId();
  const keepRef = useRef<HTMLButtonElement>(null);
  const [confirming, setConfirming] = useState(false);
  const [result, submit, pending] = useActionState<DeleteAccountState, FormData>(
    deleteAccountAction,
    {},
  );

  useEffect(() => {
    if (confirming) keepRef.current?.focus();
  }, [confirming]);

  if (!confirming) {
    return (
      <Button variant="danger" onClick={() => setConfirming(true)} disabled={!hydrated}>
        <Trash aria-hidden="true" className="size-4" />
        Delete my account…
      </Button>
    );
  }
  return (
    <form
      action={submit}
      noValidate
      aria-label="Confirm deleting your account"
      aria-describedby={descriptionId}
      className="max-w-md space-y-4 rounded-panel border border-danger/40 bg-surface p-4"
    >
      <p id={descriptionId} className="text-sm">
        Enter your password to delete your account and everything in it.
      </p>
      {result.error && !result.field ? (
        <ProblemAlert title="Your account wasn’t deleted" detail={result.error} />
      ) : null}
      <Field label="Password" error={result.field === "password" ? result.error : undefined}>
        {(props) => <PasswordInput {...props} name="password" autoComplete="current-password" />}
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="danger" loading={pending}>
          {pending ? "Deleting everything…" : "Delete my account permanently"}
        </Button>
        <Button
          ref={keepRef}
          variant="secondary"
          disabled={pending}
          onClick={() => setConfirming(false)}
        >
          Keep my account
        </Button>
      </div>
    </form>
  );
}
