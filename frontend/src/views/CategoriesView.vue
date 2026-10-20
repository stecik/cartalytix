<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { request } from "../api";
import type { Category, CategoryAssignmentResponse, CategoryAssignmentResult, CategoryItem, CategoryItemKey, CategoryItemPage, CategoryItemSnapshot, CategoryPage } from "../api";
import AppDialog from "../components/AppDialog.vue";
import AppIcon from "../components/AppIcon.vue";
import CategoryPicker from "../components/CategoryPicker.vue";
import QueryState from "../components/QueryState.vue";

interface UndoAssignment { items: CategoryItemSnapshot[]; expected_category_id: string | null }
const { t } = useI18n();
const client = useQueryClient();
const categorySearch = ref("");
const categoryQuery = ref("");
const categoryOffset = ref(0);
const categoryBrowserOpen = ref(false);
const categoryPath = computed(() => {
  const params = new URLSearchParams({ limit: "10", offset: String(categoryOffset.value) });
  if (categoryQuery.value) params.set("q", categoryQuery.value);
  return "/categories/page?" + params;
});
const categories = useQuery({
  queryKey: ["categories", "sidebar", categoryPath],
  queryFn: ({ signal }) => request<CategoryPage>(categoryPath.value, { signal }),
});
const search = ref("");
const query = ref("");
const filter = ref("");
const filterName = ref("");
const activeCategoryLabel = computed(() => filter.value === "uncategorized" ? t("uncategorized") : filter.value ? filterName.value : t("allCategories"));
const cursors = ref<string[]>([""]);
const cursor = computed(() => cursors.value.at(-1) ?? "");
const selected = ref<string[]>([]);
const changing = ref(false);
const status = ref<string | null>(null);
const error = ref<string | null>(null);
const undo = ref<UndoAssignment | null>(null);
const apiPath = computed(() => {
  const params = new URLSearchParams({ limit: "50" });
  if (cursor.value) params.set("cursor", cursor.value);
  if (query.value) params.set("q", query.value);
  if (filter.value === "uncategorized") params.set("uncategorized", "true");
  else if (filter.value) params.set("category_id", filter.value);
  return "/category-items?" + params;
});
const items = useQuery({
  queryKey: ["category-items", apiPath],
  queryFn: ({ signal }) => request<CategoryItemPage>(apiPath.value, { signal }),
});
const busy = computed(() => changing.value || save.isPending.value || remove.isPending.value);
const productsBusy = computed(() => busy.value || items.isFetching.value || search.value.trim() !== query.value);
function identity(item: CategoryItemKey): string {
  return JSON.stringify([item.connection_id, item.name, item.currency]);
}
const selectedKeys = computed(() => new Set(selected.value));
const selectedItems = computed(() => (items.data.value?.items ?? []).filter((item) => selectedKeys.value.has(identity(item))));
const allSelected = computed(() => !!items.data.value?.items.length && selectedItems.value.length === items.data.value.items.length);
function selectPage(event: Event): void {
  if (productsBusy.value || !(event.target instanceof HTMLInputElement)) return;
  selected.value = event.target.checked ? (items.data.value?.items ?? []).map(identity) : [];
}
watch([query, filter], () => { cursors.value = [""]; selected.value = []; });
watch(cursor, () => { selected.value = []; });
watch(items.data, (page) => {
  const visible = new Set((page?.items ?? []).map(identity));
  selected.value = selected.value.filter((key) => visible.has(key));
});
let searchTimer: ReturnType<typeof setTimeout> | undefined;
let categoryTimer: ReturnType<typeof setTimeout> | undefined;
watch(search, (value) => {
  selected.value = [];
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { query.value = value.trim(); }, 300);
});
watch(categorySearch, (value) => {
  clearTimeout(categoryTimer);
  categoryTimer = setTimeout(() => { categoryOffset.value = 0; categoryQuery.value = value.trim(); }, 300);
});
onBeforeUnmount(() => { clearTimeout(searchTimer); clearTimeout(categoryTimer); });
function nextPage(): void {
  const next = items.data.value?.next_cursor;
  if (next && !productsBusy.value) cursors.value = [...cursors.value, next];
}
function chooseFilter(categoryId: string, name = ""): void {
  if (busy.value) return;
  filter.value = categoryId;
  filterName.value = name;
  categoryBrowserOpen.value = false;
}
async function refresh(): Promise<void> {
  await Promise.all([
    client.invalidateQueries({ queryKey: ["categories"] }),
    client.invalidateQueries({ queryKey: ["category-picker"] }),
    client.invalidateQueries({ queryKey: ["category-items"] }),
  ]);
}
const editing = ref<{ category: Category | null; name: string } | null>(null);
const save = useMutation({
  mutationFn: ({ category, name }: { category: Category | null; name: string }) => request<Category>(category ? "/categories/" + encodeURIComponent(category.id) : "/categories", {
    method: category ? "PATCH" : "POST", body: JSON.stringify({ name: name.trim() }),
  }),
  onSuccess: async (category) => {
    if (category.id === filter.value) filterName.value = category.name;
    editing.value = null; await refresh();
  },
  onSettled: () => { changing.value = false; },
});
function editCategory(category: Category | null): void {
  if (busy.value) return;
  save.reset(); editing.value = { category, name: category?.name ?? "" };
}
function saveCategory(): void {
  if (busy.value || !editing.value?.name.trim()) return;
  changing.value = true; save.mutate(editing.value);
}
const deleting = ref<Category | null>(null);
const remove = useMutation({
  mutationFn: (category: Category) => request<void>("/categories/" + encodeURIComponent(category.id), { method: "DELETE" }),
  onSuccess: async (_result, category) => {
    deleting.value = null;
    if (filter.value === category.id) filter.value = "";
    selected.value = [];
    await refresh();
  },
  onSettled: () => { changing.value = false; },
});
function deleteCategory(category: Category): void {
  if (busy.value) return;
  remove.reset(); deleting.value = category;
}
function confirmDelete(): void {
  if (busy.value || !deleting.value) return;
  changing.value = true; remove.mutate(deleting.value);
}
async function assign(products: CategoryItem[], categoryId: string | null): Promise<void> {
  if (productsBusy.value || !products.length || editing.value || deleting.value) return;
  changing.value = true; error.value = null; status.value = null;
  try {
    const result = await request<CategoryAssignmentResponse>("/category-items/assign", {
      method: "POST", body: JSON.stringify({ items: products.map(({ connection_id, name, currency }) => ({ connection_id, name, currency })), category_id: categoryId, dry_run: false }),
    });
    if (result.changed > 0) undo.value = { items: result.previous_items, expected_category_id: categoryId };
    selected.value = []; status.value = t("categoryChangesApplied", result.changed);
    await refresh();
  } catch { error.value = t("categoryUpdateFailed"); }
  finally { changing.value = false; }
}
async function undoAssignment(): Promise<void> {
  if (busy.value || !undo.value || editing.value || deleting.value) return;
  changing.value = true; error.value = null; status.value = null;
  try {
    await request<CategoryAssignmentResult>("/category-items/restore", { method: "POST", body: JSON.stringify(undo.value) });
    undo.value = null; selected.value = []; status.value = t("categoryChangesUndone");
    await refresh();
  } catch { error.value = t("categoryUndoFailed"); }
  finally { changing.value = false; }
}
</script>

