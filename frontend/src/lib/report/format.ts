import type { Chart, ReportFact } from "@/lib/report/types";

/**
 * Chart values written the way the facts write them (engine/analysis/facts.py): shares as
 * whole percent (one decimal below ten), measures with three significant figures, currency
 * symbols in front and other units after. Ticks use the same rules, so an axis never shows a
 * number in a different style from the sentence beside it.
 */

const CURRENCY = new Set(["$", "€", "£", "¥"]);

function share(fraction: number): string {
  const pct = fraction * 100;
  const abs = Math.abs(pct);
  return abs > 0 && abs < 10 ? `${pct.toFixed(1)}%` : `${Math.round(pct)}%`;
}

function plain(value: number): string {
  if (!Number.isFinite(value)) return "n/a";
  if (Math.abs(value) >= 100 || Number.isInteger(value)) {
    return Math.round(value).toLocaleString("en-US");
  }
  return Number(value.toPrecision(3)).toString();
}

export function formatChartValue(
  value: number,
  format: Chart["value_format"],
  unit: string | null,
): string {
  switch (format) {
    case "percent":
      return share(value);
    case "signed_percent":
      return value > 0 ? `+${share(value)}` : share(value);
    case "count":
      return Math.round(value).toLocaleString("en-US");
    case "strength":
      return value.toFixed(2);
    default: {
      const text = plain(value);
      if (!unit || unit === "%") return text;
      if (CURRENCY.has(unit))
        return text.startsWith("-") ? `-${unit}${text.slice(1)}` : `${unit}${text}`;
      return `${text} ${unit}`;
    }
  }
}

/** Three to five round ticks between min and max (0 always included when in range). */
export function niceTicks(min: number, max: number, count = 4): number[] {
  if (min === max) return [min];
  const span = max - min;
  const rough = span / count;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step =
    [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => span / s <= count) ?? power * 10;
  const start = Math.ceil(min / step) * step;
  const ticks: number[] = [];
  for (let t = start; t <= max + step * 1e-9; t += step) ticks.push(Number(t.toPrecision(12)));
  return ticks;
}

/** How strong a fact's evidence is, in words (colour is never the only signal). */
export const TIER: Record<
  ReportFact["tier"],
  { label: string; tone: "positive" | "neutral" | "attention" }
> = {
  strong: { label: "Strong evidence", tone: "positive" },
  supported: { label: "Supported", tone: "positive" },
  inconclusive: { label: "Not reliable", tone: "attention" },
  descriptive: { label: "Descriptive", tone: "neutral" },
};

const EFFECT: Record<string, string> = {
  spearman: "Rank correlation",
  pooled_spearman: "Rank correlation within groups",
  kendall_tau: "Kendall's tau",
  eta: "Eta",
  cramers_v: "Cramér's V",
  mean_difference: "Difference of averages",
  share_difference: "Difference of shares",
};

export function effectLabel(name: string): string {
  return EFFECT[name] ?? name.replaceAll("_", " ");
}

/** A test statistic for reading: two decimals for sizes up to 1, else three significant
 * figures. */
export function formatEffect(value: number): string {
  if (!Number.isFinite(value)) return "n/a";
  const text = Math.abs(value) <= 1 ? value.toFixed(2) : Number(value.toPrecision(3)).toString();
  return text === "-0.00" ? "0.00" : text;
}

/** A p- or q-value: "below 0.001" for very small ones, else three decimals. */
export function formatProbability(value: number): string {
  if (value < 0.001) return "below 0.001";
  return value.toFixed(3);
}
