<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";
import { ApiError, request } from "../api";
import type { Connection, LidlLoginResult } from "../api";

const props = defineProps<{ connectionId: string }>();
const emit = defineEmits<{ connected: [connection: Connection]; cancel: [] }>();
const { t } = useI18n();
const email = ref("");
const password = ref("");
const code = ref("");
const flowId = ref<string | null>(null);
const busy = ref(false);
const errorKey = ref("");
const codeInput = ref<HTMLInputElement | null>(null);
const endpoint = `/providers/lidl/connections/${props.connectionId}/login`;
let disposed = false;

function loginError(error: unknown): string {
  const value = error instanceof ApiError ? error.code : "";
  if (value === "credentials_rejected") return "lidlCredentialsRejected";
  if (value === "otp_rejected") return "lidlCodeRejected";
  if (value === "account_locked") return "lidlAccountLocked";
  if (value === "login_expired") return "lidlLoginExpired";
  if (["login_busy", "device_bootstrap_busy", "connection_busy"].includes(value ?? "")) return "lidlLoginBusy";
  if (["login_rate_limited", "provider_busy", "authorization_rate_limited"].includes(value ?? "")) return "lidlProviderBusy";
  if (["login_request_uncertain", "token_exchange_uncertain"].includes(value ?? "")) return "lidlLoginUncertain";
  if (value === "account_mismatch" || value === "account_identity_unavailable") return "syncIdentityFailed";
  if (value?.startsWith("captcha_") || value?.startsWith("device_")) return "lidlVerificationFailed";
  return "lidlLoginFailed";
}

function discardFlow(identifier: string): void {
  void request<void>(`${endpoint}/${identifier}`, { method: "DELETE" }).catch(() => undefined);
}

async function submit(): Promise<void> {
  if (busy.value) return;
  busy.value = true;
  errorKey.value = "";
  const identifier = flowId.value;
  // Keep secrets out of the mutation/query cache and clear inputs before waiting.
  const body = identifier ? { code: code.value } : { email: email.value, password: password.value };
  password.value = "";
  code.value = "";
  try {
    const result = await request<LidlLoginResult>(identifier ? `${endpoint}/${identifier}/verify` : endpoint, {
      method: "POST", body: JSON.stringify(body),
    });
    if (disposed) {
      if (result.flow_id) discardFlow(result.flow_id);
      return;
    }
    if (result.step === "completed" && result.connection) {
      flowId.value = null;
      emit("connected", result.connection);
    } else if (result.step === "otp" && result.flow_id) {
      flowId.value = result.flow_id;
      await nextTick();
      codeInput.value?.focus();
    } else {
      errorKey.value = "lidlLoginFailed";
    }
  } catch (error) {
    if (identifier) discardFlow(identifier);
    flowId.value = null;
    errorKey.value = loginError(error);
  } finally {
    if ("password" in body) body.password = "";
    if ("code" in body) body.code = "";
    busy.value = false;
  }
}

onBeforeUnmount(() => {
  disposed = true;
  password.value = code.value = "";
  if (flowId.value) discardFlow(flowId.value);
});
</script>

<template>
  <form class="connection-form lidl-login-form" :aria-busy="busy" @submit.prevent="submit">
    <p class="section-note">{{ t("lidlLoginSafety") }}</p>
    <template v-if="!flowId">
      <label :for="`lidl-email-${connectionId}`">{{ t("lidlEmail") }}</label>
      <input :id="`lidl-email-${connectionId}`" v-model="email" type="email" autocomplete="username" maxlength="254" required :disabled="busy" />
      <label :for="`lidl-password-${connectionId}`">{{ t("lidlPassword") }}</label>
      <input :id="`lidl-password-${connectionId}`" v-model="password" type="password" autocomplete="off" maxlength="1024" required :disabled="busy" />
    </template>
    <template v-else>
      <p class="verification-note">{{ t("lidlCodeInstructions") }}</p>
      <label :for="`lidl-code-${connectionId}`">{{ t("lidlVerificationCode") }}</label>
      <input :id="`lidl-code-${connectionId}`" ref="codeInput" v-model="code" inputmode="numeric" autocomplete="one-time-code" maxlength="16" required :disabled="busy" />
    </template>
    <p v-if="errorKey" class="form-alert" role="alert">{{ t(errorKey) }}</p>
    <div class="connection-actions">
      <button type="submit" class="button primary" :disabled="busy">{{ t(busy ? "lidlSigningIn" : flowId ? "lidlVerifyCode" : "lidlSignIn") }}</button>
      <button type="button" class="button secondary" :disabled="busy" @click="emit('cancel')">{{ t("cancel") }}</button>
    </div>
  </form>
</template>

<style scoped>
.verification-note { margin-top: 18px; font-size: 13px; line-height: 1.7; }
</style>
