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
  --dp-background-color: var(--bg-raised);
  --dp-text-color: var(--text);
  --dp-hover-color: var(--bg-subtle);
  --dp-hover-text-color: var(--text);
  --dp-hover-icon-color: var(--accent);
  --dp-primary-color: var(--accent);
  --dp-primary-disabled-color: var(--text-muted);
  --dp-primary-text-color: var(--on-accent);
  --dp-secondary-color: var(--text-muted);
  --dp-border-color: var(--control);
  --dp-menu-border-color: var(--border-overlay);
  --dp-border-color-hover: var(--control-hover);
  --dp-border-color-focus: var(--focus-strong);
  --dp-disabled-color: var(--bg-subtle);
  --dp-disabled-color-text: var(--text-muted);
  --dp-icon-color: var(--text-muted);
  --dp-danger-color: var(--danger);
  --dp-highlight-color: var(--focus-ring);
}
.app-date .dp--input {
  height: var(--control-height);
  color: var(--accent);
}
/* The library toggles on both the icon and its wrapper, so an icon click reopens and closes at once; let it reach the input. */
.app-date .dp--input-icon {
  pointer-events: none;
}
.app-date .dp--input-focus {
  box-shadow: 0 0 0 3px var(--focus-ring);
}
.dp--menu {
  border-radius: 10px;
  box-shadow: 0 12px 32px var(--shadow-overlay);
}
</style>
