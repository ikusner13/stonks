import { defineConfig } from "vitest/config";

// Separate from vite.config.ts: the Cloudflare + TanStack Router plugins
// there assume a Vite dev/build context and aren't needed to unit-test
// plain TS modules like src/lib/format.ts.
export default defineConfig({
  test: {
    environment: "node",
  },
});
