"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { SiteMenu } from "@/components/shell/site-menu";
import { ThemeToggle } from "@/components/shell/theme-toggle";
import { Button } from "@/components/ui/button";
import { authClient } from "@/lib/auth-client";
import type { Theme } from "@/lib/theme";

export function UserMenu({ email, home, theme }: { email: string; home: string; theme?: Theme }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function signOut() {
    setPending(true);
    await authClient.signOut();
    router.replace("/sign-in");
    router.refresh();
  }

  return (
    <div className="flex items-center gap-2 sm:gap-3">
      <span className="hidden max-w-[16rem] truncate text-muted md:inline" title={email}>
        {email}
      </span>
      <ThemeToggle initial={theme} />
      <SiteMenu home={home} onSignOut={signOut} signingOut={pending} />
      {/* On narrow screens "Sign out" moves into the menu, so the header fits one row. */}
      <span className="hidden sm:block">
        <Button variant="secondary" loading={pending} onClick={signOut}>
          {pending ? "Signing out…" : "Sign out"}
        </Button>
      </span>
    </div>
  );
}
