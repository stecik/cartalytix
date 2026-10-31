"""Owner-scoped categories shared by the browser and MCP connector."""

from base64 import b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from dataclasses import dataclass, field
from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import ColumnElement, and_, delete, exists, func, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cartalytix.accounting import accounting, item_included
from cartalytix.category_schemas import (
    CategoriesOutput,
    CategoryAssignmentInput,
    CategoryAssignmentOutput,
    CategoryChangeOutput,
    CategoryInput,
    CategoryItemOutput,
    CategoryItemsOutput,
    CategoryKeyInput,
    CategoryOutput,
    CategoryPageOutput,
    CategoryRestoreInput,
    CategoryRestoreItemInput,
)
from cartalytix.dependencies import Db, Owner
from cartalytix.item_routes import _money
from cartalytix.models import (
    Category,
    CategoryMembership,
    CategoryProduct,
    ProviderConnection,
    Receipt,
    ReceiptItem,
    User,
)
from cartalytix.search import matches, name_matches

router = APIRouter(prefix="/api/v1", tags=["categories"])


def _lock_owner(db: Session, owner_id: UUID) -> None:
    # Serializes first-time product inserts and replacements across web/MCP workers.
    db.execute(select(User.id).where(User.id == owner_id).with_for_update()).one()


def _category(db: Session, owner_id: UUID, category_id: UUID) -> Category:
    row = db.scalar(
        select(Category).where(Category.id == category_id, Category.owner_id == owner_id)
    )
    if row is None:
        raise HTTPException(404, "category_not_found")
    return row


@router.get("/categories", response_model=CategoriesOutput)
def categories(user: Owner, db: Db) -> CategoriesOutput:
    rows = db.execute(
        select(Category.id, Category.name, func.count(CategoryMembership.product_id))
        .outerjoin(CategoryMembership, CategoryMembership.category_id == Category.id)
        .where(Category.owner_id == user.id)
        .group_by(Category.id, Category.name, Category.name_key)
        .order_by(Category.name_key, Category.id)
    )
    return CategoriesOutput(
        items=[
            CategoryOutput(id=identifier, name=name, item_count=count)
            for identifier, name, count in rows
        ]
    )


