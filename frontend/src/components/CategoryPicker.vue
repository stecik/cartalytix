<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { request } from "../api";
import type { CategoryPage } from "../api";
import AppIcon from "./AppIcon.vue";

const props = defineProps<{ categoryId?: string | null; display: string; label: string; disabled?: boolean }>();
const emit = defineEmits<{ choose: [categoryId: string | null] }>();
const { t } = useI18n();
const id = useId();
const trigger = ref<HTMLButtonElement | null>(null);
const panel = ref<HTMLElement | null>(null);
const input = ref<HTMLInputElement | null>(null);
const open = ref(false);
const search = ref("");
const query = ref("");
const offset = ref(0);
const active = ref(0);
const position = ref({ top: "0px", left: "0px", width: "260px", maxHeight: "400px" });
const path = computed(() => {
  const params = new URLSearchParams({ limit: "10", offset: String(offset.value) });
  if (query.value) params.set("q", query.value);
  return "/categories/page?" + params;
});
const categories = useQuery({
  queryKey: ["category-picker", path],
  queryFn: ({ signal }) => request<CategoryPage>(path.value, { signal }),
  enabled: open,
  staleTime: 30_000,
});
const options = computed(() => query.value ? categories.data.value?.items ?? [] : [{ id: null, name: t("removeCategory") }, ...(categories.data.value?.items ?? [])]);
const loading = computed(() => categories.isFetching.value || search.value.trim() !== query.value);
let timer: ReturnType<typeof setTimeout> | undefined;
watch(search, (value) => {
  clearTimeout(timer);
  timer = setTimeout(() => { query.value = value.trim(); offset.value = 0; active.value = 0; }, 300);
});
watch(categories.data, () => { active.value = 0; });
watch(active, async () => {
  await nextTick();
  document.getElementById(id + "-option-" + active.value)?.scrollIntoView?.({ block: "nearest" });
});
watch(() => props.disabled, (disabled) => { if (disabled) close(false); });

async function show(): Promise<void> {
  if (props.disabled) return;
  const rect = trigger.value?.getBoundingClientRect();
  if (rect) {
    const width = Math.min(300, window.innerWidth - 24);
    const below = window.innerHeight - rect.bottom - 12;
    const above = rect.top - 12;
    const height = Math.min(420, Math.max(below, above));
    position.value = { top: (below >= Math.min(420, above) ? rect.bottom + 6 : Math.max(12, rect.top - height - 6)) + "px", left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)) + "px", width: width + "px", maxHeight: height + "px" };
  }
  search.value = ""; query.value = ""; offset.value = 0; active.value = 0; open.value = true;
  await nextTick(); input.value?.focus();
}
function close(focusTrigger = true): void {
  open.value = false;
  clearTimeout(timer);
  if (focusTrigger) trigger.value?.focus();
}
function choose(index: number): void {
  const option = options.value[index];
  if (!option || props.disabled || loading.value || categories.isError.value) return;
  close(); emit("choose", option.id);
}
function onKey(event: KeyboardEvent): void {
  const last = options.value.length - 1;
  const moves: Record<string, number> = { ArrowDown: Math.max(0, Math.min(last, active.value + 1)), ArrowUp: Math.max(0, active.value - 1) };
  if (event.key in moves) { event.preventDefault(); active.value = moves[event.key] ?? 0; }
  else if (event.key === "Enter") { event.preventDefault(); choose(active.value); }
}
function onPanelKey(event: KeyboardEvent): void {
  if (event.key === "Escape") { event.preventDefault(); close(); }
}
function onFocusOut(event: FocusEvent): void {
  if (!(event.relatedTarget instanceof Node) || (!panel.value?.contains(event.relatedTarget) && !trigger.value?.contains(event.relatedTarget))) close(false);
}
function onTriggerKey(event: KeyboardEvent): void {
  if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) { event.preventDefault(); void show(); }
}
function outside(event: Event): void {
  if (!(event.target instanceof Node) || (!panel.value?.contains(event.target) && !trigger.value?.contains(event.target))) close(false);
}
function reposition(): void { close(false); }
watch(open, (value) => {
  if (value) {
    document.addEventListener("pointerdown", outside);
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition);
  } else {
    document.removeEventListener("pointerdown", outside);
    window.removeEventListener("resize", reposition);
    window.removeEventListener("scroll", reposition);
  }
});
onBeforeUnmount(() => {
  clearTimeout(timer);
  document.removeEventListener("pointerdown", outside);
  window.removeEventListener("resize", reposition);
  window.removeEventListener("scroll", reposition);
});
</script>

