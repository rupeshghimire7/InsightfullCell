import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import jsxA11y from "eslint-plugin-jsx-a11y";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  globalIgnores([
    ".next/**",
    "coverage/**",
    // Playwright's traces and reports (bundled third-party scripts) after an e2e run.
    "test-results/**",
    "playwright-report/**",
    "node_modules/**",
    "next-env.d.ts",
    "src/lib/api/schema.d.ts",
  ]),
  {
    // eslint-config-next registers the jsx-a11y plugin with a handful of rules; turn on the
    // full recommended set (WCAG 2.2 AA is a requirement, 08-ui-ux-spec.md section 7).
    files: ["**/*.{ts,tsx}"],
    rules: {
      ...jsxA11y.flatConfigs.recommended.rules,
      // A scrolling container must be reachable by keyboard (axe: scrollable-region-focusable),
      // so a labelled role="region" may take tabIndex={0}.
      "jsx-a11y/no-noninteractive-tabindex": ["error", { tags: [], roles: ["tabpanel", "region"] }],
      "@typescript-eslint/consistent-type-imports": ["error", { fixStyle: "inline-type-imports" }],
      "@typescript-eslint/no-floating-promises": "off",
      "no-console": ["error", { allow: ["warn", "error"] }],
      eqeqeq: ["error", "always"],
      "no-restricted-syntax": [
        "error",
        {
          selector: "Literal[value=/\\u2014/]",
          message: "No em dashes in UI strings (08-ui-ux-spec.md section 6).",
        },
      ],
    },
  },
  {
    files: ["scripts/**", "tests/**"],
    rules: { "no-console": "off" },
  },
]);
