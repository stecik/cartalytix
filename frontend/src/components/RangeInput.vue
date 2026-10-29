<script setup lang="ts">
import { useId } from "vue";
import { useI18n } from "vue-i18n";

defineProps<{ label: string; min: string; max: string; minLabel: string; maxLabel: string }>();
const emit = defineEmits<{ "update:min": [value: string]; "update:max": [value: string] }>();
const { t } = useI18n();
const id = useId();

function value(event: Event): string {
  return event.target instanceof HTMLInputElement ? event.target.value.trim() : "";
}
</script>

<template>
  <div class="range-input" role="group" :aria-labelledby="id">
    <span :id="id">{{ label }}</span>
    <div class="range-box">
      <input type="number" inputmode="decimal" min="0" step="0.01" :value="min" :placeholder="t('rangeFrom')" :aria-label="minLabel" @change="emit('update:min', value($event))">
      <span aria-hidden="true">–</span>
      <input type="number" inputmode="decimal" min="0" step="0.01" :value="max" :placeholder="t('rangeTo')" :aria-label="maxLabel" @change="emit('update:max', value($event))">
    </div>
  </div>
</template>

<style scoped>
.range-input { display: flex; flex-direction: column; gap: 6px; font-size: 11px; color: var(--muted); }
.range-box { display: flex; align-items: center; width: 200px; height: 40px; border: 1px solid var(--border-input); border-radius: 4px; background: var(--surface-input); }
.range-box:focus-within { border-color: var(--border-input-focus); box-shadow: 0 0 0 3px var(--ring-input-focus); }
.range-box input { width: 0; flex: 1 1 0; min-width: 0; height: 100%; border: 0; background: transparent; padding: 0 12px; font-size: 13px; color: var(--text); appearance: textfield; -moz-appearance: textfield; }
.range-box input::-webkit-outer-spin-button,
.range-box input::-webkit-inner-spin-button { margin: 0; appearance: none; }
.range-box input:focus { outline: 0; }
.range-box > span { color: var(--muted); font-size: 13px; }
@media (max-width: 780px) { .range-box { width: 100%; } }
</style>
