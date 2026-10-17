import { runInNewContext } from "node:vm";
import type { ViteDevServer } from "vite";
import { describe, expect, it, vi } from "vitest";
import { developmentWorkerCleanup } from "../vite.config";

function middleware() {
  const use = vi.fn();
  developmentWorkerCleanup.configureServer({ middlewares: { use } } as unknown as ViteDevServer);
  const response = { setHeader: vi.fn(), end: vi.fn() };
  const handle = use.mock.calls[0]![0] as (
    request: { url: string }, reply: typeof response, next: () => void,
  ) => void;
  return { handle, response };
}

describe("development service worker retirement", () => {
  it("only applies to development and serves uncached JavaScript for the old worker URL", () => {
    expect(developmentWorkerCleanup.apply).toBe("serve");
    const { handle, response } = middleware();
    const next = vi.fn();
    handle({ url: "/sw.js?update" }, response, next);
    expect(next).not.toHaveBeenCalled();
    expect(response.setHeader).toHaveBeenCalledWith("Content-Type", "application/javascript");
    expect(response.setHeader).toHaveBeenCalledWith("Cache-Control", "no-store");
    expect(response.end).toHaveBeenCalledWith(expect.any(String));
  });

  it("leaves application, API and asset requests unchanged", () => {
    const { handle, response } = middleware();
    const next = vi.fn();
    for (const url of ["/", "/api/v1/connections", "/src/main.ts", "/sw.js.map"]) {
      handle({ url }, response, next);
    }
    expect(next).toHaveBeenCalledTimes(4);
    expect(response.end).not.toHaveBeenCalled();
  });

  it("unregisters the stale worker, clears only its precache and reloads its clients", async () => {
    const { handle, response } = middleware();
    handle({ url: "/sw.js" }, response, vi.fn());
    const source = response.end.mock.calls[0]![0] as string;
    type WorkerEvent = { waitUntil: (promise: Promise<unknown>) => void };
    const listeners = new Map<string, (event: WorkerEvent) => void>();
    const unregister = vi.fn().mockResolvedValue(true);
    const navigate = vi.fn().mockResolvedValue(undefined);
    const removeCache = vi.fn().mockResolvedValue(true);
    const skipWaiting = vi.fn();
    const scope = "http://localhost:18080/";
    runInNewContext(source, {
      self: {
        addEventListener: (name: string, listener: (event: WorkerEvent) => void) => listeners.set(name, listener),
        skipWaiting,
        registration: { scope, unregister },
        clients: { matchAll: vi.fn().mockResolvedValue([{ url: scope, navigate }]) },
      },
      caches: {
        keys: vi.fn().mockResolvedValue([`workbox-precache-v2-${scope}`, "keep-other-cache", `workbox-precache-v2-${scope}other/`]),
        delete: removeCache,
      },
    });
    listeners.get("install")!({ waitUntil: vi.fn() });
    expect(skipWaiting).toHaveBeenCalledOnce();
    let activation: Promise<unknown> | undefined;
    listeners.get("activate")!({ waitUntil: (promise) => { activation = promise; } });
    await activation;
    expect(unregister).toHaveBeenCalledOnce();
    expect(removeCache).toHaveBeenCalledExactlyOnceWith(`workbox-precache-v2-${scope}`);
    expect(navigate).toHaveBeenCalledExactlyOnceWith(scope);
  });
});
