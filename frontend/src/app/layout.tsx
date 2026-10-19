import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "InsightfulCell", template: "%s · InsightfulCell" },
  description: "Reports where every number shows its work.",
  robots: { index: false, follow: false },
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

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // Browser extensions (password managers, Grammarly, dark mode, translation) add attributes to
    // <html> and <body> before React loads. This ignores attribute differences on these two
    // elements only; mismatches anywhere inside are still reported.
    <html
      lang="en"
      className={`${GeistSans.variable} ${GeistMono.variable}`}
      suppressHydrationWarning
    >
      <body className="antialiased" suppressHydrationWarning>
        {children}
      </body>
    </html>
  );
}
