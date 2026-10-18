from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator


class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class Locale(StrEnum):
    EN = "en"
    CS = "cs"


class ReceiptView(StrEnum):
    RENDERED = "rendered"
    DETAILED = "detailed"


class Theme(StrEnum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


class UserOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    locale: str
    receipt_view: str
    theme: Theme


class UserSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locale: Locale | None = None
    receipt_view: ReceiptView | None = None
    theme: Theme | None = None

    @field_validator("theme")
    @classmethod
    def require_theme(cls, value: Theme | None) -> Theme:
        if value is None:
            raise ValueError("Theme must not be null")
        return value


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
    account_label: str | None = None
    receipt_count: int = 0
    sync_interval_minutes: int | None = None
    last_synced_at: datetime | None = None
    next_sync_at: datetime | None = None
    latest_sync: SyncRunOutput | None = None


class ProviderCode(StrEnum):
    LIDL = "lidl"
    MIXIT = "mixit"
    FERMATO = "fermato"
    BOLT = "bolt"


class ConnectionInput(BaseModel):
    name: str = Field(default="", max_length=120)
    sync_interval_minutes: int | None = Field(default=None, ge=60, le=10080)


class ConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    sync_interval_minutes: int | None = Field(default=None, ge=60, le=10080)


class ClearConnectionDataInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pause_sync: bool = True


class LidlAuthorizeInput(BaseModel):
    refresh_token: SecretStr = Field(min_length=1, max_length=32768)


class LidlLoginInput(BaseModel):
    model_config = {"extra": "forbid"}
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=1024)


class BoltLoginInput(BaseModel):
    model_config = {"extra": "forbid"}
    phone: str = Field(pattern=r"^\+[1-9]\d{7,14}$")


class BoltLoginOutput(BaseModel):
    flow_id: str


class BoltVerificationInput(BaseModel):
    model_config = {"extra": "forbid"}
    flow_id: SecretStr = Field(min_length=1, max_length=2048)
    code: SecretStr = Field(min_length=1, max_length=16)


class MixitLoginInput(BaseModel):
    model_config = {"extra": "forbid"}
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=1024)


class FermatoLoginInput(BaseModel):
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


class ReceiptSort(StrEnum):
    PURCHASED_AT_DESC = "purchased_at_desc"
    PURCHASED_AT_ASC = "purchased_at_asc"
    TOTAL_AMOUNT_DESC = "total_amount_desc"
    TOTAL_AMOUNT_ASC = "total_amount_asc"


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


class StatsPeriod(StrEnum):
    LAST_12_MONTHS = "12m"
    THIS_YEAR = "year"
    ALL = "all"


class CurrencyStats(BaseModel):
    currency: str
    amount: str
    receipt_count: int
    average: str
    previous_amount: str | None
    previous_receipt_count: int | None
    discount_amount: str


class MerchantStats(BaseModel):
    merchant: str
    amount: str
    receipt_count: int


class MonthStats(BaseModel):
    month: str
    amount: str
    receipt_count: int
    merchants: list[MerchantStats] = Field(default_factory=list)


class ItemStats(BaseModel):
    name: str
    receipt_count: int
    amount: str


class StatsOutput(BaseModel):
    period: StatsPeriod
    month: str | None
    currency: str | None
    totals: list[CurrencyStats]
    months: list[MonthStats]
    merchants: list[MerchantStats]
    frequent_items: list[ItemStats]
    top_spend_items: list[ItemStats]
    weekly_hours: list[list[int]]


class ItemSort(StrEnum):
    NAME_ASC = "name_asc"
    NAME_DESC = "name_desc"
    RECEIPT_COUNT_DESC = "receipt_count_desc"
    RECEIPT_COUNT_ASC = "receipt_count_asc"
    TOTAL_AMOUNT_DESC = "total_amount_desc"
    TOTAL_AMOUNT_ASC = "total_amount_asc"
    AVERAGE_PRICE_DESC = "average_price_desc"
    AVERAGE_PRICE_ASC = "average_price_asc"
    LAST_PURCHASED_DESC = "last_purchased_desc"
    LAST_PURCHASED_ASC = "last_purchased_asc"


class ItemSummaryOutput(BaseModel):
    name: str
    currency: str
    unit: str | None
    receipt_count: int
    quantity: str
    total_amount: str
    average_price: str
    min_price: str
    max_price: str
    last_purchased_at: datetime


class ItemsOutput(BaseModel):
    items: list[ItemSummaryOutput]
    total: int
    limit: int
    offset: int


class ItemPurchaseOutput(BaseModel):
    receipt_id: UUID
    purchased_at: datetime
    merchant: str
    quantity: str
    unit: str | None
    unit_price: str
    total_amount: str


class ItemDetailOutput(ItemSummaryOutput):
    purchases: list[ItemPurchaseOutput]


class SuggestionKind(StrEnum):
    ITEM = "item"
    MERCHANT = "merchant"


class SuggestionOutput(BaseModel):
    kind: SuggestionKind
    text: str
    receipt_count: int


class SuggestionsOutput(BaseModel):
    items: list[SuggestionOutput]


class McpAuthorizationOutput(BaseModel):
    client_name: str
    redirect_host: str
    scopes: list[str]


class McpConsentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool


class McpConsentOutput(BaseModel):
    redirect_url: str


class McpGrantOutput(BaseModel):
    id: UUID
    client_name: str
    created_at: datetime


class McpGrantsOutput(BaseModel):
    items: list[McpGrantOutput]


class ProviderOutput(BaseModel):
    code: str
    name: str
    status: Literal["available", "not_implemented"] = "not_implemented"


class ProvidersOutput(BaseModel):
    items: list[ProviderOutput]
