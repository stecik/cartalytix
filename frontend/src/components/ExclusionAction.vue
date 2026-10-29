<script setup lang="ts">
import { computed } from "vue";
import { useMutation, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { request } from "../api";
import type { ItemDetail, ReceiptDetail } from "../api";
import AppButton from "./AppButton.vue";

const props = defineProps<{
  scope: "receipt" | "item" | "product";
  path: string;
  excluded?: boolean;
  identity?: { name: string; currency: string };
  restoreLabel?: string;
}>();
const emit = defineEmits<{ saved: [result: ReceiptDetail | ItemDetail | undefined] }>();
const { t } = useI18n();
const client = useQueryClient();
const queryFamilies = new Set(["receipts", "overview", "overview-stats", "items", "item-detail", "item-totals", "item-exclusions", "receipt-suggestions"]);
const labels = {
  receipt: ["excludeReceipt", "restoreReceipt"],
  item: ["excludeOccurrence", "restoreOccurrence"],
  product: ["excludeProduct", "restoreProduct"],
} as const;
const label = computed(() => props.excluded && props.restoreLabel ? props.restoreLabel : t(labels[props.scope][props.excluded ? 1 : 0]));
const mutation = useMutation({
  mutationFn: () => request<ReceiptDetail | ItemDetail | undefined>(props.path, {
    method: "PATCH",
    body: JSON.stringify({ ...props.identity, excluded: !props.excluded }),
  }),
  onSuccess: (result) => {
    emit("saved", result);
    return client.invalidateQueries({
      predicate: (query) => queryFamilies.has(String(query.queryKey[0])),
    });
  },
});
</script>

<template>
  <span class="exclusion-action" :data-exclusion="scope">
    <AppButton type="button" size="sm" class="exclusion-button" :disabled="mutation.isPending.value" :aria-busy="mutation.isPending.value" @click.stop="mutation.mutate()">
      {{ mutation.isPending.value ? t("saving") : label }}
    </AppButton>
    <span v-if="mutation.isError.value" role="alert" class="exclusion-error">{{ t("exclusionSaveFailed") }}</span>
  </span>
</template>

<style scoped>
.exclusion-action { display: inline-flex; flex-direction: column; align-items: flex-start; gap: 5px; }
.exclusion-button:disabled { cursor: wait; }
.exclusion-error { max-width: 240px; color: var(--danger); font-size: 12px; white-space: normal; }
</style>