<template>
  <div class="category-picker">
    <button ref="trigger" type="button" class="category-picker-trigger" aria-haspopup="listbox" :aria-expanded="open" :aria-controls="id + '-list'" :aria-label="label" :disabled="disabled" @click="open ? close() : show()" @keydown="onTriggerKey" @focusout="onFocusOut"><span>{{ display }}</span><AppIcon name="chevron" /></button>
    <Teleport to="body">
      <div v-if="open" ref="panel" class="category-picker-panel" :style="position" @keydown="onPanelKey" @focusout="onFocusOut">
        <input ref="input" v-model="search" role="combobox" type="search" maxlength="100" aria-autocomplete="list" :aria-expanded="open" :aria-controls="id + '-list'" :aria-activedescendant="!loading && !categories.isError.value && options.length ? id + '-option-' + active : undefined" :aria-label="t('searchCategories')" :placeholder="t('searchCategories')" @keydown="onKey" />
        <p v-if="loading" class="field-note" role="status">{{ t("loading") }}</p>
        <div v-else-if="categories.isError.value" role="alert"><p>{{ t("categoryLoadFailed") }}</p><button type="button" class="text-button" @click="categories.refetch()">{{ t("retry") }}</button></div>
        <ul :id="id + '-list'" role="listbox" :aria-label="label" :aria-busy="loading">
          <template v-if="!loading && !categories.isError.value">
            <li v-for="(option, index) in options" :id="id + '-option-' + index" :key="option.id ?? 'remove'" role="option" :aria-selected="categoryId !== undefined && option.id === categoryId" :class="{ active: active === index }" @pointermove="active = index" @mousedown.prevent @click="choose(index)">{{ option.name }}</li>
          </template>
        </ul>
        <p v-if="!loading && !categories.isError.value && !categories.data.value?.items.length" class="field-note">{{ t("noMatchingCategories") }}</p>
        <div v-if="offset > 0 || (categories.data.value?.total ?? 0) > 10" class="category-picker-pages">
          <button type="button" class="text-button" :disabled="offset === 0 || loading" @click="offset = Math.max(0, offset - 10)">{{ t("previous") }}</button>
          <span>{{ t("categoryPage", { page: offset / 10 + 1 }) }}</span>
          <button type="button" class="text-button" :disabled="offset + 10 >= (categories.data.value?.total ?? 0) || loading" @click="offset += 10">{{ t("next") }}</button>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.category-picker { display: inline-block; max-width: 100%; }
.category-picker-trigger { display: inline-flex; align-items: center; justify-content: space-between; gap: 8px; max-width: 220px; min-height: 34px; padding: 0 11px; border: 1px solid var(--border-select-compact); border-radius: 999px; background: var(--surface-input); color: var(--green); font-size: 12px; text-align: left; }
.category-picker-trigger > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.category-picker-trigger svg { width: 13px; height: 13px; flex-shrink: 0; color: var(--muted); }
.category-picker-trigger:hover { border-color: var(--border-select-hover); }
.category-picker-trigger:focus-visible, .category-picker-trigger[aria-expanded="true"] { outline: 0; border-color: var(--border-input-focus); box-shadow: 0 0 0 2px var(--ring-input-focus); }
.category-picker-panel { position: fixed; z-index: 50; overflow: auto; padding: 8px; border: 1px solid var(--border-select-list); border-radius: 10px; background: var(--surface-input); box-shadow: 0 12px 32px var(--shadow-select); }
.category-picker-panel input { width: 100%; min-height: 38px; font-size: 13px; }
.category-picker-panel ul { list-style: none; padding: 0; margin: 6px 0; }
.category-picker-panel li { padding: 9px 10px; border-radius: 6px; font-size: 13px; color: var(--green); overflow-wrap: anywhere; cursor: pointer; }
.category-picker-panel li.active { background: var(--surface-toggle); }
.category-picker-panel li[aria-selected="true"] { font-weight: 600; }
.category-picker-panel .field-note { padding: 0 8px; font-size: 12px; }
.category-picker-pages { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 4px 2px; border-top: 1px solid var(--line); font-size: 11px; color: var(--muted); }
</style>
