import { formatCount, formatNumber, formatPercent } from "@/lib/format";

/**
 * Micro-charts for a column profile. One series, so one hue (--chart, checked against the
 * surface in both modes); bars rise from the baseline with 2 px surface gaps and rounded tops;
 * every bar has a hover title, and the numbers beside the chart say the same in text.
 */

const WIDTH = 132;
const HEIGHT = 32;
const GAP = 2;

export function Histogram({
  bins,
  range,
  label,
}: {
  bins: readonly number[];
  range: readonly [number, number];
  label: string;
}) {
  const peak = Math.max(...bins, 1);
  const width = (WIDTH - GAP * (bins.length - 1)) / bins.length;
  const step = (range[1] - range[0]) / bins.length;
  return (
    <svg
      role="img"
      aria-label={`Distribution of ${label}, from ${formatNumber(range[0])} to ${formatNumber(range[1])}`}
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      className="h-8 w-[132px] shrink-0 overflow-visible"
    >
      <line x1={0} x2={WIDTH} y1={HEIGHT - 0.5} y2={HEIGHT - 0.5} className="stroke-border" />
      {bins.map((count, i) => {
        const h = count === 0 ? 0 : Math.max(2, (count / peak) * (HEIGHT - 2));
        const x = i * (width + GAP);
        const low = range[0] + i * step;
        return (
          <path
            key={i}
            d={barPath(x, HEIGHT - 1 - h, width, h)}
            className="fill-chart"
            data-count={count}
          >
            <title>
              {`${formatNumber(low)} to ${formatNumber(low + step)}: ${formatCount(count)} rows`}
            </title>
          </path>
        );
      })}
    </svg>
  );
}

/** A bar with a rounded top and a square foot, anchored to the baseline. */
function barPath(x: number, y: number, w: number, h: number): string {
  if (h <= 0) return "";
  const r = Math.min(2, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/** The most common labels and their share of the column, as bars without a track. */
export function CategoryShares({
  items,
  total,
  limit = 4,
}: {
  items: readonly { value: string; count: number }[];
  total: number;
  limit?: number;
}) {
  const shown = items.slice(0, limit);
  const peak = Math.max(...shown.map((i) => i.count), 1);
  return (
    <ul className="w-full space-y-1" aria-label="Most common values">
      {shown.map((item) => (
        <li
          key={item.value}
          className="grid grid-cols-[minmax(0,7rem)_minmax(0,1fr)_3rem] items-center gap-2 text-xs"
        >
          <span className="truncate" title={item.value}>
            {item.value || "(blank)"}
          </span>
          <span aria-hidden="true" className="h-2">
            <span
              className="block h-2 rounded-r-[2px] bg-chart"
              style={{ width: `${Math.max(4, (item.count / peak) * 100)}%` }}
            />
          </span>
          <span className="tabular text-right font-mono text-muted">
            {formatPercent(total ? item.count / total : 0)}
          </span>
        </li>
      ))}
    </ul>
  );
}
