<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { ApiError } from "../api";
import AppDialog from "./AppDialog.vue";
import AppSelect from "./AppSelect.vue";
import AppSwitch from "./AppSwitch.vue";
import AppButton from "./AppButton.vue";

const emit = defineEmits<{ close: [] }>();
const { t } = useI18n();
const hours = ref("24");
const imports = ref(true);
const technical = ref(false);
const busy = ref(false);
const failed = ref(false);
const periods = computed(() => [
  { value: "1", label: t("diagnosticsHour") },
  { value: "24", label: t("diagnosticsDay") },
  { value: "168", label: t("diagnosticsWeek") },
]);

async function download(): Promise<void> {
  if (busy.value || (!imports.value && !technical.value)) return;
  busy.value = true;
  failed.value = false;
  try {
    const query = new URLSearchParams({ hours: hours.value, imports: String(imports.value), technical: String(technical.value) });
    const response = await fetch("/api/v1/diagnostics/download?" + query.toString(), {
      credentials: "same-origin", cache: "no-store", headers: { Accept: "application/zip" },
    });
    if (!response.ok) throw new ApiError(response.status);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1] ?? "cartalytix-logs.zip";
    try {
      document.body.append(anchor);
      anchor.click();
    } finally {
      anchor.remove();
      URL.revokeObjectURL(url);
    }
  } catch {
    failed.value = true;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <AppDialog :title="t('diagnosticsDownload')" :busy="busy" @close="emit('close')">
    <p>{{ t("diagnosticsPrivacy") }}</p>
    <form :aria-busy="busy" @submit.prevent="download">
      <fieldset class="diagnostics-options" :disabled="busy">
      <legend>{{ t("diagnosticsPeriod") }}</legend>
      <AppSelect v-model="hours" :options="periods" :label="t('diagnosticsPeriod')" />
      <div class="diagnostics-switches">
        <AppSwitch v-model="imports" :label="t('diagnosticsImports')" :disabled="busy">{{ t("diagnosticsImports") }}</AppSwitch>
        <AppSwitch v-model="technical" :label="t('diagnosticsTechnical')" :disabled="busy">{{ t("diagnosticsTechnical") }}</AppSwitch>
      </div>
      </fieldset>
      <p class="field-note">{{ t("diagnosticsTechnicalHistory") }}</p>
      <p v-if="failed" role="alert" class="form-alert">{{ t("diagnosticsFailed") }}</p>
      <p v-if="busy" role="status" class="field-note">{{ t("diagnosticsPreparing") }}</p>
      <div class="dialog-actions">
        <AppButton data-action="cancel" type="button" :disabled="busy" @click="emit('close')">{{ t("cancel") }}</AppButton>
        <AppButton variant="primary" type="submit" :disabled="busy || (!imports && !technical)">{{ t(busy ? "diagnosticsPreparing" : "diagnosticsDownload") }}</AppButton>
      </div>
    </form>
  </AppDialog>
</template>

<style scoped>
.diagnostics-options { border: 0; margin: 0; padding: 0; min-width: 0; }
.diagnostics-options legend { margin-bottom: 8px; }
.diagnostics-switches { display: grid; gap: 16px; margin-block: 20px; }
.diagnostics-switches :deep(input[type=checkbox]) { width: 34px; height: 20px; margin: 0; }
</style>
