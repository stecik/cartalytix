export interface User {
  id: string;
  email: string;
  locale?: string;
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
  country: string;
  sync_interval_minutes: number | null;
  last_synced_at: string | null;
  next_sync_at: string | null;
  latest_sync: SyncRun | null;
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
