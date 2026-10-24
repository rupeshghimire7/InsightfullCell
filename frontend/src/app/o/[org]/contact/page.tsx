import type { Metadata } from "next";
import { EnvelopeSimple, LinkedinLogo } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { ProblemAlert } from "@/components/ui/problem-alert";
import { readContent } from "@/lib/content";
import { parseTeam } from "@/lib/team";

export const metadata: Metadata = { title: "Contact" };

const LINK =
  "inline-flex min-h-9 items-center gap-2 rounded-control border border-border bg-surface px-3 text-sm font-medium hover:bg-bg pointer-coarse:min-h-11";

/** The people behind InsightfulCell, from `public/content/team.md` (see that file's format). */
export default async function ContactPage() {
  const markdown = await readContent("team.md");
  const team = markdown ? parseTeam(markdown) : [];
  return (
    <>
      <PageHeader title="Contact" />
      {team.length === 0 ? (
        <ProblemAlert
          title="The contact list isn’t available"
          detail="The team file couldn’t be read."
          hint="Try again later."
        />
      ) : (
        <ul className="grid gap-4 md:grid-cols-2">
          {team.map((member) => (
            <li
              key={member.name}
              className="space-y-3 rounded-panel border border-border bg-surface p-5"
            >
              <div>
                <h2 className="text-lg font-semibold">{member.name}</h2>
                {member.role ? <p className="text-muted">{member.role}</p> : null}
              </div>
              {member.bio.map((paragraph) => (
                <p key={paragraph} className="max-w-[65ch] leading-relaxed">
                  {paragraph}
                </p>
              ))}
              {member.linkedin || member.email ? (
                <p className="flex flex-wrap gap-2">
                  {member.linkedin ? (
                    <a
                      href={member.linkedin}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={LINK}
                    >
                      <LinkedinLogo aria-hidden="true" className="size-4" />
                      LinkedIn
                      <span className="sr-only">: {member.name} (opens in a new tab)</span>
                    </a>
                  ) : null}
                  {member.email ? (
                    <a href={`mailto:${member.email}`} className={LINK}>
                      <EnvelopeSimple aria-hidden="true" className="size-4" />
                      {member.email}
                    </a>
                  ) : null}
                </p>
              ) : null}
              {member.university || member.program || member.studentId ? (
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                  {member.university ? (
                    <>
                      <dt className="text-muted">University</dt>
                      <dd>{member.university}</dd>
                    </>
                  ) : null}
                  {member.program ? (
                    <>
                      <dt className="text-muted">Program</dt>
                      <dd>{member.program}</dd>
                    </>
                  ) : null}
                  {member.studentId ? (
                    <>
                      <dt className="text-muted">Student ID</dt>
                      <dd>{member.studentId}</dd>
                    </>
                  ) : null}
                </dl>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
