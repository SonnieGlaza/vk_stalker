"""Постройки на базе группировки (блок 2 бэклога).

Медпункт / Автосервис / Антенна / Бар. Строятся лидером группировки
за казну (RU) и стройматериалы со склада фракции, со временем
распадаются (BUILDING_DECAY_PER_HOUR HP в час).

Состояние построек хранится в meta_kv под ключом
`building:{faction}:{key}` как JSON {"hp": int, "built": bool,
"updated_at": iso-строка UTC}. Соглашение по времени такое же, как в
остальном проекте (см. location_zones/arena_grid): храним iso-строку
datetime.now(timezone.utc).isoformat(), при чтении наивный datetime
трактуем как UTC.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.game_logic import ActionResult, faction_home_base
from app.storage import Storage

BUILDING_KEYS = ("medpunkt", "autoservice", "antenna", "bar")

BUILDING_TITLES: dict[str, str] = {
    "medpunkt": "Медпункт",
    "autoservice": "Автосервис",
    "antenna": "Антенна (радиовышка)",
    "bar": "Бар",
}

# Иконки для статус-текста построек.
BUILDING_ICONS: dict[str, str] = {
    "medpunkt": "🏥",
    "autoservice": "🔧",
    "antenna": "📡",
    "bar": "🍺",
}

MATERIALS_ITEM_KEY = "materials"

BUILDING_COST_MATERIALS = 500
BUILDING_COST_RU = 500_000
BUILDING_MAX_HP = 500
BUILDING_DECAY_PER_HOUR = 3


def _building_meta_key(faction: str, key: str) -> str:
    return f"building:{faction}:{key}"


def _building_state_data(storage: Storage, faction: str, key: str) -> dict | None:
    raw = storage.get_meta(_building_meta_key(faction, key))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def get_building_state(storage: Storage, faction: str, key: str) -> dict:
    """Состояние постройки с ленивым применением распада.

    Пересчитанное состояние сохраняется обратно (updated_at = now),
    поэтому распад применяется за фактически прошедшие интервалы.
    Если записи нет — возвращает {"hp": 0, "built": False} без записи.
    """
    data = _building_state_data(storage, faction, key)
    if data is None:
        return {"hp": 0, "built": False}
    hp = max(0, int(data.get("hp") or 0))
    built = bool(data.get("built"))
    if not built:
        return {"hp": hp, "built": False}

    updated_raw = data.get("updated_at") or ""
    now = datetime.now(timezone.utc)
    try:
        updated_at = datetime.fromisoformat(str(updated_raw))
    except ValueError:
        updated_at = now
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)

    hours_passed = max(0, int((now - updated_at).total_seconds() // 3600))
    if hours_passed > 0:
        hp = max(0, hp - hours_passed * BUILDING_DECAY_PER_HOUR)
        if hp <= 0:
            hp = 0
            built = False
        storage.set_meta(
            _building_meta_key(faction, key),
            json.dumps(
                {"hp": hp, "built": built, "updated_at": now.isoformat()},
                ensure_ascii=False,
            ),
        )
    return {"hp": hp, "built": built}


def faction_building_active(storage: Storage, faction: str | None, key: str) -> bool:
    if faction is None or str(faction).strip() == "" or key not in BUILDING_KEYS:
        return False
    return bool(get_building_state(storage, str(faction), key).get("built"))


def build_building(storage: Storage, telegram_id: int, key: str) -> ActionResult:
    """Построить постройку на базе группировки (только лидер, за казну + материалы)."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Сначала создай персонажа и выбери группировку.")
    if key not in BUILDING_KEYS:
        return ActionResult(False, "Нет такой постройки.")
    if storage.get_faction_leader_id(player.faction) != telegram_id:
        return ActionResult(False, "Строить может только лидер группировки.")

    title = BUILDING_TITLES[key]
    state = get_building_state(storage, player.faction, key)
    if state.get("built"):
        return ActionResult(False, f"«{title}» уже построен(а).")

    if storage.get_faction_warehouse(player.faction).get(MATERIALS_ITEM_KEY, 0) < BUILDING_COST_MATERIALS:
        return ActionResult(
            False,
            f"На складе фракции недостаточно стройматериалов (нужно {BUILDING_COST_MATERIALS}).",
        )
    if not storage.withdraw_faction_treasury(player.faction, BUILDING_COST_RU):
        return ActionResult(False, f"В казне недостаточно средств (нужно {BUILDING_COST_RU} RU).")

    if not storage.change_faction_warehouse_item(player.faction, MATERIALS_ITEM_KEY, -BUILDING_COST_MATERIALS):
        storage.change_faction_treasury(player.faction, BUILDING_COST_RU)
        return ActionResult(False, "Не удалось списать стройматериалы. Средства возвращены в казну.")

    state = {"hp": BUILDING_MAX_HP, "built": True, "updated_at": datetime.now(timezone.utc).isoformat()}
    storage.set_meta(_building_meta_key(player.faction, key), json.dumps(state, ensure_ascii=False))

    return ActionResult(
        True,
        f"«{title}» построен(а) на базе «{faction_home_base(player.faction)}» "
        f"(−{BUILDING_COST_RU} RU, −{BUILDING_COST_MATERIALS} материалов). "
        f"HP постройки: {BUILDING_MAX_HP}/{BUILDING_MAX_HP}.",
    )


def faction_buildings_status_text(storage: Storage, faction: str | None) -> str:
    """Статус всех построек фракции, по строке на каждую (с ценой строительства)."""
    if faction is None or str(faction).strip() == "":
        return "Постройки: нет группировки."
    cost_ru = f"{BUILDING_COST_RU:,}".replace(",", " ")
    lines: list[str] = [
        f"💰 Стоимость постройки: {BUILDING_COST_MATERIALS} стройматериалов + {cost_ru} RU из казны",
    ]
    for key in BUILDING_KEYS:
        icon = BUILDING_ICONS.get(key, "🏚")
        title = BUILDING_TITLES[key]
        state = get_building_state(storage, str(faction), key)
        if state.get("built"):
            lines.append(f"{icon} {title}: построен, HP {state['hp']}/{BUILDING_MAX_HP}")
        else:
            lines.append(f"{icon} {title}: не построен")
    return "\n".join(lines)


def repair_discount_bonus_percent(storage: Storage, faction: str | None) -> int:
    """Доп. скидка на ремонт (у техника), если построен автосервис."""
    return 15 if faction_building_active(storage, faction, "autoservice") else 0


def passive_heal_per_minute(storage: Storage, faction: str | None) -> float:
    """Пассивный хил в HP/минуту (≈6 HP/час), если построен медпункт."""
    return 0.1 if faction_building_active(storage, faction, "medpunkt") else 0.0


def bar_energy_regen_bonus_multiplier(storage: Storage, faction: str | None) -> float:
    """Доп. множитель к регену энергии (+50%), если построен бар."""
    return 0.5 if faction_building_active(storage, faction, "bar") else 0.0