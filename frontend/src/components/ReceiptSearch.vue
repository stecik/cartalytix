<script setup lang="ts">
import { keepPreviousData, useQuery } from "@tanstack/vue-query";
import { computed, onBeforeUnmount, ref, useId, watch } from "vue";
import { useI18n } from "vue-i18n";
import { request } from "../api";
import type { Suggestion } from "../api";
import { highlight, MIN_SUGGESTION_LENGTH } from "../search";

const DEBOUNCE_MS = 200;

const model = defineModel<string>({ required: true });
const emit = defineEmits<{ select: [text: string] }>();

const { t } = useI18n();
const listId = useId();
const open = ref(false);
const active = ref(-1);
const term = ref(model.value.trim());
let timer: ReturnType<typeof setTimeout> | undefined;

watch(model, (value) => {
  clearTimeout(timer);
  timer = setTimeout(() => {
    term.value = value.trim();
  }, DEBOUNCE_MS);
});
onBeforeUnmount(() => clearTimeout(timer));

const enabled = computed(() => open.value && term.value.length >= MIN_SUGGESTION_LENGTH);
const suggestions = useQuery({
  queryKey: ["receipt-suggestions", term],
  queryFn: ({ signal }) =>
    request<{ items: Suggestion[] }>(
      `/receipts/suggestions?${new URLSearchParams({ q: term.value })}`,
      { signal },
    ),
  enabled,
  staleTime: 60_000,
  // Older suggestions stay visible only while the user refines the same term.
  placeholderData: (previous, previousQuery) => {
    const previousTerm = String(previousQuery?.queryKey[1] ?? "").toLocaleLowerCase();
    return previousTerm && term.value.toLocaleLowerCase().startsWith(previousTerm)
      ? keepPreviousData(previous)
      : undefined;
  },
});

const items = computed(() => {
  const found = enabled.value ? (suggestions.data.value?.items ?? []) : [];
  const current = model.value.trim().toLocaleLowerCase();
  return found.length === 1 && found[0]?.text.toLocaleLowerCase() === current ? [] : found;
});
const expanded = computed(() => open.value && items.value.length > 0);

watch(items, () => {
  active.value = -1;
});

function optionId(index: number): string {
  return `${listId}-${index}`;
}

function choose(text: string): void {
  clearTimeout(timer);
  model.value = text;
  term.value = text.trim();
  open.value = false;
  emit("select", text.trim());
}

function move(step: number): void {
  if (!expanded.value) {
    open.value = true;
    return;
  }
  const next = active.value + step;
  if (next >= items.value.length) active.value = -1;
  else if (next < -1) active.value = items.value.length - 1;
  else active.value = next;
}

function onEnter(): void {
  const item = items.value[active.value];
  choose(item ? item.text : model.value);
}

function onEscape(): void {
  if (expanded.value) open.value = false;
  else model.value = "";
}
</script>

<template>
  <div class="receipt-search">
    <label>
      <span class="sr-only">{{ t("searchReceipts") }}</span>
      <input
        v-model="model"
        type="search"
        maxlength="100"
        autocomplete="off"
        role="combobox"
        aria-autocomplete="list"
        :aria-expanded="expanded"
        :aria-controls="listId"
        :aria-activedescendant="active >= 0 ? optionId(active) : undefined"
        :placeholder="t('searchReceipts')"
        @input="open = true"
        @focus="open = true"
        @blur="open = false"
        @keydown.down.prevent="move(1)"
        @keydown.up.prevent="move(-1)"
        @keydown.enter.prevent="onEnter"
        @keydown.esc.prevent="onEscape"
      >
    </label>
    <ul
      v-show="expanded"
      :id="listId"
      class="suggestions"
      role="listbox"
      :aria-label="t('searchSuggestions')"
    >
      <li
        v-for="(item, index) in items"
        :id="optionId(index)"
        :key="`${item.kind}:${item.text}`"
        role="option"
        :aria-selected="index === active"
        :class="{ active: index === active }"
        @mousedown.prevent="choose(item.text)"
        @mousemove="active = index"
      >
        <span class="suggestion-text">
          <template v-for="(segment, part) in highlight(item.text, term)" :key="part">
            <mark v-if="segment.match">{{ segment.text }}</mark>
            <template v-else>{{ segment.text }}</template>
          </template>
        </span>
        <span class="suggestion-kind" :class="item.kind">
          {{ t(item.kind === "item" ? "suggestionItem" : "suggestionMerchant") }}
        </span>
        <span class="suggestion-count" :title="t('receiptCount', item.receipt_count)">
          {{ item.receipt_count }}×
        </span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.receipt-search {
  position: relative;
}
.receipt-search label {
  display: block;
}
.receipt-search input {
  width: 100%;
  height: 40px;
  border: 1px solid #cbd1c3;
  border-radius: 4px;
  background: #fbfbf6;
  padding: 0 12px;
  font-size: 13px;
}
.receipt-search input:focus {
  border-color: #67856c;
  box-shadow: 0 0 0 3px #dfe6d9;
  outline: 0;
}
.suggestions {
  position: absolute;
  z-index: 5;
  top: calc(100% + 6px);
  left: 0;
  right: 0;
  margin: 0;
  padding: 6px;
  list-style: none;
  background: #fbfbf6;
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: 0 12px 32px rgba(23, 62, 52, 0.14);
  max-height: 340px;
  overflow-y: auto;
  animation: arrive 0.12s ease-out;
}
.suggestions li {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 5px;
  font-size: 13px;
  color: var(--green);
  cursor: pointer;
}
.suggestions li.active {
  background: var(--light);
}
.suggestion-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.suggestion-text mark {
  background: none;
  color: inherit;
  font-weight: 700;
}
.suggestion-kind {
  font-size: 10px;
  letter-spacing: 0.4px;
  text-transform: uppercase;
  color: var(--muted);
  padding: 3px 7px;
  border-radius: 999px;
  background: #eef1e7;
}
.suggestion-kind.merchant {
  background: #e3ebe3;
  color: #3f7a55;
}
.suggestion-count {
  min-width: 34px;
  text-align: right;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: var(--muted);
}
</style>
