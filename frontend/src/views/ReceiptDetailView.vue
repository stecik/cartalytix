<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { sourceName } from "../names";
import { useRoute, useRouter } from "vue-router";
import { ApiError, request } from "../api";
import type { ChangeHistoryPage, ChangeOperation, ReceiptDetail, ReceiptDiscount, User } from "../api";
import AppDialog from "../components/AppDialog.vue";
import ChangeHistoryList from "../components/ChangeHistoryList.vue";
import ChangePreviewDialog from "../components/ChangePreviewDialog.vue";
import SelectionBar from "../components/SelectionBar.vue";
import AppIcon from "../components/AppIcon.vue";
import ManualReceiptDialog from "../components/ManualReceiptDialog.vue";
import ReceiptSource from "../components/ReceiptSource.vue";
import QueryState from "../components/QueryState.vue";
import SegmentedToggle from "../components/SegmentedToggle.vue";
import ExclusionAction from "../components/ExclusionAction.vue";
import ExclusionStatus from "../components/ExclusionStatus.vue";
import IncludedQuantityAction from "../components/IncludedQuantityAction.vue";
import { sessionOptions } from "../auth";
import { providerLogos } from "../providerLogos";
import { useUserSettings } from "../settings";
import AppButton from "../components/AppButton.vue";
import PageHeader from "../components/PageHeader.vue";

const route = useRoute();
const { t, locale } = useI18n();
const receiptId = computed(() => String(route.params.id ?? ""));
const receipt = useQuery({
  queryKey: computed(() => ["receipts", "detail", receiptId.value]),
  queryFn: ({ signal }) => request<ReceiptDetail>(`/receipts/${encodeURIComponent(receiptId.value)}`, { signal }),
});
// The receipt shows its latest few changes; the change history page pages through all of them.
const HISTORY_PREVIEW = 3;
const MAX_HISTORY = 100;
const historyLimit = ref(HISTORY_PREVIEW);
watch(receiptId, () => { historyLimit.value = HISTORY_PREVIEW; });
const history = useQuery({
  queryKey: computed(() => ["changes", "receipt", receiptId.value, historyLimit.value]),
  queryFn: ({ signal }) => request<ChangeHistoryPage>(`/changes?${new URLSearchParams({ receipt_id: receiptId.value, limit: String(historyLimit.value) }).toString()}`, { signal }),
  // Keep the shown changes while more load, but never another receipt's.
  placeholderData: (previous, previousQuery) => previousQuery?.queryKey[2] === receiptId.value ? previous : undefined,
});
const router = useRouter();
const client = useQueryClient();
const deleting = ref(false);
const editing = ref(false);

async function receiptEdited(): Promise<void> {
  editing.value = false;
  await client.invalidateQueries();
}

const remove = useMutation({
  mutationFn: () => request<void>(`/receipts/${encodeURIComponent(receiptId.value)}`, { method: "DELETE" }),
  onSuccess: async () => {
    deleting.value = false;
    client.removeQueries({ queryKey: ["receipts", "detail", receiptId.value] });
    await router.push("/receipts");
    await client.invalidateQueries();
  },
});
const session = useQuery(sessionOptions);
const settings = useUserSettings();
const receiptView = computed({
  get: (): User["receipt_view"] => session.data.value?.receipt_view ?? "rendered",
  set: (value: User["receipt_view"]) => settings.mutate({ receipt_view: value }),
});
const rendered = computed(() => receiptView.value === "rendered");
const views = computed(() => [
  { value: "rendered" as const, label: t("receiptViewRendered") },
  { value: "detailed" as const, label: t("receiptViewDetailed") },
]);
const notFound = computed(() => receipt.error.value instanceof ApiError && receipt.error.value.status === 404);
const language = computed(() => locale.value === "cs" ? "cs-CZ" : "en-GB");
const amountFormatter = computed(() => receipt.data.value ? new Intl.NumberFormat(language.value, {
  style: "currency", currency: receipt.data.value.currency, maximumFractionDigits: 2,
}) : null);
const unitPriceFormatter = computed(() => receipt.data.value ? new Intl.NumberFormat(language.value, {
  style: "currency", currency: receipt.data.value.currency, maximumFractionDigits: 4,
}) : null);
const quantityFormatter = computed(() => new Intl.NumberFormat(language.value, { maximumFractionDigits: 4 }));
const dateFormatter = computed(() => new Intl.DateTimeFormat(language.value, { dateStyle: "medium", timeStyle: "short" }));
const discounts = computed(() => {
  const itemIds = new Set(receipt.data.value?.items.map((item) => item.id));
  const grouped = new Map<string | null, ReceiptDiscount[]>();
  for (const discount of receipt.data.value?.discounts ?? []) {
    const key = discount.item_id && itemIds.has(discount.item_id) ? discount.item_id : null;
    const items = grouped.get(key) ?? [];
    items.push(discount);
    grouped.set(key, items);
  }
  return grouped;
});

