import type { NextConfig } from "next";

const config: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  // `next dev` otherwise writes an AGENTS.md into the project.
  agentRules: false,
  // `next dev` refuses its own scripts and hot reload to hosts it does not know; the origins in
  // AUTH_TRUSTED_ORIGINS (an ngrok tunnel, say) are this app. Production servers ignore it.
  allowedDevOrigins: (process.env.AUTH_TRUSTED_ORIGINS ?? "").split(",").flatMap((entry) => {
    try {
      return entry.trim() ? [new URL(entry.trim()).hostname] : [];
    } catch {
      return [];
    }
  }),
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
        ],
      },
    ];
  },
};

export default config;
