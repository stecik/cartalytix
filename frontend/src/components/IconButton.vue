<script setup lang="ts">
// Icon-only action; the label is both the accessible name and the hover hint.
withDefaults(defineProps<{ label: string; tone?: "neutral" | "danger" }>(), { tone: "neutral" });
</script>

<template>
  <button type="button" :class="['icon-button', tone]" :aria-label="label" :title="label"><slot /></button>
</template>

<style scoped>
.icon-button { display: grid; place-items: center; flex-shrink: 0; width: 36px; height: 36px; padding: 0; border: 0; border-radius: 5px; background: transparent; color: var(--text-muted); font-size: 22px; line-height: 1; transition: background 0.16s, color 0.16s; }
.icon-button :deep(svg) { width: 18px; height: 18px; }
.neutral:hover:enabled { color: var(--accent); background: var(--bg-muted); }
.danger:hover:enabled { color: var(--danger); background: var(--bg-danger); }
</style>
