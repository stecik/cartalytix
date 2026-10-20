from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CategoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class CategoryOutput(BaseModel):
    id: UUID
    name: str
    item_count: int = 0


class CategoriesOutput(BaseModel):
    items: list[CategoryOutput]


class CategoryPageOutput(CategoriesOutput):
    total: int
    limit: int
    offset: int


class CategoryKeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    connection_id: UUID
    name: str = Field(min_length=1, max_length=1000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class CategoryItemOutput(CategoryKeyInput):
    connection_name: str
    receipt_count: int
    total_amount: str
    category_id: UUID | None
    category_name: str | None
    assignment_source: Literal["manual", "mcp"] | None


class CategoryItemsOutput(BaseModel):
    items: list[CategoryItemOutput]
    next_cursor: str | None


class CategoryAssignmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[CategoryKeyInput] = Field(min_length=1, max_length=200)
    category_id: UUID | None
    dry_run: bool = False
    overwrite_manual: bool = False

    @model_validator(mode="after")
    def unique_items(self) -> CategoryAssignmentInput:
        if len(set(self.items)) != len(self.items):
            raise ValueError("Duplicate items")
        return self


class CategoryAssignmentOutput(BaseModel):
    matched: int
    changed: int
    protected: int
    dry_run: bool


class CategoryRestoreItemInput(CategoryKeyInput):
    category_id: UUID | None


class CategoryChangeOutput(CategoryAssignmentOutput):
    previous_items: list[CategoryRestoreItemInput]


class CategoryRestoreInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[CategoryRestoreItemInput] = Field(min_length=1, max_length=200)
    expected_category_id: UUID | None

    @model_validator(mode="after")
    def unique_items(self) -> CategoryRestoreInput:
        identities = {(item.connection_id, item.name, item.currency) for item in self.items}
        if len(identities) != len(self.items):
            raise ValueError("Duplicate items")
        return self
