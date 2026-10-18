import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { initializeTheme, resolvedTheme, setTheme, themePreference } from "./theme";

let dark = false;
const listeners = new Set<() => void>();
let dispose: (() => void) | undefined;

beforeEach(() => {
  localStorage.clear();
  dark = false;
  listeners.clear();
  vi.stubGlobal("matchMedia", vi.fn(() => ({
    get matches() { return dark; },
    addEventListener: (_event: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_event: string, listener: () => void) => listeners.delete(listener),
  })));
});

afterEach(() => {
  dispose?.();
  dispose = undefined;
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("style");
  document.head.querySelector('meta[name="theme-color"]')?.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function changeSystem(value: boolean): void {
  dark = value;
  for (const listener of listeners) listener();
}

describe("appearance preference", () => {
  it("follows the system until an explicit preference is selected", () => {
    dark = true;
    dispose = initializeTheme();
    expect(themePreference.value).toBe("system");
    expect(document.documentElement.dataset.theme).toBe("dark");
    changeSystem(false);
    expect(resolvedTheme.value).toBe("light");
    setTheme("dark");
    changeSystem(true);
    changeSystem(false);
    expect(resolvedTheme.value).toBe("dark");
    expect(localStorage.getItem("cartalytix-theme")).toBe("dark");
    setTheme("system");
    expect(resolvedTheme.value).toBe("light");
  });

  it("restores a saved light preference even on a dark system", () => {
    dark = true;
    localStorage.setItem("cartalytix-theme", "light");
    dispose = initializeTheme();
    expect(themePreference.value).toBe("light");
    expect(resolvedTheme.value).toBe("light");
  });

  it("falls back to the system for invalid storage and survives blocked storage", () => {
    localStorage.setItem("cartalytix-theme", "invalid");
    dispose = initializeTheme();
    expect(themePreference.value).toBe("system");
    dispose();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    dark = true;
    dispose = initializeTheme();
    expect(resolvedTheme.value).toBe("dark");
    expect(() => setTheme("light")).not.toThrow();
    expect(resolvedTheme.value).toBe("light");
  });

  it("updates the browser chrome from CSS and replaces its system listener on initialization", () => {
    const meta = document.createElement("meta");
    meta.name = "theme-color";
    document.head.append(meta);
    document.documentElement.style.setProperty("--paper", "rgb(20, 30, 25)");
    initializeTheme();
    dispose = initializeTheme();
    expect(listeners.size).toBe(1);
    setTheme("dark");
    expect(meta.content).toBe("rgb(20, 30, 25)");
    dispose();
    expect(listeners.size).toBe(0);
  });
});
