from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr


class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class UserOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    locale: str


class SyncRunOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    connection_id: UUID
    status: Literal["pending", "running", "completed", "failed"]
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    receipts_seen: int
    receipts_imported: int
    receipts_skipped: int
    pages_count: int


class SyncRunsOutput(BaseModel):
    items: list[SyncRunOutput]


class ConnectionOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    provider: str
    name: str
    country: str
    status: str
    sync_interval_minutes: int | None = None
    last_synced_at: datetime | None = None
    next_sync_at: datetime | None = None
    latest_sync: SyncRunOutput | None = None


class LidlConnectionInput(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"\S")
    sync_interval_minutes: int | None = Field(default=None, ge=60, le=10080)


class LidlConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"\S")
    sync_interval_minutes: int | None = Field(default=None, ge=60, le=10080)


class LidlAuthorizeInput(BaseModel):
    refresh_token: SecretStr = Field(min_length=1, max_length=32768)


class LidlLoginInput(BaseModel):
    model_config = {"extra": "forbid"}
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=1024)


class LidlVerificationInput(BaseModel):
    model_config = {"extra": "forbid"}
    code: SecretStr = Field(min_length=1, max_length=16)


class LidlLoginOutput(BaseModel):
    step: Literal["otp", "completed"]
    flow_id: str | None = None
    method: Literal["email", "sms"] | None = None
    connection: ConnectionOutput | None = None


class SyncInput(BaseModel):
    refresh_existing: bool = False


class ConnectionsOutput(BaseModel):
    items: list[ConnectionOutput]


class ReceiptOutput(BaseModel):
    id: UUID
    connection_id: UUID
    provider: str
    external_id: str
    purchased_at: datetime
    merchant: str
    total_amount: str
    currency: str


class ReceiptsOutput(BaseModel):
    items: list[ReceiptOutput]
    total: int
    limit: int
    offset: int


class ReceiptItemOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    position: int
    name: str
    quantity: Decimal
    unit: str | None
    unit_price: Decimal | None
    total_amount: Decimal
    original_amount: Decimal | None
    deposit_amount: Decimal | None
    printed_net_amount: Decimal | None
    is_weight: bool
    source_code: str | None
    warranty: str | None


class DiscountOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    item_id: UUID | None
    name: str
    amount: Decimal


class ReceiptDetailOutput(ReceiptOutput):
    rounding_amount: Decimal
    quality_issues: list[str]
    source_format: str
    items: list[ReceiptItemOutput]
    discounts: list[DiscountOutput]


class CurrencyTotal(BaseModel):
    currency: str
    amount: str


class OverviewOutput(BaseModel):
    receipt_count: int
    connection_count: int
    totals: list[CurrencyTotal]
    latest_receipts: list[ReceiptOutput]


class ProviderOutput(BaseModel):
    code: str
    name: str
    status: Literal["available", "not_implemented"] = "not_implemented"


class ProvidersOutput(BaseModel):
    items: list[ProviderOutput]