const selected = ref<string[]>([]);
watch(receiptId, () => { selected.value = []; });
const selectedItems = computed(() => {
  const chosen = new Set(selected.value);
  return receipt.data.value?.items.filter((item) => chosen.has(item.id)) ?? [];
});
// Operations are frozen when the dialog opens, so a refetch cannot change what the preview showed.
const bulkChange = ref<{ title: string; operations: ChangeOperation[] } | null>(null);

function bulkExclude(excluded: boolean): void {
  bulkChange.value = {
    title: t(excluded ? "excludeSelected" : "restoreSelected"),
    operations: selectedItems.value.map((item) => ({ kind: "item_exclusion", receipt_id: receiptId.value, item_id: item.id, excluded })),
  };
}

function clearSelection(): void {
  selected.value = [];
}

function negated(value: string): string {
  return String(-Number(value));
}

function money(value: string, unitPrice = false): string {
  const formatter = unitPrice ? unitPriceFormatter.value : amountFormatter.value;
  return formatter?.format(Number(value)) ?? "";
}

function quantity(value: string, unit: string | null): string {
  const amount = quantityFormatter.value.format(Number(value));
  const label = unit === "piece" ? t("unitPiece") : unit;
  return label ? `${amount} ${label}` : amount;
}

function date(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? t("unknownDate") : dateFormatter.value.format(parsed);
}
</script>

