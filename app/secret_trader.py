"""Тайный торговец в Припяти: скупает информацию (документы/флешки/дневники).

Информация добывается с трупов (обычные люди — дневники, трупы Монолита —
дневники и флешки) и из схронов (дневники). Продать её можно только здесь,
секретному торговцу; на склад фракции и в личный хабар она не идёт.
"""

from __future__ import annotations

from app.game_logic import (
    ActionResult,
    ITEM_LABELS,
    _dead_block_text,
    _is_dead,
    is_traveling,
    travel_block_text,
)
from app.storage import Storage

SECRET_TRADER_LOCATION = "Припять"

INTEL_ITEM_KEYS = ("intel_document", "intel_flash", "intel_diary")

INTEL_SELL_PRICES: dict[str, int] = {
    "intel_document": 10000,
    "intel_flash": 5000,
    "intel_diary": 2000,
}


def secret_trader_available(character) -> bool:
    """Торговец доступен, только когда персонаж стоит в Припяти и не в пути."""
    return character.location == SECRET_TRADER_LOCATION and not is_traveling(character)


def secret_trader_menu_text(storage: Storage, telegram_id: int) -> str:
    """Заголовок меню тайного торговца + количество и цена каждой информации.

    Нулевые количества показываются тоже — игрок видит, что можно добыть.
    """
    character = storage.get_character(telegram_id, refresh_energy=False)
    inventory = character.inventory if character is not None else {}
    lines = [
        "🕵 Бунс — тайный торговец в Припяти.",
        "Скупает информацию, добытую в Зоне: документы, флешки, дневники.",
        "Платит наличными, без лишних вопросов.",
        "",
    ]
    for key in INTEL_ITEM_KEYS:
        count = int(inventory.get(key, 0))
        price = int(INTEL_SELL_PRICES.get(key, 0))
        label = ITEM_LABELS.get(key, key)
        lines.append(f"{label}: у тебя {count} шт. — цена {price} RU/шт")
    return "\n".join(lines)


def _guard_sell(storage: Storage, telegram_id: int) -> ActionResult | None:
    """Общие проверки перед продажей. Возвращает ошибку либо None."""
    character = storage.get_character(telegram_id, refresh_energy=False)
    if character is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(character):
        return ActionResult(False, _dead_block_text())
    blocked = travel_block_text(character)
    if blocked:
        return ActionResult(False, blocked)
    if not secret_trader_available(character):
        return ActionResult(False, "Бунс есть только в Припяти.")
    return None


def sell_intel_item(storage: Storage, telegram_id: int, item_key: str, amount: int = 1) -> ActionResult:
    """Продать один вид информации секретному торговцу."""
    guard = _guard_sell(storage, telegram_id)
    if guard is not None:
        return guard
    if item_key not in INTEL_ITEM_KEYS:
        return ActionResult(False, "Такую информацию торговец не покупает.")
    character = storage.get_character(telegram_id, refresh_energy=False)
    owned = int(character.inventory.get(item_key, 0)) if character is not None else 0
    if amount <= 0 or owned < amount:
        return ActionResult(False, "У тебя нет столько этой информации.")
    if not storage.remove_item(telegram_id, item_key, amount):
        return ActionResult(False, "У тебя нет столько этой информации.")
    price = int(INTEL_SELL_PRICES.get(item_key, 0)) * amount
    storage.change_money(telegram_id, price)
    label = ITEM_LABELS.get(item_key, item_key)
    return ActionResult(True, f"Продано: {label} x{amount} за {price} RU.")


def sell_all_intel(storage: Storage, telegram_id: int) -> ActionResult:
    """Продать всю информацию одним действием. Возвращает список проданного + выручку."""
    guard = _guard_sell(storage, telegram_id)
    if guard is not None:
        return guard
    character = storage.get_character(telegram_id, refresh_energy=False)
    sold: list[str] = []
    total = 0
    for key in INTEL_ITEM_KEYS:
        count = int(character.inventory.get(key, 0)) if character is not None else 0
        if count <= 0:
            continue
        if not storage.remove_item(telegram_id, key, count):
            continue
        price = int(INTEL_SELL_PRICES.get(key, 0)) * count
        storage.change_money(telegram_id, price)
        label = ITEM_LABELS.get(key, key)
        sold.append(f"{label} x{count}")
        total += price
    if not sold:
        return ActionResult(False, "У тебя нет информации на продажу.")
    return ActionResult(True, f"Продано: {', '.join(sold)}. Выручка: {total} RU.")