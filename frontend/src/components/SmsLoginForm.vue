<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from "vue";
import { useI18n } from "vue-i18n";
import { ApiError, request } from "../api";
import type { Connection } from "../api";
import AppButton from "./AppButton.vue";

const props = defineProps<{ connectionId: string; provider: "bolt" | "albert" }>();
const emit = defineEmits<{ connected: [connection: Connection]; cancel: [] }>();
const { t } = useI18n();
const phone = ref("+420");
const code = ref("");
const flowId = ref<string | null>(null);
const busy = ref(false);
const errorKey = ref("");
const codeInput = ref<HTMLInputElement | null>(null);
const isAlbert = computed(() => props.provider === "albert");
const albertCodePattern = "[0-9]{6}";
let disposed = false;

function loginError(error: unknown): string {
  const value = error instanceof ApiError ? error.code : "";
  if (value === "phone_rejected" || value === "validation_error") return isAlbert.value ? "albertPhoneRejected" : "boltPhoneRejected";
  if (value === "otp_rejected") return isAlbert.value ? "albertCodeRejected" : "boltCodeRejected";
  if (value === "login_expired") return "smsLoginExpired";
  if (value === "challenge_required" && !isAlbert.value) return "boltChallengeRequired";
  if (value === "connection_busy") return "lidlLoginBusy";
  if (value === "credentials_unavailable" && isAlbert.value) return "albertCredentialsUnavailable";
  if (["login_rate_limited", "rate_limited", "provider_unavailable"].includes(value ?? "")) return isAlbert.value ? "albertProviderBusy" : "boltProviderBusy";
  if (value === "account_mismatch" || value === "account_identity_unavailable") return "syncIdentityFailed";
  return isAlbert.value ? "albertLoginFailed" : "boltLoginFailed";
}

async function submit(): Promise<void> {
  if (busy.value) return;
  const identifier = flowId.value;
  if (identifier && isAlbert.value && !/^[0-9]{6}$/.test(code.value)) return;

  const endpoint = `/providers/${props.provider}/connections/${props.connectionId}/login`;
  busy.value = true;
  errorKey.value = "";
  // Keep the code out of the mutation/query cache and clear the input before waiting.
  const body = identifier ? { flow_id: identifier, code: code.value } : { phone: phone.value.replace(/\s+/g, "") };
  code.value = "";
  try {
    if (identifier) {
      const connection = await request<Connection>(`${endpoint}/verify`, { method: "POST", body: JSON.stringify(body) });
      if (!disposed) {
        flowId.value = null;
        emit("connected", connection);
      }
    } else {
      const result = await request<{ flow_id: string }>(endpoint, { method: "POST", body: JSON.stringify(body) });
      if (disposed) return;
      flowId.value = result.flow_id;
      await nextTick();
      codeInput.value?.focus();
    }
  } catch (error) {
    if (disposed) return;
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
  flowId.value = null;
});
</script>

<template>
  <form class="connection-form" :aria-busy="busy" @submit.prevent="submit">
    <p class="section-note">{{ t(isAlbert ? "albertLoginSafety" : "boltLoginSafety") }}</p>
    <template v-if="!flowId">
      <label :for="`${provider}-phone-${connectionId}`">{{ t("smsPhone") }}</label>
      <input :id="`${provider}-phone-${connectionId}`" v-model="phone" type="tel" autocomplete="tel" maxlength="20" required :disabled="busy" />
    </template>
    <template v-else>
      <p class="verification-note">{{ t(isAlbert ? "albertCodeInstructions" : "boltCodeInstructions") }}</p>
      <label :for="`${provider}-code-${connectionId}`">{{ t("lidlVerificationCode") }}</label>
      <input :id="`${provider}-code-${connectionId}`" ref="codeInput" v-model="code" inputmode="numeric" autocomplete="one-time-code"
        :maxlength="isAlbert ? 6 : 16" :pattern="isAlbert ? albertCodePattern : undefined" required :disabled="busy"
      />
    </template>
    <p v-if="errorKey" class="form-alert" role="alert">{{ t(errorKey) }}</p>
    <div class="connection-actions">
      <AppButton type="submit" variant="primary" :disabled="busy">{{ t(busy ? "lidlSigningIn" : flowId ? "lidlVerifyCode" : "smsSendCode") }}</AppButton>
      <AppButton type="button" :disabled="busy" @click="emit('cancel')">{{ t("cancel") }}</AppButton>
    </div>
  </form>
</template>

<style scoped>
.verification-note { margin-top: 18px; font-size: 13px; line-height: 1.7; }
</style>
