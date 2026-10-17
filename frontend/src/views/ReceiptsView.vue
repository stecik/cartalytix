<script setup lang="ts">
import { ref } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { request } from "../api";
import type { ReceiptPage } from "../api";
import QueryState from "../components/QueryState.vue";
import ReceiptLedger from "../components/ReceiptLedger.vue";

const { t } = useI18n();
const offset = ref(0);
const limit = 30;
const receipts = useQuery({
  queryKey: ["receipts", offset],
  queryFn: ({ signal }) =>
    request<ReceiptPage>(`/receipts?limit=${limit}&offset=${offset.value}`, {
      signal,
    }),
});
</script>

<template>
  <div class="view-content">
    <header class="page-heading">
      <div class="eyebrow">{{ t("workspace") }}</div>
      <h1>{{ t("receipts") }}</h1>
      <p>{{ t("receiptIntro") }}</p>
    </header>
    <QueryState
      :pending="receipts.isPending.value"
      :failed="receipts.isError.value"
      @retry="receipts.refetch()"
    >
      <section v-if="receipts.data.value" class="ledger-section">
        <div class="section-heading">
          <h2>{{ t("receiptCount", receipts.data.value.total) }}</h2>
        </div>
        <ReceiptLedger :receipts="receipts.data.value.items" />
        <div v-if="receipts.data.value.total > limit" class="pagination">
          <span>{{
            t("pageRange", {
              start: offset + 1,
              end: Math.min(offset + limit, receipts.data.value.total),
              total: receipts.data.value.total,
            })
          }}</span>
          <div>
            <button
              class="button secondary"
              :disabled="offset === 0"
              @click="offset = Math.max(0, offset - limit)"
            >
              {{ t("previous") }}
</button><button
              class="button secondary"
              :disabled="offset + limit >= receipts.data.value.total"
              @click="offset += limit"
            >
              {{ t("next") }}
            </button>
          </div>
        </div>
      </section>
    </QueryState>
  </div>
</template>
