import type { ReactNode } from "react";

/**
 * A page's title block: optional back link, an optional decorative mark (`leading`) beside the
 * title, the h1, one line of context, and actions.
 */
export function PageHeader({
  title,
  description,
  back,
  leading,
  meta,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  back?: ReactNode;
  leading?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    // Laid out by its own width, not the window's: a side panel can narrow the page.
    <div className="@container mb-8 space-y-3">
      {back ? <div className="text-sm">{back}</div> : null}
      <div className="flex flex-col gap-4 @5xl:flex-row @5xl:items-end @5xl:justify-between">
        <div className="flex min-w-0 items-center gap-4 sm:gap-5">
          {leading}
          <div className="min-w-0 space-y-1.5">
            <h1 className="text-2xl font-semibold break-words sm:text-[28px] sm:leading-tight">
              {title}
            </h1>
            {description ? <p className="max-w-[65ch] text-muted">{description}</p> : null}
            {meta ? (
              <div className="flex flex-wrap items-center gap-3 pt-1 text-sm">{meta}</div>
            ) : null}
          </div>
        </div>
        {actions ? <div className="flex shrink-0 flex-wrap gap-2">{actions}</div> : null}
      </div>
    </div>
  );
}
