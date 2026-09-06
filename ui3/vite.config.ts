import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { fileURLToPath } from "node:url";

// ESM config ("type": "module") — __dirname does not exist here.
const HERE = path.dirname(fileURLToPath(import.meta.url));

// Single-origin dev: proxy all backend routes to the API server so the
// Bearer flow is uniform and there is no CORS dance (brief §7.4).
const API_TARGET = process.env.VITE_API_TARGET || "http://127.0.0.1:8765";
const API_PREFIXES = [
  "/auth",
  "/research",
  "/sessions",
  "/library",
  "/hitl",
  "/events",
  "/roles",
  "/memory",
  "/git",
  "/health",
  "/evolution",
  "/hypothesis",
  "/onboarding",
];

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(HERE, "src") } },
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      API_PREFIXES.map((p) => [
        p,
        { target: API_TARGET, changeOrigin: true, ws: false },
      ]),
    ),
  },
});
