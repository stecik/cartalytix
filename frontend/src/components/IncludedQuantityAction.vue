<script setup lang="ts">
import { computed, ref } from "vue";
import { useMutation, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { request } from "../api";
import type { ReceiptDetail, ReceiptItem } from "../api";
import AppButton from "./AppButton.vue";
import AppDialog from "./AppDialog.vue";
import { spendingQueries } from "./ExclusionAction.vue";

/** Counts only part of a receipt line, e.g. one of four shared burgers. */
const props = defineProps<{ path: string; item: ReceiptItem; amount: string; format: (value: string) => string }>();
const { t } = useI18n();
const client = useQueryClient();
const editing = ref(false);
const value = ref<number | "">("");
// Pieces split only into whole pieces; weighed or fractional lines take any part.
const whole = computed(() => !props.item.is_weight && Number.isInteger(Number(props.item.quantity)));
const quantity = computed(() => props.format(props.item.quantity));

function open(): void {
  value.value = Number(props.item.included_quantity ?? props.item.quantity);
  editing.value = true;
}

const save = useMutation({
  mutationFn: (included: string | null) => request<ReceiptDetail>(props.path, {
    method: "PATCH",
    body: JSON.stringify({ included_quantity: included }),
  }),
  onSuccess: () => {
    editing.value = false;
    return client.invalidateQueries({ predicate: (query) => spendingQueries.has(String(query.queryKey[0])) });
  },
});
</script>

<template>
  <span v-if="item.included_quantity" class="included-part">{{ t("countedPart", { counted: format(item.included_quantity), quantity, amount }) }}</span>
  <AppButton v-if="!item.exclusion_reason && Number(item.quantity) > 0" type="button" size="sm" data-action="count-part" @click.stop="open">{{ t("countPart") }}</AppButton>
  <AppDialog v-if="editing" :title="t('countPart')" :busy="save.isPending.value" @close="editing = false">
    <p>{{ t("includedQuantityHelp") }}</p>
    <form :aria-busy="save.isPending.value" @submit.prevent="value !== '' && save.mutate(String(value))">
      <label for="included-quantity">{{ t("includedQuantity") }}</label>
      <input id="included-quantity" v-model="value" type="number" inputmode="decimal" min="0" :max="item.quantity" :step="whole ? 1 : 'any'" required :disabled="save.isPending.value">
      <p class="field-note">{{ t(whole ? "includedQuantityWhole" : "includedQuantityRange", { quantity }) }}</p>
      <p v-if="save.isError.value" role="alert" class="form-alert">{{ t("includedQuantityFailed") }}</p>
      <div class="dialog-actions">
        <AppButton v-if="item.included_quantity" type="button" :disabled="save.isPending.value" @click="save.mutate(null)">{{ t("countWhole") }}</AppButton>
        <AppButton variant="primary" type="submit" :disabled="save.isPending.value">{{ save.isPending.value ? t("saving") : t("save") }}</AppButton>
      </div>
    </form>
  </AppDialog>
</template>

<style scoped>
.included-part { color: var(--text-muted); font-size: 11px; line-height: 1.6; }
</style>
