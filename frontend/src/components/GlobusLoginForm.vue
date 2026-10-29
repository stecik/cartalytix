<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";
import { ApiError, request } from "../api";
import type { Connection } from "../api";
import AppButton from "./AppButton.vue";

const props = defineProps<{ connectionId: string }>();
const emit = defineEmits<{ connected: [connection: Connection]; cancel: [] }>();
const { t } = useI18n();
const username = ref("");
const password = ref("");
const busy = ref(false);
const errorKey = ref("");
let disposed = false;

function loginError(error: unknown): string {
  const value = error instanceof ApiError ? error.code : "";
  if (value === "credentials_rejected") return "globusCredentialsRejected";
  if (value === "account_locked") return "globusAccountLocked";
  if (value === "captcha_required") return "globusVerificationRequired";
  if (value === "login_timeout") return "globusLoginTimeout";
  if (value === "login_busy" || value === "connection_busy") return "lidlLoginBusy";
  if (["login_rate_limited", "rate_limited", "provider_unavailable", "browser_unavailable"].includes(value ?? "")) return "globusProviderBusy";
  if (value === "account_mismatch" || value === "account_identity_unavailable") return "syncIdentityFailed";
  return "globusLoginFailed";
}

async function submit(): Promise<void> {
  if (busy.value) return;
  busy.value = true;
  errorKey.value = "";
  // Keep credentials out of the mutation/query cache and clear the password before waiting.
  const body = { username: username.value, password: password.value };
  password.value = "";
  try {
    const connection = await request<Connection>(`/providers/globus/connections/${props.connectionId}/login`, {
      method: "POST", body: JSON.stringify(body),
    });
    if (!disposed) emit("connected", connection);
  } catch (error) {
    errorKey.value = loginError(error);
  } finally {
    body.username = "";
    body.password = "";
    busy.value = false;
  }
}

onBeforeUnmount(() => {
  disposed = true;
  password.value = "";
});
</script>

<template>
  <form class="connection-form" :aria-busy="busy" @submit.prevent="submit">
    <p class="section-note">{{ t("globusLoginSafety") }}</p>
    <label :for="`globus-username-${connectionId}`">{{ t("globusUsername") }}</label>
    <input :id="`globus-username-${connectionId}`" v-model="username" type="text" autocomplete="username" maxlength="254" required :disabled="busy" />
    <label :for="`globus-password-${connectionId}`">{{ t("globusPassword") }}</label>
    <input :id="`globus-password-${connectionId}`" v-model="password" type="password" autocomplete="off" maxlength="1024" required :disabled="busy" />
    <p v-if="errorKey" class="form-alert" role="alert">{{ t(errorKey) }}</p>
    <div class="connection-actions">
      <AppButton type="submit" variant="primary" :disabled="busy">{{ t(busy ? "lidlSigningIn" : "globusConnect") }}</AppButton>
      <AppButton type="button" :disabled="busy" @click="emit('cancel')">{{ t("cancel") }}</AppButton>
    </div>
  </form>
</template>
