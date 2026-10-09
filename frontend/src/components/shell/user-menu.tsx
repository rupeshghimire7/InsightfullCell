"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { authClient } from "@/lib/auth-client";

export function UserMenu({ email }: { email: string }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function signOut() {
    setPending(true);
    await authClient.signOut();
    router.replace("/sign-in");
    router.refresh();
  }

  return (
    <div className="flex items-center gap-3">
      <span className="hidden max-w-[16rem] truncate text-muted sm:inline" title={email}>
        {email}
      </span>
      <Button variant="secondary" loading={pending} onClick={signOut}>
        {pending ? "Signing out…" : "Sign out"}
      </Button>
    </div>
  );
}
