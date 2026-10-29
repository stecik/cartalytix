<script setup lang="ts">
const props = defineProps<{ modelValue: boolean; label: string; disabled?: boolean }>();
const emit = defineEmits<{ "update:modelValue": [value: boolean] }>();

function change(event: Event): void {
  if (!(event.target instanceof HTMLInputElement)) return;
  emit("update:modelValue", event.target.checked);
  // Controlled: the state follows the model, so a declined change does not stick.
  event.target.checked = props.modelValue;
}
</script>

<template>
  <label class="app-switch">
    <input type="checkbox" role="switch" :checked="modelValue" :disabled="disabled" :aria-label="label" @change="change">
    <slot />
  </label>
</template>

<style scoped>
.app-switch { display: flex; align-items: center; gap: 9px; font-size: 12px; color: var(--text-muted); flex-shrink: 0; }
.app-switch input { appearance: none; border: 0; border-radius: 15px; width: 34px; height: 20px; background: var(--control); position: relative; padding: 0; margin: 0; cursor: pointer; transition: background .15s; }
.app-switch input::after { content: ''; position: absolute; top: 3px; left: 3px; width: 14px; height: 14px; background: var(--bg-surface); border-radius: 50%; transition: transform .15s; }
.app-switch input:checked { background: var(--accent); }
.app-switch input:checked::after { transform: translateX(14px); }
.app-switch input:disabled { opacity: .5; cursor: not-allowed; }
.app-switch input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
</style>
