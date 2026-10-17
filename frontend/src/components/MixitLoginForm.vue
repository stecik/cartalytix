<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";
import { ApiError, request } from "../api";
import type { Connection } from "../api";

const props = defineProps<{ connectionId: string }>();
const emit = defineEmits<{ connected: [connection: Connection]; cancel: [] }>();
const { t } = useI18n();
const email = ref("");
const password = ref("");
const busy = ref(false);
const errorKey = ref("");
let disposed = false;

function loginError(error: unknown): string {
  const value = error instanceof ApiError ? error.code : "";
  if (value === "credentials_rejected") return "mixitCredentialsRejected";
  if (value === "connection_busy") return "lidlLoginBusy";
  if (["login_rate_limited", "rate_limited", "provider_unavailable"].includes(value ?? "")) return "mixitProviderBusy";
  if (value === "account_mismatch" || value === "account_identity_unavailable") return "syncIdentityFailed";
  return "mixitLoginFailed";
}

async function submit(): Promise<void> {
  if (busy.value) return;
  busy.value = true;
  errorKey.value = "";
  // Keep the password out of the mutation/query cache and clear the input before waiting.
  const body = { email: email.value, password: password.value };
  password.value = "";
  try {
    const connection = await request<Connection>(`/providers/mixit/connections/${props.connectionId}/login`, {
      method: "POST", body: JSON.stringify(body),
    });
    if (!disposed) emit("connected", connection);
  } catch (error) {
    errorKey.value = loginError(error);
  } finally {
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
    <p class="section-note">{{ t("mixitLoginSafety") }}</p>
    <label :for="`mixit-email-${connectionId}`">{{ t("mixitEmail") }}</label>
    <input :id="`mixit-email-${connectionId}`" v-model="email" type="email" autocomplete="username" maxlength="254" required :disabled="busy" />
    <label :for="`mixit-password-${connectionId}`">{{ t("mixitPassword") }}</label>
    <input :id="`mixit-password-${connectionId}`" v-model="password" type="password" autocomplete="off" maxlength="1024" required :disabled="busy" />
    <p v-if="errorKey" class="form-alert" role="alert">{{ t(errorKey) }}</p>
    <div class="connection-actions">
      <button type="submit" class="button primary" :disabled="busy">{{ t(busy ? "lidlSigningIn" : "mixitSignIn") }}</button>
      <button type="button" class="button secondary" :disabled="busy" @click="emit('cancel')">{{ t("cancel") }}</button>
    </div>
  </form>
</template>
