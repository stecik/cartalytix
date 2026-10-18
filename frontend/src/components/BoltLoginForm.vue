<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";
import { ApiError, request } from "../api";
import type { Connection } from "../api";

const props = defineProps<{ connectionId: string }>();
const emit = defineEmits<{ connected: [connection: Connection]; cancel: [] }>();
const { t } = useI18n();
const phone = ref("+420");
const code = ref("");
const flowId = ref<string | null>(null);
const busy = ref(false);
const errorKey = ref("");
const codeInput = ref<HTMLInputElement | null>(null);
const endpoint = `/providers/bolt/connections/${props.connectionId}/login`;
let disposed = false;

function loginError(error: unknown): string {
  const value = error instanceof ApiError ? error.code : "";
  if (value === "phone_rejected" || value === "validation_error") return "boltPhoneRejected";
  if (value === "otp_rejected") return "boltCodeRejected";
  if (value === "login_expired") return "boltLoginExpired";
  if (value === "challenge_required") return "boltChallengeRequired";
  if (value === "connection_busy") return "lidlLoginBusy";
  if (["login_rate_limited", "rate_limited", "provider_unavailable"].includes(value ?? "")) return "boltProviderBusy";
  if (value === "account_mismatch" || value === "account_identity_unavailable") return "syncIdentityFailed";
  return "boltLoginFailed";
}

async function submit(): Promise<void> {
  if (busy.value) return;
  busy.value = true;
  errorKey.value = "";
  const identifier = flowId.value;
  // Keep the code out of the mutation/query cache and clear the input before waiting.
  const body = identifier ? { flow_id: identifier, code: code.value } : { phone: phone.value.replace(/\s+/g, "") };
  code.value = "";
  try {
    if (identifier) {
      const connection = await request<Connection>(`${endpoint}/verify`, { method: "POST", body: JSON.stringify(body) });
      if (!disposed) emit("connected", connection);
    } else {
      const result = await request<{ flow_id: string }>(endpoint, { method: "POST", body: JSON.stringify(body) });
      if (disposed) return;
      flowId.value = result.flow_id;
      await nextTick();
      codeInput.value?.focus();
    }
  } catch (error) {
    errorKey.value = loginError(error);
    if (identifier && error instanceof ApiError && error.code === "login_expired") flowId.value = null;
  } finally {
    if ("code" in body) body.code = "";
    busy.value = false;
  }
}

onBeforeUnmount(() => {
  disposed = true;
  code.value = "";
});
</script>

<template>
  <form class="connection-form" :aria-busy="busy" @submit.prevent="submit">
    <p class="section-note">{{ t("boltLoginSafety") }}</p>
    <template v-if="!flowId">
      <label :for="`bolt-phone-${connectionId}`">{{ t("boltPhone") }}</label>
      <input :id="`bolt-phone-${connectionId}`" v-model="phone" type="tel" autocomplete="tel" maxlength="20" required :disabled="busy" />
    </template>
    <template v-else>
      <p class="verification-note">{{ t("boltCodeInstructions") }}</p>
      <label :for="`bolt-code-${connectionId}`">{{ t("lidlVerificationCode") }}</label>
      <input :id="`bolt-code-${connectionId}`" ref="codeInput" v-model="code" inputmode="numeric" autocomplete="one-time-code" maxlength="16" required :disabled="busy" />
    </template>
    <p v-if="errorKey" class="form-alert" role="alert">{{ t(errorKey) }}</p>
    <div class="connection-actions">
      <button type="submit" class="button primary" :disabled="busy">{{ t(busy ? "lidlSigningIn" : flowId ? "lidlVerifyCode" : "boltSendCode") }}</button>
      <button type="button" class="button secondary" :disabled="busy" @click="emit('cancel')">{{ t("cancel") }}</button>
    </div>
  </form>
</template>

<style scoped>
.verification-note { margin-top: 18px; font-size: 13px; line-height: 1.7; }
</style>
