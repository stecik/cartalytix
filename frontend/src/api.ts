import type { ThemePreference } from "./theme";

export interface User {
  id: string;
  email: string;
  locale: string;
  receipt_view: "rendered" | "detailed";
  theme: ThemePreference;
  dashboard_period: PresetPeriod;
  dashboard_category_slices: number;
  default_sync_interval_minutes: number | null;
  is_admin: boolean;
  must_change_password: boolean;
}

export interface AdminUser {
  id: string;
  email: string;
  is_admin: boolean;
  is_disabled: boolean;
  must_change_password: boolean;
  created_at: string;
}

/** Generated password; the API returns it only in this one response. */
export interface AdminUserPassword {
  user: AdminUser;
  password: string;
}

export interface Receipt {
  id: string;
  connection_id: string;
  provider: string;
  external_id: string;
  purchased_at: string;
  merchant: string;
  total_amount: string;
  currency: string;
  is_excluded: boolean;
  included_amount: string;
  is_manual: boolean;
}

export interface Shop {
  merchant: string;
  provider: string | null;
  receipt_count: number;
}

export interface ManualReceiptItemInput {
  name: string;
  quantity?: string;
  unit?: string | null;
  total_amount: string;
}

export interface ManualReceiptInput {
  merchant: string;
  provider?: string | null;
  purchased_at: string;
  currency?: string;
  total_amount?: string | null;
  items: ManualReceiptItemInput[];
}

export type ExclusionReason = "receipt" | "item" | "product" | null;
export type ExclusionMode = "included" | "excluded" | "all";
export type ExclusionScope = "all" | "product" | "item";

export type ItemExclusion = {
  name: string;
  currency: string;
  purchased_at: string;
  total_amount: string;
  blocked_by: NonNullable<ExclusionReason>[];
} & (
  | { kind: "product"; receipt_id: null; item_id: null; merchant: null }
  | { kind: "item"; receipt_id: string; item_id: string; merchant: string }
);

export interface ItemExclusionPage {
  items: ItemExclusion[];
  total: number;
  limit: number;
  offset: number;
}

export interface ReceiptItem {
  id: string;
  position: number;
  name: string;
  quantity: string;
  unit: string | null;
  unit_price: string | null;
  total_amount: string;
  original_amount: string | null;
  deposit_amount: string | null;
  printed_net_amount: string | null;
  is_weight: boolean;
  source_code: string | null;
  warranty: string | null;
  is_excluded: boolean;
  included_quantity: string | null;
  exclusion_reason: ExclusionReason;
  included_amount: string;
}

export interface ReceiptDiscount {
  id: string;
  item_id: string | null;
  name: string;
  amount: string;
}

export interface ReceiptDetail extends Receipt {
  rounding_amount: string;
  quality_issues: string[];
  source_format: string;
  items: ReceiptItem[];
  discounts: ReceiptDiscount[];
}

export type ChangeOperation =
  | { kind: "receipt_exclusion"; receipt_id: string; excluded: boolean }
  | { kind: "item_exclusion"; receipt_id: string; item_id: string; excluded: boolean }
  | { kind: "item_included_quantity"; receipt_id: string; item_id: string; included_quantity: string | null }
  | { kind: "product_exclusion"; name: string; currency: string; excluded: boolean };

/** A dry run never writes; its `state` passed back as `expected_state` rejects the write if the data changed since. */
export interface ChangeBatchInput {
  operations: ChangeOperation[];
  dry_run?: boolean;
  idempotency_key?: string;
  expected_state?: string;
}

export interface ChangeAmount {
  currency: string;
  before_amount: string;
  after_amount: string;
  difference: string;
}

export interface ChangeResult {
  index: number;
  kind: ChangeOperation["kind"];
  receipt_id: string | null;
  item_id: string | null;
  name: string;
  currency: string;
  before: boolean | string | null;
  after: boolean | string | null;
  changed: boolean;
}

export interface ChangeReceiptImpact extends ChangeAmount {
  receipt_id: string;
  merchant: string;
  purchased_at: string;
}

export interface ChangeItemImpact extends ChangeAmount {
  receipt_id: string;
  item_id: string;
  name: string;
}

/** Receipts, items and totals list only what counts differently after the batch. */
export interface ChangeBatchOutput {
  batch_id: string | null;
  dry_run: boolean;
  replayed: boolean;
  state: string;
  changes: ChangeResult[];
  receipts: ChangeReceiptImpact[];
  items: ChangeItemImpact[];
  totals: ChangeAmount[];
}

export interface Overview {
  receipt_count: number;
  connection_count: number;
  totals: Array<{ currency: string; amount: string }>;
  latest_receipts: Receipt[];
}

export interface ItemSummary {
  name: string;
  currency: string;
  unit: string | null;
  receipt_count: number;
  quantity: string;
  total_amount: string;
  average_price: string;
  min_price: string;
  max_price: string;
  last_purchased_at: string;
  is_excluded: boolean;
  included_amount: string;
}

