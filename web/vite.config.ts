import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development Vite serves the page and passes every /api call to the backend on port 8787.
// Set VITE_MOCK=1 (npm run dev:mock) to answer every call from the fixtures in src/mock.ts instead.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": { target: "http://127.0.0.1:8787", changeOrigin: true } } },
  build: { outDir: "dist", emptyOutDir: true },
});
