import { formatChartValue } from "@/lib/report/format";
import type { ReportMap } from "@/lib/report/types";

/**
 * A map's colour steps: five equal-count groups of the values shown, lightest to darkest (the
 * same rule as the HTML export, `exports/svg.py`). Colours are mixes of the chart colour with the
 * surface, so they follow the theme.
 */
export const STEP_COUNT = 5;
export const STEP_FILLS = [18, 36, 56, 76, 100].map(
  (pct) => `color-mix(in oklab, var(--chart) ${pct}%, var(--surface))`,
);

export function thresholds(values: readonly number[]): number[] {
  const ordered = [...values].sort((a, b) => a - b);
  if (ordered.length === 0) return [];
  const out: number[] = [];
  for (let k = 1; k < STEP_COUNT; k++) {
    const index = Math.min(ordered.length - 1, Math.ceil((ordered.length * k) / STEP_COUNT) - 1);
    out.push(ordered[index] ?? 0);
  }
  return out;
}

export function stepOf(value: number, bounds: readonly number[]): number {
  const index = bounds.findIndex((bound) => value <= bound);
  return index === -1 ? STEP_COUNT - 1 : index;
}

export interface LegendStep {
  step: number;
  label: string;
}

/** The steps that have regions, low to high, each labelled with the range of its values. */
export function legend(map: ReportMap): LegendStep[] {
  const shown = shownValues(map);
  if (shown.length === 0) return [];
  const bounds = thresholds(shown);
  const steps: LegendStep[] = [];
  for (let step = 0; step < STEP_COUNT; step++) {
    const members = shown.filter((v) => stepOf(v, bounds) === step);
    if (members.length === 0) continue;
    const low = formatChartValue(Math.min(...members), map.value_format, map.unit);
    const high = formatChartValue(Math.max(...members), map.value_format, map.unit);
    steps.push({ step, label: low === high ? low : `${low} to ${high}` });
  }
  return steps;
}

export function shownValues(map: ReportMap): number[] {
  return map.regions
    .filter((r) => r.status === "value" && r.value !== null)
    .map((r) => r.value as number);
}
