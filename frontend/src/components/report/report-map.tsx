"use client";

import { useId, useMemo, useState, type KeyboardEvent, type MouseEvent } from "react";
import { ExploreButton, useExplore } from "@/components/report/explore";
import { SealCheck } from "@/components/ui/icons";
import { formatCount } from "@/lib/format";
import { formatChartValue } from "@/lib/report/format";
import { STEP_FILLS, legend, shownValues, stepOf, thresholds } from "@/lib/report/map-scale";
import type { MapRegion, ReportMap } from "@/lib/report/types";

/**
 * A map of regions coloured by one measure. Outlines arrive projected and simplified from the
 * analysis, so nothing is fetched or computed here but colours. Regions with fewer than ten rows
 * are hatched and carry no number; regions with no rows are plain. The value under the pointer
 * (or chosen with the arrow keys) is read out, and every number is in the table below, which is
 * also where a region can be explored with the keyboard. Clicking a coloured region explores it.
 */
export function ReportMap({ map }: { map: ReportMap }) {
  const rawId = useId();
  const id = rawId.replace(/[^A-Za-z0-9_-]/g, "");
  const tableId = `${id}-table`;
  const explore = useExplore();
  const [active, setActive] = useState<string | null>(null);

  const bounds = useMemo(() => thresholds(shownValues(map)), [map]);
  const steps = useMemo(() => legend(map), [map]);
  const valued = useMemo(
    () =>
      map.regions.filter((r) => r.status === "value").sort((a, b) => a.name.localeCompare(b.name)),
    [map],
  );
  const listed = useMemo(
    () =>
      map.regions
        .filter((r) => r.status !== "no_rows")
        .sort(
          (a, b) => (b.value ?? -Infinity) - (a.value ?? -Infinity) || a.name.localeCompare(b.name),
        ),
    [map],
  );
  const current = map.regions.find((r) => r.code === active) ?? null;
  const format = (value: number) => formatChartValue(value, map.value_format, map.unit);
  const canExplore = explore !== null && !!map.drill_column;

  function regionAt(event: MouseEvent<SVGSVGElement>): MapRegion | null {
    const target = event.target as Element | null;
    const code = target?.getAttribute?.("data-code");
    return code ? (map.regions.find((r) => r.code === code) ?? null) : null;
  }

  function onKey(event: KeyboardEvent<SVGSVGElement>) {
    if (valued.length === 0) return;
    const at = valued.findIndex((r) => r.code === active);
    const last = valued.length - 1;
    const moves: Record<string, number> = {
      ArrowRight: Math.min(last, at + 1),
      ArrowDown: Math.min(last, at + 1),
      ArrowLeft: Math.max(0, at === -1 ? 0 : at - 1),
      ArrowUp: Math.max(0, at === -1 ? 0 : at - 1),
      Home: 0,
      End: last,
    };
    if (event.key in moves) {
      event.preventDefault();
      setActive(valued[moves[event.key] ?? 0]?.code ?? null);
    } else if (event.key === "Escape") {
      setActive(null);
    }
  }

  function onClick(event: MouseEvent<SVGSVGElement>) {
    const region = regionAt(event);
    if (!explore || !map.drill_column || !region?.key) return;
    explore.request(
      {
        column: map.drill_column,
        columnLabel: placeLabel(map),
        key: region.key,
        label: region.name,
        rows: region.n,
      },
      event.currentTarget,
    );
  }

  return (
    <figure className="min-w-0 space-y-3">
      <figcaption className="text-sm font-medium">{map.title}</figcaption>
      <div className="relative">
        <svg
          role="img"
          aria-label={`${map.title}. Map of ${valued.length} regions with a value. Use the arrow keys to read each one; the numbers are also in the table below.`}
          tabIndex={0}
          viewBox={`0 0 ${map.width} ${map.height}`}
          className="block h-auto max-h-[28rem] w-full touch-pan-y rounded-control focus-visible:outline-2 focus-visible:outline-offset-4"
          onPointerMove={(event) => setActive(regionAt(event)?.code ?? null)}
          onPointerLeave={() => setActive(null)}
          onClick={onClick}
          onKeyDown={onKey}
          onBlur={() => setActive(null)}
        >
          <defs>
            <pattern
              id={`${id}-few`}
              width="6"
              height="6"
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(45)"
            >
              <rect width="6" height="6" className="fill-surface" />
              <line x1="0" y1="0" x2="0" y2="6" className="stroke-muted" strokeWidth="1.4" />
            </pattern>
          </defs>
          {map.regions.map((region) => (
            <path
              key={region.code}
              d={region.path}
              data-code={region.code}
              fillRule="evenodd"
              // A style, not the fill attribute: CSS variables and color-mix work everywhere here.
              style={{ fill: fillOf(region, bounds, `${id}-few`) }}
              className={`stroke-surface ${region.code === active ? "stroke-text" : ""} ${canExplore && region.key ? "cursor-pointer" : ""}`}
              strokeWidth={region.code === active ? 1.6 : 0.6}
            />
          ))}
        </svg>
        <p
          aria-live="polite"
          className={`pointer-events-none absolute top-0 right-0 max-w-[80%] rounded-control border border-border bg-raised px-2 py-1 text-xs shadow-sm transition-opacity motion-reduce:transition-none ${current ? "opacity-100" : "opacity-0"}`}
        >
          {current ? <Readout region={current} format={format} /> : ""}
        </p>
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted" aria-label="Legend">
        {steps.map((step) => (
          <li key={step.step} className="inline-flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="inline-block size-3 rounded-[3px] border border-border"
              style={{ background: STEP_FILLS[step.step] }}
            />
            <span className="tabular">{step.label}</span>
          </li>
        ))}
        {map.regions.some((r) => r.status === "few_rows") ? (
          <li className="inline-flex items-center gap-1.5">
            <svg aria-hidden="true" className="size-3 rounded-[3px] border border-border">
              <rect width="12" height="12" style={{ fill: `url(#${id}-few)` }} />
            </svg>
            Fewer than ten rows
          </li>
        ) : null}
      </ul>
      {map.note ? <p className="text-xs text-muted">{map.note}</p> : null}
      {map.check?.verified ? (
        <p className="inline-flex items-center gap-1 text-xs text-muted">
          <SealCheck aria-hidden="true" weight="fill" className="size-3.5 text-success" />
          All {formatCount(map.check.claims)} regions were checked against the data
        </p>
      ) : null}
      <details className="text-sm">
        <summary
          className="inline-flex min-h-9 cursor-pointer items-center text-xs font-medium text-accent underline underline-offset-2 select-none pointer-coarse:min-h-11"
          aria-controls={tableId}
        >
          {canExplore ? "Show the numbers and explore a region" : "Show the numbers"}
        </summary>
        <div
          id={tableId}
          role="region"
          aria-label={`Numbers behind: ${map.title}`}
          tabIndex={0}
          className="mt-2 max-h-72 overflow-auto rounded-control border border-border"
        >
          <table className="w-full border-collapse text-left text-xs">
            <caption className="sr-only">{map.title}</caption>
            <thead className="sticky top-0 bg-surface">
              <tr className="border-b border-border text-muted">
                <th scope="col" className="px-3 py-1.5 font-medium">
                  Region
                </th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">
                  {map.value_label}
                </th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">
                  Rows
                </th>
                {canExplore ? (
                  <th scope="col" className="px-3 py-1.5 font-medium">
                    <span className="sr-only">Explore</span>
                  </th>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {listed.map((region) => (
                <tr key={region.code} className="border-b border-border last:border-b-0">
                  <th scope="row" className="px-3 py-1.5 font-normal">
                    {region.name}
                  </th>
                  <td className="tabular px-3 py-1.5 text-right font-mono">
                    {region.status === "value" && region.value !== null
                      ? format(region.value)
                      : "Fewer than ten rows"}
                  </td>
                  <td className="tabular px-3 py-1.5 text-right font-mono">
                    {region.n === null ? "" : formatCount(region.n)}
                  </td>
                  {canExplore ? (
                    <td className="px-1 py-0.5 text-right">
                      {region.key && map.drill_column ? (
                        <ExploreButton
                          target={{
                            column: map.drill_column,
                            columnLabel: placeLabel(map),
                            key: region.key,
                            label: region.name,
                            rows: region.n,
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

function fillOf(region: MapRegion, bounds: number[], hatch: string): string {
  if (region.status === "value" && region.value !== null) {
    return STEP_FILLS[stepOf(region.value, bounds)] ?? STEP_FILLS[0] ?? "currentColor";
  }
  if (region.status === "few_rows") return `url(#${hatch})`;
  return "var(--border)";
}

function Readout({ region, format }: { region: MapRegion; format: (v: number) => string }) {
  if (region.status === "value" && region.value !== null) {
    return (
      <>
        <span className="tabular font-mono font-semibold">{format(region.value)}</span>
        <span className="text-muted">
          {" "}
          {region.name}
          {region.n !== null ? `, ${formatCount(region.n)} rows` : ""}
        </span>
      </>
    );
  }
  return (
    <span className="text-muted">
      {region.name}: {region.status === "few_rows" ? "fewer than ten rows" : "no rows"}
    </span>
  );
}

function placeLabel(map: ReportMap): string {
  return map.place_label || "the place";
}
