"use client";

import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
} from "react";
import { ExploreButton, useExplore } from "@/components/report/explore";
import { DownloadSimple, SealCheck } from "@/components/ui/icons";
import { saveJson, slug } from "@/lib/download";
import { formatChartValue, niceTicks } from "@/lib/report/format";
import type { Chart, ChartPoint } from "@/lib/report/types";

/**
 * The report's charts. One series each, so one hue (--chart, validated against the surface in
 * both modes) and no legend: the caption names what is plotted. Thin marks: bars at most 20 px
 * thick with a 4 px rounded data end, 2 px lines, 8 px markers with a 2 px surface ring,
 * hairline solid grid. Every chart reads out the value under the pointer or the keyboard
 * (arrow keys), and every value is also in the table under it, so nothing hides behind a hover.
 * A chart checked by the analysis says so; groups that can be explored on their own get an
 * Explore button in the table.
 */
export function ReportChart({ chart }: { chart: Chart }) {
  const tableId = useId();
  const explore = useExplore();
  const format = (value: number) => formatChartValue(value, chart.value_format, chart.unit);
  const explorable =
    explore !== null && chart.drill_column !== null && chart.drill_column !== undefined
      ? chart.drill_column
      : null;
  const exploring = explorable !== null && chart.points.some((p) => p.key);
  return (
    <figure className="min-w-0 space-y-3">
      <figcaption className="text-sm font-medium">{chart.title}</figcaption>
      {chart.kind === "bar" ? (
        <BarChart chart={chart} format={format} />
      ) : (
        <PlotChart chart={chart} format={format} />
      )}
      {chart.note ? <p className="text-xs text-muted">{chart.note}</p> : null}
      <ChartFooter chart={chart} />
      <details className="group text-sm">
        <summary
          className="inline-flex min-h-9 cursor-pointer items-center text-xs font-medium text-accent underline underline-offset-2 select-none pointer-coarse:min-h-11"
          aria-controls={tableId}
        >
          {exploring ? "Show the numbers and explore a group" : "Show the numbers"}
        </summary>
        <div
          id={tableId}
          role="region"
          aria-label={`Numbers behind: ${chart.title}`}
          tabIndex={0}
          className="mt-2 max-h-72 overflow-auto rounded-control border border-border"
        >
          <table className="w-full border-collapse text-left text-xs">
            <caption className="sr-only">{chart.title}</caption>
            <thead className="sticky top-0 bg-surface">
              <tr className="border-b border-border text-muted">
                <th scope="col" className="px-3 py-1.5 font-medium">
                  {chart.kind === "scatter" ? `${chart.x_label}, ${chart.y_label}` : chart.x_label}
                </th>
                {chart.kind === "scatter" ? null : (
                  <th scope="col" className="px-3 py-1.5 text-right font-medium">
                    {chart.y_label}
                  </th>
                )}
                {chart.points.some((p) => p.n !== null) ? (
                  <th scope="col" className="px-3 py-1.5 text-right font-medium">
                    Rows
                  </th>
                ) : null}
                {exploring ? (
                  <th scope="col" className="px-3 py-1.5 font-medium">
                    <span className="sr-only">Explore</span>
                  </th>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {chart.points.map((point, i) => (
                <tr key={i} className="border-b border-border last:border-b-0">
                  <th scope="row" className="px-3 py-1.5 font-normal">
                    {point.label}
                  </th>
                  {chart.kind === "scatter" ? null : (
                    <td className="tabular px-3 py-1.5 text-right font-mono">
                      {format(point.value)}
                    </td>
                  )}
                  {chart.points.some((p) => p.n !== null) ? (
                    <td className="tabular px-3 py-1.5 text-right font-mono">
                      {point.n === null ? "" : point.n.toLocaleString("en-US")}
                    </td>
                  ) : null}
                  {exploring ? (
                    <td className="px-1 py-0.5 text-right">
                      {point.key && explorable ? (
                        <ExploreButton
                          target={{
                            column: explorable,
                            columnLabel: chart.x_label,
                            key: point.key,
                            label: point.label,
                            rows: point.n,
                          }}
                        />
                      ) : null}
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}

type Format = (value: number) => string;

/** Whether the analysis re-checked the chart, and its Vega-Lite spec to download. */
function ChartFooter({ chart }: { chart: Chart }) {
  const spec = chart.vega_lite;
  if (!chart.check?.verified && !spec) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
      {chart.check?.verified ? (
        <span className="inline-flex items-center gap-1">
          <SealCheck aria-hidden="true" weight="fill" className="size-3.5 text-success" />
          {chart.check.claims === 1
            ? "Its value was checked against the data"
            : `All ${chart.check.claims.toLocaleString("en-US")} values were checked against the data`}
        </span>
      ) : null}
      {spec ? (
        <button
          type="button"
          onClick={() => saveJson(`${slug(chart.title)}.vl.json`, spec)}
          className="inline-flex min-h-8 items-center gap-1 font-medium text-accent underline-offset-2 hover:underline print:hidden pointer-coarse:min-h-11"
        >
          <DownloadSimple aria-hidden="true" className="size-3.5" />
          Download Vega-Lite spec
        </button>
      ) : null}
    </div>
  );
}

// --------------------------------------------------------------------------- bars

/** Horizontal bars in HTML: labels wrap and stay readable at any width. Signed values grow
 * left or right from a zero line. */
function BarChart({ chart, format }: { chart: Chart; format: Format }) {
  const values = chart.points.map((p) => p.value);
  const low = Math.min(0, ...values);
  const high = Math.max(0, ...values);
  const span = high - low || 1;
  const zero = ((0 - low) / span) * 100;
  // Room beside the bars for the longest value label, so it never runs past the chart (11 px
  // mono digits are about 6.7 px wide, plus the 6 px gap).
  const longest = Math.max(...chart.points.map((p) => format(p.value).length), 1);
  const reserve = Math.ceil(longest * 6.7 + 8);
  return (
    <ul
      className="space-y-1.5"
      aria-label={`${chart.title}. ${chart.y_label} by ${chart.x_label}.`}
    >
      {chart.points.map((point) => {
        const width = (Math.abs(point.value) / span) * 100;
        const negative = point.value < 0;
        return (
          <li
            key={point.label}
            className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] items-center gap-3 text-xs sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)]"
          >
            <span className="truncate text-text" title={point.label}>
              {point.label}
            </span>
            <span
              className="flex h-5 min-w-0 items-center"
              style={{ paddingLeft: low < 0 ? reserve : 0, paddingRight: high > 0 ? reserve : 0 }}
            >
              <span className="relative flex h-5 w-full items-center">
                {low < 0 ? (
                  <span
                    aria-hidden="true"
                    className="absolute inset-y-0 w-px bg-border"
                    style={{ left: `${zero}%` }}
                  />
                ) : null}
                <span
                  aria-hidden="true"
                  className={`absolute h-4 bg-chart ${negative ? "rounded-l-[4px]" : "rounded-r-[4px]"}`}
                  style={{
                    left: `${negative ? zero - width : zero}%`,
                    width: `${Math.max(width, point.value === 0 ? 0 : 0.6)}%`,
                  }}
                />
                <span
                  className="tabular absolute font-mono text-[11px] whitespace-nowrap text-muted"
                  style={
                    negative
                      ? { right: `${100 - (zero - width)}%`, paddingRight: 6 }
                      : { left: `${zero + width}%`, paddingLeft: 6 }
                  }
                >
                  {format(point.value)}
                </span>
              </span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}

// --------------------------------------------------------------------------- plots

const PLOT_HEIGHT = 220;
const BOTTOM = 26;
const TOP = 10;

function useWidth(): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(640);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(240, Math.round(entry.contentRect.width)));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

interface Mark {
  cx: number;
  cy: number;
  point: ChartPoint;
}

function PlotChart({ chart, format }: { chart: Chart; format: Format }) {
  const [ref, width] = useWidth();
  const [active, setActive] = useState<number | null>(null);
  const points = chart.points;

  const geometry = useMemo(() => layout(chart, width, format), [chart, width, format]);
  const { marks, yTicks, yScale, left, xLabels } = geometry;
  const plotRight = width - 8;
  const current = active === null ? null : marks[active];

  function onPointer(event: PointerEvent<SVGSVGElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - box.left) / box.width) * width;
    const y = ((event.clientY - box.top) / box.height) * (PLOT_HEIGHT + BOTTOM);
    let best = 0;
    let distance = Infinity;
    marks.forEach((mark, i) => {
      const d =
        chart.kind === "scatter" ? (mark.cx - x) ** 2 + (mark.cy - y) ** 2 : Math.abs(mark.cx - x);
      if (d < distance) {
        distance = d;
        best = i;
      }
    });
    setActive(best);
  }

  function onKey(event: KeyboardEvent<SVGSVGElement>) {
    if (!marks.length) return;
    const last = marks.length - 1;
    const next: Record<string, number> = {
      ArrowRight: Math.min(last, (active ?? -1) + 1),
      ArrowUp: Math.min(last, (active ?? -1) + 1),
      ArrowLeft: Math.max(0, (active ?? 1) - 1),
      ArrowDown: Math.max(0, (active ?? 1) - 1),
      Home: 0,
      End: last,
    };
    if (event.key in next) {
      event.preventDefault();
      setActive(next[event.key] ?? 0);
    } else if (event.key === "Escape") {
      setActive(null);
    }
  }

  const readout = current ? describe(chart, current.point, format) : "";
  return (
    <div ref={ref} className="relative min-w-0">
      <svg
        role="img"
        aria-label={`${chart.title}. ${chart.y_label} against ${chart.x_label}. Use the arrow keys to read each value.`}
        tabIndex={0}
        width="100%"
        viewBox={`0 0 ${width} ${PLOT_HEIGHT + BOTTOM}`}
        className="block touch-pan-y overflow-visible rounded-control focus-visible:outline-2 focus-visible:outline-offset-4"
        onPointerMove={onPointer}
        onPointerLeave={() => setActive(null)}
        onKeyDown={onKey}
        onBlur={() => setActive(null)}
      >
        {yTicks.map((tick) => (
          <g key={tick}>
            <line
              x1={left}
              x2={plotRight}
              y1={yScale(tick)}
              y2={yScale(tick)}
              className="stroke-border"
              strokeWidth={1}
            />
            <text
              x={left - 6}
              y={yScale(tick)}
              dy="0.32em"
              textAnchor="end"
              className="fill-muted text-[11px] tabular-nums"
            >
              {format(tick)}
            </text>
          </g>
        ))}
        {xLabels.map((label) => (
          <text
            key={`${label.x}-${label.text}`}
            x={label.x}
            y={PLOT_HEIGHT + 18}
            textAnchor={label.anchor}
            className="fill-muted text-[11px]"
          >
            {label.text}
          </text>
        ))}
        {chart.kind === "histogram" ? (
          <Columns marks={marks} geometry={geometry} active={active} />
        ) : null}
        {chart.kind === "line" ? <Line marks={marks} active={active} format={format} /> : null}
        {chart.kind === "scatter"
          ? marks.map((mark, i) => (
              <circle
                key={i}
                cx={mark.cx}
                cy={mark.cy}
                r={i === active ? 6 : 4}
                className="fill-chart stroke-surface"
                strokeWidth={2}
                opacity={active === null || i === active ? 0.85 : 0.35}
              />
            ))
          : null}
        {current && chart.kind !== "scatter" ? (
          <line
            x1={current.cx}
            x2={current.cx}
            y1={TOP}
            y2={PLOT_HEIGHT}
            className="stroke-muted"
            strokeWidth={1}
          />
        ) : null}
      </svg>
      <p
        aria-live="polite"
        className={`pointer-events-none absolute top-0 right-0 max-w-[80%] rounded-control border border-border bg-raised px-2 py-1 text-xs shadow-sm transition-opacity ${current ? "opacity-100" : "opacity-0"}`}
      >
        {readout}
      </p>
      {points.length === 0 ? <p className="text-xs text-muted">No values to draw.</p> : null}
    </div>
  );
}

function describe(chart: Chart, point: ChartPoint, format: Format): ReactNode {
  if (chart.kind === "scatter") return point.label;
  const rows = point.n !== null ? `, ${point.n.toLocaleString("en-US")} rows` : "";
  return (
    <>
      <span className="tabular font-mono font-semibold">{format(point.value)}</span>
      <span className="text-muted">
        {" "}
        {point.label}
        {rows}
      </span>
    </>
  );
}

interface Geometry {
  marks: Mark[];
  yTicks: number[];
  yScale: (value: number) => number;
  left: number;
  step: number;
  xLabels: { x: number; text: string; anchor: "start" | "middle" | "end" }[];
}

function layout(chart: Chart, width: number, format: Format): Geometry {
  const points = chart.points;
  const values = points.map((p) => p.value);
  const zeroBased = chart.kind === "histogram";
  let low = Math.min(...values, zeroBased ? 0 : Infinity);
  let high = Math.max(...values, zeroBased ? 0 : -Infinity);
  if (!Number.isFinite(low) || !Number.isFinite(high)) [low, high] = [0, 1];
  if (low === high) [low, high] = [low - 1, high + 1];
  const yTicks = niceTicks(low, high);
  const yLow = Math.min(low, yTicks[0] ?? low);
  const yHigh = Math.max(high, yTicks[yTicks.length - 1] ?? high);
  const longest = Math.max(...yTicks.map((t) => format(t).length), 1);
  const left = Math.min(96, 12 + longest * 6.6);
  const right = width - 8;
  const yScale = (v: number) => TOP + (1 - (v - yLow) / (yHigh - yLow || 1)) * (PLOT_HEIGHT - TOP);

  if (chart.kind === "scatter") {
    const xs = points.map((p) => p.x ?? 0);
    const xLow = Math.min(...xs);
    const xHigh = Math.max(...xs);
    const xScale = (v: number) => left + ((v - xLow) / (xHigh - xLow || 1)) * (right - left);
    const xTicks = niceTicks(xLow, xHigh, 4);
    return {
      marks: points.map((p) => ({ cx: xScale(p.x ?? 0), cy: yScale(p.value), point: p })),
      yTicks,
      yScale,
      left,
      step: 0,
      xLabels: xTicks.map((t, i) => ({
        x: xScale(t),
        text: formatChartValue(t, "number", null),
        anchor: i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle",
      })),
    };
  }
  const n = Math.max(points.length, 1);
  const step = (right - left) / n;
  const marks = points.map((p, i) => ({
    cx: left + step * (i + 0.5),
    cy: yScale(p.value),
    point: p,
  }));
  const picks = [...new Set([0, Math.floor((n - 1) / 2), n - 1])].filter((i) => i < points.length);
  const xLabels = picks.map((i, k) => {
    const label = points[i]?.label ?? "";
    const text = chart.kind === "histogram" ? (label.split(" to ")[0] ?? label) : label;
    const anchor: "start" | "middle" | "end" =
      k === 0 ? "start" : k === picks.length - 1 ? "end" : "middle";
    const x = anchor === "start" ? left : anchor === "end" ? right : (marks[i]?.cx ?? left);
    return { x, text, anchor };
  });
  return { marks, yTicks, yScale, left, step, xLabels };
}

function Columns({
  marks,
  geometry,
  active,
}: {
  marks: Mark[];
  geometry: Geometry;
  active: number | null;
}) {
  const base = geometry.yScale(0);
  const width = Math.max(1, geometry.step - 2);
  return (
    <>
      {marks.map((mark, i) => {
        const height = Math.max(0, base - mark.cy);
        if (height <= 0) return null;
        const x = mark.cx - width / 2;
        const r = Math.min(4, width / 2, height);
        return (
          <path
            key={i}
            d={`M${x},${base}V${mark.cy + r}Q${x},${mark.cy} ${x + r},${mark.cy}H${x + width - r}Q${x + width},${mark.cy} ${x + width},${mark.cy + r}V${base}Z`}
            className="fill-chart"
            opacity={active === null || active === i ? 1 : 0.55}
          />
        );
      })}
    </>
  );
}

function Line({ marks, active, format }: { marks: Mark[]; active: number | null; format: Format }) {
  if (!marks.length) return null;
  const path = marks.map((m, i) => `${i === 0 ? "M" : "L"}${m.cx},${m.cy}`).join(" ");
  const last = marks[marks.length - 1];
  return (
    <>
      <path
        d={path}
        fill="none"
        className="stroke-chart"
        strokeWidth={2}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {marks.map((mark, i) => (
        <circle
          key={i}
          cx={mark.cx}
          cy={mark.cy}
          r={i === active ? 6 : 4}
          className="fill-chart stroke-surface"
          strokeWidth={2}
        />
      ))}
      {last ? (
        <text
          x={last.cx}
          y={last.cy - 10}
          textAnchor="end"
          className="fill-text text-[11px] font-medium tabular-nums"
        >
          {format(last.point.value)}
        </text>
      ) : null}
    </>
  );
}
