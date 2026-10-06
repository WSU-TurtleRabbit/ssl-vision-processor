import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// The UI always talks to its own origin. In dev, Vite forwards the backend
// routes to wrapper_backend on :8765, so only :5173 needs to be reachable.
// Override with e.g. WRAPPER_BACKEND=http://localhost:8795 npm run dev.
const backend = process.env["WRAPPER_BACKEND"] ?? "http://localhost:8765";

// https://vite.dev/config/
export default defineConfig({
  plugins: [svelte()],
  // mermaid (Help view only) pulls in large lazily-loaded diagram chunks
  // (e.g. elk ~1.5 MB); they never load on the operator page itself.
  build: { chunkSizeWarningLimit: 2000 },
  server: {
    host: true,
    proxy: {
      "/ws": { target: backend.replace(/^http/, "ws"), ws: true },
      "/api": backend,
      "/snapshots": backend,
      "/snapshot/": backend,
    },
  },
});
