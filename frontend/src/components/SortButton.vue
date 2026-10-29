<script setup lang="ts">
// Column header sort control; the direction mirrors the header's aria-sort.
defineProps<{ direction: "ascending" | "descending" | "none" }>();
const MARKS = { ascending: "↑", descending: "↓", none: "" } as const;
</script>

<template>
  <button type="button" class="sort-button"><slot /><span aria-hidden="true">{{ MARKS[direction] }}</span></button>
</template>

<style scoped>
.sort-button { display: inline-flex; gap: 4px; padding: 0; border: 0; background: none; color: inherit; font: inherit; }
.sort-button:hover { color: var(--accent); }
.sort-button span { min-width: 1ch; }
:global(th.amount) .sort-button { justify-content: flex-end; }
</style>
