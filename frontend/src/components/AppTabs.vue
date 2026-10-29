<script setup lang="ts" generic="T extends string">
import { nextTick, ref } from "vue";

// With panelId each tab controls the panel `${panelId}-${value}` labelled by the tab `${panelId}-tab-${value}`.
const props = defineProps<{ options: ReadonlyArray<{ value: T; label: string }>; label: string; panelId?: string }>();
const model = defineModel<T>({ required: true });
const list = ref<HTMLElement | null>(null);

async function onKey(event: KeyboardEvent): Promise<void> {
  const values = props.options.map((option) => option.value);
  const position = values.indexOf(model.value);
  const moves: Record<string, number> = { ArrowRight: (position + 1) % values.length, ArrowLeft: (position + values.length - 1) % values.length, Home: 0, End: values.length - 1 };
  const target = values[moves[event.key] ?? -1];
  if (target === undefined) return;
  event.preventDefault();
  model.value = target;
  await nextTick();
  list.value?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus();
}
</script>

<template>
  <div ref="list" class="app-tabs" role="tablist" :aria-label="label" @keydown="onKey">
    <button
      v-for="option in options"
      :id="panelId ? `${panelId}-tab-${option.value}` : undefined"
      :key="option.value"
      type="button"
      role="tab"
      :data-tab="option.value"
      :aria-selected="model === option.value"
      :aria-controls="panelId ? `${panelId}-${option.value}` : undefined"
      :tabindex="model === option.value ? 0 : -1"
      @click="model = option.value"
    >
      {{ option.label }}
    </button>
  </div>
</template>

<style scoped>
.app-tabs { display: flex; flex-wrap: wrap; gap: 0 26px; }
.app-tabs button { padding: 12px 0; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--text-muted); font-size: 13px; white-space: nowrap; transition: color 0.16s, border-color 0.16s; }
.app-tabs button:hover { color: var(--accent); }
.app-tabs button[aria-selected="true"] { border-bottom-color: var(--accent); color: var(--accent); font-weight: 600; }
@media (max-width: 390px) { .app-tabs { gap: 0 18px; } .app-tabs button { font-size: 12px; } }
</style>
