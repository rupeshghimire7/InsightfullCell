import "server-only";
import { readFile } from "node:fs/promises";
import path from "node:path";

/**
 * Reads an editable text file from `public/content/` (the team list, the licence). The folder
 * ships in the web image with `public/`, so a change needs only a redeploy, no code edit.
 * `null` when the file is missing, so a page can say so instead of failing.
 */
export async function readContent(name: string): Promise<string | null> {
  try {
    return await readFile(path.join(process.cwd(), "public", "content", name), "utf8");
  } catch {
    return null;
  }
}
