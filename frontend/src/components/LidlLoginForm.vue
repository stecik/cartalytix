<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";
import { ApiError, request } from "../api";
import type { Connection, LidlLoginResult } from "../api";

const props = withDefaults(defineProps<{ connectionId: string; provider?: "lidl" | "kaufland" }>(), { provider: "lidl" });
const emit = defineEmits<{ connected: [connection: Connection]; cancel: [] }>();
const { t, te } = useI18n();
const email = ref("");
const password = ref("");
const code = ref("");
const flowId = ref<string | null>(null);
const busy = ref(false);
const errorKey = ref("");
const codeInput = ref<HTMLInputElement | null>(null);
const endpoint = `/providers/${props.provider}/connections/${props.connectionId}/login`;
// Provider-specific wording where it exists; shared steps reuse the Lidl texts.
const key = (name: string): string => te(`${props.provider}${name}`) ? `${props.provider}${name}` : `lidl${name}`;
let disposed = false;

function loginError(error: unknown): string {
  const value = error instanceof ApiError ? error.code : "";
  if (value === "credentials_rejected") return key("CredentialsRejected");
  if (value === "otp_rejected") return key("CodeRejected");
  if (value === "account_locked") return key("AccountLocked");
  if (value === "login_expired") return key("LoginExpired");
  if (["login_busy", "device_bootstrap_busy", "connection_busy"].includes(value ?? "")) return key("LoginBusy");
  if (["login_rate_limited", "provider_busy", "authorization_rate_limited"].includes(value ?? "")) return key("ProviderBusy");
  if (["login_request_uncertain", "token_exchange_uncertain"].includes(value ?? "")) return key("LoginUncertain");
  if (value === "account_mismatch" || value === "account_identity_unavailable") return "syncIdentityFailed";
  if (value?.startsWith("captcha_") || value?.startsWith("device_")) return key("VerificationFailed");
  return key("LoginFailed");
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
      errorKey.value = key("LoginFailed");
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
    <p class="section-note">{{ t(key("LoginSafety")) }}</p>
    <template v-if="!flowId">
      <label :for="`${provider}-email-${connectionId}`">{{ t(key("Email")) }}</label>
      <input :id="`${provider}-email-${connectionId}`" v-model="email" type="email" autocomplete="username" maxlength="254" required :disabled="busy" />
      <label :for="`${provider}-password-${connectionId}`">{{ t(key("Password")) }}</label>
      <input :id="`${provider}-password-${connectionId}`" v-model="password" type="password" autocomplete="off" maxlength="1024" required :disabled="busy" />
    </template>
    <template v-else>
      <p class="verification-note">{{ t(key("CodeInstructions")) }}</p>
      <label :for="`${provider}-code-${connectionId}`">{{ t(key("VerificationCode")) }}</label>
      <input :id="`${provider}-code-${connectionId}`" ref="codeInput" v-model="code" inputmode="numeric" autocomplete="one-time-code" maxlength="16" required :disabled="busy" />
    </template>
    <p v-if="errorKey" class="form-alert" role="alert">{{ t(errorKey) }}</p>
    <div class="connection-actions">
      <button type="submit" class="button primary" :disabled="busy">{{ t(key(busy ? "SigningIn" : flowId ? "VerifyCode" : "SignIn")) }}</button>
      <button type="button" class="button secondary" :disabled="busy" @click="emit('cancel')">{{ t("cancel") }}</button>
    </div>
  </form>
</template>

<style scoped>
.verification-note { margin-top: 18px; font-size: 13px; line-height: 1.7; }
</style>
