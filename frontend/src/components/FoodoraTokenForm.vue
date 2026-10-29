<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";
import { ApiError, request } from "../api";
import type { Connection } from "../api";
import AppSelect from "./AppSelect.vue";
import AppButton from "./AppButton.vue";

const props = defineProps<{ connectionId: string }>();
const emit = defineEmits<{ connected: [connection: Connection]; cancel: [] }>();
const { t } = useI18n();
type VerificationMethod = "EMAIL" | "SMS" | "CALL";
interface LoginResult {
  step: "method" | "otp" | "completed";
  flow_id?: string | null;
  methods: VerificationMethod[];
  method?: VerificationMethod | null;
  connection?: Connection | null;
}

const email = ref("");
const password = ref("");
const code = ref("");
const flowId = ref<string | null>(null);
const step = ref<"credentials" | "method" | "otp">("credentials");
const methods = ref<VerificationMethod[]>([]);
const method = ref<VerificationMethod>("EMAIL");
const methodOptions = computed(() => methods.value.map((value) => ({ value, label: t(`foodoraMethod${value}`) })));
const busy = ref(false);
const errorKey = ref("");
const codeInput = ref<HTMLInputElement | null>(null);
const endpoint = `/providers/foodora/connections/${props.connectionId}/login`;
let disposed = false;

function loginError(error: unknown): string {
  const value = error instanceof ApiError ? error.code : "";
  if (value === "credentials_rejected") return "foodoraCredentialsRejected";
  if (value === "account_not_found" || value === "password_login_unavailable") return "foodoraPasswordUnavailable";
  if (value === "otp_rejected") return "foodoraCodeRejected";
  if (value === "login_expired") return "foodoraLoginExpired";
  if (value === "challenge_required") return "foodoraVerificationRequired";
  if (value === "login_busy" || value === "connection_busy") return "lidlLoginBusy";
  if (["login_rate_limited", "rate_limited", "provider_unavailable", "login_unavailable", "network_error", "service_unavailable"].includes(value ?? "")) return "foodoraProviderBusy";
  if (value === "account_mismatch" || value === "account_identity_unavailable") return "syncIdentityFailed";
  return "foodoraLoginFailed";
}

function discardFlow(identifier: string): void {
  void request<void>(`${endpoint}/${identifier}`, { method: "DELETE" }).catch(() => undefined);
}

function resetFlow(): void {
  if (flowId.value) discardFlow(flowId.value);
  flowId.value = null;
  step.value = "credentials";
  methods.value = [];
  code.value = "";
}

function close(): void {
  disposed = true;
  password.value = "";
  resetFlow();
}

function cancel(): void {
  close();
  emit("cancel");
}

async function submit(): Promise<void> {
  if (busy.value || disposed) return;
  if (step.value === "otp" && !/^\d{4,10}$/.test(code.value)) {
    code.value = "";
    errorKey.value = "foodoraCodeRejected";
    return;
  }
  busy.value = true;
  errorKey.value = "";
  const identifier = flowId.value;
  const action = step.value;
  // Keep secrets out of query caches and clear inputs before waiting.
  const body = action === "credentials"
    ? { email: email.value.trim(), password: password.value }
    : action === "method" ? { method: method.value } : { code: code.value };
  password.value = code.value = "";
  try {
    const path = action === "credentials" ? endpoint : `${endpoint}/${identifier}/${action === "method" ? "send" : "verify"}`;
    const result = await request<LoginResult>(path, {
      method: "POST", body: JSON.stringify(body),
    });
    if (disposed) {
      if (result.flow_id) discardFlow(result.flow_id);
      return;
    }
    if (result.step === "completed" && result.connection) {
      flowId.value = null;
      emit("connected", result.connection);
    } else if (result.step === "method" && result.flow_id && result.methods.length) {
      flowId.value = result.flow_id;
      methods.value = result.methods;
      method.value = result.methods.includes("EMAIL") ? "EMAIL" : result.methods[0]!;
      step.value = "method";
    } else if (result.step === "otp" && result.flow_id) {
      flowId.value = result.flow_id;
      step.value = "otp";
      await nextTick();
      codeInput.value?.focus();
    } else {
      if (result.flow_id && result.flow_id !== identifier) discardFlow(result.flow_id);
      resetFlow();
      errorKey.value = "foodoraLoginFailed";
    }
  } catch (error) {
    if (disposed) return;
    const value = error instanceof ApiError ? error.code : "";
    if (!["otp_rejected", "login_busy", "connection_busy", "login_rate_limited"].includes(value ?? "")) resetFlow();
    errorKey.value = loginError(error);
  } finally {
    if ("password" in body) body.password = "";
    if ("code" in body) body.code = "";
    busy.value = false;
  }
}

onBeforeUnmount(close);
</script>

<template>
  <form class="connection-form" :aria-busy="busy" @submit.prevent="submit">
    <p class="section-note">{{ t("foodoraLoginSafety") }}</p>
    <template v-if="step === 'credentials'">
      <label :for="`foodora-email-${connectionId}`">{{ t("foodoraEmail") }}</label>
      <input :id="`foodora-email-${connectionId}`" v-model="email" type="email" autocomplete="username" maxlength="254" required :disabled="busy" />
      <label :for="`foodora-password-${connectionId}`">{{ t("foodoraPassword") }}</label>
      <input :id="`foodora-password-${connectionId}`" v-model="password" type="password" autocomplete="off" maxlength="1024" required :disabled="busy" />
    </template>
    <template v-else-if="step === 'method'">
      <label>{{ t("foodoraVerificationMethod") }}</label>
      <AppSelect v-model="method" class="method-select" :options="methodOptions" :label="t('foodoraVerificationMethod')" :disabled="busy" />
    </template>
    <template v-else>
      <p class="verification-note">{{ t("foodoraCodeInstructions") }}</p>
      <label :for="`foodora-code-${connectionId}`">{{ t("foodoraVerificationCode") }}</label>
      <input :id="`foodora-code-${connectionId}`" ref="codeInput" v-model="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{4,10}" minlength="4" maxlength="10" required :disabled="busy" />
    </template>
    <p v-if="errorKey" class="form-alert" role="alert">{{ t(errorKey) }}</p>
    <div class="connection-actions">
      <AppButton type="submit" variant="primary" :disabled="busy">{{ t(busy ? "lidlSigningIn" : step === "method" ? "foodoraSendCode" : step === "otp" ? "foodoraVerifyCode" : "foodoraConnect") }}</AppButton>
      <AppButton type="button" @click="cancel">{{ t("cancel") }}</AppButton>
    </div>
  </form>
</template>

<style scoped>
.verification-note { margin-top: 18px; font-size: 13px; line-height: 1.7; }
.method-select { display: block; width: 100%; }
</style>
