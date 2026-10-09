import type { ReactNode } from "react";
import type { IconComponent } from "@/components/ui/icons";

/** What an empty list means and how to fill it. Never a dead end. */
export function EmptyState({
  title,
  children,
  action,
  icon: Icon,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
  icon?: IconComponent;
}) {
  return (
    <div className="flex flex-col items-start gap-3 rounded-panel border border-dashed border-border px-6 py-8 sm:flex-row sm:items-center sm:gap-5">
      {Icon ? (
        <span className="grid size-11 shrink-0 place-items-center rounded-full bg-accent/10 text-accent">
          <Icon aria-hidden="true" className="size-5" />
        </span>
      ) : null}
      <div className="min-w-0">
        <h3 className="text-base font-semibold">{title}</h3>
        <p className="mt-1 max-w-[60ch] text-muted">{children}</p>
        {action ? <div className="mt-3">{action}</div> : null}
      </div>
    </div>
  );
}
