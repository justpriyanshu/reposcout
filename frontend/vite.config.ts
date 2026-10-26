/// <reference types="vitest/config" />
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));

// The FastAPI app serves the build from /static/ (see src/reposcout/static_files.py).
const BASE = "/static/";
// `reposcout serve` / `reposcout serve-public` while developing (`npm run dev`).
const API = process.env.REPOSCOUT_API ?? "http://127.0.0.1:8000";

/**
 * The theme must be applied before first paint, and the CSP allows no inline scripts, so a tiny
 * classic (blocking) script goes first in <head>. Vite only bundles module scripts, so this plugin
 * emits it as a content-hashed asset and links it from both pages.
 */
function themeInit(): Plugin {
  const source = readFileSync(here("./theme-init.js"), "utf8");
  const fileName = `assets/theme-init-${createHash("sha256").update(source).digest("hex").slice(0, 10)}.js`;
  let base = BASE;
  return {
    name: "reposcout-theme-init",
    configResolved(config) { base = config.base; },
    configureServer(server) {
      server.middlewares.use(`${BASE}theme-init.js`, (_req, res) => {
        res.setHeader("Content-Type", "text/javascript");
        res.end(source);
      });
    },
    generateBundle() { this.emitFile({ type: "asset", fileName, source }); },
    transformIndexHtml: {
      order: "post",
      handler(html, ctx) {
        const src = ctx.server ? `${base}theme-init.js` : `${base}${fileName}`;
        return html.replace("<!--theme-init-->", `<script src="${src}"></script>`);
      },
    },
  };
}

const proxied = ["/api", "/auth", "/health", "/stats", "/search", "/ask", "/review", "/reviews"];

export default defineConfig({
  base: BASE,
  plugins: [react(), tailwindcss(), themeInit()],
  resolve: { alias: { "@": here("./src") } },
  build: {
    outDir: here("../src/reposcout/static/dist"),
    emptyOutDir: true,
    assetsDir: "assets",
    rolldownOptions: {
      input: { app: here("./app.html"), index: here("./index.html") },
      output: {
        // React, TanStack Query, Radix and Sonner change rarely: one long-cached vendor file. The
        // shared QueryClient goes with the ui code: as a chunk of its own it picked up the bundler's
        // CommonJS runtime, which vendor imports, while it imports vendor, and neither page started.
        codeSplitting: { groups: [{ name: "vendor", test: /node_modules/ }, { name: "ui", test: /src\/(styles|components|hooks|lib)\/|src\/app\/queryClient/ }] },
      },
    },
  },
  server: {
    proxy: Object.fromEntries(proxied.map((path) => [path, { target: API, changeOrigin: false }])),
  },
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
    coverage: {
      provider: "v8",
      include: ["src/lib/**/*.ts"],
      exclude: ["src/lib/**/*.test.ts"],
      reporter: ["text", "html"],
      reportsDirectory: "coverage",
    },
  },
});