@router.get("/categories/page", response_model=CategoryPageOutput)
def category_page(
    user: Owner,
    db: Db,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> CategoryPageOutput:
    filters: list[ColumnElement[bool]] = [Category.owner_id == user.id]
    if q and q.strip():
        filters.append(matches(Category.name, q.strip()))
    total = db.scalar(select(func.count()).select_from(Category).where(*filters)) or 0
    page_ids = (
        select(Category.id)
        .where(*filters)
        .order_by(Category.name_key, Category.id)
        .limit(limit)
        .offset(offset)
        .correlate(None)
    )
    rows = db.execute(
        select(Category.id, Category.name, func.count(CategoryMembership.product_id))
        .outerjoin(CategoryMembership, CategoryMembership.category_id == Category.id)
        .where(Category.id.in_(page_ids))
        .group_by(Category.id, Category.name, Category.name_key)
        .order_by(Category.name_key, Category.id)
    )
    return CategoryPageOutput(
        items=[
            CategoryOutput(id=identifier, name=name, item_count=count)
            for identifier, name, count in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


def _save_category(db: Session, row: Category, body: CategoryInput) -> CategoryOutput:
    row.name, row.name_key = body.name, body.name.casefold()
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "category_name_taken") from exc
    count = (
        db.scalar(
            select(func.count())
            .select_from(CategoryMembership)
            .where(CategoryMembership.category_id == row.id)
        )
        or 0
    )
    output = CategoryOutput(id=row.id, name=row.name, item_count=count)
    db.commit()
    return output


@router.post("/categories", response_model=CategoryOutput, status_code=201)
def create_category(user: Owner, db: Db, body: CategoryInput) -> CategoryOutput:
    _lock_owner(db, user.id)
    row = Category(owner_id=user.id)
    db.add(row)
    return _save_category(db, row, body)


@router.patch("/categories/{category_id}", response_model=CategoryOutput)
def update_category(category_id: UUID, user: Owner, db: Db, body: CategoryInput) -> CategoryOutput:
    _lock_owner(db, user.id)
    return _save_category(db, _category(db, user.id, category_id), body)


def delete_category(
    category_id: UUID,
    user: Owner,
    db: Db,
    *,
    source: Literal["manual", "mcp"] = "manual",
    overwrite_manual: bool = False,
) -> None:
    _lock_owner(db, user.id)
    row = _category(db, user.id, category_id)
    products = select(CategoryMembership.product_id).where(CategoryMembership.category_id == row.id)
    if (
        source == "mcp"
        and not overwrite_manual
        and db.scalar(
            select(
                exists().where(
                    CategoryProduct.id.in_(products), CategoryProduct.assignment_source == "manual"
                )
            )
        )
    ):
        raise HTTPException(409, "category_contains_manual_items")
    db.execute(
        update(CategoryProduct)
        .where(CategoryProduct.id.in_(products))
        .values(assignment_source=source)
    )
    db.execute(delete(CategoryMembership).where(CategoryMembership.category_id == row.id))
    db.delete(row)
    db.commit()


@router.delete("/categories/{category_id}", status_code=204)
def delete_category_route(category_id: UUID, user: Owner, db: Db) -> None:
    delete_category(category_id, user, db)


@dataclass
class ProductState:
    product: CategoryProduct
    categories: dict[UUID, str] = field(default_factory=dict)


def _states(
    db: Session,
    owner_id: UUID,
    keys: list[CategoryKeyInput],
) -> dict[CategoryKeyInput, ProductState]:
    if not keys:
        return {}
    rows = db.execute(
        select(CategoryProduct, Category.id, Category.name)
        .join(ProviderConnection, CategoryProduct.connection_id == ProviderConnection.id)
        .outerjoin(CategoryMembership, CategoryMembership.product_id == CategoryProduct.id)
        .outerjoin(
            Category,
            and_(Category.id == CategoryMembership.category_id, Category.owner_id == owner_id),
        )
        .where(
            ProviderConnection.owner_id == owner_id,
            tuple_(
                CategoryProduct.connection_id, CategoryProduct.name_hash, CategoryProduct.currency
            ).in_(
                [
                    (key.connection_id, sha256(key.name.encode()).hexdigest(), key.currency)
                    for key in keys
                ]
            ),
        )
        .order_by(Category.id)
    )
    states: dict[CategoryKeyInput, ProductState] = {}
    for product, category_id, category_name in rows:
        key = CategoryKeyInput(
            connection_id=product.connection_id, name=product.name, currency=product.currency
        )
        state = states.setdefault(key, ProductState(product))
        if category_id is not None:
            state.categories[category_id] = category_name
    return states


def _cursor(key: CategoryKeyInput) -> str:
    return urlsafe_b64encode(key.model_dump_json().encode()).decode().rstrip("=")


def _read_cursor(value: str) -> CategoryKeyInput:
    try:
        payload = b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        return CategoryKeyInput.model_validate_json(payload)
    except (ValueError, Base64Error, ValidationError) as exc:
        raise HTTPException(422, "invalid_category_cursor") from exc


def _membership(category_id: UUID | None) -> ColumnElement[bool]:
    filters = [
        CategoryProduct.connection_id == Receipt.connection_id,
        CategoryProduct.name == ReceiptItem.name,
        CategoryProduct.currency == Receipt.currency,
    ]
    if category_id is not None:
        filters.append(CategoryMembership.category_id == category_id)
    return exists(
        select(CategoryMembership.product_id)
        .join(CategoryProduct, CategoryMembership.product_id == CategoryProduct.id)
        .where(*filters)
    )


@router.get("/category-items", response_model=CategoryItemsOutput)
def category_items(
    user: Owner,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=12000)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    category_id: UUID | None = None,
    uncategorized: bool = False,
) -> CategoryItemsOutput:
    if category_id is not None and uncategorized:
        raise HTTPException(422, "invalid_category_filter")
    # Excluded purchases are never categorized, so only included occurrences are listed.
    included = item_included(user.id)
    filters: list[ColumnElement[bool]] = [ProviderConnection.owner_id == user.id, included]
    if category_id is not None:
        _category(db, user.id, category_id)
        filters.append(_membership(category_id))
    elif uncategorized:
        filters.append(~_membership(None))
    if q and q.strip():
        filters.append(name_matches(ReceiptItem.name, q.strip()))
    if cursor:
        key = _read_cursor(cursor)
        filters.append(
            tuple_(ReceiptItem.name, ProviderConnection.id, Receipt.currency)
            > tuple_(key.name, key.connection_id, key.currency)
        )
    rows = db.execute(
        select(
            ProviderConnection.id,
            ProviderConnection.name,
            ReceiptItem.name,
            Receipt.currency,
            func.count(func.distinct(Receipt.id)),
        )
        .select_from(ReceiptItem)
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
        .where(*filters)
        .group_by(
            ProviderConnection.id, ProviderConnection.name, ReceiptItem.name, Receipt.currency
        )
        .order_by(ReceiptItem.name, ProviderConnection.id, Receipt.currency)
        .limit(limit + 1)
    ).all()
    keys = [
        CategoryKeyInput(connection_id=row[0], name=row[2], currency=row[3]) for row in rows[:limit]
    ]
    totals: dict[CategoryKeyInput, Decimal] = {}
    if keys:
        page_filter = tuple_(Receipt.connection_id, ReceiptItem.name, Receipt.currency).in_(
            [(key.connection_id, key.name, key.currency) for key in keys]
        )
        matching_receipts = (
            select(Receipt.id)
            .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
            .join(ReceiptItem, ReceiptItem.receipt_id == Receipt.id)
            .where(
                ProviderConnection.owner_id == user.id,
                tuple_(Receipt.connection_id, ReceiptItem.name, Receipt.currency).in_(
                    [(key.connection_id, key.name, key.currency) for key in keys]
                ),
            )
        )
        amounts, _ = accounting(user.id, matching_receipts)
        total_rows = db.execute(
            select(
                Receipt.connection_id,
                ReceiptItem.name,
                Receipt.currency,
                func.sum(amounts.c.net_amount),
            )
            .select_from(ReceiptItem)
            .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
            .join(amounts, amounts.c.id == ReceiptItem.id)
            .where(page_filter, included)
            .group_by(Receipt.connection_id, ReceiptItem.name, Receipt.currency)
        )
        totals = {
            CategoryKeyInput(connection_id=connection_id, name=name, currency=currency): total
            for connection_id, name, currency, total in total_rows
        }
    states = _states(db, user.id, keys)
    items: list[CategoryItemOutput] = []
    for key, row in zip(keys, rows[:limit], strict=True):
        state = states.get(key)
        assigned = next(iter(state.categories.items()), (None, None)) if state else (None, None)
        items.append(
            CategoryItemOutput(
                **key.model_dump(),
                connection_name=row[1],
                receipt_count=row[4],
                total_amount=_money(totals.get(key, Decimal(0))),
                category_id=assigned[0],
                category_name=assigned[1],
                assignment_source=cast(Literal["manual", "mcp"], state.product.assignment_source)
                if state
                else None,
            )
        )
    return CategoryItemsOutput(
        items=items, next_cursor=_cursor(keys[-1]) if len(rows) > limit else None
    )


def _assign_category_items(
    user: Owner,
    db: Db,
    body: CategoryAssignmentInput,
    *,
    source: Literal["manual", "mcp"] = "manual",
) -> CategoryChangeOutput:
    if not body.dry_run:
        _lock_owner(db, user.id)
    if body.category_id is not None:
        _category(db, user.id, body.category_id)
    existing = db.execute(
        select(Receipt.connection_id, ReceiptItem.name, Receipt.currency)
        .select_from(ReceiptItem)
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
        .where(
            ProviderConnection.owner_id == user.id,
            tuple_(Receipt.connection_id, ReceiptItem.name, Receipt.currency).in_(
                [(key.connection_id, key.name, key.currency) for key in body.items]
            ),
        )
        .distinct()
    )
    found = {
        CategoryKeyInput(connection_id=connection_id, name=name, currency=currency)
        for connection_id, name, currency in existing
    }
    if found != set(body.items):
        raise HTTPException(404, "category_item_not_found")
    states = _states(db, user.id, body.items)
    previous_items = [
        CategoryRestoreItemInput(
            **key.model_dump(),
            category_id=next(iter(states[key].categories), None) if key in states else None,
        )
        for key in body.items
    ]
    changed, protected = 0, 0
    replacements: list[tuple[CategoryKeyInput, ProductState | None]] = []
    desired = {body.category_id} if body.category_id is not None else set()
    for key in body.items:
        state = states.get(key)
        current = set(state.categories) if state else set()
        if (
            source == "mcp"
            and state
            and state.product.assignment_source == "manual"
            and not body.overwrite_manual
        ):
            protected += 1
            continue
        if current != desired:
            changed += 1
        if current != desired or (state is None or state.product.assignment_source != source):
            replacements.append((key, state))
    if not body.dry_run:
        product_ids = [state.product.id for _, state in replacements if state]
        if product_ids:
            db.execute(
                delete(CategoryMembership).where(CategoryMembership.product_id.in_(product_ids))
            )
        products: list[CategoryProduct] = []
        for key, state in replacements:
            if state:
                product = state.product
                product.assignment_source = source
            else:
                product = CategoryProduct(
                    connection_id=key.connection_id,
                    name=key.name,
                    name_hash=sha256(key.name.encode()).hexdigest(),
                    currency=key.currency,
                    assignment_source=source,
                )
                db.add(product)
            products.append(product)
        db.flush()
        if body.category_id is not None:
            db.add_all(
                CategoryMembership(category_id=body.category_id, product_id=product.id)
                for product in products
            )
        db.commit()
    return CategoryChangeOutput(
        matched=len(body.items),
        changed=changed,
        protected=protected,
        dry_run=body.dry_run,
        previous_items=previous_items,
    )


def assign_category_items(
    user: Owner,
    db: Db,
    body: CategoryAssignmentInput,
    *,
    source: Literal["manual", "mcp"] = "manual",
) -> CategoryAssignmentOutput:
    result = _assign_category_items(user, db, body, source=source)
    return CategoryAssignmentOutput(
        matched=result.matched,
        changed=result.changed,
        protected=result.protected,
        dry_run=result.dry_run,
    )


@router.post("/category-items/assign", response_model=CategoryChangeOutput)
def assign_category_items_route(
    user: Owner,
    db: Db,
    body: CategoryAssignmentInput,
) -> CategoryChangeOutput:
    return _assign_category_items(user, db, body)


@router.post("/category-items/restore", response_model=CategoryAssignmentOutput)
def restore_category_items(
    user: Owner,
    db: Db,
    body: CategoryRestoreInput,
) -> CategoryAssignmentOutput:
    _lock_owner(db, user.id)
    keys = [
        CategoryKeyInput(connection_id=item.connection_id, name=item.name, currency=item.currency)
        for item in body.items
    ]
    states = _states(db, user.id, keys)
    expected = {body.expected_category_id} if body.expected_category_id is not None else set()
    if any(
        key not in states
        or set(states[key].categories) != expected
        or states[key].product.assignment_source != "manual"
        for key in keys
    ):
        raise HTTPException(409, "category_assignment_changed")
    category_ids = {item.category_id for item in body.items if item.category_id is not None}
    if category_ids:
        owned_categories = set(
            db.scalars(
                select(Category.id).where(
                    Category.owner_id == user.id, Category.id.in_(category_ids)
                )
            )
        )
        if owned_categories != category_ids:
            raise HTTPException(409, "category_restore_unavailable")
    db.execute(
        delete(CategoryMembership).where(
            CategoryMembership.product_id.in_([state.product.id for state in states.values()])
        )
    )
    db.add_all(
        CategoryMembership(category_id=item.category_id, product_id=states[key].product.id)
        for key, item in zip(keys, body.items, strict=True)
        if item.category_id is not None
    )
    changed = sum(item.category_id != body.expected_category_id for item in body.items)
    db.commit()
    return CategoryAssignmentOutput(matched=len(keys), changed=changed, protected=0, dry_run=False)
