import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 5273, not Vite's default 5173, so this cannot collide with anything else
// already running on the machine.
const DEV_PORT = 5273;
const API_PORT = Number(process.env.BELLWETHER_PORT ?? 8788);

export default defineConfig({
  plugins: [react()],
  server: {
    port: DEV_PORT,
    strictPort: true,
    proxy: {
      // Proxying in dev keeps the console on one origin, which matters for SSE:
      // EventSource cannot set headers, so a cross-origin stream would need CORS
      // on every reconnect. The API allows the dev origin anyway, as a fallback.
      "/api": {
        target: `http://127.0.0.1:${API_PORT}`,
        changeOrigin: true,
        // Buffering a proxied SSE response would defeat the entire point.
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            proxyRes.headers["cache-control"] = "no-cache, no-transform";
          });
        },
      },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
