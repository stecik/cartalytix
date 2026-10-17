<script setup lang="ts">
import { computed } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import type { User } from "../api";
import { sessionOptions } from "../auth";
import { useUserSettings } from "../settings";
import AppSelect from "../components/AppSelect.vue";
import SegmentedToggle from "../components/SegmentedToggle.vue";

const { t, locale } = useI18n();
const session = useQuery(sessionOptions);
const settings = useUserSettings();
const receiptView = computed({
  get: (): User["receipt_view"] => session.data.value?.receipt_view ?? "rendered",
  set: (value: User["receipt_view"]) => settings.mutate({ receipt_view: value }),
});
const language = computed({
  get: () => locale.value,
  set: (value: string) => settings.mutate({ locale: value }),
});
const languages = computed(() => [
  { value: "en", label: t("english") },
  { value: "cs", label: t("czech") },
]);
const views = computed(() => [
  { value: "rendered" as const, label: t("receiptViewRendered") },
  { value: "detailed" as const, label: t("receiptViewDetailed") },
]);
</script>

<template>
  <div class="view-content">
    <header class="page-heading">
      <div class="eyebrow">{{ t("workspace") }}</div>
      <h1>{{ t("settings") }}</h1>
      <p>{{ t("settingsIntro") }}</p>
    </header>
    <p v-if="settings.isError.value" role="alert" class="form-alert">{{ t("settingsSaveFailed") }}</p>
    <section class="settings-section">
      <fieldset class="settings-group">
        <legend>{{ t("receiptDisplay") }}</legend>
        <SegmentedToggle v-model="receiptView" :options="views" :label="t('receiptDisplay')" />
        <p class="settings-hint">{{ t(receiptView === "rendered" ? "receiptViewRenderedHint" : "receiptViewDetailedHint") }}</p>
      </fieldset>
      <div class="settings-group">
        <span class="settings-legend">{{ t("language") }}</span>
        <AppSelect v-model="language" class="settings-select" :options="languages" :label="t('language')" />
      </div>
    </section>
  </div>
</template>

<style scoped>
.settings-section { border-top: 1px solid var(--line); max-width: 640px; }
.settings-group { display: block; border: 0; margin: 0; padding: 28px 0; border-bottom: 1px solid var(--line); }
.settings-group legend,
.settings-legend { display: block; float: left; width: 100%; font-size: 13px; font-weight: 650; margin-bottom: 16px; }
.settings-group .view-toggle { clear: both; margin: 0; }
.settings-hint { margin-top: 10px; font-size: 12px; color: var(--muted); }
.settings-select { clear: both; display: block; width: 220px; }
@media (max-width: 780px) { .settings-select { width: 100%; } }
</style>
