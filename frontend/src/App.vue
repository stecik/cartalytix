<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import { useRegisterSW } from "virtual:pwa-register/vue";
import { clearSession, sessionOptions, signOut } from "./auth";
import AppIcon from "./components/AppIcon.vue";

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
const links = [
  { to: "/", name: "overview" },
  { to: "/receipts", name: "receipts" },
  { to: "/connections", name: "connections" },
] as const;
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
          :class="{ active: route.name === link.name || (link.name === 'receipts' && route.name === 'receipt-detail') }"
          >
<AppIcon :name="link.name" />{{ t(link.name) }}
</RouterLink>
      </nav>
      <div class="sidebar-bottom">
        <span class="privacy-mark" aria-hidden="true">◒</span><span>{{ t("privateData") }}</span>
      </div>
    </aside>
    <div class="app-main">
      <header class="topbar">
        <RouterLink v-if="!isLogin" to="/" class="mobile-wordmark">
<AppIcon name="mark" />{{ t("brand") }}
</RouterLink>
        <span v-if="!isLogin" class="account-email">{{
          session.data.value?.email
        }}</span>
        <div class="topbar-actions">
          <button
            v-if="installPrompt"
            class="text-button install-button"
            @click="install"
          >
            <AppIcon name="download" /><span>{{ t("install") }}</span>
          </button>
          <label class="language-select"><span class="sr-only">{{ t("language") }}</span><select v-model="locale" :aria-label="t('language')">
              <option value="en">{{ t("english") }}</option>
              <option value="cs">{{ t("czech") }}</option>
            </select></label>
          <button
            v-if="!isLogin"
            class="text-button logout-button"
            :disabled="logout.isPending.value"
            :aria-label="t(logout.isPending.value ? 'signingOut' : 'signOut')"
            @click="logout.mutate()"
          >
            <AppIcon name="logout" /><span>{{
              t(logout.isPending.value ? "signingOut" : "signOut")
            }}</span>
          </button>
        </div>
      </header>
      <div v-if="!online" class="notice" role="status">{{ t("offline") }}</div>
      <div v-if="logout.isError.value" class="notice notice-error" role="alert">
        {{ t("logoutFailed") }}
      </div>
      <div v-if="needRefresh" class="notice update-notice" role="status">
        <span>{{ t("updateReady") }}</span><button class="text-button" @click="updateServiceWorker(true)">
          {{ t("update") }}
</button><button class="text-button" @click="needRefresh = false">
          {{ t("dismiss") }}
        </button>
      </div>
      <RouterView v-if="isLogin" />
      <main v-else id="main-content" class="workspace-main">
        <RouterView />
      </main>
    </div>
    <nav v-if="!isLogin" :aria-label="t('navigation')" class="mobile-nav">
      <RouterLink
        v-for="link in links"
        :key="link.to"
        :to="link.to"
        :class="{ active: route.name === link.name || (link.name === 'receipts' && route.name === 'receipt-detail') }"
        >
<AppIcon :name="link.name" /><span>{{
          t(link.name)
        }}</span>
</RouterLink>
    </nav>
  </div>
</template>
