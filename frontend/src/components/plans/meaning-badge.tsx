import {
  CalendarBlank,
  CurrencyDollar,
  EyeSlash,
  Fingerprint,
  MapPin,
  Percent,
  Ruler,
  Tag,
  TextAlignLeft,
  type IconComponent,
} from "@/components/ui/icons";
import { meaningLabel, personalLabel } from "@/lib/plans/labels";

const ICON: Record<string, IconComponent> = {
  continuous: Ruler,
  currency: CurrencyDollar,
  percentage: Percent,
  nominal: Tag,
  datetime: CalendarBlank,
  identifier: Fingerprint,
  text: TextAlignLeft,
  geography: MapPin,
};

/** What a column means, as an icon and a word. Neutral: meaning is not a state. */
export function MeaningBadge({ meaning }: { meaning: string }) {
  const Icon = ICON[meaning] ?? Tag;
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border bg-bg px-2 py-0.5 text-xs font-medium whitespace-nowrap">
      <Icon aria-hidden="true" className="size-3.5 text-muted" />
      {meaningLabel(meaning)}
    </span>
  );
}

/** A personal-data column: masked from ingest on, never sent to a model. */
export function MaskedBadge({ kind }: { kind: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-accent/35 bg-accent/10 px-2 py-0.5 text-xs font-medium whitespace-nowrap"
      title={`${personalLabel(kind)}: masked and left out of the analysis`}
    >
      <EyeSlash aria-hidden="true" className="size-3.5 text-accent" />
      Masked<span className="sr-only">: {personalLabel(kind)}</span>
    </span>
  );
}