export type ItemKey = Pick<ItemSummary, "name" | "currency">;

export interface Category {
  id: string;
  name: string;
  item_count: number;
}

export interface CategoryItemKey {
  connection_id: string;
  name: string;
  currency: string;
}

export interface CategoryPage {
  items: Category[];
  total: number;
  limit: number;
  offset: number;
}

export interface CategoryItemSnapshot extends CategoryItemKey {
  category_id: string | null;
}

export interface CategoryItem extends CategoryItemKey {
  connection_name: string;
  receipt_count: number;
  total_amount: string;
  category_id: string | null;
  category_name: string | null;
  assignment_source: "manual" | "mcp" | null;
}

export interface CategoryItemPage {
  items: CategoryItem[];
  next_cursor: string | null;
}

export interface CategoryAssignmentResult {
  matched: number;
  changed: number;
  protected: number;
  dry_run: boolean;
}

export interface CategoryAssignmentResponse extends CategoryAssignmentResult {
  previous_items: CategoryItemSnapshot[];
}

/** Currency has a fixed length, so the key stays unambiguous for any product name. */
export function itemKey(item: ItemKey): string {
  return `${item.currency}:${item.name}`;
}

export interface ItemTotals {
  items: ItemSummary[];
  totals: Array<{ currency: string; total_amount: string }>;
}

export interface ItemPage {
  items: ItemSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface ItemPurchase {
  item_id: string;
  is_excluded: boolean;
  receipt_id: string;
  purchased_at: string;
  merchant: string;
  quantity: string;
  unit: string | null;
  unit_price: string;
  total_amount: string;
  exclusion_reason: ExclusionReason;
  included_amount: string;
}

export interface ItemDetail extends ItemSummary {
  purchases: ItemPurchase[];
}

export interface Suggestion {
  kind: "item" | "merchant";
  text: string;
  receipt_count: number;
}

export type PresetPeriod = "today" | "month" | "year" | "30d" | "90d" | "12m" | "all";
export type StatsPeriod = PresetPeriod | "custom";
export type StatsGranularity = "day" | "month";

export interface CurrencyStats {
  currency: string;
  amount: string;
  receipt_count: number;
  average: string;
  previous_amount: string | null;
  previous_receipt_count: number | null;
  discount_amount: string;
}

export interface BucketStats {
  /** YYYY-MM for month buckets, YYYY-MM-DD for day buckets. */
  key: string;
  amount: string;
  receipt_count: number;
  merchants: MerchantStats[];
}

export interface MerchantStats {
  merchant: string;
  amount: string;
  receipt_count: number;
}

export interface ItemStats {
  name: string;
  receipt_count: number;
  amount: string;
}

export interface Stats {
  period: StatsPeriod;
  bucket: string | null;
  granularity: StatsGranularity;
  currency: string | null;
  totals: CurrencyStats[];
  buckets: BucketStats[];
  merchants: MerchantStats[];
  frequent_items: ItemStats[];
  top_spend_items: ItemStats[];
  categories: CategoryStats[];
  weekly_hours: number[][];
}

/** One category's share of the period; null id and name stand for uncategorized products. */
export interface CategoryStats {
  category_id: string | null;
  name: string | null;
  purchase_count: number;
  amount: string;
}

export interface Provider {
  code: string;
  name: string;
  status: string;
  connection_count: number;
}

export interface Connection {
  id: string;
  provider: string;
  name: string;
  status: string;
  account_label?: string | null;
  country: string;
  sync_interval_minutes: number | null;
  last_synced_at: string | null;
  next_sync_at: string | null;
  latest_sync: SyncRun | null;
  receipt_count: number;
}

export interface SyncRun {
  id: string;
  connection_id: string;
  status: "pending" | "running" | "completed" | "failed";
  error_code: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  receipts_seen: number;
  receipts_imported: number;
  receipts_skipped: number;
  pages_count: number;
}

export interface LidlLoginResult {
  step: "otp" | "completed";
  flow_id: string | null;
  method: "email" | "sms" | null;
  connection: Connection | null;
}

export interface ReceiptPage {
  items: Receipt[];
  total: number;
  limit: number;
  offset: number;
}

export interface McpAuthorization {
  client_name: string;
  redirect_host: string;
  scopes: string[];
}

export type McpScope =
  | "cartalytix:read"
  | "cartalytix:categories:write"
  | "cartalytix:calculator"
  | "cartalytix:exclusions:write"
  | "cartalytix:receipts:write";

export interface McpGrant {
  id: string;
  client_name: string;
  created_at: string;
  scopes: McpScope[];
  last_used_at: string | null;
}

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code?: string) {
    super(`HTTP ${status}`);
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    let code: string | undefined;
    if (typeof body === "object" && body !== null) {
      if ("error" in body && typeof body.error === "object" && body.error !== null
        && "code" in body.error && typeof body.error.code === "string") code = body.error.code;
      else if ("detail" in body && typeof body.detail === "string") code = body.detail;
    }
    throw new ApiError(response.status, code);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
