<script setup lang="ts">
defineProps<{ label: string }>();
</script>

<template>
  <label class="filter-field"><span>{{ label }}</span><slot /></label>
</template>

<style scoped>
.filter-field { display: flex; flex-direction: column; gap: 6px; font-size: 11px; color: var(--text-muted); }
.filter-field > :slotted(.app-select) { min-width: 210px; }
@media (max-width: 780px) { .filter-field > :slotted(*) { width: 100%; min-width: 0; } }
</style>
