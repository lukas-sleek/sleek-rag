/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Proxy /api/* to the FastAPI backend so the frontend can use relative
  // URLs everywhere. nginx in prod routes /api/* directly to uvicorn (this
  // rewrite never runs there), so this is the fallback path for direct-to-
  // Next.js access — local dev (next dev → :8001) and on-box prod testing
  // via VSCode tunnel (next start → :8000).
  async rewrites() {
    const backend = process.env.BACKEND_INTERNAL_URL || "http://127.0.0.1:8001";
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
