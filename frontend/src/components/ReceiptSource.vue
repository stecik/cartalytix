<script setup lang="ts">
import { useI18n } from "vue-i18n";

/** Provider code of a receipt; manually added receipts also get a badge and hide their hidden source. */
defineProps<{ provider: string; manual: boolean }>();
const { t } = useI18n();
</script>

<template>
  <span v-if="provider !== 'manual'">{{ provider }}</span>
  <span v-if="manual" class="manual-badge">{{ t("addedManually") }}</span>
</template>

<style scoped>
.manual-badge { display: inline-block; padding: 0 6px; border-radius: 4px; background: var(--bg-muted); color: var(--text-muted); font-size: 11px; line-height: 1.6; white-space: nowrap; }
span + .manual-badge { margin-left: 8px; }
</style>
