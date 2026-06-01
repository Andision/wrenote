import path from "node:path";

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// dev: vite serves on 5173; the page connects directly to ws://localhost:8000/ws
// prod: `npm run build` → backend/static/app/, which the FastAPI server serves at `/`.
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
      "/ws": { target: "http://127.0.0.1:8000", ws: true, changeOrigin: true },
      "/api": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
      "/info": "http://127.0.0.1:8000",
      "/sessions": "http://127.0.0.1:8000",
      "/groups": "http://127.0.0.1:8000",
      "/jobs": "http://127.0.0.1:8000",
      "/recordings": "http://127.0.0.1:8000",
    },
  },
  build: {
    // Emit straight into the backend's static dir so the packaged server can
    // serve the SPA at `/` with no copy step.
    outDir: path.resolve(__dirname, "../backend/static/app"),
    emptyOutDir: true,
    sourcemap: true,
  },
});