<template>
  <div class="view-content categories-workspace">
    <header class="page-heading">
      <div class="eyebrow">{{ t("workspace") }}</div>
      <h1>{{ t("categories") }}</h1>
      <p>{{ t("categoriesIntro") }}</p>
    </header>
    <div class="category-layout">
      <aside class="category-manager" :aria-label="t('manageCategories')">
        <div class="section-heading">
          <h2>{{ t("manageCategories") }}</h2>
          <button class="text-button" data-action="create-category" :disabled="busy" @click="editCategory(null)"><AppIcon name="plus" />{{ t("createCategory") }}</button>
        </div>
        <button type="button" class="button secondary category-browser-toggle" data-action="browse-categories" aria-controls="category-browser" :aria-expanded="categoryBrowserOpen" @click="categoryBrowserOpen = !categoryBrowserOpen"><span>{{ t("browseCategories") }}</span><span class="category-browser-current">{{ activeCategoryLabel }}</span><AppIcon name="chevron" /></button>
        <div id="category-browser" class="category-browser" :class="{ expanded: categoryBrowserOpen }">
        <label class="category-search"><span>{{ t("searchCategories") }}</span><input v-model="categorySearch" data-search="categories" type="search" maxlength="100" :placeholder="t('searchCategories')" :disabled="busy" /></label>
        <nav class="category-navigation" :aria-label="t('categoryFilter')">
          <button type="button" data-filter="" :aria-current="filter === '' ? 'true' : undefined" :disabled="busy" @click="chooseFilter('')">{{ t("allCategories") }}</button>
          <button type="button" data-filter="uncategorized" :aria-current="filter === 'uncategorized' ? 'true' : undefined" :disabled="busy" @click="chooseFilter('uncategorized')">{{ t("uncategorized") }}</button>
        </nav>
        <QueryState :pending="categories.isPending.value" :failed="categories.isError.value" @retry="categories.refetch()">
          <ul v-if="categories.data.value?.items.length" class="category-list">
            <li v-for="category in categories.data.value.items" :key="category.id">
              <button class="category-label" type="button" :data-filter="category.id" :aria-current="filter === category.id ? 'true' : undefined" :disabled="busy" @click="chooseFilter(category.id, category.name)"><span>{{ category.name }}</span><span class="category-count">{{ t("categoryProducts", category.item_count) }}</span></button>
              <div class="category-actions">
                <button type="button" class="text-button" data-action="rename-category" :disabled="busy" :aria-label="t('renameCategoryNamed', { name: category.name })" @click="editCategory(category)">{{ t("renameCategory") }}</button>
                <button type="button" class="text-button" data-action="delete-category" :disabled="busy" :aria-label="t('deleteCategoryNamed', { name: category.name })" @click="deleteCategory(category)"><AppIcon name="trash" /></button>
              </div>
            </li>
          </ul>
          <p v-else class="field-note">{{ t(categoryQuery ? "noMatchingCategories" : "noCategories") }}</p>
          <div v-if="categoryOffset > 0 || (categories.data.value?.total ?? 0) > 10" class="pagination category-pagination">
            <span>{{ t("categoryPage", { page: categoryOffset / 10 + 1 }) }}</span>
            <div><button class="text-button" :disabled="categoryOffset === 0 || categories.isFetching.value || busy" @click="categoryOffset = Math.max(0, categoryOffset - 10)">{{ t("previous") }}</button><button class="text-button" data-action="next-categories" :disabled="categoryOffset + 10 >= (categories.data.value?.total ?? 0) || categories.isFetching.value || busy" @click="categoryOffset += 10">{{ t("next") }}</button></div>
          </div>
        </QueryState>
        </div>
      </aside>
      <section class="category-products" :aria-label="t('categorizeProducts')" :aria-busy="busy">
        <h2>{{ t("categorizeProducts") }}</h2>
        <label class="category-search product-search"><span>{{ t("searchItems") }}</span><input v-model="search" data-search="products" type="search" maxlength="100" :placeholder="t('searchItems')" :disabled="busy" /></label>
        <p class="field-note category-assignment-help">{{ t("categoryAssignmentHelp") }}</p>
        <div v-if="selectedItems.length" class="category-batch" role="region" :aria-label="t('targetCategory')">
          <span role="status">{{ t("selectedProducts", selectedItems.length) }}</span>
          <CategoryPicker :label="t('targetCategory')" :display="t('chooseCategory')" :disabled="productsBusy" @choose="assign(selectedItems, $event)" />
          <button type="button" class="text-button" :disabled="busy" @click="selected = []">{{ t("clearSelection") }}</button>
        </div>
        <div v-if="status || undo || error" class="category-feedback">
          <p v-if="error" class="form-alert" role="alert">{{ error }}</p>
          <p v-if="status" role="status" class="field-note">{{ status }}</p>
          <button v-if="undo" type="button" class="text-button" data-action="undo-assignment" :disabled="busy" @click="undoAssignment">{{ t("undoCategoryChange") }}</button>
        </div>
        <QueryState :pending="items.isPending.value" :failed="items.isError.value" @retry="items.refetch()">
          <div v-if="items.data.value?.items.length" class="table-wrap">
            <table>
              <thead>
