import type { NextConfig } from "next";

// Local dev proxy to the real FastAPI backend — same role
// living-world-explorer/vite.config.ts's server.proxy plays for that app
// (proxies /api to http://localhost:8031 so relative paths work without
// CORS). NEXT_PUBLIC_API_TARGET overrides the backend origin.
const API_TARGET = process.env.NEXT_PUBLIC_API_TARGET ?? "http://localhost:8031";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_TARGET}/api/:path*` },
    ];
  },
};

export default nextConfig;
