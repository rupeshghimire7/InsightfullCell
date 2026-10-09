import {
  CheckCircle,
  CircleNotch,
  Clock,
  Hourglass,
  Prohibit,
  StopCircle,
  XCircle,
  type IconComponent,
} from "@/components/ui/icons";
import { RUN_STATUS, type Tone } from "@/lib/runs/status";
import type { RunStatus } from "@/lib/runs/types";

const ICON: Record<RunStatus, IconComponent> = {
  queued: Clock,
  running: CircleNotch,
  awaiting_review: Hourglass,
  succeeded: CheckCircle,
  failed: XCircle,
  cancelled: StopCircle,
  expired: Prohibit,
};

const TONE: Record<Tone, { badge: string; icon: string }> = {
  neutral: { badge: "border-border bg-bg", icon: "text-muted" },
  active: { badge: "border-accent/30 bg-accent/10", icon: "text-accent" },
  attention: { badge: "border-warning/40 bg-warning/10", icon: "text-warning" },
  positive: { badge: "border-success/30 bg-success/10", icon: "text-success" },
  negative: { badge: "border-danger/35 bg-danger/10", icon: "text-danger" },
};

/** A run's status as a pill: an icon and a word, so colour is never the only signal. */
export function RunStatusBadge({ status }: { status: RunStatus }) {
  const { label, tone } = RUN_STATUS[status];
  const Icon = ICON[status];
  const styles = TONE[tone];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium whitespace-nowrap text-text ${styles.badge}`}
      data-status={status}
    >
      <Icon
        aria-hidden="true"
        weight={status === "running" ? "bold" : "fill"}
        className={`size-3.5 ${styles.icon} ${status === "running" ? "animate-spin motion-reduce:animate-none" : ""}`}
      />
      {label}
    </span>
  );
}
