from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session
from test_api import client as client
from test_api import engine as engine
from test_api import login
from test_exclusions import ORIGIN, basket

from cartalytix.models import ChangeBatch, ProductExclusion, Receipt, ReceiptItem


def changes(client: TestClient, body: dict[str, Any]) -> Response:
    return client.post("/api/v1/changes", json=body, headers=ORIGIN)


def exclude_spatula(receipt_id: str, tool_id: str) -> dict[str, Any]:
    return {
        "kind": "item_exclusion",
        "receipt_id": receipt_id,
        "item_id": tool_id,
        "excluded": True,
    }


def test_dry_run_previews_impact_and_apply_checks_state(client: TestClient, engine: Engine) -> None:
    receipt_id, food_id, tool_id, _ = basket(engine)
    login(client)
    operations = [
        exclude_spatula(receipt_id, tool_id),
        {"kind": "product_exclusion", "name": "Bread", "currency": "CZK", "excluded": True},
    ]
    preview = changes(client, {"operations": operations, "dry_run": True})
    assert preview.status_code == 200
    body = preview.json()
    assert body.get("batch_id") is None
    assert [change.get("changed") for change in body.get("changes")] == [True, True]
    receipt = next(row for row in body.get("receipts") if row.get("receipt_id") == receipt_id)
    assert (receipt.get("before_amount"), receipt.get("after_amount")) == ("81.00", "0.00")
    assert {item.get("item_id"): item.get("difference") for item in body.get("items")} == {
        food_id: "-45.00",
        tool_id: "-36.00",
    }
    with Session(engine) as db:
        assert (
            db.scalar(select(ReceiptItem.is_excluded).where(ReceiptItem.id == UUID(tool_id)))
            is False
        )
        assert db.scalar(select(func.count()).select_from(ProductExclusion)) == 0

    state = body.get("state")
    applied = changes(client, {"operations": operations, "expected_state": state})
    assert applied.status_code == 200
    assert applied.json().get("batch_id") is not None
    assert applied.json().get("totals") == body.get("totals")
    stale = changes(client, {"operations": operations, "expected_state": state})
    assert stale.status_code == 409
    assert stale.json().get("error", {}).get("code") == "changes_conflict"


def test_failed_batch_leaves_nothing_applied(client: TestClient, engine: Engine) -> None:
    receipt_id, food_id, tool_id, foreign = basket(engine)
    login(client)
    response = changes(
        client,
        {
            "operations": [
                exclude_spatula(receipt_id, tool_id),
                {"kind": "receipt_exclusion", "receipt_id": receipt_id, "excluded": True},
                {"kind": "receipt_exclusion", "receipt_id": foreign, "excluded": True},
            ]
        },
    )
    assert response.status_code == 404
    assert response.json().get("error", {}).get("code") == "receipt_not_found"
    assert response.json().get("error", {}).get("params") == {"operation": 2}
    too_many = changes(
        client,
        {
            "operations": [
                exclude_spatula(receipt_id, tool_id),
                {
                    "kind": "item_included_quantity",
                    "receipt_id": receipt_id,
                    "item_id": food_id,
                    "included_quantity": "0.5",
                },
            ]
        },
    )
    assert too_many.status_code == 422
    assert too_many.json().get("error", {}).get("code") == "included_quantity_not_whole"
    duplicate = changes(client, {"operations": [exclude_spatula(receipt_id, tool_id)] * 2})
    assert duplicate.json().get("error", {}).get("params") == {"operation": 1}
    with Session(engine) as db:
        assert (
            db.scalar(select(ReceiptItem.is_excluded).where(ReceiptItem.id == UUID(tool_id)))
            is False
        )
        assert db.scalar(select(Receipt.is_excluded).where(Receipt.id == UUID(receipt_id))) is False
        assert db.scalar(select(func.count()).select_from(ChangeBatch)) == 0


def test_repeated_key_replays_and_noops_store_nothing(client: TestClient, engine: Engine) -> None:
    receipt_id, _, tool_id, _ = basket(engine)
    login(client)
    body = {"operations": [exclude_spatula(receipt_id, tool_id)], "idempotency_key": "agent-1"}
    first = changes(client, body)
    again = changes(client, body)
    assert first.status_code == again.status_code == 200
    assert again.json().get("replayed") is True
    assert again.json().get("batch_id") == first.json().get("batch_id")
    assert again.json().get("totals") == [
        {
            "currency": "CZK",
            "before_amount": "81.00",
            "after_amount": "45.00",
            "difference": "-36.00",
        }
    ]
    reused = changes(
        client,
        {**body, "operations": [{**exclude_spatula(receipt_id, tool_id), "excluded": False}]},
    )
    assert reused.status_code == 409
    assert reused.json().get("error", {}).get("code") == "idempotency_key_reused"
    unchanged = changes(client, {"operations": [exclude_spatula(receipt_id, tool_id)]})
    assert unchanged.json().get("batch_id") is None
    assert unchanged.json().get("changes")[0].get("changed") is False
    assert unchanged.json().get("totals") == []
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(ChangeBatch)) == 1
