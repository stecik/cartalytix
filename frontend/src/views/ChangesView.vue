<script setup lang="ts">
import { computed, watch } from "vue";
import { keepPreviousData, useQuery } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import { request } from "../api";
import type { ChangeHistoryPage } from "../api";
import AppButton from "../components/AppButton.vue";
import ChangeHistoryList from "../components/ChangeHistoryList.vue";
import PageHeader from "../components/PageHeader.vue";
import QueryState from "../components/QueryState.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const limit = 30;
const offset = computed(() => {
  const value = Number(typeof route.query.offset === "string" ? route.query.offset : "");
  return Number.isInteger(value) && value > 0 ? value : 0;
});
const apiPath = computed(() => `/changes?${new URLSearchParams({ limit: String(limit), offset: String(offset.value) }).toString()}`);
const history = useQuery({
  queryKey: ["changes", apiPath],
  queryFn: ({ signal }) => request<ChangeHistoryPage>(apiPath.value, { signal }),
  placeholderData: keepPreviousData,
});
watch([() => history.data.value?.total, history.isPlaceholderData], ([total, placeholder]) => {
  if (!placeholder && total !== undefined && offset.value > 0 && offset.value >= total) {
    changePage(Math.floor(Math.max(0, total - 1) / limit) * limit);
  }
});

function changePage(value: number): void {
  void router.replace({ query: value > 0 ? { offset: String(value) } : {} });
}
</script>

<template>
  <div class="view-content">
    <PageHeader :eyebrow="t('workspace')" :title="t('changes')" :intro="t('changeHistoryIntro')" />
    <QueryState :pending="history.isPending.value" :failed="history.isError.value" @retry="history.refetch()">
      <section v-if="history.data.value" class="ledger-section">
        <div class="section-heading">
          <h2>{{ t("changeCount", history.data.value.total) }}</h2>
        </div>
        <ChangeHistoryList v-if="history.data.value.items.length" :batches="history.data.value.items" link-receipts />
        <div v-else class="empty-state"><h3>{{ t("noChanges") }}</h3></div>
        <div v-if="history.data.value.total > limit" class="pagination">
          <span>{{ t("pageRange", { start: offset + 1, end: Math.min(offset + limit, history.data.value.total), total: history.data.value.total }) }}</span>
          <div>
            <AppButton :disabled="offset === 0" @click="changePage(Math.max(0, offset - limit))">{{ t("previous") }}</AppButton>
            <AppButton :disabled="offset + limit >= history.data.value.total" @click="changePage(offset + limit)">{{ t("next") }}</AppButton>
          </div>
        </div>
      </section>
    </QueryState>
  </div>
</template>
