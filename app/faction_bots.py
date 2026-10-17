"""Оборонительные боты группировки на базе (склад/гараж). Т1 по умолчанию, апгрейд до Т2."""

from __future__ import annotations

import json
import random
from typing import Any

from app.game_logic import ActionResult, h
from app.storage import Storage

FACTION_BOTS_META_PREFIX = "faction_bots:"
LOCATION_GARRISON_META_PREFIX = "location_garrison:"
FACTION_BOT_UPGRADE_COST = 50_000
FACTION_BOT_COUNT_UPGRADE_COST = 100_000
FACTION_BOT_DEFAULT_COUNT = 3
FACTION_BOT_MAX_COUNT = 5

BOT_T1_WEAPONS: tuple[str, ...] = ("ПМ", "Фора-12", "Обрез")
BOT_T2_WEAPONS: tuple[str, ...] = ("Гадюка-5", "Чейзер-13", "АКС-74У")
BOT_T3_WEAPONS: tuple[str, ...] = ("АК-74", "СПАС-12")
BOT_MONOLITH_WEAPONS: tuple[str, ...] = BOT_T3_WEAPONS
BOT_T1_ARMOR = "Кожаная куртка"
BOT_T2_ARMOR = "Сталкерский бронежилет"
BOT_MONOLITH_ARMOR = "Костюм СЕВА"


def _meta_key(faction: str) -> str:
    return f"{FACTION_BOTS_META_PREFIX}{faction}"


def _garrison_meta_key(location_name: str) -> str:
    return f"{LOCATION_GARRISON_META_PREFIX}{location_name}"


def get_location_garrison(storage: Storage, location_name: str) -> dict[str, Any] | None:
    raw = storage.get_meta(_garrison_meta_key(location_name))
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    faction = str(parsed.get("faction") or "")
    if not faction:
        return None
    return {
        "faction": faction,
        "count": max(0, int(parsed.get("count") or 0)),
        "tier": max(1, min(2, int(parsed.get("tier") or 1))),
    }


def _save_location_garrison(
    storage: Storage,
    location_name: str,
    *,
    faction: str,
    count: int,
    tier: int,
) -> None:
    payload = {
        "faction": faction,
        "count": max(0, int(count)),
        "tier": max(1, min(2, int(tier))),
    }
    storage.set_meta(_garrison_meta_key(location_name), json.dumps(payload, ensure_ascii=False))


def _clear_location_garrison(storage: Storage, location_name: str) -> None:
    storage.delete_meta(_garrison_meta_key(location_name))


def sync_faction_location_garrisons(storage: Storage, faction: str) -> None:
    """Распределить ботов группировки по всем занятым ею точкам."""
    if not faction:
        return
    bots = get_faction_bots(storage, faction)
    total = int(bots.get("count") or 0)
    tier = int(bots.get("tier") or 1)
    controlled = sorted(
        str(loc["name"])
        for loc in storage.get_locations()
        if str(loc.get("controlled_by") or "") == faction
    )
    for loc in storage.get_locations():
        name = str(loc["name"])
        garrison = get_location_garrison(storage, name)
        if garrison and garrison.get("faction") == faction and name not in controlled:
            _clear_location_garrison(storage, name)
    if not controlled or total <= 0:
        for name in controlled:
            _clear_location_garrison(storage, name)
        return
    n = len(controlled)
    if total < n:
        for i, name in enumerate(controlled):
            if i < total:
                _save_location_garrison(storage, name, faction=faction, count=1, tier=tier)
            else:
                _clear_location_garrison(storage, name)
        return
    per_loc = total // n
    remainder = total % n
    for i, name in enumerate(controlled):
        count = per_loc + (1 if i < remainder else 0)
        _save_location_garrison(storage, name, faction=faction, count=count, tier=tier)


def apply_location_control(storage: Storage, location_name: str, faction: str | None) -> None:
    """Сменить контроль точки и перераспределить гарнизоны ботов."""
    loc = storage.get_location(location_name)
    old_faction = str(loc.get("controlled_by") or "") if loc else ""
    storage.set_location_control(location_name, faction)
    _clear_location_garrison(storage, location_name)
    if old_faction and old_faction != (faction or ""):
        sync_faction_location_garrisons(storage, old_faction)
    if faction:
        sync_faction_location_garrisons(storage, faction)


def garrison_defenders_for_location(storage: Storage, location_name: str, faction: str) -> int:
    """Сколько ботов группировки сидит на точке (доп. защитники в штурме)."""
    if not faction:
        return 0
    garrison = get_location_garrison(storage, location_name)
    if garrison is None or garrison.get("faction") != faction:
        sync_faction_location_garrisons(storage, faction)
        garrison = get_location_garrison(storage, location_name)
    if garrison is None or garrison.get("faction") != faction:
        return 0
    return max(0, int(garrison.get("count") or 0))


def get_faction_bots(storage: Storage, faction: str) -> dict[str, Any]:
    raw = storage.get_meta(_meta_key(faction))
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                tier = int(parsed.get("tier") or 1)
                count = int(parsed.get("count") or FACTION_BOT_DEFAULT_COUNT)
                return {
                    "tier": max(1, min(2, tier)),
                    "count": max(1, min(FACTION_BOT_MAX_COUNT, count)),
                }
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    default = {"tier": 1, "count": FACTION_BOT_DEFAULT_COUNT}
    storage.set_meta(_meta_key(faction), json.dumps(default, ensure_ascii=False))
    return default


