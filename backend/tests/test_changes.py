from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session
from test_api import client as client
from test_api import engine as engine
from test_api import login
from test_exclusions import ORIGIN, basket

from cartalytix.models import ChangeBatch, ChangeEntry, ProductExclusion, Receipt, ReceiptItem


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


def history(client: TestClient, query: str = "") -> list[dict[str, Any]]:
    response = client.get(f"/api/v1/changes{query}")
    assert response.status_code == 200
    return response.json().get("items", [])


def test_changes_are_recorded_once_and_undone_atomically(
    client: TestClient, engine: Engine
) -> None:
    receipt_id, food_id, tool_id, _ = basket(engine)
    login(client)
    url = f"/api/v1/receipts/{receipt_id}/items/{tool_id}/exclusion"
    assert client.patch(url, json={"excluded": True}, headers=ORIGIN).status_code == 200
    assert client.patch(url, json={"excluded": True}, headers=ORIGIN).status_code == 200
    quantity = {
        "kind": "item_included_quantity",
        "receipt_id": receipt_id,
        "item_id": food_id,
        "included_quantity": "0",
    }
    both = changes(
        client,
        {
            "operations": [
                quantity,
                {"kind": "receipt_exclusion", "receipt_id": receipt_id, "excluded": True},
            ]
        },
    ).json()
    batches = history(client, f"?receipt_id={receipt_id}")
    assert [len(batch.get("entries", [])) for batch in batches] == [2, 1]
    first = batches[1]
    assert first.get("source") == "user"
    assert first.get("entries", [])[0] | {"id": None} == {
        "id": None,
        "kind": "item_exclusion",
        "receipt_id": receipt_id,
        "item_id": tool_id,
        "name": "Spatula",
        "currency": "CZK",
        "before": False,
        "after": True,
        "undone_by_id": None,
    }
    assert first.get("totals", [])[0].get("difference") == "-36.00"

    # The later receipt exclusion made the line exclusion irrelevant, not different: undo it.
    partial = client.post(
        f"/api/v1/changes/{both.get('batch_id')}/undo",
        json={"entry_ids": [batches[0].get("entries", [])[1].get("id")]},
        headers=ORIGIN,
    )
    assert partial.status_code == 200
    with Session(engine) as db:
        assert db.scalar(select(Receipt.is_excluded).where(Receipt.id == UUID(receipt_id))) is False
        assert (
            db.scalar(select(ReceiptItem.included_quantity).where(ReceiptItem.id == UUID(food_id)))
            == 0
        )
    again = client.post(
        f"/api/v1/changes/{both.get('batch_id')}/undo",
        json={"entry_ids": [batches[0].get("entries", [])[1].get("id")]},
        headers=ORIGIN,
    )
    assert again.status_code == 409
    assert again.json().get("error", {}).get("code") == "already_undone"
    retried = client.post(
        f"/api/v1/changes/{both.get('batch_id')}/undo",
        json={"entry_ids": [batches[0].get("entries", [])[1].get("id")], "idempotency_key": "u1"},
        headers=ORIGIN,
    )
    assert retried.status_code == 409
    redo_body = {"idempotency_key": "redo-1"}
    redo = client.post(
        f"/api/v1/changes/{partial.json().get('batch_id')}/undo", json=redo_body, headers=ORIGIN
    )
    replayed = client.post(
        f"/api/v1/changes/{partial.json().get('batch_id')}/undo", json=redo_body, headers=ORIGIN
    )
    assert replayed.json().get("replayed") is True
    assert replayed.json().get("batch_id") == redo.json().get("batch_id")
    assert redo.status_code == 200
    with Session(engine) as db:
        assert db.scalar(select(Receipt.is_excluded).where(Receipt.id == UUID(receipt_id))) is True
    latest = history(client)[0]
    assert latest.get("undo_of_id") == partial.json().get("batch_id")


def test_undo_reports_conflicts_with_later_changes(client: TestClient, engine: Engine) -> None:
    receipt_id, _, tool_id, _ = basket(engine)
    login(client)
    excluded = changes(client, {"operations": [exclude_spatula(receipt_id, tool_id)]}).json()
    changes(client, {"operations": [exclude_spatula(receipt_id, tool_id) | {"excluded": False}]})
    preview = client.post(
        f"/api/v1/changes/{excluded.get('batch_id')}/undo", json={"dry_run": True}, headers=ORIGIN
    )
    assert preview.status_code == 409
    error = preview.json().get("error", {})
    assert error.get("code") == "undo_conflict"
    assert error.get("params", {}).get("conflicts") == [
        {
            "entry_id": history(client)[1].get("entries", [])[0].get("id"),
            "name": "Spatula",
            "expected": True,
            "current": False,
        }
    ]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(ChangeBatch)) == 2
        assert db.scalar(select(func.count()).select_from(ChangeEntry)) == 2
    missing = client.post(f"/api/v1/changes/{uuid4()}/undo", json={}, headers=ORIGIN)
    assert missing.status_code == 404


def test_product_changes_appear_in_receipt_history(client: TestClient, engine: Engine) -> None:
    receipt_id, _, _, foreign = basket(engine)
    login(client)
    product = {"name": "Bread", "currency": "CZK", "excluded": True}
    assert client.patch("/api/v1/items/exclusion", json=product, headers=ORIGIN).status_code == 200
    assert [
        batch.get("entries", [])[0].get("kind")
        for batch in history(client, f"?receipt_id={receipt_id}")
    ] == ["product_exclusion"]
    assert history(client, f"?receipt_id={foreign}") == []
    detail = client.get(f"/api/v1/changes/{history(client)[0].get('id')}")
    assert detail.status_code == 200
    assert detail.json().get("receipts", [])[0].get("receipt_id") == receipt_id
