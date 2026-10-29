<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import SegmentedToggle from "./SegmentedToggle.vue";

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
  <SegmentedToggle class="exclusion-filter" :model-value="modelValue" :options="options" :label="t('overviewInclusion')" @update:model-value="emit('update:modelValue', $event)" />
</template>

<style scoped>
.exclusion-filter { margin: 0; padding: 0; border: 0; border-radius: 0; background: transparent; }
.exclusion-filter :deep(label) { padding: 12px 16px; border-radius: 0; border-bottom: 2px solid transparent; }
.exclusion-filter :deep(label:first-of-type) { padding-left: 0; }
.exclusion-filter :deep(label.selected) { background: transparent; box-shadow: none; border-bottom-color: var(--green); }
</style>
