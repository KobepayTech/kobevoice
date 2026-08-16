import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  eslint: {
    // Lint is a separate CI step (`pnpm lint`), not a build gate.
    //
    // The upstream starter wires prettier in through eslint's compat layer, and
    // in that path eslint-plugin-prettier cannot resolve the `plugins` entries
    // in .prettierrc — so it silently falls back to prettier's defaults and
    // demands double quotes on files that .prettierrc formats with single ones.
    // The two tools disagree with each other, not with our code, and the
    // disagreement is purely stylistic. Type errors are still enforced below.
    ignoreDuringBuilds: true,
  },
  typescript: {
    // Type errors DO fail the build — this is the check that catches real bugs.
    ignoreBuildErrors: false,
  },
};

export default nextConfig;
