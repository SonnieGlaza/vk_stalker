"""Обмен предметами / RU между игроками на одной локации."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from app.game_logic import ActionResult, ITEM_LABELS, h
from app.storage import Storage, utc_now

TRADE_OFFER_META_PREFIX = "trade:offer:"
TRADE_OFFER_TTL_MINUTES = 10
TRADE_MAX_MONEY = 50_000
TRADE_MAX_ITEM_AMOUNT = 50


def _offer_key(from_id: int) -> str:
    return f"{TRADE_OFFER_META_PREFIX}{int(from_id)}"


def _parse_offer(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def get_trade_offer(storage: Storage, from_id: int) -> dict[str, Any] | None:
    offer = _parse_offer(storage.get_meta(_offer_key(from_id)))
    if offer is None:
        return None
    expires = str(offer.get("expires_at") or "")
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(expires)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt <= utc_now():
            storage.delete_meta(_offer_key(from_id))
            return None
    except (TypeError, ValueError):
        storage.delete_meta(_offer_key(from_id))
        return None
    return offer


def create_trade_offer(
    storage: Storage,
    from_id: int,
    to_id: int,
    *,
    kind: str,
    item_key: str | None = None,
    amount: int = 0,
) -> ActionResult:
    from app.player_busy import player_busy_reason

    if from_id == to_id:
        return ActionResult(False, "Нельзя обмениваться с собой.")
    giver = storage.get_character(from_id, refresh_energy=False)
    taker = storage.get_character(to_id, refresh_energy=False)
    if giver is None or taker is None:
        return ActionResult(False, "Один из игроков не найден.")
    if giver.health <= 0 or taker.health <= 0:
        return ActionResult(False, "Обмен возможен только между живыми сталкерами.")
    busy = player_busy_reason(storage, from_id)
    if busy:
        return ActionResult(False, busy)
    busy_to = player_busy_reason(storage, to_id)
    if busy_to:
        return ActionResult(False, f"У второго игрока: {busy_to}")
    if giver.location != taker.location:
        return ActionResult(
            False,
            f"Нужно быть на одной локации. Ты на «{giver.location}», "
            f"{h(taker.nickname)} на «{taker.location}».",
        )

    kind = (kind or "").strip().lower()
    amount = int(amount)
    if kind == "money":
        if amount <= 0 or amount > TRADE_MAX_MONEY:
            return ActionResult(False, f"Сумма: 1…{TRADE_MAX_MONEY} RU.")
        if giver.money < amount:
            return ActionResult(False, "Недостаточно денег.")
        item_key = None
        label = f"{amount} RU"
    elif kind == "item":
        key = (item_key or "").strip()
        if not key:
            return ActionResult(False, "Укажи предмет.")
        if amount <= 0 or amount > TRADE_MAX_ITEM_AMOUNT:
            return ActionResult(False, f"Количество: 1…{TRADE_MAX_ITEM_AMOUNT}.")
        if int(giver.inventory.get(key, 0)) < amount:
            return ActionResult(False, "Недостаточно предмета в инвентаре.")
        item_key = key
        label = f"{ITEM_LABELS.get(key, key)} x{amount}"
    else:
        return ActionResult(False, "Тип обмена: money или item.")

    if get_trade_offer(storage, from_id) is not None:
        return ActionResult(False, "У тебя уже есть активное предложение обмена. Отмени его.")

    expires = utc_now() + timedelta(minutes=TRADE_OFFER_TTL_MINUTES)
    offer = {
        "from_id": int(from_id),
        "to_id": int(to_id),
        "kind": kind,
        "item_key": item_key,
        "amount": amount,
        "from_name": str(giver.nickname),
        "to_name": str(taker.nickname),
        "location": str(giver.location),
        "expires_at": expires.isoformat(),
        "label": label,
    }
    storage.set_meta(_offer_key(from_id), json.dumps(offer, ensure_ascii=False))
    return ActionResult(
        True,
        f"Предложение обмена отправлено {h(taker.nickname)}: {label}.\n"
        f"Ждём подтверждения {TRADE_OFFER_TTL_MINUTES} мин. Локация: «{giver.location}».",
        payload={
            "notify": [
                (
                    to_id,
                    f"🤝 {h(giver.nickname)} предлагает обмен на «{giver.location}»:\n"
                    f"Отдаёт тебе: {label}.\n"
                    f"Прими или отклони в личке бота (кнопки ниже).",
                )
            ],
            "offer": offer,
        },
    )


def cancel_trade_offer(storage: Storage, from_id: int) -> ActionResult:
    offer = get_trade_offer(storage, from_id)
    if offer is None:
        return ActionResult(False, "Активного предложения нет.")
    storage.delete_meta(_offer_key(from_id))
    to_id = int(offer.get("to_id") or 0)
    return ActionResult(
        True,
        "Предложение обмена отменено.",
        payload={
            "notify": [
                (to_id, f"🤝 {h(str(offer.get('from_name') or from_id))} отменил предложение обмена.")
            ]
            if to_id
            else None
        },
    )


def decline_trade_offer(storage: Storage, from_id: int, by_id: int) -> ActionResult:
    offer = get_trade_offer(storage, from_id)
    if offer is None:
        return ActionResult(False, "Предложение уже недействительно.")
    if int(offer.get("to_id") or 0) != int(by_id):
        return ActionResult(False, "Это предложение не для тебя.")
    storage.delete_meta(_offer_key(from_id))
    return ActionResult(
        True,
        "Обмен отклонён.",
        payload={
            "notify": [
                (
                    from_id,
                    f"🤝 {h(str(offer.get('to_name') or by_id))} отклонил(а) обмен ({offer.get('label')}).",
                )
            ]
        },
    )


def accept_trade_offer(storage: Storage, from_id: int, by_id: int) -> ActionResult:
    from app.player_busy import player_busy_reason

    offer = get_trade_offer(storage, from_id)
    if offer is None:
        return ActionResult(False, "Предложение уже недействительно.")
    if int(offer.get("to_id") or 0) != int(by_id):
        return ActionResult(False, "Это предложение не для тебя.")

    giver = storage.get_character(from_id, refresh_energy=False)
    taker = storage.get_character(by_id, refresh_energy=False)
    if giver is None or taker is None:
        storage.delete_meta(_offer_key(from_id))
        return ActionResult(False, "Игрок не найден.")
    if giver.location != taker.location or giver.location != str(offer.get("location") or ""):
        storage.delete_meta(_offer_key(from_id))
        return ActionResult(False, "Обмен сорвался: вы уже не на одной локации.")
    for tid, who in ((from_id, "инициатора"), (by_id, "тебя")):
        busy = player_busy_reason(storage, tid)
        if busy:
            return ActionResult(False, f"Сейчас занят {who}: {busy}")

    kind = str(offer.get("kind") or "")
    amount = int(offer.get("amount") or 0)
    label = str(offer.get("label") or "")

    if kind == "money":
        if not storage.change_money(from_id, -amount):
            storage.delete_meta(_offer_key(from_id))
            return ActionResult(False, "У инициатора не хватило денег.")
        storage.change_money(by_id, amount)
    elif kind == "item":
        item_key = str(offer.get("item_key") or "")
        if not storage.remove_item(from_id, item_key, amount):
            storage.delete_meta(_offer_key(from_id))
            return ActionResult(False, "У инициатора не хватило предмета.")
        storage.add_item(by_id, item_key, amount)
    else:
        storage.delete_meta(_offer_key(from_id))
        return ActionResult(False, "Некорректное предложение.")

    storage.delete_meta(_offer_key(from_id))
    return ActionResult(
        True,
        f"Обмен принят: получено {label}.",
        payload={
            "notify": [
                (
                    from_id,
                    f"🤝 {h(taker.nickname)} принял(а) обмен. Ты отдал(а): {label}.",
                )
            ]
        },
    )


def list_tradeable_inventory(storage: Storage, telegram_id: int) -> list[dict[str, str | int]]:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return []
    rows: list[dict[str, str | int]] = []
    for key, amount in sorted(player.inventory.items()):
        qty = int(amount or 0)
        if qty <= 0:
            continue
        if key.startswith("weapon_") or key.startswith("armor_"):
            continue
        rows.append(
            {
                "item_key": key,
                "title": ITEM_LABELS.get(key, key),
                "amount": qty,
            }
        )
    return rows[:30]
