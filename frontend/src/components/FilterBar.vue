<script setup lang="ts">
import { useI18n } from "vue-i18n";
import AppButton from "./AppButton.vue";

// The search slot takes the remaining row width; the reset action appears only while filtered.
defineProps<{ filtered?: boolean }>();
const emit = defineEmits<{ reset: [] }>();
const { t } = useI18n();
</script>

<template>
  <div class="filter-bar" role="search">
    <div v-if="$slots.search" class="filter-search"><slot name="search" /></div>
    <slot />
    <AppButton v-if="filtered" type="button" size="sm" class="reset-filters" @click="emit('reset')">{{ t("resetFilters") }}</AppButton>
  </div>
</template>

<style scoped>
.filter-bar { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 12px; margin-bottom: 36px; }
.filter-search { flex: 1 1 140px; min-width: 0; }
.filter-search > :slotted(*) { width: 100%; }
@media (max-width: 780px) {
  .filter-bar { display: grid; grid-template-columns: 1fr 1fr; }
  .filter-search,
  .reset-filters { grid-column: 1 / -1; }
}
</style>
