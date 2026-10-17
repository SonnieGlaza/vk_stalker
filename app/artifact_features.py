"""Расширенная механика артефактов: износ, рад, крафт, страховка, UI."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from app.storage import utc_now

if TYPE_CHECKING:
    from app.game_logic import ActionResult, Character, Storage

ARTIFACT_EXTENDED_KEYS: tuple[str, ...] = (
    "artifact_fire",
    "artifact_blood",
    "artifact_crystal",
)

ARTIFACT_EXTENDED_INVENTORY_TO_NAME: dict[str, str] = {
    "artifact_fire": "Арт «Жар»",
    "artifact_blood": "Арт «Кровь»",
    "artifact_crystal": "Арт «Кристалл»",
}

ARTIFACT_EXTENDED_BONUSES: dict[str, dict[str, int]] = {
    "Арт «Жар»": {"power": 2, "hp": 0, "rad_pressure": 3, "cleanse_power": 0},
    "Арт «Кровь»": {"power": 1, "hp": 5, "rad_pressure": 2, "cleanse_power": 0},
    "Арт «Антирад»": {"power": 2, "hp": 0, "rad_pressure": 0, "cleanse_power": 5},
    "Артефакт Зоны": {"power": 2, "hp": 0, "rad_pressure": 1, "cleanse_power": 0},
    "Артефакт": {"power": 2, "hp": 0, "rad_pressure": 1, "cleanse_power": 0},
    "Арт «Сила»": {"power": 1, "hp": 0, "rad_pressure": 1, "cleanse_power": 0},
    "Арт «Живучесть»": {"power": 1, "hp": 10, "rad_pressure": 0, "cleanse_power": 0},
    "Арт «Кристалл»": {"power": 1, "hp": 0, "rad_pressure": 0, "cleanse_power": 3},
    # Финальный артефакт сборки из лаб (Предел-1/2/3). Никогда не чинится —
    # см. ARTIFACT_NO_REPAIR_NAMES и repair_equipped_artifacts.
    "Артефакт «Предел»": {
        "cleanse_power": 15,
        "armor": 10,
        "energy_regen": 10,
        "hp_regen": 10,
        "psy_resist": 100,
    },
}

# Артефакты, которые нельзя восстановить ремонтом — только износ без восстановления.
ARTIFACT_NO_REPAIR_NAMES: frozenset[str] = frozenset({"Артефакт «Предел»"})

ARTIFACT_WEAR_SLOT_SUFFIX = "_wear"
ARTIFACT_WEAR_MIN = 0
ARTIFACT_WEAR_MAX = 100
ARTIFACT_WEAR_QUEST_MIN = 1
ARTIFACT_WEAR_QUEST_MAX = 3

ARTIFACT_INSURANCE_COST_RU = 8000
ARTIFACT_INSURANCE_DAYS = 7
ARTIFACT_INSURANCE_META = "artifact_insurance_until"

ARTIFACT_EQUIP_COOLDOWN_HOURS = 1
ARTIFACT_EQUIP_COOLDOWN_PREFIX = "artifact_equip_cd:"

ARTIFACT_CRAFT_COST_RU = 750
ARTIFACT_CRAFT_SUCCESS_PERCENT = 15
ARTIFACT_CRAFT_REWARD_POOL: tuple[str, ...] = ("artifact_power", "artifact_vitality")

ARTIFACT_FACTION_SELL_TAX_PERCENT = 10
ARTIFACT_DYNAMIC_SELL_META_PREFIX = "trader_art_sold:"
ARTIFACT_DYNAMIC_SELL_FLOOR = 0.55
ARTIFACT_DYNAMIC_SELL_STEP = 0.03

ARTIFACT_HOTSPOT_META = "artifact_hotspot"
ARTIFACT_HOTSPOT_HOURS = 24
ARTIFACT_HOTSPOT_MULT = 1.5

ARTIFACT_COOP_BONUS_MULT = 1.12
ARTIFACT_DEEP_HUNT_MAX_MOVES = 12
ARTIFACT_DEEP_HUNT_DROP_MULT = 1.35
ARTIFACT_DEEP_HUNT_CIRCLES_DELTA = -1

JUNK_COLLECTION_META = "junk_types_seen"
ARTIFACT_REPAIR_COST_PER_PERCENT = 35
MAX_ARTIFACT_EQUIP_SLOTS = 3

DETECTOR_VALUABLE_TIER: dict[str, frozenset[str]] = {
    "detector_otklik": frozenset({"artifact_power"}),
    "detector_medved": frozenset({"artifact_power", "artifact_vitality"}),
    "detector_veles": frozenset({"artifact_power", "artifact_vitality", "artifact_fire", "artifact_blood"}),
    "detector_svarog": frozenset(
        {
            "artifact",
            "artifact_power",
            "artifact_vitality",
            "artifact_antirad",
            "artifact_fire",
            "artifact_blood",
            "artifact_crystal",
        }
    ),
}


def _gl():
    from app import game_logic as gl

    return gl


def _equipped_names(character: Character) -> list[str]:
    gl = _gl()
    names: list[str] = []
    for key in gl.ARTIFACT_EQUIP_SLOT_KEYS:
        name = str(character.equipment.get(key, "Нет") or "Нет")
        if name and name != "Нет":
            names.append(name)
    return names


def merge_extended_artifact_catalog() -> None:
    gl = _gl()
    gl.ARTIFACT_INVENTORY_TO_NAME.update(ARTIFACT_EXTENDED_INVENTORY_TO_NAME)
    gl.ARTIFACT_NAME_TO_INVENTORY.update({v: k for k, v in ARTIFACT_EXTENDED_INVENTORY_TO_NAME.items()})
    gl.ARTIFACT_DROP_KEYS = tuple(dict.fromkeys(tuple(gl.ARTIFACT_DROP_KEYS) + ARTIFACT_EXTENDED_KEYS))
    gl.ARTIFACT_ALL_KEYS = gl.ARTIFACT_DROP_KEYS + gl.ARTIFACT_JUNK_KEYS
    for name, stats in ARTIFACT_EXTENDED_BONUSES.items():
        if name not in gl.ARTIFACT_EQUIP_BONUSES:
            gl.ARTIFACT_EQUIP_BONUSES[name] = {"power": stats.get("power", 0), "hp": stats.get("hp", 0)}
    for key, name in ARTIFACT_EXTENDED_INVENTORY_TO_NAME.items():
        sell = 3500 if key == "artifact_crystal" else 1800 if key == "artifact_fire" else 1400
        if key not in gl.SHOP_ITEMS:
            gl.SHOP_ITEMS[key] = {"name": name, "buy_price": 0, "sell_price": sell}
        gl.ITEM_LABELS[key] = name
    trophies = list(gl.TRADER_SELL_CATALOG.get("trophies", ()))
    for key in ARTIFACT_EXTENDED_KEYS:
        if key not in trophies:
            trophies.append(key)
    gl.TRADER_SELL_CATALOG["trophies"] = tuple(trophies)
    for loc, rows in list(gl.ARTIFACT_LOCATION_SPAWNS.items()):
        extra = list(rows)
        if loc in ("Темная долина", "Радар", "Болото"):
            extra.extend((("artifact_fire", 2.5), ("artifact_blood", 2.0)))
        if loc == "Радар":
            extra.append(("artifact_crystal", 1.5))
        gl.ARTIFACT_LOCATION_SPAWNS[loc] = tuple(extra)


def all_valuable_artifact_keys() -> frozenset[str]:
    gl = _gl()
    return frozenset(gl.ARTIFACT_DROP_KEYS)


def artifact_bonus_entry(display_name: str) -> dict[str, int]:
    gl = _gl()
    base = dict(gl.ARTIFACT_EQUIP_BONUSES.get(display_name, {}))
    ext = ARTIFACT_EXTENDED_BONUSES.get(display_name, {})
    for k, v in ext.items():
        base[k] = int(v)
    return base


def _wear_field(slot_key: str) -> str:
    return f"{slot_key}{ARTIFACT_WEAR_SLOT_SUFFIX}"


def get_artifact_wear(character: Character, slot_key: str) -> int:
    raw = character.equipment.get(_wear_field(slot_key), ARTIFACT_WEAR_MAX)
    try:
        return max(ARTIFACT_WEAR_MIN, min(ARTIFACT_WEAR_MAX, int(raw)))
    except (TypeError, ValueError):
        return ARTIFACT_WEAR_MAX


def set_artifact_wear(storage: Storage, telegram_id: int, slot_key: str, wear: int) -> None:
    storage.update_equipment_fields(
        telegram_id, {_wear_field(slot_key): max(ARTIFACT_WEAR_MIN, min(ARTIFACT_WEAR_MAX, int(wear)))}
    )


def wear_multiplier(wear: int) -> float:
    return max(0.0, min(1.0, wear / float(ARTIFACT_WEAR_MAX)))


def scaled_artifact_power_bonus(character: Character) -> int:
    total = 0
    gl = _gl()
    for slot_key in gl.ARTIFACT_EQUIP_SLOT_KEYS:
        name = str(character.equipment.get(slot_key, "Нет") or "Нет")
        if not name or name == "Нет":
            continue
        stats = artifact_bonus_entry(name)
        total += int(round(int(stats.get("power", 0)) * wear_multiplier(get_artifact_wear(character, slot_key))))
    return total


def scaled_artifact_hp_bonus(character: Character) -> int:
    total = 0
    gl = _gl()
    for slot_key in gl.ARTIFACT_EQUIP_SLOT_KEYS:
        name = str(character.equipment.get(slot_key, "Нет") or "Нет")
        if not name or name == "Нет":
            continue
        stats = artifact_bonus_entry(name)
        total += int(round(int(stats.get("hp", 0)) * wear_multiplier(get_artifact_wear(character, slot_key))))
    return total


def compute_equipped_artifact_rad_profile(character: Character) -> tuple[int, int, int, int]:
    pressure = 0
    cleanse_power = 0
    gl = _gl()
    for slot_key in gl.ARTIFACT_EQUIP_SLOT_KEYS:
        name = str(character.equipment.get(slot_key, "Нет") or "Нет")
        if not name or name == "Нет":
            continue
        mult = wear_multiplier(get_artifact_wear(character, slot_key))
        stats = artifact_bonus_entry(name)
        pressure += int(round(int(stats.get("rad_pressure", 0)) * mult))
        cleanse_power += int(round(int(stats.get("cleanse_power", 0)) * mult))
    interval = max(3, 10 - cleanse_power)
    cleanse_amount = 1 if cleanse_power > 0 else 0
    return pressure, cleanse_amount, interval, cleanse_power


def apply_passive_artifact_radiation(radiation: int, minutes_passed: int, character: Character) -> int:
    pressure, cleanse_amount, interval, _ = compute_equipped_artifact_rad_profile(character)
    rad = radiation
    if pressure > 0:
        pressure_ticks = minutes_passed // 10
        if pressure_ticks > 0:
            rad = min(100, rad + pressure_ticks * pressure)
    if cleanse_amount > 0 and interval > 0:
        cleanse_ticks = minutes_passed // interval
        if cleanse_ticks > 0:
            rad = max(0, rad - cleanse_ticks * cleanse_amount)
    return rad


def apply_quest_artifact_wear(storage: Storage, telegram_id: int) -> list[str]:
    gl = _gl()
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return []
    notes: list[str] = []
    for slot_key in gl.ARTIFACT_EQUIP_SLOT_KEYS:
        name = str(player.equipment.get(slot_key, "Нет") or "Нет")
        if not name or name == "Нет":
            continue
        loss = random.randint(ARTIFACT_WEAR_QUEST_MIN, ARTIFACT_WEAR_QUEST_MAX)
        cur = get_artifact_wear(player, slot_key)
        new_wear = cur - loss
        if new_wear <= 0:
            storage.set_equipment_item(telegram_id, slot_key, "Нет")
            storage.update_equipment_fields(telegram_id, {_wear_field(slot_key): ARTIFACT_WEAR_MAX})
            inv_key = gl.ARTIFACT_NAME_TO_INVENTORY.get(name)
            if inv_key:
                storage.add_item(telegram_id, inv_key, 1)
            notes.append(f"💥 {name} рассыпался от износа ({cur}% → 0%).")
            storage.sync_gear_power(telegram_id)
            updated = storage.get_character(telegram_id, refresh_energy=False)
            if updated:
                max_hp = gl.effective_max_health(updated)
                if updated.health > max_hp:
                    storage.change_health(telegram_id, max_hp - updated.health, max_health=max_hp)
        else:
            set_artifact_wear(storage, telegram_id, slot_key, new_wear)
            notes.append(f"⚙️ {name}: износ {cur}% → {new_wear}%.")
    return notes


def repair_equipped_artifacts(storage: Storage, telegram_id: int) -> ActionResult:
    gl = _gl()
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return gl.ActionResult(False, "Сначала создай персонажа.")
    total_missing = 0
    for slot_key in gl.ARTIFACT_EQUIP_SLOT_KEYS:
        name = str(player.equipment.get(slot_key, "Нет") or "Нет")
        if not name or name == "Нет" or name in ARTIFACT_NO_REPAIR_NAMES:
            continue
        wear = get_artifact_wear(player, slot_key)
        if wear < ARTIFACT_WEAR_MAX:
            total_missing += ARTIFACT_WEAR_MAX - wear
    if total_missing <= 0:
        return gl.ActionResult(False, "Экипированные артефакты не нуждаются в ремонте.")
    cost = total_missing * ARTIFACT_REPAIR_COST_PER_PERCENT
    if not storage.change_money(telegram_id, -cost):
        return gl.ActionResult(False, f"Нужно {cost} RU для ремонта артефактов.")
    for slot_key in gl.ARTIFACT_EQUIP_SLOT_KEYS:
        name = str(player.equipment.get(slot_key, "Нет") or "Нет")
        if name and name != "Нет" and name not in ARTIFACT_NO_REPAIR_NAMES:
            set_artifact_wear(storage, telegram_id, slot_key, ARTIFACT_WEAR_MAX)
    return gl.ActionResult(True, f"Артефакты восстановлены до 100% за {cost} RU.")


# --- Сборка финального артефакта «Предел» из трёх частей лаб ---
PREDEL_PART_KEYS: tuple[str, ...] = ("artifact_predel1", "artifact_predel2", "artifact_predel3")
PREDEL_FINAL_KEY = "artifact_predel"


def has_all_predel_parts(storage: Storage, telegram_id: int) -> bool:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return False
    return all(int(player.inventory.get(key, 0)) >= 1 for key in PREDEL_PART_KEYS)


def combine_predel_artifact(storage: Storage, telegram_id: int) -> ActionResult:
    """Собрать «Артефакт «Предел»» из трёх частей (Предел-1/2/3)."""
    gl = _gl()
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return gl.ActionResult(False, "Сначала создай персонажа.")
    missing = [key for key in PREDEL_PART_KEYS if int(player.inventory.get(key, 0)) < 1]
    if missing:
        labels = [gl.ITEM_LABELS.get(key, key) for key in missing]
        return gl.ActionResult(
            False,
            f"Нужны все три части: «Предел-1», «Предел-2» и «Предел-3». Не хватает: {', '.join(labels)}.",
        )
    for key in PREDEL_PART_KEYS:
        if not storage.remove_item(telegram_id, key, 1):
            return gl.ActionResult(False, "Части артефакта не нашлись в инвентаре.")
    storage.add_item(telegram_id, PREDEL_FINAL_KEY, 1)
    label = gl.ITEM_LABELS.get(PREDEL_FINAL_KEY, PREDEL_FINAL_KEY)
    return gl.ActionResult(
        True,
        f"🧩 Части слиты: собран «{label}». Он в инвентаре — экипируй через ⚙️ Экипировка.",
    )


def build_predel_assembly_hint(storage: Storage, telegram_id: int) -> str:
    """Подсказка в меню артов: есть ли части и готов ли сбор."""
    gl = _gl()
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ""
    have = [key for key in PREDEL_PART_KEYS if int(player.inventory.get(key, 0)) >= 1]
    if not have:
        return ""
    lines = [f"🧩 Части «Предела»: {len(have)}/3 ({", ".join(gl.ITEM_LABELS.get(k, k) for k in have)})."]
    if len(have) == 3:
        lines.append("Можно собрать «Артефакт «Предел»» — жми кнопку сборки.")
    else:
        lines.append("Собери все три — в меню техника появится сборка.")
    lines.append("")
    return "\n".join(lines)


def craft_artifact_from_junk(storage: Storage, telegram_id: int, key_a: str, key_b: str) -> ActionResult:
    gl = _gl()
    key_a = str(key_a)
    key_b = str(key_b)
    if key_a not in gl.ARTIFACT_JUNK_KEYS or key_b not in gl.ARTIFACT_JUNK_KEYS:
        return gl.ActionResult(False, "Нужны два разных мусорных артефакта.")
    if key_a == key_b:
        return gl.ActionResult(False, "Нужны два разные типа мусора.")
    if not storage.change_money(telegram_id, -ARTIFACT_CRAFT_COST_RU):
        return gl.ActionResult(False, f"Нужно {ARTIFACT_CRAFT_COST_RU} RU для опыта.")
    if not storage.remove_item(telegram_id, key_a, 1) or not storage.remove_item(telegram_id, key_b, 1):
        storage.change_money(telegram_id, ARTIFACT_CRAFT_COST_RU)
        return gl.ActionResult(False, "В инвентаре нет нужного мусора.")
    if random.randint(1, 100) > ARTIFACT_CRAFT_SUCCESS_PERCENT:
        return gl.ActionResult(True, "Опыт не удался — мусор ушёл в переплавку, артефакт не получился.")
    reward = random.choice(ARTIFACT_CRAFT_REWARD_POOL)
    storage.add_item(telegram_id, reward, 1)
    label = gl.ITEM_LABELS.get(reward, reward)
    return gl.ActionResult(True, f"Удача! Собран артефакт: {label}.")


def note_junk_type_seen(storage: Storage, telegram_id: int, junk_key: str) -> None:
    gl = _gl()
    if junk_key not in gl.ARTIFACT_JUNK_KEYS:
        return
    raw = storage.get_meta(f"{JUNK_COLLECTION_META}:{telegram_id}") or "[]"
    try:
        seen = set(json.loads(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        seen = set()
    if junk_key in seen:
        return
    seen.add(junk_key)
    storage.set_meta(f"{JUNK_COLLECTION_META}:{telegram_id}", json.dumps(sorted(seen), ensure_ascii=False))


def junk_types_collected_count(storage: Storage, telegram_id: int) -> int:
    gl = _gl()
    raw = storage.get_meta(f"{JUNK_COLLECTION_META}:{telegram_id}") or "[]"
    try:
        seen = json.loads(raw)
        if isinstance(seen, list):
            return len([k for k in seen if k in gl.ARTIFACT_JUNK_KEYS])
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return 0


def set_post_emission_artifact_hotspot(storage: Storage) -> str | None:
    gl = _gl()
    candidates = [loc for loc in gl.ARTIFACT_LOCATION_SPAWNS if loc in gl.MAP_TRAVEL_POINTS]
    if not candidates:
        return None
    location = random.choice(candidates)
    until = utc_now() + timedelta(hours=ARTIFACT_HOTSPOT_HOURS)
    storage.set_meta(
        ARTIFACT_HOTSPOT_META,
        json.dumps({"location": location, "until": until.isoformat()}, ensure_ascii=False),
    )
    return location


def read_artifact_hotspot(storage: Storage) -> tuple[str | None, datetime | None]:
    raw = storage.get_meta(ARTIFACT_HOTSPOT_META)
    if not raw:
        return None, None
    try:
        data = json.loads(raw)
        loc = str(data.get("location") or "") or None
        until = datetime.fromisoformat(str(data.get("until") or ""))
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if utc_now() >= until:
            return None, None
        return loc, until
    except (json.JSONDecodeError, TypeError, ValueError):
        return None, None


def artifact_hotspot_multiplier(storage: Storage, location: str) -> float:
    loc, _ = read_artifact_hotspot(storage)
    if loc and loc == location:
        return ARTIFACT_HOTSPOT_MULT
    return 1.0


def detector_allows_valuable_art(detector_key: str, art_key: str) -> bool:
    if art_key in _gl().ARTIFACT_JUNK_KEYS:
        return True
    allowed = DETECTOR_VALUABLE_TIER.get(detector_key, DETECTOR_VALUABLE_TIER["detector_svarog"])
    return art_key in allowed


def filter_roll_for_detector(detector_key: str, art_key: str | None) -> str | None:
    if art_key is None:
        return None
    gl = _gl()
    if art_key in all_valuable_artifact_keys() and not detector_allows_valuable_art(detector_key, art_key):
        return None
    return art_key


def effective_artifact_trader_sell_price(storage: Storage, item_key: str, base_price: int) -> int:
    sold_raw = storage.get_meta(f"{ARTIFACT_DYNAMIC_SELL_META_PREFIX}{item_key}") or "0"
    try:
        sold = max(0, int(sold_raw))
    except (TypeError, ValueError):
        sold = 0
    mult = max(ARTIFACT_DYNAMIC_SELL_FLOOR, 1.0 - sold * ARTIFACT_DYNAMIC_SELL_STEP)
    return max(1, int(round(base_price * mult)))


def record_artifact_trader_sale(storage: Storage, item_key: str) -> None:
    key = f"{ARTIFACT_DYNAMIC_SELL_META_PREFIX}{item_key}"
    raw = storage.get_meta(key) or "0"
    try:
        sold = int(raw)
    except (TypeError, ValueError):
        sold = 0
    storage.set_meta(key, str(sold + 1))


def artifact_faction_tax(amount: int, faction: str | None) -> tuple[int, int]:
    if not faction or amount <= 0:
        return amount, 0
    tax = max(0, int(round(amount * ARTIFACT_FACTION_SELL_TAX_PERCENT / 100)))
    return max(0, amount - tax), tax


def buy_artifact_insurance(storage: Storage, telegram_id: int) -> ActionResult:
    gl = _gl()
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return gl.ActionResult(False, "Сначала создай персонажа.")
    until_raw = storage.get_meta(f"{ARTIFACT_INSURANCE_META}:{telegram_id}")
    if until_raw:
        try:
            until = datetime.fromisoformat(until_raw)
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if utc_now() < until:
                return gl.ActionResult(False, f"Страховка уже активна до {until.strftime('%d.%m %H:%M')} UTC.")
        except ValueError:
            pass
    if not storage.change_money(telegram_id, -ARTIFACT_INSURANCE_COST_RU):
        return gl.ActionResult(False, f"Нужно {ARTIFACT_INSURANCE_COST_RU} RU.")
    until = utc_now() + timedelta(days=ARTIFACT_INSURANCE_DAYS)
    storage.set_meta(f"{ARTIFACT_INSURANCE_META}:{telegram_id}", until.isoformat())
    return gl.ActionResult(
        True,
        f"Страховка артефактов активна {ARTIFACT_INSURANCE_DAYS} дн. "
        f"(до {until.strftime('%d.%m %H:%M')} UTC). При смерти один экипированный арт сохранится.",
    )


def apply_artifact_insurance_on_death(storage: Storage, telegram_id: int) -> str:
    """Устаревший вызов — используй strip_equipped_artifacts_for_death."""
    return strip_equipped_artifacts_for_death(storage, telegram_id)


def _insured_artifact_slot(storage: Storage, telegram_id: int) -> str | None:
    raw = storage.get_meta(f"{ARTIFACT_INSURANCE_META}:{telegram_id}")
    if not raw:
        return None
    try:
        until = datetime.fromisoformat(raw)
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if utc_now() >= until:
            storage.delete_meta(f"{ARTIFACT_INSURANCE_META}:{telegram_id}")
            return None
    except ValueError:
        storage.delete_meta(f"{ARTIFACT_INSURANCE_META}:{telegram_id}")
        return None
    gl = _gl()
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return None
    for slot_key in gl.ARTIFACT_EQUIP_SLOT_KEYS:
        name = str(player.equipment.get(slot_key, "Нет") or "Нет")
        if name and name != "Нет":
            storage.delete_meta(f"{ARTIFACT_INSURANCE_META}:{telegram_id}")
            return slot_key
    return None


def strip_equipped_artifacts_for_death(storage: Storage, telegram_id: int) -> str:
    """Перед лутом рюкзака: снять экипированные арты в инвентарь; страховка оставляет один слот."""
    gl = _gl()
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ""
    insured_slot = _insured_artifact_slot(storage, telegram_id)
    notes: list[str] = []
    for slot_key in gl.ARTIFACT_EQUIP_SLOT_KEYS:
        name = str(player.equipment.get(slot_key, "Нет") or "Нет")
        if not name or name == "Нет":
            continue
        if slot_key == insured_slot:
            notes.append(f"🛡 Страховка сохранила {name} (слот {slot_key}) при смерти.")
            continue
        inv_key = gl.ARTIFACT_NAME_TO_INVENTORY.get(name)
        storage.set_equipment_item(telegram_id, slot_key, "Нет")
        storage.update_equipment_fields(telegram_id, {_wear_field(slot_key): ARTIFACT_WEAR_MAX})
        if inv_key:
            storage.add_item(telegram_id, inv_key, 1)
    storage.sync_gear_power(telegram_id)
    updated = storage.get_character(telegram_id, refresh_energy=False)
    if updated is not None:
        max_hp = gl.effective_max_health(updated)
        if updated.health > max_hp:
            storage.change_health(telegram_id, max_hp - updated.health, max_health=max_hp)
    return "\n".join(notes)


def artifact_equip_cooldown_active(storage: Storage, telegram_id: int, item_key: str) -> bool:
    raw = storage.get_meta(f"{ARTIFACT_EQUIP_COOLDOWN_PREFIX}{telegram_id}:{item_key}")
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(raw)
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        return utc_now() < until
    except ValueError:
        return False


def mark_artifact_equip_cooldown(storage: Storage, telegram_id: int, item_key: str) -> None:
    until = utc_now() + timedelta(hours=ARTIFACT_EQUIP_COOLDOWN_HOURS)
    storage.set_meta(f"{ARTIFACT_EQUIP_COOLDOWN_PREFIX}{telegram_id}:{item_key}", until.isoformat())


def artifact_outgoing_damage_mult(character: Character) -> float:
    mult = 1.0
    for name in _equipped_names(character):
        stats = artifact_bonus_entry(name)
        power = int(stats.get("power", 0))
        if power >= 2:
            mult += 0.05
        elif power >= 1:
            mult += 0.03
    return mult


def artifact_incoming_damage_reduction(character: Character) -> int:
    bonus = 0
    for name in _equipped_names(character):
        if "Живучесть" in name or "Кристалл" in name:
            bonus += 1
        bonus += int(artifact_bonus_entry(name).get("armor", 0))
    return bonus


def scaled_artifact_energy_regen_bonus(character: Character) -> int:
    """Флатный бонус к регену энергии от экипированных артов (на тик регена)."""
    gl = _gl()
    total = 0
    for slot_key in gl.ARTIFACT_EQUIP_SLOT_KEYS:
        name = str(character.equipment.get(slot_key, "Нет") or "Нет")
        if not name or name == "Нет":
            continue
        stats = artifact_bonus_entry(name)
        total += int(round(int(stats.get("energy_regen", 0)) * wear_multiplier(get_artifact_wear(character, slot_key))))
    return total


def scaled_artifact_hp_regen_per_minute(character: Character) -> float:
    """Доп. пассивный хил в минуту от экипированных артов (≈/час дели на 60)."""
    gl = _gl()
    total = 0.0
    for slot_key in gl.ARTIFACT_EQUIP_SLOT_KEYS:
        name = str(character.equipment.get(slot_key, "Нет") or "Нет")
        if not name or name == "Нет":
            continue
        stats = artifact_bonus_entry(name)
        hp_regen = int(stats.get("hp_regen", 0))
        if hp_regen:
            total += hp_regen * wear_multiplier(get_artifact_wear(character, slot_key)) / 60.0
    return total


def artifact_psy_resist(character: Character) -> int:
    """Сумма сопротивления пси-воздействию (аура контролёра/полтергейста) от экипированных артов."""
    gl = _gl()
    total = 0
    for slot_key in gl.ARTIFACT_EQUIP_SLOT_KEYS:
        name = str(character.equipment.get(slot_key, "Нет") or "Нет")
        if not name or name == "Нет":
            continue
        stats = artifact_bonus_entry(name)
        total += int(round(int(stats.get("psy_resist", 0)) * wear_multiplier(get_artifact_wear(character, slot_key))))
    return total


def artifact_quest_heal_per_turn(character: Character) -> int:
    return sum(1 for name in _equipped_names(character) if "Живучесть" in name)


def coop_hunt_drop_multiplier(storage: Storage, telegram_id: int, location: str) -> float:
    from app.artifact_hunt import get_hunt_session, list_active_hunt_player_ids

    for other_id in list_active_hunt_player_ids(storage):
        if other_id == telegram_id:
            continue
        session = get_hunt_session(storage, other_id)
        if session and session.location == location:
            return ARTIFACT_COOP_BONUS_MULT
    return 1.0


def build_my_artifacts_text(storage: Storage, telegram_id: int) -> str:
    gl = _gl()
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return "Сначала создай персонажа."
    cap = gl.max_artifact_slots(player)
    pressure, cleanse_amt, interval, cleanse_pwr = compute_equipped_artifact_rad_profile(player)
    lines = [
        "💎 Мои артефакты",
        f"Сила снаряги: {gl.equipment_power(player)} | Ячеек: {cap}/{MAX_ARTIFACT_EQUIP_SLOTS}",
        f"☢ Давление артов: +{pressure} рад / 10 мин"
        + (f" | Очистка: −{cleanse_amt} каждые {interval} мин" if cleanse_pwr else ""),
        "",
        "Экипировано:",
    ]
    for idx, slot_key in enumerate(gl.ARTIFACT_EQUIP_SLOT_KEYS, start=1):
        if idx > cap:
            break
        name = str(player.equipment.get(slot_key, "Нет") or "Нет")
        if not name or name == "Нет":
            lines.append(f"  {idx}. пусто")
            continue
        wear = get_artifact_wear(player, slot_key)
        short = artifact_bonus_entry(name)
        bits = []
        if short.get("power"):
            bits.append(f"+{short['power']} сила")
        if short.get("hp"):
            bits.append(f"+{short['hp']} HP")
        if short.get("rad_pressure"):
            bits.append(f"+{short['rad_pressure']} рад")
        if short.get("cleanse_power"):
            bits.append(f"очистка {short['cleanse_power']}")
        lines.append(f"  {idx}. {name} ({wear}%) — {', '.join(bits) or 'без бонуса'}")
    inv_lines = []
    for key in all_valuable_artifact_keys():
        qty = int(player.inventory.get(key, 0))
        if qty > 0:
            inv_lines.append(f"• {gl.ITEM_LABELS.get(key, key)} ×{qty}")
    lines.extend(["", "В инвентаре (ценные):"])
    lines.extend(inv_lines or ["• пусто"])
    junk_count = sum(int(player.inventory.get(k, 0)) for k in gl.ARTIFACT_JUNK_KEYS)
    lines.append(f"\nМусор в инвентаре: {junk_count} шт.")
    lines.append(f"Коллекция мусора: {junk_types_collected_count(storage, telegram_id)}/{len(gl.ARTIFACT_JUNK_KEYS)} типов.")
    hotspot_loc, hotspot_until = read_artifact_hotspot(storage)
    if hotspot_loc and hotspot_until:
        lines.append(
            f"\n🔥 Горячая точка: «{hotspot_loc}» до {hotspot_until.strftime('%d.%m %H:%M')} UTC (×{ARTIFACT_HOTSPOT_MULT:g})."
        )
    lines.append("")
    lines.append(build_equip_slot_hint(player))
    hint = build_predel_assembly_hint(storage, telegram_id)
    if hint:
        lines.append("")
        lines.append(hint.strip())
    return "\n".join(lines)


def build_artifact_drop_table_text(storage: Storage, location: str, detector_chance: int = 17) -> str:
    gl = _gl()
    hint = gl.describe_location_artifact_spawns(location)
    lines = [
        f"📊 Таблица дропа — «{location}»",
        hint,
        "",
        "Детектор усиливает локальные шансы (не Зону/топ-арты).",
        f"Базовый шанс детектора: {detector_chance}%.",
        "",
        "Цепочка детекторов:",
        "• Отклик — мусор + «Сила»",
        "• Медведь — + «Живучесть»",
        "• Велес — + «Жар», «Кровь»",
        "• Сварог — все ценные, включая Зону и Антирад",
    ]
    hotspot_loc, hotspot_until = read_artifact_hotspot(storage)
    if hotspot_loc:
        if hotspot_until:
            lines.append(f"\n🔥 Горячая точка сейчас: «{hotspot_loc}» до {hotspot_until.strftime('%d.%m %H:%M')} UTC.")
        if hotspot_loc == location:
            lines.append(f"На этой точке действует ×{ARTIFACT_HOTSPOT_MULT:g} к локальному дропу.")
    return "\n".join(lines)


def build_equip_slot_hint(character: Character) -> str:
    gl = _gl()
    cap = gl.max_artifact_slots(character)
    if cap <= 0:
        return "💡 Ячейки артов откроются на броне T3 (1 слот) или T4+ (2). У техника — ещё +1 слот."
    if cap >= MAX_ARTIFACT_EQUIP_SLOTS:
        return "💡 Все 3 ячейки доступны. Следи за износом после контрактов."
    return f"💡 Доступно {cap} из {MAX_ARTIFACT_EQUIP_SLOTS} ячеек. T4-броня или апгрейд техника дадут ещё слоты."