<tr>
                <th scope="col" class="category-checkbox"><input type="checkbox" :checked="allSelected" :indeterminate="selectedItems.length > 0 && !allSelected" :aria-label="t('selectPageProducts')" :disabled="productsBusy" @change="selectPage" /></th>
                <th scope="col">{{ t("items") }}</th><th scope="col" class="category-wide">{{ t("account") }}</th><th scope="col" class="category-cell">{{ t("category") }}</th><th scope="col" class="category-wide">{{ t("receipts") }}</th><th scope="col" class="category-wide">{{ t("amount") }}</th>
              </tr>
</thead>
              <tbody>
<tr v-for="item in items.data.value.items" :key="identity(item)">
                <td class="category-checkbox"><input v-model="selected" type="checkbox" :value="identity(item)" :aria-label="t('selectCategoryProduct', { name: item.name, account: item.connection_name, currency: item.currency })" :disabled="productsBusy" /></td>
                <td class="category-product-name"><span>{{ item.name }}</span><span class="category-product-context">{{ item.connection_name }} · {{ item.currency }}</span></td><td class="category-wide">{{ item.connection_name }}</td>
                <td class="category-cell">
<div class="category-inline">
                  <span v-if="selectedItems.length">{{ item.category_name ?? t("uncategorized") }}</span>
                  <CategoryPicker v-else :category-id="item.category_id" :display="item.category_name ?? t('uncategorized')" :label="t('changeProductCategory', { name: item.name, account: item.connection_name, currency: item.currency })" :disabled="productsBusy" @choose="assign([item], $event)" />
                  <button v-if="item.category_id && !selectedItems.length" type="button" class="text-button category-remove" data-action="remove-item-category" :aria-label="t('removeProductCategory', { name: item.name, account: item.connection_name, currency: item.currency })" :disabled="productsBusy" @click="assign([item], null)">×</button>
                </div>
