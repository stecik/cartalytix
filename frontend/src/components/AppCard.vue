<script setup lang="ts">
// Padded suits plain content; cards with a header or edge-to-edge lists lay out their own body.
defineProps<{ padded?: boolean }>();
</script>

<template>
  <section :class="['app-card', { padded }]">
    <header v-if="$slots.header" class="app-card-header"><slot name="header" /></header>
    <slot />
  </section>
</template>

<style scoped>
/* No overflow clipping: popovers such as AppSelect lists must be able to leave the card. */
.app-card { border: 1px solid var(--border-card); border-radius: 12px; background: var(--bg-raised); }
.app-card + .app-card { margin-top: 28px; }
.padded { padding: 24px; }
.app-card-header :slotted(h2) { font-size: 16px; font-weight: 650; letter-spacing: -.3px; }
.app-card-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px 12px; padding: 18px 24px; border-bottom: 1px solid var(--border-card); border-radius: 11px 11px 0 0; background: var(--bg-subtle); }
@media (max-width: 600px) {
  .app-card + .app-card { margin-top: 22px; }
  .padded { padding: 18px 16px; }
  .app-card-header { padding: 16px; }
}
</style>
