/**
 * Start the production server from the `standalone` build output (what the Docker image runs).
 * Next.js does not copy static assets into `.next/standalone`; this does, then starts it.
 */
import { spawn } from "node:child_process";
import { cpSync, existsSync } from "node:fs";
import { resolve } from "node:path";

const standalone = resolve(".next/standalone");
if (!existsSync(resolve(standalone, "server.js"))) {
  console.error("No standalone build found. Run `pnpm build` first.");
  process.exit(1);
}
cpSync(resolve(".next/static"), resolve(standalone, ".next/static"), { recursive: true });
if (existsSync(resolve("public")))
  cpSync(resolve("public"), resolve(standalone, "public"), { recursive: true });

const child = spawn(process.execPath, [resolve(standalone, "server.js")], {
  stdio: "inherit",
  cwd: standalone,
  env: {
    ...process.env,
    HOSTNAME: process.env.HOSTNAME ?? "0.0.0.0",
    PORT: process.env.PORT ?? "3000",
  },
});
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => child.kill(signal));
child.on("exit", (code) => process.exit(code ?? 0));
