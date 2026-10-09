import type { ButtonHTMLAttributes, ReactNode } from "react";

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
      className={`inline-flex min-h-9 items-center justify-center gap-2 rounded-control border px-4 text-sm font-medium transition-colors aria-disabled:cursor-not-allowed aria-disabled:opacity-70 pointer-coarse:min-h-11 ${VARIANTS[variant]} ${className}`}
      {...rest}
    >
      {loading ? <Spinner /> : null}
      {children}
    </button>
  );
}

function Spinner() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="size-4 animate-spin motion-reduce:animate-none"
      fill="none"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity="0.25" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
