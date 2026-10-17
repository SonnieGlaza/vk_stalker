"""Лут с мутантов и НПС на тактических полях."""

from __future__ import annotations

import random

from app.game_logic import ITEM_LABELS
from app.storage import Storage

# Мутанты роняют части тел / биоматериал — не человеческий хабар.
# Шансы уменьшены в 3 раза относительно таблицы частей (~94% → ~31%).
MUTANT_LOOT_TABLE: tuple[tuple[str, int, int], ...] = (
    ("mutant_tail", 7, 1),
    ("mutant_claw", 7, 1),
    ("mutant_fang", 6, 1),
    ("mutant_eye", 5, 1),
    ("mutant_hide", 4, 1),
    ("mutant_tendril", 3, 1),
)
# Шансы уменьшены в 3 раза относительно старых таблиц (~92% → ~30%).
NPC_LOOT_TABLE: tuple[tuple[str, int, int], ...] = (
    ("medkit", 7, 1),
    ("ammo_pack", 10, 1),
    ("diesel_can", 4, 1),
    ("gasoline_can", 3, 1),
    ("antirad", 3, 1),
    ("bread", 3, 1),
)


def _roll_from_table(table: tuple[tuple[str, int, int], ...]) -> tuple[str, int] | None:
    roll = random.randint(1, 100)
    acc = 0
    for item_key, chance, amount in table:
        acc += chance
        if roll <= acc:
            return item_key, amount
    return None


def roll_mutant_loot() -> tuple[str, int] | None:
    return _roll_from_table(MUTANT_LOOT_TABLE)


def roll_npc_loot() -> tuple[str, int] | None:
    return _roll_from_table(NPC_LOOT_TABLE)


def grant_combat_loot(
    storage: Storage,
    telegram_id: int,
    *,
    npc: bool,
    monolith: bool = False,
) -> str | None:
    """Выдаёт лут убийце. Возвращает короткую строку для лога или None.

    monolith=True — «труп монолита»: дополнительно дневник и флешка.
    """
    notes: list[str] = []
    drop = roll_npc_loot() if npc else roll_mutant_loot()
    if drop is not None:
        item_key, amount = drop
        if item_key == "diesel_can":
            storage.change_diesel(telegram_id, 5 * amount)
            notes.append("дизель +5")
        elif item_key == "gasoline_can":
            storage.change_gasoline(telegram_id, 5 * amount)
            notes.append("бензин +5")
        else:
            storage.add_item(telegram_id, item_key, amount)
            label = ITEM_LABELS.get(item_key, item_key)
            notes.append(f"{label} x{amount}")
    # Информация: обычный человеческий труп / схрон-носитель — дневник (~10%).
    # Труп монолита — дневник и флешка, каждый со своим 10%-роллом.
    if (npc or monolith) and random.random() * 100 < 10:
        storage.add_item(telegram_id, "intel_diary", 1)
        notes.append("Дневник x1")
    if monolith and random.random() * 100 < 10:
        storage.add_item(telegram_id, "intel_flash", 1)
        notes.append("Флешка x1")
    if not notes:
        return None
    return ", ".join(notes)
