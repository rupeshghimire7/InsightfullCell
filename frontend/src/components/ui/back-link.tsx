import Link from "next/link";
import { ArrowLeft } from "@/components/ui/icons";

export function BackLink({ href, children }: { href: string; children: string }) {
  return (
    <Link
      href={href}
      className="inline-flex min-h-6 items-center gap-1.5 font-medium text-muted hover:text-text pointer-coarse:min-h-11"
    >
      <ArrowLeft aria-hidden="true" className="size-4" />
      {children}
    </Link>
  );
}
