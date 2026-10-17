<script setup lang="ts">
import { useI18n } from "vue-i18n";
defineProps<{ pending: boolean; failed: boolean }>();
defineEmits<{ retry: [] }>();
const { t } = useI18n();
</script>

<template>
  <div v-if="pending" class="query-state" role="status">
    <span class="loading-dot" />{{ t("loading") }}
  </div>
  <div v-else-if="failed" class="query-state error-state" role="alert">
    <p>{{ t("requestFailed") }}</p>
    <button class="button secondary" @click="$emit('retry')">
      {{ t("retry") }}
    </button>
  </div>
  <slot v-else />
</template>
