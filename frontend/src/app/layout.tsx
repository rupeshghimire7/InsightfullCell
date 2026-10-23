import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import type { Metadata, Viewport } from "next";
import { cookies } from "next/headers";
import type { ReactNode } from "react";
import { parseTheme, THEME_COOKIE } from "@/lib/theme";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "InsightfulCell", template: "%s · InsightfulCell" },
  description: "Reports where every number shows its work.",
  robots: { index: false, follow: false },
  // favicon.ico sits next to this file (Next links it); the rest are served from public/icons.
  icons: {
    icon: [
      { url: "/icons/favicon-32x32.png", sizes: "32x32", type: "image/png" },
      { url: "/icons/favicon-16x16.png", sizes: "16x16", type: "image/png" },
    ],
    apple: { url: "/icons/apple-touch-icon.png", sizes: "180x180", type: "image/png" },
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f3f1" },
    { media: "(prefers-color-scheme: dark)", color: "#131614" },
  ],
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  // The header toggle's choice; without it the system setting decides (globals.css).
  const theme = parseTheme((await cookies()).get(THEME_COOKIE)?.value);
  return (
    // Browser extensions (password managers, Grammarly, dark mode, translation) add attributes to
    // <html> and <body> before React loads. This ignores attribute differences on these two
    // elements only; mismatches anywhere inside are still reported.
    <html
      lang="en"
      className={`${GeistSans.variable} ${GeistMono.variable}`}
      data-theme={theme}
      suppressHydrationWarning
    >
      <body className="antialiased" suppressHydrationWarning>
        {children}
      </body>
    </html>
  );
}
