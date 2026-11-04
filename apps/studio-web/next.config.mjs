/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  transpilePackages: ["@enterprise/ui", "@enterprise/api-client"],
  async rewrites() {
    const controlApiInternalUrl =
      process.env.CONTROL_API_INTERNAL_URL ||
      process.env.NEXT_PUBLIC_CONTROL_API_URL ||
      "http://127.0.0.1:8000";
    return [
      {
        source: "/v1/:path*",
        destination: `${controlApiInternalUrl}/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
