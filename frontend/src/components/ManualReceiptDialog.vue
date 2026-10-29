<script setup lang="ts">
import { computed, ref } from "vue";
import { useMutation, useQuery } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { request } from "../api";
import type { ManualReceiptInput, Receipt, Shop } from "../api";
import { providerName } from "../names";
import AppDateInput from "./AppDateInput.vue";
import AppDialog from "./AppDialog.vue";
import AppIcon from "./AppIcon.vue";
import AppSelect from "./AppSelect.vue";
import IconButton from "./IconButton.vue";
import SegmentedToggle from "./SegmentedToggle.vue";
import AppButton from "./AppButton.vue";

const NEW_SHOP = "new";
const emit = defineEmits<{ close: []; saved: [receipts: Receipt[]] }>();
const { t } = useI18n();

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

const now = new Date();
const mode = ref<"form" | "json">("form");
const modes = computed(() => [
  { value: "form" as const, label: t("manualReceiptForm") },
  { value: "json" as const, label: t("manualReceiptJson") },
]);
const shops = useQuery({
  queryKey: ["shops"],
  queryFn: ({ signal }) => request<{ items: Shop[] }>("/shops", { signal }),
});
const shopOptions = computed(() => [
  ...(shops.data.value?.items ?? []).map((shop, index) => ({
    value: String(index),
    label: shop.provider ? `${shop.merchant} (${providerName(shop.provider)})` : shop.merchant,
  })),
  { value: NEW_SHOP, label: t("manualReceiptNewShop") },
]);
const shop = ref(NEW_SHOP);
const merchant = ref("");
const date = ref(`${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`);
const time = ref(`${pad(now.getHours())}:${pad(now.getMinutes())}`);
const currency = ref("CZK");
const items = ref([{ name: "", quantity: "1", total: "" }]);
const json = ref("");
const problem = ref<"manualReceiptJsonInvalid" | "manualReceiptDateRequired" | null>(null);

function amount(value: string): string {
  return value.trim().replace(",", ".");
}

const total = computed(() => items.value.reduce((sum, item) => sum + (Number(amount(item.total)) || 0), 0));

function formReceipt(): ManualReceiptInput {
  const selected = shop.value === NEW_SHOP ? null : shops.data.value?.items[Number(shop.value)];
  return {
    merchant: selected?.merchant ?? merchant.value.trim(),
    provider: selected?.provider ?? null,
    purchased_at: `${date.value}T${time.value || "12:00"}:00`,
    currency: currency.value.trim().toUpperCase(),
    items: items.value.map((item) => ({ name: item.name.trim(), quantity: amount(item.quantity) || "1", total_amount: amount(item.total) })),
  };
}

function jsonReceipts(): ManualReceiptInput[] | null {
  try {
    const parsed: unknown = JSON.parse(json.value);
    if (Array.isArray(parsed)) return parsed as ManualReceiptInput[];
    if (typeof parsed === "object" && parsed !== null) {
      return "receipts" in parsed && Array.isArray(parsed.receipts) ? parsed.receipts as ManualReceiptInput[] : [parsed as ManualReceiptInput];
    }
  } catch {
    // Reported below as invalid JSON.
  }
  return null;
}

async function loadFile(event: Event): Promise<void> {
  const file = event.target instanceof HTMLInputElement ? event.target.files?.[0] : undefined;
  if (file) json.value = await file.text();
}

const save = useMutation({
  mutationFn: (receipts: ManualReceiptInput[]) => request<{ items: Receipt[] }>("/receipts/manual", { method: "POST", body: JSON.stringify({ receipts }) }),
  onSuccess: (result) => emit("saved", result.items),
});

function submit(): void {
  problem.value = null;
  if (mode.value === "json") {
    const receipts = jsonReceipts();
    if (receipts?.length) save.mutate(receipts);
    else problem.value = "manualReceiptJsonInvalid";
  } else if (!date.value) problem.value = "manualReceiptDateRequired";
  else save.mutate([formReceipt()]);
}
</script>

