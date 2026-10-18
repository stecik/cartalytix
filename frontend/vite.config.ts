import { defineConfig } from "vitest/config";
import type { Plugin, ViteDevServer } from "vite";
import vue from "@vitejs/plugin-vue";
import { VitePWA } from "vite-plugin-pwa";
import { createPwaPalette } from "./pwaPalette.ts";

const palette = createPwaPalette(import.meta.dirname);

export const developmentWorkerCleanup = {
  name: "retire-production-worker-in-development",
  apply: "serve",
  configureServer: (server: ViteDevServer) => {
    server.middlewares.use((request, response, next) => {
      if (request.url?.split("?")[0] !== "/sw.js") return next();
      response.setHeader("Content-Type", "application/javascript");
      response.setHeader("Cache-Control", "no-store");
      response.end(`
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const scope = self.registration.scope;
    await self.registration.unregister();
    const names = await caches.keys();
    await Promise.all(names
      .filter(name => name.startsWith("workbox-precache-") && name.endsWith(scope))
      .map(name => caches.delete(name)));
    const clients = await self.clients.matchAll({ type: "window" });
    await Promise.all(clients.map(client => client.navigate(client.url)));
  })());
});
`);
    });
  },
} satisfies Plugin;

export default defineConfig({
  plugins: [
    developmentWorkerCleanup,
    palette.plugin,
    vue(),
    VitePWA({
      integration: palette.integration,
      registerType: "prompt",
      includeAssets: [
        "icon.svg",
        "icon-192.png",
        "icon-512.png",
        "apple-touch-icon.png",
      ],
      manifest: {
        name: "Cartalytix",
        short_name: "Cartalytix",
        description: "Your shopping receipts, in one place.",
        lang: "en",
        ...palette.manifestColors,
        display: "standalone",
        start_url: "/",
        scope: "/",
        icons: [
          {
            src: "/icon.svg",
            sizes: "any",
            type: "image/svg+xml",
            purpose: "any",
          },
          {
            src: "/icon-192.png",
            sizes: "192x192",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "/icon-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any",
          },
        ],
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,png,woff2}"],
        navigateFallbackDenylist: [/^\/api(?:\/|$)/, /^\/health$/],
        runtimeCaching: [],
        cleanupOutdatedCaches: true,
      },
    }),
  ],
  server: {
    proxy: {
      "/api": {
        target: process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000",
        timeout: 180_000,
        proxyTimeout: 180_000,
      },
      // MCP connector endpoints and their OAuth discovery documents.
      "^/(mcp|\\.well-known/oauth-(protected-resource|authorization-server))(/|$)": {
        target: process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000",
      },
    },
  },
  test: { environment: "jsdom", clearMocks: true, restoreMocks: true },
});
