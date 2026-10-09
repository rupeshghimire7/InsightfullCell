import type { ButtonHTMLAttributes, ReactNode } from "react";
import { CircleNotch } from "@/components/ui/icons";

type Variant = "primary" | "secondary" | "ghost";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-accent-contrast hover:brightness-110 border-transparent",
  secondary: "bg-surface text-text border-border hover:bg-bg",
  ghost: "bg-transparent text-text border-transparent hover:bg-surface",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  /** Keeps the label visible and adds a spinner; the button stays focusable but inert. */
  loading?: boolean;
  children: ReactNode;
}

export function Button({
  variant = "primary",
  loading = false,
  className = "",
  children,
  onClick,
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      aria-disabled={loading || rest.disabled ? true : undefined}
      onClick={loading ? (event) => event.preventDefault() : onClick}
      className={`press inline-flex min-h-9 items-center justify-center gap-2 rounded-control border px-4 text-sm font-medium whitespace-nowrap aria-disabled:cursor-not-allowed aria-disabled:opacity-70 pointer-coarse:min-h-11 ${VARIANTS[variant]} ${className}`}
      {...rest}
    >
      {loading ? <Spinner /> : null}
      {children}
    </button>
  );
}

function Spinner() {
  return (
    <CircleNotch
      aria-hidden="true"
      weight="bold"
      className="size-4 animate-spin motion-reduce:animate-none"
    />
  );
}
