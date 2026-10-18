import type { ThemePreference } from "./theme";

export interface User {
  id: string;
  email: string;
  locale: string;
  receipt_view: "rendered" | "detailed";
  theme: ThemePreference;
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
}

export interface ItemPage {
  items: ItemSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface ItemPurchase {
  receipt_id: string;
  purchased_at: string;
  merchant: string;
  quantity: string;
  unit: string | null;
  unit_price: string;
  total_amount: string;
}

export interface ItemDetail extends ItemSummary {
  purchases: ItemPurchase[];
}

export interface Suggestion {
  kind: "item" | "merchant";
  text: string;
  receipt_count: number;
}

export type StatsPeriod = "12m" | "year" | "all";

export interface CurrencyStats {
  currency: string;
  amount: string;
  receipt_count: number;
  average: string;
  previous_amount: string | null;
  previous_receipt_count: number | null;
  discount_amount: string;
}

export interface MonthStats {
  month: string;
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
  month: string | null;
  currency: string | null;
  totals: CurrencyStats[];
  months: MonthStats[];
  merchants: MerchantStats[];
  frequent_items: ItemStats[];
  top_spend_items: ItemStats[];
  weekly_hours: number[][];
}

export interface Provider {
  code: string;
  name: string;
  status: string;
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
