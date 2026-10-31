<script setup lang="ts">
import { computed } from "vue";
import { useMutation, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { ApiError, request } from "../api";
import type { ChangeBatchInput, ChangeBatchOutput, UndoConflict, UndoInput } from "../api";
import { changeValue } from "../changes";
import { sourceName } from "../names";
import { formatMoney, numberLocale } from "../stats";
import AppButton from "./AppButton.vue";
import AppDialog from "./AppDialog.vue";
import { spendingQueries } from "./ExclusionAction.vue";

/** Previews a batch of spending changes, or an undo of one, as a dry run, then applies exactly what was previewed. */
const props = withDefaults(defineProps<{ title: string; path?: string; body: Omit<ChangeBatchInput, "dry_run" | "idempotency_key" | "expected_state"> | Omit<UndoInput, "dry_run" | "idempotency_key"> }>(), { path: "/changes" });
const emit = defineEmits<{ close: []; applied: [result: ChangeBatchOutput] }>();
const { t, locale } = useI18n();
const client = useQueryClient();
// One key per dialog, so repeating a confirm whose response got lost replays instead of applying twice.
// getRandomValues, unlike randomUUID, also works over plain HTTP on a LAN address.
const idempotencyKey = Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
const errors: Record<string, string> = {
  changes_conflict: "changesConflict",
  idempotency_key_reused: "changesKeyReused",
  receipt_not_found: "changesTargetMissing",
  receipt_item_not_found: "changesTargetMissing",
  item_not_found: "changesTargetMissing",
  duplicate_change: "changesDuplicate",
  included_quantity_out_of_range: "changesInvalidQuantity",
  included_quantity_not_whole: "changesInvalidQuantity",
  undo_conflict: "undoConflict",
  already_undone: "undoAlreadyDone",
  change_batch_not_found: "undoMissing",
  change_entry_not_found: "undoMissing",
  undo_target_missing: "undoTargetMissing",
};

function send(body: ChangeBatchInput | UndoInput): Promise<ChangeBatchOutput> {
  return request<ChangeBatchOutput>(props.path, { method: "POST", body: JSON.stringify(body) });
}

const preview = useMutation({ mutationFn: () => send({ ...props.body, dry_run: true }) });
const confirm = useMutation({
  // Undo checks for later changes itself and takes no expected state.
  mutationFn: (state: string) => send({ ...props.body, dry_run: false, idempotency_key: idempotencyKey, ...("operations" in props.body ? { expected_state: state } : {}) }),
  onSuccess: (result) => {
    emit("applied", result);
    emit("close");
    return client.invalidateQueries({ predicate: (query) => spendingQueries.has(String(query.queryKey[0])) });
  },
  onError: (error) => {
    // The data moved since the preview; show the fresh impact before anything is written.
    if (error instanceof ApiError && error.code === "changes_conflict") preview.mutate();
    // The history behind the dialog is stale; refresh it so it shows what changed.
    if (error instanceof ApiError && (error.code === "undo_conflict" || error.code === "already_undone"))
      return client.invalidateQueries({ queryKey: ["changes"] });
  },
});
preview.mutate();

const result = computed(() => preview.data.value);
const unchanged = computed(() => result.value?.changes.filter((change) => !change.changed).length ?? 0);
const nothing = computed(() => !!result.value && unchanged.value === result.value.changes.length);
const rows = computed(() => {
  if (!result.value) return [];
  const receipts = new Map(result.value.receipts.map((receipt) => [receipt.receipt_id, receipt]));
  const withItems = new Set(result.value.items.map((item) => item.receipt_id));
  return [
    ...result.value.items.map((item) => ({ ...item, key: item.item_id, title: sourceName(item.name, t), receipt: receipts.get(item.receipt_id) })),
    ...result.value.receipts.filter((receipt) => !withItems.has(receipt.receipt_id))
      .map((receipt) => ({ ...receipt, key: receipt.receipt_id, title: receipt.merchant, receipt: undefined })),
  ];
});
// Merchant and date only tell rows apart when the batch spans several receipts.
const manyReceipts = computed(() => new Set(rows.value.map((row) => row.receipt_id)).size > 1);
const dateFormatter = computed(() => new Intl.DateTimeFormat(numberLocale(locale.value), { dateStyle: "medium" }));
const busy = computed(() => confirm.isPending.value);
const failure = computed(() => {
  if (preview.isError.value) return message(preview.error.value, "changePreviewFailed");
  return confirm.isError.value ? message(confirm.error.value, "changesFailed") : null;
});
// Undo never overwrites a later change; list what blocked it.
const conflicts = computed<UndoConflict[]>(() => {
  const error = preview.error.value ?? confirm.error.value;
  const list = error instanceof ApiError && error.code === "undo_conflict" ? error.params.conflicts : undefined;
  return Array.isArray(list) ? list as UndoConflict[] : [];
});

function value(recorded: UndoConflict["current"]): string {
  return changeValue(recorded, t, locale.value);
}

function money(amount: string, currency: string): string {
  return formatMoney(amount, currency, locale.value);
}

function difference(amount: string, currency: string): string {
  return new Intl.NumberFormat(numberLocale(locale.value), { style: "currency", currency, signDisplay: "exceptZero" }).format(Number(amount));
}

function message(value: unknown, fallback: string): string {
  return t((value instanceof ApiError && value.code ? errors[value.code] : undefined) ?? fallback);
}

function apply(): void {
  if (!result.value || nothing.value || conflicts.value.length || preview.isPending.value || busy.value) return;
  confirm.mutate(result.value.state);
}
</script>

<template>
  <AppDialog :title="title" :busy="busy" @close="emit('close')">
    <p v-if="preview.isPending.value" role="status">{{ t("changePreviewLoading") }}</p>
    <template v-else-if="result">
      <p v-if="nothing" role="status">{{ t("changeNothing") }}</p>
      <template v-else>
        <dl v-if="result.totals.length" class="change-totals">
          <div v-for="total in result.totals" :key="total.currency">
            <dt>{{ t("changeImpact") }}</dt>
            <dd class="change-difference">{{ difference(total.difference, total.currency) }}</dd>
            <dd class="change-range">{{ money(total.before_amount, total.currency) }} → {{ money(total.after_amount, total.currency) }}</dd>
          </div>
        </dl>
        <p v-else>{{ t("changeNoImpact") }}</p>
        <p v-if="unchanged">{{ t("changeUnchanged", unchanged) }}</p>
        <ul v-if="rows.length" class="change-list" tabindex="0" :aria-label="t('changeDetails')">
          <li v-for="row in rows" :key="row.key">
            <div class="change-row">
              <span>{{ row.title }}</span>
              <strong>{{ difference(row.difference, row.currency) }}</strong>
            </div>
            <div class="change-range">
              <span v-if="manyReceipts && row.receipt">{{ row.receipt.merchant }} · {{ dateFormatter.format(new Date(row.receipt.purchased_at)) }} · </span>
              <span v-else-if="manyReceipts && 'purchased_at' in row">{{ dateFormatter.format(new Date(row.purchased_at)) }} · </span>
              {{ money(row.before_amount, row.currency) }} → {{ money(row.after_amount, row.currency) }}
            </div>
          </li>
        </ul>
      </template>
    </template>
    <p v-if="failure" role="alert" class="form-alert">{{ failure }}</p>
    <ul v-if="conflicts.length" class="change-list conflict-list" tabindex="0" :aria-label="t('undoConflicts')">
      <li v-for="conflict in conflicts" :key="conflict.entry_id">
        <div class="change-row"><span>{{ sourceName(conflict.name, t) }}</span></div>
        <div class="change-range">{{ t("undoConflictValues", { expected: value(conflict.expected), current: value(conflict.current) }) }}</div>
      </li>
    </ul>
    <div class="dialog-actions">
      <AppButton type="button" data-action="cancel" :disabled="busy" @click="emit('close')">{{ t("cancel") }}</AppButton>
      <AppButton v-if="preview.isError.value && !conflicts.length" type="button" data-action="retry-preview" @click="preview.mutate()">{{ t("retry") }}</AppButton>
      <AppButton v-else-if="!conflicts.length" variant="primary" type="button" data-action="apply-changes" :disabled="!result || nothing || preview.isPending.value || busy" :aria-busy="busy" @click="apply">
        {{ busy ? t("saving") : t("applyChanges") }}
      </AppButton>
    </div>
  </AppDialog>
</template>

<style scoped>
.change-totals { margin: 4px 0 16px; }
.change-totals > div + div { margin-top: 12px; }
.change-totals dt { color: var(--text-muted); font-size: 12px; }
.change-totals dd { margin: 0; }
.change-difference { margin-top: 4px; color: var(--accent); font-size: 24px; font-weight: 600; font-variant-numeric: tabular-nums; }
.change-range { color: var(--text-muted); font-size: 12px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.change-list { list-style: none; margin: 16px 0 0; padding: 0 4px 0 0; max-height: min(280px, 36dvh); overflow: auto; border-top: 1px solid var(--border); }
.change-list > li { padding: 10px 0; border-bottom: 1px solid var(--border); }
.change-row { display: flex; justify-content: space-between; align-items: baseline; gap: 16px; font-size: 13px; }
.change-row > span { overflow-wrap: anywhere; }
.change-row > strong { white-space: nowrap; font-weight: 600; font-variant-numeric: tabular-nums; }
</style>
