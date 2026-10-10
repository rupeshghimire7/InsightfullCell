/** Locale-aware formatting through Intl (never hand-rolled), with non-breaking spaces. */

const NBSP = " ";

export function formatBytes(bytes: number, locale?: string): string {
  const units = ["B", "KB", "MB", "GB"] as const;
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit += 1;
  }
  const digits = unit === 0 || value >= 100 ? 0 : 1;
  const number = new Intl.NumberFormat(locale, {
    maximumFractionDigits: digits,
    minimumFractionDigits: 0,
  }).format(value);
  return `${number}${NBSP}${units[unit]}`;
}

export function formatPercent(fraction: number, locale?: string): string {
  return new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 0 }).format(
    Math.min(1, Math.max(0, fraction)),
  );
}

export function formatDate(iso: string, locale?: string): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(iso),
  );
}

/** The same in every locale and time zone ("2026-10-10 00:27 UTC"): safe to render on the
 * server and hydrate in any browser. */
export function formatUtc(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return `${date.toISOString().slice(0, 16).replace("T", " ")}${NBSP}UTC`;
}

export function formatCount(n: number, locale?: string): string {
  return new Intl.NumberFormat(locale).format(n);
}

/** A statistic for reading, not for copying: three significant figures, compact above 10,000
 * ("12.5K"), so a profile never pretends to more precision than it shows. */
export function formatNumber(n: number, locale?: string): string {
  return new Intl.NumberFormat(locale, {
    maximumSignificantDigits: 3,
    notation: Math.abs(n) >= 10_000 ? "compact" : "standard",
  }).format(n);
}
