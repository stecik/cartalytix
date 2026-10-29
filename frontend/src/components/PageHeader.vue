<script setup lang="ts">
// The intro slot replaces the plain intro text; the aside slot sits beside the title on wide screens.
defineProps<{ title: string; eyebrow?: string; intro?: string }>();
</script>

<template>
  <header class="page-heading">
    <div class="page-heading-main">
      <div v-if="eyebrow" class="eyebrow">{{ eyebrow }}</div>
      <h1>{{ title }}</h1>
      <p v-if="intro || $slots.intro"><slot name="intro">{{ intro }}</slot></p>
      <slot />
    </div>
    <slot name="aside" />
  </header>
</template>

<style scoped>
.page-heading { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 24px; margin-bottom: 37px; }
.page-heading-main { min-width: 0; }
h1 { margin: 10px 0 11px; font-size: 39px; font-weight: 650; letter-spacing: -1.8px; line-height: 1.25; }
p { color: var(--text-muted); font-size: 14px; line-height: 1.6; }
@media (max-width: 780px) {
  .page-heading { flex-direction: column; align-items: flex-start; gap: 18px; margin-bottom: 28px; }
  h1 { margin-top: 8px; font-size: 34px; }
  p { font-size: 13px; }
}
</style>
