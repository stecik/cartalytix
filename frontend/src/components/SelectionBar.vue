<script setup lang="ts">
import { useI18n } from "vue-i18n";
import AppButton from "./AppButton.vue";

/** Sticky bar with bulk actions for the current selection; the default slot holds the actions. */
defineProps<{ summary: string }>();
const emit = defineEmits<{ clear: [] }>();
const { t } = useI18n();
</script>

<template>
  <div class="selection-bar" role="region" :aria-label="t('selectionActions')">
    <span role="status">{{ summary }}</span>
    <slot />
    <AppButton type="button" variant="text" data-action="clear-selection" @click="emit('clear')">{{ t("clearSelection") }}</AppButton>
  </div>
</template>

<style scoped>
.selection-bar { position: sticky; top: 12px; z-index: 10; display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; padding: 12px 16px; margin: 0 0 20px; border: 1px solid var(--border-overlay); border-radius: 10px; background: var(--bg-raised); box-shadow: 0 4px 16px var(--shadow-overlay); }
.selection-bar > span { margin-right: auto; font-size: 13px; }
@media (max-width: 600px) {
  .selection-bar { top: 8px; padding: 10px 12px; }
  .selection-bar > span { width: 100%; }
}
</style>
