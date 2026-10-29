<script setup lang="ts">
import { computed, ref } from "vue";
import { useMutation, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { ApiError } from "../api";
import { changeCredentials, isStrongPassword } from "../auth";
import AppDialog from "./AppDialog.vue";
import AppButton from "./AppButton.vue";

const errorKeys: Record<string, string> = {
  invalid_current_password: "invalidCurrentPassword",
  weak_password: "passwordRule",
  password_unchanged: "passwordUnchanged",
  credentials_unchanged: "credentialsUnchanged",
  email_taken: "emailTaken",
  login_rate_limited: "rateLimited",
};

const props = defineProps<{ email: string }>();
const emit = defineEmits<{ close: []; saved: [] }>();

const { t } = useI18n();
const client = useQueryClient();
const email = ref(props.email);
const newPassword = ref("");
const confirmation = ref("");
const currentPassword = ref("");
const problem = ref<"passwordRule" | "passwordsDoNotMatch" | "credentialsUnchanged" | null>(null);
const emailChanged = computed(() => email.value.trim().toLocaleLowerCase() !== props.email);

const change = useMutation({
  mutationFn: () => changeCredentials(client, {
    current_password: currentPassword.value,
    ...(emailChanged.value ? { email: email.value.trim() } : {}),
    ...(newPassword.value ? { new_password: newPassword.value } : {}),
  }),
  onSuccess: () => emit("saved"),
});
const errorKey = computed(() => {
  const error = change.error.value;
  return (error instanceof ApiError && error.code && errorKeys[error.code]) || "credentialsChangeFailed";
});

function submit(): void {
  if (!emailChanged.value && !newPassword.value) problem.value = "credentialsUnchanged";
  else if (newPassword.value && !isStrongPassword(newPassword.value)) problem.value = "passwordRule";
  else if (newPassword.value && newPassword.value !== confirmation.value) problem.value = "passwordsDoNotMatch";
  else problem.value = null;
  if (!problem.value) change.mutate();
}
</script>

<template>
  <AppDialog :title="t('signInDetails')" :busy="change.isPending.value" @close="emit('close')">
    <form :aria-busy="change.isPending.value" @submit.prevent="submit">
      <label for="credentials-email">{{ t("email") }}</label>
      <input id="credentials-email" v-model="email" type="email" name="email" autocomplete="username" required :disabled="change.isPending.value" />
      <label for="credentials-new-password">{{ t("newPassword") }}</label>
      <input id="credentials-new-password" v-model="newPassword" type="password" name="new-password" autocomplete="new-password" maxlength="1024" aria-describedby="credentials-new-password-note" :disabled="change.isPending.value" />
      <p id="credentials-new-password-note" class="field-note">{{ t("newPasswordOptional") }} {{ t("passwordRule") }}</p>
      <template v-if="newPassword">
        <label for="credentials-confirm-password">{{ t("confirmPassword") }}</label>
        <input id="credentials-confirm-password" v-model="confirmation" type="password" name="confirm-password" autocomplete="new-password" required :disabled="change.isPending.value" />
      </template>
      <label for="credentials-current-password">{{ t("currentPassword") }}</label>
      <input id="credentials-current-password" v-model="currentPassword" type="password" name="current-password" autocomplete="current-password" required maxlength="1024" :disabled="change.isPending.value" />
      <p v-if="problem" role="alert" class="form-alert">{{ t(problem) }}</p>
      <p v-else-if="change.isError.value" role="alert" class="form-alert">{{ t(errorKey) }}</p>
      <div class="dialog-actions">
        <AppButton data-action="cancel" type="button" :disabled="change.isPending.value" @click="emit('close')">{{ t("cancel") }}</AppButton>
        <AppButton variant="primary" type="submit" :disabled="change.isPending.value">{{ t(change.isPending.value ? "saving" : "save") }}</AppButton>
      </div>
    </form>
  </AppDialog>
</template>
