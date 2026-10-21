import type { MetadataRoute } from "next";

/** `/manifest.webmanifest`: the name and icons used when the app is added to a home screen. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "InsightfulCell",
    short_name: "InsightfulCell",
    description: "Reports where every number shows its work.",
    start_url: "/",
    display: "standalone",
    background_color: "#f4f3f1",
    theme_color: "#2f6f62",
    icons: [
      { src: "/icons/android-chrome-192x192.png", sizes: "192x192", type: "image/png" },
      { src: "/icons/android-chrome-512x512.png", sizes: "512x512", type: "image/png" },
    ],
  };
}
