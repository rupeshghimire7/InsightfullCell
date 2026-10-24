"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { authClient } from "@/lib/auth-client";

/** "Sign out", as a link-styled button, for pages outside the app shell. */
export function SignOutLink() {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  return (
    <button
      type="button"
      disabled={pending}
      onClick={async () => {
        setPending(true);
        await authClient.signOut();
        router.replace("/sign-in");
        router.refresh();
      }}
      className="inline-flex min-h-6 items-center font-medium text-accent underline underline-offset-2 disabled:opacity-70 pointer-coarse:min-h-11"
    >
      {pending ? "Signing out…" : "Sign out"}
    </button>
  );
}
