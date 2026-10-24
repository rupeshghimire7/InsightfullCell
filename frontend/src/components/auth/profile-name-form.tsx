"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";
import { Field, settle, transportError, useHydrated } from "@/components/auth/sign-in-form";
import { Button } from "@/components/ui/button";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { authClient } from "@/lib/auth-client";

type Status = { kind: "idle" | "busy" | "saved" } | { kind: "error"; message: string };

/**
 * The name on the account, editable. The email address is shown beside it, read-only: it is
 * what the account signs in with and is never changed (the server refuses it too).
 */
export function ProfileNameForm({ name, email }: { name: string; email: string }) {
  const router = useRouter();
  const hydrated = useHydrated();
  const input = useRef<HTMLInputElement>(null);
  const [fieldError, setFieldError] = useState<string>();
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (status.kind === "busy") return;
    const next = (input.current?.value ?? "").trim();
    if (!next) {
      setFieldError("Enter your name.");
      input.current?.focus();
      return;
    }
    setFieldError(undefined);
    setStatus({ kind: "busy" });
    const error = await settle(() => authClient.updateUser({ name: next }));
    if (error) {
      setStatus({
        kind: "error",
        message: transportError(error) ?? "We couldn’t save your name. Try again.",
      });
      return;
    }
    setStatus({ kind: "saved" });
    router.refresh();
  }

  return (
    <form onSubmit={onSubmit} method="post" noValidate className="max-w-sm space-y-4">
      {status.kind === "error" ? (
        <ProblemAlert title="Your name wasn’t saved" detail={status.message} />
      ) : null}
      <Field label="Name" error={fieldError}>
        {(props) => (
          <input
            {...props}
            ref={input}
            name="name"
            type="text"
            autoComplete="name"
            maxLength={100}
            defaultValue={name}
            onChange={() => status.kind === "saved" && setStatus({ kind: "idle" })}
          />
        )}
      </Field>
      <Field label="Email address" hint="Your email can’t be changed: it’s how you sign in.">
        {(props) => (
          <input {...props} name="email" type="email" value={email} readOnly aria-readonly="true" />
        )}
      </Field>
      <div className="flex items-center gap-3">
        <Button type="submit" loading={status.kind === "busy"} disabled={!hydrated}>
          {status.kind === "busy" ? "Saving…" : "Save name"}
        </Button>
        <p role="status" className="text-sm text-muted">
          {status.kind === "saved" ? "Saved." : ""}
        </p>
      </div>
    </form>
  );
}
