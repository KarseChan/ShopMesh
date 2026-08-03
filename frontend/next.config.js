/** @type {import('next').NextConfig} */
const nextConfig = {
  // 关闭 Next 的 gzip 压缩:gzip 必须攒齐整个 body 才能压,会把 SSE 流缓冲成
  // 一次性返回(实测经 rewrite 代理时响应带 content-encoding: gzip 且所有事件
  // 同一时刻到达)。关掉后代理才能按 chunk 透传 SSE。
  compress: false,
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
