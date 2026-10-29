<script setup lang="ts">
import { useI18n } from "vue-i18n";
import type { ExclusionReason } from "../api";

defineProps<{ reason?: ExclusionReason }>();
const { t } = useI18n();
const labels = { receipt: "excludedByReceipt", item: "excludedByItem", product: "excludedByProduct" } as const;
</script>

<template>
  <span v-if="reason" class="exclusion-status">{{ t(labels[reason]) }}</span>
</template>

<style scoped>
.exclusion-status { display: block; color: var(--text-muted); font-size: 11px; line-height: 1.6; font-weight: 400; }
</style>
