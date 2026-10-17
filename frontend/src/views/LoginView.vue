<script setup lang="ts">
import { computed, ref } from "vue";
import { useMutation, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import { ApiError } from "../api";
import { authenticate } from "../auth";
import AppIcon from "../components/AppIcon.vue";

const { t } = useI18n();
const client = useQueryClient();
const router = useRouter();
const route = useRoute();
const email = ref("");
const password = ref("");
const login = useMutation({
  mutationFn: () => authenticate(client, email.value.trim(), password.value),
  onSuccess: async () => {
    password.value = "";
    await router.replace("/");
  },
  onError: () => {
    password.value = "";
  },
});
const errorKey = computed(() => {
  if (login.error.value instanceof ApiError) {
    if (login.error.value.status === 401) return "invalidCredentials";
    if (login.error.value.status === 429) return "rateLimited";
  }
  return "loginFailed";
});
</script>

<template>
  <main id="main-content" class="login-layout">
    <div class="login-story">
      <div class="login-wordmark">
        <AppIcon name="mark" /><span>{{ t("brand") }}</span>
      </div>
      <div class="login-story-content">
        <div class="eyebrow">{{ t("privateWorkspace") }}</div>
        <h1>{{ t("tagline") }}</h1>
        <p>{{ t("loginDescription") }}</p>
        <div class="receipt-art" aria-hidden="true">
          <span /><span /><span /><span /><span />
          <div class="art-total" />
          <div class="art-barcode" />
        </div>
      </div>
      <span class="login-footnote">{{ t("privateData") }}</span>
    </div>
    <div class="login-form-section">
      <div class="login-form-inner">
        <div class="eyebrow">{{ t("brand") }}</div>
        <h2>{{ t("welcome") }}</h2>
        <p class="intro">{{ t("signInIntro") }}</p>
        <p
          v-if="route.query.reason === 'unavailable' && !login.isError.value"
          role="alert"
          class="form-alert"
        >
          {{ t("sessionUnavailable") }}
        </p>
        <form
          :aria-busy="login.isPending.value"
          @submit.prevent="login.mutate()"
        >
          <label for="email">{{ t("email") }}</label>
          <input
            id="email"
            v-model="email"
            type="email"
            name="email"
            autocomplete="username"
            required
            :disabled="login.isPending.value"
          />
          <label for="password">{{ t("password") }}</label>
          <input
            id="password"
            v-model="password"
            type="password"
            name="password"
            autocomplete="current-password"
            required
            :disabled="login.isPending.value"
          />
          <p class="field-note">{{ t("accountNote") }}</p>
          <p v-if="login.isError.value" role="alert" class="form-alert">
            {{ t(errorKey) }}
          </p>
          <button
            type="submit"
            class="button primary login-submit"
            :disabled="login.isPending.value"
          >
            {{ t(login.isPending.value ? "signingIn" : "signIn")
            }}<AppIcon name="arrow" />
          </button>
        </form>
        <p class="registration-note">{{ t("closedRegistration") }}</p>
      </div>
    </div>
  </main>
</template>
