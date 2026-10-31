from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MAX_CHANGES = 500


class ChangeKind(StrEnum):
    RECEIPT_EXCLUSION = "receipt_exclusion"
    ITEM_EXCLUSION = "item_exclusion"
    ITEM_INCLUDED_QUANTITY = "item_included_quantity"
    PRODUCT_EXCLUSION = "product_exclusion"


class ReceiptExclusionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal[ChangeKind.RECEIPT_EXCLUSION]
    receipt_id: UUID
    excluded: bool


class ItemExclusionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal[ChangeKind.ITEM_EXCLUSION]
    receipt_id: UUID
    item_id: UUID
    excluded: bool


class ItemIncludedQuantityChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal[ChangeKind.ITEM_INCLUDED_QUANTITY]
    receipt_id: UUID
    item_id: UUID
    # NULL counts the whole line again.
    included_quantity: Decimal | None = Field(ge=0, max_digits=18, decimal_places=6)


class ProductExclusionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal[ChangeKind.PRODUCT_EXCLUSION]
    name: str = Field(min_length=1, max_length=1000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    excluded: bool


Change = Annotated[
    ReceiptExclusionChange
    | ItemExclusionChange
    | ItemIncludedQuantityChange
    | ProductExclusionChange,
    Field(discriminator="kind"),
]


class ChangeBatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operations: list[Change] = Field(min_length=1, max_length=MAX_CHANGES)
    dry_run: bool = False
    # A repeated key returns the first result instead of applying the batch again.
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=64)
    # The state returned by a dry run; a different current state rejects the batch.
    expected_state: str | None = Field(default=None, min_length=64, max_length=64)


ChangeValue = bool | str | None


class ChangeOutput(BaseModel):
    index: int
    kind: ChangeKind
    receipt_id: UUID | None
    item_id: UUID | None
    name: str
    currency: str
    before: ChangeValue
    after: ChangeValue
    changed: bool


class AmountChangeOutput(BaseModel):
    before_amount: str
    after_amount: str
    difference: str


class ReceiptImpactOutput(AmountChangeOutput):
    receipt_id: UUID
    merchant: str
    purchased_at: datetime
    currency: str


class ItemImpactOutput(AmountChangeOutput):
    receipt_id: UUID
    item_id: UUID
    name: str
    currency: str


class CurrencyImpactOutput(AmountChangeOutput):
    currency: str


class ChangeBatchOutput(BaseModel):
    batch_id: UUID | None
    dry_run: bool
    replayed: bool = False
    state: str
    changes: list[ChangeOutput]
    receipts: list[ReceiptImpactOutput]
    items: list[ItemImpactOutput]
    totals: list[CurrencyImpactOutput]
