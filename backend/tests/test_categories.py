from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session
from test_api import client as client
from test_api import engine as engine
from test_api import login
from test_items import seed

from cartalytix.models import (
    CategoryMembership,
    CategoryProduct,
    Discount,
    ProviderConnection,
    RawDocument,
    Receipt,
    ReceiptItem,
    User,
)
from cartalytix.sync import _save_receipt

ORIGIN = {"Origin": "http://testserver"}
URL = "/api/v1/categories"
PRODUCTS = "/api/v1/category-items"


def category(client: TestClient, name: str) -> str:
    response = client.post(URL, json={"name": name}, headers=ORIGIN)
    assert response.status_code == 201
    return str(response.json().get("id"))


def key(item: dict[str, Any]) -> dict[str, Any]:
    return {field: item.get(field) for field in ("connection_id", "name", "currency")}


def assign(
    client: TestClient,
    products: list[dict[str, Any]],
    category_id: str | None,
    *,
    dry_run: bool = False,
) -> Response:
    return client.post(
        f"{PRODUCTS}/assign",
        json={
            "items": [key(item) for item in products],
            "category_id": category_id,
            "dry_run": dry_run,
        },
        headers=ORIGIN,
    )


def test_category_crud_is_private_and_validated(client: TestClient) -> None:
    assert client.get(URL).status_code == 401
    login(client)
    identifier = category(client, "  Food  ")
    assert client.get(URL).json().get("items") == [
        {"id": identifier, "name": "Food", "item_count": 0}
    ]
    assert client.post(URL, json={"name": "food"}, headers=ORIGIN).status_code == 409
    assert client.post(URL, json={"name": "  "}, headers=ORIGIN).status_code == 422
    assert client.post(URL, json={"name": "x" * 101}, headers=ORIGIN).status_code == 422
    assert (
        client.patch(f"{URL}/{identifier}", json={"name": "Groceries"}, headers=ORIGIN)
        .json()
        .get("name")
        == "Groceries"
    )
    client.post(
        "/api/v1/auth/login",
        json={"email": "other@example.com", "password": "Other-secret-password"},
        headers=ORIGIN,
    )
    assert client.get(URL).json().get("items") == []
    assert client.delete(f"{URL}/{identifier}", headers=ORIGIN).status_code == 404
    assert (
        client.patch(f"{URL}/{identifier}", json={"name": "Stolen"}, headers=ORIGIN).status_code
        == 404
    )


def test_preview_batch_replace_and_unassign(client: TestClient, engine: Engine) -> None:
    seed(engine)
    login(client)
    food = category(client, "Food")
    drinks = category(client, "Drinks")
    products = client.get(PRODUCTS).json().get("items")
    assert len(products) == 3
    response = assign(client, products, food, dry_run=True)
    assert response.status_code == 200
    assert response.json() == {
        "matched": 3,
        "changed": 3,
        "protected": 0,
        "dry_run": True,
        "previous_items": [{**key(item), "category_id": None} for item in products],
    }
    assert len(client.get(f"{PRODUCTS}?uncategorized=true").json().get("items")) == 3
    assert assign(client, products, food).json().get("changed") == 3
    assert assign(client, products, food).json().get("changed") == 0
    assert assign(client, products[:1], drinks).json().get("changed") == 1
    assert len(client.get(f"{PRODUCTS}?category_id={food}").json().get("items")) == 2
    assert len(client.get(f"{PRODUCTS}?category_id={drinks}").json().get("items")) == 1
    assert assign(client, products[:1], None).json().get("changed") == 1
    assert len(client.get(f"{PRODUCTS}?uncategorized=true").json().get("items")) == 1
    assert client.get(f"{PRODUCTS}?q=rohlik").json().get("items")[0].get("name") == "Rohlík"
    assert client.delete(f"{URL}/{food}", headers=ORIGIN).status_code == 204
    assert len(client.get(f"{PRODUCTS}?uncategorized=true").json().get("items")) == 3
    assert client.get("/api/v1/items").json().get("total") == 3


