<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";
import { ApiError, request } from "../api";
import type { Connection } from "../api";
import AppButton from "./AppButton.vue";

const props = defineProps<{ connectionId: string }>();
const emit = defineEmits<{ connected: [connection: Connection]; cancel: [] }>();
const { t } = useI18n();
const link = ref("");
const busy = ref(false);
const errorKey = ref("");
let disposed = false;

function loginError(error: unknown): string {
  const value = error instanceof ApiError ? error.code : "";
  if (value === "magic_link_invalid" || value === "validation_error") return "woltLinkInvalid";
  if (value === "magic_link_rejected") return "woltLinkRejected";
  if (value === "access_confirmation_required") return "woltConfirmationRequired";
  if (value === "connection_busy") return "lidlLoginBusy";
  if (["login_rate_limited", "rate_limited", "provider_unavailable"].includes(value ?? "")) return "woltProviderBusy";
  if (value === "account_mismatch" || value === "account_identity_unavailable") return "syncIdentityFailed";
  return "woltLoginFailed";
}

async function submit(): Promise<void> {
  if (busy.value) return;
  busy.value = true;
  errorKey.value = "";
  // Keep the one-time link out of the mutation/query cache and clear the input before waiting.
  const body = { link: link.value.trim() };
  link.value = "";
  try {
    const connection = await request<Connection>(`/providers/wolt/connections/${props.connectionId}/login`, {
      method: "POST", body: JSON.stringify(body),
    });
    if (!disposed) emit("connected", connection);
  } catch (error) {
    errorKey.value = loginError(error);
  } finally {
    body.link = "";
    busy.value = false;
  }
}

onBeforeUnmount(() => {
  disposed = true;
  link.value = "";
});
</script>

<template>
  <form class="connection-form" :aria-busy="busy" @submit.prevent="submit">
    <p class="section-note">{{ t("woltLoginSafety") }}</p>
    <ol class="wolt-steps">
      <li>{{ t("woltStepRequest") }} <a href="https://wolt.com/cs/cze" target="_blank" rel="noopener noreferrer">wolt.com</a></li>
      <li>{{ t("woltStepCopy") }}</li>
    </ol>
    <label :for="`wolt-link-${connectionId}`">{{ t("woltLink") }}</label>
    <input :id="`wolt-link-${connectionId}`" v-model="link" type="url" autocomplete="off" maxlength="2048" required :disabled="busy" />
    <p v-if="errorKey" class="form-alert" role="alert">{{ t(errorKey) }}</p>
    <div class="connection-actions">
      <AppButton type="submit" variant="primary" :disabled="busy">{{ t(busy ? "lidlSigningIn" : "woltConnect") }}</AppButton>
      <AppButton type="button" :disabled="busy" @click="emit('cancel')">{{ t("cancel") }}</AppButton>
    </div>
  </form>
</template>

<style scoped>
.wolt-steps { margin: 14px 0 4px; padding-left: 20px; font-size: 13px; line-height: 1.7; }
</style>
