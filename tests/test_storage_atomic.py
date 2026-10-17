"""Unit tests for atomic storage operations."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.storage import Storage


def _memory_storage() -> Storage:
    tmp = tempfile.mkdtemp()
    db = str(Path(tmp) / "test.db")
    snap = str(Path(tmp) / "test.backup.json")
    storage = Storage(db, snapshot_path=snap)
    storage.init_db()
    return storage


def test_complete_auction_sale_success() -> None:
    storage = _memory_storage()
    storage.create_character(1001, "Seller", "male")
    storage.create_character(1002, "Buyer", "male")
    storage.set_faction(1001, "Долг")
    storage.set_faction(1002, "Долг")
    buyer = storage.get_character(1002, refresh_energy=False)
    assert buyer is not None
    money_before = buyer.money
    medkits_before = int(buyer.inventory.get("medkit", 0))
    lot_id = storage.create_auction(
        seller_id=1001,
        faction="Долг",
        item_key="medkit",
        amount=1,
        price=1000,
    )
    ok = storage.complete_auction_sale(
        lot_id,
        buyer_id=1002,
        seller_id=1001,
        price=1000,
        seller_income=900,
        item_key="medkit",
        amount=1,
    )
    assert ok
    buyer_after = storage.get_character(1002, refresh_energy=False)
    assert buyer_after is not None
    assert buyer_after.money == money_before - 1000
    assert buyer_after.inventory.get("medkit") == medkits_before + 1


def test_complete_auction_sale_fails_without_double_charge() -> None:
    storage = _memory_storage()
    storage.create_character(1001, "Seller", "male")
    storage.create_character(1002, "Buyer", "male")
    storage.set_faction(1001, "Долг")
    buyer = storage.get_character(1002, refresh_energy=False)
    assert buyer is not None
    money_before = buyer.money
    lot_id = storage.create_auction(
        seller_id=1001,
        faction="Долг",
        item_key="medkit",
        amount=1,
        price=1000,
    )
    storage.close_auction(lot_id, buyer_id=1002, status="sold")
    ok = storage.complete_auction_sale(
        lot_id,
        buyer_id=1002,
        seller_id=1001,
        price=1000,
        seller_income=900,
        item_key="medkit",
        amount=1,
    )
    assert not ok
    buyer_after = storage.get_character(1002, refresh_energy=False)
    assert buyer_after is not None
    assert buyer_after.money == money_before


def test_cas_meta_value() -> None:
    storage = _memory_storage()
    storage.set_meta("cas:test", json.dumps({"turn_seq": 0}))
    old = storage.get_meta("cas:test")
    assert old is not None
    assert storage.cas_meta_value("cas:test", expected_value=old, new_value=json.dumps({"turn_seq": 1}))
    assert storage.cas_meta_value("cas:test", expected_value=old, new_value=json.dumps({"turn_seq": 99})) is False
    assert json.loads(storage.get_meta("cas:test") or "{}")["turn_seq"] == 1


def test_cancel_auction_and_refund() -> None:
    storage = _memory_storage()
    storage.create_character(2001, "Seller2", "male")
    storage.set_faction(2001, "Долг")
    player = storage.get_character(2001, refresh_energy=False)
    assert player is not None
    vodka_before = int(player.inventory.get("vodka", 0))
    lot_id = storage.create_auction(
        seller_id=2001,
        faction="Долг",
        item_key="vodka",
        amount=2,
        price=500,
    )
    assert storage.cancel_auction_and_refund(
        lot_id,
        seller_id=2001,
        item_key="vodka",
        amount=2,
    )
    player = storage.get_character(2001, refresh_energy=False)
    assert player is not None
    assert player.inventory.get("vodka") == vodka_before + 2


def test_meta_kv_in_snapshot() -> None:
    storage = _memory_storage()
    storage.set_meta("session:test", "alive")
    storage.save_snapshot(force=True)
    payload = json.loads(storage.snapshot_path.read_text(encoding="utf-8"))
    assert payload.get("version") == 3
    keys = {row["key"] for row in payload.get("meta_kv") or []}
    assert "session:test" in keys
