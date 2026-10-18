import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";
import { Resvg } from "@resvg/resvg-js";
import { normalizePath } from "vite";
import type { Plugin } from "vite";
import type { PWAIntegration } from "vite-plugin-pwa";

type ManifestColors = { theme_color: string; background_color: string };
type Asset = { source: string | Uint8Array; contentType: string };

function paletteReader(css: string): (name: string) => string {
  const basePalette = Array.from(
    css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/^\s*:root\s*\{([^{}]*)\}/gm),
    (match) => match[1]!,
  ).join("\n");
  const declarations = new Map(
    Array.from(basePalette.matchAll(/(--[\w-]+)\s*:\s*([^;\r\n]+);/g),
      (match) => [match[1]!, match[2]!.trim()]),
  );
  function resolve(name: string, ancestors: string[] = []): string {
    if (ancestors.includes(name)) throw new Error(`colors.css: circular palette reference ${[...ancestors, name].join(" -> ")}`);
    const value = declarations.get(name);
    if (value === undefined) throw new Error(`colors.css: missing palette entry ${name}`);
    return value.replace(/var\(\s*(--[\w-]+)\s*\)/g, (_, reference: string) => resolve(reference, [...ancestors, name]));
  }
  return (name) => {
    const value = resolve(name);
    const functional = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*([\d.]+))?\s*\)$/.exec(value);
    const validHex = /^#(?:[\da-f]{3}|[\da-f]{4}|[\da-f]{6}|[\da-f]{8})$/i.test(value);
    const validFunction = functional !== null
      && functional.slice(1, 4).every((channel) => Number.isFinite(Number(channel)) && Number(channel) >= 0 && Number(channel) <= 255)
      && (value.startsWith("rgba(") ? functional[4] !== undefined && Number(functional[4]) >= 0 && Number(functional[4]) <= 1 : functional[4] === undefined);
    if (!validHex && !validFunction) throw new Error(`colors.css: ${name} has invalid color ${value}`);
    return value;
  };
}

export function createPwaPalette(root: string): { plugin: Plugin; manifestColors: ManifestColors; integration: PWAIntegration } {
  const palettePath = join(root, "src/colors.css");
  const iconPath = join(root, "src/assets/icon.svg");
  const cursorPath = join(root, "src/assets/text-cursor.svg");
  const manifestColors = {} as ManifestColors;
  const assets = new Map<string, Asset>();
  let manifestRevision: string | undefined;
  function refresh(): void {
    const color = paletteReader(readFileSync(palettePath, "utf8"));
    Object.assign(manifestColors, { theme_color: color("--green"), background_color: color("--paper") });
    const renderSvg = (path: string): string => readFileSync(path, "utf8").replace(/var\(\s*(--[\w-]+)\s*\)/g, (_, name: string) => color(name));
    const icon = renderSvg(iconPath);
    assets.set("icon.svg", { source: icon, contentType: "image/svg+xml" });
    assets.set("text-cursor.svg", { source: renderSvg(cursorPath), contentType: "image/svg+xml" });
    for (const [name, size] of [["icon-192.png", 192], ["icon-512.png", 512], ["apple-touch-icon.png", 180]] as const) {
      assets.set(name, {
        source: new Resvg(icon, {
          fitTo: { mode: "width", value: size },
          background: manifestColors.theme_color,
          font: { loadSystemFonts: false },
        }).render().asPng(),
        contentType: "image/png",
      });
    }
  }
  refresh();
  return {
    manifestColors,
    integration: {
      beforeBuildServiceWorker(options) {
        if (!manifestRevision) throw new Error("PWA manifest must be emitted before service-worker generation");
        for (const entry of options.workbox.additionalManifestEntries ?? []) {
          if (typeof entry !== "string" && entry.url === options.manifestFilename) entry.revision = manifestRevision;
        }
      },
    },
    plugin: {
      name: "pwa-palette-assets",
      config: () => ({ build: { rolldownOptions: { external: ["/text-cursor.svg"] } } }),
      buildStart() {
        refresh();
        for (const path of [palettePath, iconPath, cursorPath]) this.addWatchFile(path);
      },
      transformIndexHtml: {
        order: "pre",
        handler: (html) => html.replaceAll("%PALETTE_THEME_COLOR%", manifestColors.theme_color),
      },
      generateBundle: {
        order: "post",
        handler(_, bundle) {
          const manifest = bundle["manifest.webmanifest"];
          if (!manifest || manifest.type !== "asset") throw new Error("Missing generated PWA manifest.webmanifest");
          const source = typeof manifest.source === "string" ? manifest.source : Buffer.from(manifest.source).toString("utf8");
          const content = JSON.parse(source) as Record<string, unknown>;
          manifest.source = `${JSON.stringify({ ...content, ...manifestColors })}\n`;
          manifestRevision = createHash("md5").update(manifest.source).digest("hex");
          for (const [fileName, asset] of assets) this.emitFile({ type: "asset", fileName, source: asset.source });
        },
      },
      configureServer(server) {
        const sources = [palettePath, iconPath, cursorPath].map(normalizePath);
        server.watcher.add(sources);
        server.watcher.on("change", (path) => {
          if (sources.includes(normalizePath(path))) void server.restart();
        });
        server.middlewares.use((request, response, next) => {
          const asset = assets.get(request.url?.split("?")[0]?.slice(1) ?? "");
          if (!asset) return next();
          response.setHeader("Content-Type", asset.contentType);
          response.setHeader("Cache-Control", "no-cache");
          response.end(asset.source);
        });
      },
    },
  };
}
