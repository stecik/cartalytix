<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import type { ChangeHistoryBatch, ChangeHistoryEntry, UndoInput } from "../api";
import { changeSentence } from "../changes";
import { numberLocale } from "../stats";
import AppButton from "./AppButton.vue";
import ChangePreviewDialog from "./ChangePreviewDialog.vue";

/** Recorded change batches, newest first, each undoable as a whole or entry by entry. */
defineProps<{ batches: ChangeHistoryBatch[]; linkReceipts?: boolean }>();
const { t, locale } = useI18n();
// Bulk changes can hold hundreds of entries; long batches open on request.
const COLLAPSED_ENTRIES = 5;
const expanded = ref(new Set<string>());
// The undo request is frozen when the dialog opens, so a refetch cannot change what the preview showed.
const undoing = ref<{ batchId: string; redo: boolean; body: Omit<UndoInput, "dry_run" | "idempotency_key"> } | null>(null);
const dateFormatter = computed(() => new Intl.DateTimeFormat(numberLocale(locale.value), { dateStyle: "medium", timeStyle: "short" }));

function source(batch: ChangeHistoryBatch): string {
  if (batch.source === "user") return t("changeSourceUser");
  return batch.client_name ? t("changeSourceAssistantNamed", { client: batch.client_name }) : t("changeSourceAssistant");
}

function sentence(entry: ChangeHistoryEntry): string {
  return changeSentence(entry, t, locale.value);
}

function difference(amount: string, currency: string): string {
  return new Intl.NumberFormat(numberLocale(locale.value), { style: "currency", currency, signDisplay: "exceptZero" }).format(Number(amount));
}

function pending(batch: ChangeHistoryBatch): ChangeHistoryEntry[] {
  return batch.entries.filter((entry) => !entry.undone_by_id);
}

function visible(batch: ChangeHistoryBatch): ChangeHistoryEntry[] {
  return expanded.value.has(batch.id) ? batch.entries : batch.entries.slice(0, COLLAPSED_ENTRIES);
}

function expand(batch: ChangeHistoryBatch): void {
  expanded.value = new Set(expanded.value).add(batch.id);
}

function undo(batch: ChangeHistoryBatch, entry?: ChangeHistoryEntry): void {
  const left = pending(batch);
  // Undoing the whole batch would be refused once any entry is undone, so name the remaining ones.
  const entryIds = entry ? [entry.id] : left.length < batch.entries.length ? left.map((item) => item.id) : undefined;
  // Undoing an undo batch reapplies the original change.
  undoing.value = { batchId: batch.id, redo: !!batch.undo_of_id, body: entryIds ? { entry_ids: entryIds } : {} };
}
</script>

<template>
  <ol class="change-history">
    <li v-for="batch in batches" :key="batch.id" class="change-batch">
      <div class="change-batch-heading">
        <div class="change-batch-meta">
          <time :datetime="batch.created_at">{{ dateFormatter.format(new Date(batch.created_at)) }}</time>
          <span class="change-source">{{ source(batch) }}</span>
          <span v-if="batch.undo_of_id" class="change-tag">{{ t("changeUndoOf") }}</span>
          <span v-if="!pending(batch).length" class="change-tag">{{ t("changeUndone") }}</span>
        </div>
        <div v-if="batch.totals.length" class="change-batch-totals">
          <strong v-for="total in batch.totals" :key="total.currency">{{ difference(total.difference, total.currency) }}</strong>
        </div>
      </div>
      <ul class="change-entries">
        <li v-for="entry in visible(batch)" :key="entry.id" :class="{ 'change-undone': entry.undone_by_id }">
          <RouterLink v-if="linkReceipts && entry.receipt_id" class="change-sentence" :to="{ name: 'receipt-detail', params: { id: entry.receipt_id } }">{{ sentence(entry) }}</RouterLink>
          <span v-else class="change-sentence">{{ sentence(entry) }}</span>
          <span v-if="entry.undone_by_id && pending(batch).length" class="change-tag">{{ t("changeUndone") }}</span>
          <AppButton v-else-if="!entry.undone_by_id && batch.entries.length > 1" type="button" variant="text" size="sm" data-action="undo-entry" :aria-label="t('undoEntry', { change: sentence(entry) })" @click="undo(batch, entry)">{{ t(batch.undo_of_id ? "redo" : "undo") }}</AppButton>
        </li>
      </ul>
      <div class="change-batch-actions">
        <AppButton v-if="visible(batch).length < batch.entries.length" type="button" variant="text" size="sm" data-action="expand-entries" @click="expand(batch)">{{ t("changeEntriesMore", { count: batch.entries.length }) }}</AppButton>
        <AppButton v-if="pending(batch).length" type="button" size="sm" data-action="undo-batch" @click="undo(batch)">{{ t(batch.undo_of_id ? "redo" : "undo") }}</AppButton>
      </div>
    </li>
  </ol>
  <ChangePreviewDialog v-if="undoing" :title="t(undoing.redo ? 'redoChange' : 'undoChange')" :path="`/changes/${encodeURIComponent(undoing.batchId)}/undo`" :body="undoing.body" @close="undoing = null" />
</template>

<style scoped>
.change-history { list-style: none; margin: 0; padding: 0; }
.change-batch { padding: 18px 0; border-bottom: 1px solid var(--border); }
.change-batch-heading { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 8px 20px; }
.change-batch-meta { display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px 12px; color: var(--text-muted); font-size: 12px; }
.change-source { color: var(--text); font-weight: 550; }
.change-tag { border: 1px solid var(--border); border-radius: 4px; padding: 2px 7px; color: var(--text-muted); font-size: 11px; white-space: nowrap; }
.change-batch-totals { display: flex; flex-wrap: wrap; gap: 4px 12px; }
.change-batch-totals strong { color: var(--accent); font-size: 15px; font-weight: 600; white-space: nowrap; font-variant-numeric: tabular-nums; }
.change-entries { list-style: none; margin: 10px 0 0; padding: 0; font-size: 13px; }
.change-entries > li { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 32px; }
.change-sentence { min-width: 0; overflow-wrap: anywhere; }
a.change-sentence { color: var(--accent); text-underline-offset: 3px; }
a.change-sentence:hover { text-decoration: underline; }
.change-undone .change-sentence { color: var(--text-muted); text-decoration: line-through; }
.change-entries .button.text { min-height: 32px; flex-shrink: 0; }
.change-batch-actions { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: 8px 16px; }
.change-batch-actions:not(:empty) { margin-top: 8px; }
</style>
