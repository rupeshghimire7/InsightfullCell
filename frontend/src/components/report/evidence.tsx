import {
  CheckCircle,
  Info,
  SealCheck,
  WarningCircle,
  type IconComponent,
} from "@/components/ui/icons";
import { effectLabel, formatEffect, formatProbability, TIER } from "@/lib/report/format";
import type { ReportFact } from "@/lib/report/types";

const TONE: Record<(typeof TIER)[keyof typeof TIER]["tone"], { badge: string; icon: string }> = {
  positive: { badge: "border-success/30 bg-success/10", icon: "text-success" },
  attention: { badge: "border-warning/40 bg-warning/10", icon: "text-warning" },
  neutral: { badge: "border-border bg-bg", icon: "text-muted" },
};
const TIER_ICON: Record<ReportFact["tier"], IconComponent> = {
  strong: SealCheck,
  supported: CheckCircle,
  inconclusive: WarningCircle,
  descriptive: Info,
};

/** How sure a fact is: an icon and words, from its test after the false-discovery control. */
export function EvidenceBadge({ tier }: { tier: ReportFact["tier"] }) {
  const { label, tone } = TIER[tier];
  const Icon = TIER_ICON[tier];
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${TONE[tone].badge}`}
    >
      <Icon aria-hidden="true" weight="fill" className={`size-3.5 ${TONE[tone].icon}`} />
      {label}
    </span>
  );
}

export function TestLine({ test }: { test: NonNullable<ReportFact["test"]> }) {
  const interval =
    test.ci_low !== null && test.ci_high !== null
      ? ` (95% interval ${formatEffect(test.ci_low)} to ${formatEffect(test.ci_high)})`
      : "";
  return (
    <p className="mt-1 text-xs text-muted">
      {test.method}: {effectLabel(test.effect_name)}{" "}
      <span className="tabular font-mono">{formatEffect(test.effect)}</span>
      {interval}; p-value{" "}
      <span className="tabular font-mono">{formatProbability(test.p_value)}</span> (
      <span className="tabular font-mono">{formatProbability(test.q_value)}</span> after the
      false-discovery control).
    </p>
  );
}

export function CheckLine({ check }: { check: NonNullable<ReportFact["check"]> }) {
  const changed = check.outcome === "weakens" || check.outcome === "flips";
  const Icon = changed ? WarningCircle : CheckCircle;
  return (
    <p className={`mt-1 flex items-start gap-1 text-xs ${changed ? "text-warning" : "text-muted"}`}>
      <Icon aria-hidden="true" weight="fill" className="mt-px size-3.5 shrink-0" />
      {check.note}
    </p>
  );
}

/** "Exploratory" next to a fact found in a drill-down. */
export function ExploratoryBadge() {
  return (
    <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-warning/40 bg-warning/10 px-2 py-0.5 text-xs font-medium whitespace-nowrap">
      <WarningCircle aria-hidden="true" weight="fill" className="size-3.5 text-warning" />
      Exploratory
    </span>
  );
}
