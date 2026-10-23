/** One person on the Contact page, read from `public/content/team.md`. */
export interface TeamMember {
  name: string;
  role?: string;
  linkedin?: string;
  email?: string;
  bio: string[];
}

const EMAIL = /^[^\s@<>()]+@[^\s@<>()]+\.[^\s@<>()]+$/;

/**
 * Parses team.md: `## Name`, then `Key: value` lines (Role, LinkedIn, Email), then the bio, with
 * a blank line between paragraphs. HTML comments are ignored. A link that is not an https URL or
 * an email address is dropped rather than rendered.
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
    let inFields = true;
    const flush = () => {
      if (paragraph.length) member.bio.push(paragraph.join(" "));
      paragraph = [];
    };
    for (const raw of rest) {
      const line = raw.trim();
      const field = inFields ? /^(Role|LinkedIn|Email):\s*(.*)$/i.exec(line) : null;
      if (field) {
        const key = (field[1] ?? "").toLowerCase();
        const value = (field[2] ?? "").trim();
        if (key === "role" && value) member.role = value;
        if (key === "linkedin" && value.startsWith("https://")) member.linkedin = value;
        if (key === "email" && EMAIL.test(value)) member.email = value;
        continue;
      }
      if (!line) {
        if (member.role || member.linkedin || member.email) inFields = false;
        flush();
        continue;
      }
      inFields = false;
      paragraph.push(line);
    }
    flush();
    members.push(member);
  }
  return members;
}
