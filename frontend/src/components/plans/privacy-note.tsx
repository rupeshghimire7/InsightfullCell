import Link from "next/link";
import { EyeSlash, LockSimple, ShieldCheck } from "@/components/ui/icons";

/**
 * What happens to the person's data, said plainly. Every sentence here is a property the
 * system enforces (masking at ingest, the gateway's payload check, no rows in any prompt), so
 * the page never promises more than the code does.
 */
export function PrivacyNote({ recipeHref }: { recipeHref?: string }) {
  return (
    <section
      aria-labelledby="privacy-title"
      className="rounded-panel border border-border bg-surface p-4 sm:p-5"
    >
      <div className="flex items-start gap-3">
        <ShieldCheck
          aria-hidden="true"
          weight="duotone"
          className="mt-0.5 size-6 shrink-0 text-accent"
        />
        <div className="min-w-0 space-y-3">
          <div className="space-y-1">
            <h2 id="privacy-title" className="text-base font-semibold">
              Your data stays yours
            </h2>
            <p className="max-w-[65ch] text-sm text-muted">
              The analysis is built so a language model can help without ever seeing your records.
            </p>
          </div>
          <ul className="grid grid-cols-1 gap-x-8 gap-y-3 text-sm md:grid-cols-3">
            <li className="flex items-start gap-2">
              <LockSimple aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-accent" />
              <span>
                Your rows are never sent to a language model. When one helps plan or write up the
                analysis, it only sees a description of the table and figures we computed.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <EyeSlash aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-accent" />
              <span>
                Names, email addresses, phone numbers and similar details are hidden from the moment
                your file is read, and are never analysed.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <ShieldCheck aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-accent" />
              <span>
                Every request is checked before it leaves. If anything looks like personal data, it
                is stopped and our own engine does the work instead.
              </span>
            </li>
          </ul>
          {recipeHref ? (
            <p className="text-sm">
              <Link
                href={recipeHref}
                className="font-medium text-accent underline underline-offset-2"
              >
                See exactly what was shared
              </Link>
            </p>
          ) : null}
        </div>
      </div>
    </section>
  );
}
