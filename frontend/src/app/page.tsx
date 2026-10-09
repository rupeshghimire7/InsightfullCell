import { redirect } from "next/navigation";
import { ServiceUnavailable } from "@/components/ui/service-unavailable";
import { getEnv } from "@/lib/env";
import { getSessionOrUnavailable } from "@/lib/session";

/** Signed-in users land in their active organisation; everyone else signs in. */
export default async function RootPage() {
  const session = await getSessionOrUnavailable();
  if (session === "unavailable") {
    return <ServiceUnavailable retryHref="/" developerHint={getEnv().APP_ENV === "local"} />;
  }
  redirect(session ? `/o/${session.organizationId}` : "/sign-in");
}
