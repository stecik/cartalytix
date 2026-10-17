<script setup lang="ts" generic="T extends string">
import { useId } from "vue";

defineProps<{ options: ReadonlyArray<{ value: T; label: string }>; label: string }>();
const model = defineModel<T>({ required: true });
const name = useId();
</script>

<template>
  <fieldset class="view-toggle">
    <legend class="sr-only">{{ label }}</legend>
    <label v-for="option in options" :key="option.value" :class="{ selected: model === option.value }">
      <input v-model="model" type="radio" :name="name" :value="option.value" class="sr-only">{{ option.label }}
    </label>
  </fieldset>
</template>

<style scoped>
.view-toggle { display: flex; width: max-content; max-width: 100%; padding: 4px; border: 1px solid var(--line); border-radius: 999px; background: #eef0e7; }
.view-toggle label { position: relative; padding: 8px 18px; border-radius: 999px; font-size: 13px; color: var(--muted); cursor: pointer; white-space: nowrap; transition: background 0.16s, color 0.16s, box-shadow 0.16s; }
.view-toggle label:hover { color: var(--green); }
.view-toggle label.selected { background: #fffef8; color: var(--green); font-weight: 600; box-shadow: 0 1px 4px #17311f1f; }
.view-toggle label:has(:focus-visible) { outline: 3px solid #8ea99b; outline-offset: 2px; }
</style>
