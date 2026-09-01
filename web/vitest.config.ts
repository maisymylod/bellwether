import { defineConfig } from "vitest/config";

// Kept separate from vite.config.ts because Vitest ships its own copy of Vite,
// and a single config typed against both trips over the two Plugin types.
// The unit tests are plain TypeScript with no JSX, so they need no plugins.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