</td>
                <td class="category-wide">{{ item.receipt_count }}</td><td class="category-amount category-wide">{{ item.currency }} {{ item.total_amount }}</td>
              </tr>
</tbody>
            </table>
          </div>
          <div v-else class="empty-state"><h3>{{ t("noMatchingItems") }}</h3></div>
          <div v-if="cursors.length > 1 || items.data.value?.next_cursor" class="pagination">
            <span>{{ t("categoryPage", { page: cursors.length }) }}</span>
            <div><button class="button secondary" :disabled="cursors.length === 1 || productsBusy" @click="cursors = cursors.slice(0, -1)">{{ t("previous") }}</button><button class="button secondary" data-action="next-page" :disabled="!items.data.value?.next_cursor || productsBusy" @click="nextPage">{{ t("next") }}</button></div>
          </div>
        </QueryState>
      </section>
    </div>
    <AppDialog v-if="editing" :title="t(editing.category ? 'renameCategory' : 'createCategory')" :busy="busy" @close="editing = null">
      <form @submit.prevent="saveCategory">
        <label for="category-name">{{ t("categoryName") }}</label>
        <input id="category-name" v-model="editing.name" type="text" maxlength="100" required autofocus :disabled="busy" />
        <p v-if="save.isError.value" class="form-alert" role="alert">{{ t("categorySaveFailed") }}</p>
        <div class="dialog-actions"><button class="button secondary" type="button" data-action="cancel" :disabled="busy" @click="editing = null">{{ t("cancel") }}</button><button class="button primary" type="submit" :disabled="!editing.name.trim() || busy">{{ t("save") }}</button></div>
      </form>
    </AppDialog>
    <AppDialog v-if="deleting" :title="t('deleteCategory')" :busy="busy" @close="deleting = null">
      <p>{{ t("deleteCategoryHelp", { name: deleting.name }) }}</p>
      <p v-if="remove.isError.value" class="form-alert" role="alert">{{ t("categoryUpdateFailed") }}</p>
      <div class="dialog-actions"><button class="button secondary" type="button" data-action="cancel" :disabled="busy" @click="deleting = null">{{ t("cancel") }}</button><button class="button danger" type="button" :disabled="busy" @click="confirmDelete">{{ t("deleteCategory") }}</button></div>
    </AppDialog>
  </div>
</template>

