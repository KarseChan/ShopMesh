/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      // Auth endpoints → Java control plane
      {
        source: "/api/auth/:path*",
        destination: "http://localhost:18080/api/auth/:path*",
      },
      // JWKS endpoint → Java
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
