<script setup lang="ts">
import { computed, onBeforeUnmount, provide, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import { useRegisterSW } from "virtual:pwa-register/vue";
import { clearSession, sessionOptions, signOut } from "./auth";
import AppIcon from "./components/AppIcon.vue";
import AppSelect from "./components/AppSelect.vue";
import { useUserSettings } from "./settings";
import { pwaInstallKey } from "./pwaInstall";
import { isThemePreference, setTheme } from "./theme";
import AppButton from "./components/AppButton.vue";

interface InstallPrompt extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

const { t, locale } = useI18n();
const route = useRoute();
const router = useRouter();
const client = useQueryClient();
const isLogin = computed(() => route.name === "login");
const session = useQuery({
  ...sessionOptions,
  enabled: computed(() => !isLogin.value),
});
watch(session.data, async (user) => {
  if (user === null && !isLogin.value) {
    await clearSession(client);
    await router.replace("/login");
  }
});
const logout = useMutation({
  mutationFn: () => signOut(client),
  onSuccess: () => router.replace("/login"),
});
const userLinks = [
  { to: "/", name: "overview" },
  { to: "/receipts", name: "receipts" },
  { to: "/items", name: "items" },
  { to: "/categories", name: "categories" },
  { to: "/calculator", name: "calculator" },
  { to: "/connections", name: "connections" },
  { to: "/settings", name: "settings" },
] as const;
const adminLinks = [
  { to: "/accounts", name: "accounts" },
  { to: "/settings", name: "settings" },
] as const;
const links = computed(() => {
  const user = session.data.value;
  if (!user || user.must_change_password) return [];
  return user.is_admin ? adminLinks : userLinks;
});
const mobileLinks = computed(() => links.value.filter((link) => link.name !== "settings"));
const settings = useUserSettings();
watch(
  () => session.data.value?.theme,
  (value) => { if (isThemePreference(value)) setTheme(value); },
  { immediate: true },
);
watch(
  () => session.data.value?.locale,
  (value) => {
    if ((value === "en" || value === "cs") && value !== locale.value) locale.value = value;
  },
  { immediate: true },
);

const languages = computed(() => [
  { value: "en", label: t("english") },
  { value: "cs", label: t("czech") },
]);

function changeLanguage(value: string): void {
  if (session.data.value && !isLogin.value) settings.mutate({ locale: value });
  else locale.value = value;
}
const online = ref(navigator.onLine);
const installPrompt = ref<InstallPrompt | null>(null);
const { needRefresh, updateServiceWorker } = useRegisterSW();

function setOnline(): void {
  online.value = navigator.onLine;
}
function captureInstall(event: Event): void {
  event.preventDefault();
  installPrompt.value = event as InstallPrompt;
}
function clearInstall(): void {
  installPrompt.value = null;
}
async function install(): Promise<void> {
  if (!installPrompt.value) return;
  await installPrompt.value.prompt();
  await installPrompt.value.userChoice;
  installPrompt.value = null;
}

provide(pwaInstallKey, { available: computed(() => installPrompt.value !== null), install });

window.addEventListener("online", setOnline);
window.addEventListener("offline", setOnline);
window.addEventListener("beforeinstallprompt", captureInstall);
window.addEventListener("appinstalled", clearInstall);
onBeforeUnmount(() => {
  window.removeEventListener("online", setOnline);
  window.removeEventListener("offline", setOnline);
  window.removeEventListener("beforeinstallprompt", captureInstall);
  window.removeEventListener("appinstalled", clearInstall);
});
watch(
  locale,
  (value) => {
    document.documentElement.lang = value;
    try {
      localStorage.setItem("cartalytix-language", value);
    } catch {
      /* App remains usable without browser storage. */
    }
  },
  { immediate: true },
);
</script>

<template>
  <a class="skip-link" href="#main-content">{{ t("skipToContent") }}</a>
  <div :class="['app', { 'login-app': isLogin }]">
    <aside v-if="!isLogin" class="sidebar">
      <RouterLink to="/" class="wordmark">
<span class="brand-mark"><AppIcon name="mark" /></span>{{ t("brand") }}
</RouterLink>
      <span class="sidebar-label">{{ t("workspace") }}</span>
      <nav :aria-label="t('navigation')" class="desktop-nav">
        <RouterLink
          v-for="link in links"
          :key="link.to"
          :to="link.to"
          :class="{ active: route.name === link.name || (link.name === 'receipts' && route.name === 'receipt-detail') || (link.name === 'items' && route.name === 'item-detail') }"
          >
<AppIcon :name="link.name" />{{ t(link.name) }}
</RouterLink>
      </nav>
    </aside>
    <div class="app-main">
      <header class="topbar">
        <RouterLink v-if="!isLogin" to="/" class="mobile-wordmark">
<AppIcon name="mark" /><span>{{ t("brand") }}</span>
</RouterLink>
        <span v-if="!isLogin" class="account-email">{{
          session.data.value?.email
        }}</span>
        <div class="topbar-actions">
          <AppButton
            v-if="installPrompt"
            variant="text" class="install-button"
            @click="install"
          >
            <AppIcon name="download" /><span>{{ t("install") }}</span>
          </AppButton>
          <AppSelect class="language-select" compact :model-value="locale" :options="languages" :label="t('language')" @update:model-value="changeLanguage" />
          <RouterLink
            v-if="!isLogin && links.length"
            to="/settings"
            class="mobile-settings"
            :class="{ active: route.name === 'settings' }"
            :aria-label="t('settings')"
            :title="t('settings')"
          >
            <AppIcon name="gear" />
          </RouterLink>
          <AppButton
            v-if="!isLogin"
            variant="text" class="logout-button"
            :disabled="logout.isPending.value"
            :aria-label="t(logout.isPending.value ? 'signingOut' : 'signOut')"
            @click="logout.mutate()"
          >
            <AppIcon name="logout" /><span>{{
              t(logout.isPending.value ? "signingOut" : "signOut")
            }}</span>
          </AppButton>
        </div>
      </header>
      <div v-if="!online" class="notice" role="status">{{ t("offline") }}</div>
      <div v-if="logout.isError.value" class="notice notice-error" role="alert">
        {{ t("logoutFailed") }}
      </div>
      <div v-if="needRefresh" class="notice update-notice" role="status">
        <span>{{ t("updateReady") }}</span><AppButton variant="text" @click="updateServiceWorker(true)">
          {{ t("update") }}
</AppButton><AppButton variant="text" @click="needRefresh = false">
          {{ t("dismiss") }}
        </AppButton>
      </div>
      <RouterView v-if="isLogin" />
      <main v-else id="main-content" class="workspace-main">
        <RouterView />
      </main>
    </div>
    <nav v-if="!isLogin && mobileLinks.length" :aria-label="t('navigation')" class="mobile-nav">
      <RouterLink
        v-for="link in mobileLinks"
        :key="link.to"
        :to="link.to"
        :class="{ active: route.name === link.name || (link.name === 'receipts' && route.name === 'receipt-detail') || (link.name === 'items' && route.name === 'item-detail') }"
        >
<AppIcon :name="link.name" /><span>{{
          t(link.name)
        }}</span>
</RouterLink>
    </nav>
  </div>
</template>
