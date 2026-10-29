// @vitest-environment node
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { Resvg } from "@resvg/resvg-js";
import { build, createServer } from "vite";
import { VitePWA } from "vite-plugin-pwa";
import type { VitePluginPWAAPI } from "vite-plugin-pwa";
import { afterEach, describe, expect, it } from "vitest";
import { createPwaPalette } from "../pwaPalette";

const roots: string[] = [];

afterEach(async () => {
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

async function fixture(green: string = "12, 34, 56"): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "cartalytix-palette-"));
  roots.push(root);
  await mkdir(join(root, "src/assets"), { recursive: true });
  await Promise.all([
    writeFile(join(root, "src/colors.css"), `:root {
      --accent-rgb: ${green};
      --accent: rgb(var(--accent-rgb));
      --bg-page-base: #abcdef;
      --bg-page: var(--bg-page-base);
      --bg-surface: rgba(255, 255, 255, 1);
    }`),
    writeFile(join(root, "index.html"), '<html><head><meta name="theme-color" content="%PALETTE_THEME_COLOR%"></head><body></body></html>'),
    writeFile(join(root, "src/assets/icon.svg"), '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"><rect width="512" height="512" rx="112" fill="var(--accent)"/><rect x="128" y="128" width="256" height="256" fill="var(--bg-page)"/></svg>'),
    writeFile(join(root, "src/assets/text-cursor.svg"), '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><path stroke="var(--bg-surface)" d="M0 0h24"/><path stroke="var(--accent)" d="M0 1h24"/></svg>'),
  ]);
  return root;
}

async function generate(root: string): Promise<void> {
  const palette = createPwaPalette(root);
  await build({
    configFile: false,
    root,
    logLevel: "silent",
    plugins: [palette.plugin, VitePWA({
      integration: palette.integration,
      manifest: { name: "Test", ...palette.manifestColors, icons: [{ src: "/icon.svg", sizes: "any", type: "image/svg+xml" }] },
      workbox: { globPatterns: ["**/*.{html,svg,png}"] },
    })],
  });
}

