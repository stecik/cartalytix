<script setup lang="ts">
import { computed, ref } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { ApiError, request } from "../api";
import type { AdminUser, AdminUserPassword } from "../api";
import AppDialog from "../components/AppDialog.vue";
import AppIcon from "../components/AppIcon.vue";
import QueryState from "../components/QueryState.vue";
import AppButton from "../components/AppButton.vue";
import PageHeader from "../components/PageHeader.vue";

type Confirmation = { action: "reset" | "delete"; user: AdminUser };

const { t, locale } = useI18n();
const client = useQueryClient();
const queryKey = ["admin-users"];
const users = useQuery({
  queryKey,
  queryFn: ({ signal }) => request<{ items: AdminUser[] }>("/admin/users", { signal }),
});
const email = ref("");
const issued = ref<AdminUserPassword | null>(null);
const copied = ref(false);
const confirming = ref<Confirmation | null>(null);
const refresh = () => client.invalidateQueries({ queryKey });

function showPassword(result: AdminUserPassword): void {
  copied.value = false;
  issued.value = result;
}
const create = useMutation({
  mutationFn: () => request<AdminUserPassword>("/admin/users", {
    method: "POST",
    body: JSON.stringify({ email: email.value.trim() }),
  }),
  onSuccess: async (result) => {
    email.value = "";
    showPassword(result);
    await refresh();
  },
});
const createError = computed(() =>
  create.error.value instanceof ApiError && create.error.value.code === "email_taken" ? "emailTaken" : "accountCreateFailed");
const confirm = useMutation({
  mutationFn: async ({ action, user }: Confirmation) => {
    const path = `/admin/users/${encodeURIComponent(user.id)}`;
    if (action === "delete") return void await request<void>(path, { method: "DELETE" });
    showPassword(await request<AdminUserPassword>(`${path}/reset-password`, { method: "POST" }));
  },
  onSuccess: async () => {
    confirming.value = null;
    await refresh();
  },
});
const toggle = useMutation({
  mutationFn: (user: AdminUser) => request<AdminUser>(`/admin/users/${encodeURIComponent(user.id)}`, {
    method: "PATCH",
    body: JSON.stringify({ is_disabled: !user.is_disabled }),
  }),
  onSettled: refresh,
});

function createdOn(value: string): string {
  return new Intl.DateTimeFormat(locale.value === "cs" ? "cs-CZ" : "en-GB", { dateStyle: "medium" }).format(new Date(value));
}
function status(user: AdminUser): string {
  if (user.is_admin) return "accountAdministrator";
  if (user.is_disabled) return "accountDisabled";
  return user.must_change_password ? "accountPendingPassword" : "accountActive";
}
async function copyPassword(): Promise<void> {
  if (!issued.value) return;
  try {
    await navigator.clipboard.writeText(issued.value.password);
    copied.value = true;
  } catch {
    copied.value = false;
  }
}
</script>

<template>
  <div class="view-content accounts-workspace">
    <PageHeader :eyebrow="t('administration')" :title="t('accounts')" :intro="t('accountsIntro')" />
    <form class="account-create" :aria-busy="create.isPending.value" @submit.prevent="create.mutate()">
      <label for="account-email">{{ t("email") }}</label>
      <div class="account-create-row">
        <input id="account-email" v-model="email" type="email" name="email" autocomplete="off" required :disabled="create.isPending.value" />
        <AppButton type="submit" variant="primary" :disabled="create.isPending.value">
          <AppIcon name="plus" />{{ t(create.isPending.value ? "creatingAccount" : "createAccount") }}
        </AppButton>
      </div>
      <p class="field-note">{{ t("createAccountHint") }}</p>
      <p v-if="create.isError.value" role="alert" class="form-alert">{{ t(createError) }}</p>
    </form>
    <p v-if="toggle.isError.value" role="alert" class="form-alert">{{ t("accountUpdateFailed") }}</p>
    <QueryState v-if="!users.data.value" :pending="users.isPending.value" :failed="users.isError.value" @retry="users.refetch()" />
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">{{ t("email") }}</th>
            <th scope="col">{{ t("accountStatus") }}</th>
            <th scope="col">{{ t("accountCreated") }}</th>
            <th scope="col"><span class="sr-only">{{ t("actions") }}</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="user in users.data.value.items" :key="user.id">
            <td class="account-email-cell">{{ user.email }}</td>
            <td>{{ t(status(user)) }}</td>
            <td>{{ createdOn(user.created_at) }}</td>
            <td>
              <div v-if="!user.is_admin" class="account-actions">
                <AppButton type="button" variant="text" @click="confirming = { action: 'reset', user }">{{ t("resetPassword") }}</AppButton>
                <AppButton type="button" variant="text" :disabled="toggle.isPending.value" @click="toggle.mutate(user)">{{ t(user.is_disabled ? "enableAccount" : "disableAccount") }}</AppButton>
                <AppButton type="button" variant="text" @click="confirming = { action: 'delete', user }">
                  <AppIcon name="trash" />{{ t("deleteAccount") }}
                </AppButton>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <AppDialog v-if="confirming" :title="t(confirming.action === 'delete' ? 'deleteAccountTitle' : 'resetPasswordTitle')" :busy="confirm.isPending.value" @close="confirming = null">
      <p>{{ t(confirming.action === "delete" ? "deleteAccountHelp" : "resetPasswordHelp", { email: confirming.user.email }) }}</p>
      <p v-if="confirm.isError.value" class="form-alert" role="alert">{{ t("accountUpdateFailed") }}</p>
      <div class="dialog-actions">
        <AppButton data-action="cancel" type="button" :disabled="confirm.isPending.value" @click="confirming = null">{{ t("cancel") }}</AppButton>
        <AppButton :variant="confirming.action === 'delete' ? 'danger' : 'primary'" type="button" :disabled="confirm.isPending.value" @click="confirming && confirm.mutate(confirming)">
          {{ t(confirming.action === "delete" ? "deleteAccount" : "resetPassword") }}
        </AppButton>
      </div>
    </AppDialog>
    <AppDialog v-if="issued" :title="t('generatedPasswordTitle')" @close="issued = null">
      <p>{{ t("generatedPasswordHelp", { email: issued.user.email }) }}</p>
      <code class="generated-password">{{ issued.password }}</code>
      <p v-if="copied" role="status">{{ t("passwordCopied") }}</p>
      <div class="dialog-actions">
        <AppButton type="button" @click="copyPassword">{{ t("copyPassword") }}</AppButton>
        <AppButton variant="primary" data-action="cancel" type="button" @click="issued = null">{{ t("done") }}</AppButton>
      </div>
    </AppDialog>
  </div>
</template>

<style scoped>
.accounts-workspace { max-width: 1040px; }
.account-create { max-width: 560px; margin: 0 0 36px; }
.account-create label { margin-top: 0; }
.account-create-row { display: flex; gap: 10px; }
.account-create-row input { flex: 1; min-width: 0; }
.account-create-row .button { flex-shrink: 0; }
.account-create-row .button svg { width: 16px; height: 16px; }
.account-email-cell { overflow-wrap: anywhere; white-space: normal; }
.account-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 4px 14px; }
.account-actions svg { width: 14px; height: 14px; }
.generated-password { display: block; padding: 12px 14px; border: 1px solid var(--border); border-radius: 6px; font-size: 15px; overflow-wrap: anywhere; user-select: all; }
@media (max-width: 600px) { .account-create-row { flex-direction: column; } .account-actions { justify-content: flex-start; } }
</style>
