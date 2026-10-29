<script setup lang="ts">
import { computed, ref } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { ApiError } from "../api";
import { changeCredentials, isStrongPassword, sessionOptions } from "../auth";
import AppIcon from "../components/AppIcon.vue";
import AppButton from "../components/AppButton.vue";
import PageHeader from "../components/PageHeader.vue";

const errorKeys: Record<string, string> = {
  weak_password: "passwordRule",
  password_unchanged: "passwordUnchanged",
  email_change_required: "emailChangeRequired",
  email_taken: "emailTaken",
  login_rate_limited: "rateLimited",
};

const { t } = useI18n();
const client = useQueryClient();
const router = useRouter();
const session = useQuery(sessionOptions);
// Only the first-start administrator has to replace its well-known e-mail too.
const requireEmail = computed(() => session.data.value?.is_admin === true);
const email = ref("");
const newPassword = ref("");
const confirmation = ref("");
const problem = ref<"passwordRule" | "passwordsDoNotMatch" | null>(null);

const change = useMutation({
  mutationFn: () => changeCredentials(client, {
    new_password: newPassword.value,
    ...(requireEmail.value ? { email: email.value.trim() } : {}),
  }),
  onSuccess: async () => {
    newPassword.value = confirmation.value = "";
    await router.replace("/");
  },
});
const errorKey = computed(() => {
  const error = change.error.value;
  return (error instanceof ApiError && error.code && errorKeys[error.code]) || "credentialsChangeFailed";
});

function submit(): void {
  if (!isStrongPassword(newPassword.value)) problem.value = "passwordRule";
  else if (newPassword.value !== confirmation.value) problem.value = "passwordsDoNotMatch";
  else problem.value = null;
  if (!problem.value) change.mutate();
}
</script>

<template>
  <div class="view-content credentials-workspace">
    <PageHeader :eyebrow="t('accountSecurity')" :title="t(requireEmail ? 'changeCredentialsTitle' : 'changePasswordTitle')" :intro="t(requireEmail ? 'changeCredentialsIntro' : 'changePasswordIntro')" />
    <form :aria-busy="change.isPending.value" @submit.prevent="submit">
      <template v-if="requireEmail">
        <label for="new-email">{{ t("newEmail") }}</label>
        <input id="new-email" v-model="email" type="email" name="email" autocomplete="username" required :disabled="change.isPending.value" />
      </template>
      <label for="new-password">{{ t("newPassword") }}</label>
      <input id="new-password" v-model="newPassword" type="password" name="new-password" autocomplete="new-password" required maxlength="1024" aria-describedby="new-password-note" :disabled="change.isPending.value" />
      <p id="new-password-note" class="field-note">{{ t("passwordRule") }}</p>
      <label for="confirm-password">{{ t("confirmPassword") }}</label>
      <input id="confirm-password" v-model="confirmation" type="password" name="confirm-password" autocomplete="new-password" required :disabled="change.isPending.value" />
      <p v-if="problem" role="alert" class="form-alert">{{ t(problem) }}</p>
      <p v-else-if="change.isError.value" role="alert" class="form-alert">{{ t(errorKey) }}</p>
      <AppButton type="submit" variant="primary" class="login-submit" :disabled="change.isPending.value">
        {{ t(change.isPending.value ? "saving" : "saveAndContinue") }}<AppIcon name="arrow" />
      </AppButton>
    </form>
  </div>
</template>

<style scoped>
.credentials-workspace { max-width: 460px; }
.credentials-workspace form { margin-top: 0; }
.credentials-workspace form label:first-child { margin-top: 0; }
</style>
