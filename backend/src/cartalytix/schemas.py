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


class StatsPeriod(StrEnum):
    TODAY = "today"
    THIS_MONTH = "month"
    THIS_YEAR = "year"
    LAST_30_DAYS = "30d"
    LAST_90_DAYS = "90d"
    LAST_12_MONTHS = "12m"
    ALL = "all"
    CUSTOM = "custom"


class UserOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    locale: str
    receipt_view: str
    theme: Theme
    dashboard_period: StatsPeriod
    dashboard_category_slices: int
    is_admin: bool
    must_change_password: bool


class CredentialsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Not needed for the forced change right after signing in with an issued password.
    current_password: str | None = Field(default=None, max_length=1024)
    # Optional only for a voluntary change of the e-mail alone.
    new_password: str | None = Field(default=None, min_length=1, max_length=1024)
    email: EmailStr | None = None


class AdminUserOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    is_admin: bool
    is_disabled: bool
    must_change_password: bool
    created_at: datetime


class AdminUsersOutput(BaseModel):
    items: list[AdminUserOutput]


class AdminUserCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr


class AdminUserUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_disabled: bool


class AdminUserPasswordOutput(BaseModel):
    """Generated password, returned exactly once so the administrator can hand it over."""

    user: AdminUserOutput
    password: str


class UserSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locale: Locale | None = None
    receipt_view: ReceiptView | None = None
    theme: Theme | None = None
    dashboard_period: StatsPeriod | None = None
    # Eight distinguishable category colors plus one segment for the rest.
    dashboard_category_slices: int | None = Field(default=None, ge=3, le=9)

    @field_validator("theme")
    @classmethod
    def require_theme(cls, value: Theme | None) -> Theme:
        if value is None:
            raise ValueError("Theme must not be null")
        return value

    @field_validator("dashboard_period")
    @classmethod
    def require_preset_period(cls, value: StatsPeriod | None) -> StatsPeriod:
        # A custom range has no dates to remember, so only presets can be the default.
        if value is None or value == StatsPeriod.CUSTOM:
            raise ValueError("Dashboard period must be a preset")
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
    total: int
    limit: int
    offset: int


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
    NEJKAFE = "nejkafe"
    BOLT = "bolt"
    WOLT = "wolt"
    KAUFLAND = "kaufland"
    FOODORA = "foodora"
    GLOBUS = "globus"


class FoodoraAuthorizeInput(BaseModel):
    model_config = {"extra": "forbid"}
    access_token: SecretStr = Field(min_length=1, max_length=8192)


class FoodoraMethod(StrEnum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    CALL = "CALL"


class FoodoraLoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=1024)


class FoodoraMethodInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: FoodoraMethod


class FoodoraVerificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: SecretStr = Field(min_length=4, max_length=10)

    @field_validator("code")
    @classmethod
    def require_digits(cls, value: SecretStr) -> SecretStr:
        code = value.get_secret_value()
        if not code.isascii() or not code.isdigit():
            raise ValueError("Invalid verification code")
        return value


class FoodoraLoginOutput(BaseModel):
    step: Literal["method", "otp", "completed"]
    flow_id: str | None = None
    methods: list[FoodoraMethod] = Field(default_factory=list)
    method: FoodoraMethod | None = None
    connection: ConnectionOutput | None = None


class ShoptetShop(StrEnum):
    FERMATO = "fermato"
    NEJKAFE = "nejkafe"


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


class WoltLoginInput(BaseModel):
    model_config = {"extra": "forbid"}
    link: SecretStr = Field(min_length=1, max_length=2048)


class GlobusLoginInput(BaseModel):
    model_config = {"extra": "forbid"}
    username: str = Field(min_length=1, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=1024)

    @field_validator("username")
    @classmethod
    def strip_username(cls, value: str) -> str:
        username = value.strip()
        if not username:
            raise ValueError("Username must not be blank")
        return username


class MixitLoginInput(BaseModel):
    model_config = {"extra": "forbid"}
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=1024)


class ShoptetLoginInput(BaseModel):
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
    is_excluded: bool
    included_amount: str
    is_manual: bool = False


class ManualReceiptItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(default=Decimal(1), gt=0, max_digits=18, decimal_places=6)
    unit: str | None = Field(default=None, max_length=32)
    total_amount: Decimal = Field(max_digits=18, decimal_places=2)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("Name must not be blank")
        return name


class ManualReceiptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    merchant: str = Field(min_length=1, max_length=256)
    # Shop of a known provider (logo, same listing as synced receipts); None for other shops.
    provider: ProviderCode | None = None
    # A time without an offset is local Czech time.
    purchased_at: datetime
    currency: str = Field(default="CZK", pattern=r"^[A-Z]{3}$")
    # Defaults to the sum of the items.
    total_amount: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)
    items: list[ManualReceiptItemInput] = Field(min_length=1, max_length=500)

    @field_validator("merchant")
    @classmethod
    def strip_merchant(cls, value: str) -> str:
        merchant = value.strip()
        if not merchant:
            raise ValueError("Merchant must not be blank")
        return merchant


class ManualReceiptsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receipts: list[ManualReceiptInput] = Field(min_length=1, max_length=100)


class ManualReceiptsOutput(BaseModel):
    items: list[ReceiptOutput]


class ShopOutput(BaseModel):
    merchant: str
    provider: str | None
    receipt_count: int


class ShopsOutput(BaseModel):
    items: list[ShopOutput]


class ExclusionFilter(StrEnum):
    INCLUDED = "included"
    EXCLUDED = "excluded"
    ALL = "all"


class ExclusionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    excluded: bool


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
    is_excluded: bool
    exclusion_reason: Literal["receipt", "item", "product"] | None = None
    included_amount: str = "0.00"


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


class StatsGranularity(StrEnum):
    DAY = "day"
    MONTH = "month"


# A chart bucket: a month (YYYY-MM) or a day (YYYY-MM-DD).
STATS_BUCKET_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?$"


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


class BucketStats(BaseModel):
    key: str
    amount: str
    receipt_count: int
    merchants: list[MerchantStats] = Field(default_factory=list)


class ItemStats(BaseModel):
    name: str
    receipt_count: int
    amount: str


class CategoryStats(BaseModel):
    """Spending of one category; null id and name stand for uncategorized products."""

    category_id: UUID | None
    name: str | None
    purchase_count: int
    amount: str


class StatsOutput(BaseModel):
    period: StatsPeriod
    bucket: str | None
    granularity: StatsGranularity
    currency: str | None
    totals: list[CurrencyStats]
    buckets: list[BucketStats]
    merchants: list[MerchantStats]
    frequent_items: list[ItemStats]
    top_spend_items: list[ItemStats]
    categories: list[CategoryStats]
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
    is_excluded: bool
    included_amount: str


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
    item_id: UUID
    is_excluded: bool
    exclusion_reason: Literal["receipt", "item", "product"] | None
    included_amount: str


class ItemDetailOutput(ItemSummaryOutput):
    purchases: list[ItemPurchaseOutput]


class ItemKeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=1000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class ProductExclusionInput(ItemKeyInput):
    excluded: bool


class ItemTotalsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ItemKeyInput] = Field(max_length=200)
    purchased_from: datetime | None = None
    purchased_to: datetime | None = None
    connection_id: UUID | None = None


class ItemTotalOutput(BaseModel):
    currency: str
    total_amount: str


class ItemTotalsOutput(BaseModel):
    items: list[ItemSummaryOutput]
    totals: list[ItemTotalOutput]


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


class McpScope(StrEnum):
    """Permissions the owner toggles per connection; read is the mandatory base."""

    READ = "cartalytix:read"
    CATEGORIES_WRITE = "cartalytix:categories:write"
    CALCULATOR = "cartalytix:calculator"
    EXCLUSIONS_WRITE = "cartalytix:exclusions:write"
    RECEIPTS_WRITE = "cartalytix:receipts:write"


class McpConsentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool
    # Read is always granted; optional permissions stay off unless the owner enables them.
    scopes: list[McpScope] = Field(default_factory=list, max_length=len(McpScope))


class McpConsentOutput(BaseModel):
    redirect_url: str


class McpGrantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scopes: list[McpScope] = Field(max_length=len(McpScope))

    @field_validator("scopes")
    @classmethod
    def require_read(cls, scopes: list[McpScope]) -> list[McpScope]:
        if McpScope.READ not in scopes:
            raise ValueError("read_required")
        return scopes


class McpGrantOutput(BaseModel):
    id: UUID
    client_name: str
    created_at: datetime
    scopes: list[McpScope]
    last_used_at: datetime | None


class McpGrantsOutput(BaseModel):
    items: list[McpGrantOutput]


class ProviderOutput(BaseModel):
    code: str
    name: str
    status: Literal["available", "not_implemented"] = "not_implemented"
    connection_count: int = 0


class ProvidersOutput(BaseModel):
    items: list[ProviderOutput]