<template>
  <div class="view-content receipt-detail">
    <RouterLink to="/receipts" class="text-link back-link receipt-back"><AppIcon name="arrow" />{{ t("backToReceipts") }}</RouterLink>
    <PageHeader :eyebrow="t('receiptDetails')" :title="receipt.data.value?.merchant ?? t('receiptDetails')">
      <template v-if="receipt.data.value" #intro><time :datetime="receipt.data.value.purchased_at">{{ date(receipt.data.value.purchased_at) }}</time><span class="receipt-provider"><ReceiptSource :provider="receipt.data.value.provider" :manual="receipt.data.value.is_manual" /></span></template>
    </PageHeader>
    <p v-if="notFound" role="alert" class="form-alert">{{ t("receiptNotFound") }}</p>
    <QueryState v-else :pending="receipt.isPending.value" :failed="receipt.isError.value" @retry="receipt.refetch()">
      <template v-if="receipt.data.value">
        <p v-if="receipt.data.value.quality_issues.length" role="status" class="receipt-quality">{{ t("receiptQualityWarning") }}</p>
        <div class="receipt-accounting">
          <div class="included-total"><span>{{ t("includedAmount") }}</span><strong>{{ money(receipt.data.value.included_amount ?? receipt.data.value.total_amount) }}</strong></div>
          <ExclusionAction scope="receipt" :path="`/receipts/${encodeURIComponent(receiptId)}/exclusion`" :excluded="receipt.data.value.is_excluded" />
          <ExclusionStatus v-if="receipt.data.value.is_excluded" reason="receipt" />
          <div v-if="receipt.data.value.is_manual" class="manual-actions">
            <AppButton type="button" @click="editing = true">{{ t("editReceipt") }}</AppButton>
            <AppButton type="button" @click="deleting = true">{{ t("deleteReceipt") }}</AppButton>
          </div>
        </div>
        <ManualReceiptDialog v-if="editing" :receipt="receipt.data.value" @close="editing = false" @saved="receiptEdited" />
        <AppDialog v-if="deleting" :title="t('deleteReceipt')" :busy="remove.isPending.value" @close="deleting = false">
          <p>{{ t("deleteReceiptHelp") }}</p>
          <p v-if="remove.isError.value" role="alert" class="form-alert">{{ t("deleteReceiptFailed") }}</p>
          <div class="dialog-actions"><AppButton type="button" data-action="cancel" :disabled="remove.isPending.value" @click="deleting = false">{{ t("cancel") }}</AppButton><AppButton variant="danger" type="button" :disabled="remove.isPending.value" @click="remove.mutate()">{{ t("deleteReceipt") }}</AppButton></div>
        </AppDialog>
        <SegmentedToggle v-model="receiptView" class="receipt-view-toggle" :options="views" :label="t('receiptDisplay')" />
        <SelectionBar v-if="selectedItems.length" :summary="t('selectedLines', selectedItems.length)" @clear="clearSelection">
          <AppButton v-if="selectedItems.some((item) => !item.is_excluded)" type="button" size="sm" data-action="exclude-selected" @click="bulkExclude(true)">{{ t("excludeSelected") }}</AppButton>
          <AppButton v-if="selectedItems.some((item) => item.is_excluded)" type="button" size="sm" data-action="restore-selected" @click="bulkExclude(false)">{{ t("restoreSelected") }}</AppButton>
        </SelectionBar>
        <ChangePreviewDialog v-if="bulkChange" :title="bulkChange.title" :body="{ operations: bulkChange.operations }" @applied="clearSelection" @close="bulkChange = null" />
        <div v-if="rendered" class="receipt-paper-wrap">
          <article class="receipt-paper" :aria-label="t('receiptViewRendered')">
            <header class="receipt-paper-header">
              <img v-if="providerLogos[receipt.data.value.provider]" :src="providerLogos[receipt.data.value.provider]" width="52" height="52" alt="" />
              <h2>{{ receipt.data.value.merchant }}</h2>
              <time :datetime="receipt.data.value.purchased_at">{{ date(receipt.data.value.purchased_at) }}</time>
            </header>
            <p v-if="!receipt.data.value.items.length" class="receipt-paper-empty">{{ t("noReceiptItems") }}</p>
            <ol v-else class="receipt-paper-items">
              <li v-for="item in receipt.data.value.items" :key="item.id" :class="{ 'excluded-row': item.exclusion_reason }">
                <div class="receipt-paper-line"><span><input v-model="selected" type="checkbox" class="line-select" :value="item.id" :aria-label="t('selectItem', { name: sourceName(item.name, t) })">{{ sourceName(item.name, t) }}</span><span>{{ money(item.original_amount ?? item.total_amount) }}</span></div>
                <div v-if="item.unit_price !== null && (item.is_weight || Number(item.quantity) !== 1)" class="receipt-paper-sub">{{ quantity(item.quantity, item.unit) }} × {{ money(item.unit_price, true) }}</div>
                <div v-for="discount in discounts.get(item.id)" :key="discount.id" class="receipt-paper-line receipt-paper-sub"><span>{{ discount.name ? sourceName(discount.name, t) : t("discount") }}</span><span>{{ money(negated(discount.amount)) }}</span></div>
                <div class="receipt-item-actions">
                  <ExclusionStatus :reason="item.exclusion_reason" />
                  <ExclusionAction scope="item" :path="`/receipts/${encodeURIComponent(receiptId)}/items/${encodeURIComponent(item.id)}/exclusion`" :excluded="item.is_excluded" />
                  <IncludedQuantityAction :path="`/receipts/${encodeURIComponent(receiptId)}/items/${encodeURIComponent(item.id)}/included-quantity`" :item="item" :amount="money(item.included_amount)" :format="(value) => quantity(value, item.unit)" />
                  <RouterLink v-if="item.exclusion_reason === 'product'" class="text-link" :to="{ path: '/items/detail', query: { name: item.name, currency: receipt.data.value.currency } }">{{ t("manageProductExclusion") }}</RouterLink>
                </div>
              </li>
            </ol>
            <div v-if="discounts.get(null)?.length || Number(receipt.data.value.rounding_amount) !== 0" class="receipt-paper-block">
              <div v-for="discount in discounts.get(null)" :key="discount.id" class="receipt-paper-line"><span>{{ discount.name ? sourceName(discount.name, t) : t("discount") }}</span><span>{{ money(negated(discount.amount)) }}</span></div>
              <div v-if="Number(receipt.data.value.rounding_amount) !== 0" class="receipt-paper-line"><span>{{ t("rounding") }}</span><span>{{ money(receipt.data.value.rounding_amount) }}</span></div>
            </div>
            <div class="receipt-paper-total"><span>{{ t("receiptTotal") }}</span><strong>{{ money(receipt.data.value.total_amount) }}</strong></div>
            <footer class="receipt-paper-footer">
              <span><ReceiptSource :provider="receipt.data.value.provider" :manual="receipt.data.value.is_manual" /></span>
              <span v-if="!receipt.data.value.is_manual">{{ t("receiptNumber") }} {{ receipt.data.value.external_id }}</span>
            </footer>
          </article>
        </div>
        <template v-else>
        <section class="receipt-items" :aria-label="t('receiptItems')">
          <div class="section-heading"><h2>{{ t("receiptItems") }}</h2></div>
          <p v-if="!receipt.data.value.items.length" class="section-note">{{ t("noReceiptItems") }}</p>
          <ol v-else class="receipt-item-list">
            <li v-for="item in receipt.data.value.items" :key="item.id" :class="{ 'excluded-row': item.exclusion_reason }">
              <div class="receipt-item-heading"><input v-model="selected" type="checkbox" class="line-select" :value="item.id" :aria-label="t('selectItem', { name: sourceName(item.name, t) })"><h3>{{ sourceName(item.name, t) }}</h3><strong>{{ money(item.total_amount) }}</strong></div>
              <dl class="receipt-item-facts">
                <div><dt>{{ t("quantity") }}</dt><dd>{{ quantity(item.quantity, item.unit) }}</dd></div>
                <div v-if="item.unit_price !== null"><dt>{{ t("unitPrice") }}</dt><dd>{{ money(item.unit_price, true) }}</dd></div>
                <div v-if="item.original_amount !== null"><dt>{{ t("beforeCoupon") }}</dt><dd>{{ money(item.original_amount) }}</dd></div>
                <div v-if="item.deposit_amount !== null"><dt>{{ t("deposit") }}</dt><dd>{{ money(item.deposit_amount) }}</dd></div>
                <div v-if="item.printed_net_amount !== null"><dt>{{ t("printedNetAmount") }}</dt><dd>{{ money(item.printed_net_amount) }}</dd></div>
                <div v-if="item.source_code"><dt>{{ t("productCode") }}</dt><dd>{{ item.source_code }}</dd></div>
                <div v-if="item.warranty"><dt>{{ t("warranty") }}</dt><dd>{{ item.warranty }}</dd></div>
              </dl>
              <p v-if="item.is_weight" class="section-note">{{ t("weighedItem") }}</p>
              <ul v-if="discounts.get(item.id)?.length" class="receipt-discounts" :aria-label="t('itemDiscounts')">
                <li v-for="discount in discounts.get(item.id)" :key="discount.id"><span>{{ discount.name ? sourceName(discount.name, t) : t("discount") }}</span><span>{{ money(discount.amount) }}</span></li>
              </ul>
              <div class="receipt-item-actions">
                <ExclusionStatus :reason="item.exclusion_reason" />
                <ExclusionAction scope="item" :path="`/receipts/${encodeURIComponent(receiptId)}/items/${encodeURIComponent(item.id)}/exclusion`" :excluded="item.is_excluded" />
                  <IncludedQuantityAction :path="`/receipts/${encodeURIComponent(receiptId)}/items/${encodeURIComponent(item.id)}/included-quantity`" :item="item" :amount="money(item.included_amount)" :format="(value) => quantity(value, item.unit)" />
                <RouterLink v-if="item.exclusion_reason === 'product'" class="text-link" :to="{ path: '/items/detail', query: { name: item.name, currency: receipt.data.value.currency } }">{{ t("manageProductExclusion") }}</RouterLink>
              </div>
            </li>
          </ol>
        </section>
        <section v-if="discounts.get(null)?.length" class="receipt-level-discounts" :aria-label="t('receiptDiscounts')">
          <div class="section-heading"><h2>{{ t("receiptDiscounts") }}</h2></div>
          <ul class="receipt-discounts"><li v-for="discount in discounts.get(null)" :key="discount.id"><span>{{ discount.name ? sourceName(discount.name, t) : t("discount") }}</span><span>{{ money(discount.amount) }}</span></li></ul>
        </section>
        <p v-if="receipt.data.value.discounts.length" class="section-note">{{ t("discountValuesNote") }}</p>
        <dl class="receipt-total">
          <div v-if="Number(receipt.data.value.rounding_amount) !== 0" class="receipt-rounding"><dt>{{ t("rounding") }}</dt><dd>{{ money(receipt.data.value.rounding_amount) }}</dd></div>
          <div><dt>{{ t("receiptTotal") }}</dt><dd>{{ money(receipt.data.value.total_amount) }}</dd></div>
        </dl>
        </template>
        <section v-if="history.data.value?.total" class="receipt-history" :aria-label="t('changes')">
          <div class="section-heading">
            <h2>{{ t("changes") }}</h2>
            <RouterLink class="text-link" to="/changes" :aria-label="t('allChanges')">{{ t("allChanges") }}<AppIcon name="arrow" /></RouterLink>
          </div>
          <ChangeHistoryList :batches="history.data.value.items" />
          <AppButton v-if="history.data.value.items.length < history.data.value.total && historyLimit < MAX_HISTORY" type="button" variant="text" data-action="more-changes" :disabled="history.isFetching.value" @click="historyLimit = Math.min(historyLimit + 10, MAX_HISTORY)">{{ t("moreChanges") }}</AppButton>
        </section>
      </template>
    </QueryState>
  </div>