<template>
  <AppDialog class="manual-receipt-dialog" :title="t('addReceipt')" :busy="save.isPending.value" @close="emit('close')">
    <SegmentedToggle v-model="mode" :options="modes" :label="t('addReceipt')" />
    <form :aria-busy="save.isPending.value" @submit.prevent="submit">
      <template v-if="mode === 'form'">
        <label>{{ t("manualReceiptShop") }}</label>
        <AppSelect v-model="shop" :options="shopOptions" :label="t('manualReceiptShop')" />
        <template v-if="shop === NEW_SHOP">
          <label for="manual-merchant">{{ t("manualReceiptShopName") }}</label>
          <input id="manual-merchant" v-model="merchant" type="text" maxlength="256" required :disabled="save.isPending.value">
        </template>
        <div class="manual-when">
          <div>
            <label>{{ t("manualReceiptDate") }}</label>
            <AppDateInput v-model="date" required />
          </div>
          <div>
            <label for="manual-time">{{ t("manualReceiptTime") }}</label>
            <input id="manual-time" v-model="time" type="time" :disabled="save.isPending.value">
          </div>
          <div>
            <label for="manual-currency">{{ t("manualReceiptCurrency") }}</label>
            <input id="manual-currency" v-model="currency" type="text" pattern="[A-Za-z]{3}" maxlength="3" required :disabled="save.isPending.value">
          </div>
        </div>
        <fieldset class="manual-items">
          <legend>{{ t("manualReceiptItems") }}</legend>
          <div v-for="(item, index) in items" :key="index" class="manual-item">
            <input v-model="item.name" type="text" maxlength="500" required :aria-label="t('manualReceiptItemName')" :placeholder="t('manualReceiptItemName')" :disabled="save.isPending.value">
            <input v-model="item.quantity" type="text" inputmode="decimal" pattern="\d+([.,]\d+)?" :aria-label="t('quantity')" :placeholder="t('quantity')" :disabled="save.isPending.value">
            <input v-model="item.total" type="text" inputmode="decimal" pattern="-?\d+([.,]\d{1,2})?" required :aria-label="t('manualReceiptItemTotal')" :placeholder="t('manualReceiptItemTotal')" :disabled="save.isPending.value">
            <IconButton tone="danger" :label="t('manualReceiptRemoveItem')" :disabled="items.length === 1 || save.isPending.value" @click="items.splice(index, 1)"><AppIcon name="trash" /></IconButton>
          </div>
          <div class="manual-items-footer">
            <AppButton type="button" :disabled="save.isPending.value" @click="items.push({ name: '', quantity: '1', total: '' })">{{ t("manualReceiptAddItem") }}</AppButton>
            <span>{{ t("receiptTotal") }} <strong>{{ total.toFixed(2) }} {{ currency }}</strong></span>
          </div>
        </fieldset>
      </template>
      <template v-else>
        <p class="field-note">{{ t("manualReceiptJsonHelp") }}</p>
        <label for="manual-json-file">{{ t("manualReceiptJsonFile") }}</label>
        <input id="manual-json-file" type="file" accept="application/json,.json" :disabled="save.isPending.value" @change="loadFile">
        <label for="manual-json">{{ t("manualReceiptJson") }}</label>
        <textarea id="manual-json" v-model="json" rows="10" spellcheck="false" required :disabled="save.isPending.value" />
      </template>
      <p v-if="problem" role="alert" class="form-alert">{{ t(problem) }}</p>
      <p v-else-if="save.isError.value" role="alert" class="form-alert">{{ t("manualReceiptFailed") }}</p>
      <div class="dialog-actions">
        <AppButton data-action="cancel" type="button" :disabled="save.isPending.value" @click="emit('close')">{{ t("cancel") }}</AppButton>
        <AppButton variant="primary" type="submit" :disabled="save.isPending.value">{{ t(save.isPending.value ? "saving" : "addReceipt") }}</AppButton>
      </div>
    </form>
  </AppDialog>
</template>

<style scoped>
.manual-receipt-dialog { width: min(620px, calc(100% - 32px)); }
.manual-when { display: grid; grid-template-columns: 1.4fr 1fr 0.8fr; gap: 12px; }
.manual-items { border: 0; padding: 0; margin: 24px 0 0; min-width: 0; }
.manual-items legend { font-size: 12px; font-weight: 550; margin-bottom: 10px; }
.manual-item { display: grid; grid-template-columns: 1fr 72px 110px 36px; gap: 8px; align-items: center; margin-bottom: 8px; }
.manual-items-footer { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; margin-top: 4px; font-size: 13px; }
textarea { width: 100%; padding: 10px 13px; font-family: monospace; font-size: 12px; resize: vertical; }
input[type=file] { height: auto; padding: 10px 0; border: 0; background: transparent; }
@media (max-width: 520px) {
  .manual-when { grid-template-columns: 1fr 1fr; }
  .manual-item { grid-template-columns: 1fr 1fr 36px; }
  .manual-item input:first-child { grid-column: 1 / -1; }
}
</style>
