/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      // Python-only 模式(路线 B):不起 Java,auth 走 Python 自带的 /api/auth。
      // 若要恢复 Java 控制面,把下面两个 :9000 改回 :18080 即可。
      {
        source: "/api/auth/:path*",
        destination: "http://localhost:9000/api/auth/:path*",
      },
      // JWKS endpoint(Python 未提供;Python-only 模式下用不到,留给 Java 用)
      {
        source: "/.well-known/jwks.json",
        destination: "http://localhost:18080/.well-known/jwks.json",
      },
      // Everything else → Python agent
      {
        source: "/api/:path*",
        destination: "http://localhost:9000/api/:path*",
      },
    ];
  },
};

module.exports = nextConfig;
