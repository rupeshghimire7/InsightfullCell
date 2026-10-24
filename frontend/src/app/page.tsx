import { redirect } from "next/navigation";
import { ServiceUnavailable } from "@/components/ui/service-unavailable";
import { getEnv } from "@/lib/env";
import { getSessionOrUnavailable, requirePassword } from "@/lib/session";

/**
 * Signed-in users land in their active organisation (after choosing a password, if they have
 * none yet); everyone else signs in.
 */
export default async function RootPage() {
  const session = await getSessionOrUnavailable();
  if (session === "unavailable") {
    return <ServiceUnavailable retryHref="/" developerHint={getEnv().APP_ENV === "local"} />;
  }
  if (!session) redirect("/sign-in");
  await requirePassword(session);
  redirect(`/o/${session.organizationId}`);
}
