from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class ExclusionScope(StrEnum):
    ALL = "all"
    PRODUCT = "product"
    ITEM = "item"


class ExclusionSort(StrEnum):
    NAME_ASC = "name_asc"
    NAME_DESC = "name_desc"
    TOTAL_AMOUNT_ASC = "total_amount_asc"
    TOTAL_AMOUNT_DESC = "total_amount_desc"
    LAST_PURCHASED_ASC = "last_purchased_asc"
    LAST_PURCHASED_DESC = "last_purchased_desc"


ExclusionReason = Literal["receipt", "item", "product"]


class ExclusionOutput(BaseModel):
    kind: Literal["product", "item"]
    name: str
    currency: str
    receipt_id: UUID | None
    item_id: UUID | None
    purchased_at: datetime
    merchant: str | None
    total_amount: str
    blocked_by: list[ExclusionReason]


class ExclusionsOutput(BaseModel):
    items: list[ExclusionOutput]
    total: int
    limit: int
    offset: int
