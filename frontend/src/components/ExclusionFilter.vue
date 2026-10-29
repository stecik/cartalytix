<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import AppTabs from "./AppTabs.vue";

defineProps<{ modelValue: string }>();
const emit = defineEmits<{ "update:modelValue": [value: string] }>();
const { t } = useI18n();
const options = computed(() => [
  { value: "included", label: t("included") },
  { value: "excluded", label: t("excluded") },
  { value: "all", label: t("allRecords") },
]);
</script>

<template>
  <AppTabs class="exclusion-filter" :model-value="modelValue" :options="options" :label="t('overviewInclusion')" @update:model-value="emit('update:modelValue', $event)" />
</template>