def test_batch_rejects_foreign_or_missing_items_atomically(
    client: TestClient,
    engine: Engine,
) -> None:
    seed(engine)
    login(client)
    identifier = category(client, "Food")
    products = client.get(PRODUCTS).json().get("items")
    with Session(engine) as db:
        foreign = db.scalar(
            select(ProviderConnection.id).where(ProviderConnection.name == "Private Lidl")
        )
    invalid = {**key(products[0]), "connection_id": str(foreign)}
    assert assign(client, [products[0], invalid], identifier).status_code == 404
    assert assign(client, products, str(uuid4())).status_code == 404
    assert len(client.get(f"{PRODUCTS}?uncategorized=true").json().get("items")) == 3
    assert assign(client, [products[0], products[0]], identifier).status_code == 422
    assert assign(client, [], identifier).status_code == 422


def test_cursor_does_not_skip_products_when_assigning_each_page(
    client: TestClient,
    engine: Engine,
) -> None:
    seed(engine)
    login(client)
    identifier = category(client, "Food")
    cursor: str | None = None
    seen: list[str] = []
    while True:
        params: dict[str, str | int] = {"limit": 1, "uncategorized": "true"}
        if cursor:
            params["cursor"] = cursor
        response = client.get(PRODUCTS, params=params)
        assert response.status_code == 200
        page = response.json()
        seen.extend(item.get("name") for item in page.get("items"))
        assert assign(client, page.get("items"), identifier).status_code == 200
        cursor = page.get("next_cursor")
        if cursor is None:
            break
    assert seen == ["Káva", "Rohlík", "Sýr"]
    assert client.get(f"{PRODUCTS}?cursor=bad").status_code == 422


def test_categories_survive_reimport_and_apply_to_future_purchases(
    client: TestClient,
    engine: Engine,
) -> None:
    receipts = seed(engine)
    login(client)
    identifier = category(client, "Bakery")
    product = client.get(f"{PRODUCTS}?q=rohlik").json().get("items")[0]
    assert assign(client, [product], identifier).status_code == 200
    with Session(engine) as db:
        receipt = db.get(Receipt, UUID(receipts.get("Lidl Praha-1", "")))
        assert receipt is not None
        connection_id, external_id = receipt.connection_id, receipt.external_id
        raw = RawDocument(
            connection_id=connection_id,
            external_id=external_id,
            payload=b"{}",
            checksum="test",
            parser_version="test",
        )
        db.add(raw)
        db.flush()
        raw_id = raw.id
        db.commit()
    normalized: dict[str, Any] = {
        "date": "2026-03-01T10:00:00+00:00",
        "currency": "CZK",
        "total": "20.00",
        "items": [
            {"name": "Rohlík", "quantity": "10", "unit_price": "2.00", "original_amount": "20.00"}
        ],
    }
    _save_receipt(engine, connection_id, external_id, raw_id, normalized)
    _save_receipt(engine, connection_id, "new-categorized-receipt", raw_id, normalized)
    result = client.get(f"{PRODUCTS}?category_id={identifier}").json().get("items")
    assert len(result) == 1
    assert result[0].get("receipt_count") == 4
    assert result[0].get("category_name") == "Bakery"


def test_product_identity_keeps_connections_and_currencies_separate(
    client: TestClient,
    engine: Engine,
) -> None:
    seed(engine)
    login(client)
    with Session(engine) as db:
        own = db.scalar(select(ProviderConnection).where(ProviderConnection.name == "My Lidl"))
        assert own is not None
        another = ProviderConnection(owner_id=own.owner_id, provider="mixit", name="My Mixit")
        db.add(another)
        db.flush()
        for connection_id, currency in [(another.id, "CZK"), (own.id, "EUR")]:
            receipt = Receipt(
                connection_id=connection_id,
                external_id=str(uuid4()),
                purchased_at=datetime.now(UTC),
                merchant="Shop",
                currency=currency,
                total_amount=1,
            )
            db.add(receipt)
            db.flush()
            db.add(
                ReceiptItem(
                    receipt_id=receipt.id, position=1, name="Rohlík", quantity=1, total_amount=1
                )
            )
        db.commit()
    products = client.get(f"{PRODUCTS}?q=rohlik").json().get("items")
    assert len(products) == 3
    identifier = category(client, "Bakery")
    assign(client, products[:1], identifier)
    assert len(client.get(f"{PRODUCTS}?category_id={identifier}").json().get("items")) == 1


