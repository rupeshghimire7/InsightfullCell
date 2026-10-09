import { ProblemAlert } from "@/components/ui/problem-alert";

export interface ServiceUnavailableProps {
  /** Where "Try again" goes (the page the user asked for). */
  retryHref: string;
  /** Local development: name the likely cause and the command that fixes it. */
  developerHint?: boolean;
}

/**
 * Full-page notice for when the sign-in database cannot be reached. A plain link, not a client
 * component: it must render even when nothing else on the server works.
 */
export function ServiceUnavailable({ retryHref, developerHint = false }: ServiceUnavailableProps) {
  return (
    <main id="main" className="mx-auto max-w-xl px-4 py-16">
      <h1 className="sr-only">Service unavailable</h1>
      <ProblemAlert
        title="Sign-in is unavailable right now"
        detail="We couldn’t reach the database that holds accounts and sessions."
        hint={
          developerHint
            ? "PostgreSQL isn’t reachable. Start it with “make up” (and run “make migrate” on a new database), then try again."
            : "This is on our side. Try again in a minute."
        }
        action={
          <a
            href={retryHref}
            className="inline-flex min-h-6 items-center font-medium text-accent underline underline-offset-2 pointer-coarse:min-h-11"
          >
            Try again
          </a>
        }
      />
    </main>
  );
}
