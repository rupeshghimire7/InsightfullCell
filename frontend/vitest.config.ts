import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      "server-only": fileURLToPath(new URL("./tests/stubs/server-only.ts", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
    restoreMocks: true,
    coverage: {
      provider: "v8",
      include: ["src/lib/**", "src/components/**"],
      // Modules that only run inside Next.js servers or a real browser/worker are exercised by the
      // end-to-end smoke test (scripts/e2e-smoke.sh), not by jsdom unit tests.
      exclude: [
        "src/lib/api/schema.d.ts",
        "src/lib/auth.ts",
        "src/lib/auth-options.ts",
        "src/lib/auth-client.ts",
        "src/lib/backend.ts",
        "src/lib/session.ts",
        "src/lib/upload/hash.ts",
        "src/lib/upload/hash.worker.ts",
      ],
      thresholds: { lines: 80, functions: 80, branches: 75, statements: 80 },
    },
  },
});