def test_category_listing_has_bounded_queries(client: TestClient, engine: Engine) -> None:
    seed(engine)
    login(client)
    products = client.get(PRODUCTS).json().get("items")
    for index, product in enumerate(products):
        assign(client, [product], category(client, f"Category {index}"))
    statements: list[str] = []

    def capture(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert len(client.get(PRODUCTS).json().get("items")) == 3
        assert len(statements) <= 4
        statements.clear()
        assert len(client.get(URL).json().get("items")) == 3
        assert len(statements) <= 2
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_category_totals_keep_discount_allocation_from_the_complete_receipt(
    client: TestClient,
    engine: Engine,
) -> None:
    receipts = seed(engine)
    login(client)
    with Session(engine) as db:
        receipt_id = UUID(receipts.get("Lidl Praha-1", ""))
        item = db.scalar(
            select(ReceiptItem).where(
                ReceiptItem.receipt_id == receipt_id,
                ReceiptItem.name == "Rohlík",
            )
        )
        assert item is not None
        db.add_all(
            [
                Discount(receipt_id=receipt_id, item_id=item.id, name="Item discount", amount=2),
                Discount(receipt_id=receipt_id, name="Basket discount", amount=3),
            ]
        )
        db.commit()
    page = client.get(PRODUCTS, params={"q": "rohlik", "limit": 1}).json()
    assert len(page.get("items")) == 1
    # First purchase: 20 - 2 - round(3 * 18 / 107.9, 2) = 17.50; other purchases 15 + 8.
    assert page.get("items")[0].get("total_amount") == "40.50"


def test_database_allows_many_categories_but_application_replaces_all(
    client: TestClient,
    engine: Engine,
) -> None:
    seed(engine)
    login(client)
    first, second = category(client, "First"), category(client, "Second")
    item = client.get(PRODUCTS).json().get("items")[0]
    assert assign(client, [item], first).status_code == 200
    with Session(engine) as db:
        product = db.scalar(select(CategoryProduct))
        assert product is not None
        db.add(CategoryMembership(product_id=product.id, category_id=UUID(second)))
        db.commit()
        assert len(db.scalars(select(CategoryMembership)).all()) == 2
    assert assign(client, [item], second).json().get("changed") == 1
    with Session(engine) as db:
        rows = db.scalars(select(CategoryMembership)).all()
        assert len(rows) == 1
        assert rows[0].category_id == UUID(second)


def test_manual_removal_remains_protected_against_mcp(
    client: TestClient,
    engine: Engine,
) -> None:
    from cartalytix.category_routes import assign_category_items
    from cartalytix.category_schemas import CategoryAssignmentInput

    seed(engine)
    login(client)
    identifier = category(client, "Food")
    item = client.get(PRODUCTS).json().get("items")[0]
    assert assign(client, [item], None).status_code == 200
    body = CategoryAssignmentInput(items=[key(item)], category_id=UUID(identifier))
    with Session(engine) as db:
        owner = db.scalar(select(User).where(User.email == "owner@example.com"))
        assert owner is not None
        assert assign_category_items(owner, db, body, source="mcp").model_dump() == {
            "matched": 1,
            "changed": 0,
            "protected": 1,
            "dry_run": False,
        }
        body.overwrite_manual = True
        assert assign_category_items(owner, db, body, source="mcp").changed == 1


def test_batch_queries_do_not_grow_per_item(client: TestClient, engine: Engine) -> None:
    seed(engine)
    login(client)
    identifier = category(client, "Food")
    items = client.get(PRODUCTS).json().get("items")
    statements: list[str] = []

    def capture(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert assign(client, items, identifier).status_code == 200
        assert len(statements) <= 8
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_category_search_paginates_ten_and_matches_accents(client: TestClient) -> None:
    login(client)
    for number in range(25):
        category(client, f"Category {number:02}")
    category(client, "Pečivo")
    page = client.get(f"{URL}/page", params={"q": "category", "limit": 10, "offset": 10})
    assert page.status_code == 200
    assert page.json().get("total") == 25
    assert page.json().get("limit") == 10
    assert page.json().get("offset") == 10
    assert [row.get("name") for row in page.json().get("items")] == [
        f"Category {number:02}" for number in range(10, 20)
    ]
    assert client.get(f"{URL}/page?q=PECIVO").json().get("items")[0].get("name") == "Pečivo"
    assert client.get(f"{URL}/page?offset=30").json().get("items") == []
    assert client.get(f"{URL}/page?limit=101").status_code == 422
    assert client.get(f"{URL}/page?offset=-1").status_code == 422
    client.post(
        "/api/v1/auth/login",
        json={"email": "other@example.com", "password": "Other-secret-password"},
        headers=ORIGIN,
    )
    assert client.get(f"{URL}/page").json().get("total") == 0


def test_assign_returns_actual_previous_categories_for_undo(
    client: TestClient,
    engine: Engine,
) -> None:
    seed(engine)
    login(client)
    first, second = category(client, "First"), category(client, "Second")
    product = client.get(PRODUCTS).json().get("items")[0]
    assign(client, [product], first)
    result = assign(client, [product], second)
    assert result.json().get("previous_items") == [{**key(product), "category_id": first}]


def test_undo_restores_mixed_previous_categories_in_one_batch(
    client: TestClient,
    engine: Engine,
) -> None:
    seed(engine)
    login(client)
    first, second, target = (
        category(client, "First"),
        category(client, "Second"),
        category(client, "Target"),
    )
    products = client.get(PRODUCTS).json().get("items")
    assign(client, products[:1], first)
    assign(client, products[1:2], second)
    result = assign(client, products, target).json()
    expected = [
        {**key(product), "category_id": previous}
        for product, previous in zip(products, [first, second, None], strict=True)
    ]
    assert result.get("previous_items") == expected
    undone = client.post(
        f"{PRODUCTS}/restore",
        json={"items": expected, "expected_category_id": target},
        headers=ORIGIN,
    )
    assert undone.status_code == 200
    assert undone.json().get("changed") == 3
    current = client.get(PRODUCTS).json().get("items")
    assert [item.get("category_id") for item in current] == [first, second, None]


def test_undo_conflict_changes_nothing_and_cannot_use_a_foreign_category(
    client: TestClient,
    engine: Engine,
) -> None:
    seed(engine)
    login(client)
    target, later = category(client, "Target"), category(client, "Later")
    products = client.get(PRODUCTS).json().get("items")
    previous = assign(client, products, target).json().get("previous_items")
    assign(client, products[:1], later)
    response = client.post(
        f"{PRODUCTS}/restore",
        json={"items": previous, "expected_category_id": target},
        headers=ORIGIN,
    )
    assert response.status_code == 409
    assert [item.get("category_id") for item in client.get(PRODUCTS).json().get("items")] == [
        later,
        target,
        target,
    ]
    client.post(
        "/api/v1/auth/login",
        json={"email": "other@example.com", "password": "Other-secret-password"},
        headers=ORIGIN,
    )
    foreign = category(client, "Private")
    login(client)
    invalid = [{**key(products[1]), "category_id": foreign}]
    assert (
        client.post(
            f"{PRODUCTS}/restore",
            json={"items": invalid, "expected_category_id": target},
            headers=ORIGIN,
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"{PRODUCTS}/restore",
            json={"items": invalid * 2, "expected_category_id": target},
            headers=ORIGIN,
        ).status_code
        == 422
    )


def test_category_page_counts_only_requested_page_without_n_plus_one(
    client: TestClient,
    engine: Engine,
) -> None:
    seed(engine)
    login(client)
    identifier = category(client, "Food")
    products = client.get(PRODUCTS).json().get("items")
    assign(client, products, identifier)
    statements: list[str] = []

    def capture(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = client.get(f"{URL}/page?limit=10")
        assert response.status_code == 200
        assert response.json().get("items")[0].get("item_count") == 3
        assert len(statements) <= 3
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_undo_unassignment_restores_category_and_rejects_foreign_products(
    client: TestClient,
    engine: Engine,
) -> None:
    seed(engine)
    login(client)
    identifier = category(client, "Food")
    product = client.get(PRODUCTS).json().get("items")[0]
    assign(client, [product], identifier)
    previous = assign(client, [product], None).json().get("previous_items")
    response = client.post(
        f"{PRODUCTS}/restore",
        json={"items": previous, "expected_category_id": None},
        headers=ORIGIN,
    )
    assert response.status_code == 200
    assert client.get(PRODUCTS).json().get("items")[0].get("category_id") == identifier
    with Session(engine) as db:
        foreign_connection = db.scalar(
            select(ProviderConnection.id).where(ProviderConnection.name == "Private Lidl")
        )
    invalid = [{**key(product), "connection_id": str(foreign_connection), "category_id": None}]
    assert (
        client.post(
            f"{PRODUCTS}/restore",
            json={"items": invalid, "expected_category_id": None},
            headers=ORIGIN,
        ).status_code
        == 409
    )
