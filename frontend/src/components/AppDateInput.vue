<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { VueDatePicker } from "@vuepic/vue-datepicker";
import "@vuepic/vue-datepicker/dist/main.css";
import { cs, enGB } from "date-fns/locale";

/** Calendar date field; the value is an ISO date ("2026-10-18") or "" when empty. */
const props = withDefaults(
  defineProps<{ modelValue: string; min?: string; max?: string; required?: boolean }>(),
  { min: "", max: "", required: false },
);
const emit = defineEmits<{ "update:modelValue": [value: string] }>();
const { t, locale } = useI18n();

// A pattern, not a function: the picker would also apply a formatter function to the model value.
const formats = computed(() => ({ input: locale.value === "cs" ? "dd.MM.yyyy" : "dd/MM/yyyy" }));

function update(value: unknown): void {
  const next = typeof value === "string" ? value : "";
  if (next !== props.modelValue) emit("update:modelValue", next);
}
</script>

<template>
  <VueDatePicker
    class="app-date"
    :model-value="modelValue || null"
    model-type="yyyy-MM-dd"
    :min-date="min || undefined"
    :max-date="max || undefined"
    :locale="locale === 'cs' ? cs : enGB"
    :week-start="1"
    :formats="formats"
    :time-config="{ enableTimePicker: false }"
    :input-attrs="{ clearable: !required, required }"
    :action-row="{ showNow: true, showSelect: false, showCancel: false, showPreview: false, nowBtnLabel: t('today') }"
    :config="{ keepActionRow: true }"
    :aria-labels="{ clearInput: t('clearDate') }"
    auto-apply
    teleport
    @update:model-value="update"
  />
</template>

<style>
/* The picker menu is teleported to <body>, so its theme cannot be scoped. */
.dp--theme-light,
.dp--theme-dark {
  --dp-font-family: "DM Sans Variable", sans-serif;
  --dp-font-size: 13px;
  --dp-border-radius: 4px;
  --dp-cell-border-radius: 6px;
  --dp-input-padding: 0 30px 0 12px;
  --dp-background-color: var(--surface-input);
  --dp-text-color: var(--text);
  --dp-hover-color: var(--surface-toggle);
  --dp-hover-text-color: var(--text);
  --dp-hover-icon-color: var(--green);
  --dp-primary-color: var(--green);
  --dp-primary-disabled-color: var(--muted);
  --dp-primary-text-color: var(--text-primary-button);
  --dp-secondary-color: var(--muted);
  --dp-border-color: var(--border-input);
  --dp-menu-border-color: var(--border-select-list);
  --dp-border-color-hover: var(--border-select-hover);
  --dp-border-color-focus: var(--border-input-focus);
  --dp-disabled-color: var(--surface-toggle);
  --dp-disabled-color-text: var(--muted);
  --dp-icon-color: var(--muted);
  --dp-danger-color: var(--danger);
  --dp-highlight-color: var(--ring-input-focus);
}
.app-date .dp--input {
  height: 40px;
  color: var(--green);
}
.app-date .dp--input-focus {
  box-shadow: 0 0 0 3px var(--ring-input-focus);
}
.dp--menu {
  border-radius: 10px;
  box-shadow: 0 12px 32px var(--shadow-select);
}
</style>
