import { configureAxe } from "vitest-axe";

/**
 * jsdom has no layout or canvas, so axe cannot measure colour contrast here. Contrast is
 * verified from the design tokens instead (tests/lib/contrast.test.ts).
 */
export const axe = configureAxe({ rules: { "color-contrast": { enabled: false } } });
