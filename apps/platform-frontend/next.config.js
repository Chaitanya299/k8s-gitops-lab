/** @type {import('next').NextConfig} */
const nextConfig = {
  // Slim runtime image: ship only the standalone server + traced deps.
  output: "standalone",
  // No eslint config shipped — don't block the production build on it.
  eslint: { ignoreDuringBuilds: true },
};
module.exports = nextConfig;