def bot_weapons_for_tier(tier: int, *, faction: str | None = None) -> tuple[str, ...]:
    if faction == "Монолит":
        return BOT_MONOLITH_WEAPONS
    return BOT_T2_WEAPONS if int(tier) >= 2 else BOT_T1_WEAPONS


def bot_armor_for_tier(tier: int, *, faction: str | None = None) -> str:
    if faction == "Монолит":
        return BOT_MONOLITH_ARMOR
    return BOT_T2_ARMOR if int(tier) >= 2 else BOT_T1_ARMOR


def pick_bot_weapon(tier: int, *, faction: str | None = None) -> str:
    return random.choice(bot_weapons_for_tier(tier, faction=faction))


def build_faction_bots_overview(storage: Storage, faction: str) -> str:
    bots = get_faction_bots(storage, faction)
    tier = int(bots["tier"])
    count = int(bots["count"])
    weapons = ", ".join(bot_weapons_for_tier(tier, faction=faction))
    armor = bot_armor_for_tier(tier, faction=faction)
    lines = [
        f"🤖 Оборонительные боты: {count} шт.",
        f"Тир {tier}: {armor}, оружие — {weapons}.",
    ]
    if faction == "Монолит":
        lines.append("Боты Монолита: оружие Т3 (фикс. снаряга).")
    elif tier < 2:
        lines.append(
            f"Улучшение до Т2 ({BOT_T2_ARMOR}, {', '.join(BOT_T2_WEAPONS)}): "
            f"{FACTION_BOT_UPGRADE_COST:,} RU из казны."
        )
    else:
        lines.append("Боты уже на максимальном тире (Т2).")
    if count < FACTION_BOT_MAX_COUNT:
        lines.append(
            f"Набор +1 бот (макс. {FACTION_BOT_MAX_COUNT}): "
            f"{FACTION_BOT_COUNT_UPGRADE_COST:,} RU из казны."
        )
    garrison_lines: list[str] = []
    for loc in storage.get_locations():
        name = str(loc["name"])
        if str(loc.get("controlled_by") or "") != faction:
            continue
        g = get_location_garrison(storage, name)
        bot_n = int(g.get("count") or 0) if g and g.get("faction") == faction else 0
        if bot_n > 0:
            garrison_lines.append(f"• «{name}»: {bot_n} бот.")
    if garrison_lines:
        lines.append("На занятых точках автоматически:")
        lines.extend(garrison_lines)
    return "\n".join(lines)


def upgrade_faction_bots(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if not player.faction:
        return ActionResult(False, "Сначала выбери группировку.")
    if player.faction == "Монолит":
        return ActionResult(
            False,
            "Боты Монолита уже на снаряжении Т3 — тир не улучшается.",
        )
    leader_id = storage.get_faction_leader_id(player.faction)
    if leader_id is None or int(leader_id) != telegram_id:
        return ActionResult(False, "Улучшать ботов может только лидер группировки.")

    bots = get_faction_bots(storage, player.faction)
    if int(bots["tier"]) >= 2:
        return ActionResult(False, "Боты уже улучшены до Т2.")

    if not storage.withdraw_faction_treasury(player.faction, FACTION_BOT_UPGRADE_COST):
        return ActionResult(
            False,
            f"В казне недостаточно средств. Нужно {FACTION_BOT_UPGRADE_COST:,} RU.",
        )

    bots["tier"] = 2
    storage.set_meta(_meta_key(player.faction), json.dumps(bots, ensure_ascii=False))
    sync_faction_location_garrisons(storage, player.faction)
    return ActionResult(
        True,
        f"🤖 Боты «{player.faction}» улучшены до Т2!\n"
        f"Снаряжение: {BOT_T2_ARMOR}, оружие — {', '.join(BOT_T2_WEAPONS)}.\n"
        f"Списано из казны: {FACTION_BOT_UPGRADE_COST:,} RU.",
    )


def upgrade_faction_bot_count(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if not player.faction:
        return ActionResult(False, "Сначала выбери группировку.")
    leader_id = storage.get_faction_leader_id(player.faction)
    if leader_id is None or int(leader_id) != telegram_id:
        return ActionResult(False, "Набирать ботов может только лидер группировки.")

    bots = get_faction_bots(storage, player.faction)
    count = int(bots["count"])
    if count >= FACTION_BOT_MAX_COUNT:
        return ActionResult(False, f"Уже максимум ботов ({FACTION_BOT_MAX_COUNT}).")

    if not storage.withdraw_faction_treasury(player.faction, FACTION_BOT_COUNT_UPGRADE_COST):
        return ActionResult(
            False,
            f"В казне недостаточно средств. Нужно {FACTION_BOT_COUNT_UPGRADE_COST:,} RU.",
        )

    bots["count"] = count + 1
    storage.set_meta(_meta_key(player.faction), json.dumps(bots, ensure_ascii=False))
    sync_faction_location_garrisons(storage, player.faction)
    return ActionResult(
        True,
        f"🤖 На базе «{player.faction}» теперь {bots['count']} оборонительных ботов "
        f"(−{FACTION_BOT_COUNT_UPGRADE_COST:,} RU из казны).",
    )
