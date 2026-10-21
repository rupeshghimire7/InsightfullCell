import Image from "next/image";

/**
 * The logo's cube mark (`public/icons/logo-mark.webp`): cut from the app icon with the white paper
 * removed (colour-to-alpha outside the cube, so faint lines keep their edges; the cube itself
 * stays solid, its glass lid frosted), so it sits on any background in either theme.
 */
export function LogoMark({ size = 20, className = "" }: { size?: number; className?: string }) {
  return (
    <Image
      src="/icons/logo-mark.webp"
      alt=""
      width={size}
      height={size}
      // One 224 px WebP, already small: served as is, so it needs no image optimiser.
      unoptimized
      className={`shrink-0 select-none ${className}`}
    />
  );
}

/** The mark and the name, as on every page header. The name carries the meaning. */
export function Brand({ size = 20, className = "" }: { size?: number; className?: string }) {
  return (
    <span className={`flex items-center gap-2 font-semibold ${className}`}>
      <LogoMark size={size} />
      InsightfulCell
    </span>
  );
}
