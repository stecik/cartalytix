import { readonly, ref } from "vue";

export type ThemePreference = "system" | "light" | "dark";
type ResolvedTheme = Exclude<ThemePreference, "system">;

const preference = ref<ThemePreference>("system");
const resolved = ref<ResolvedTheme>("light");
export const themePreference = readonly(preference);
export const resolvedTheme = readonly(resolved);
let system: MediaQueryList | undefined;
let stopListening: (() => void) | undefined;

export function isThemePreference(value: unknown): value is ThemePreference {
  return value === "system" || value === "light" || value === "dark";
}

function applyTheme(): void {
  const theme = preference.value === "system" ? (system?.matches ? "dark" : "light") : preference.value;
  document.documentElement.dataset.theme = theme;
  resolved.value = theme;
  const background = getComputedStyle(document.documentElement).getPropertyValue("--bg-page").trim();
  if (background) document.querySelector('meta[name="theme-color"]')?.setAttribute("content", background);
}

export function setTheme(value: ThemePreference): void {
  preference.value = value;
  applyTheme();
  try {
    localStorage.setItem("cartalytix-theme", value);
  } catch {
    /* Account preferences still work when device storage is unavailable. */
  }
}

export function initializeTheme(): () => void {
  stopListening?.();
  system = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : undefined;
  let saved: unknown;
  try {
    saved = localStorage.getItem("cartalytix-theme");
  } catch {
    /* Follow the system if device storage is unavailable. */
  }
  preference.value = isThemePreference(saved) ? saved : "system";
  applyTheme();
  const media = system;
  media?.addEventListener("change", applyTheme);
  const cleanup = (): void => media?.removeEventListener("change", applyTheme);
  stopListening = cleanup;
  return cleanup;
}
