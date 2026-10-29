<script setup lang="ts">
import { useI18n } from "vue-i18n";
import AppButton from "./AppButton.vue";
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
    <AppButton @click="$emit('retry')">
      {{ t("retry") }}
    </AppButton>
  </div>
  <slot v-else />
</template>