describe("PWA palette assets", () => {
  it("keeps generated branding on the base palette when dark overrides follow it", async () => {
    const root = await fixture();
    const palettePath = join(root, "src/colors.css");
    const base = await readFile(palettePath, "utf8");
    await writeFile(palettePath, `${base}
      :root[data-theme="dark"] {
        --accent-rgb: 133, 190, 158;
        --bg-page-base: #111a16;
        --bg-surface: #1d2923;
      }
      .unrelated { --accent-rgb: 1, 2, 3; }
    `);
    await generate(root);
    const output = join(root, "dist");
    expect(JSON.parse(await readFile(join(output, "manifest.webmanifest"), "utf8")))
      .toMatchObject({ theme_color: "rgb(12, 34, 56)", background_color: "#abcdef" });
    expect(await readFile(join(output, "icon.svg"), "utf8"))
      .toContain('fill="rgb(12, 34, 56)"');
    expect(await readFile(join(output, "text-cursor.svg"), "utf8"))
      .toContain('stroke="rgba(255, 255, 255, 1)"');
  }, 30_000);

  it("resolves aliases for generated metadata, manifest, SVG and PNG assets", async () => {
    const root = await fixture();
    await generate(root);
    const output = join(root, "dist");
    expect(await readFile(join(output, "index.html"), "utf8")).toContain('name="theme-color" content="rgb(12, 34, 56)"');
    const manifest = JSON.parse(await readFile(join(output, "manifest.webmanifest"), "utf8")) as Record<string, unknown>;
    expect(manifest.theme_color).toBe("rgb(12, 34, 56)");
    expect(manifest.background_color).toBe("#abcdef");
    expect(await readFile(join(output, "icon.svg"), "utf8")).toContain('fill="rgb(12, 34, 56)"');
    expect(await readFile(join(output, "icon.svg"), "utf8")).toContain('fill="#abcdef"');
    expect(await readFile(join(output, "text-cursor.svg"), "utf8")).toContain('stroke="rgba(255, 255, 255, 1)"');
    for (const [file, size] of [["icon-192.png", 192], ["icon-512.png", 512], ["apple-touch-icon.png", 180]] as const) {
      const png = await readFile(join(output, file));
      expect(png.subarray(1, 4).toString()).toBe("PNG");
      expect(png.readUInt32BE(16)).toBe(size);
      expect(png.readUInt32BE(20)).toBe(size);
      const pixels = new Resvg(`<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}"><image width="${size}" height="${size}" href="data:image/png;base64,${png.toString("base64")}"/></svg>`, { font: { loadSystemFonts: false } }).render().pixels;
      expect([...pixels.subarray(0, 4)]).toEqual([12, 34, 56, 255]);
      const center = (Math.floor(size / 2) * size + Math.floor(size / 2)) * 4;
      expect([...pixels.subarray(center, center + 4)]).toEqual([171, 205, 239, 255]);
    }
    const worker = await readFile(join(output, "sw.js"), "utf8");
    expect(worker).toContain("icon-192.png");
    expect(worker).toContain("text-cursor.svg");
    const previousPng = await readFile(join(output, "icon-192.png"));
    await writeFile(join(root, "src/colors.css"), ":root {\n--accent: #eeddcc;\n--bg-page: #abcdef;\n--bg-surface: #ffffff;\n}");
    await generate(root);
    expect(await readFile(join(output, "icon-192.png"))).not.toEqual(previousPng);
    expect(await readFile(join(output, "index.html"), "utf8")).toContain('content="#eeddcc"');
    expect(JSON.parse(await readFile(join(output, "manifest.webmanifest"), "utf8"))).toMatchObject({ theme_color: "#eeddcc" });
  }, 30_000);

  it("refreshes palette assets and metadata within the same build watcher", async () => {
    const root = await fixture();
    const palette = createPwaPalette(root);
    const pwa = VitePWA({
      integration: palette.integration,
      manifest: { name: "Test", ...palette.manifestColors },
      workbox: { globPatterns: ["**/*.{html,svg,png}"] },
    });
    const watcher = await build({
      configFile: false,
      root,
      logLevel: "silent",
      build: { watch: {} },
      plugins: [palette.plugin, pwa],
    });
    if (Array.isArray(watcher) || !("on" in watcher)) throw new Error("Expected build watcher");
    let nextBuild = Promise.withResolvers<void>();
    watcher.on("event", (event) => {
      if (event.code === "END") nextBuild.resolve();
      if (event.code === "ERROR") nextBuild.reject(event.error);
    });
    const output = join(root, "dist");
    try {
      await nextBuild.promise;
      const originalPng = await readFile(join(output, "icon-192.png"));
      expect(await readFile(join(output, "icon.svg"), "utf8")).toContain('fill="rgb(12, 34, 56)"');
      nextBuild = Promise.withResolvers<void>();
      await writeFile(join(root, "src/colors.css"), ":root {\n--accent: #eeddcc;\n--bg-page: #aa9988;\n--bg-surface: #ffeeaa;\n}");
      await nextBuild.promise;
      expect(await readFile(join(output, "index.html"), "utf8")).toContain('content="#eeddcc"');
      expect(await readFile(join(output, "icon.svg"), "utf8")).toContain('fill="#eeddcc"');
      expect(await readFile(join(output, "icon.svg"), "utf8")).toContain('fill="#aa9988"');
      expect(await readFile(join(output, "text-cursor.svg"), "utf8")).toContain('stroke="#ffeeaa"');
      expect(await readFile(join(output, "icon-192.png"))).not.toEqual(originalPng);
      expect(JSON.parse(await readFile(join(output, "manifest.webmanifest"), "utf8"))).toMatchObject({ theme_color: "#eeddcc", background_color: "#aa9988" });
    } finally {
      await watcher.close();
    }
    const api = pwa.find((plugin) => plugin.name === "vite-plugin-pwa")?.api as VitePluginPWAAPI | undefined;
    if (!api) throw new Error("Missing PWA service-worker API");
    await api.generateSW();
    const worker = await readFile(join(output, "sw.js"), "utf8");
    const revisions = new Map(Array.from(worker.matchAll(/["']?url["']?\s*:\s*["']([^"']+)["']\s*,\s*["']?revision["']?\s*:\s*["']([^"']+)["']/g), (entry) => [entry[1]!, entry[2]!]));
    for (const name of ["index.html", "icon.svg", "icon-192.png", "manifest.webmanifest"]) {
      const revision = createHash("md5").update(await readFile(join(output, name))).digest("hex");
      expect(revisions.get(name)).toBe(revision);
    }
  }, 30_000);

  it("serves the same palette assets during development", async () => {
    const root = await fixture();
    const palette = createPwaPalette(root);
    const server = await createServer({ configFile: false, root, plugins: [palette.plugin], server: { host: "127.0.0.1", port: 0 } });
    try {
      await server.listen();
      const address = server.httpServer?.address();
      if (typeof address !== "object" || address === null) throw new Error("Missing development server address");
      const base = `http://127.0.0.1:${address.port}`;
      expect(await (await fetch(`${base}/icon.svg`)).text()).toContain('fill="rgb(12, 34, 56)"');
      expect(await (await fetch(`${base}/text-cursor.svg`)).text()).toContain('stroke="rgba(255, 255, 255, 1)"');
      const icon = await fetch(`${base}/icon-512.png`);
      expect(icon.headers.get("content-type")).toBe("image/png");
      expect(Buffer.from(await icon.arrayBuffer()).readUInt32BE(16)).toBe(512);
      expect(await (await fetch(base)).text()).toContain('content="rgb(12, 34, 56)"');
    } finally {
      await server.close();
    }
  });

  it.each([
    ["--bg-page: #abcdef;\n--bg-surface: #fff;", /--accent/],
    ["--accent: var(--missing);\n--bg-page: #abcdef;\n--bg-surface: #fff;", /--missing/],
    ["--accent: var(--accent);\n--bg-page: #abcdef;\n--bg-surface: #fff;", /circular/i],
    ["--accent: rgb(900, 0, 0);\n--bg-page: #abcdef;\n--bg-surface: #fff;", /--accent.*invalid/i],
  ])("fails clearly for missing or invalid palette entries: %s", async (declarations, error) => {
    const root = await fixture();
    await writeFile(join(root, "src/colors.css"), `:root {\n${declarations}\n}`);
    expect(() => createPwaPalette(root)).toThrow(error);
  });
});