</template>

<style scoped>
.receipt-back { margin-bottom: 25px; }
.receipt-accounting { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px 24px; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); padding: 18px 0; margin-bottom: 24px; }
.manual-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.included-total { display: flex; flex-direction: column; gap: 5px; font-size: 12px; color: var(--text-muted); }
.included-total strong { font-size: 24px; color: var(--accent); font-weight: 600; font-variant-numeric: tabular-nums; }
.receipt-item-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; margin-top: 10px; font-family: "DM Sans Variable", sans-serif; font-size: 12px; }
.excluded-row > .receipt-paper-line:first-child, .excluded-row > .receipt-item-heading { color: var(--text-muted); }
.receipt-provider { margin-left: 18px; }
.receipt-items { border-top: 1px solid var(--border); padding-top: 24px; }
.receipt-item-list { list-style: none; padding: 0; margin: 20px 0 0; }
.receipt-item-list > li { padding: 24px 0; border-top: 1px solid var(--border); }
.receipt-item-heading { display: flex; justify-content: space-between; align-items: baseline; gap: 20px; }
.receipt-item-heading h3 { flex: 1; font-size: 15px; font-weight: 600; overflow-wrap: anywhere; }
.line-select { flex-shrink: 0; width: 17px; height: 17px; margin: 0; accent-color: var(--accent); cursor: pointer; }
.receipt-item-heading > .line-select { align-self: center; margin-right: -8px; }
.receipt-paper-line .line-select { width: 15px; height: 15px; margin: 0 8px 0 0; vertical-align: -2px; }
.receipt-item-heading > strong { white-space: nowrap; font-size: 15px; font-variant-numeric: tabular-nums; }
.receipt-item-facts { display: flex; flex-wrap: wrap; gap: 14px 32px; font-size: 12px; margin: 18px 0 0; }
.receipt-item-facts dt { color: var(--text-muted); margin-bottom: 6px; }
.receipt-item-facts dd { margin: 0; overflow-wrap: anywhere; }
.receipt-discounts { list-style: none; margin: 16px 0 0; padding: 0; font-size: 12px; }
.receipt-discounts li { display: flex; justify-content: space-between; gap: 20px; padding: 9px 0; color: var(--accent); }
.receipt-discounts li > span:last-child { white-space: nowrap; font-variant-numeric: tabular-nums; }
.receipt-level-discounts { border-top: 1px solid var(--border); padding-top: 24px; margin-top: 12px; }
.receipt-total { border-top: 1px solid var(--border); padding-top: 24px; margin: 26px 0; }
.receipt-total > div { display: flex; justify-content: space-between; align-items: baseline; gap: 20px; font-size: 24px; font-weight: 600; }
.receipt-total dd { margin: 0; white-space: nowrap; font-variant-numeric: tabular-nums; }
.receipt-total > .receipt-rounding { font-size: 12px; font-weight: 400; margin-bottom: 20px; color: var(--text-muted); }
.receipt-view-toggle { margin: 0 auto 28px; }
.receipt-paper-wrap { filter: drop-shadow(0 3px 10px var(--shadow-soft)); max-width: 400px; margin: 8px auto 48px; }
.receipt-paper { --tooth: 7px; background: var(--receipt-paper); color: var(--receipt-ink); padding: 38px 28px; font-family: ui-monospace, "Cascadia Mono", "SFMono-Regular", Consolas, monospace; font-size: 13px; line-height: 1.55;
  mask: conic-gradient(from 135deg at top, #0000, #000 1deg 89deg, #0000 90deg) top / calc(2 * var(--tooth)) 51% repeat-x,
    conic-gradient(from -45deg at bottom, #0000, #000 1deg 89deg, #0000 90deg) bottom / calc(2 * var(--tooth)) 51% repeat-x; }
.receipt-paper-header { display: flex; flex-direction: column; align-items: center; gap: 6px; text-align: center; padding-bottom: 18px; border-bottom: 1px dashed var(--receipt-rule); }
.receipt-paper-header img { width: 52px; height: 52px; object-fit: contain; margin-bottom: 6px; }
.receipt-paper-header h2 { font-family: inherit; font-size: 15px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; }
.receipt-paper-header time { font-size: 12px; color: var(--receipt-ink-muted); }
.receipt-paper-items { list-style: none; margin: 0; padding: 14px 0; }
.receipt-paper-items > li + li { margin-top: 8px; }
.receipt-paper-line { display: flex; justify-content: space-between; gap: 16px; }
.receipt-paper-line > span:first-child { overflow-wrap: anywhere; }
.receipt-paper-line > span:last-child { white-space: nowrap; font-variant-numeric: tabular-nums; }
.receipt-paper-sub { padding-left: 14px; font-size: 12px; color: var(--receipt-ink-muted); }
.receipt-paper-block { padding: 12px 0; border-top: 1px dashed var(--receipt-rule); }
.receipt-paper-empty { padding: 18px 0; text-align: center; color: var(--receipt-ink-muted); }
.receipt-paper-total { display: flex; justify-content: space-between; align-items: baseline; gap: 16px; padding: 14px 0; border-top: 3px double var(--receipt-ink); border-bottom: 1px dashed var(--receipt-rule); font-size: 17px; font-weight: 700; text-transform: uppercase; }
.receipt-paper-total strong { white-space: nowrap; font-variant-numeric: tabular-nums; }
.receipt-paper-footer { display: flex; flex-direction: column; align-items: center; gap: 4px; padding-top: 16px; font-size: 11px; color: var(--receipt-ink-muted); text-transform: uppercase; letter-spacing: 0.5px; }
@media (max-width: 390px) { .receipt-paper { padding: 32px 18px; font-size: 12px; } }
.receipt-history { border-top: 1px solid var(--border); padding-top: 24px; margin-bottom: 32px; }
.receipt-quality { background: var(--bg-warning); color: var(--warning); font-size: 13px; padding: 18px; margin-bottom: 24px; line-height: 1.7; }
</style>
