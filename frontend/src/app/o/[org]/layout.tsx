import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { AppShell } from "@/components/shell/app-shell";
import { ServiceUnavailable } from "@/components/ui/service-unavailable";
import { getEnv } from "@/lib/env";
import { getSessionOrUnavailable } from "@/lib/session";

export default async function OrganisationLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ org: string }>;
}) {
  const session = await getSessionOrUnavailable();
  const { org } = await params;
  if (session === "unavailable") {
    return (
      <ServiceUnavailable retryHref={`/o/${org}`} developerHint={getEnv().APP_ENV === "local"} />
    );
  }
  if (!session) redirect("/sign-in");
  // The URL never grants access: the API trusts the token's organisation, so a mismatched URL
  // is only ever a stale link. Send the user to the organisation they are actually in.
  if (org !== session.organizationId) redirect(`/o/${session.organizationId}`);
  return (
    <AppShell orgId={session.organizationId} email={session.email}>
      {children}
    </AppShell>
  );
}
