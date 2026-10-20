<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";
import { ApiError, request } from "../api";
import type { Connection } from "../api";

const props = defineProps<{ connectionId: string }>();
const emit = defineEmits<{ connected: [connection: Connection]; cancel: [] }>();
const { t } = useI18n();
const token = ref("");
const busy = ref(false);
const errorKey = ref("");
let disposed = false;

function loginError(error: unknown): string {
  const value = error instanceof ApiError ? error.code : "";
  if (["auth_required", "validation_error", "provider_response_invalid"].includes(value ?? "")) return "foodoraTokensRejected";
  if (value === "connection_busy") return "lidlLoginBusy";
  if (["authorization_rate_limited", "rate_limited", "provider_unavailable"].includes(value ?? "")) return "foodoraProviderBusy";
  if (value === "account_mismatch" || value === "account_identity_unavailable") return "syncIdentityFailed";
  return "foodoraLoginFailed";
}

async function submit(): Promise<void> {
  if (busy.value) return;
  busy.value = true;
  errorKey.value = "";
  // Keep the token out of the mutation/query cache and clear the input before waiting.
  const body = { access_token: token.value.trim() };
  token.value = "";
  try {
    const connection = await request<Connection>(`/providers/foodora/connections/${props.connectionId}/authorize`, {
      method: "POST", body: JSON.stringify(body),
    });
    if (!disposed) emit("connected", connection);
  } catch (error) {
    errorKey.value = loginError(error);
  } finally {
    body.access_token = "";
    busy.value = false;
  }
}

onBeforeUnmount(() => {
  disposed = true;
  token.value = "";
});
</script>

<template>
  <form class="connection-form" :aria-busy="busy" @submit.prevent="submit">
    <p class="section-note">{{ t("foodoraLoginSafety") }}</p>
    <ol class="foodora-steps">
      <li>{{ t("foodoraStepSignIn") }} <a href="https://www.foodora.cz/" target="_blank" rel="noopener noreferrer">foodora.cz</a></li>
      <li>{{ t("foodoraStepCopy") }}</li>
    </ol>
    <label :for="`foodora-token-${connectionId}`">{{ t("foodoraAccessToken") }}</label>
    <input :id="`foodora-token-${connectionId}`" v-model="token" type="password" autocomplete="off" maxlength="8192" required :disabled="busy" />
    <p v-if="errorKey" class="form-alert" role="alert">{{ t(errorKey) }}</p>
    <div class="connection-actions">
      <button type="submit" class="button primary" :disabled="busy">{{ t(busy ? "lidlSigningIn" : "foodoraConnect") }}</button>
      <button type="button" class="button secondary" :disabled="busy" @click="emit('cancel')">{{ t("cancel") }}</button>
    </div>
  </form>
</template>

<style scoped>
.foodora-steps { margin: 14px 0 4px; padding-left: 20px; font-size: 13px; line-height: 1.7; }
</style>
