/** One person on the Contact page, read from `public/content/team.md`. */
export interface TeamMember {
  name: string;
  role?: string;
  linkedin?: string;
  email?: string;
  university?: string;
  program?: string;
  studentId?: string;
  bio: string[];
}

const EMAIL = /^[^\s@<>()]+@[^\s@<>()]+\.[^\s@<>()]+$/;

/** The `Key: value` lines of a person's block (any order, any case, blank lines allowed). */
const FIELD = /^(Role|Bio|LinkedIn|Email|University|Program|Student ID):\s*(.*)$/i;

/**
 * Parses team.md: `## Name`, then `Key: value` lines (Role, Bio, LinkedIn, Email, University,
 * Program, Student ID). Other lines are more bio, a blank line starting a new paragraph. HTML
 * comments are ignored. A link that is not an https URL or an email address is dropped.
 */
export function parseTeam(markdown: string): TeamMember[] {
  const text = markdown.replace(/<!--[\s\S]*?-->/g, "");
  const members: TeamMember[] = [];
  for (const block of text.split(/^## /m).slice(1)) {
    const [heading, ...rest] = block.split("\n");
    const name = (heading ?? "").trim();
    if (!name) continue;
    const member: TeamMember = { name, bio: [] };
    let paragraph: string[] = [];
    const flush = () => {
      if (paragraph.length) member.bio.push(paragraph.join(" "));
      paragraph = [];
    };
    for (const raw of rest) {
      const line = raw.trim();
      const field = FIELD.exec(line);
      if (!field) {
        if (line) paragraph.push(line);
        else flush();
        continue;
      }
      flush();
      const key = (field[1] ?? "").toLowerCase();
      const value = (field[2] ?? "").trim();
      if (!value) continue;
      if (key === "role") member.role = value;
      if (key === "bio") member.bio.push(value);
      if (key === "linkedin" && value.startsWith("https://")) member.linkedin = value;
      if (key === "email" && EMAIL.test(value)) member.email = value;
      if (key === "university") member.university = value;
      if (key === "program") member.program = value;
      if (key === "student id") member.studentId = value;
    }
    flush();
    members.push(member);
  }
  return members;
}
