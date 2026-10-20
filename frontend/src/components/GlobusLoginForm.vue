<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";
import { ApiError, request } from "../api";
import type { Connection } from "../api";

const props = defineProps<{ connectionId: string }>();
const emit = defineEmits<{ connected: [connection: Connection]; cancel: [] }>();
const { t } = useI18n();
const cookie = ref("");
const busy = ref(false);
const errorKey = ref("");
let disposed = false;

function loginError(error: unknown): string {
  const value = error instanceof ApiError ? error.code : "";
  if (value === "session_cookie_invalid" || value === "validation_error") return "globusCookieInvalid";
  if (value === "session_cookie_rejected") return "globusCookieRejected";
  if (value === "connection_busy") return "lidlLoginBusy";
  if (["login_rate_limited", "rate_limited", "provider_unavailable"].includes(value ?? "")) return "globusProviderBusy";
  if (value === "account_mismatch" || value === "account_identity_unavailable") return "syncIdentityFailed";
  return "globusLoginFailed";
}

async function submit(): Promise<void> {
  if (busy.value) return;
  busy.value = true;
  errorKey.value = "";
  // Keep the session cookie out of the mutation/query cache and clear the input before waiting.
  const body = { cookie: cookie.value.trim() };
  cookie.value = "";
  try {
    const connection = await request<Connection>(`/providers/globus/connections/${props.connectionId}/login`, {
      method: "POST", body: JSON.stringify(body),
    });
    if (!disposed) emit("connected", connection);
  } catch (error) {
    errorKey.value = loginError(error);
  } finally {
    body.cookie = "";
    busy.value = false;
  }
}

onBeforeUnmount(() => {
  disposed = true;
  cookie.value = "";
});
</script>

<template>
  <form class="connection-form" :aria-busy="busy" @submit.prevent="submit">
    <p class="section-note">{{ t("globusLoginSafety") }}</p>
    <ol class="globus-steps">
      <li>{{ t("globusStepSignIn") }} <a href="https://www.globus.cz/" target="_blank" rel="noopener noreferrer">globus.cz</a></li>
      <li>{{ t("globusStepCopy") }}</li>
    </ol>
    <label :for="`globus-cookie-${connectionId}`">{{ t("globusCookie") }}</label>
    <input :id="`globus-cookie-${connectionId}`" v-model="cookie" type="password" autocomplete="off" maxlength="4200" required :disabled="busy" />
    <p v-if="errorKey" class="form-alert" role="alert">{{ t(errorKey) }}</p>
    <div class="connection-actions">
      <button type="submit" class="button primary" :disabled="busy">{{ t(busy ? "lidlSigningIn" : "globusConnect") }}</button>
      <button type="button" class="button secondary" :disabled="busy" @click="emit('cancel')">{{ t("cancel") }}</button>
    </div>
  </form>
</template>

<style scoped>
.globus-steps { margin: 14px 0 4px; padding-left: 20px; font-size: 13px; line-height: 1.7; }
</style>
