<script setup lang="ts" generic="T extends string">
import { useId } from "vue";

// Block stretches the options evenly over the full width of the container.
defineProps<{ options: ReadonlyArray<{ value: T; label: string }>; label: string; block?: boolean }>();
const model = defineModel<T>({ required: true });
const name = useId();
</script>

<template>
  <fieldset :class="['view-toggle', { block }]">
    <legend class="sr-only">{{ label }}</legend>
    <label v-for="option in options" :key="option.value" :class="{ selected: model === option.value }">
      <input v-model="model" type="radio" :name="name" :value="option.value" class="sr-only">{{ option.label }}
    </label>
  </fieldset>
</template>

<style scoped>
.view-toggle { display: flex; width: max-content; max-width: 100%; margin: 0; padding: 4px; border: 1px solid var(--border); border-radius: 999px; background: var(--bg-subtle); }
.view-toggle label { position: relative; padding: 8px 18px; border-radius: 999px; font-size: 13px; color: var(--text-muted); cursor: pointer; white-space: nowrap; transition: background 0.16s, color 0.16s, box-shadow 0.16s; }
.view-toggle label:hover { color: var(--accent); }
.view-toggle label.selected { background: var(--bg-raised); color: var(--accent); font-weight: 600; box-shadow: 0 1px 4px var(--shadow-soft); }
.view-toggle label:has(:focus-visible) { outline: 3px solid var(--focus); outline-offset: 2px; }
.view-toggle.block { display: grid; grid-auto-columns: minmax(0, 1fr); grid-auto-flow: column; width: 100%; }
.view-toggle.block label { display: flex; align-items: center; justify-content: center; min-height: 36px; padding: 8px 10px; white-space: normal; text-align: center; }
</style>
