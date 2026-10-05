"use client";

import { useLocale } from "@/components/LocaleProvider";
import { getBasePath } from "@/lib/config";

export type BrandLogoVariant = "full" | "mark";

// Aspect ratios mirror each asset's viewBox so the logo never distorts.
export const BRAND_LOGO_ASSETS: Record<BrandLogoVariant, { src: string; aspectRatio: number }> = {
  full: { src: "/images/akte-agent-logo.svg", aspectRatio: 240 / 64 },
  mark: { src: "/images/akte-agent-mark.svg", aspectRatio: 1 },
};

interface BrandLogoProps {
  variant?: BrandLogoVariant;
  /** Rendered height in px; width always follows the intrinsic aspect ratio. */
  height?: number;
  /** Hide from assistive technology when the name is already announced nearby. */
  decorative?: boolean;
  className?: string;
}

/**
 * The only place logo markup is rendered. The navy artwork drops below 3:1 on
 * dark surfaces, so dark mode and forced-colours mode get a light backing plate.
 */
export function BrandLogo({ variant = "full", height = 36, decorative = false, className = "" }: BrandLogoProps) {
  const { t } = useLocale();
  const asset = BRAND_LOGO_ASSETS[variant];
  return (
    <span
      className={`inline-flex max-w-full shrink-0 rounded-md forced-color-adjust-none dark:bg-white dark:p-1 forced-colors:bg-white forced-colors:p-1 ${className}`.trim()}
      aria-hidden={decorative || undefined}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- static export serves the SVG as-is */}
      <img
        src={`${getBasePath()}${asset.src}`}
        alt={decorative ? "" : t("app.name")}
        draggable={false}
        style={{ height, width: "auto", maxWidth: "100%", aspectRatio: asset.aspectRatio, objectFit: "contain" }}
      />
    </span>
  );
}