<style scoped>
.categories-workspace { max-width: 1440px; }
.category-layout { display: grid; grid-template-columns: 270px minmax(0, 1fr); gap: 32px; align-items: start; }
.category-manager { padding-right: 24px; border-right: 1px solid var(--line); }
.category-manager .section-heading { align-items: start; flex-direction: column; gap: 10px; margin-bottom: 18px; }
.category-manager .section-heading h2 { margin: 0; }
.category-manager .text-button svg { width: 15px; height: 15px; }
.category-browser-toggle { display: none; }
.category-search { display: flex; flex-direction: column; gap: 6px; font-size: 11px; color: var(--muted); }
.category-search input { height: 40px; width: 100%; }
.category-navigation { display: grid; margin-top: 16px; padding-bottom: 10px; border-bottom: 1px solid var(--line); }
.category-navigation button, .category-label { border: 0; border-radius: 6px; background: transparent; color: var(--text); text-align: left; padding: 10px; font-size: 13px; }
.category-navigation button:hover, .category-label:hover { background: var(--surface-toggle); }
.category-navigation [aria-current], .category-label[aria-current] { background: var(--surface-toggle); color: var(--green); font-weight: 600; }
.category-list { list-style: none; padding: 0; margin: 8px 0; }
.category-list li { display: flex; align-items: center; gap: 6px; }
.category-label { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.category-label > span { display: block; }
.category-count { margin-top: 3px; color: var(--muted); font-size: 11px; font-weight: 400; }
.category-actions { display: flex; align-items: center; gap: 10px; }
.category-actions .text-button { font-size: 11px; padding: 8px 0; }
.category-pagination { flex-wrap: wrap; gap: 12px; font-size: 11px; }
.category-pagination > div { display: flex; gap: 14px; }
.category-products { min-width: 0; }
.category-products h2 { margin-top: 0; }
.product-search { margin: 20px 0 10px; }
.category-assignment-help { font-size: 12px; line-height: 1.6; }
.category-batch { position: sticky; top: 12px; z-index: 10; display: flex; flex-wrap: wrap; align-items: center; gap: 14px; padding: 14px 16px; margin: 20px 0; border: 1px solid var(--border-select-compact); border-radius: 10px; background: var(--surface-input); box-shadow: 0 4px 16px var(--shadow-select); }
.category-batch > span { margin-right: auto; font-size: 13px; }
.category-feedback { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin: 14px 0; }
.category-feedback p { margin: 0; }
.category-feedback .form-alert { width: 100%; }
.category-checkbox { width: 38px; }
.category-checkbox input { width: 17px; height: 17px; accent-color: var(--green); }
.category-product-name { white-space: normal; min-width: 120px; overflow-wrap: anywhere; }
.category-product-context { display: none; }
.category-inline { display: flex; align-items: center; gap: 6px; }
.category-remove { font-size: 20px; min-width: 30px; min-height: 34px; }
.category-amount { white-space: nowrap; font-variant-numeric: tabular-nums; }
@media (max-width: 900px) {
  .category-layout { grid-template-columns: minmax(0, 1fr); gap: 28px; }
  .category-manager { padding: 0 0 22px; border-right: 0; border-bottom: 1px solid var(--line); }
  .category-manager .section-heading { flex-direction: row; justify-content: space-between; }
  .category-browser-toggle { display: flex; align-items: center; gap: 12px; width: 100%; min-width: 0; }
  .category-browser-toggle > span:first-child { flex-shrink: 0; }
  .category-browser-current { margin-left: auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 400; color: var(--muted); }
  .category-browser-toggle svg { width: 14px; height: 14px; flex-shrink: 0; }
  .category-browser-toggle[aria-expanded="true"] svg { transform: rotate(180deg); }
  .category-browser:not(.expanded) { display: none; }
  .category-browser.expanded { margin-top: 16px; }
  .category-navigation { grid-template-columns: 1fr 1fr; }
  .category-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 24px; }
}
@media (max-width: 600px) {
  .category-list { grid-template-columns: minmax(0, 1fr); }
  .category-batch { top: 8px; gap: 10px; }
  .category-batch > span { width: 100%; }
  .category-products table { table-layout: fixed; width: 100%; min-width: 0; }
  .category-wide { display: none; }
  .category-products th, .category-products td { padding: 14px 6px; }
  .category-products .category-checkbox { width: 30px; padding-left: 0; }
  .category-product-name { min-width: 0; }
  .category-product-context { display: block; margin-top: 5px; font-size: 10px; color: var(--muted); }
  .category-cell { width: 44%; }
  .category-inline { min-width: 0; }
  .category-inline > span { white-space: normal; overflow-wrap: anywhere; }
  .category-inline :deep(.category-picker) { flex: 1; min-width: 0; }
  .category-inline :deep(.category-picker-trigger) { width: 100%; max-width: 100%; padding: 0 8px; }
  .category-remove { flex-shrink: 0; }
}
</style>
