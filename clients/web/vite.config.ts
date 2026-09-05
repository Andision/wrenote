import path from "node:path";

import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// dev: vite serves on 5173; /v1 (HTTP + WS) is proxied to the engine on :8000
// prod: `npm run build` → engine/static/app/, which the engine serves at `/`.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    // The SPA now uses same-origin URLs (window.location). In dev it loads from
    // :5173, so proxy the backend's API + WS routes to the uvicorn dev server.
    proxy: {
      // Contract v1 (HTTP + WS) and the shell's readiness probe.
      "/v1": { target: "http://127.0.0.1:8000", ws: true, changeOrigin: true },
      "/health": "http://127.0.0.1:8000",
    },
  },
  // Vitest: jsdom for the component tests, and `globals` so specs read as
  // plainly as the engine's pytest ones. `setupFiles` is where the browser
  // APIs jsdom lacks get stubbed — see src/test/setup.ts.
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    restoreMocks: true,
  },
  build: {
    // Emit straight into the engine's static dir so the packaged server can
    // serve the SPA at `/` with no copy step.
    outDir: path.resolve(__dirname, "../../engine/static/app"),
    emptyOutDir: true,
    sourcemap: true,
  },
});
