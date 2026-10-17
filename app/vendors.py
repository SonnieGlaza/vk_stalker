"""Поэтапный ассортимент: бармен, медик, техник."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.storage import Storage

VENDOR_TIER_MAX = 5

VENDOR_KEYS = ("barkeep", "medic", "tech")

# Бесплатное лечение у медика по этапу ассортимента (вкачке медика).
# hp_percent — потолок лечения в % от максимального HP, rad — снятие радиации.
MEDIC_HEAL_PLAN: dict[int, dict[str, int]] = {
    1: {"hp_percent": 0, "rad": 0},
    2: {"hp_percent": 25, "rad": 0},
    3: {"hp_percent": 50, "rad": 10},
    4: {"hp_percent": 75, "rad": 15},
    5: {"hp_percent": 100, "rad": 20},
}

VENDOR_TITLES: dict[str, str] = {
    "barkeep": "Бармен",
    "medic": "Медик",
    "tech": "Техник",
}

VENDOR_NAMES_BY_FACTION: dict[str, dict[str, str]] = {
    "Свобода": {"barkeep": "Ганжа", "tech": "Дядька Яр", "medic": "Ашот"},
    "Долг": {"barkeep": "Колобок", "tech": "Громов", "medic": "Митяй"},
    "Бандиты": {"barkeep": "Боров", "tech": "Прозрачный", "medic": "Зуб"},
    "Нейтралы": {"barkeep": "Суслов", "tech": "Фургон", "medic": "Спартак"},
}

VENDOR_FACTION_ROLE: dict[str, str] = {
    "Свобода": "торговец Свободы",
    "Долг": "торговец Долга",
    "Бандиты": "торговец бандитов",
    "Нейтралы": "торговец нейтралов",
}

VENDOR_REP_PREFIX: dict[str, str] = {
    "barkeep": "vendor_rep:barkeep:",
    "medic": "vendor_rep:medic:",
    "tech": "vendor_rep:tech:",
}
# Пороги авторитета для этапов 2/3/4/5 (этап 1 с нуля).
VENDOR_REP_THRESHOLDS: tuple[int, ...] = (200, 1000, 5000, 20000)
VENDOR_REP_BY_DIFFICULTY: dict[str, int] = {
    "easy": 2,
    "hard": 3,
    "heavy": 4,
    "impossible": 5,
}

VENDOR_META_PREFIX: dict[str, str] = {
    "barkeep": "vendor:barkeep_tier:",
    "medic": "vendor:medic_tier:",
    "tech": "vendor:tech_tier:",
}

VENDOR_STAGE_LABELS: dict[str, dict[int, str]] = {
    "barkeep": {
        1: "еда/вода, T1 стволы и броня, «Отклик», велосипед",
        2: "+ T2, Нива, «Медведь», колбаса/минералка",
        3: "+ T3, броня среднего класса, «Велес», спальник, тушёнка",
        4: "+ T4/T5, Гаусс и Енот, экзо/Носорог, «Сварог», грузовик",
        5: "ассортимент на максимуме (как этап 4)",
    },
    "medic": {
        1: "обычная аптечка",
        2: "+ армейская аптечка",
        3: "+ антирад",
        4: "+ научная аптечка",
        5: "+ антибиотик (защита от Выброса)",
    },
    "tech": {
        1: "улучшение брони, скидка на ремонт 2%",
        2: "закись азота (N2O), доп. ячейка артефакта (+1 к ячейкам брони), скидка на ремонт 4%",
        3: "новых товаров нет — скидка на ремонт 6%",
        4: "новых товаров нет — скидка на ремонт 8%",
        5: "скидка на ремонт 8% (максимум)",
    },
}

# Скидка техника на ремонт по этапу.
TECH_REPAIR_DISCOUNT_PERCENT: dict[int, int] = {1: 2, 2: 4, 3: 6, 4: 8, 5: 8}

# Добавки ассортимента бармена по этапам (накопительно).
_BARKEEP_STAGE_ITEMS: dict[int, tuple[str, ...]] = {
    1: (
        "bread",
        "water_bottle",
        "energy_drink",
        "ammo_pistol",
        "ammo_shotgun",
        "ammo_rifle",
        "ammo_gauss",
        "vodka",
        "diesel_can",
        "gasoline_can",
        "stash_case",
        "stash_coordinates",
        "radio_set",
        "weapon_pm",
        "weapon_fort12",
        "weapon_fora12",
        "weapon_sawedoff",
        "armor_leather",
        "detector_otklik",
        "bicycle",
    ),
    2: (
        "sausage",
        "mineral_water",
        "weapon_mp5",
        "weapon_chaser13",
        "weapon_aks74u",
        "armor_stalker_vest",
        "armor_zarya",
        "armor_sunrise",
        "detector_medved",
        "niva",
    ),
    3: (
        "stew",
        "beard_tea",
        "weapon_ak74",
        "weapon_spas12",
        "armor_psz7d",
        "armor_bulat",
        "armor_berill5m",
        "armor_seva",
        "armor_scientific",
        "detector_veles",
        "sleeping_bag",
    ),
    4: (
        "weapon_lr300",
        "weapon_il86",
        "weapon_an94",
        "weapon_gp37",
        "weapon_vintar",
        "weapon_svd",
        "weapon_rp74",
        "weapon_gauss",
        "weapon_raccoon",
        "armor_exo",
        "armor_exoskeleton",
        "armor_nosorog",
        "detector_svarog",
        "truck",
    ),
    5: (),
}

_MEDIC_STAGE_ITEMS: dict[int, tuple[str, ...]] = {
    1: ("medkit",),
    2: ("medkit_army",),
    3: ("antirad",),
    4: ("medkit_science",),
    5: ("antibiotic",),
}

_TECH_STAGE_ITEMS: dict[int, tuple[str, ...]] = {
    1: ("armor_upgrade", "gear_upgrade"),
    2: ("nitrous_oxide", "artifact_slot"),
    3: (),
    4: (),
    5: (),
}


def vendor_person_name(faction: str | None, vendor: str) -> str:
    names = VENDOR_NAMES_BY_FACTION.get(str(faction or ""), {})
    return names.get(vendor) or VENDOR_TITLES.get(vendor, vendor)


def vendor_button_title(faction: str | None, vendor: str) -> str:
    person = vendor_person_name(faction, vendor)
    role = VENDOR_TITLES.get(vendor, vendor)
    if person == role:
        return role
    return f"{person} · {role}"


def vendor_quest_label(faction: str | None, vendor: str) -> str:
    person = vendor_person_name(faction, vendor)
    role = VENDOR_FACTION_ROLE.get(str(faction or ""), VENDOR_TITLES.get(vendor, vendor))
    return f"{person} ({role})"


def _vendor_rep_key(vendor: str, telegram_id: int) -> str:
    return f"{VENDOR_REP_PREFIX[vendor]}{int(telegram_id)}"


def get_vendor_reputation(storage: Storage, telegram_id: int, vendor: str) -> int:
    if vendor not in VENDOR_REP_PREFIX:
        return 0
    raw = storage.get_meta(_vendor_rep_key(vendor, telegram_id))
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def add_vendor_reputation(storage: Storage, telegram_id: int, vendor: str, amount: int) -> int:
    if vendor not in VENDOR_REP_PREFIX or amount == 0:
        return get_vendor_reputation(storage, telegram_id, vendor)
    total = get_vendor_reputation(storage, telegram_id, vendor) + int(amount)
    total = max(0, total)
    storage.set_meta(_vendor_rep_key(vendor, telegram_id), str(total))
    return total


def reputation_to_tier(rep: int) -> int:
    tier = 1
    for threshold in VENDOR_REP_THRESHOLDS:
        if int(rep) >= int(threshold):
            tier += 1
        else:
            break
    return max(1, min(VENDOR_TIER_MAX, tier))


def next_rep_threshold(rep: int) -> int | None:
    """Следующий порог авторитета или None, если уже максимум."""
    for threshold in VENDOR_REP_THRESHOLDS:
        if int(rep) < int(threshold):
            return int(threshold)
    return None


def reputation_progress_label(rep: int) -> str:
    """Короткая строка прогресса, например «100/1000»."""
    need = next_rep_threshold(rep)
    if need is None:
        return f"{int(rep)}/{VENDOR_REP_THRESHOLDS[-1]}"
    return f"{int(rep)}/{need}"


def _vendor_meta_key(vendor: str, telegram_id: int) -> str:
    prefix = VENDOR_META_PREFIX[vendor]
    return f"{prefix}{int(telegram_id)}"


def get_stored_vendor_tier(storage: Storage, telegram_id: int, vendor: str) -> int:
    if vendor not in VENDOR_META_PREFIX:
        return 1
    raw = storage.get_meta(_vendor_meta_key(vendor, telegram_id))
    if raw is None or str(raw).strip() == "":
        return 1
    try:
        tier = int(raw)
    except (TypeError, ValueError):
        return 1
    return max(1, min(VENDOR_TIER_MAX, tier))


def get_vendor_tier(storage: Storage, telegram_id: int, vendor: str) -> int:
    stored = get_stored_vendor_tier(storage, telegram_id, vendor)
    earned = reputation_to_tier(get_vendor_reputation(storage, telegram_id, vendor))
    return max(stored, earned)


def set_vendor_tier(storage: Storage, telegram_id: int, vendor: str, tier: int) -> None:
    if vendor not in VENDOR_META_PREFIX:
        return
    safe = max(1, min(VENDOR_TIER_MAX, int(tier)))
    storage.set_meta(_vendor_meta_key(vendor, telegram_id), str(safe))


def unlocked_vendor_item_keys(vendor: str, tier: int) -> frozenset[str]:
    safe = max(1, min(VENDOR_TIER_MAX, int(tier)))
    table = {
        "barkeep": _BARKEEP_STAGE_ITEMS,
        "medic": _MEDIC_STAGE_ITEMS,
        "tech": _TECH_STAGE_ITEMS,
    }.get(vendor)
    if table is None:
        return frozenset()
    unlocked: set[str] = set()
    for stage in range(1, safe + 1):
        unlocked.update(table.get(stage, ()))
    return frozenset(unlocked)


def _build_item_vendor_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for items in _MEDIC_STAGE_ITEMS.values():
        for key in items:
            mapping[key] = "medic"
    for items in _TECH_STAGE_ITEMS.values():
        for key in items:
            mapping[key] = "tech"
    for items in _BARKEEP_STAGE_ITEMS.values():
        for key in items:
            mapping[key] = "barkeep"
    return mapping


_ITEM_VENDOR_MAP = _build_item_vendor_map()
# Рация продаётся у бармена (плюс гейт по постройке антенны — см. vendor_item_is_unlocked).
_ITEM_VENDOR_MAP["radio_set"] = "barkeep"


def shop_item_vendor(item_key: str) -> str | None:
    """Какой специалист продаёт товар. None — не из ассортимента специалистов."""
    from app.game_logic import (
        ARMOR_CATALOG,
        SEASON_REWARD_ITEM_KEYS,
        WEAPON_CATALOG,
        normalize_shop_item_key,
    )

    key = normalize_shop_item_key(item_key)
    if key in _ITEM_VENDOR_MAP:
        return _ITEM_VENDOR_MAP[key]
    if key in SEASON_REWARD_ITEM_KEYS:
        return None
    if key in WEAPON_CATALOG or key in ARMOR_CATALOG:
        return "barkeep"
    return None


def vendor_item_is_unlocked(storage: Storage, telegram_id: int, item_key: str) -> bool:
    from app.game_logic import normalize_shop_item_key

    key = normalize_shop_item_key(item_key)
    vendor = shop_item_vendor(key)
    if vendor is None:
        return True
    unlocked = unlocked_vendor_item_keys(vendor, get_vendor_tier(storage, telegram_id, vendor))
    if key in unlocked:
        if key == "radio_set":
            # Рация у бармена появляется только когда на базе фракции построена антенна.
            from app.faction_buildings import faction_building_active

            character = storage.get_character(telegram_id, refresh_energy=False)
            faction = character.faction if character else None
            if not faction_building_active(storage, faction, "antenna"):
                return False
        return True
    aliases = {
        "weapon_fora12": "weapon_fort12",
        "weapon_fort12": "weapon_fora12",
        "armor_sunrise": "armor_zarya",
        "armor_zarya": "armor_sunrise",
        "armor_berill5m": "armor_bulat",
        "armor_bulat": "armor_berill5m",
        "armor_exoskeleton": "armor_exo",
        "armor_exo": "armor_exoskeleton",
        "gear_upgrade": "armor_upgrade",
        "armor_upgrade": "gear_upgrade",
    }
    alt = aliases.get(key)
    return bool(alt and alt in unlocked)


def tech_repair_discount_percent(storage: Storage, telegram_id: int) -> int:
    tier = get_vendor_tier(storage, telegram_id, "tech")
    return int(TECH_REPAIR_DISCOUNT_PERCENT.get(tier, 2))


def apply_tech_repair_discount(
    storage: Storage, telegram_id: int, price: int
) -> tuple[int, int]:
    """Вернуть (цена_со_скидкой, процент_скидки).

    Процент скидки включает скидку техника по этапу ассортимента плюс
    доп. скидку за построенный автосервис группировки (единственная
    точка, где применяется бонус автосервиса — действует на все виды
    ремонта сразу).
    """
    pct = tech_repair_discount_percent(storage, telegram_id)
    from app.faction_buildings import repair_discount_bonus_percent

    character = storage.get_character(telegram_id, refresh_energy=False)
    faction = character.faction if character else None
    pct = pct + repair_discount_bonus_percent(storage, faction)
    if pct <= 0 or price <= 0:
        return max(0, int(price)), 0
    discounted = max(1, int(round(int(price) * (100 - pct) / 100.0)))
    return discounted, pct


def vendor_assortment_blurb(storage: Storage, telegram_id: int, vendor: str) -> str:
    title = VENDOR_TITLES.get(vendor, vendor)
    character = storage.get_character(telegram_id, refresh_energy=False)
    person = vendor_person_name(character.faction if character else None, vendor)
    tier = get_vendor_tier(storage, telegram_id, vendor)
    rep = get_vendor_reputation(storage, telegram_id, vendor)
    label = VENDOR_STAGE_LABELS.get(vendor, {}).get(tier, f"этап {tier}")
    lines = [
        f"{person} ({title}): этап ассортимента {tier}/{VENDOR_TIER_MAX} — {label}.",
        f"Авторитет: {reputation_progress_label(rep)}.",
    ]
    if vendor == "tech":
        _discounted, _pct = apply_tech_repair_discount(storage, telegram_id, 1000)
        lines.append(f"Скидка на ремонт сейчас: {_pct}%.")
    if tier < VENDOR_TIER_MAX:
        need = current_rep_need(tier)
        lines.append(
            f"Следующий этап — {need} авторитета "
            f"(🟢+{VENDOR_REP_BY_DIFFICULTY['easy']} · "
            f"🟡+{VENDOR_REP_BY_DIFFICULTY['hard']} · "
            f"🟠+{VENDOR_REP_BY_DIFFICULTY['heavy']} · "
            f"🔴+{VENDOR_REP_BY_DIFFICULTY['impossible']} за задание у этого торговца)."
        )
    else:
        lines.append("Ассортимент на максимуме.")
    return "\n".join(lines)


def current_rep_need(current_tier: int) -> int:
    """Порог авторитета для открытия следующего этапа."""
    idx = max(0, min(len(VENDOR_REP_THRESHOLDS) - 1, int(current_tier) - 1))
    return int(VENDOR_REP_THRESHOLDS[idx])


def vendor_purge_meta_keys(telegram_id: int) -> list[str]:
    tid = int(telegram_id)
    keys = [f"{prefix}{tid}" for prefix in VENDOR_META_PREFIX.values()]
    keys.extend(f"{prefix}{tid}" for prefix in VENDOR_REP_PREFIX.values())
    return keys


def medic_heal(storage: Storage, telegram_id: int) -> ActionResult:
    """Бесплатное лечение у медика: сила зависит от этапа вкачки медика.

    Этап 1 — лечения нет; 2 — до 25% HP; 3 — до 50% HP и −10 рад;
    4 — до 75% HP и −15 рад; 5 — до 100% HP и −20 рад.
    КД между бесплатными сеансами — 30 минут.
    """
    from datetime import datetime, timedelta, timezone

    from app.game_logic import ActionResult, _dead_block_text, _is_dead, effective_max_health

    heal_cd_key = f"medic_heal_cd:{int(telegram_id)}"
    raw_cd = storage.get_meta(heal_cd_key)
    if raw_cd:
        try:
            ready_at = datetime.fromisoformat(raw_cd)
        except (TypeError, ValueError):
            ready_at = None
        if ready_at is not None:
            if ready_at.tzinfo is None:
                ready_at = ready_at.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if ready_at > now:
                left_sec = max(1, int((ready_at - now).total_seconds()))
                mins = left_sec // 60
                return ActionResult(False, f"💉 Медик ещё занят — следующий бесплатный сеанс через {mins} мин.")

    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    tier = get_vendor_tier(storage, telegram_id, "medic")
    plan = MEDIC_HEAL_PLAN.get(tier, MEDIC_HEAL_PLAN[1])
    if plan["hp_percent"] <= 0:
        return ActionResult(
            False,
            "Медик пока только продаёт аптечки — бесплатно лечит со 2 этапа вкачки. "
            "Прокачай его авторитетом за задания у этого торговца.",
        )
    max_hp = effective_max_health(player)
    heal_cap = max(1, int(round(max_hp * plan["hp_percent"] / 100.0)))
    heal_amount = min(heal_cap, max(0, max_hp - int(player.health)))
    rad_remove = min(plan["rad"], max(0, int(player.radiation)))
    if heal_amount <= 0 and rad_remove <= 0:
        return ActionResult(False, f"Тебе сейчас не нужно лечение (HP {int(player.health)}/{max_hp}).")
    storage.change_health(telegram_id, heal_amount, max_health=max_hp)
    if rad_remove > 0:
        storage.adjust_survival(telegram_id, radiation_delta=-rad_remove)
    storage.set_meta(heal_cd_key, (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat())
    parts: list[str] = []
    if heal_amount > 0:
        parts.append(f"+{heal_amount} HP")
    if rad_remove > 0:
        parts.append(f"−{rad_remove} рад")
    return ActionResult(True, f"💉 Медик бесплатно подлечил тебя: {', '.join(parts)}.")
