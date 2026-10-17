from __future__ import annotations

import hashlib
import json
import logging
import random
from math import dist
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.faction_ranks import (
    leader_title,
    rank_by_key,
    ranks_for_faction,
    resolve_rank_title,
)
from app.skins import next_skin_progress, resolve_skin
from app.html_utils import html_safe as h

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QuestType:
    key: str
    title: str
    energy_cost: int
    reward_min: int
    reward_max: int
    ammo_required: int
    medkit_required: int


QUESTS: dict[str, QuestType] = {
    "easy": QuestType("easy", "Легко", 10, 472, 717, 0, 0),
    "hard": QuestType("hard", "Средне", 16, 700, 1137, 0, 0),
    "heavy": QuestType("heavy", "Опасно", 22, 1155, 2100, 2, 1),
    "impossible": QuestType("impossible", "Невозможно", 28, 1470, 3150, 3, 1),
}


@dataclass(frozen=True)
class QuestContractTemplate:
    key: str
    difficulty: str
    title: str
    work_location: str
    min_transport: str | None = None  # "niva" | "truck"
    return_home: bool = True
    # collect/scout/loot/deliver_junk — тип работы на точке.
    mission_kind: str = "collect"
    deliver_items: tuple[tuple[str, int], ...] = ()
    min_gear_power: int = 0


QUEST_CONTRACTS: dict[str, QuestContractTemplate] = {
    "easy_boloto": QuestContractTemplate(
        "easy_boloto", "easy", "Сбор образцов на Болоте", "Болото", mission_kind="collect"
    ),
    "easy_agroprom": QuestContractTemplate(
        "easy_agroprom", "easy", "Разведка у Агропрома", "НИИ Агропром", mission_kind="scout"
    ),
    "easy_dump": QuestContractTemplate(
        "easy_dump", "easy", "Поиск хабара на Свалке", "Свалка", mission_kind="loot"
    ),
    "easy_escort_dump": QuestContractTemplate(
        "easy_escort_dump",
        "easy",
        "Сопровождение курьера на Свалке",
        "Свалка",
        mission_kind="escort",
    ),
    "easy_escort_boloto": QuestContractTemplate(
        "easy_escort_boloto",
        "easy",
        "Сопровождение на Болоте",
        "Болото",
        mission_kind="escort",
    ),
    "hard_yantar": QuestContractTemplate(
        "hard_yantar",
        "hard",
        "Снять показания на Янтаре",
        "Янтарь",
        min_transport="niva",
        mission_kind="scout",
    ),
    "hard_forest": QuestContractTemplate(
        "hard_forest",
        "hard",
        "Зачистка Рыжего леса",
        "Рыжий лес",
        min_transport="niva",
        mission_kind="clear_mutant",
    ),
    "hard_valley": QuestContractTemplate(
        "hard_valley",
        "hard",
        "Рейд в Темную долину",
        "Темная долина",
        min_transport="niva",
        mission_kind="clear_mutant",
    ),
    "hard_escort_agroprom": QuestContractTemplate(
        "hard_escort_agroprom",
        "hard",
        "Провести сталкера к Агропрому",
        "НИИ Агропром",
        min_transport="niva",
        mission_kind="escort",
    ),
    "heavy_boloto": QuestContractTemplate(
        "heavy_boloto", "heavy", "Опасный сбор на Болоте", "Болото", mission_kind="collect"
    ),
    "heavy_yantar": QuestContractTemplate(
        "heavy_yantar",
        "heavy",
        "Экспедиция на Янтарь",
        "Янтарь",
        min_transport="niva",
        mission_kind="scout",
    ),
    "heavy_valley": QuestContractTemplate(
        "heavy_valley",
        "heavy",
        "Зачистка в Темной долине",
        "Темная долина",
        min_transport="niva",
        mission_kind="clear_marauder",
    ),
    "heavy_escort_valley": QuestContractTemplate(
        "heavy_escort_valley",
        "heavy",
        "Эскорт через Темную долину",
        "Темная долина",
        min_transport="niva",
        mission_kind="escort",
    ),
    "impossible_radar": QuestContractTemplate(
        "impossible_radar",
        "impossible",
        "Зачистка Радара",
        "Радар",
        min_transport="truck",
        mission_kind="clear_mutant",
    ),
    "impossible_forest": QuestContractTemplate(
        "impossible_forest",
        "impossible",
        "Аномалии Рыжего леса",
        "Рыжий лес",
        min_transport="truck",
        mission_kind="anomaly",
        min_gear_power=14,
    ),
    "easy_deliver_junk": QuestContractTemplate(
        "easy_deliver_junk",
        "easy",
        "Сдать мусор с Свалки",
        "Свалка",
        mission_kind="deliver_junk",
        deliver_items=(("artifact_junk_bolt", 3), ("artifact_junk_slime", 2)),
    ),
    "hard_deliver_junk": QuestContractTemplate(
        "hard_deliver_junk",
        "hard",
        "Партия хлама с Радара",
        "Радар",
        min_transport="niva",
        mission_kind="deliver_junk",
        deliver_items=(("artifact_junk_battery", 2), ("artifact_junk_flash", 2), ("artifact_junk_stone", 2)),
    ),
    "heavy_deliver_junk": QuestContractTemplate(
        "heavy_deliver_junk",
        "heavy",
        "Разбор находок в долине",
        "Темная долина",
        min_transport="niva",
        mission_kind="deliver_junk",
        deliver_items=(("artifact_junk_splinter", 3), ("artifact_junk_bolt", 3)),
    ),
}

# По одному старому контракту каждой сложности на торговца.
VENDOR_CONTRACT_KEYS: dict[str, dict[str, str]] = {
    "barkeep": {
        "easy": "easy_dump",
        "hard": "hard_forest",
        "heavy": "heavy_boloto",
        "impossible": "impossible_radar",
    },
    "medic": {
        "easy": "easy_agroprom",
        "hard": "hard_yantar",
        "heavy": "heavy_yantar",
        "impossible": "impossible_forest",
    },
    "tech": {
        "easy": "easy_escort_boloto",
        "hard": "hard_escort_agroprom",
        "heavy": "heavy_escort_valley",
        "impossible": "impossible_radar",
    },
}

CONTRACT_TURN_IN_BONUS_PERCENT = 10
LOCATION_TYPE_RU_MULT: dict[str, float] = {
    "точка ресурсов": 1.1,
    "точка интереса": 1.2,
    "база": 1.0,
}
CONTROLLED_LOCATION_RU_BONUS = 1.1

SHOP_ITEMS: dict[str, dict[str, int | str]] = {
    "energy_drink": {"name": "Энергетик", "buy_price": 111, "sell_price": 50},
    "nitrous_oxide": {"name": "Закись азота (N2O)", "buy_price": 1250, "sell_price": 500},
    "medkit": {"name": "Аптечка", "buy_price": 100, "sell_price": 46},
    "medkit_army": {"name": "Армейская аптечка", "buy_price": 200, "sell_price": 80},
    "medkit_science": {"name": "Научная аптечка", "buy_price": 300, "sell_price": 120},
    "ammo_pistol": {"name": "Патроны (пистолетные)", "buy_price": 50, "sell_price": 20},
    "ammo_shotgun": {"name": "Патроны (дробь)", "buy_price": 90, "sell_price": 36},
    "ammo_rifle": {"name": "Патроны (винтовочные)", "buy_price": 120, "sell_price": 48},
    "ammo_gauss": {"name": "Элементы для гаусса", "buy_price": 300, "sell_price": 120},
    "ammo_pack": {"name": "Патроны", "buy_price": 53, "sell_price": 24},
    "artifact": {"name": "Артефакт Зоны", "buy_price": 0, "sell_price": 5000},
    "artifact_power": {"name": "Арт «Сила»", "buy_price": 0, "sell_price": 1100},
    "artifact_vitality": {"name": "Арт «Живучесть»", "buy_price": 0, "sell_price": 1100},
    "artifact_antirad": {"name": "Арт «Антирад»", "buy_price": 0, "sell_price": 5000},
    "artifact_junk_slime": {"name": "Слизь", "buy_price": 0, "sell_price": 280},
    "artifact_junk_bolt": {"name": "Ржавый болт", "buy_price": 0, "sell_price": 250},
    "artifact_junk_battery": {"name": "Дохлая батарейка", "buy_price": 0, "sell_price": 350},
    "artifact_junk_flash": {"name": "Вспышка", "buy_price": 0, "sell_price": 400},
    "artifact_junk_stone": {"name": "Аномальный камень", "buy_price": 0, "sell_price": 300},
    "artifact_junk_fog": {"name": "Сгусток тумана", "buy_price": 0, "sell_price": 400},
    "artifact_junk_splinter": {"name": "Осколок", "buy_price": 0, "sell_price": 350},
    "mutant_tail": {"name": "Хвост мутанта", "buy_price": 0, "sell_price": 180},
    "mutant_claw": {"name": "Коготь мутанта", "buy_price": 0, "sell_price": 200},
    "mutant_fang": {"name": "Клык мутанта", "buy_price": 0, "sell_price": 220},
    "mutant_eye": {"name": "Глаз мутанта", "buy_price": 0, "sell_price": 260},
    "mutant_hide": {"name": "Шкура мутанта", "buy_price": 0, "sell_price": 300},
    "mutant_tendril": {"name": "Щупальце кровососа", "buy_price": 0, "sell_price": 340},
    "vodka": {"name": "Водка", "buy_price": 66, "sell_price": 22},
    "antirad": {"name": "Антирад", "buy_price": 177, "sell_price": 58},
    "antibiotic": {"name": "Антибиотик", "buy_price": 700, "sell_price": 240},
    "bread": {"name": "Хлеб", "buy_price": 22, "sell_price": 7},
    "sausage": {"name": "Колбаса", "buy_price": 44, "sell_price": 15},
    "stew": {"name": "Тушёнка", "buy_price": 111, "sell_price": 37},
    "water_bottle": {"name": "Бутылка воды", "buy_price": 22, "sell_price": 7},
    "mineral_water": {"name": "Минералка", "buy_price": 44, "sell_price": 15},
    "beard_tea": {"name": "Чай Бороды", "buy_price": 111, "sell_price": 37},
    "detector_otklik": {"name": "Детектор «Отклик»", "buy_price": 1000, "sell_price": 500},
    "detector_medved": {"name": "Детектор «Медведь»", "buy_price": 4000, "sell_price": 2000},
    "detector_veles": {"name": "Детектор «Велес»", "buy_price": 10000, "sell_price": 5000},
    "detector_svarog": {"name": "Детектор «Сварог»", "buy_price": 30000, "sell_price": 15000},
    "gear_upgrade": {"name": "Улучшение брони (+1 защита)", "buy_price": 5000, "sell_price": 2000},
    "armor_upgrade": {"name": "Улучшение брони (+1 защита)", "buy_price": 5000, "sell_price": 2000},
    "artifact_slot": {"name": "Доп. ячейка артефакта", "buy_price": 15000, "sell_price": 5000},
    "truck": {"name": "Грузовик", "buy_price": 50000, "sell_price": 17500},
    "niva": {"name": "Нива", "buy_price": 10000, "sell_price": 4500},
    "bicycle": {"name": "Велосипед", "buy_price": 3500, "sell_price": 1500},
    "sleeping_bag": {"name": "Спальник", "buy_price": 20000, "sell_price": 10000},
    "diesel_can": {"name": "Канистра дизеля (+5)", "buy_price": 199, "sell_price": 89},
    "gasoline_can": {"name": "Канистра бензина (+5)", "buy_price": 100, "sell_price": 44},
    "fuel_can": {"name": "Канистра дизеля (+5)", "buy_price": 199, "sell_price": 89},
    "stash_case": {"name": "Тайник", "buy_price": 5000, "sell_price": 1200},
    "stash_coordinates": {"name": "Координаты хабара", "buy_price": 3500, "sell_price": 0},
    "radio_set": {"name": "Рация", "buy_price": 30000, "sell_price": 12000},
}

# Сила снаряги: рейтинги и цены оружия/брони масштабированы ×18/11,
# чтобы топ (Гаусс + Носорог + Артефакт Зоны) давал ровно 20 очков.
# Цены растут вместе с силой (вклад в gear_power). Продажа ≈ 48% покупки.
ARMOR_CATALOG: dict[str, dict[str, int | str]] = {
    "armor_leather": {"name": "Кожаная куртка", "buy_price": 2000, "sell_price": 960},
    "armor_stalker_vest": {"name": "Сталкерский бронежилет", "buy_price": 4000, "sell_price": 1920},
    "armor_psz7d": {"name": "ПСЗ-7 «Долг»", "buy_price": 7000, "sell_price": 3360},
    "armor_zarya": {"name": "Комбинезон «Заря»", "buy_price": 6000, "sell_price": 2880},
    "armor_bulat": {"name": "Берилл-5М «Булат»", "buy_price": 12000, "sell_price": 5760},
    "armor_seva": {"name": "Костюм СЕВА", "buy_price": 16000, "sell_price": 7680},
    "armor_scientific": {"name": "Научный костюм", "buy_price": 25000, "sell_price": 12000},
    "armor_exo": {"name": "Экзоскелет", "buy_price": 45000, "sell_price": 21600},
    "armor_nosorog": {"name": "Носорог", "buy_price": 90000, "sell_price": 43200},
}

WEAPON_CATALOG: dict[str, dict[str, int | str]] = {
    "weapon_pm": {"name": "ПМ", "buy_price": 2000, "sell_price": 960},
    "weapon_fort12": {"name": "Фора-12", "buy_price": 3000, "sell_price": 1440},
    "weapon_sawedoff": {"name": "Обрез", "buy_price": 1800, "sell_price": 860},
    "weapon_chaser13": {"name": "Чейзер-13", "buy_price": 5500, "sell_price": 2640},
    "weapon_spas12": {"name": "СПАС-12", "buy_price": 11000, "sell_price": 5280},
    "weapon_mp5": {"name": "Гадюка-5", "buy_price": 4500, "sell_price": 2160},
    "weapon_aks74u": {"name": "АКС-74У", "buy_price": 7500, "sell_price": 3600},
    "weapon_ak74": {"name": "АК-74", "buy_price": 10000, "sell_price": 4800},
    "weapon_lr300": {"name": "ТРс-301", "buy_price": 15000, "sell_price": 7200},
    "weapon_il86": {"name": "ИЛ86", "buy_price": 16000, "sell_price": 7680},
    "weapon_gp37": {"name": "ГП37", "buy_price": 25000, "sell_price": 12000},
    "weapon_an94": {"name": "АН-94", "buy_price": 22000, "sell_price": 10560},
    "weapon_vintar": {"name": "Винтарь ВС", "buy_price": 35000, "sell_price": 16800},
    "weapon_svd": {"name": "СВДм-2", "buy_price": 38000, "sell_price": 18240},
    "weapon_rp74": {"name": "РП-74", "buy_price": 40000, "sell_price": 19200},
    "weapon_gauss": {"name": "Гаусс-пушка", "buy_price": 90000, "sell_price": 43200},
    "weapon_raccoon": {"name": "Енот", "buy_price": 75000, "sell_price": 36000},
}

# Сезонные награды топ-3 — не продаются у торговца, выдаются в конце сезона.
SEASON_REWARD_WEAPONS: dict[str, dict[str, int | str]] = {
    "weapon_season_champion": {"name": "РПК «Чемпион Зоны»", "sell_price": 12000},
    "weapon_season_silver": {"name": "ВСС «Серебряный сталкер»", "sell_price": 8000},
}
SEASON_REWARD_ARMOR: dict[str, dict[str, int | str]] = {
    "armor_season_champion": {"name": "Костюм «Чемпион Зоны»", "sell_price": 10000},
    "armor_season_bronze": {"name": "Бронекостюм «Бронза сезона»", "sell_price": 6000},
}
SEASON_RANK_REWARDS: dict[int, tuple[tuple[str, str], ...]] = {
    1: (
        ("weapon_season_champion", "РПК «Чемпион Зоны»"),
        ("armor_season_champion", "Костюм «Чемпион Зоны»"),
    ),
    2: (("weapon_season_silver", "ВСС «Серебряный сталкер»"),),
    3: (("armor_season_bronze", "Бронекостюм «Бронза сезона»"),),
    4: (("artifact_fire", "Арт «Жар»"),),
    5: (("artifact_blood", "Арт «Кровь»"),),
    6: (("artifact_power", "Арт «Сила»"),),
    7: (("artifact_vitality", "Арт «Живучесть»"),),
    8: (("artifact_antirad", "Арт «Антирад»"),),
    9: (("artifact_crystal", "Арт «Кристалл»"),),
    10: (("artifact", "Артефакт Зоны"),),
}
SEASON_REWARD_ITEM_KEYS: frozenset[str] = frozenset(SEASON_REWARD_WEAPONS) | frozenset(SEASON_REWARD_ARMOR)

# --- Экономика: множители цен покупки/продажи ---
# T1 (бармен этап 1): ×5; остальное оружие/броня: ×10.
_T1_GEAR_KEYS: frozenset[str] = frozenset(
    {
        "weapon_pm",
        "weapon_fort12",
        "weapon_sawedoff",
        "armor_leather",
    }
)
_FOOD_CONSUMABLE_KEYS: frozenset[str] = frozenset(
    {
        "bread",
        "sausage",
        "stew",
        "water_bottle",
        "mineral_water",
        "beard_tea",
        "energy_drink",
        "vodka",
    }
)
_MEDKIT_KEYS: frozenset[str] = frozenset(
    {
        "medkit",
        "medkit_army",
        "medkit_science",
        "antirad",
        "antibiotic",
    }
)


def _scale_item_prices(item: dict[str, int | str], mult: int) -> None:
    if mult == 1:
        return
    if "buy_price" in item:
        item["buy_price"] = int(item["buy_price"]) * mult
    if "sell_price" in item:
        item["sell_price"] = int(item["sell_price"]) * mult


def round_shop_price(value: int) -> int:
    """Округление цены до «круглых» сумм: остаток >51 — вверх, иначе вниз (до сотен; ниже 52 — до десятков)."""
    if value <= 0:
        return 0
    if value < 52:
        base = (value // 10) * 10
        rem = value % 10
        if rem > 5:
            return base + 10
        return base
    base = (value // 100) * 100
    rem = value % 100
    if rem > 51:
        return base + 100
    return base


for _key, _item in ARMOR_CATALOG.items():
    _scale_item_prices(_item, 5 if _key in _T1_GEAR_KEYS else 10)
for _key, _item in WEAPON_CATALOG.items():
    _scale_item_prices(_item, 5 if _key in _T1_GEAR_KEYS else 10)

# Еда/аптечки ×5; велик ×3; машины ×10; Отклик ×3; прочие детекторы ×10.
_scale_item_prices(SHOP_ITEMS["bicycle"], 3)
_scale_item_prices(SHOP_ITEMS["niva"], 10)
_scale_item_prices(SHOP_ITEMS["truck"], 10)
_scale_item_prices(SHOP_ITEMS["detector_otklik"], 3)
_scale_item_prices(SHOP_ITEMS["detector_medved"], 10)
_scale_item_prices(SHOP_ITEMS["detector_veles"], 10)
_scale_item_prices(SHOP_ITEMS["detector_svarog"], 10)
for _key in _FOOD_CONSUMABLE_KEYS | _MEDKIT_KEYS:
    if _key in SHOP_ITEMS:
        _scale_item_prices(SHOP_ITEMS[_key], 5)

# Снижение расходников: средний коэфф. ≈0.443 от прежних финальных цен (аптечки заданы явно).
_CONSUMABLE_PRICE_OVERRIDES: dict[str, tuple[int, int]] = {
    "medkit": (500, 231),
    "medkit_army": (1000, 400),
    "medkit_science": (1500, 600),
    "antirad": (886, 288),
    "antibiotic": (3500, 1200),
    "energy_drink": (554, 248),
    "vodka": (332, 111),
    "bread": (111, 35),
    "sausage": (222, 73),
    "stew": (554, 184),
    "water_bottle": (111, 35),
    "mineral_water": (222, 73),
    "beard_tea": (554, 184),
    "ammo_pack": (53, 24),
    "ammo_pistol": (50, 20),
    "ammo_shotgun": (90, 36),
    "ammo_rifle": (120, 48),
    "ammo_gauss": (300, 120),
    "diesel_can": (199, 89),
    "gasoline_can": (100, 44),
    "fuel_can": (199, 89),
}
for _key, (_buy, _sell) in _CONSUMABLE_PRICE_OVERRIDES.items():
    if _key in SHOP_ITEMS:
        SHOP_ITEMS[_key]["buy_price"] = round_shop_price(_buy)
        SHOP_ITEMS[_key]["sell_price"] = round_shop_price(_sell)
for _ammo_key, _buy, _sell in (
    ("ammo_pistol", 50, 20),
    ("ammo_shotgun", 90, 36),
    ("ammo_rifle", 120, 48),
    ("ammo_gauss", 300, 120),
):
    SHOP_ITEMS[_ammo_key]["buy_price"] = _buy
    SHOP_ITEMS[_ammo_key]["sell_price"] = _sell
if "nitrous_oxide" in SHOP_ITEMS:
    SHOP_ITEMS["nitrous_oxide"]["buy_price"] = 1250
    SHOP_ITEMS["nitrous_oxide"]["sell_price"] = 500

# Обрез сильнее ПМ — фиксируем цену выше ПМ (после T1×5).
WEAPON_CATALOG["weapon_sawedoff"]["buy_price"] = 17000
WEAPON_CATALOG["weapon_sawedoff"]["sell_price"] = 8160

# Legacy callback alias used in keyboards.
WEAPON_CATALOG["weapon_fora12"] = WEAPON_CATALOG["weapon_fort12"]
ARMOR_CATALOG["armor_sunrise"] = ARMOR_CATALOG["armor_zarya"]
ARMOR_CATALOG["armor_berill5m"] = ARMOR_CATALOG["armor_bulat"]
ARMOR_CATALOG["armor_exoskeleton"] = ARMOR_CATALOG["armor_exo"]
WEAPON_CATALOG.update(SEASON_REWARD_WEAPONS)
ARMOR_CATALOG.update(SEASON_REWARD_ARMOR)

SHOP_ITEMS.update(ARMOR_CATALOG)
SHOP_ITEMS.update(WEAPON_CATALOG)
for _season_key in SEASON_REWARD_ITEM_KEYS:
    SHOP_ITEMS.pop(_season_key, None)

# Координаты должны совпадать с app/zone_map.py, чтобы время перехода
# соответствовало визуальной дистанции на карте.
MAP_TRAVEL_POINTS: dict[str, tuple[int, int]] = {
    "Кордон": (110, 530),
    "Свалка": (250, 470),
    "Росток": (395, 410),
    "Армейские склады": (195, 250),
    "НИИ Агропром": (340, 320),
    "Янтарь": (735, 180),
    "Болото": (115, 365),
    "Темная долина": (510, 520),
    "Рыжий лес": (730, 235),
    "Радар": (740, 125),
    "Припять": (780, 80),
    "ЧАЭС": (715, 45),
    "Тунель": (440, 220),
}

# Сила растёт по ступеням: следующий класс оружия/брони обычно сильнее предыдущего.
# Потолок профиля: оружие + броня + арт ≤ 20 (см. equipment_power).
WEAPON_RATING_BY_NAME: dict[str, int] = {
    "Нож": 2,
    "ПМ": 2,
    "Фора-12": 3,
    "Обрез": 3,
    "Гадюка-5": 4,
    "Чейзер-13": 4,
    "АКС-74У": 5,
    "АК-74": 6,
    "СПАС-12": 6,
    "ТРс-301": 7,
    "ИЛ86": 7,
    "АН-94": 8,
    "ГП37": 8,
    "Винтарь ВС": 9,
    "СВДм-2": 9,
    "РП-74": 9,
    "Енот": 9,
    "Гаусс-пушка": 10,
    "РПК «Чемпион Зоны»": 10,
    "ВСС «Серебряный сталкер»": 9,
}

ARMOR_RATING_BY_NAME: dict[str, int] = {
    "Куртка новичка": 2,
    "Кожаная куртка": 2,
    "Сталкерский бронежилет": 3,
    "Комбинезон «Заря»": 4,
    "ПСЗ-7 «Долг»": 4,  # legacy item in old inventories
    "Берилл-5М «Булат»": 5,
    "Костюм СЕВА": 6,
    "Научный костюм": 7,
    "Экзоскелет": 8,
    "Носорог": 9,
    "Костюм «Чемпион Зоны»": 9,
    "Бронекостюм «Бронза сезона»": 8,
}
# Совместимость с историческими названиями экипировки из старых сохранений.
ARMOR_RATING_BY_NAME.setdefault("Бронежилет сталкера", ARMOR_RATING_BY_NAME["Сталкерский бронежилет"])
ARMOR_RATING_BY_NAME.setdefault("Усиленный бронекостюм", ARMOR_RATING_BY_NAME["ПСЗ-7 «Долг»"])
ARMOR_RATING_BY_NAME.setdefault("Штурмовой экзоскелет", ARMOR_RATING_BY_NAME["Экзоскелет"])

# Шанс полного блока входящего урона (мобы/NPC) по названию экипированной брони.
ARMOR_BLOCK_CHANCE_BY_NAME: dict[str, int] = {
    "Куртка новичка": 0,
    "Кожаная куртка": 3,
    "Сталкерский бронежилет": 5,
    "Комбинезон «Заря»": 7,
    "ПСЗ-7 «Долг»": 8,
    "Берилл-5М «Булат»": 10,
    "Костюм СЕВА": 12,
    "Научный костюм": 14,
    "Экзоскелет": 16,
    "Носорог": 20,
    "Костюм «Чемпион Зоны»": 20,
    "Бронекостюм «Бронза сезона»": 16,
}
ARMOR_BLOCK_CHANCE_BY_NAME.setdefault(
    "Бронежилет сталкера", ARMOR_BLOCK_CHANCE_BY_NAME["Сталкерский бронежилет"]
)
ARMOR_BLOCK_CHANCE_BY_NAME.setdefault(
    "Усиленный бронекостюм", ARMOR_BLOCK_CHANCE_BY_NAME["ПСЗ-7 «Долг»"]
)
ARMOR_BLOCK_CHANCE_BY_NAME.setdefault(
    "Штурмовой экзоскелет", ARMOR_BLOCK_CHANCE_BY_NAME["Экзоскелет"]
)

# Постоянное смягчение входящего урона (−HP) с 100% шансом по экипированной броне.
# Правило баланса: 1 очко силы брони = 2.5 гашения урона (генерируется из ARMOR_RATING_BY_NAME,
# который уже включает legacy-алиасы названий).
ARMOR_MITIGATION_PER_POWER = 2.5
ARMOR_MITIGATION_BY_NAME: dict[str, int] = {
    name: int(rating * ARMOR_MITIGATION_PER_POWER + 0.5)
    for name, rating in ARMOR_RATING_BY_NAME.items()
}


ITEM_LABELS = {
    "energy_drink": "Энергетик",
    "nitrous_oxide": "Закись азота (N2O)",
    "medkit": "Аптечка",
    "medkit_army": "Армейская аптечка",
    "medkit_science": "Научная аптечка",
    "ammo_pistol": "Патроны (пистолетные)",
    "ammo_shotgun": "Патроны (дробь)",
    "ammo_rifle": "Патроны (винтовочные)",
    "ammo_gauss": "Элементы для гаусса",
    "ammo_pack": "Патроны",
    "artifact": "Артефакт Зоны",
    "artifact_power": "Арт «Сила»",
    "artifact_vitality": "Арт «Живучесть»",
    "artifact_antirad": "Арт «Антирад»",
    "artifact_junk_slime": "Слизь",
    "artifact_junk_bolt": "Ржавый болт",
    "artifact_junk_battery": "Дохлая батарейка",
    "artifact_junk_flash": "Вспышка",
    "artifact_junk_stone": "Аномальный камень",
    "artifact_junk_fog": "Сгусток тумана",
    "artifact_junk_splinter": "Осколок",
    "mutant_tail": "Хвост мутанта",
    "mutant_claw": "Коготь мутанта",
    "mutant_fang": "Клык мутанта",
    "mutant_eye": "Глаз мутанта",
    "mutant_hide": "Шкура мутанта",
    "mutant_tendril": "Щупальце кровососа",
    "vodka": "Водка",
    "antirad": "Антирад",
    "antibiotic": "Антибиотик",
    "bread": "Хлеб",
    "sausage": "Колбаса",
    "stew": "Тушёнка",
    "water_bottle": "Бутылка воды",
    "mineral_water": "Минералка",
    "beard_tea": "Чай Бороды",
    "diesel_can": "Канистра дизеля",
    "gasoline_can": "Канистра бензина",
    "fuel_can": "Канистра дизеля",
    "detector_otklik": "Детектор «Отклик»",
    "detector_medved": "Детектор «Медведь»",
    "detector_veles": "Детектор «Велес»",
    "detector_svarog": "Детектор «Сварог»",
    "sleeping_bag": "Спальник",
    "bicycle": "Велосипед",
    "stash_case": "Тайник",
    "stash_coordinates": "Координаты хабара",
    "radio_set": "Рация",
    "intel_document": "Документы",
    "intel_flash": "Флешка",
    "intel_diary": "Дневник",
    "artifact_predel1": "Артефакт «Предел-1»",
    "artifact_predel2": "Артефакт «Предел-2»",
    "artifact_predel3": "Артефакт «Предел-3»",
    "artifact_predel": "Артефакт «Предел»",
    "materials": "Стройматериалы",
    "armor_leather": "Кожаная куртка",
    "armor_stalker_vest": "Сталкерский бронежилет",
    "armor_psz7d": "ПСЗ-7 «Долг»",
    "armor_zarya": "Комбинезон «Заря»",
    "armor_bulat": "Берилл-5М «Булат»",
    "armor_seva": "Костюм СЕВА",
    "armor_scientific": "Научный костюм",
    "armor_exo": "Экзоскелет",
    "armor_nosorog": "Носорог",
    "armor_sunrise": "Комбинезон «Заря»",
    "armor_berill5m": "Берилл-5М «Булат»",
    "armor_exoskeleton": "Экзоскелет",
    "armor_upgrade": "Улучшение брони (+1 защита)",
    "artifact_slot": "Доп. ячейка артефакта",
    "gear_upgrade": "Улучшение брони (+1 защита)",
    "weapon_pm": "ПМ",
    "weapon_fort12": "Фора-12",
    "weapon_fora12": "Фора-12",
    "weapon_sawedoff": "Обрез",
    "weapon_chaser13": "Чейзер-13",
    "weapon_spas12": "СПАС-12",
    "weapon_mp5": "Гадюка-5",
    "weapon_aks74u": "АКС-74У",
    "weapon_ak74": "АК-74",
    "weapon_lr300": "ТРс-301",
    "weapon_il86": "ИЛ86",
    "weapon_gp37": "ГП37",
    "weapon_an94": "АН-94",
    "weapon_vintar": "Винтарь ВС",
    "weapon_svd": "СВДм-2",
    "weapon_rp74": "РП-74",
    "weapon_gauss": "Гаусс-пушка",
    "weapon_raccoon": "Енот",
    "weapon_season_champion": "РПК «Чемпион Зоны»",
    "weapon_season_silver": "ВСС «Серебряный сталкер»",
    "armor_season_champion": "Костюм «Чемпион Зоны»",
    "armor_season_bronze": "Бронекостюм «Бронза сезона»",
    "artifact_fire": "Арт «Жар»",
    "artifact_blood": "Арт «Кровь»",
}

FUEL_CAN_DIESEL_AMOUNT = 5
FUEL_CAN_GASOLINE_AMOUNT = 5
SHOP_FUEL_CAN_ALIASES: dict[str, str] = {"fuel_can": "diesel_can"}
SHOP_ITEM_ALIASES: dict[str, str] = {"gear_upgrade": "armor_upgrade", **SHOP_FUEL_CAN_ALIASES}


def normalize_shop_item_key(item_key: str) -> str:
    return SHOP_ITEM_ALIASES.get(item_key, item_key)


# Предпочитаемые ключи каталога для групп алиасов (продажа/инвентарь).
_SELL_CANONICAL_PRIMARY: dict[str, tuple[str, ...]] = {
    "armor_zarya": ("armor_sunrise",),
    "armor_berill5m": ("armor_bulat",),
    "armor_exo": ("armor_exoskeleton",),
    "weapon_fora12": ("weapon_fort12",),
}


def canonical_sell_item_key(item_key: str) -> str:
    item_key = normalize_shop_item_key(item_key)
    for canon, aliases in _SELL_CANONICAL_PRIMARY.items():
        if item_key == canon or item_key in aliases:
            return canon
    return item_key


def _reject_if_player_busy(
    storage: Storage, telegram_id: int, *, skip: str | None = None
) -> ActionResult | None:
    from app.player_busy import player_busy_reason

    busy = player_busy_reason(storage, telegram_id, skip=skip)
    if busy:
        return ActionResult(False, busy)
    return None


ARTIFACT_DETECTORS: tuple[tuple[str, str, int], ...] = (
    ("detector_otklik", "Отклик", 17),
    ("detector_medved", "Медведь", 20),
    ("detector_veles", "Велес", 35),
    ("detector_svarog", "Сварог", 50),
)
ARTIFACT_SEARCH_ENERGY_COST = 10

# Экипированные арты: бонус к силе и/или к запасу HP.
ARTIFACT_EQUIP_BONUSES: dict[str, dict[str, int]] = {
    "Артефакт Зоны": {"power": 2, "hp": 0},
    "Артефакт": {"power": 2, "hp": 0},  # старые сейвы
    "Арт «Сила»": {"power": 1, "hp": 0},
    "Арт «Живучесть»": {"power": 1, "hp": 10},
    "Арт «Антирад»": {"power": 2, "hp": 0},
    "Артефакт «Предел»": {"power": 0, "hp": 20},
}
ARTIFACT_ENERGY_REGEN_NAMES = frozenset({"Артефакт Зоны", "Артефакт"})
# Пассивная очистка радиации (−1 раз в N минут), пока арт экипирован.
ARTIFACT_RAD_CLEANSE_NAMES = frozenset({"Арт «Антирад»"})
ARTIFACT_RAD_CLEANSE_INTERVAL_MINUTES = 10
ARTIFACT_RAD_CLEANSE_AMOUNT = 1
ARTIFACT_INVENTORY_TO_NAME: dict[str, str] = {
    "artifact": "Артефакт Зоны",
    "artifact_power": "Арт «Сила»",
    "artifact_vitality": "Арт «Живучесть»",
    "artifact_antirad": "Арт «Антирад»",
    "artifact_predel": "Артефакт «Предел»",
    "artifact_junk_slime": "Слизь",
    "artifact_junk_bolt": "Ржавый болт",
    "artifact_junk_battery": "Дохлая батарейка",
    "artifact_junk_flash": "Вспышка",
    "artifact_junk_stone": "Аномальный камень",
    "artifact_junk_fog": "Сгусток тумана",
    "artifact_junk_splinter": "Осколок",
}
ARTIFACT_NAME_TO_INVENTORY: dict[str, str] = {
    **{name: key for key, name in ARTIFACT_INVENTORY_TO_NAME.items()},
    "Артефакт": "artifact",  # старые сейвы
}
# Ценные арты (квесты/рейды) и полный список ключей.
ARTIFACT_DROP_KEYS = ("artifact", "artifact_power", "artifact_vitality", "artifact_antirad", "artifact_predel")
ARTIFACT_EQUIP_SLOT_KEYS: tuple[str, ...] = ("artifact", "artifact_2", "artifact_3")
MAX_ARTIFACT_EQUIP_SLOTS = 3
# База ячеек от тира брони (тайник/ассортимент): T1–T2 без артов, T3=1, T4+=2.
ARTIFACT_BASE_SLOTS_BY_ARMOR_TIER: dict[int, int] = {1: 0, 2: 0, 3: 1, 4: 2, 5: 2}
ARTIFACT_JUNK_KEYS = (
    "artifact_junk_slime",
    "artifact_junk_bolt",
    "artifact_junk_battery",
    "artifact_junk_flash",
    "artifact_junk_stone",
    "artifact_junk_fog",
    "artifact_junk_splinter",
)
ARTIFACT_JUNK_POOL_PERCENT = 60.0
ARTIFACT_JUNK_POOL_KEY = "__junk__"
ARTIFACT_ALL_KEYS = ARTIFACT_DROP_KEYS + ARTIFACT_JUNK_KEYS

# Артефакт Зоны — 0.1% на любой локации при поиске.
ARTIFACT_ZONE_GLOBAL_PERCENT = 0.1
# Топ-арты с фиксированным % на конкретной локации (без множителя детектора).
ARTIFACT_TOP_LOCATION_SPAWNS: dict[str, tuple[tuple[str, float], ...]] = {
    "Радар": (("artifact_antirad", 0.1),),
}

# Локальные спавны при поиске детектором (кроме глобальной Зоны и топ-артов), %:
ARTIFACT_LOCATION_SPAWNS: dict[str, tuple[tuple[str, float], ...]] = {
    "Болото": (
        ("artifact_power", 5.0),
        ("artifact_vitality", 5.0),
        (ARTIFACT_JUNK_POOL_KEY, ARTIFACT_JUNK_POOL_PERCENT),
    ),
    "Радар": (
        ("artifact_power", 7.0),
        ("artifact_vitality", 6.0),
        (ARTIFACT_JUNK_POOL_KEY, ARTIFACT_JUNK_POOL_PERCENT),
    ),
    "Янтарь": ((ARTIFACT_JUNK_POOL_KEY, ARTIFACT_JUNK_POOL_PERCENT),),
    "Рыжий лес": ((ARTIFACT_JUNK_POOL_KEY, ARTIFACT_JUNK_POOL_PERCENT),),
    "Темная долина": (
        ("artifact_power", 7.0),
        ("artifact_vitality", 7.0),
        (ARTIFACT_JUNK_POOL_KEY, ARTIFACT_JUNK_POOL_PERCENT),
    ),
    "НИИ Агропром": ((ARTIFACT_JUNK_POOL_KEY, ARTIFACT_JUNK_POOL_PERCENT),),
    "Свалка": ((ARTIFACT_JUNK_POOL_KEY, ARTIFACT_JUNK_POOL_PERCENT),),
    "Росток": ((ARTIFACT_JUNK_POOL_KEY, ARTIFACT_JUNK_POOL_PERCENT),),
    "Кордон": ((ARTIFACT_JUNK_POOL_KEY, ARTIFACT_JUNK_POOL_PERCENT),),
    "Армейские склады": ((ARTIFACT_JUNK_POOL_KEY, ARTIFACT_JUNK_POOL_PERCENT),),
}

# Запасной пул мусора, если локации нет в таблице.
ARTIFACT_DEFAULT_JUNK_SPAWNS: tuple[tuple[str, float], ...] = (
    (ARTIFACT_JUNK_POOL_KEY, ARTIFACT_JUNK_POOL_PERCENT),
)

# Абсолютные шансы дропа typed-артов в награде рейдов (взаимоисключающие), %:
ARTIFACT_RAID_DROP_RATES_PERCENT: tuple[tuple[str, float], ...] = (
    ("artifact_power", 3.0),
    ("artifact_vitality", 3.0),
)


def is_valuable_artifact_key(item_key: str) -> bool:
    """Ценный арт (не мусор): можно экипировать и учитывать в статистике находок."""
    return normalize_shop_item_key(item_key) in ARTIFACT_DROP_KEYS


def record_valuable_artifact_found_stat(storage: Storage, telegram_id: int, art_key: str) -> None:
    if is_valuable_artifact_key(art_key):
        storage.add_player_stat(telegram_id, "artifacts_found", 1)


def _artifact_equip_slot_for_name(character: Character, artifact_name: str) -> str | None:
    """Слот экипировки, где надет арт с данным отображаемым именем, или None."""
    for slot_key in ARTIFACT_EQUIP_SLOT_KEYS:
        equipped = str(character.equipment.get(slot_key, "Нет") or "Нет")
        if equipped == artifact_name:
            return slot_key
    return None


def pick_weighted_raid_artifact_key() -> str:
    """Выбор typed-арта для награды рейда (без Зоны/Антирада)."""
    keys = [key for key, _ in ARTIFACT_RAID_DROP_RATES_PERCENT]
    weights = [float(chance) for _, chance in ARTIFACT_RAID_DROP_RATES_PERCENT]
    return random.choices(keys, weights=weights, k=1)[0]


def best_detector_base_chance(character: Character) -> int:
    """Базовый шанс лучшего детектора в инвентаре (0 если нет)."""
    best = 0
    for key, _, base in ARTIFACT_DETECTORS:
        if int(character.inventory.get(key, 0)) > 0:
            best = int(base)
    return best


def _detector_spawn_multiplier(base_chance: int) -> float:
    """Отклик ~0.55 … Сварог 1.0 — усиливает локальный спавн (не Зону)."""
    return max(0.35, min(1.25, float(base_chance) / 50.0 + 0.35))


def location_artifact_spawn_table(location: str, detector_base_chance: int) -> list[tuple[str, float]]:
    """Таблица абсолютных %: Зона 0.1% везде + топ-локальные + локальные (детектор усиливает обычные)."""
    mult = _detector_spawn_multiplier(detector_base_chance)
    table: list[tuple[str, float]] = [("artifact", ARTIFACT_ZONE_GLOBAL_PERCENT)]
    for key, chance in ARTIFACT_TOP_LOCATION_SPAWNS.get(location, ()):
        table.append((key, float(chance)))
    local = ARTIFACT_LOCATION_SPAWNS.get(location, ARTIFACT_DEFAULT_JUNK_SPAWNS)
    for key, chance in local:
        if key == "artifact":
            # Зона уже учтена глобально — не дублируем.
            continue
        table.append((key, float(chance) * mult))
    return table


def roll_location_artifact_drop(
    location: str,
    detector_base_chance: int,
    storage: Storage | None = None,
    *,
    detector_key: str | None = None,
    drop_mult: float = 1.0,
) -> str | None:
    """Поиск на локации: взаимоисключающий ролл по таблице спавна."""
    table = location_artifact_spawn_table(location, detector_base_chance)
    if storage is not None:
        from app.artifact_features import artifact_hotspot_multiplier

        mult = artifact_hotspot_multiplier(storage, location)
        if mult > 1.0:
            table = [
                (key, float(chance) * mult if key not in ("artifact",) and key not in dict(ARTIFACT_TOP_LOCATION_SPAWNS.get(location, ())) else float(chance))
                for key, chance in table
            ]
    if drop_mult > 1.0:
        top_keys = dict(ARTIFACT_TOP_LOCATION_SPAWNS.get(location, ()))
        table = [
            (
                key,
                float(chance) * drop_mult
                if key not in ("artifact",) and key not in top_keys
                else float(chance),
            )
            for key, chance in table
        ]
    roll = random.uniform(0.0, 100.0)
    cumulative = 0.0
    for key, chance in table:
        cumulative += float(chance)
        if roll < cumulative:
            resolved_key = random.choice(ARTIFACT_JUNK_KEYS) if key == ARTIFACT_JUNK_POOL_KEY else key
            if detector_key:
                from app.artifact_features import filter_roll_for_detector

                return filter_roll_for_detector(detector_key, resolved_key)
            return resolved_key
    return None


def describe_location_artifact_spawns(location: str) -> str:
    local = ARTIFACT_LOCATION_SPAWNS.get(location, ARTIFACT_DEFAULT_JUNK_SPAWNS)
    parts = [f"Артефакт Зоны ~{ARTIFACT_ZONE_GLOBAL_PERCENT}%"]
    for key, chance in ARTIFACT_TOP_LOCATION_SPAWNS.get(location, ()):
        parts.append(f"{ITEM_LABELS.get(key, key)} ~{chance:g}%")
    for key, chance in local:
        if key == "artifact":
            continue
        if key == ARTIFACT_JUNK_POOL_KEY:
            parts.append(f"Мусор ~{chance:g}%")
        else:
            parts.append(f"{ITEM_LABELS.get(key, key)} ~{chance:g}%")
    return ", ".join(parts)


EQUIP_PAGE_SIZE = 8
EQUIP_SLOT_LABELS = {
    "weapon": "Оружие",
    "armor": "Броня",
    "artifact": "Артефакты",
}

WAREHOUSE_ITEM_KEYS = ("ammo_pack", "medkit", "energy_drink", "artifact", "materials")
TREASURY_WITHDRAW_MIN_RANK = 5

# Дроп контрабанды (независимые роллы при успехе).
SMUGGLING_CONSUMABLE_CHANCE = 20  # аптечка / еда / вода — каждый тип отдельно
SMUGGLING_ARMOR_T2_CHANCE = 4
SMUGGLING_WEAPON_T1_CHANCE = 4
SMUGGLING_OTKLIK_CHANCE = 5
SMUGGLING_META_PREFIX = "smuggle:active:"
SMUGGLING_BASE_CHANCE = 42
SMUGGLING_TRANSPORT_BONUS: dict[str, int] = {
    "truck": 12,
    "niva": 6,
    "bicycle": 3,
    "foot": 0,
}
SMUGGLING_REWARD_MIN = 220
SMUGGLING_REWARD_MAX = 320
SMUGGLING_FAIL_PENALTY_MIN = 150
SMUGGLING_FAIL_PENALTY_MAX = 300

SMUGGLING_FOOD_DROP_KEYS = ("bread", "sausage", "stew")
SMUGGLING_WATER_DROP_KEYS = ("water_bottle", "mineral_water")
SMUGGLING_ARMOR_T2_KEYS = ("armor_stalker_vest", "armor_zarya")
SMUGGLING_WEAPON_T1_KEYS = ("weapon_pm", "weapon_sawedoff", "weapon_fort12")

# Тайники (кейсы): дроп с активностей + покупка у торговца.
STASH_ITEM_KEY = "stash_case"
STASH_ACTIVITY_DROP_CHANCE = 5  # %
STASH_CONSUMABLE_KEYS = (
    "medkit",
    "medkit_army",
    "medkit_science",
    "energy_drink",
    "ammo_pack",
    "vodka",
    "antirad",
    "antibiotic",
    "bread",
    "sausage",
    "stew",
    "water_bottle",
    "mineral_water",
    "beard_tea",
)
# Шансы тира снаряги при открытии (броня ИЛИ оружие, не вместе), %:
# 1-2: 4%, 3: 2%, 4: 1%, 5: 0.05%
STASH_GEAR_TIER_CHANCES: tuple[tuple[int | tuple[int, int], float], ...] = (
    ((1, 2), 4.0),
    (3, 2.0),
    (4, 1.0),
    (5, 0.05),
)
STASH_ARMOR_BY_TIER: dict[int, tuple[str, ...]] = {
    1: ("armor_leather",),
    2: ("armor_stalker_vest", "armor_zarya"),
    3: ("armor_bulat", "armor_seva", "armor_scientific"),
    4: ("armor_exo",),
    5: ("armor_nosorog",),
}
STASH_WEAPON_BY_TIER: dict[int, tuple[str, ...]] = {
    1: ("weapon_pm", "weapon_fort12", "weapon_sawedoff"),
    2: ("weapon_mp5", "weapon_chaser13", "weapon_aks74u"),
    3: ("weapon_ak74", "weapon_spas12"),
    4: ("weapon_lr300", "weapon_il86", "weapon_an94"),
    5: (
        "weapon_gp37",
        "weapon_vintar",
        "weapon_svd",
        "weapon_rp74",
        "weapon_gauss",
        "weapon_raccoon",
    ),
}

# Ассортимент оружия у торговца: 5 этапов (накопительно).
# 1=T1 … 4=T4, 5=T5 (включая Гаусс и Енот).
TRADER_WEAPON_TIER_MAX = 5
TRADER_WEAPON_TIER_META_PREFIX = "trader:weapon_tier:"
# Стоимость перехода на этот этап (с предыдущего).
TRADER_WEAPON_TIER_UPGRADE_COST: dict[int, int] = {
    2: 120_000,
    3: 300_000,
    4: 750_000,
    5: 1_200_000,
}
TRADER_WEAPON_STAGE_LABELS: dict[int, str] = {
    1: "T1 — пистолеты и обрезы",
    2: "T2 — ПП и дробовики",
    3: "T3 — автоматы и СПАС",
    4: "T4 — штурмовые винтовки",
    5: "T5 — топ + Гаусс и Енот",
}


def trader_weapon_tier_meta_key(telegram_id: int) -> str:
    return f"{TRADER_WEAPON_TIER_META_PREFIX}{int(telegram_id)}"


def get_trader_weapon_tier(storage: Storage, telegram_id: int) -> int:
    raw = storage.get_meta(trader_weapon_tier_meta_key(telegram_id))
    if raw is None or str(raw).strip() == "":
        return 1
    try:
        tier = int(raw)
    except (TypeError, ValueError):
        return 1
    return max(1, min(TRADER_WEAPON_TIER_MAX, tier))


def set_trader_weapon_tier(storage: Storage, telegram_id: int, tier: int) -> None:
    safe = max(1, min(TRADER_WEAPON_TIER_MAX, int(tier)))
    storage.set_meta(trader_weapon_tier_meta_key(telegram_id), str(safe))


def unlocked_trader_weapon_keys(tier: int) -> frozenset[str]:
    """Ключи оружия, доступные к покупке на данном этапе ассортимента."""
    safe = max(1, min(TRADER_WEAPON_TIER_MAX, int(tier)))
    unlocked: set[str] = set()
    for t in range(1, safe + 1):
        unlocked.update(STASH_WEAPON_BY_TIER.get(t, ()))
    # Алиас клавиатуры Фора-12.
    if "weapon_fort12" in unlocked:
        unlocked.add("weapon_fora12")
    return frozenset(unlocked)


def trader_weapon_is_unlocked(storage: Storage, telegram_id: int, item_key: str) -> bool:
    key = normalize_shop_item_key(item_key)
    if key == "weapon_fora12":
        key = "weapon_fort12"
    return key in unlocked_trader_weapon_keys(get_trader_weapon_tier(storage, telegram_id))


def upgrade_trader_weapon_tier(storage: Storage, telegram_id: int) -> ActionResult:
    """Купить следующий этап ассортимента оружия у торговца."""
    character = storage.get_character(telegram_id)
    if character is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(character):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    current = get_trader_weapon_tier(storage, telegram_id)
    if current >= TRADER_WEAPON_TIER_MAX:
        return ActionResult(
            False,
            f"Ассортимент оружия уже максимальный (этап {TRADER_WEAPON_TIER_MAX}/{TRADER_WEAPON_TIER_MAX}):\n"
            f"{TRADER_WEAPON_STAGE_LABELS[TRADER_WEAPON_TIER_MAX]}",
        )
    nxt = current + 1
    cost = int(TRADER_WEAPON_TIER_UPGRADE_COST.get(nxt, 0))
    if cost <= 0:
        return ActionResult(False, "Улучшение недоступно.")
    if not storage.change_money(telegram_id, -cost):
        return ActionResult(False, f"Недостаточно денег для улучшения ассортимента ({cost} RU).")
    set_trader_weapon_tier(storage, telegram_id, nxt)
    label = TRADER_WEAPON_STAGE_LABELS.get(nxt, f"этап {nxt}")
    return ActionResult(
        True,
        f"Ассортимент оружия улучшен до этапа {nxt}/{TRADER_WEAPON_TIER_MAX} (−{cost} RU).\nТеперь доступно: {label}.",
        payload={"trader_weapon_tier": nxt},
    )


def trader_weapon_assortment_blurb(storage: Storage, telegram_id: int) -> str:
    tier = get_trader_weapon_tier(storage, telegram_id)
    label = TRADER_WEAPON_STAGE_LABELS.get(tier, f"этап {tier}")
    lines = [f"🔫 Ассортимент оружия: этап {tier}/{TRADER_WEAPON_TIER_MAX} — {label}."]
    if tier < TRADER_WEAPON_TIER_MAX:
        nxt = tier + 1
        cost = int(TRADER_WEAPON_TIER_UPGRADE_COST.get(nxt, 0))
        nxt_label = TRADER_WEAPON_STAGE_LABELS.get(nxt, f"этап {nxt}")
        lines.append(f"Следующий этап ({nxt_label}) — {cost} RU.")
    else:
        lines.append("Максимум: T5 стволы, Гаусс-пушка и Енот.")
    return "\n".join(lines)
STASH_CONSUMABLE_DROP_CHANCE = 40  # % на каждый обычный расходник при открытии
# Редкие расходники в тайнике — пониженный шанс.
STASH_CONSUMABLE_DROP_CHANCE_BY_KEY: dict[str, int] = {
    "medkit_army": 15,
    "medkit_science": 10,
}

# Эффекты аптечек: heal HP, radiation delta (отрицательный = снятие).
MEDKIT_EFFECTS: dict[str, dict[str, int]] = {
    "medkit": {"heal": 25, "radiation": 0},
    "medkit_army": {"heal": 50, "radiation": 0},
    "medkit_science": {"heal": 75, "radiation": -15},
}
# Для заданий «Опасно»/«Невозможно» подходит любая аптечка (сначала обычная).
MEDKIT_QUEST_KEYS: tuple[str, ...] = ("medkit", "medkit_army", "medkit_science")


def _total_medkit_stock(character: Character) -> int:
    return sum(int(character.inventory.get(key, 0)) for key in MEDKIT_QUEST_KEYS)


def _consume_quest_medkits(storage: Storage, telegram_id: int, amount: int) -> bool:
    """Списывает аптечки любого типа, начиная с обычной."""
    if amount <= 0:
        return True
    remaining = amount
    consumed: list[tuple[str, int]] = []
    character = storage.get_character(telegram_id, refresh_energy=False)
    if character is None:
        return False
    for key in MEDKIT_QUEST_KEYS:
        if remaining <= 0:
            break
        have = int(character.inventory.get(key, 0))
        take = min(have, remaining)
        if take <= 0:
            continue
        if not storage.remove_item(telegram_id, key, take):
            for undone_key, undone_qty in consumed:
                storage.add_item(telegram_id, undone_key, undone_qty)
            return False
        consumed.append((key, take))
        remaining -= take
        character = storage.get_character(telegram_id, refresh_energy=False)
        if character is None:
            for undone_key, undone_qty in consumed:
                storage.add_item(telegram_id, undone_key, undone_qty)
            return False
    if remaining > 0:
        for undone_key, undone_qty in consumed:
            storage.add_item(telegram_id, undone_key, undone_qty)
        return False
    return True

AUCTION_DEFAULT_LOTS: dict[str, tuple[str, int, int]] = {
    "artifact": ("artifact", 1, 5000),
    "artifact_power": ("artifact_power", 1, 1100),
    "artifact_vitality": ("artifact_vitality", 1, 1100),
    "artifact_antirad": ("artifact_antirad", 1, 5000),
    "ammo_pack": ("ammo_pack", 5, 520),
    "medkit": ("medkit", 2, 420),
}

# Предметы, которые можно выставить на биржу собственным лотом (не экипировка).
CUSTOM_EXCHANGE_ITEM_KEYS = {
    "ammo_pack",
    "energy_drink",
    "vodka",
    "diesel_can",
    "gasoline_can",
    "fuel_can",
    "stash_case",
}
CUSTOM_EXCHANGE_ITEM_PREFIXES = ("artifact", "detector_", "medkit")

EXCHANGE_CATEGORIES = ("all", "artifact", "consumable", "fuel", "other")
EXCHANGE_CATEGORY_LABELS = {
    "all": "все",
    "artifact": "артефакты",
    "consumable": "расходники",
    "fuel": "топливо",
    "other": "прочее",
}
EXCHANGE_FUEL_ITEM_KEYS = {"diesel_can", "gasoline_can", "fuel_can"}
EXCHANGE_CONSUMABLE_ITEM_KEYS = {
    "ammo_pack",
    "medkit",
    "medkit_army",
    "medkit_science",
    "energy_drink",
    "vodka",
    "antirad",
    "antibiotic",
    "bread",
    "sausage",
    "stew",
    "water_bottle",
    "mineral_water",
    "beard_tea",
}

MARKET_SELL_FEE_PERCENT = 25
EXCHANGE_SELL_FEE_PERCENT = 30
TRADER_EQUIPMENT_SELL_RATE = 1 / 3
RESOURCE_POINT_INCOME_PER_HOUR = 200
BASE_POINT_INCOME_PER_HOUR = 100
BASE_FORTIFY_COST_RU = 100_000
BASE_FORTIFY_POWER_BONUS = 1
POINTS_INCOME_META_KEY = "points_income_last_at"
POINTS_INCOME_MAX_HOURS = 16
EMISSION_INTERVAL_HOURS = 6
EMISSION_WARN_60_MINUTES = 60
EMISSION_WARN_30_MINUTES = 30
EMISSION_META_AT = "emission_at"
EMISSION_META_WARN60 = "emission_warn60_sent"
EMISSION_META_WARN30 = "emission_warn30_sent"
EMISSION_META_PHASE = "emission_phase"
EMISSION_META_WAVE_AT = "emission_wave_at"
EMISSION_PROTECT_META_PREFIX = "emission_protect_until:"
EMISSION_PROTECT_BUFFER_MINUTES = 5
EMISSION_WAVE_GAP_MINUTES = 7
ZONE_EVENT_META_NEXT_AT = "zone_event_next_at"
ZONE_EVENT_INTERVAL_MIN_MINUTES = 30
ZONE_EVENT_INTERVAL_MAX_MINUTES = 90

DAILY_CONTRACTS_META_KEY = "contracts:daily"
WEEKLY_CONTRACT_META_KEY = "contracts:weekly"
DAILY_CONTRACTS_COUNT = 3
DAILY_CONTRACT_BONUS_PERCENT = 50
DAILY_CONTRACT_RATING_BONUS = 2
WEEKLY_CONTRACT_BONUS_PERCENT = 120
WEEKLY_CONTRACT_RATING_BONUS = 7
WEEKLY_CONTRACT_DIFFICULTIES: frozenset[str] = frozenset({"heavy", "impossible"})
DAILY_ARTIFACT_HUNT_META_PREFIX = "contracts:daily_hunt:"
DAILY_ARTIFACT_HUNT_BONUS_RU = 1000
CONTRACT_DAILY_DONE_META_PREFIX = "contracts:daily_done:"
CONTRACT_WEEKLY_DONE_META_PREFIX = "contracts:weekly_done:"

RATING_SEASON_META_KEY = "rating_season"
RATING_SEASON_LENGTH_DAYS = 14
MSK_TZ = ZoneInfo("Europe/Moscow")

FACTION_HOME_BASE: dict[str, str] = {
    "Долг": "Росток",
    "Свобода": "Армейские склады",
    "Нейтралы": "Кордон",
    "Бандиты": "Свалка",
    "Монолит": "ЧАЭС",
}

# Лор для доната в казну группировки (показывается в ЛС).
FACTION_LORE: dict[str, str] = {
    "Долг": (
        "🏛 Как сформировался «Долг»\n"
        "После второго Выброса военные и сталкеры, видевшие слишком много трупов, "
        "собрались на Ростке. Не ради хабара — чтобы поставить Зоне рамку. "
        "Из разрозненных блокпостов выросла армия: уставы, патрули, казна на патроны.\n\n"
        "🎯 Цель в Зоне\n"
        "Уничтожать мутантов, держать аномалии под контролем и не дать Зоне выплеснуться "
        "за периметр. Каждый рубль в казну — это ещё один боец на стене, а не очередной "
        "труп в Рыжем лесу."
    ),
    "Свобода": (
        "🏛 Как сформировалась «Свобода»\n"
        "Те, кому осточертели кордоны и приказы, ушли на Армейские склады. "
        "Из дезертиров, анархистов и охотников за артефактами сложилась вольница: "
        "свои законы, своя охрана, никакой «большой земли» над душой.\n\n"
        "🎯 Цель в Зоне\n"
        "Жить в Зоне, а не бояться её. Свободный доступ к аномалиям, исследования "
        "без намордника и право каждого сталкера идти туда, куда зовёт детектор. "
        "Казна кормит патрули складов и держит ворота открытыми для своих."
    ),
    "Нейтралы": (
        "🏛 Как сформировались «Нейтралы»\n"
        "Кордон всегда был перекрёстком: кто не влез в Долг и не сбежал в Свободу, "
        "остался торговать, водить новичков и не лезть в чужие войны. Из этого "
        "получилась сеть: Сидорович, проводники, вольные сталкеры без знамени.\n\n"
        "🎯 Цель в Зоне\n"
        "Выжить и заработать, не становясь пушечным мясом группировок. "
        "Нейтралы держат тропы, базары и хабар. Деньги в казну — это еда новичкам, "
        "патроны проводникам и шанс не остаться одному, когда попрёт Выброс."
    ),
    "Бандиты": (
        "🏛 Как сформировались «Бандиты»\n"
        "Свалка собрала тех, кому Зона — не храм и не лаборатория, а касса. "
        "Шайки схлопнулись вокруг смотрящих: дань с троп, обрез наперевес, свои "
        "понятия вместо устава. Кто не платит — тот добыча.\n\n"
        "🎯 Цель в Зоне\n"
        "Контролировать хабар и дороги. Казна — общий котёл: стволы, бензин, откуп "
        "от чужих рейдов. Чем толще общак, тем громче имя на Свалке и тем тише "
        "ходят чужие по вашим тропам."
    ),
    "Монолит": (
        "🏛 Как сформировался «Монолит»\n"
        "Те, кто ушёл за Радар и услышал Зов. База на ЧАЭС — не для туристов: "
        "сюда пускают только своих. Остальным Зона закрывает дорогу.\n\n"
        "🎯 Цель в Зоне\n"
        "Служить Монолиту. Бойцы и боты держат периметр; чужие группировки "
        "ломают зубы о гаусс и процентный жребий, если живые не вышли в поле."
    ),
}


def format_faction_lore(faction: str | None) -> str:
    if not faction:
        return "Сначала вступи в группировку — тогда расскажу, за что ты платишь."
    return FACTION_LORE.get(str(faction), f"Группировка «{faction}»: своих в Зоне не бросаем.")

# Скорость перехода (×) и отдельный множитель награды за контракт, если доехал на этом транспорте.
TRAVEL_SPEED_FOOT = 1
TRAVEL_SPEED_BICYCLE = 1.25
TRAVEL_SPEED_NIVA = 1.5
TRAVEL_SPEED_TRUCK = 2
TRANSPORT_QUEST_REWARD_MULT: dict[str, float] = {
    "foot": 1.0,
    "bicycle": 1.5,
    "niva": 2.0,
    "truck": 3.0,
}
TRANSPORT_QUEST_REWARD_LABELS: dict[str, str] = {
    "foot": "пешком",
    "bicycle": "велосипед",
    "niva": "Нива",
    "truck": "грузовик",
}
TRAVEL_ENERGY_FOOT = 16
TRAVEL_ENERGY_BICYCLE = 11
TRAVEL_ENERGY_NIVA = 12
TRAVEL_ENERGY_TRUCK = 8
TOPUP_RATE_RU_PER_STAR = 150
CONTRACT_CANCEL_PENALTY_RU = 50
CONTRACT_CANCEL_RATING_PENALTY = 1
NCAP_SUCCESS_PAY_RU = 150
WAR_SUCCESS_PAY_RU = 100
WAR_ALLY_SUCCESS_PAY_RU = 50
WAR_ALLY_SUCCESS_RATING = 10
WAR_LOBBY_ENERGY_COST = 24
# 1 игровая минута пути = 10 реальных секунд (отсчёт в КПК).
TRAVEL_REAL_SECONDS_PER_GAME_MINUTE = 10
N2O_TRAVEL_META_PREFIX = "n2o_travel:"
N2O_TRIP_META_PREFIX = "n2o_trip:"
N2O_TRAVEL_SPEED_MULT = 2.0


def _n2o_travel_meta_key(telegram_id: int) -> str:
    return f"{N2O_TRAVEL_META_PREFIX}{int(telegram_id)}"


def _n2o_trip_meta_key(telegram_id: int) -> str:
    return f"{N2O_TRIP_META_PREFIX}{int(telegram_id)}"


def has_n2o_travel_boost(storage: Storage, telegram_id: int) -> bool:
    return bool(storage.get_meta(_n2o_travel_meta_key(telegram_id)))


def consume_n2o_travel_boost(storage: Storage, telegram_id: int) -> bool:
    key = _n2o_travel_meta_key(telegram_id)
    if not storage.get_meta(key):
        return False
    storage.delete_meta(key)
    return True


def _clear_n2o_trip_boost(storage: Storage, telegram_id: int) -> None:
    storage.delete_meta(_n2o_trip_meta_key(telegram_id))


def _mark_n2o_trip_used(storage: Storage, telegram_id: int) -> None:
    storage.set_meta(_n2o_trip_meta_key(telegram_id), "1")


def has_n2o_trip_boost(storage: Storage, telegram_id: int) -> bool:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or not is_traveling(player):
        _clear_n2o_trip_boost(storage, telegram_id)
        return False
    return bool(storage.get_meta(_n2o_trip_meta_key(telegram_id)))


def can_use_n2o_during_travel(storage: Storage, telegram_id: int) -> bool:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or not is_traveling(player):
        _clear_n2o_trip_boost(storage, telegram_id)
        return False
    if has_n2o_trip_boost(storage, telegram_id):
        return False
    return int(player.inventory.get("nitrous_oxide", 0)) > 0


def apply_n2o_to_travel_minutes(
    storage: Storage,
    telegram_id: int,
    travel_minutes: int,
) -> tuple[int, str]:
    if not consume_n2o_travel_boost(storage, telegram_id):
        return travel_minutes, ""
    reduced = max(1, int(round(travel_minutes / N2O_TRAVEL_SPEED_MULT)))
    return reduced, "\nЗакись азота: время в пути ÷2."


def apply_n2o_to_active_travel(storage: Storage, telegram_id: int) -> ActionResult:
    """Ускорить текущий переход: оставшееся время ÷2."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if not is_traveling(player):
        return ActionResult(False, "Сейчас ты никуда не едешь.")
    if has_n2o_trip_boost(storage, telegram_id):
        return ActionResult(False, "Закись азота уже использована на этом переходе.")
    if player.travel_arrives_at is None:
        return ActionResult(False, "Не удалось определить время прибытия.")
    if not storage.remove_item(telegram_id, "nitrous_oxide", 1):
        return ActionResult(False, "У тебя нет закиси азота (N2O) в инвентаре.")
    remaining_sec = max(1, int((_as_utc(player.travel_arrives_at) - _utc_now()).total_seconds()))
    new_remaining_sec = max(1, int(round(remaining_sec / N2O_TRAVEL_SPEED_MULT)))
    new_arrives_at = _utc_now() + timedelta(seconds=new_remaining_sec)
    if not storage.set_travel_arrives_at(telegram_id, new_arrives_at):
        storage.add_item(telegram_id, "nitrous_oxide", 1)
        return ActionResult(False, "Не удалось ускорить переход.")
    _mark_n2o_trip_used(storage, telegram_id)
    return ActionResult(
        True,
        "Закись азота (N2O) активирована.\n"
        f"Осталось ехать: {format_remaining_travel(new_arrives_at)} (÷2).",
    )
ZONE_EVENT_POOL: tuple[tuple[str, int, str], ...] = (
    ("mutant_swarm", 10, "Миграция мутантов: сопротивление на локации выросло."),
    ("bandit_ambush", 7, "Бандитские засады усилили гарнизон противника."),
    ("anomaly_flux", -6, "Аномальный шторм спутал вражеские патрули."),
    ("merc_support", 5, "Наемники временно усилили местных NPC."),
    ("silent_night", -4, "Тихая ночь: активность NPC снижена."),
)


GEAR_PROGRESS: tuple[tuple[int, str, str], ...] = (
    (0, "Куртка новичка", "Нож"),
    (7, "Бронежилет сталкера", "ПМ"),
    (13, "Усиленный бронекостюм", "АКС-74У"),
    (20, "Штурмовой экзоскелет", "АН-94"),
)

MAX_DURABILITY = 100
MIN_EFFECTIVE_DURABILITY = 15
ARMOR_UPGRADE_PRICE = 5000
RATING_REWARD = {
    "quest_success": 12,  # fallback; см. QUEST_RATING_BY_DIFFICULTY
    "quest_fail": 2,
    "war_success": 22,
    "war_fail": 6,
    "raid_success": 26,
    "raid_fail": 8,
    "depot_raid_success": 20,
    "depot_raid_fail": 6,
    "depot_raid_defense": 5,
    "smuggle_success": 13,
    "smuggle_fail": 3,
    "trade_action": 4,
    "duel_win": 8,
    "duel_lose": 2,
    "predel_assembly": 70,
}

# Рейтинг за задания по сложности (успех / штраф за провал).
QUEST_RATING_BY_DIFFICULTY: dict[str, tuple[int, int]] = {
    "easy": (4, 1),
    "hard": (7, 1),
    "heavy": (14, 2),
    "impossible": (20, 4),
}

DUEL_ENERGY_COST = 10
DUEL_PENDING_TTL_SECONDS = 10 * 60
DUEL_WINNER_WOUND_MIN = 5
DUEL_WINNER_WOUND_MAX = 12
DUEL_LOSER_HP_REMAINING = 20
DUEL_LOSER_MONEY_PERCENT = 5
DUEL_LOSER_MONEY_CAP = 5000
DUEL_META_IN_PREFIX = "duel:pending_in:"
DUEL_META_OUT_PREFIX = "duel:pending_out:"

QUEST_FAIL_PENALTY_RANGE: dict[str, tuple[int, int]] = {
    "easy": (30, 80),
    "hard": (60, 130),
    "heavy": (90, 170),
    "impossible": (120, 220),
}

RAID_ARTIFACT_MIN_ENEMY_POWER = 35
RAID_ARTIFACT_DROP_CHANCE = 5  # % шанс арта участнику при успехе (NPC ≥ порога)
WAR_MIN_FACTION_MEMBERS = 3
MAX_FACTION_ALLIANCES = 2

# Рейды на склад/гараж вражеской группировки (не логова, не базы).
DEPOT_RAID_KINDS: tuple[str, ...] = ("warehouse", "garage")
DEPOT_RAID_LABELS: dict[str, str] = {"warehouse": "склад", "garage": "гараж"}
DEPOT_RAID_ENERGY_COST = 16
DEPOT_RAID_MIN_LOOT_PERCENT = 20
DEPOT_RAID_MAX_LOOT_PERCENT = 50
DEPOT_RAID_VEHICLE_STEAL_CHANCE = 5  # % шанс угнать 1 машину сверху канистр (только гараж)
GARAGE_VEHICLE_RENTAL_MINUTES = 30  # аренда при выдаче техники из гаража группировки
GARAGE_VEHICLE_RENTALS_META = "garage:vehicle_rentals"
GARAGE_RENTAL_REQUESTS_META = "garage:rental_requests"
GARAGE_RENTAL_REQUEST_MAX_RANK = 4  # запрос на аренду — ранги 1–4 (5+ подтверждают выдачу)
DEPOT_RAID_FAIL_MONEY_PENALTY = 90
DEPOT_RAID_DEFENSE_POWER_RATIO = 0.55  # доля силы домашней базы цели, обороняющая склад/гараж

RAID_MIN_MEMBERS = 2
RAID_MAX_MEMBERS = 5
RAID_TURN_SECONDS = 12
RAID_MATCH_SECONDS = 15 * 60
RAID_LOOT_TURNS = 2
RAID_CAPTURE_TURNS = 3

SURVIVAL_ACTIVE_RADIATION_MIN = 1
SURVIVAL_ACTIVE_RADIATION_MAX = 3
SURVIVAL_ACTIVE_HUNGER_INC = 1
SURVIVAL_ACTIVE_THIRST_INC = 1
SURVIVAL_ACTIVE_HP_DRAIN_MIN = 1
SURVIVAL_ACTIVE_HP_DRAIN_MAX = 3

HUNGER_PASSIVE_PER_HOUR = 1
THIRST_PASSIVE_PER_HOUR = 1
SURVIVAL_TICK_MINUTES = 30
SURVIVAL_OVERLIMIT_HP_DRAIN = 10
TRANSFER_FEE_PERCENT = 20
TRUCK_WEAR_MIN = 3
TRUCK_WEAR_MAX = 8
NIVA_WEAR_MIN = 2
NIVA_WEAR_MAX = 6

SURVIVAL_CRAVING_THRESHOLD = 40
SURVIVAL_URGENT_THRESHOLD = 75
SURVIVAL_CRITICAL_THRESHOLD = 100

HUNGER_CRAVING_PHRASES: tuple[str, ...] = (
    "Как же есть хочется...",
    "Живот уже урчит.",
    "Был бы сейчас хлеб с колбасой...",
    "Зона кормит радиациями, а не обедом.",
    "Пора бы перекусить, брат.",
)
HUNGER_URGENT_PHRASES: tuple[str, ...] = (
    "Голод давит сильнее, чем отклик детектора.",
    "Есть хочется так, что готов сухарик у бандитов купить.",
    "Без еды далеко не уйду.",
    "Сил мало — нужно поесть.",
)
HUNGER_CRITICAL_PHRASES: tuple[str, ...] = (
    "Голод уже бьёт по здоровью — срочно ешь!",
    "Сил почти нет. Ищи еду в инвентаре!",
    "Так голоден, что Зона кажется пирожком.",
)
THIRST_CRAVING_PHRASES: tuple[str, ...] = (
    "Как же пить хочется...",
    "Горло пересохло.",
    "Вода бы сейчас — хоть глоток.",
    "Жажда напоминает о себе.",
    "Минералку бы... или хотя бы водички.",
)
THIRST_URGENT_PHRASES: tuple[str, ...] = (
    "Жажда мешает сосредоточиться.",
    "Язык прилип к небу.",
    "Без воды далеко не уйду.",
    "Пить хочется сильнее, чем артефакты искать.",
)
THIRST_CRITICAL_PHRASES: tuple[str, ...] = (
    "Жажда уже опасная — пей, пока не поздно!",
    "Горло сохнет. Срочно нужна вода!",
    "Обезвоживание бьёт по силам.",
)


@dataclass(frozen=True)
class AchievementRule:
    key: str
    title: str
    description: str
    reward_ru: int
    reward_rating: int
    check: Callable[[dict[str, int], Character], bool]


def _faction_controls_all_contestable_points(storage: Storage, faction: str | None) -> bool:
    """True, если группировка держит все точки, кроме баз ГП."""
    if not faction:
        return False
    contestable = [
        loc
        for loc in storage.get_locations()
        if str(loc.get("point_type") or "") != "база"
    ]
    if not contestable:
        return False
    return all(str(loc.get("controlled_by") or "") == faction for loc in contestable)


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    text: str
    payload: dict[str, Any] | None = None


REFERRAL_INVITER_BONUS_RU = 1250
REFERRAL_STARTER_PACK: tuple[tuple[str, int], ...] = (
    ("stew", 2),
    ("antirad", 1),
    ("water_bottle", 1),
    ("medkit", 1),
)

# Стартовый набор нового сталкера (деньги + экип + расходники).
STARTING_MONEY_RU = 3000
STARTING_EQUIPMENT_JSON = (
    '{"weapon":"ПМ","armor":"Куртка новичка","weapon_durability":100,"armor_durability":100}'
)
STARTING_INVENTORY: dict[str, int] = {
    "medkit": 2,
    "vodka": 2,
    "bread": 2,
    "sausage": 1,
    "water_bottle": 2,
}


def parse_referral_payload(raw: str | None) -> int | None:
    """Извлекает telegram_id пригласившего из /start payload (ref_123 / ref123)."""
    token = (raw or "").strip()
    if not token:
        return None
    lowered = token.casefold()
    if lowered.startswith("ref_"):
        digits = token[4:]
    elif lowered.startswith("ref"):
        digits = token[3:]
    else:
        return None
    if not digits.isdigit():
        return None
    value = int(digits)
    return value if value > 0 else None


def build_referral_link(bot_username: str, telegram_id: int) -> str:
    username = (bot_username or "").lstrip("@").strip()
    return f"https://t.me/{username}?start=ref_{int(telegram_id)}"


def apply_referral_rewards(
    storage: Storage,
    invitee_id: int,
    referrer_id: int | None,
) -> ActionResult:
    """Награда за реферал: пригласивший +1250 RU, новичок — стартовый набор."""
    if referrer_id is None:
        return ActionResult(False, "Реферал не указан.")
    if int(referrer_id) == int(invitee_id):
        return ActionResult(False, "Нельзя пригласить самого себя.")
    if not storage.character_exists(int(referrer_id)):
        return ActionResult(False, "Пригласивший ещё не зарегистрирован в Зоне.")
    if storage.has_referral_claim(int(invitee_id)):
        return ActionResult(False, "Стартовый набор по рефералу уже получен.")
    if not storage.record_referral(invitee_id=int(invitee_id), referrer_id=int(referrer_id)):
        return ActionResult(False, "Не удалось зафиксировать реферал.")

    for item_key, amount in REFERRAL_STARTER_PACK:
        storage.add_item(int(invitee_id), item_key, amount)
    storage.change_money(int(referrer_id), REFERRAL_INVITER_BONUS_RU)

    pack_text = ", ".join(
        f"{ITEM_LABELS.get(key, key)} x{amount}" for key, amount in REFERRAL_STARTER_PACK
    )
    return ActionResult(
        True,
        f"Реферал засчитан.\n"
        f"Тебе стартовый набор: {pack_text}.\n"
        f"Пригласивший получил {REFERRAL_INVITER_BONUS_RU} RU.",
    )


@dataclass(frozen=True)
class RaidLaunchResult:
    ok: bool
    text: str
    notify_member_ids: tuple[int, ...]
    tactical_raid: bool = False


@dataclass(frozen=True)
class WarLobbyResult:
    ok: bool
    text: str
    notify_member_ids: tuple[int, ...] = ()
    tactical_cwar: bool = False
    monolith_pending: bool = False
    monolith_notify_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class QuestChanceBreakdown:
    chance: int
    base_chance: int
    gear_bonus: int
    ammo_bonus: int
    medkit_bonus: int


def resolve_equipment_by_power(gear_power: int) -> tuple[str, str]:
    armor = GEAR_PROGRESS[0][1]
    weapon = GEAR_PROGRESS[0][2]
    for threshold, armor_name, weapon_name in GEAR_PROGRESS:
        if gear_power >= threshold:
            armor = armor_name
            weapon = weapon_name
    return armor, weapon


def _durability_percent(character: Character, slot: str) -> int:
    key = f"{slot}_durability"
    raw = character.equipment.get(key, MAX_DURABILITY)
    if isinstance(raw, (int, float)):
        value = int(raw)
    else:
        try:
            value = int(str(raw))
        except ValueError:
            value = MAX_DURABILITY
    return max(0, min(MAX_DURABILITY, value))


def _durability_penalty(percent: int, max_penalty: int) -> int:
    if percent >= MIN_EFFECTIVE_DURABILITY:
        return 0
    missing = MIN_EFFECTIVE_DURABILITY - percent
    return int(round((missing / MIN_EFFECTIVE_DURABILITY) * max_penalty))


def _equipped_artifact_names(character: Character) -> list[str]:
    names: list[str] = []
    for key in ARTIFACT_EQUIP_SLOT_KEYS:
        name = str(character.equipment.get(key, "Нет") or "Нет")
        if name and name != "Нет":
            names.append(name)
    return names


def armor_gear_tier(armor_name: str) -> int:
    """Тир брони для ячеек артов (1–5), по таблице тайника."""
    name = str(armor_name or "").strip()
    if not name or name == "Куртка новичка":
        return 1
    for tier, keys in STASH_ARMOR_BY_TIER.items():
        for key in keys:
            meta = ARMOR_CATALOG.get(key)
            if meta is not None and str(meta.get("name")) == name:
                return int(tier)
    # Legacy / сезонные.
    if name in {"ПСЗ-7 «Долг»", "Усиленный бронекостюм"}:
        return 3
    if name in {"Костюм «Чемпион Зоны»", "Бронекостюм «Бронза сезона»", "Штурмовой экзоскелет"}:
        return 4 if name != "Костюм «Чемпион Зоны»" else 5
    if name == "Бронежилет сталкера":
        return 2
    return 1


def armor_base_artifact_slots(armor_name: str) -> int:
    """Базовые ячейки артов от брони: T1–T2=0, T3=1, T4+=2."""
    return int(ARTIFACT_BASE_SLOTS_BY_ARMOR_TIER.get(armor_gear_tier(armor_name), 0))


def has_purchased_artifact_slot_upgrade(character: Character) -> bool:
    """Куплена ли у техника доп. ячейка (+1 к базе брони)."""
    raw_upgrade = character.equipment.get("artifact_slot_upgrade")
    try:
        if int(raw_upgrade or 0) >= 1:
            return True
    except (TypeError, ValueError):
        pass
    # Legacy: раньше artifact_slots=2 означало купленный апгрейд.
    raw = character.equipment.get("artifact_slots")
    try:
        return int(raw or 1) >= 2
    except (TypeError, ValueError):
        return False


def has_extra_artifact_slot(character: Character) -> bool:
    """Совместимость: True, если доп. ячейка у техника уже куплена."""
    return has_purchased_artifact_slot_upgrade(character)


def max_artifact_slots(character: Character) -> int:
    """Сколько артов можно надеть: база брони + апгрейд техника, макс. 3."""
    armor_name = str(character.equipment.get("armor", "Куртка новичка") or "Куртка новичка")
    base = armor_base_artifact_slots(armor_name)
    bonus = 1 if has_purchased_artifact_slot_upgrade(character) else 0
    return max(0, min(MAX_ARTIFACT_EQUIP_SLOTS, base + bonus))


def has_any_equipped_artifact(character: Character) -> bool:
    return any(
        str(character.equipment.get(key, "Нет") or "Нет") not in ("", "Нет")
        for key in ARTIFACT_EQUIP_SLOT_KEYS
    )


def _trim_equipped_artifacts_to_cap(
    storage: Storage,
    telegram_id: int,
    character: Character,
) -> list[str]:
    """Снять лишние арты сверх лимита ячеек (с конца). Возвращает снятые имена."""
    cap = max_artifact_slots(character)
    removed: list[str] = []
    for idx, key in enumerate(ARTIFACT_EQUIP_SLOT_KEYS):
        name = str(character.equipment.get(key, "Нет") or "Нет")
        if not name or name == "Нет":
            continue
        if idx < cap:
            continue
        _unequip_artifact_to_inventory(storage, telegram_id, name, slot=key)
        removed.append(name)
    return removed


def equipment_power(character: Character) -> int:
    weapon_name = str(character.equipment.get("weapon", "Нож"))
    armor_name = str(character.equipment.get("armor", "Куртка новичка"))
    weapon_durability = _durability_percent(character, "weapon")
    armor_durability = _durability_percent(character, "armor")

    weapon_level = _weapon_rating(weapon_name)
    armor_level = _armor_rating(armor_name)
    artifact_bonus = 0
    try:
        from app.artifact_features import scaled_artifact_power_bonus

        artifact_bonus = scaled_artifact_power_bonus(character)
    except Exception:
        for artifact_name in _equipped_artifact_names(character):
            if artifact_name in ARTIFACT_EQUIP_BONUSES:
                artifact_bonus += int(ARTIFACT_EQUIP_BONUSES[artifact_name].get("power", 0))
            else:
                artifact_bonus += 2
    durability_penalty = _durability_penalty(weapon_durability, 6) + _durability_penalty(armor_durability, 6)
    # Жёсткий потолок шкалы профиля: топ оружие+броня+арты = 20.
    return max(1, min(20, weapon_level + armor_level + artifact_bonus - durability_penalty))


def _artifact_hp_bonus(character: Character) -> int:
    try:
        from app.artifact_features import scaled_artifact_hp_bonus

        return scaled_artifact_hp_bonus(character)
    except Exception:
        total = 0
        for artifact_name in _equipped_artifact_names(character):
            total += int(ARTIFACT_EQUIP_BONUSES.get(artifact_name, {}).get("hp", 0))
        return total


def effective_max_health(character: Character) -> int:
    return 100 + max(0, _artifact_hp_bonus(character))


def compute_total_gear_power(character: Character) -> int:
    return equipment_power(character)


def armor_defense(character: Character) -> int:
    """Плоское снижение урона от апгрейдов брони: +1 за каждый уровень улучшения."""
    try:
        return max(0, int(character.equipment.get("armor_upgrade_level", 0)))
    except (TypeError, ValueError):
        return 0


def armor_flat_mitigation(character: Character) -> int:
    """Постоянное смягчение удара (−HP) от самой брони, всегда."""
    armor_name = str(character.equipment.get("armor", "Куртка новичка"))
    return max(0, int(ARMOR_MITIGATION_BY_NAME.get(armor_name, 0)))


def armor_block_chance(character: Character) -> int:
    """Шанс (%) полностью сбросить входящий удар по экипированной броне."""
    armor_name = str(character.equipment.get("armor", "Куртка новичка"))
    return max(0, min(100, int(ARMOR_BLOCK_CHANCE_BY_NAME.get(armor_name, 0))))


def apply_incoming_damage(raw_damage: int, character: Character, *, min_damage: int = 1) -> int:
    """Блок брони → 0; иначе урон − смягчение брони − апгрейды − арты.

    Нижняя граница поднята на +10: даже сильная броня не срежет удар ниже
    (min_damage + 10) — против «урона по 1 HP».
    """
    chance = armor_block_chance(character)
    if chance > 0 and random.randint(1, 100) <= chance:
        return 0
    try:
        from app.artifact_features import artifact_incoming_damage_reduction

        art_reduce = artifact_incoming_damage_reduction(character)
    except Exception:
        art_reduce = 0
    reduced = int(raw_damage) - armor_flat_mitigation(character) - armor_defense(character) - art_reduce
    return max(min_damage + 10, reduced)


def _inventory_has_named_gear(character: Character, catalog: dict[str, dict[str, int | str]], name: str) -> bool:
    for key, meta in catalog.items():
        if str(meta.get("name")) != name:
            continue
        if int(character.inventory.get(key, 0)) > 0:
            return True
    return False


def _owns_named_armor(character: Character, name: str) -> bool:
    if str(character.equipment.get("armor", "")) == name:
        return True
    return _inventory_has_named_gear(character, ARMOR_CATALOG, name)


def _owns_named_weapon(character: Character, name: str) -> bool:
    if str(character.equipment.get("weapon", "")) == name:
        return True
    return _inventory_has_named_gear(character, WEAPON_CATALOG, name)


def _owns_top_gear_set(character: Character) -> bool:
    return _owns_named_armor(character, "Носорог") and _owns_named_weapon(character, "Гаусс-пушка")


def _return_armor_upgrades_to_inventory(storage: Storage, telegram_id: int, character: Character) -> int:
    """Снимает установленные улучшения брони обратно в инвентарь. Возвращает число снятых."""
    level = armor_defense(character)
    if level <= 0:
        return 0
    storage.update_equipment_fields(telegram_id, {"armor_upgrade_level": 0})
    storage.add_item(telegram_id, "armor_upgrade", level)
    return level


def _apply_durability_decay(storage: Storage, telegram_id: int, weapon_loss: int, armor_loss: int) -> str:
    character = storage.get_character(telegram_id, refresh_energy=False)
    if character is None:
        return ""
    weapon_old = _durability_percent(character, "weapon")
    armor_old = _durability_percent(character, "armor")
    weapon_new = max(0, weapon_old - max(0, weapon_loss))
    armor_new = max(0, armor_old - max(0, armor_loss))
    if weapon_new == weapon_old and armor_new == armor_old:
        return ""
    storage.update_equipment_fields(
        telegram_id,
        {"weapon_durability": weapon_new, "armor_durability": armor_new},
    )
    warning = ""
    if weapon_new <= 10 or armor_new <= 10:
        warning = "\n⚠️ Снаряжение на грани поломки: загляни в ремонт у торговца."
    return (
        f"\nИзнос: оружие {weapon_old}%→{weapon_new}%, броня {armor_old}%→{armor_new}%."
        f"{warning}"
    )


RESPAWN_HEALTH = 60
RESPAWN_ENERGY = 60
RESPAWN_COST_RU = 500
RESPAWN_DEBT_META_PREFIX = "respawn:debt:"
DEATH_INVENTORY_KEEP_RATIO = 0.2  # 80% лута растаскивают мутанты
PERSONAL_STASH_PAGE_SIZE = 8


def _is_dead(character: Character) -> bool:
    return character.health <= 0


def _pick_survival_phrase(phrases: tuple[str, ...]) -> str:
    return random.choice(phrases)


def build_survival_craving_notice(character: Character) -> str:
    if _is_dead(character):
        return ""
    lines: list[str] = []
    if character.hunger >= SURVIVAL_CRITICAL_THRESHOLD:
        lines.append(_pick_survival_phrase(HUNGER_CRITICAL_PHRASES))
    elif character.hunger >= SURVIVAL_URGENT_THRESHOLD:
        lines.append(_pick_survival_phrase(HUNGER_URGENT_PHRASES))
    elif character.hunger >= SURVIVAL_CRAVING_THRESHOLD:
        lines.append(_pick_survival_phrase(HUNGER_CRAVING_PHRASES))
    if character.thirst >= SURVIVAL_CRITICAL_THRESHOLD:
        lines.append(_pick_survival_phrase(THIRST_CRITICAL_PHRASES))
    elif character.thirst >= SURVIVAL_URGENT_THRESHOLD:
        lines.append(_pick_survival_phrase(THIRST_URGENT_PHRASES))
    elif character.thirst >= SURVIVAL_CRAVING_THRESHOLD:
        lines.append(_pick_survival_phrase(THIRST_CRAVING_PHRASES))
    if not lines:
        return ""
    return "💬 " + "\n💬 ".join(lines) + "\n\n"


def append_survival_craving_notice(storage: Storage, telegram_id: int, text: str) -> str:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return text
    notice = build_survival_craving_notice(player)
    if not notice:
        return text
    return notice + text


def _dead_block_text() -> str:
    return (
        "Персонаж мёртв (HP=0).\n"
        "Нажми ♻️ «Спасение на базе» в сообщении о смерти или любую кнопку меню."
    )


def _respawn_debt_key(telegram_id: int) -> str:
    return f"{RESPAWN_DEBT_META_PREFIX}{int(telegram_id)}"


def get_respawn_debt(storage: Storage, telegram_id: int) -> int:
    raw = storage.get_meta(_respawn_debt_key(telegram_id))
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def add_respawn_debt(storage: Storage, telegram_id: int, amount: int) -> int:
    if amount <= 0:
        return get_respawn_debt(storage, telegram_id)
    total = get_respawn_debt(storage, telegram_id) + int(amount)
    storage.set_meta(_respawn_debt_key(telegram_id), str(total))
    return total


def clear_respawn_debt(storage: Storage, telegram_id: int) -> None:
    storage.delete_meta(_respawn_debt_key(telegram_id))


def collect_respawn_debt(storage: Storage, telegram_id: int) -> int:
    """Списать долг за спасение с текущего баланса. Возвращает уплаченную сумму."""
    debt = get_respawn_debt(storage, telegram_id)
    if debt <= 0:
        return 0
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.money <= 0:
        return 0
    pay = min(debt, int(player.money))
    if pay <= 0 or not storage.change_money(telegram_id, -pay, skip_debt_collect=True):
        return 0
    remaining = debt - pay
    if remaining <= 0:
        clear_respawn_debt(storage, telegram_id)
    else:
        storage.set_meta(_respawn_debt_key(telegram_id), str(remaining))
    return pay


def format_respawn_debt_line(storage: Storage, telegram_id: int) -> str:
    debt = get_respawn_debt(storage, telegram_id)
    if debt <= 0:
        return ""
    return f"📉 Долг за спасение: {debt} RU (спишется с первого заработка).\n"


DEATH_CAUSE_META_PREFIX = "death:last_cause:"


def _death_cause_key(telegram_id: int) -> str:
    return f"{DEATH_CAUSE_META_PREFIX}{int(telegram_id)}"


def remember_death_cause(storage: Storage, telegram_id: int, cause: str) -> None:
    storage.set_meta(_death_cause_key(telegram_id), cause)


def pop_death_cause(storage: Storage, telegram_id: int) -> str | None:
    key = _death_cause_key(telegram_id)
    raw = storage.get_meta(key)
    if raw:
        storage.delete_meta(key)
    return raw


def peek_death_cause(storage: Storage, telegram_id: int) -> str | None:
    return storage.get_meta(_death_cause_key(telegram_id))


DEATH_KILLER_META_PREFIX = "death:last_killer:"


def _death_killer_key(telegram_id: int) -> str:
    return f"{DEATH_KILLER_META_PREFIX}{int(telegram_id)}"


def remember_death_killer(storage: Storage, telegram_id: int, name: str) -> None:
    storage.set_meta(_death_killer_key(telegram_id), name)


def pop_death_killer(storage: Storage, telegram_id: int) -> str | None:
    key = _death_killer_key(telegram_id)
    raw = storage.get_meta(key)
    if raw:
        storage.delete_meta(key)
    return raw


def peek_death_killer(storage: Storage, telegram_id: int) -> str | None:
    return storage.get_meta(_death_killer_key(telegram_id))


def build_dead_character_text(
    character: Character,
    *,
    where: str | None = None,
    cause: str | None = None,
    storage: Storage | None = None,
    killer_name: str | None = None,
) -> str:
    """Текст смерти: каждый раз новый, с локацией и причиной (бой/голод/рад…)."""
    from app.death_flavor import generate_death_story

    resolved = cause
    if resolved is None and storage is not None:
        resolved = peek_death_cause(storage, character.telegram_id)
    resolved_killer = killer_name
    if resolved_killer is None and storage is not None:
        resolved_killer = peek_death_killer(storage, character.telegram_id)
    return generate_death_story(
        character,
        where=where,
        cause=resolved,
        home=faction_home_base(character.faction),
        respawn_cost=RESPAWN_COST_RU,
        max_hp=effective_max_health(character),
        killer_name=resolved_killer,
    )


def build_battle_death_text(
    character: Character,
    *,
    where: str | None = None,
    cause: str | None = "combat",
    storage: Storage | None = None,
    killer_name: str | None = None,
) -> str:
    """Смерть на поле / вылазке — тот же генератор, явная причина."""
    return build_dead_character_text(
        character, where=where, cause=cause, storage=storage, killer_name=killer_name
    )


DEATH_LOG_MAX_ENTRIES = 5
DEATH_LOG_TEXT_LIMIT = 400
ARTIFACT_FIND_LOG_MAX_ENTRIES = 20
ARTIFACT_FIND_SOURCE_LABELS: dict[str, str] = {
    "hunt": "Охота",
    "quest": "Контракт",
    "raid": "Рейд",
}


def _artifact_find_log_key(telegram_id: int) -> str:
    return f"artifact_find_log:{int(telegram_id)}"


def append_artifact_find_log(
    storage: Storage,
    telegram_id: int,
    art_key: str,
    *,
    location: str,
    source: str,
    detector_name: str | None = None,
) -> None:
    """Журнал находок артефактов (ценные и мусор) — последние N записей в meta."""
    key = normalize_shop_item_key(art_key)
    if key not in ARTIFACT_ALL_KEYS:
        return
    label = ITEM_LABELS.get(key, key)
    if key in ARTIFACT_JUNK_KEYS:
        label = f"{label} (мусор)"
    source_label = ARTIFACT_FIND_SOURCE_LABELS.get(source, source)
    detail = source_label
    if detector_name and source == "hunt":
        detail = f"{source_label} · {detector_name}"
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "art_key": key,
        "label": label,
        "location": str(location or "").strip() or "неизвестно",
        "source": source,
        "detail": detail,
    }
    meta_key = _artifact_find_log_key(telegram_id)
    raw = storage.get_meta(meta_key)
    entries: list[dict[str, str]] = []
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                entries = [e for e in loaded if isinstance(e, dict)]
        except (json.JSONDecodeError, TypeError, ValueError):
            entries = []
    entries.append(entry)
    entries = entries[-ARTIFACT_FIND_LOG_MAX_ENTRIES:]
    storage.set_meta(meta_key, json.dumps(entries, ensure_ascii=False))


def on_artifact_found(
    storage: Storage,
    telegram_id: int,
    art_key: str,
    *,
    location: str,
    source: str,
    detector_name: str | None = None,
) -> None:
    """Статистика ценных артов + запись в журнал находок."""
    if art_key in ARTIFACT_JUNK_KEYS:
        from app.artifact_features import note_junk_type_seen

        note_junk_type_seen(storage, telegram_id, art_key)
    else:
        record_valuable_artifact_found_stat(storage, telegram_id, art_key)
    append_artifact_find_log(
        storage,
        telegram_id,
        art_key,
        location=location,
        source=source,
        detector_name=detector_name,
    )


def build_artifact_find_log_text(storage: Storage, telegram_id: int) -> str:
    """Журнал находок: последние записи, новые сверху."""
    raw = storage.get_meta(_artifact_find_log_key(telegram_id))
    entries: list[dict[str, str]] = []
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                entries = [e for e in loaded if isinstance(e, dict)]
        except (json.JSONDecodeError, TypeError, ValueError):
            entries = []
    if not entries:
        return (
            "💎 Журнал находок пуст.\n"
            "Здесь появятся артефакты с охоты, контрактов и рейдов — с локацией и источником."
        )
    lines = ["💎 Журнал находок (последние записи, UTC):", ""]
    for idx, entry in enumerate(reversed(entries), start=1):
        when = _format_death_log_when(str(entry.get("at") or ""))
        label = str(entry.get("label") or entry.get("art_key") or "?")
        location = str(entry.get("location") or "?")
        detail = str(entry.get("detail") or entry.get("source") or "?")
        lines.append(f"{idx}. {when} — {label}")
        lines.append(f"   📍 {location} · {detail}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _death_log_key(telegram_id: int) -> str:
    return f"death_log:{int(telegram_id)}"


def append_death_log(storage: Storage, telegram_id: int, story_text: str) -> None:
    """Хранит последние 5 записей о смерти игрока (JSON-список в meta)."""
    text = (story_text or "").strip()
    if not text:
        return
    if len(text) > DEATH_LOG_TEXT_LIMIT:
        text = text[:DEATH_LOG_TEXT_LIMIT].rstrip() + "…"
    key = _death_log_key(telegram_id)
    raw = storage.get_meta(key)
    entries: list[dict[str, str]] = []
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                entries = [e for e in loaded if isinstance(e, dict)]
        except (json.JSONDecodeError, TypeError, ValueError):
            entries = []
    entries.append({"at": datetime.now(timezone.utc).isoformat(), "text": text})
    entries = entries[-DEATH_LOG_MAX_ENTRIES:]
    storage.set_meta(key, json.dumps(entries, ensure_ascii=False))


def _death_notice_sent_key(telegram_id: int) -> str:
    return f"death_notice_sent:{int(telegram_id)}"


def append_death_log_once(storage: Storage, telegram_id: int, story_text: str) -> None:
    """Записать в журнал смертей один раз за текущую смерть."""
    if storage.get_meta(_death_notice_sent_key(telegram_id)):
        return
    append_death_log(storage, telegram_id, story_text)
    storage.set_meta(_death_notice_sent_key(telegram_id), "1")


def clear_death_notice_sent(storage: Storage, telegram_id: int) -> None:
    storage.delete_meta(_death_notice_sent_key(telegram_id))


def _format_death_log_when(raw_at: str) -> str:
    try:
        parsed = datetime.fromisoformat(raw_at)
    except (ValueError, TypeError):
        return "неизвестно когда"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%d.%m %H:%M")


def build_death_log_text(storage: Storage, telegram_id: int) -> str:
    """Журнал смертей: последние до 5 записей, самые новые сверху."""
    raw = storage.get_meta(_death_log_key(telegram_id))
    entries: list[dict[str, str]] = []
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                entries = [e for e in loaded if isinstance(e, dict)]
        except (json.JSONDecodeError, TypeError, ValueError):
            entries = []
    if not entries:
        return "☠️ Журнал смертей пуст.\nПока ты держишься — или ещё не успел записать историю."
    lines = ["☠️ Журнал смертей (последние записи, UTC):", ""]
    for idx, entry in enumerate(reversed(entries), start=1):
        when = _format_death_log_when(str(entry.get("at") or ""))
        text = str(entry.get("text") or "").strip()
        lines.append(f"{idx}. {when}")
        if text:
            lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip()


def _apply_death_inventory_loot(storage: Storage, telegram_id: int) -> str:
    """Оставить ~20% каждого стака в инвентаре. Экипировка и хабар не трогаются."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ""
    inventory = dict(player.inventory)
    if not inventory:
        return "Рюкзак и так был пуст."
    kept: dict[str, int] = {}
    lost_lines: list[str] = []
    total_lost = 0
    for key, amount in sorted(inventory.items()):
        qty = int(amount or 0)
        if qty <= 0:
            continue
        keep = max(0, int(qty * DEATH_INVENTORY_KEEP_RATIO))
        lost = qty - keep
        if keep > 0:
            kept[key] = keep
        if lost > 0:
            total_lost += lost
            lost_lines.append(f"• {ITEM_LABELS.get(key, key)} ×{lost}")
    storage._set_inventory(telegram_id, kept)
    storage.save_snapshot()
    if total_lost <= 0:
        return "Мутанты почти ничего не нашли — потери минимальны."
    preview = "\n".join(lost_lines[:12])
    more = f"\n• …и ещё позиций: {len(lost_lines) - 12}" if len(lost_lines) > 12 else ""
    return f"Мутанты растащили из рюкзака:\n{preview}{more}"


def format_personal_stash(storage: Storage, telegram_id: int) -> str:
    stash = storage.get_personal_stash(telegram_id)
    if not stash:
        return "🗄 Личный хабар пуст."
    lines = [
        f"• {ITEM_LABELS.get(key, key)} x{amount}"
        for key, amount in sorted(stash.items())
    ]
    return "🗄 Личный хабар:\n" + "\n".join(lines)


def deposit_to_personal_stash(
    storage: Storage,
    telegram_id: int,
    item_key: str,
    amount: int,
) -> ActionResult:
    if amount <= 0:
        return ActionResult(False, "Некорректное количество.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if is_traveling(player):
        return ActionResult(False, travel_block_text(player) or "Ты в пути.")
    home = faction_home_base(player.faction)
    if player.location != home:
        return ActionResult(False, f"Схрон доступен только на домашней базе «{home}».")
    key = str(item_key)
    if not storage.remove_item(telegram_id, key, amount):
        return ActionResult(False, f"Недостаточно: {ITEM_LABELS.get(key, key)}.")
    stash = storage.get_personal_stash(telegram_id)
    stash[key] = int(stash.get(key, 0)) + amount
    storage.set_personal_stash(telegram_id, stash)
    return ActionResult(
        True,
        f"В хабар убрано: {ITEM_LABELS.get(key, key)} x{amount}.",
    )


def withdraw_from_personal_stash(
    storage: Storage,
    telegram_id: int,
    item_key: str,
    amount: int,
) -> ActionResult:
    if amount <= 0:
        return ActionResult(False, "Некорректное количество.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if is_traveling(player):
        return ActionResult(False, travel_block_text(player) or "Ты в пути.")
    home = faction_home_base(player.faction)
    if player.location != home:
        return ActionResult(False, f"Схрон доступен только на домашней базе «{home}».")
    key = str(item_key)
    stash = storage.get_personal_stash(telegram_id)
    have = int(stash.get(key, 0))
    if have < amount:
        return ActionResult(False, f"В хабаре недостаточно: {ITEM_LABELS.get(key, key)}.")
    left = have - amount
    if left <= 0:
        stash.pop(key, None)
    else:
        stash[key] = left
    storage.set_personal_stash(telegram_id, stash)
    storage.add_item(telegram_id, key, amount)
    return ActionResult(
        True,
        f"Из хабара взято: {ITEM_LABELS.get(key, key)} x{amount}.",
    )


def list_stash_deposit_buttons(
    character: Character,
    *,
    page: int = 0,
) -> tuple[list[tuple[str, str]], int, int]:
    """[(label, callback), ...], page, total_pages."""
    items = [
        (key, int(amount))
        for key, amount in sorted(character.inventory.items())
        if int(amount) > 0
    ]
    total = len(items)
    total_pages = max(1, (total + PERSONAL_STASH_PAGE_SIZE - 1) // PERSONAL_STASH_PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    start = safe_page * PERSONAL_STASH_PAGE_SIZE
    chunk = items[start : start + PERSONAL_STASH_PAGE_SIZE]
    buttons: list[tuple[str, str]] = []
    for key, amount in chunk:
        title = ITEM_LABELS.get(key, key)
        label = f"📥 {title} x{amount}"
        if len(label) > 48:
            label = f"📥 {title[:28]}… x{amount}"
        buttons.append((label, f"stash:put:{key}"))
    return buttons, safe_page, total_pages


def list_stash_withdraw_buttons(
    storage: Storage,
    telegram_id: int,
    *,
    page: int = 0,
) -> tuple[list[tuple[str, str]], int, int]:
    stash = storage.get_personal_stash(telegram_id)
    items = [(key, int(amount)) for key, amount in sorted(stash.items()) if int(amount) > 0]
    total = len(items)
    total_pages = max(1, (total + PERSONAL_STASH_PAGE_SIZE - 1) // PERSONAL_STASH_PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    start = safe_page * PERSONAL_STASH_PAGE_SIZE
    chunk = items[start : start + PERSONAL_STASH_PAGE_SIZE]
    buttons: list[tuple[str, str]] = []
    for key, amount in chunk:
        title = ITEM_LABELS.get(key, key)
        label = f"📤 {title} x{amount}"
        if len(label) > 48:
            label = f"📤 {title[:28]}… x{amount}"
        buttons.append((label, f"stash:take:{key}"))
    return buttons, safe_page, total_pages


def respawn_character(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if not _is_dead(player):
        return ActionResult(False, "Респавн доступен только при HP=0.")

    cost = RESPAWN_COST_RU
    paid = min(int(player.money), cost)
    debt_added = cost - paid
    if paid > 0 and not storage.change_money(telegram_id, -paid, skip_debt_collect=True):
        paid = 0
        debt_added = cost

    from app.artifact_features import strip_equipped_artifacts_for_death

    insurance_text = strip_equipped_artifacts_for_death(storage, telegram_id)
    loot_text = _apply_death_inventory_loot(storage, telegram_id)
    if insurance_text:
        loot_text = f"{insurance_text}\n\n{loot_text}"
    death_cause = peek_death_cause(storage, telegram_id)
    survival_death = death_cause in {"hunger", "thirst"}
    current_health = player.health
    current_energy = player.energy
    max_hp = int(effective_max_health(player))
    if survival_death:
        # После смерти от голода/жажды: шкалы с нуля, HP = 50% от максимума.
        storage.set_survival_needs(telegram_id, radiation=0, hunger=0, thirst=0)
        target_hp = max(1, max_hp // 2)
    else:
        target_hp = RESPAWN_HEALTH
    storage.change_health(telegram_id, target_hp - current_health)
    storage.restore_energy(telegram_id, RESPAWN_ENERGY - current_energy)
    home = faction_home_base(player.faction)
    storage.set_location(telegram_id, home)
    storage.clear_travel(telegram_id)
    pop_death_cause(storage, telegram_id)
    pop_death_killer(storage, telegram_id)
    clear_death_notice_sent(storage, telegram_id)

    from app.player_busy import clear_all_activity_sessions

    clear_all_activity_sessions(storage, telegram_id)

    from app.location_walk import clear_walk_position

    clear_walk_position(storage, telegram_id)

    pay_lines: list[str] = []
    if paid > 0:
        pay_lines.append(f"Списано {paid} RU за спасение.")
    if debt_added > 0:
        total_debt = add_respawn_debt(storage, telegram_id, debt_added)
        pay_lines.append(
            f"В долг: {debt_added} RU (всего долг {total_debt} RU — спишется, когда появятся деньги)."
        )
    if not pay_lines:
        pay_lines.append(f"Спасение оплачено: {cost} RU.")

    recovery_extra = ""
    if survival_death:
        recovery_extra = "Голод, жажда и радиация сброшены до 0.\n"

    return ActionResult(
        True,
        f"Сталкеры нашли тебя без сознания и доставили на «{home}».\n"
        f"{pay_lines[0]}\n"
        + (f"{pay_lines[1]}\n" if len(pay_lines) > 1 else "")
        + recovery_extra
        + f"HP восстановлено до {target_hp}, энергия до {RESPAWN_ENERGY}.\n\n"
        f"{loot_text}",
    )


def _add_rating(storage: Storage, telegram_id: int, amount: int) -> None:
    if amount == 0:
        return
    if amount < 0:
        current = storage.get_player_stats(telegram_id).get("rating_points", 0)
        if int(current) + amount < 0:
            amount = -int(current)
        if amount == 0:
            return
    storage.add_player_stat(telegram_id, "rating_points", amount)
    if amount > 0:
        storage.add_player_stat(telegram_id, "season_rating", amount)


# ---------------------------------------------------------------------------
# Персональные бонусы (автоматика): ежедневный рандомный рейтинг и иммунитеты
# для указанных telegram_id.
# ---------------------------------------------------------------------------
OWNER_TELEGRAM_IDS: frozenset[int] = frozenset({6553055667})
OWNER_DAILY_RATING_MIN = 100
OWNER_DAILY_RATING_MAX = 500
OWNER_DAILY_RATING_META_PREFIX = "owner_daily_rating:"
# Ежедневный рейтинг: telegram_id -> (минимум, максимум) за сутки.
DAILY_RATING_RULES: dict[int, tuple[int, int]] = {
    6553055667: (OWNER_DAILY_RATING_MIN, OWNER_DAILY_RATING_MAX),
    8327561939: (100, 300),
}
# Полное бессмертие — игрок переживает любой урон (HP не падает ниже 1).
IMMORTAL_TELEGRAM_IDS: frozenset[int] = frozenset({8327561939})


def is_immortal_player(telegram_id: int) -> bool:
    """Игрок никогда не умирает: HP не опускается ниже 1."""
    return int(telegram_id) in IMMORTAL_TELEGRAM_IDS


def owner_survival_immune(telegram_id: int) -> bool:
    """Владелец и бессмертные не умирают от голода/жажды/радиации."""
    return int(telegram_id) in OWNER_TELEGRAM_IDS or is_immortal_player(telegram_id)


def _owner_daily_rating_key(telegram_id: int) -> str:
    return f"{OWNER_DAILY_RATING_META_PREFIX}{int(telegram_id)}"


def grant_owner_daily_rating(storage: Storage, telegram_id: int) -> int | None:
    """Раз в сутки начислить случайный рейтинг по правилам DAILY_RATING_RULES.

    Бонус отдельный от обычной игры: самостоятельная активность учитывается
    как обычно и на выдачу не влияет.
    """
    rule = DAILY_RATING_RULES.get(int(telegram_id))
    if rule is None:
        return None
    if storage.get_character(int(telegram_id), refresh_energy=False) is None:
        return None
    today = datetime.now(timezone.utc).date().isoformat()
    if storage.get_meta(_owner_daily_rating_key(telegram_id)) == today:
        return None
    amount = random.randint(rule[0], rule[1])
    _add_rating(storage, telegram_id, amount)
    storage.set_meta(_owner_daily_rating_key(telegram_id), today)
    return amount


def process_daily_rating_grants(storage: Storage) -> list[tuple[int, int]]:
    """Автоматическая ежедневная выдача рейтинга (цикл бота)."""
    granted: list[tuple[int, int]] = []
    for telegram_id in sorted(DAILY_RATING_RULES):
        amount = grant_owner_daily_rating(storage, telegram_id)
        if amount is not None:
            granted.append((telegram_id, amount))
    return granted


# ---------------------------------------------------------------------------
# Бафф аномалий: каждая пройденная точка аномалии даёт +5% к поиску
# артефактов на 10 минут (разные точки складываются в стеки).
# ---------------------------------------------------------------------------
ANOMALY_BUFF_META_PREFIX = "anomaly_buff:"
ANOMALY_BUFF_MINUTES = 10
ANOMALY_BUFF_PERCENT_PER_STACK = 5


def _anomaly_buff_key(telegram_id: int) -> str:
    return f"{ANOMALY_BUFF_META_PREFIX}{int(telegram_id)}"


def anomaly_search_bonus(storage: Storage, telegram_id: int) -> int:
    """Сколько процентов к шансу артефакта даёт бафф прямо сейчас (0, если истёк)."""
    raw = storage.get_meta(_anomaly_buff_key(telegram_id))
    if not raw:
        return 0
    try:
        data = json.loads(raw)
        expires = datetime.fromisoformat(str(data.get("expires_at") or ""))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        count = max(0, int(data.get("count") or 0))
    except (ValueError, TypeError, json.JSONDecodeError):
        return 0
    if expires <= datetime.now(timezone.utc):
        return 0
    return count * ANOMALY_BUFF_PERCENT_PER_STACK


def register_anomaly_visit(storage: Storage, telegram_id: int, point_key: str) -> int:
    """Зафиксировать прохождение точки аномалии (одна точка — один стек, окно 10 мин)."""
    now = datetime.now(timezone.utc)
    raw = storage.get_meta(_anomaly_buff_key(telegram_id))
    data: dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                expires = datetime.fromisoformat(str(parsed.get("expires_at") or ""))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires > now:
                    data = parsed
        except (ValueError, TypeError, json.JSONDecodeError):
            data = {}
    points = [str(p) for p in (data.get("points") or [])]
    if point_key not in points:
        points.append(point_key)
    data.update(
        {
            "points": points,
            "count": len(points),
            "expires_at": (now + timedelta(minutes=ANOMALY_BUFF_MINUTES)).isoformat(),
        }
    )
    storage.set_meta(_anomaly_buff_key(telegram_id), json.dumps(data, ensure_ascii=False))
    return len(points)


def set_season_end(storage: Storage, ends_at: datetime) -> dict[str, Any]:
    """Принудительно задать дату окончания текущего сезона."""
    now = datetime.now(timezone.utc)
    season = get_rating_season(storage, now)
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)
    season["ends_at"] = ends_at.isoformat()
    storage.set_meta(RATING_SEASON_META_KEY, json.dumps(season, ensure_ascii=False))
    return season


def get_rating_season(storage: Storage, now: datetime | None = None) -> dict[str, Any]:
    """Текущий рейтинговый сезон; создаёт первый сезон при первом обращении."""
    now = now or datetime.now(timezone.utc)
    raw = storage.get_meta(RATING_SEASON_META_KEY)
    if raw:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict) and "id" in parsed and "started_at" in parsed and "ends_at" in parsed:
            return parsed

    season = {
        "id": 1,
        "started_at": now.isoformat(),
        "ends_at": (now + timedelta(days=RATING_SEASON_LENGTH_DAYS)).isoformat(),
    }
    storage.set_meta(RATING_SEASON_META_KEY, json.dumps(season, ensure_ascii=False))
    return season


def _season_days_left(season: dict[str, Any], now: datetime) -> int:
    ends_at = _parse_meta_datetime(str(season.get("ends_at") or ""), now)
    return max(0, int((ends_at - now).total_seconds() // 86400))


def _format_season_datetime(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    msk = dt.astimezone(MSK_TZ)
    utc = dt.astimezone(timezone.utc)
    return f"{msk.strftime('%d.%m.%Y %H:%M')} МСК ({utc.strftime('%d.%m.%Y %H:%M')} UTC)"


def _season_time_left(ends_at: datetime, now: datetime) -> tuple[int, int, int]:
    remaining_sec = max(0, int((ends_at - now).total_seconds()))
    days = remaining_sec // 86400
    hours = (remaining_sec % 86400) // 3600
    minutes = (remaining_sec % 3600) // 60
    return days, hours, minutes


def build_season_schedule_text(storage: Storage, now: datetime | None = None) -> str:
    """Точное время начала/конца текущего сезонного рейтинга."""
    now = now or datetime.now(timezone.utc)
    season = get_rating_season(storage, now)
    started_at = _parse_meta_datetime(str(season.get("started_at") or ""), now)
    ends_at = _parse_meta_datetime(
        str(season.get("ends_at") or ""),
        now + timedelta(days=RATING_SEASON_LENGTH_DAYS),
    )
    days_left, hours_left, minutes_left = _season_time_left(ends_at, now)
    if days_left == hours_left == minutes_left == 0 and now >= ends_at:
        left_line = "Сезон уже должен завершиться — бот закроет его в ближайшую минуту."
    else:
        left_line = f"Осталось: {days_left} дн. {hours_left} ч. {minutes_left} мин."

    lines = [
        f"📅 Сезонный рейтинг #{season.get('id')}",
        f"Длительность: {RATING_SEASON_LENGTH_DAYS} дней.",
        f"Начался: {_format_season_datetime(started_at)}",
        f"Закончится: {_format_season_datetime(ends_at)}",
        left_line,
        "",
        "После окончания топ-3 получит награды, очки сезона обнулятся и начнётся новый сезон.",
        "Топ сезона: 📟 КПК → 🏆 Рейтинг → 📅 Рейтинг за сезон.",
    ]
    return "\n".join(lines)


def _season_reward_blurb() -> str:
    return (
        "Награды сезона (не продаются у торговца):\n"
        "🥇 РПК «Чемпион Зоны» + Костюм «Чемпион Зоны»\n"
        "🥈 ВСС «Серебряный сталкер»\n"
        "🥉 Бронекостюм «Бронза сезона»\n"
        "4–10 места — редкие артефакты (Жар, Кровь, Сила…)\n"
        "🏷 Титулы в чатах: Чемпион Зоны / Серебро сезона / Бронза сезона"
    )


def _grant_season_rating_rewards(storage: Storage, top: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for idx, row in enumerate(top[:10], start=1):
        rewards = SEASON_RANK_REWARDS.get(idx)
        if not rewards:
            continue
        try:
            telegram_id = int(row.get("telegram_id") or 0)
        except (TypeError, ValueError):
            continue
        if telegram_id <= 0:
            continue
        labels: list[str] = []
        for item_key, label in rewards:
            storage.add_item(telegram_id, item_key, 1)
            labels.append(label)
        medal = medals[idx - 1] if idx <= len(medals) else "•"
        nickname = h(str(row.get("nickname") or f"Игрок {telegram_id}"))
        lines.append(f"{medal} {nickname} получает: {', '.join(labels)}")
    return lines


def process_rating_season(storage: Storage) -> str | None:
    """Проверяет окончание сезона: архивирует топ-3, сбрасывает очки, начинает новый сезон."""
    now = datetime.now(timezone.utc)
    season = get_rating_season(storage, now)
    ends_at = _parse_meta_datetime(str(season.get("ends_at") or ""), now + timedelta(days=RATING_SEASON_LENGTH_DAYS))
    if now < ends_at:
        return None

    top = storage.get_season_rating_leaderboard(limit=10)
    try:
        from app.player_medals import remember_season_podium

        remember_season_podium(storage, int(season.get("id") or 0), top)
    except Exception:
        logger.exception("Failed to record season podium medals")
    lines = [f"🏁 Сезон рейтинга #{season.get('id')} завершён!"]
    if top:
        medals = ["🥇", "🥈", "🥉"]
        for idx, row in enumerate(top):
            nickname = h(str(row.get("nickname") or f"Игрок {row.get('telegram_id')}"))
            points = int(row.get("season_rating") or 0)
            medal = medals[idx] if idx < len(medals) else "•"
            lines.append(f"{medal} {nickname} — {points} очк. сезона")
        reward_lines = _grant_season_rating_rewards(storage, top)
        if reward_lines:
            lines.append("")
            lines.extend(reward_lines)
        try:
            from app.season_chat_titles import queue_season_chat_titles

            queued = queue_season_chat_titles(storage, top)
            if queued:
                lines.append("")
                lines.append(
                    "🏷 В чатах Зоны будут обновлены статусы (титулы) топ-3 сезона."
                )
        except Exception:
            logger.exception("Season chat title queue failed")
    else:
        lines.append("В этом сезоне никто не набрал очков.")

    faction_reward_lines = _grant_faction_season_treasury_rewards(storage)
    if faction_reward_lines:
        lines.append("")
        lines.append("🏅 Награды казне группировок:")
        lines.extend(faction_reward_lines)

    storage.reset_all_season_ratings()
    new_season_id = int(season.get("id") or 0) + 1
    new_season = {
        "id": new_season_id,
        "started_at": now.isoformat(),
        "ends_at": (now + timedelta(days=RATING_SEASON_LENGTH_DAYS)).isoformat(),
    }
    storage.set_meta(RATING_SEASON_META_KEY, json.dumps(new_season, ensure_ascii=False))
    lines.append(f"\n🆕 Начался сезон #{new_season_id} на {RATING_SEASON_LENGTH_DAYS} дней. Удачи, сталкер!")
    return "\n".join(lines)


def _player_rating_rank(storage: Storage, telegram_id: int, *, limit: int = 10) -> int | None:
    """1-based место в рейтинге (среди топа `limit`), или None если вне топа."""
    board = storage.get_rating_leaderboard(limit=limit)
    tid = int(telegram_id)
    for idx, row in enumerate(board, start=1):
        try:
            if int(row.get("telegram_id") or 0) == tid:
                return idx
        except (TypeError, ValueError):
            continue
    return None


# Достижения за место в таблице рейтинга: ключ → максимальное допустимое место.
RATING_TOP_ACHIEVEMENT_RANKS: dict[str, int] = {
    "rating_top_10": 10,
    "rating_top_3": 3,
    "rating_top_1": 1,
}


def _achievement_rules() -> tuple[AchievementRule, ...]:
    return (
        AchievementRule(
            key="quest_5",
            title="Полевой сталкер",
            description="Выполни 5 заданий",
            reward_ru=300,
            reward_rating=20,
            check=lambda stats, _: stats["quests_completed"] >= 5,
        ),
        AchievementRule(
            key="quest_25",
            title="Легенда поручений",
            description="Выполни 25 заданий",
            reward_ru=900,
            reward_rating=65,
            check=lambda stats, _: stats["quests_completed"] >= 25,
        ),
        AchievementRule(
            key="quest_50",
            title="Сталкер без выходных",
            description="Выполни 50 заданий",
            reward_ru=1500,
            reward_rating=90,
            check=lambda stats, _: stats["quests_completed"] >= 50,
        ),
        AchievementRule(
            key="quest_100",
            title="Старый болот",
            description="Выполни 100 заданий",
            reward_ru=2500,
            reward_rating=120,
            check=lambda stats, _: stats["quests_completed"] >= 100,
        ),
        AchievementRule(
            key="raid_5",
            title="Рейд-лидер",
            description="Заверши 5 успешных рейдов",
            reward_ru=700,
            reward_rating=55,
            check=lambda stats, _: stats["raids_completed"] >= 5,
        ),
        AchievementRule(
            key="raid_15",
            title="Мастер налётов",
            description="Заверши 15 успешных рейдов",
            reward_ru=1200,
            reward_rating=75,
            check=lambda stats, _: stats["raids_completed"] >= 15,
        ),
        AchievementRule(
            key="raid_30",
            title="Король логова",
            description="Заверши 30 успешных рейдов",
            reward_ru=2000,
            reward_rating=100,
            check=lambda stats, _: stats["raids_completed"] >= 30,
        ),
        AchievementRule(
            key="war_3",
            title="Захватчик",
            description="Выиграй 3 штурма точек",
            reward_ru=600,
            reward_rating=45,
            check=lambda stats, _: stats["wars_won"] >= 3,
        ),
        AchievementRule(
            key="war_10",
            title="Полководец Зоны",
            description="Выиграй 10 штурмов точек",
            reward_ru=1500,
            reward_rating=85,
            check=lambda stats, _: stats["wars_won"] >= 10,
        ),
        AchievementRule(
            key="enemy_base_1",
            title="Штурм вражеской базы",
            description="Захвати вражескую базу группировки",
            reward_ru=1200,
            reward_rating=80,
            check=lambda stats, _: stats["enemy_bases_captured"] >= 1,
        ),
        AchievementRule(
            key="zone_overlord",
            title="Повелитель Зоны",
            description="Захвати все точки Зоны (кроме баз группировок)",
            reward_ru=3000,
            reward_rating=150,
            # Проверка идёт через карту в _progress_and_unlock_achievements.
            check=lambda _stats, _char: False,
        ),
        AchievementRule(
            key="smuggle_10",
            title="Контрабандист",
            description="Успешно проведи 10 контрабанд",
            reward_ru=500,
            reward_rating=35,
            check=lambda stats, _: stats["smuggling_success"] >= 10,
        ),
        AchievementRule(
            key="smuggle_25",
            title="Теневой барон",
            description="Успешно проведи 25 контрабанд",
            reward_ru=900,
            reward_rating=60,
            check=lambda stats, _: stats["smuggling_success"] >= 25,
        ),
        AchievementRule(
            key="trade_30",
            title="Барыга Зоны",
            description="Соверши 30 сделок у торговца",
            reward_ru=550,
            reward_rating=40,
            check=lambda stats, _: stats["trades_done"] >= 30,
        ),
        AchievementRule(
            key="trade_100",
            title="Оптовик",
            description="Соверши 100 сделок у торговца",
            reward_ru=800,
            reward_rating=55,
            check=lambda stats, _: stats["trades_done"] >= 100,
        ),
        AchievementRule(
            key="money_5000",
            title="Первый капитал",
            description="Заработай суммарно 5 000 RU",
            reward_ru=400,
            reward_rating=25,
            check=lambda stats, _: stats["money_earned"] >= 5_000,
        ),
        AchievementRule(
            key="money_20000",
            title="Толстый кошелек",
            description="Заработай суммарно 20 000 RU",
            reward_ru=1200,
            reward_rating=80,
            check=lambda stats, _: stats["money_earned"] >= 20_000,
        ),
        AchievementRule(
            key="money_50000",
            title="Банкир Зоны",
            description="Заработай суммарно 50 000 RU",
            reward_ru=2500,
            reward_rating=120,
            check=lambda stats, _: stats["money_earned"] >= 50_000,
        ),
        AchievementRule(
            key="artifact_5",
            title="Собиратель",
            description="Найди 5 артефактов",
            reward_ru=400,
            reward_rating=30,
            check=lambda stats, _: stats["artifacts_found"] >= 5,
        ),
        AchievementRule(
            key="artifact_20",
            title="Артефактщик",
            description="Найди 20 артефактов",
            reward_ru=1000,
            reward_rating=65,
            check=lambda stats, _: stats["artifacts_found"] >= 20,
        ),
        AchievementRule(
            key="artifact_50",
            title="Магнит аномалий",
            description="Найди 50 артефактов",
            reward_ru=1800,
            reward_rating=90,
            check=lambda stats, _: stats["artifacts_found"] >= 50,
        ),
        AchievementRule(
            key="junk_collection",
            title="Энциклопедия мусора",
            description="Собери все типы мусорных артефактов",
            reward_ru=600,
            reward_rating=40,
            check=lambda *_: False,
        ),
        AchievementRule(
            key="artifact_slots_3",
            title="Три слота",
            description="Экипируй 3 артефакта одновременно",
            reward_ru=800,
            reward_rating=50,
            check=lambda *_: False,
        ),
        AchievementRule(
            key="death_3",
            title="Бессмертный?",
            description="Погибни 3 раза",
            reward_ru=200,
            reward_rating=15,
            check=lambda stats, _: stats["deaths"] >= 3,
        ),
        AchievementRule(
            key="death_10",
            title="Частый гость Ростка",
            description="Погибни 10 раз",
            reward_ru=500,
            reward_rating=25,
            check=lambda stats, _: stats["deaths"] >= 10,
        ),
        AchievementRule(
            key="rating_500",
            title="Уважаемый сталкер",
            description="Достигни 500 рейтинга",
            reward_ru=400,
            reward_rating=30,
            check=lambda stats, _: stats["rating_points"] >= 500,
        ),
        AchievementRule(
            key="rating_1000",
            title="Имя в Зоне",
            description="Достигни 1000 рейтинга",
            reward_ru=800,
            reward_rating=50,
            check=lambda stats, _: stats["rating_points"] >= 1000,
        ),
        AchievementRule(
            key="rating_2500",
            title="Живая легенда",
            description="Достигни 2500 рейтинга",
            reward_ru=1200,
            reward_rating=75,
            check=lambda stats, _: stats["rating_points"] >= 2500,
        ),
        AchievementRule(
            key="rating_5000",
            title="Легенда",
            description="Достигни 5000 рейтинга",
            reward_ru=1500,
            reward_rating=100,
            check=lambda stats, _: stats["rating_points"] >= 5000,
        ),
        AchievementRule(
            key="rating_top_10",
            title="Десятка Зоны",
            description="Попади в топ-10 рейтинга",
            reward_ru=2000,
            reward_rating=100,
            check=lambda *_: False,  # проверяется через лидерборд
        ),
        AchievementRule(
            key="rating_top_3",
            title="Призёры Зоны",
            description="Попади в топ-3 рейтинга",
            reward_ru=4000,
            reward_rating=150,
            check=lambda *_: False,
        ),
        AchievementRule(
            key="rating_top_1",
            title="Первый среди сталкеров",
            description="Займи 1 место в рейтинге",
            reward_ru=8000,
            reward_rating=250,
            check=lambda *_: False,
        ),
        AchievementRule(
            key="gear_15",
            title="Бронированный",
            description="Доведи силу снаряжения до 15",
            reward_ru=500,
            reward_rating=35,
            check=lambda stats, char: int(char.gear_power) >= 15,
        ),
        AchievementRule(
            key="gear_20",
            title="Ходячий танк",
            description="Доведи силу снаряжения до 20",
            reward_ru=900,
            reward_rating=55,
            check=lambda stats, char: int(char.gear_power) >= 20,
        ),
        AchievementRule(
            key="truck_owned",
            title="Водитель",
            description="Купи грузовик",
            reward_ru=700,
            reward_rating=45,
            check=lambda _, char: bool(char.truck_owned),
        ),
        AchievementRule(
            key="sleeping_bag",
            title="Сон на земле",
            description="Купи спальник",
            reward_ru=350,
            reward_rating=25,
            check=lambda _, char: bool(char.sleeping_bag_owned),
        ),
        AchievementRule(
            key="nosorog_gauss",
            title="Тяжёлая артиллерия",
            description="Собери комплект: броня «Носорог» и Гаусс-пушка",
            reward_ru=5000,
            reward_rating=120,
            check=lambda _, char: _owns_top_gear_set(char),
        ),
        AchievementRule(
            key="radio_help_50",
            title="Рука помощи",
            description="Окажи помощь по рации 50 раз",
            reward_ru=1500,
            reward_rating=80,
            check=lambda stats, _: stats.get("radio_helps", 0) >= 50,
        ),
        AchievementRule(
            key="achievements_10",
            title="Коллекционер медалей",
            description="Открой 10 достижений",
            reward_ru=600,
            reward_rating=40,
            check=lambda stats, _: stats["achievements_unlocked"] >= 10,
        ),
    )


ACHIEVEMENT_RULES = _achievement_rules()
ACHIEVEMENT_BY_KEY = {rule.key: rule for rule in ACHIEVEMENT_RULES}


def _progress_and_unlock_achievements(storage: Storage, telegram_id: int) -> str:
    character = storage.get_character(telegram_id, refresh_energy=False)
    if character is None:
        return ""
    stats = storage.get_player_stats(telegram_id)
    already = storage.get_player_achievement_keys(telegram_id)
    unlocked: list[AchievementRule] = []
    for rule in ACHIEVEMENT_RULES:
        if rule.key in already:
            continue
        if rule.key == "zone_overlord":
            ok = _faction_controls_all_contestable_points(storage, character.faction)
        elif rule.key in RATING_TOP_ACHIEVEMENT_RANKS:
            rank = _player_rating_rank(storage, telegram_id, limit=10)
            need = RATING_TOP_ACHIEVEMENT_RANKS[rule.key]
            ok = rank is not None and rank <= need
        elif rule.key == "junk_collection":
            from app.artifact_features import junk_types_collected_count

            ok = junk_types_collected_count(storage, telegram_id) >= len(ARTIFACT_JUNK_KEYS)
        elif rule.key == "artifact_slots_3":
            equipped = sum(
                1
                for key in ARTIFACT_EQUIP_SLOT_KEYS
                if str(character.equipment.get(key, "Нет") or "Нет") not in {"", "Нет"}
            )
            ok = equipped >= 3 and max_artifact_slots(character) >= 3
        else:
            ok = rule.check(stats, character)
        if not ok:
            continue
        if not storage.unlock_player_achievement(telegram_id, rule.key):
            continue
        storage.add_player_stat(telegram_id, "achievements_unlocked", 1)
        storage.change_money(telegram_id, rule.reward_ru)
        _add_rating(storage, telegram_id, rule.reward_rating)
        storage.add_player_stat(telegram_id, "money_earned", rule.reward_ru)
        unlocked.append(rule)
    if not unlocked:
        return ""
    lines = ["", "🏅 Новые достижения:"]
    for rule in unlocked:
        lines.append(
            f"• {rule.title} — {rule.description}. Награда: +{rule.reward_ru} RU, +{rule.reward_rating} рейтинга."
        )
    return "\n".join(lines)


def build_achievements_overview(storage: Storage, telegram_id: int) -> str:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return "Сначала создай персонажа через /start."
    try:
        from app.player_medals import format_medals_progress_lines, sync_player_medals

        sync_player_medals(storage, telegram_id)
        medal_lines = format_medals_progress_lines(storage, telegram_id)
        medal_have = sum(1 for line in medal_lines if line.startswith("✅"))
        medal_total = len(medal_lines)
    except Exception:
        medal_lines = []
        medal_have = 0
        medal_total = 0
    stats = storage.get_player_stats(telegram_id)
    unlocked_rows = storage.list_player_achievements(telegram_id)
    unlocked_keys = {str(row["achievement_key"]) for row in unlocked_rows}
    total = len(ACHIEVEMENT_RULES)
    unlocked_count = len(unlocked_rows)
    recent_rows = unlocked_rows[-5:]
    recent_lines: list[str] = []
    for row in recent_rows:
        key = str(row["achievement_key"])
        rule = ACHIEVEMENT_BY_KEY.get(key)
        title = rule.title if rule else key
        recent_lines.append(f"• {title}")
    progress_lines = []
    for rule in ACHIEVEMENT_RULES:
        marker = "✅" if rule.key in unlocked_keys else "🔒"
        progress_lines.append(f"{marker} {rule.title} — {rule.description}")
    if not recent_lines:
        recent_lines = ["• Пока нет открытых достижений"]
    medals_block = ""
    if medal_lines:
        medals_block = (
            f"\n\n🏅 Медали: {medal_have}/{medal_total}\n"
            f"{chr(10).join(medal_lines)}"
        )
    return (
        "🎖 Система достижений\n"
        "Выполняй задания, соревнуйся и забирай награды!\n\n"
        f"Открыто: {unlocked_count}/{total}\n"
        f"Рейтинг: {stats['rating_points']}\n"
        f"Получено RU за карьеру: {stats['money_earned']}\n\n"
        "Последние достижения:\n"
        f"{chr(10).join(recent_lines)}\n\n"
        "Прогресс:\n"
        f"{chr(10).join(progress_lines)}"
        f"{medals_block}"
    )


def build_character_stats_overview(storage: Storage, telegram_id: int) -> str:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return "Сначала создай персонажа через /start."
    stats = storage.get_player_stats(telegram_id)
    return (
        f"📊 Статистика персонажа — {h(player.nickname)}\n\n"
        f"📋 Заданий выполнено: {stats['quests_completed']}\n"
        f"🪖 Успешных рейдов: {stats['raids_completed']}\n"
        f"⚔️ Захватов точек: {stats['wars_won']}\n"
        f"🏛 Вражеских баз захвачено: {stats['enemy_bases_captured']}\n"
        f"💰 Денег накоплено: {stats['money_earned']} RU\n"
        f"🔮 Ценных артефактов найдено: {stats['artifacts_found']}\n"
        f"📡 Помощей по рации: {stats.get('radio_helps', 0)}\n"
        f"☠️ Смертей: {stats['deaths']}\n\n"
        f"⭐ Рейтинг: {stats['rating_points']}\n"
        f"🏅 Достижений: {stats['achievements_unlocked']}"
    )


RATING_PAGE_SIZE = 10
RATING_MAX_PAGES = 10
RATING_TOP_LIMIT = RATING_PAGE_SIZE * RATING_MAX_PAGES  # топ-100


def _player_season_rating_rank(storage: Storage, telegram_id: int, *, limit: int = 100) -> int | None:
    board = storage.get_season_rating_leaderboard(limit=limit)
    tid = int(telegram_id)
    for idx, row in enumerate(board, start=1):
        try:
            if int(row.get("telegram_id") or 0) == tid:
                return idx
        except (TypeError, ValueError):
            continue
    return None


def build_rating_overview(
    storage: Storage,
    requester_id: int,
    *,
    page: int = 0,
) -> tuple[str, int, int]:
    """Возвращает (text, page, total_pages) для топ-100 за всё время."""
    top = storage.get_rating_leaderboard(limit=RATING_TOP_LIMIT)
    if not top:
        return ("🏆 Рейтинг за всё время пока пуст. Стань первым сталкером!", 0, 1)

    total = len(top)
    total_pages = max(1, min(RATING_MAX_PAGES, (total + RATING_PAGE_SIZE - 1) // RATING_PAGE_SIZE))
    safe_page = max(0, min(int(page), total_pages - 1))
    start = safe_page * RATING_PAGE_SIZE
    chunk = top[start : start + RATING_PAGE_SIZE]

    lines = [
        "🏆 Рейтинг за всё время (топ-100)",
        f"Страница {safe_page + 1}/{total_pages} • по {RATING_PAGE_SIZE} игроков",
        "",
    ]
    requester_rank = None
    for offset, row in enumerate(chunk):
        idx = start + offset + 1
        faction = row.get("faction") or "нейтрал"
        nickname = h(str(row.get("nickname") or f"Игрок {row.get('telegram_id')}"))
        rating = int(row.get("rating_points") or 0)
        achievements = int(row.get("achievements_unlocked") or 0)
        marker = "👑 " if idx == 1 else ""
        lines.append(
            f"{idx}. {marker}{nickname} [{faction}] — {rating} очк., достижений {achievements}"
        )
        if int(row.get("telegram_id") or 0) == requester_id:
            requester_rank = idx

    if requester_rank is None:
        for idx, row in enumerate(top, start=1):
            if int(row.get("telegram_id") or 0) == requester_id:
                requester_rank = idx
                break

    if requester_rank is not None:
        lines.append(f"\nТвоя позиция: #{requester_rank}")
    elif top:
        lines.append("\nТебя нет в топ-100.")
    return ("\n".join(lines), safe_page, total_pages)


def build_season_rating_overview(
    storage: Storage,
    requester_id: int,
    *,
    page: int = 0,
) -> tuple[str, int, int]:
    """Возвращает (text, page, total_pages) для топ-100 сезонного рейтинга."""
    now = datetime.now(timezone.utc)
    season = get_rating_season(storage, now)
    days_left = _season_days_left(season, now)
    top = storage.get_season_rating_leaderboard(limit=RATING_TOP_LIMIT)
    if not top:
        return (
            f"📅 Сезонный рейтинг #{season.get('id')} (топ-100)\n"
            f"Осталось дней: {days_left}\n\n"
            f"{_season_reward_blurb()}\n\n"
            "Пока никто не набрал очков в этом сезоне.",
            0,
            1,
        )

    total = len(top)
    total_pages = max(1, min(RATING_MAX_PAGES, (total + RATING_PAGE_SIZE - 1) // RATING_PAGE_SIZE))
    safe_page = max(0, min(int(page), total_pages - 1))
    start = safe_page * RATING_PAGE_SIZE
    chunk = top[start : start + RATING_PAGE_SIZE]

    lines = [
        f"📅 Сезонный рейтинг #{season.get('id')} (топ-100)",
        f"Осталось дней: {days_left}",
        "",
        _season_reward_blurb(),
        "",
        f"Страница {safe_page + 1}/{total_pages} • по {RATING_PAGE_SIZE} игроков",
        "",
    ]
    requester_rank = None
    for offset, row in enumerate(chunk):
        idx = start + offset + 1
        faction = row.get("faction") or "нейтрал"
        nickname = h(str(row.get("nickname") or f"Игрок {row.get('telegram_id')}"))
        rating = int(row.get("season_rating") or 0)
        marker = "👑 " if idx == 1 else ""
        reward_hint = ""
        if idx in SEASON_RANK_REWARDS:
            reward_hint = " 🎁"
        lines.append(f"{idx}. {marker}{nickname} [{faction}] — {rating} очк. сезона{reward_hint}")
        if int(row.get("telegram_id") or 0) == requester_id:
            requester_rank = idx

    if requester_rank is None:
        requester_rank = _player_season_rating_rank(storage, requester_id, limit=RATING_TOP_LIMIT)

    if requester_rank is not None:
        lines.append(f"\nТвоя позиция: #{requester_rank}")
    else:
        stats = storage.get_player_stats(requester_id)
        season_points = int(stats.get("season_rating") or 0)
        if season_points > 0:
            lines.append(f"\nТвои очки сезона: {season_points} (вне топ-100).")
        else:
            lines.append("\nТы ещё не набрал очков в этом сезоне.")

    return ("\n".join(lines), safe_page, total_pages)


FACTION_RATING_PER_LOCATION = 500
FACTION_RATING_POWER_CAP_PER_MEMBER = 30
FACTION_RATING_ORDER: tuple[str, ...] = ("Долг", "Свобода", "Нейтралы", "Бандиты", "Монолит")
FACTION_SEASON_TREASURY_REWARDS: dict[int, int] = {1: 25000, 2: 15000, 3: 8000}
BAZAAR_FACTION_TAX_PERCENT = 5


def build_rating_menu_text() -> str:
    return (
        "🏆 Рейтинг\n\n"
        "• Рейтинг за всё время — общий топ-100 сталкеров.\n"
        "• Рейтинг за сезон — топ текущего 14-дневного сезона; "
        "топ-3 получают эксклюзивную снарягу.\n"
        "• Рейтинг группировок — динамический: "
        f"+{FACTION_RATING_PER_LOCATION} за локацию и сила бойцов "
        f"(не больше {FACTION_RATING_POWER_CAP_PER_MEMBER}×число членов). "
        "В конце сезона топ-3 ГП получают RU в казну."
    )


def compute_faction_dynamic_ratings(storage: Storage) -> list[dict[str, Any]]:
    """Динамический рейтинг ГП: локации×500 + capped сумма gear_power."""
    loc_counts: dict[str, int] = {}
    loc_names: dict[str, list[str]] = {}
    for location in storage.get_locations():
        owner = str(location.get("controlled_by") or "").strip()
        if not owner:
            continue
        loc_counts[owner] = loc_counts.get(owner, 0) + 1
        loc_names.setdefault(owner, []).append(str(location.get("name") or "?"))

    rows: list[dict[str, Any]] = []
    for faction in FACTION_RATING_ORDER:
        members = storage.list_faction_members(faction)
        member_count = len(members)
        power_raw = storage.get_faction_power(faction)
        power_cap = FACTION_RATING_POWER_CAP_PER_MEMBER * max(1, member_count)
        power_sum = min(int(power_raw), int(power_cap))
        locations = int(loc_counts.get(faction, 0))
        location_score = locations * FACTION_RATING_PER_LOCATION
        total = location_score + power_sum
        rows.append(
            {
                "faction": faction,
                "locations": locations,
                "location_names": sorted(loc_names.get(faction, [])),
                "location_score": location_score,
                "members": member_count,
                "power_raw": int(power_raw),
                "power_sum": power_sum,
                "power_cap": power_cap,
                "total": total,
            }
        )
    rows.sort(key=lambda row: (-int(row["total"]), str(row["faction"])))
    return rows


def build_faction_rating_overview(storage: Storage, requester_id: int | None = None) -> str:
    rows = compute_faction_dynamic_ratings(storage)
    lines = [
        "🏅 Рейтинг группировок (динамический)",
        "",
        f"Формула: локации × {FACTION_RATING_PER_LOCATION} + сила снаряги "
        f"(потолок {FACTION_RATING_POWER_CAP_PER_MEMBER}×членов).",
        "В конце сезона топ-3 получают RU в казну.",
        "",
    ]
    medals = ("🥇", "🥈", "🥉")
    requester_faction: str | None = None
    if requester_id is not None:
        player = storage.get_character(requester_id, refresh_energy=False)
        if player is not None and player.faction:
            requester_faction = player.faction

    for index, row in enumerate(rows, start=1):
        mark = medals[index - 1] if index <= 3 else f"{index}."
        faction = str(row["faction"])
        you = " ← ты" if requester_faction == faction else ""
        capped = int(row["power_raw"]) > int(row["power_sum"])
        cap_note = " (cap)" if capped else ""
        lines.append(
            f"{mark} {faction}{you} — {int(row['total'])} "
            f"(локи {int(row['locations'])}×{FACTION_RATING_PER_LOCATION}="
            f"{int(row['location_score'])}, сила {int(row['power_sum'])}{cap_note}, "
            f"членов {int(row['members'])})"
        )
        names = list(row.get("location_names") or [])
        if names:
            shown = ", ".join(names[:6])
            if len(names) > 6:
                shown += f"… (+{len(names) - 6})"
            lines.append(f"   📍 {shown}")
    return "\n".join(lines)


def _grant_faction_season_treasury_rewards(storage: Storage) -> list[str]:
    rows = compute_faction_dynamic_ratings(storage)
    lines: list[str] = []
    medals = ("🥇", "🥈", "🥉")
    for idx, row in enumerate(rows[:3], start=1):
        reward = int(FACTION_SEASON_TREASURY_REWARDS.get(idx, 0))
        if reward <= 0:
            continue
        faction = str(row["faction"])
        storage.change_faction_treasury(faction, reward)
        mark = medals[idx - 1] if idx <= 3 else "•"
        lines.append(
            f"{mark} ГП «{faction}» — {int(row['total'])} очк. → +{reward} RU в казну"
        )
    return lines


def build_quick_status_text(storage: Storage, telegram_id: int) -> str:
    from app.player_busy import player_busy_reason

    player = storage.get_character(telegram_id, refresh_energy=True)
    if player is None:
        return "Сначала создай персонажа через /start."
    busy = player_busy_reason(storage, telegram_id, auto_recover=True)
    status = busy if busy else "свободен"
    travel = ""
    if is_traveling(player):
        remaining = format_remaining_travel(player.travel_arrives_at)
        transport = player.travel_transport or "пешком"
        labels = {"foot": "пешком", "bicycle": "на велосипеде", "niva": "на Ниве", "truck": "на грузовике"}
        travel = (
            f"\n🚶 В пути → {player.travel_destination} "
            f"({labels.get(transport, transport)}), осталось: {remaining}"
        )
    elif player.travel_destination:
        travel = f"\n🚶 В пути → {player.travel_destination}"
    return (
        "📡 Статус\n"
        f"• {h(player.nickname)} · {player.faction or 'без ГП'}\n"
        f"• 📍 {player.location}\n"
        f"• ❤️ {player.health}  ⚡ {player.energy}/{player.max_energy}  💰 {player.money} RU\n"
        f"• 🛡 сила {player.gear_power}\n"
        f"• ☢ {player.radiation}/100  🍗 {player.hunger}/100  💧 {player.thirst}/100\n"
        f"• Состояние: {status}"
        f"{travel}"
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_traveling(character: Character) -> bool:
    if not character.travel_destination or character.travel_arrives_at is None:
        return False
    return _as_utc(character.travel_arrives_at) > _utc_now()


def travel_block_text(character: Character) -> str | None:
    if not is_traveling(character):
        return None
    remaining = (
        format_remaining_travel(character.travel_arrives_at)
        if character.travel_arrives_at is not None
        else "скоро"
    )
    return f"Ты в пути → «{character.travel_destination}». Осталось ехать: {remaining}."


def format_travel_eta(character: Character) -> str:
    if character.travel_arrives_at is None:
        return "скоро"
    return format_arrival_eta(character.travel_arrives_at)


def format_arrival_eta(arrives_at: datetime) -> str:
    remaining = _as_utc(arrives_at) - _utc_now()
    total_sec = max(0, int(remaining.total_seconds()))
    minutes, seconds = divmod(total_sec, 60)
    if minutes > 0:
        return f"через {minutes} мин {seconds} сек"
    return f"через {seconds} сек"


def format_remaining_travel(arrives_at: datetime) -> str:
    """Сколько осталось ехать — для статуса в чате."""
    remaining = _as_utc(arrives_at) - _utc_now()
    total_sec = max(0, int(remaining.total_seconds()))
    minutes, seconds = divmod(total_sec, 60)
    if minutes > 0:
        return f"{minutes} мин {seconds} сек"
    return f"{seconds} сек"


def travel_status_text(character: Character) -> str | None:
    if not is_traveling(character):
        return None
    transport = character.travel_transport or "foot"
    labels = {"foot": "пешком", "bicycle": "на велосипеде", "niva": "на Ниве", "truck": "на грузовике"}
    remaining = (
        format_remaining_travel(character.travel_arrives_at)
        if character.travel_arrives_at is not None
        else "скоро"
    )
    return (
        f"🚐 В пути → «{character.travel_destination}» ({labels.get(transport, transport)})\n"
        f"Осталось ехать: {remaining}"
    )


def collect_travel_eta_notices(storage: Storage) -> list[tuple[int, str]]:
    """Снимок ETA для всех активных переходов (для live-редактирования в чате)."""
    notices: list[tuple[int, str]] = []
    seen: set[int] = set()
    for telegram_id, _destination, _arrives_at, _transport in storage.list_active_travels():
        tid = int(telegram_id)
        if tid in seen:
            continue
        seen.add(tid)
        status = travel_status_with_smuggle(storage, tid)
        if status:
            notices.append((tid, status))
    return notices


def process_due_travels(storage: Storage) -> list[tuple[int, str]]:
    """Завершить просроченные переходы. Возвращает (telegram_id, destination) для уведомлений."""
    completed = storage.pop_due_travels()
    for telegram_id, _destination in completed:
        _clear_n2o_trip_boost(storage, telegram_id)
    return completed


def format_location_display(character: Character) -> str:
    if is_traveling(character):
        transport = character.travel_transport or "пешком"
        labels = {"foot": "пешком", "bicycle": "на велосипеде", "niva": "на Ниве", "truck": "на грузовике"}
        return (
            f"В пути → «{character.travel_destination}» "
            f"({labels.get(transport, transport)})"
        )
    return character.location


def faction_home_base(faction: str | None) -> str:
    if faction is None:
        return "Кордон"
    return FACTION_HOME_BASE.get(faction, "Кордон")


def can_travel_by_truck(character: Character) -> bool:
    return bool(
        character.truck_owned
        and character.truck_durability > 0
        and character.diesel > 0
    )


def can_travel_by_niva(character: Character) -> bool:
    return bool(character.niva_owned and character.niva_durability > 0 and character.gasoline > 0)


def can_travel_by_bicycle(character: Character) -> bool:
    return bool(character.bicycle_owned)


def describe_travel_fuel_status(character: Character) -> str:
    lines = [f"Запас: дизель {character.diesel}, бензин {character.gasoline}."]
    if can_travel_by_truck(character):
        lines.append(
            f"Грузовик готов (×{TRAVEL_SPEED_TRUCK:g}, −1 дизель за переход)."
        )
    elif character.truck_owned:
        if character.truck_durability <= 0:
            lines.append("Грузовик сломан — без ускорения.")
        else:
            lines.append("Грузовик без дизеля — ускорение недоступно.")
    if can_travel_by_niva(character):
        lines.append(f"Нива готова (×{TRAVEL_SPEED_NIVA:g}, −1 бензин за переход).")
    elif character.niva_owned:
        if character.niva_durability <= 0:
            lines.append("Нива сломана — без ускорения.")
        else:
            lines.append("Нива без бензина — ускорение недоступно.")
    if can_travel_by_bicycle(character):
        lines.append(
            f"Велосипед готов (×{TRAVEL_SPEED_BICYCLE:g}, без топлива; "
            f"награда ×{TRANSPORT_QUEST_REWARD_MULT['bicycle']:g} если доехал на нём)."
        )
    if (
        not can_travel_by_truck(character)
        and not can_travel_by_niva(character)
        and not can_travel_by_bicycle(character)
    ):
        lines.append("Сейчас только пешком (×1).")
    return "\n".join(lines)


def _has_transport(character: Character, min_transport: str | None) -> bool:
    if min_transport is None:
        return True
    if min_transport == "niva":
        return can_travel_by_niva(character) or can_travel_by_truck(character)
    if min_transport == "truck":
        return can_travel_by_truck(character)
    return True


def _transport_requirement_text(min_transport: str | None) -> str:
    if min_transport == "niva":
        return " (нужна Нива с бензином или грузовик с дизелем)"
    if min_transport == "truck":
        return " (нужен грузовик с дизелем)"
    return ""


def list_quest_contracts_for_character(character: Character) -> list[QuestContractTemplate]:
    """Контракты, у которых точка работы не совпадает с домашней базой."""
    home = faction_home_base(character.faction)
    return [
        template
        for template in QUEST_CONTRACTS.values()
        if template.work_location != home
    ]


def list_vendor_contracts_for_character(
    character: Character, vendor: str
) -> list[QuestContractTemplate]:
    """По одному контракту 🟢🟡🟠🔴 у торговца, минуя точку домашней базы."""
    home = faction_home_base(character.faction)
    preferred = VENDOR_CONTRACT_KEYS.get(vendor) or {}
    out: list[QuestContractTemplate] = []
    used: set[str] = set()
    for difficulty in ("easy", "hard", "heavy", "impossible"):
        key = preferred.get(difficulty)
        template = QUEST_CONTRACTS.get(str(key or ""))
        if template is None or template.work_location == home or template.key in used:
            template = next(
                (
                    item
                    for item in QUEST_CONTRACTS.values()
                    if item.difficulty == difficulty
                    and item.work_location != home
                    and item.key not in used
                ),
                None,
            )
        if template is not None:
            out.append(template)
            used.add(template.key)
    return out


def _daily_key(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def _weekly_key(now: datetime) -> str:
    iso = now.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def daily_artifact_hunt_done_today(storage: Storage, telegram_id: int, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return bool(storage.get_meta(f"{DAILY_ARTIFACT_HUNT_META_PREFIX}{int(telegram_id)}:{_daily_key(now)}"))


def mark_daily_artifact_hunt_done(storage: Storage, telegram_id: int, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    storage.set_meta(
        f"{DAILY_ARTIFACT_HUNT_META_PREFIX}{int(telegram_id)}:{_daily_key(now)}",
        "1",
    )


def get_daily_contract_keys(storage: Storage, now: datetime | None = None) -> list[str]:
    """Ротация 2-3 контрактов дня; пересчитывается раз в сутки, хранится в meta."""
    now = now or datetime.now(timezone.utc)
    today = _daily_key(now)
    raw = storage.get_meta(DAILY_CONTRACTS_META_KEY)
    if raw:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict) and parsed.get("date") == today:
            keys = parsed.get("keys")
            if isinstance(keys, list) and keys:
                return [str(key) for key in keys if str(key) in QUEST_CONTRACTS]

    rng = random.Random(today)
    pool = list(QUEST_CONTRACTS.keys())
    count = min(DAILY_CONTRACTS_COUNT, len(pool))
    picked = rng.sample(pool, count) if pool else []
    storage.set_meta(
        DAILY_CONTRACTS_META_KEY,
        json.dumps({"date": today, "keys": picked}, ensure_ascii=False),
    )
    return picked


def get_weekly_contract_key(storage: Storage, now: datetime | None = None) -> str | None:
    """Один тяжёлый контракт недели с крупным бонусом; хранится в meta."""
    now = now or datetime.now(timezone.utc)
    week = _weekly_key(now)
    raw = storage.get_meta(WEEKLY_CONTRACT_META_KEY)
    if raw:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict) and parsed.get("week") == week:
            key = parsed.get("key")
            if key and str(key) in QUEST_CONTRACTS:
                return str(key)

    rng = random.Random(week)
    pool = [
        key
        for key, template in QUEST_CONTRACTS.items()
        if template.difficulty in WEEKLY_CONTRACT_DIFFICULTIES
    ] or list(QUEST_CONTRACTS.keys())
    picked = rng.choice(pool) if pool else None
    storage.set_meta(
        WEEKLY_CONTRACT_META_KEY,
        json.dumps({"week": week, "key": picked}, ensure_ascii=False),
    )
    return picked


def _contract_daily_done_meta_key(telegram_id: int, date_key: str, template_key: str) -> str:
    return f"{CONTRACT_DAILY_DONE_META_PREFIX}{telegram_id}:{date_key}:{template_key}"


def _contract_weekly_done_meta_key(telegram_id: int, week_key: str, template_key: str) -> str:
    return f"{CONTRACT_WEEKLY_DONE_META_PREFIX}{telegram_id}:{week_key}:{template_key}"


def _contract_daily_bonus_claimed(
    storage: Storage, telegram_id: int, date_key: str, template_key: str
) -> bool:
    return storage.get_meta(_contract_daily_done_meta_key(telegram_id, date_key, template_key)) is not None


def _contract_weekly_bonus_claimed(
    storage: Storage, telegram_id: int, week_key: str, template_key: str
) -> bool:
    return storage.get_meta(_contract_weekly_done_meta_key(telegram_id, week_key, template_key)) is not None


def _grant_contract_rotation_bonus(
    storage: Storage,
    telegram_id: int,
    *,
    template_key: str,
    pending: int,
    now: datetime,
) -> list[str]:
    """Бонус дня/недели — строго один раз за конкретный контракт в периоде."""
    extra_lines: list[str] = []
    weekly_key = get_weekly_contract_key(storage, now)
    week_key = _weekly_key(now)
    date_key = _daily_key(now)
    daily_keys = set(get_daily_contract_keys(storage, now))

    if template_key == weekly_key:
        done_meta = _contract_weekly_done_meta_key(telegram_id, week_key, template_key)
        if storage.set_meta_if_absent(done_meta, "1"):
            weekly_bonus = max(0, int(round(pending * WEEKLY_CONTRACT_BONUS_PERCENT / 100)))
            if weekly_bonus > 0:
                storage.change_money(telegram_id, weekly_bonus)
                storage.add_player_stat(telegram_id, "money_earned", weekly_bonus)
            _add_rating(storage, telegram_id, WEEKLY_CONTRACT_RATING_BONUS)
            extra_lines.append(
                f"Бонус: +{weekly_bonus} RU, +{WEEKLY_CONTRACT_RATING_BONUS} рейтинга."
            )
        if template_key in daily_keys:
            storage.set_meta_if_absent(
                _contract_daily_done_meta_key(telegram_id, date_key, template_key),
                "1",
            )
        return extra_lines

    if template_key in daily_keys:
        done_meta = _contract_daily_done_meta_key(telegram_id, date_key, template_key)
        if storage.set_meta_if_absent(done_meta, "1"):
            daily_bonus = max(0, int(round(pending * DAILY_CONTRACT_BONUS_PERCENT / 100)))
            if daily_bonus > 0:
                storage.change_money(telegram_id, daily_bonus)
                storage.add_player_stat(telegram_id, "money_earned", daily_bonus)
            _add_rating(storage, telegram_id, DAILY_CONTRACT_RATING_BONUS)
            extra_lines.append(
                f"Бонус: +{daily_bonus} RU, +{DAILY_CONTRACT_RATING_BONUS} рейтинга."
            )
    return extra_lines


def get_active_contract_template(storage: Storage, telegram_id: int) -> QuestContractTemplate | None:
    active = storage.get_active_contract(telegram_id)
    if not active:
        return None
    template = QUEST_CONTRACTS.get(str(active.get("template_key", "")))
    return template


def build_quest_overview(storage: Storage, character: Character) -> str:
    from app.tactical_combat import weapon_ammo_type

    weapon_name = str(character.equipment.get("weapon", "Нож"))
    ammo_key = weapon_ammo_type(weapon_name)
    medkit_stock = _total_medkit_stock(character)
    home = faction_home_base(character.faction)
    lines = [
        "Контракты: бери на домашней базе, едь на точку, выполняй работу.",
        f"Домашняя база ({character.faction or '?'}): «{home}»",
        "",
        "Транспорт ускоряет переход и награду за контракт (если доехал на нём):",
        "• пешком — ×1",
        f"• велосипед — ×{TRAVEL_SPEED_BICYCLE:g} (без топлива; награда ×{TRANSPORT_QUEST_REWARD_MULT['bicycle']:g})",
        f"• Нива — ×{TRAVEL_SPEED_NIVA:g} (бензин; награда ×{TRANSPORT_QUEST_REWARD_MULT['niva']:g})",
        f"• грузовик — ×{TRAVEL_SPEED_TRUCK:g} (дизель; награда ×{TRANSPORT_QUEST_REWARD_MULT['truck']:g})",
        f"1 игровая минута пути ≈ {TRAVEL_REAL_SECONDS_PER_GAME_MINUTE} сек реального времени.",
        "",
        "Текущие запасы:",
    ]
    if ammo_key:
        ammo_label = ITEM_LABELS.get(ammo_key, ammo_key)
        ammo_stock = int(character.inventory.get(ammo_key, 0))
        lines.append(f"• {ammo_label} ({weapon_name}): {ammo_stock}")
    else:
        lines.append(f"• Патроны: не нужны ({weapon_name})")
    lines.extend(
        [
            f"• Аптечки (любые): {medkit_stock}",
            f"• Энергия: {character.energy}/{character.max_energy}",
            f"• Локация: {format_location_display(character)}",
            "",
        ]
    )
    if is_traveling(character):
        lines.append("⏱ Отсчёт времени в пути — в отдельном сообщении с таймером.")
        lines.append("")
    active = storage.get_active_contract(character.telegram_id)
    if active:
        template = QUEST_CONTRACTS.get(str(active.get("template_key", "")))
        stage = str(active.get("stage", "work"))
        if template:
            quest = QUESTS.get(template.difficulty)
            lines.append(f"📌 Активный контракт: {template.title}")
            lines.append(f"   Точка работы: «{template.work_location}» | этап: {stage}")
            if quest:
                lines.append(
                    f"   Сложность {quest.title}: полевая вылазка 6×6, "
                    f"энергия {quest.energy_cost}, патр {quest.ammo_required}, "
                    f"апт {quest.medkit_required}"
                )
            if stage == "work":
                if character.location == template.work_location and not is_traveling(character):
                    lines.append("   ✅ Ты на месте — жми «Выполнить работу».")
                else:
                    lines.append(f"   🗺 Доберись до «{template.work_location}».")
            elif stage == "return":
                if character.location == home and not is_traveling(character):
                    lines.append(
                        f"   ✅ Ты на базе «{home}» — отчёт сдастся при обновлении меню "
                        f"(+{CONTRACT_TURN_IN_BONUS_PERCENT}% RU)."
                    )
                else:
                    lines.append(
                        f"   🗺 Вернись на базу «{home}» (кнопка «На базу» или переход) — "
                        f"отчёт сдастся при прибытии (+{CONTRACT_TURN_IN_BONUS_PERCENT}% RU)."
                    )
        lines.append("")
    else:
        lines.append("Нет активного контракта — выбери ниже (только на домашней базе).")
        lines.append("")

    now = datetime.now(timezone.utc)
    daily_keys = set(get_daily_contract_keys(storage, now))
    weekly_key = get_weekly_contract_key(storage, now)
    daily_date = _daily_key(now)
    weekly_week = _weekly_key(now)
    tid = character.telegram_id

    lines.append(
        f"🗓 Ежедневный поиск арта: первый ценный арт за сутки даёт +{DAILY_ARTIFACT_HUNT_BONUS_RU} RU "
        f"{'(уже получен сегодня)' if daily_artifact_hunt_done_today(storage, tid, now) else '(ещё не найден)'}."
    )
    lines.append(
        f"🗓 Контракты дня (бонус +{DAILY_CONTRACT_BONUS_PERCENT}% RU, +{DAILY_CONTRACT_RATING_BONUS} рейтинг, "
        "1 раз за каждый контракт):"
    )
    for key in daily_keys:
        template = QUEST_CONTRACTS.get(key)
        if template:
            claimed = _contract_daily_bonus_claimed(storage, tid, daily_date, key)
            mark = " ✅" if claimed else ""
            lines.append(f"  • {template.title} («{template.work_location}»){mark}")
    if weekly_key:
        weekly_template = QUEST_CONTRACTS.get(weekly_key)
        if weekly_template:
            claimed = _contract_weekly_bonus_claimed(storage, tid, weekly_week, weekly_key)
            lines.append(
                f"📅 Контракт недели (бонус +{WEEKLY_CONTRACT_BONUS_PERCENT}% RU, "
                f"+{WEEKLY_CONTRACT_RATING_BONUS} рейтинг, 1 раз за задание"
                f"{' — уже получен' if claimed else ''}): "
                f"{weekly_template.title} («{weekly_template.work_location}»)"
            )
    lines.append("")

    lines.append("Доступные контракты:")
    for template in list_quest_contracts_for_character(character):
        quest = QUESTS.get(template.difficulty)
        if quest is None:
            continue
        rating_gain = QUEST_RATING_BY_DIFFICULTY.get(template.difficulty, (12, 2))[0]
        transport_note = _transport_requirement_text(template.min_transport)
        badge = ""
        if template.key == weekly_key:
            badge = " 📅"
        elif template.key in daily_keys:
            badge = " 🗓"
        lines.append(
            f"• {template.title}{badge}{transport_note}\n"
            f"  {quest.title} → «{template.work_location}» | поле 6×6 | "
        f"Базовая награда за успех миссии (уже на поле): RU {quest.reward_min}–{quest.reward_max} | рейтинг +{rating_gain}"
        )
    lines.extend(["", "🚚 Контрабанда — отдельная активность."])
    return "\n".join(lines)


def _location_contract_ru_mult(storage: Storage, location_name: str, faction: str | None) -> float:
    location = storage.get_location(location_name)
    if location is None:
        return 1.0
    mult = LOCATION_TYPE_RU_MULT.get(str(location.get("point_type") or ""), 1.0)
    if faction and str(location.get("controlled_by") or "") == faction:
        mult *= CONTROLLED_LOCATION_RU_BONUS
    return mult


def accept_quest_contract(
    storage: Storage, telegram_id: int, contract_key: str, *, vendor: str | None = None
) -> ActionResult:
    character = storage.get_character(telegram_id)
    if character is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(character):
        return ActionResult(False, _dead_block_text())
    if character.faction is None:
        return ActionResult(False, "Сначала выбери группировку.")
    if is_traveling(character):
        return ActionResult(False, travel_block_text(character) or "Ты в пути.")
    if storage.get_active_contract(telegram_id):
        return ActionResult(False, "Сначала заверши или отмени текущий контракт.")
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked

    template = QUEST_CONTRACTS.get(contract_key)
    if template is None:
        return ActionResult(False, "Такого контракта нет.")

    home = faction_home_base(character.faction)
    if template.work_location == home:
        return ActionResult(False, "Этот контракт недоступен: точка работы совпадает с твоей базой.")
    if get_active_smuggling(storage, telegram_id):
        return ActionResult(False, "Сначала заверши или брось рейс контрабанды.")
    if character.location != home:
        return ActionResult(
            False,
            f"Контракты выдают только на домашней базе «{home}». Сейчас ты в «{character.location}».",
        )
    if not _has_transport(character, template.min_transport):
        need = _transport_requirement_text(template.min_transport).strip(" ()")
        return ActionResult(False, f"Для этого контракта {need or 'нужен транспорт'}.")
    if template.min_gear_power > 0 and equipment_power(character) < template.min_gear_power:
        return ActionResult(
            False,
            f"Нужна сила снаряги ≥ {template.min_gear_power} (сейчас {equipment_power(character)}).",
        )

    payload: dict[str, Any] = {"template_key": template.key, "stage": "work", "pending_reward": 0}
    if vendor in {"barkeep", "medic", "tech"}:
        payload["vendor"] = vendor
    storage.set_active_contract(telegram_id, payload)
    from app.vendors import vendor_person_name, VENDOR_REP_BY_DIFFICULTY

    giver = ""
    if vendor:
        person = vendor_person_name(character.faction, vendor)
        rep = int(VENDOR_REP_BY_DIFFICULTY.get(template.difficulty, 0))
        giver = f"Заказчик: {person}. За успех: +{rep} авторитета.\n"
    return ActionResult(
        True,
        f"Контракт принят: «{template.title}».\n"
        f"{giver}"
        f"Доберись до «{template.work_location}» и нажми «Выполнить задание» на точке.\n"
        f"Патроны, аптечки и энергия спишутся при старте вылазки, не при принятии.",
    )


def _apply_money_penalty(storage: Storage, telegram_id: int, penalty: int) -> int:
    """Списать штраф в RU, не давая уйти от него из-за нехватки денег: минимум — весь баланс."""
    if penalty <= 0:
        return 0
    return storage.drain_money(telegram_id, penalty)


def admin_delete_player_account(storage: Storage, telegram_id: int) -> ActionResult:
    """Полностью удалить аккаунт игрока (админ): сброс сессий, отмена лобби, удаление из БД."""
    from app.player_busy import recover_stuck_player

    tid = int(telegram_id)
    player = storage.get_character(tid, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Персонаж не найден.")
    nickname = player.nickname
    faction = player.faction

    recover_stuck_player(storage, tid, force_clear=True)
    player = storage.get_character(tid, refresh_energy=False)
    if player is not None and is_traveling(player):
        storage.clear_travel(tid)

    raids_cancelled = len(storage.cancel_all_open_raids_led_by(tid))
    lobbies_cancelled = storage.cancel_all_open_war_lobbies_led_by(tid)

    deleted = storage.delete_character_account(tid)
    if deleted is None:
        return ActionResult(False, "Не удалось удалить персонажа.")

    parts = [
        f"Аккаунт «{nickname}» (id {tid}) удалён.",
        "Игрок может заново пройти /start.",
    ]
    if faction:
        parts.append(f"Группировка была: {faction}.")
    if raids_cancelled or lobbies_cancelled:
        parts.append(
            f"Отменено: рейдов {raids_cancelled}, военных лобби {lobbies_cancelled}."
        )
    return ActionResult(True, "\n".join(parts))


VALID_ADMIN_FACTIONS = ("Долг", "Свобода", "Нейтралы", "Бандиты", "Монолит")


def admin_set_player_faction(
    storage: Storage,
    *,
    target: str,
    faction: str,
) -> ActionResult:
    """Админ: перевести игрока в другую группировку по id или прозвищу."""
    from app.player_busy import recover_stuck_player

    faction_name = (faction or "").strip()
    if faction_name not in VALID_ADMIN_FACTIONS:
        return ActionResult(
            False,
            "Неизвестная группировка. Доступно: " + ", ".join(VALID_ADMIN_FACTIONS),
        )

    raw = (target or "").strip()
    if not raw:
        return ActionResult(False, "Укажи telegram_id или прозвище.")

    if raw.isdigit():
        telegram_id = int(raw)
    else:
        found = storage.find_telegram_id_by_nickname(raw)
        if found is None:
            return ActionResult(False, f"Игрок «{h(raw)}» не найден.")
        telegram_id = found

    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Персонаж не найден.")

    old_faction = player.faction
    if old_faction == faction_name:
        return ActionResult(
            True,
            f"«{player.nickname}» (id {telegram_id}) уже в группировке «{faction_name}».",
        )

    recover_stuck_player(storage, telegram_id, force_clear=True)
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is not None and is_traveling(player):
        storage.clear_travel(telegram_id)

    raids_cancelled = len(storage.cancel_all_open_raids_led_by(telegram_id))
    lobbies_cancelled = storage.cancel_all_open_war_lobbies_led_by(telegram_id)
    storage.set_faction(telegram_id, faction_name)
    updated = storage.get_character(telegram_id, refresh_energy=False)
    home = updated.location if updated is not None else faction_home_base(faction_name)
    rank = updated.faction_rank if updated is not None else None

    parts = [
        f"«{player.nickname}» (id {telegram_id}): {old_faction or 'без ГП'} → «{faction_name}».",
        f"Локация сброшена на базу: {home}.",
    ]
    if rank:
        parts.append(f"Звание: {rank}.")
    if raids_cancelled or lobbies_cancelled:
        parts.append(
            f"Отменено: рейдов {raids_cancelled}, военных лобби {lobbies_cancelled}."
        )
    return ActionResult(True, "\n".join(parts))


def apply_pending_admin_faction_transfers(storage: Storage) -> list[str]:
    """Одноразовые админ-переводы ГП при старте бота (meta-флаг, без повторов)."""
    pending: tuple[tuple[str, str, str], ...] = ()
    notes: list[str] = []
    for meta_key, nickname, faction in pending:
        if storage.get_meta(meta_key):
            continue
        result = admin_set_player_faction(storage, target=nickname, faction=faction)
        storage.set_meta(meta_key, "1" if result.ok else f"fail:{result.text[:120]}")
        notes.append(result.text)
    return notes


def cancel_quest_contract(storage: Storage, telegram_id: int) -> ActionResult:
    character = storage.get_character(telegram_id, refresh_energy=False)
    if character is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if not storage.get_active_contract(telegram_id):
        return ActionResult(False, "Нет активного контракта.")
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    from app.quest_mission import clear_mission_session

    clear_mission_session(storage, telegram_id)
    home = faction_home_base(character.faction)
    left_base = is_traveling(character) or character.location != home
    storage.set_active_contract(telegram_id, None)
    if not left_base:
        return ActionResult(True, "Контракт отменён.")
    taken = _apply_money_penalty(storage, telegram_id, CONTRACT_CANCEL_PENALTY_RU)
    _add_rating(storage, telegram_id, -CONTRACT_CANCEL_RATING_PENALTY)
    return ActionResult(
        True,
        f"Контракт отменён после выезда с базы.\n"
        f"Штраф: −{taken} RU, −{CONTRACT_CANCEL_RATING_PENALTY} рейтинга.",
    )


def _spend_quest_resources(
    storage: Storage,
    telegram_id: int,
    quest: QuestType,
) -> ActionResult | None:
    """Списать энергию за миссию. Патроны и аптечки больше не требуются/не списываются."""
    if not storage.spend_energy(telegram_id, quest.energy_cost):
        return ActionResult(
            False,
            f"Не хватает энергии. Нужно {quest.energy_cost} ед.",
        )
    return None


def apply_contract_mission_success(
    storage: Storage,
    telegram_id: int,
    *,
    quest: QuestType,
    work_location: str,
    title: str,
) -> ActionResult:
    """Награда за успешную grid-миссию (без RNG — поле и есть испытание)."""
    updated = storage.get_character(telegram_id, refresh_energy=False)
    if updated is None:
        return ActionResult(False, "Персонаж не найден.")

    durability_text = _apply_durability_decay(storage, telegram_id, weapon_loss=3, armor_loss=2)
    wear_notes = ""
    try:
        from app.artifact_features import apply_quest_artifact_wear

        wear_lines = apply_quest_artifact_wear(storage, telegram_id)
        if wear_lines:
            wear_notes = "\n" + "\n".join(wear_lines)
    except Exception:
        logger.exception("Artifact wear after contract failed for %s", telegram_id)
    rating_success, _rating_fail = QUEST_RATING_BY_DIFFICULTY.get(
        quest.key,
        (RATING_REWARD["quest_success"], RATING_REWARD["quest_fail"]),
    )
    base_reward = random.randint(quest.reward_min, quest.reward_max)
    ru_mult = _location_contract_ru_mult(storage, work_location, updated.faction)
    transport_note = ""
    arrived_transport = storage.get_last_arrival_transport(telegram_id) or "foot"
    transport_mult = 1.0
    if arrived_transport == "bicycle" and updated.bicycle_owned:
        transport_mult = TRANSPORT_QUEST_REWARD_MULT["bicycle"]
    elif arrived_transport == "niva" and updated.niva_owned:
        transport_mult = TRANSPORT_QUEST_REWARD_MULT["niva"]
    elif arrived_transport == "truck" and updated.truck_owned:
        transport_mult = TRANSPORT_QUEST_REWARD_MULT["truck"]
    if transport_mult > 1.0:
        ru_mult *= transport_mult
        label = TRANSPORT_QUEST_REWARD_LABELS.get(arrived_transport, arrived_transport)
        transport_note = f" ×{transport_mult:g} {label}"
        storage.consume_last_arrival_transport(telegram_id)
    reward = max(1, int(round(base_reward * ru_mult)))
    storage.change_money(telegram_id, reward)
    _add_rating(storage, telegram_id, rating_success)
    storage.add_player_stat(telegram_id, "quests_completed", 1)
    storage.add_player_stat(telegram_id, "money_earned", reward)

    updated = storage.get_character(telegram_id, refresh_energy=False)
    detector_key = None
    if updated is not None:
        for key, _, _ in ARTIFACT_DETECTORS:
            if int(updated.inventory.get(key, 0)) > 0:
                detector_key = key
                break
    art_key = roll_location_artifact_drop(
        work_location,
        best_detector_base_chance(updated) if updated else 12,
        storage,
        detector_key=detector_key,
    )
    if art_key is not None:
        storage.add_item(telegram_id, art_key, 1)
        on_artifact_found(
            storage,
            telegram_id,
            art_key,
            location=work_location,
            source="quest",
        )
        extra = f"\nНаходка на «{work_location}»: {ITEM_LABELS.get(art_key, art_key)}!"
    else:
        extra = ""
    stash_text = _maybe_drop_stash(storage, telegram_id)
    achievements_text = _progress_and_unlock_achievements(storage, telegram_id)
    try:
        from app.player_medals import sync_player_medals

        sync_player_medals(storage, telegram_id)
    except Exception:
        logger.exception("Failed to sync player medals after contract completion for %s", telegram_id)
    rep_note = ""
    active = storage.get_active_contract(telegram_id) or {}
    vendor = str(active.get("vendor") or "")
    if vendor in {"barkeep", "medic", "tech"}:
        from app.vendors import (
            VENDOR_REP_BY_DIFFICULTY,
            VENDOR_TIER_MAX,
            add_vendor_reputation,
            get_vendor_tier,
            reputation_progress_label,
            vendor_person_name,
        )

        gained = int(VENDOR_REP_BY_DIFFICULTY.get(quest.key, 0))
        if gained > 0:
            total = add_vendor_reputation(storage, telegram_id, vendor, gained)
            person = vendor_person_name(updated.faction, vendor)
            tier_now = get_vendor_tier(storage, telegram_id, vendor)
            rep_note = (
                f"\nАвторитет «{person}»: +{gained} "
                f"({reputation_progress_label(total)}, этап {tier_now}/{VENDOR_TIER_MAX})."
            )
    mult_note = ""
    if ru_mult > 1.0:
        loc_part = ""
        loc_only = _location_contract_ru_mult(storage, work_location, updated.faction)
        if loc_only > 1.0:
            loc_part = f"локация ×{loc_only:.2f}"
        parts = [p for p in (loc_part, transport_note.strip()) if p]
        mult_note = f" ({', '.join(parts)})" if parts else ""
    return ActionResult(
        True,
        f"«{title}» выполнено на «{work_location}»!\n"
        f"Награда: {reward} RU{mult_note}, рейтинг +{rating_success}."
        f"{extra}{stash_text}{durability_text}{wear_notes}{achievements_text}{rep_note}{_maybe_help_event_reward(storage, telegram_id, title)}",
        payload={"reward": reward},
    )


def _maybe_help_event_reward(storage: Storage, telegram_id: int, title: str) -> str:
    notes: list[str] = []
    if str(title).startswith("Помощь:"):
        try:
            from app.mini_events import complete_help_event_if_helper

            extra = complete_help_event_if_helper(storage, telegram_id)
            if extra:
                achievements_text = _progress_and_unlock_achievements(storage, telegram_id)
                notes.append(f"{extra}{achievements_text}")
        except Exception:
            pass
    try:
        from app.special_events import complete_special_event_objective

        special = complete_special_event_objective(storage, telegram_id, title=title)
        if special:
            notes.append(special)
    except Exception:
        pass
    if not notes:
        return ""
    return "\n" + "\n".join(notes)


def apply_contract_mission_fail(
    storage: Storage,
    telegram_id: int,
    *,
    quest: QuestType,
    work_location: str,
    title: str,
    reason: str = "",
) -> ActionResult:
    durability_text = _apply_durability_decay(storage, telegram_id, weapon_loss=2, armor_loss=1)
    _rating_success, rating_fail = QUEST_RATING_BY_DIFFICULTY.get(
        quest.key,
        (RATING_REWARD["quest_success"], RATING_REWARD["quest_fail"]),
    )
    min_penalty, max_penalty = QUEST_FAIL_PENALTY_RANGE.get(quest.key, (50, 120))
    penalty = random.randint(min_penalty, max_penalty)
    taken = _apply_money_penalty(storage, telegram_id, penalty)
    _add_rating(storage, telegram_id, -rating_fail)
    storage.add_player_stat(telegram_id, "quests_failed", 1)
    note = f"\n{reason}" if reason else ""
    return ActionResult(
        False,
        f"Провал «{title}» на «{work_location}».{note}\n"
        f"Потери: {taken} RU, рейтинг −{rating_fail}.{durability_text}",
    )


def run_contract_work(storage: Storage, telegram_id: int) -> ActionResult:
    character = storage.get_character(telegram_id)
    if character is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(character):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id, skip="quest")
    if blocked is not None:
        return blocked
    blocked = travel_block_text(character)
    if blocked:
        return ActionResult(False, blocked)

    active = storage.get_active_contract(telegram_id)
    if not active or str(active.get("stage", "")) != "work":
        return ActionResult(False, "Сейчас нечего выполнять на месте.")

    template = QUEST_CONTRACTS.get(str(active.get("template_key", "")))
    if template is None:
        storage.set_active_contract(telegram_id, None)
        return ActionResult(False, "Контракт повреждён — выбери новый.")

    if character.location != template.work_location:
        return ActionResult(
            False,
            f"Работа выполняется на «{template.work_location}». Ты сейчас: «{character.location}».",
        )

    quest = QUESTS.get(template.difficulty)
    if quest is None:
        return ActionResult(False, "Неизвестная сложность контракта.")

    if template.mission_kind == "deliver_junk":
        return _complete_deliver_junk_contract(storage, telegram_id, template, quest)

    # Grid-миссии (поиск / разведка / зачистка).
    from app.quest_mission import start_or_resume_quest_mission

    return start_or_resume_quest_mission(storage, telegram_id, template, quest)


def _complete_deliver_junk_contract(
    storage: Storage,
    telegram_id: int,
    template: QuestContractTemplate,
    quest: QuestType,
) -> ActionResult:
    """Сдать мусорные артефакты без grid-миссии."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Персонаж не найден.")
    missing: list[str] = []
    for item_key, qty in template.deliver_items:
        have = int(player.inventory.get(item_key, 0))
        if have < qty:
            missing.append(f"{ITEM_LABELS.get(item_key, item_key)} ×{qty - have}")
    if missing:
        return ActionResult(
            False,
            "Не хватает мусора для сдачи:\n" + "\n".join(f"• {line}" for line in missing),
        )
    spend_err = _spend_quest_resources(storage, telegram_id, quest)
    if spend_err is not None:
        return spend_err
    for item_key, qty in template.deliver_items:
        if not storage.remove_item(telegram_id, item_key, qty):
            return ActionResult(False, "Ошибка списания мусора.")
    result = apply_contract_mission_success(
        storage,
        telegram_id,
        quest=quest,
        work_location=template.work_location,
        title=template.title,
    )
    if template.return_home:
        storage.set_active_contract(
            telegram_id,
            {
                "template_key": template.key,
                "stage": "return",
                "pending_reward": int((result.payload or {}).get("reward", 0)),
            },
        )
        home = faction_home_base(player.faction)
        auto = try_auto_turn_in_contract(storage, telegram_id)
        if auto:
            return ActionResult(
                True,
                result.text + "\n\n" + auto,
                payload={"mission_done": True, "turned_in": True},
            )
        return ActionResult(
            True,
            result.text
            + f"\n\nВернись на «{home}» — отчёт сдастся автоматически при прибытии "
            + f"(+{CONTRACT_TURN_IN_BONUS_PERCENT}% RU).",
            payload={"mission_done": True},
        )
    storage.set_active_contract(telegram_id, None)
    return result


def turn_in_quest_contract(storage: Storage, telegram_id: int) -> ActionResult:
    character = storage.get_character(telegram_id)
    if character is None:
        return ActionResult(False, "Сначала создай персонажа.")
    blocked = travel_block_text(character)
    if blocked:
        return ActionResult(False, blocked)

    active = storage.get_active_contract(telegram_id)
    if not active or str(active.get("stage", "")) != "return":
        return ActionResult(False, "Нечего сдавать — сначала выполни работу на точке.")

    template = QUEST_CONTRACTS.get(str(active.get("template_key", "")))
    if template is None:
        storage.set_active_contract(telegram_id, None)
        return ActionResult(False, "Контракт не найден.")

    home = faction_home_base(character.faction)
    if character.location != home:
        return ActionResult(
            False,
            f"Сдать отчёт можно только на базе «{home}». Сейчас ты в «{character.location}».",
        )

    pending = int(active.get("pending_reward", 0) or 0)
    bonus = max(0, int(round(pending * CONTRACT_TURN_IN_BONUS_PERCENT / 100)))
    now = datetime.now(timezone.utc)
    extra_lines = _grant_contract_rotation_bonus(
        storage,
        telegram_id,
        template_key=template.key,
        pending=pending,
        now=now,
    )
    if bonus > 0:
        storage.change_money(telegram_id, bonus)
        storage.add_player_stat(telegram_id, "money_earned", bonus)
    storage.set_active_contract(telegram_id, None)

    text = (
        f"Отчёт по «{template.title}» сдан на «{home}».\n"
        f"Бонус за доставку данных: +{bonus} RU.\n"
        "Контракт закрыт."
    )
    if extra_lines:
        text += "\n\n" + "\n".join(extra_lines)
    return ActionResult(True, text)


def try_auto_turn_in_contract(storage: Storage, telegram_id: int) -> str | None:
    """Автосдача отчёта, если этап return и игрок уже на домашней базе."""
    # Подтянуть просроченный переход, чтобы локация была актуальной.
    storage.resolve_travel_if_due(telegram_id)
    character = storage.get_character(telegram_id, refresh_energy=False)
    if character is None or is_traveling(character):
        return None
    active = storage.get_active_contract(telegram_id)
    if not active or str(active.get("stage", "")) != "return":
        return None
    home = faction_home_base(character.faction)
    if character.location != home:
        return None
    result = turn_in_quest_contract(storage, telegram_id)
    return result.text if result.ok else None


def use_energy_drink(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    if not storage.remove_item(telegram_id, "energy_drink", 1):
        return ActionResult(False, "У тебя нет энергетика в инвентаре.")
    storage.restore_energy(telegram_id, 35)
    return ActionResult(True, "Ты выпил энергетик и восстановил 35 энергии.")


def use_nitrous_oxide(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if is_traveling(player):
        return apply_n2o_to_active_travel(storage, telegram_id)
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    if has_n2o_travel_boost(storage, telegram_id):
        return ActionResult(False, "Закись азота уже активирована — сначала соверши переход.")
    if not storage.remove_item(telegram_id, "nitrous_oxide", 1):
        return ActionResult(False, "У тебя нет закиси азота (N2O) в инвентаре.")
    storage.set_meta(_n2o_travel_meta_key(telegram_id), "1")
    return ActionResult(
        True,
        "Закись азота (N2O) активирована.\n"
        "Следующий переход по карте займёт в 2 раза меньше времени.",
    )


def use_medkit_item(
    storage: Storage, telegram_id: int, item_key: str = "medkit", *, skip_busy: str | None = None
) -> ActionResult:
    effect = MEDKIT_EFFECTS.get(item_key)
    if effect is None:
        return ActionResult(False, "Неизвестный тип аптечки.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id, skip=skip_busy)
    if blocked is not None:
        return blocked
    label = ITEM_LABELS.get(item_key, item_key)
    max_hp = effective_max_health(player)
    heal_cap = int(effect["heal"])
    rad_delta = int(effect.get("radiation", 0))
    needs_heal = player.health < max_hp
    needs_rad = rad_delta < 0 and player.radiation > 0
    if not needs_heal and not needs_rad:
        if rad_delta < 0:
            return ActionResult(False, "Здоровье полное и радиации нет — аптечка не нужна.")
        return ActionResult(False, "Здоровье уже полное, аптечка не требуется.")
    if not storage.remove_item(telegram_id, item_key, 1):
        return ActionResult(False, f"У тебя нет предмета: {label}.")
    heal_amount = min(heal_cap, max_hp - player.health) if needs_heal else 0
    if heal_amount > 0:
        storage.change_health(telegram_id, heal_amount, max_health=max_hp)
    if rad_delta < 0:
        storage.adjust_survival(telegram_id, radiation_delta=rad_delta)
    parts: list[str] = []
    if heal_amount > 0:
        parts.append(f"+{heal_amount} HP")
    if rad_delta < 0:
        parts.append(f"{rad_delta} рад")
    return ActionResult(True, f"Ты использовал {label}: {', '.join(parts)}.")


def use_medkit(storage: Storage, telegram_id: int) -> ActionResult:
    return use_medkit_item(storage, telegram_id, "medkit")


def use_medkit_army(storage: Storage, telegram_id: int) -> ActionResult:
    return use_medkit_item(storage, telegram_id, "medkit_army")


def use_medkit_science(storage: Storage, telegram_id: int) -> ActionResult:
    return use_medkit_item(storage, telegram_id, "medkit_science")


def _apply_active_survival(storage: Storage, telegram_id: int) -> str:
    radiation_gain = random.randint(SURVIVAL_ACTIVE_RADIATION_MIN, SURVIVAL_ACTIVE_RADIATION_MAX)
    hp_loss = random.randint(SURVIVAL_ACTIVE_HP_DRAIN_MIN, SURVIVAL_ACTIVE_HP_DRAIN_MAX)
    storage.adjust_survival(
        telegram_id,
        radiation_delta=radiation_gain,
        hunger_delta=SURVIVAL_ACTIVE_HUNGER_INC,
        thirst_delta=SURVIVAL_ACTIVE_THIRST_INC,
        health_delta=-hp_loss,
    )
    return f"\nВыживание: +{radiation_gain} рад., +1 голод, +1 жажда, -{hp_loss} HP."


def _consume_survival_item(
    storage: Storage,
    telegram_id: int,
    item_key: str,
    *,
    radiation_delta: int = 0,
    hunger_delta: int = 0,
    thirst_delta: int = 0,
    text: str,
) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    if not storage.remove_item(telegram_id, item_key, 1):
        return ActionResult(False, f"У тебя нет предмета: {ITEM_LABELS.get(item_key, item_key)}.")
    storage.adjust_survival(
        telegram_id,
        radiation_delta=radiation_delta,
        hunger_delta=hunger_delta,
        thirst_delta=thirst_delta,
    )
    return ActionResult(True, text)


def use_vodka(storage: Storage, telegram_id: int) -> ActionResult:
    return _consume_survival_item(
        storage,
        telegram_id,
        "vodka",
        radiation_delta=-20,
        text="Ты выпил водку. Радиация снижена на 20.",
    )


def use_antirad(storage: Storage, telegram_id: int) -> ActionResult:
    return _consume_survival_item(
        storage,
        telegram_id,
        "antirad",
        radiation_delta=-50,
        text="Ты использовал антирад. Радиация снижена на 50.",
    )


def _emission_protect_meta_key(telegram_id: int) -> str:
    return f"{EMISSION_PROTECT_META_PREFIX}{telegram_id}"


def has_emission_protection(
    storage: Storage,
    telegram_id: int,
    now: datetime | None = None,
) -> bool:
    raw = storage.get_meta(_emission_protect_meta_key(telegram_id))
    if not raw:
        return False
    check_at = now or datetime.now(timezone.utc)
    until = _parse_meta_datetime(raw, check_at)
    if check_at >= until:
        storage.delete_meta(_emission_protect_meta_key(telegram_id))
        return False
    return True


def _compute_emission_protect_until(storage: Storage, now: datetime) -> datetime:
    """Защита на один полный Выброс (все 3 волны): текущий или ближайший."""
    buffer = timedelta(minutes=EMISSION_PROTECT_BUFFER_MINUTES)
    waves_span = timedelta(minutes=EMISSION_WAVE_GAP_MINUTES * 2)
    phase = storage.get_meta(EMISSION_META_PHASE) or "calm"
    if phase in ("wave1", "wave2", "wave3"):
        raw_wave_at = storage.get_meta(EMISSION_META_WAVE_AT)
        wave_at = _parse_meta_datetime(raw_wave_at, now)
        # wave_at — момент текущей/следующей волны в активной фазе.
        # Нужно перекрыть все оставшиеся волны до конца Выброса.
        if phase == "wave1":
            # wave1 → +gap wave2 → +gap wave3
            end = wave_at + waves_span
        elif phase == "wave2":
            # wave2 → +gap wave3
            end = wave_at + timedelta(minutes=EMISSION_WAVE_GAP_MINUTES)
        else:
            # Последняя волна: держим до её момента (+ буфер ниже).
            end = wave_at
        if end < now:
            end = now
        return end + buffer
    raw_at = storage.get_meta(EMISSION_META_AT)
    emission_at = _parse_meta_datetime(raw_at, now + timedelta(hours=EMISSION_INTERVAL_HOURS))
    if emission_at < now:
        emission_at = now
    return emission_at + waves_span + buffer


def use_antibiotic(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    now = datetime.now(timezone.utc)
    if has_emission_protection(storage, telegram_id, now):
        until_raw = storage.get_meta(_emission_protect_meta_key(telegram_id))
        until = _parse_meta_datetime(until_raw, now)
        return ActionResult(
            False,
            f"Защита от Выброса уже активна до {until.strftime('%d.%m %H:%M')} UTC.",
        )
    if not storage.remove_item(telegram_id, "antibiotic", 1):
        return ActionResult(False, "У тебя нет предмета: Антибиотик.")
    until = _compute_emission_protect_until(storage, now)
    storage.set_meta(_emission_protect_meta_key(telegram_id), until.isoformat())
    return ActionResult(
        True,
        f"Ты принял антибиотик. Полная защита от Выброса до {until.strftime('%d.%m %H:%M')} UTC "
        "(все 3 волны текущего или ближайшего Выброса).",
    )


def use_bread(storage: Storage, telegram_id: int) -> ActionResult:
    return _consume_survival_item(
        storage,
        telegram_id,
        "bread",
        hunger_delta=-10,
        text="Ты съел хлеб. Голод снижен на 10.",
    )


def use_sausage(storage: Storage, telegram_id: int) -> ActionResult:
    return _consume_survival_item(
        storage,
        telegram_id,
        "sausage",
        hunger_delta=-20,
        text="Ты съел колбасу. Голод снижен на 20.",
    )


def use_stew(storage: Storage, telegram_id: int) -> ActionResult:
    return _consume_survival_item(
        storage,
        telegram_id,
        "stew",
        hunger_delta=-50,
        text="Ты съел тушёнку. Голод снижен на 50.",
    )


def use_water(storage: Storage, telegram_id: int) -> ActionResult:
    return _consume_survival_item(
        storage,
        telegram_id,
        "water_bottle",
        thirst_delta=-10,
        text="Ты выпил воду. Жажда снижена на 10.",
    )


def use_mineralka(storage: Storage, telegram_id: int) -> ActionResult:
    return _consume_survival_item(
        storage,
        telegram_id,
        "mineral_water",
        thirst_delta=-20,
        text="Ты выпил минералку. Жажда снижена на 20.",
    )


def use_beard_tea(storage: Storage, telegram_id: int) -> ActionResult:
    return _consume_survival_item(
        storage,
        telegram_id,
        "beard_tea",
        thirst_delta=-50,
        text="Ты выпил чай Бороды. Жажда снижена на 50.",
    )


def _maybe_drop_stash(storage: Storage, telegram_id: int) -> str:
    """5% шанс найти тайник после успешной активности."""
    if random.randint(1, 100) > STASH_ACTIVITY_DROP_CHANCE:
        return ""
    storage.add_item(telegram_id, STASH_ITEM_KEY, 1)
    return f"\nНаходка: {ITEM_LABELS[STASH_ITEM_KEY]}!"


def _roll_stash_gear_tier() -> int | None:
    """Взаимоисключающий ролл тира снаряги. Без дропа — None."""
    roll = random.random() * 100.0
    cumulative = 0.0
    # Редкость сверху вниз (T5 → T1-2), шансы из STASH_GEAR_TIER_CHANCES.
    for tier_spec, chance in reversed(STASH_GEAR_TIER_CHANCES):
        cumulative += float(chance)
        if roll < cumulative:
            if isinstance(tier_spec, tuple):
                return int(random.choice(list(tier_spec)))
            return int(tier_spec)
    return None


def _roll_stash_loot(storage: Storage, telegram_id: int) -> list[str]:
    """Лут из тайника: расходники + броня ИЛИ оружие (не вместе)."""
    drops: list[str] = []

    for item_key in STASH_CONSUMABLE_KEYS:
        chance = STASH_CONSUMABLE_DROP_CHANCE_BY_KEY.get(item_key, STASH_CONSUMABLE_DROP_CHANCE)
        if random.randint(1, 100) > chance:
            continue
        amount = random.randint(1, 2)
        storage.add_item(telegram_id, item_key, amount)
        drops.append(f"{ITEM_LABELS.get(item_key, item_key)} x{amount}")

    # Гарантия хотя бы одного расходника, если ничего не выпало.
    if not drops:
        # Не гарантируем редкие аптечки — только обычный пул.
        common_keys = [
            key for key in STASH_CONSUMABLE_KEYS if key not in STASH_CONSUMABLE_DROP_CHANCE_BY_KEY
        ]
        item_key = random.choice(common_keys or list(STASH_CONSUMABLE_KEYS))
        amount = random.randint(1, 2)
        storage.add_item(telegram_id, item_key, amount)
        drops.append(f"{ITEM_LABELS.get(item_key, item_key)} x{amount}")

    tier = _roll_stash_gear_tier()
    if tier is not None:
        # Броня или оружие — взаимоисключающе.
        pool_kind = random.choice(("armor", "weapon"))
        pool = STASH_ARMOR_BY_TIER if pool_kind == "armor" else STASH_WEAPON_BY_TIER
        candidates = pool.get(tier, ())
        if candidates:
            gear_key = random.choice(candidates)
            storage.add_item(telegram_id, gear_key, 1)
            kind_label = "броня" if pool_kind == "armor" else "оружие"
            drops.append(f"{ITEM_LABELS.get(gear_key, gear_key)} (тир {tier}, {kind_label})")

    return drops


def open_stash(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if not storage.remove_item(telegram_id, STASH_ITEM_KEY, 1):
        return ActionResult(False, "У тебя нет тайника в инвентаре.")

    loot_lines = _roll_stash_loot(storage, telegram_id)
    loot_text = "\n".join(f"• {line}" for line in loot_lines)
    return ActionResult(
        True,
        f"Ты открыл тайник и нашёл:\n{loot_text}",
    )


def transfer_money_with_fee(storage: Storage, sender_id: int, target_id: int, amount: int) -> ActionResult:
    if amount <= 0:
        return ActionResult(False, "Сумма перевода должна быть положительной.")
    if sender_id == target_id:
        return ActionResult(False, "Нельзя переводить деньги самому себе.")
    sender = storage.get_character(sender_id, refresh_energy=False)
    target = storage.get_character(target_id, refresh_energy=False)
    if sender is None or target is None:
        return ActionResult(False, "Один из игроков не найден.")
    if _is_dead(sender):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, sender_id)
    if blocked is not None:
        return blocked
    fee = int(round(amount * (TRANSFER_FEE_PERCENT / 100)))
    total = amount + fee
    if not storage.change_money(sender_id, -total):
        return ActionResult(False, f"Недостаточно денег. Нужно {total} RU (включая комиссию {fee} RU).")
    if not storage.change_money(target_id, amount):
        storage.change_money(sender_id, total)
        return ActionResult(False, "Не удалось зачислить перевод получателю — деньги возвращены.")
    return ActionResult(
        True,
        f"Перевод выполнен: {amount} RU игроку {h(target.nickname)}.\nКомиссия: {fee} RU.\nСписано: {total} RU.",
        payload={
            "notify": [
                (
                    target_id,
                    f"💰 {h(sender.nickname)} перевёл(а) тебе {amount} RU.",
                ),
            ],
        },
    )


def _duel_in_key(target_id: int) -> str:
    return f"{DUEL_META_IN_PREFIX}{int(target_id)}"


def _duel_out_key(challenger_id: int) -> str:
    return f"{DUEL_META_OUT_PREFIX}{int(challenger_id)}"


def _clear_duel_meta(storage: Storage, challenger_id: int, target_id: int) -> None:
    storage.delete_meta(_duel_in_key(target_id))
    storage.delete_meta(_duel_out_key(challenger_id))


def _parse_pending_duel_payload(raw: str | None) -> tuple[int, datetime] | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        challenger_id = int(payload["challenger_id"])
        created_at = datetime.fromisoformat(str(payload["created_at"]))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return challenger_id, created_at
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None


def get_pending_duel_challenger(storage: Storage, target_id: int) -> int | None:
    parsed = _parse_pending_duel_payload(storage.get_meta(_duel_in_key(target_id)))
    if parsed is None:
        return None
    challenger_id, created_at = parsed
    age = (datetime.now(timezone.utc) - created_at).total_seconds()
    if age > DUEL_PENDING_TTL_SECONDS:
        storage.delete_meta(_duel_in_key(target_id))
        storage.delete_meta(_duel_out_key(challenger_id))
        return None
    return challenger_id


def _duel_conditions_text() -> str:
    return (
        f"Тактическая дуэль 8×8: ход, выстрел (90°), 1 аптечка, укрытия 50% промах.\n"
        f"Дальность: пистолет/дробовик 1 · автомат 2 · снайперка 3 · гаус 4 клетки.\n"
        f"Таймер боя 3 мин — потом бесконечная волна мутантов (2 клетки/ход).\n"
        f"Стоимость при согласии: {DUEL_ENERGY_COST} энергии у каждого.\n"
        f"Проигравший: HP до {DUEL_LOSER_HP_REMAINING}, "
        f"−{DUEL_LOSER_MONEY_PERCENT}% денег (макс. {DUEL_LOSER_MONEY_CAP} RU), −{RATING_REWARD['duel_lose']} рейтинга.\n"
        f"Победитель: ранение −{DUEL_WINNER_WOUND_MIN}…−{DUEL_WINNER_WOUND_MAX} HP, "
        f"деньги проигравшего, +{RATING_REWARD['duel_win']} рейтинга."
    )


def create_duel_challenge(
    storage: Storage,
    challenger_id: int,
    target_id: int,
) -> tuple[ActionResult, str | None]:
    """Вызов на дуэль. Возвращает (ответ вызывающему, текст для цели или None)."""
    if challenger_id == target_id:
        return ActionResult(False, "Нельзя вызвать на дуэль самого себя."), None
    challenger = storage.get_character(challenger_id, refresh_energy=False)
    target = storage.get_character(target_id, refresh_energy=False)
    if challenger is None:
        return ActionResult(False, "Сначала создай персонажа через /start."), None
    if target is None:
        return ActionResult(False, "Игрок с таким telegram_id не найден."), None
    if _is_dead(challenger):
        return ActionResult(False, _dead_block_text()), None
    if _is_dead(target):
        return ActionResult(False, f"{h(target.nickname)} сейчас мёртв и не может драться."), None
    if challenger.energy < DUEL_ENERGY_COST:
        return (
            ActionResult(False, f"Нужно минимум {DUEL_ENERGY_COST} энергии для дуэли."),
            None,
        )
    if target.energy < DUEL_ENERGY_COST:
        return (
            ActionResult(False, f"У {h(target.nickname)} недостаточно энергии для дуэли."),
            None,
        )

    from app.player_busy import player_busy_reason

    for pid, who in ((challenger_id, "Ты"), (target_id, h(target.nickname))):
        busy = player_busy_reason(storage, pid)
        if busy:
            label = "Ты занят" if pid == challenger_id else f"{who} занят"
            return ActionResult(False, f"{label}: {busy.lower()}"), None

    existing_out = storage.get_meta(_duel_out_key(challenger_id))
    if existing_out:
        try:
            old_target = int(json.loads(existing_out).get("target_id", 0))
        except (TypeError, ValueError, json.JSONDecodeError):
            old_target = 0
        if old_target:
            storage.delete_meta(_duel_in_key(old_target))
        storage.delete_meta(_duel_out_key(challenger_id))

    pending_for_target = get_pending_duel_challenger(storage, target_id)
    if pending_for_target is not None:
        return (
            ActionResult(False, f"У {h(target.nickname)} уже есть активный вызов на дуэль."),
            None,
        )

    now = datetime.now(timezone.utc).isoformat()
    storage.set_meta(
        _duel_in_key(target_id),
        json.dumps({"challenger_id": challenger_id, "created_at": now}, ensure_ascii=False),
    )
    storage.set_meta(
        _duel_out_key(challenger_id),
        json.dumps({"target_id": target_id, "created_at": now}, ensure_ascii=False),
    )

    my_power = equipment_power(challenger)
    their_power = equipment_power(target)
    conditions = _duel_conditions_text()
    challenger_msg = (
        f"Вызов на дуэль отправлен: {h(target.nickname)} ({target_id}).\n"
        f"Сила снаряги: ты {my_power} vs {their_power}.\n"
        f"Бой на тактическом поле — исход решает игра, не RNG.\n"
        f"{conditions}\n"
        f"Ожидание ответа до {DUEL_PENDING_TTL_SECONDS // 60} мин."
    )
    target_msg = (
        f"⚔️ Тебя вызвал на дуэль {h(challenger.nickname)} (id {challenger_id}).\n"
        f"Сила снаряги: {h(challenger.nickname)} {my_power} vs ты {their_power}.\n"
        f"Тактическое поле 8×8 — ход, выстрел, укрытия.\n"
        f"{conditions}"
    )
    return ActionResult(True, challenger_msg), target_msg


def decline_duel(
    storage: Storage,
    target_id: int,
    challenger_id: int,
) -> tuple[ActionResult, str | None]:
    pending = get_pending_duel_challenger(storage, target_id)
    if pending is None or pending != challenger_id:
        return ActionResult(False, "Этот вызов на дуэль уже неактивен."), None
    challenger = storage.get_character(challenger_id, refresh_energy=False)
    target = storage.get_character(target_id, refresh_energy=False)
    _clear_duel_meta(storage, challenger_id, target_id)
    target_name = h(target.nickname) if target else h(str(target_id))
    challenger_name = h(challenger.nickname) if challenger else h(str(challenger_id))
    notify = f"{target_name} отклонил(а) твой вызов на дуэль."
    return ActionResult(True, f"Ты отклонил(а) дуэль с {challenger_name}."), notify


def accept_duel(
    storage: Storage,
    target_id: int,
    challenger_id: int,
) -> tuple[ActionResult, str | None]:
    """Принятие дуэли: тактическое поле, штраф проигравшему."""
    pending = get_pending_duel_challenger(storage, target_id)
    if pending is None or pending != challenger_id:
        return ActionResult(False, "Этот вызов на дуэль уже неактивен."), None

    challenger = storage.get_character(challenger_id, refresh_energy=False)
    target = storage.get_character(target_id, refresh_energy=False)
    if challenger is None or target is None:
        _clear_duel_meta(storage, challenger_id, target_id)
        return ActionResult(False, "Один из бойцов не найден."), None
    if _is_dead(challenger) or _is_dead(target):
        _clear_duel_meta(storage, challenger_id, target_id)
        return ActionResult(False, "Дуэль невозможна: один из бойцов мёртв."), None
    if challenger.energy < DUEL_ENERGY_COST or target.energy < DUEL_ENERGY_COST:
        _clear_duel_meta(storage, challenger_id, target_id)
        return ActionResult(False, f"Не хватает энергии (нужно {DUEL_ENERGY_COST} у каждого)."), None

    from app.player_busy import player_busy_reason

    for pid, who in ((challenger_id, "Вызывающий"), (target_id, "Ты")):
        busy = player_busy_reason(storage, pid, skip="duel")
        if busy:
            _clear_duel_meta(storage, challenger_id, target_id)
            prefix = busy if pid == target_id else f"{who}: {busy.lower()}"
            return ActionResult(False, prefix), None

    if not storage.spend_energy(challenger_id, DUEL_ENERGY_COST):
        _clear_duel_meta(storage, challenger_id, target_id)
        return ActionResult(False, "Не удалось списать энергию у вызывающего."), None
    if not storage.spend_energy(target_id, DUEL_ENERGY_COST):
        storage.restore_energy(challenger_id, DUEL_ENERGY_COST)
        _clear_duel_meta(storage, challenger_id, target_id)
        return ActionResult(False, "Не удалось списать энергию у тебя."), None

    _clear_duel_meta(storage, challenger_id, target_id)

    from app.duel_grid import start_duel_grid

    result, session = start_duel_grid(storage, challenger_id, target_id)
    if not result.ok or session is None:
        storage.restore_energy(challenger_id, DUEL_ENERGY_COST)
        storage.restore_energy(target_id, DUEL_ENERGY_COST)
        return ActionResult(False, result.text or "Не удалось начать дуэль."), None

    c_power = equipment_power(challenger)
    t_power = equipment_power(target)
    common = (
        f"⚔️ Тактическая дуэль на поле {session.grid}×{session.grid}!\n"
        f"{h(challenger.nickname)} ({c_power}) vs {h(target.nickname)} ({t_power}).\n"
        f"Ход {10} сек · стрельба по дальности оружия · укрытия 50% промах.\n"
        f"Первый ход: {h(challenger.nickname)}."
    )
    target_text = f"Ты принял вызов.\n{common}"
    challenger_text = f"Дуэль началась!\n{common}"
    payload = {"duel_started": True, "duel_id": session.duel_id, "challenger_id": challenger_id, "target_id": target_id}
    return ActionResult(True, target_text, payload=payload), challenger_text


# Расходники, патроны и топливо, которые можно брать пачкой у торговца.
BULK_BUY_ITEM_KEYS: frozenset[str] = frozenset(
    {
        "energy_drink",
        "nitrous_oxide",
        "medkit",
        "medkit_army",
        "medkit_science",
        "ammo_pack",
        "ammo_pistol",
        "ammo_shotgun",
        "ammo_rifle",
        "ammo_gauss",
        "vodka",
        "antirad",
        "antibiotic",
        "bread",
        "sausage",
        "stew",
        "water_bottle",
        "mineral_water",
        "beard_tea",
        "diesel_can",
        "gasoline_can",
        "fuel_can",
        "armor_upgrade",
        "gear_upgrade",
        "stash_case",
    }
)
BULK_BUY_MAX_QTY = 25

def buy_item(storage: Storage, telegram_id: int, item_key: str, amount: int = 1) -> ActionResult:
    item_key = normalize_shop_item_key(item_key)
    item = SHOP_ITEMS.get(item_key)
    if item is None:
        if item_key in SEASON_REWARD_ITEM_KEYS:
            return ActionResult(False, "Эксклюзив сезонного рейтинга — у торговца не продаётся.")
        return ActionResult(False, "Такого товара нет у торговца.")
    qty = max(1, int(amount))
    unit_price = int(item["buy_price"])
    title = str(item["name"])

    character = storage.get_character(telegram_id)
    if character is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(character):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked

    if item_key == "truck" and character.truck_owned:
        return ActionResult(False, "У тебя уже есть грузовик.")
    if item_key == "niva" and character.niva_owned:
        return ActionResult(False, "У тебя уже есть Нива.")
    if item_key == "bicycle" and character.bicycle_owned:
        return ActionResult(False, "У тебя уже есть велосипед.")
    if item_key == "sleeping_bag" and character.sleeping_bag_owned:
        return ActionResult(False, "У тебя уже есть спальник.")

    from app.vendors import shop_item_vendor, vendor_item_is_unlocked, get_vendor_tier, VENDOR_TIER_MAX, VENDOR_TITLES

    vendor = shop_item_vendor(item_key)
    if vendor is not None and not vendor_item_is_unlocked(storage, telegram_id, item_key):
        tier = get_vendor_tier(storage, telegram_id, vendor)
        who = VENDOR_TITLES.get(vendor, "торговец")
        return ActionResult(
            False,
            f"«{title}» пока нет у специалиста «{who}» (этап {tier}/{VENDOR_TIER_MAX}). "
            f"Улучши ассортимент у него.",
        )

    # Пачкой — только расходники, и не больше ×25.
    if qty > 1:
        if item_key in {"truck", "niva", "bicycle", "sleeping_bag"}:
            return ActionResult(False, f"{title} можно купить только по одной штуке.")
        if item_key not in BULK_BUY_ITEM_KEYS:
            return ActionResult(False, f"{title} покупается по одной штуке.")
        if qty > BULK_BUY_MAX_QTY:
            return ActionResult(False, f"За раз можно купить не больше {BULK_BUY_MAX_QTY} шт.")

    total_price = unit_price * qty
    from app.special_events import BLOCKADE_STOCK_KEYS, get_shop_stock, consume_shop_stock

    if item_key in BLOCKADE_STOCK_KEYS:
        stock = get_shop_stock(storage)
        if stock is not None and item_key in stock and int(stock.get(item_key, 0)) < qty:
            left = int(stock.get(item_key, 0))
            return ActionResult(
                False,
                f"Поставки перебиты бандитами: товара мало (осталось {left}). "
                "Зачисти логово, чтобы открыть поставки.",
            )

    if not storage.change_money(telegram_id, -total_price):
        need_txt = f"{total_price} RU" if qty > 1 else f"покупки: {title}"
        return ActionResult(False, f"Недостаточно денег для {need_txt}.")

    if item_key in BLOCKADE_STOCK_KEYS:
        stock_err = consume_shop_stock(storage, item_key, qty)
        if stock_err:
            storage.change_money(telegram_id, total_price)
            return ActionResult(False, stock_err)

    if item_key == "truck":
        storage.set_truck_owned(telegram_id)
        return ActionResult(
            True,
            f"Покупка оформлена: грузовик (×{TRAVEL_SPEED_TRUCK:g} к скорости перехода) в твоём распоряжении.",
        )
    if item_key == "niva":
        storage.set_niva_owned(telegram_id)
        return ActionResult(True, f"Нива куплена: переходы ×{TRAVEL_SPEED_NIVA:g}.")
    if item_key == "bicycle":
        storage.set_bicycle_owned(telegram_id)
        return ActionResult(
            True,
            f"Велосипед куплен: переходы ×{TRAVEL_SPEED_BICYCLE:g}, "
            f"награда ×{TRANSPORT_QUEST_REWARD_MULT['bicycle']:g} если доехал на велосипеде.",
        )
    if item_key == "sleeping_bag":
        storage.set_sleeping_bag_owned(telegram_id)
        return ActionResult(True, "Спальник куплен. Энергия теперь восстанавливается в 2 раза быстрее.")
    if item_key == "diesel_can":
        storage.change_diesel(telegram_id, FUEL_CAN_DIESEL_AMOUNT * qty)
        if qty == 1:
            return ActionResult(
                True,
                f"Куплена канистра дизеля. Дизель +{FUEL_CAN_DIESEL_AMOUNT} (стоимость {total_price} RU).",
            )
        return ActionResult(
            True,
            f"Куплено канистр дизеля: {qty}. Дизель +{FUEL_CAN_DIESEL_AMOUNT * qty} (стоимость {total_price} RU).",
        )
    if item_key == "gasoline_can":
        storage.change_gasoline(telegram_id, FUEL_CAN_GASOLINE_AMOUNT * qty)
        if qty == 1:
            return ActionResult(
                True,
                f"Куплена канистра бензина. Бензин +{FUEL_CAN_GASOLINE_AMOUNT} (стоимость {total_price} RU).",
            )
        return ActionResult(
            True,
            f"Куплено канистр бензина: {qty}. Бензин +{FUEL_CAN_GASOLINE_AMOUNT * qty} (стоимость {total_price} RU).",
        )
    if item_key in WEAPON_CATALOG:
        storage.add_item(telegram_id, item_key, 1)
        storage.add_player_stat(telegram_id, "trades_done", 1)
        achievements_text = _progress_and_unlock_achievements(storage, telegram_id)
        return ActionResult(
            True,
            f"Куплено оружие: {title} (стоимость {total_price} RU).\n"
            "Предмет добавлен в инвентарь, экипируй его вручную в разделе Инвентарь."
            f"{achievements_text}",
        )
    if item_key in ARMOR_CATALOG:
        storage.add_item(telegram_id, item_key, 1)
        storage.add_player_stat(telegram_id, "trades_done", 1)
        achievements_text = _progress_and_unlock_achievements(storage, telegram_id)
        return ActionResult(
            True,
            f"Куплена броня: {title}.\n"
            "Предмет добавлен в инвентарь, экипируй его вручную в разделе Инвентарь."
            f"{achievements_text}",
        )
    if item_key == "armor_upgrade":
        storage.add_item(telegram_id, "armor_upgrade", qty)
        storage.add_player_stat(telegram_id, "trades_done", 1)
        _add_rating(storage, telegram_id, RATING_REWARD["trade_action"])
        achievements_text = _progress_and_unlock_achievements(storage, telegram_id)
        if qty == 1:
            return ActionResult(
                True,
                f"Куплено улучшение брони за {total_price} RU.\n"
                "Установи его в разделе «Экипировка» на любую броню "
                "(+1 защита = −1 урона от удара)."
                f"{achievements_text}",
            )
        return ActionResult(
            True,
            f"Куплено улучшений брони: {qty} за {total_price} RU.\n"
            "Установи их в разделе «Экипировка»."
            f"{achievements_text}",
        )
    if item_key == "stash_coordinates":
        from app.stash_hunt import start_stash_hunt, STASH_COORDINATE_PRICE

        result = start_stash_hunt(storage, telegram_id, source="buy")
        if not result.ok:
            storage.change_money(telegram_id, total_price)
        return result
    if item_key == "artifact_slot":
        if has_purchased_artifact_slot_upgrade(character):
            storage.change_money(telegram_id, total_price)
            return ActionResult(False, "Дополнительная ячейка артефакта уже установлена.")
        storage.update_equipment_fields(
            telegram_id,
            {"artifact_slot_upgrade": 1, "artifact_slots": 2},
        )
        storage.add_player_stat(telegram_id, "trades_done", 1)
        _add_rating(storage, telegram_id, RATING_REWARD["trade_action"])
        updated = storage.get_character(telegram_id, refresh_energy=False)
        slots_now = max_artifact_slots(updated) if updated is not None else 1
        return ActionResult(
            True,
            "Техник поставил доп. ячейку под артефакт (+1 к ячейкам брони).\n"
            f"Сейчас доступно ячеек: {slots_now}/{MAX_ARTIFACT_EQUIP_SLOTS} "
            "(T1–T2: 0, T3: 1, T4+: 2, улучшение: +1).",
        )

    storage.add_item(telegram_id, item_key, qty)
    if qty == 1:
        return ActionResult(True, f"Куплено: {title} за {total_price} RU.")
    return ActionResult(True, f"Куплено: {title} ×{qty} за {total_price} RU.")


# Каталог продажи торговцу по категориям (ключи sell:…).
TRADER_SELL_CATALOG: dict[str, tuple[str, ...]] = {
    "consumables": (
        "energy_drink",
        "nitrous_oxide",
        "medkit",
        "medkit_army",
        "medkit_science",
        "vodka",
        "antirad",
        "antibiotic",
        "bread",
        "sausage",
        "stew",
        "water_bottle",
        "mineral_water",
        "beard_tea",
        "diesel_can",
        "gasoline_can",
    ),
    "trophies": (
        "artifact",
        "artifact_power",
        "artifact_vitality",
        "artifact_antirad",
        "artifact_junk_slime",
        "artifact_junk_bolt",
        "artifact_junk_battery",
        "artifact_junk_flash",
        "artifact_junk_stone",
        "artifact_junk_fog",
        "artifact_junk_splinter",
        "mutant_tail",
        "mutant_claw",
        "mutant_fang",
        "mutant_eye",
        "mutant_hide",
        "mutant_tendril",
    ),
    "gear": (
        "detector_otklik",
        "detector_medved",
        "detector_veles",
        "detector_svarog",
        "sleeping_bag",
        "bicycle",
        "niva",
        "truck",
        "stash_case",
        "armor_upgrade",
    ),
    "armor": (
        "armor_leather",
        "armor_stalker_vest",
        "armor_psz7d",
        "armor_zarya",
        "armor_berill5m",
        "armor_seva",
        "armor_scientific",
        "armor_exo",
        "armor_nosorog",
    ),
    "weapons": (
        "ammo_pistol",
        "ammo_shotgun",
        "ammo_rifle",
        "ammo_gauss",
        "weapon_pm",
        "weapon_fora12",
        "weapon_sawedoff",
        "weapon_mp5",
        "weapon_chaser13",
        "weapon_aks74u",
        "weapon_ak74",
        "weapon_spas12",
        "weapon_lr300",
        "weapon_il86",
        "weapon_an94",
        "weapon_gp37",
        "weapon_vintar",
        "weapon_svd",
        "weapon_rp74",
        "weapon_gauss",
        "weapon_raccoon",
    ),
}

# Алиасы ключей инвентаря для одной и той же вещи.
_SELL_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "weapon_fora12": ("weapon_fort12",),
    "weapon_fort12": ("weapon_fora12",),
    "armor_sunrise": ("armor_zarya",),
    "armor_zarya": ("armor_sunrise",),
    "armor_berill5m": ("armor_bulat",),
    "armor_bulat": ("armor_berill5m",),
    "armor_exoskeleton": ("armor_exo",),
    "armor_exo": ("armor_exoskeleton",),
}


def _inventory_qty_for_sell_key(character: Character, item_key: str) -> int:
    qty = int(character.inventory.get(item_key, 0))
    for alias in _SELL_KEY_ALIASES.get(item_key, ()):
        qty += int(character.inventory.get(alias, 0))
    return qty


def _remove_inventory_for_sell(storage: Storage, telegram_id: int, item_key: str) -> bool:
    """Снять 1 шт. из инвентаря по ключу продажи, пробуя алиасы того же предмета."""
    if storage.remove_item(telegram_id, item_key, 1):
        return True
    for alias in _SELL_KEY_ALIASES.get(item_key, ()):
        if storage.remove_item(telegram_id, alias, 1):
            return True
    return False


def player_owns_sellable_item(character: Character, item_key: str) -> bool:
    """Есть ли у игрока предмет для продажи торговцу (инвентарь / экип / флаги)."""
    item_key = normalize_shop_item_key(item_key)
    item = SHOP_ITEMS.get(item_key)
    if item is None or int(item.get("sell_price", 0)) <= 0:
        return False
    if item_key == "truck":
        return bool(character.truck_owned)
    if item_key == "niva":
        return bool(character.niva_owned)
    if item_key == "bicycle":
        return bool(character.bicycle_owned)
    if item_key == "sleeping_bag":
        return bool(character.sleeping_bag_owned)
    if item_key == "diesel_can":
        return int(character.diesel) >= FUEL_CAN_DIESEL_AMOUNT
    if item_key == "gasoline_can":
        return int(character.gasoline) >= FUEL_CAN_GASOLINE_AMOUNT
    if _inventory_qty_for_sell_key(character, item_key) > 0:
        return True
    title = str(item["name"])
    if item_key in WEAPON_CATALOG or item_key in _SELL_KEY_ALIASES:
        equipped = str(character.equipment.get("weapon", "Нож"))
        if equipped == title and title != "Нож":
            return True
    if item_key in ARMOR_CATALOG or item_key.startswith("armor_"):
        equipped = str(character.equipment.get("armor", "Куртка новичка"))
        if equipped == title and title != "Куртка новичка":
            return True
    if item_key in ARTIFACT_INVENTORY_TO_NAME:
        expected = ARTIFACT_INVENTORY_TO_NAME[item_key]
        for slot_key in ARTIFACT_EQUIP_SLOT_KEYS:
            equipped = str(character.equipment.get(slot_key, "Нет") or "Нет")
            if equipped == expected:
                return True
    return False


def list_owned_trader_sell_buttons(character: Character, category: str) -> list[tuple[str, str]]:
    """Кнопки продажи только для предметов, которые есть у игрока."""
    keys = TRADER_SELL_CATALOG.get(category, ())
    buttons: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item_key in keys:
        canon = canonical_sell_item_key(item_key)
        if canon in seen:
            continue
        if not player_owns_sellable_item(character, item_key):
            continue
        item = SHOP_ITEMS.get(canon)
        if item is None or int(item.get("sell_price", 0)) <= 0:
            continue
        seen.add(canon)
        title = str(item["name"])
        price = int(item["sell_price"])
        if canon == "truck":
            label = f"Продать {title} ({price})"
        elif canon == "sleeping_bag":
            label = f"Продать {title} ({price})"
        elif canon == "diesel_can":
            cans = max(1, int(character.diesel) // FUEL_CAN_DIESEL_AMOUNT)
            label = f"Продать {title} ×{cans} ({price})"
        elif canon == "gasoline_can":
            cans = max(1, int(character.gasoline) // FUEL_CAN_GASOLINE_AMOUNT)
            label = f"Продать {title} ×{cans} ({price})"
        else:
            qty = _inventory_qty_for_sell_key(character, canon)
            if qty <= 0:
                label = f"Продать {title} (экип.) ({price})"
            elif qty > 1:
                label = f"Продать {title} ×{qty} ({price})"
            else:
                label = f"Продать {title} ({price})"
        buttons.append((label, f"sell:{canon}"))
    return buttons


def default_trader_sell_catalog_buttons(category: str) -> list[tuple[str, str]]:
    """Подписи кнопок продажи по каталогу (fallback, когда нет персонажа)."""
    buttons: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item_key in TRADER_SELL_CATALOG.get(category, ()):
        canon = canonical_sell_item_key(item_key)
        if canon in seen:
            continue
        item = SHOP_ITEMS.get(canon)
        if item is None or int(item.get("sell_price", 0)) <= 0:
            continue
        seen.add(canon)
        price = int(item["sell_price"])
        title = str(item["name"])
        buttons.append((f"Продать {title} ({price})", f"sell:{canon}"))
    return buttons


def trader_sell_categories_with_stock(character: Character) -> list[tuple[str, str]]:
    """Категории продажи, в которых есть хотя бы один предмет игрока."""
    labels = {
        "consumables": "🧰 Расходники",
        "trophies": "💎 Трофеи",
        "gear": "🛠 Прочее",
        "armor": "🦺 Броня",
        "weapons": "🔫 Оружие",
    }
    rows: list[tuple[str, str]] = []
    for key, title in labels.items():
        if list_owned_trader_sell_buttons(character, key):
            rows.append((title, f"trade:sell:{key}:0"))
    return rows


def sell_item(storage: Storage, telegram_id: int, item_key: str) -> ActionResult:
    item_key = canonical_sell_item_key(item_key)
    item = SHOP_ITEMS.get(item_key)
    if item is None:
        return ActionResult(False, "Такого предмета нет.")
    sell_price = int(item["sell_price"])
    title = str(item["name"])
    if sell_price <= 0:
        return ActionResult(False, f"{title} торговец не выкупает.")
    character = storage.get_character(telegram_id, refresh_energy=False)
    if character is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(character):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    if item_key == "truck":
        if not character.truck_owned:
            return ActionResult(False, "У тебя нет грузовика для продажи.")
        storage.clear_truck_owned(telegram_id)
        storage.change_money(telegram_id, sell_price)
        return ActionResult(True, f"Продано: {title} за {sell_price} RU.")
    if item_key == "niva":
        if not character.niva_owned:
            return ActionResult(False, "У тебя нет Нивы для продажи.")
        storage.clear_niva_owned(telegram_id)
        storage.change_money(telegram_id, sell_price)
        return ActionResult(True, f"Продано: {title} за {sell_price} RU.")
    if item_key == "bicycle":
        if not character.bicycle_owned:
            return ActionResult(False, "У тебя нет велосипеда для продажи.")
        storage.clear_bicycle_owned(telegram_id)
        storage.change_money(telegram_id, sell_price)
        return ActionResult(True, f"Продано: {title} за {sell_price} RU.")
    if item_key == "sleeping_bag":
        if not character.sleeping_bag_owned:
            return ActionResult(False, "У тебя нет спальника для продажи.")
        storage.clear_sleeping_bag_owned(telegram_id)
        storage.change_money(telegram_id, sell_price)
        return ActionResult(True, f"Продано: {title} за {sell_price} RU.")
    if item_key in WEAPON_CATALOG:
        weapon_name = str(item["name"])
        equipped_weapon = str(character.equipment.get("weapon", "Нож"))
        final_sell_price = sell_price
        if equipped_weapon == weapon_name:
            if weapon_name == "Нож":
                return ActionResult(False, "Нож продать нельзя.")
            weapon_durability = _durability_percent(character, "weapon")
            final_sell_price = _price_with_durability(sell_price, weapon_durability)
            storage.set_equipment_item(telegram_id, "weapon", "Нож")
        elif not _remove_inventory_for_sell(storage, telegram_id, item_key):
            return ActionResult(False, f"У тебя нет оружия: {weapon_name}.")
        storage.change_money(telegram_id, final_sell_price)
        if final_sell_price != sell_price:
            return ActionResult(
                True,
                f"Продано: {title} за {final_sell_price} RU.\n"
                f"(Базовая цена {sell_price} RU снижена из-за износа.)",
            )
        return ActionResult(True, f"Продано: {title} за {final_sell_price} RU.")
    if item_key in ARMOR_CATALOG:
        armor_name = str(item["name"])
        equipped_armor = str(character.equipment.get("armor", "Куртка новичка"))
        final_sell_price = sell_price
        if equipped_armor == armor_name:
            armor_durability = _durability_percent(character, "armor")
            final_sell_price = _price_with_durability(sell_price, armor_durability)
            returned = _return_armor_upgrades_to_inventory(storage, telegram_id, character)
            storage.set_equipment_item(telegram_id, "armor", "Куртка новичка")
            upgrade_note = f"\nУлучшения брони сняты в инвентарь: ×{returned}." if returned else ""
        elif not _remove_inventory_for_sell(storage, telegram_id, item_key):
            return ActionResult(False, f"У тебя нет брони: {armor_name}.")
        else:
            upgrade_note = ""
        storage.change_money(telegram_id, final_sell_price)
        if final_sell_price != sell_price:
            return ActionResult(
                True,
                f"Продано: {title} за {final_sell_price} RU.\n"
                f"(Базовая цена {sell_price} RU снижена из-за износа.){upgrade_note}",
            )
        return ActionResult(True, f"Продано: {title} за {final_sell_price} RU.{upgrade_note}")
    if item_key in ARTIFACT_INVENTORY_TO_NAME:
        from app.artifact_features import (
            artifact_equip_cooldown_active,
            artifact_faction_tax,
            effective_artifact_trader_sell_price,
            mark_artifact_equip_cooldown,
            record_artifact_trader_sale,
        )

        expected_name = ARTIFACT_INVENTORY_TO_NAME[item_key]
        sell_price = effective_artifact_trader_sell_price(storage, item_key, sell_price)
        removed_from_inventory = storage.remove_item(telegram_id, item_key, 1)
        if not removed_from_inventory:
            slot_key = _artifact_equip_slot_for_name(character, expected_name)
            if slot_key is None:
                return ActionResult(False, f"У тебя нет артефакта: {title}.")
            storage.set_equipment_item(telegram_id, slot_key, "Нет")
            storage.sync_gear_power(telegram_id)
            updated = storage.get_character(telegram_id, refresh_energy=False)
            if updated is not None:
                max_hp = effective_max_health(updated)
                if updated.health > max_hp:
                    storage.change_health(telegram_id, max_hp - updated.health, max_health=max_hp)
        else:
            storage.sync_gear_power(telegram_id)
        payout, tax = artifact_faction_tax(sell_price, character.faction)
        storage.change_money(telegram_id, payout)
        record_artifact_trader_sale(storage, item_key)
        mark_artifact_equip_cooldown(storage, telegram_id, item_key)
        tax_note = f"\nНалог группировки 10%: −{tax} RU." if tax > 0 else ""
        dynamic_note = ""
        if payout != int(item["sell_price"]):
            dynamic_note = f"\n(Динамическая цена скупки: база {item['sell_price']} RU.)"
        return ActionResult(True, f"Продано: {title} за {payout} RU.{tax_note}{dynamic_note}")
    if item_key == "diesel_can":
        if not storage.change_diesel(telegram_id, -FUEL_CAN_DIESEL_AMOUNT):
            return ActionResult(False, "Недостаточно дизеля для продажи канистры.")
    elif item_key == "gasoline_can":
        if not storage.change_gasoline(telegram_id, -FUEL_CAN_GASOLINE_AMOUNT):
            return ActionResult(False, "Недостаточно бензина для продажи канистры.")
    else:
        if item_key not in WEAPON_CATALOG and not storage.remove_item(telegram_id, item_key, 1):
            return ActionResult(False, f"У тебя нет предмета: {title}.")
    storage.change_money(telegram_id, sell_price)
    return ActionResult(True, f"Продано: {title} за {sell_price} RU.")


def _primary_keys_by_name(catalog: dict[str, dict[str, int | str]]) -> dict[str, str]:
    by_name: dict[str, str] = {}
    for key, entry in catalog.items():
        name = str(entry["name"])
        by_name.setdefault(name, key)
    return by_name


def _price_with_durability(base_price: int, durability: int) -> int:
    # Даже сильно изношенный предмет имеет минимальную выкупную стоимость.
    effective = max(20, min(100, durability))
    return max(1, int(round(base_price * (effective / 100))))


def list_equippable_weapons(character: Character) -> list[tuple[str, str, int]]:
    options: list[tuple[str, str, int]] = []
    for key, amount in sorted(character.inventory.items()):
        if key in WEAPON_CATALOG and amount > 0:
            title = str(WEAPON_CATALOG[key]["name"])
            options.append((key, title, int(amount)))
    return options


def list_equippable_armor(character: Character) -> list[tuple[str, str, int]]:
    options: list[tuple[str, str, int]] = []
    for key, amount in sorted(character.inventory.items()):
        if key in ARMOR_CATALOG and amount > 0:
            title = str(ARMOR_CATALOG[key]["name"])
            options.append((key, title, int(amount)))
    return options


def list_equippable_artifacts(character: Character) -> list[tuple[str, str, int]]:
    options: list[tuple[str, str, int]] = []
    for key in ARTIFACT_DROP_KEYS:
        amount = int(character.inventory.get(key, 0))
        if amount > 0:
            title = ARTIFACT_INVENTORY_TO_NAME.get(key, ITEM_LABELS.get(key, key))
            options.append((key, title, amount))
    return options


def list_equippable_for_slot(character: Character, slot: str) -> list[tuple[str, str, int]]:
    if slot == "weapon":
        return list_equippable_weapons(character)
    if slot == "armor":
        return list_equippable_armor(character)
    if slot == "artifact":
        return list_equippable_artifacts(character)
    return []


def _artifact_bonus_short(artifact_name: str) -> str:
    if artifact_name in ARTIFACT_ENERGY_REGEN_NAMES:
        power = int(ARTIFACT_EQUIP_BONUSES.get(artifact_name, {}).get("power", 2))
        return f"+{power} сила, +5% энергия"
    if artifact_name in ARTIFACT_RAD_CLEANSE_NAMES:
        power = int(ARTIFACT_EQUIP_BONUSES.get(artifact_name, {}).get("power", 2))
        return (
            f"+{power} сила, −{ARTIFACT_RAD_CLEANSE_AMOUNT} рад./"
            f"{ARTIFACT_RAD_CLEANSE_INTERVAL_MINUTES} мин"
        )
    bonus = ARTIFACT_EQUIP_BONUSES.get(artifact_name, {})
    power = int(bonus.get("power", 0))
    hp = int(bonus.get("hp", 0))
    parts: list[str] = []
    if power:
        parts.append(f"+{power} сила")
    if hp:
        parts.append(f"+{hp} HP")
    return ", ".join(parts) if parts else "без бонуса"


def build_equip_root_text(character: Character) -> tuple[str, list[tuple[str, str, int]]]:
    """Корневое меню экипировки: текущая снаряга + категории."""
    weapon = str(character.equipment.get("weapon", "Нож"))
    armor = str(character.equipment.get("armor", "Куртка новичка"))
    slots_cap = max_artifact_slots(character)
    art_lines: list[str] = []
    for idx, key in enumerate(ARTIFACT_EQUIP_SLOT_KEYS, start=1):
        if idx > max(slots_cap, 1):
            # Показываем хотя бы схему лимита, если слотов 0 — одной строкой ниже.
            break
        name = str(character.equipment.get(key, "Нет") or "Нет")
        note = f" ({_artifact_bonus_short(name)})" if name and name != "Нет" else ""
        art_lines.append(f"💎 Артефакт {idx}: {name}{note}")
    if slots_cap <= 0:
        art_block = "💎 Артефакты: нет ячеек (нужна броня T3+)"
    else:
        art_block = "\n".join(art_lines)
    armor_tier = armor_gear_tier(armor)
    base_slots = armor_base_artifact_slots(armor)
    upgrade_mark = "+1 техник" if has_purchased_artifact_slot_upgrade(character) else "без апгрейда"
    w_count = len(list_equippable_weapons(character))
    a_count = len(list_equippable_armor(character))
    art_count = len(list_equippable_artifacts(character))
    menu_items = [
        ("weapon", EQUIP_SLOT_LABELS["weapon"], w_count),
        ("armor", EQUIP_SLOT_LABELS["armor"], a_count),
        ("artifact", EQUIP_SLOT_LABELS["artifact"], art_count),
    ]
    defense = armor_defense(character)
    flat_mit = armor_flat_mitigation(character)
    block = armor_block_chance(character)
    upgrade_stock = int(character.inventory.get("armor_upgrade", 0))
    defense_line = ""
    armor_bonus_bits: list[str] = []
    if flat_mit > 0:
        armor_bonus_bits.append(f"смягчение −{flat_mit} HP")
    if block > 0:
        armor_bonus_bits.append(f"блок {block}%")
    if defense > 0:
        armor_bonus_bits.append(f"апгрейд −{defense}")
    if armor_bonus_bits:
        defense_line = "\n🛡 " + " · ".join(armor_bonus_bits)
    if upgrade_stock > 0:
        defense_line += f"\n📦 Улучшений в инвентаре: {upgrade_stock}"
    text = (
        "⚙️ Экипировка\n"
        "Выбери категорию, затем предмет из инвентаря.\n"
        f"Сила снаряги: {equipment_power(character)}\n\n"
        f"🔫 Оружие: {weapon}\n"
        f"🦺 Броня: {armor} (T{armor_tier}){defense_line}\n"
        f"{art_block}\n"
        f"Ячейки артов: {slots_cap}/{MAX_ARTIFACT_EQUIP_SLOTS} "
        f"(броня T{armor_tier} → {base_slots}, {upgrade_mark})\n\n"
        f"В инвентаре: оружие {w_count}, броня {a_count}, арты {art_count}."
    )
    return text, menu_items


def build_equip_slot_page(
    character: Character,
    slot: str,
    page: int = 0,
) -> tuple[str, str, int, int, list[tuple[str, str, int]]]:
    """Возвращает (text, slot, page, total_pages, page_options)."""
    if slot not in EQUIP_SLOT_LABELS:
        return ("Неизвестная категория экипировки.", "weapon", 0, 1, [])

    options = list_equippable_for_slot(character, slot)
    label = EQUIP_SLOT_LABELS[slot]
    current = str(character.equipment.get(slot, "Нет") or "Нет")
    if slot == "weapon" and not current:
        current = "Нож"
    if slot == "armor" and not current:
        current = "Куртка новичка"

    if not options:
        empty = (
            f"⚙️ {label}\n"
            f"Сейчас надето: {current}\n\n"
            f"В инвентаре нет предметов этой категории."
        )
        return empty, slot, 0, 1, []

    total = len(options)
    total_pages = max(1, (total + EQUIP_PAGE_SIZE - 1) // EQUIP_PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    start = safe_page * EQUIP_PAGE_SIZE
    chunk = options[start : start + EQUIP_PAGE_SIZE]

    lines = [
        f"⚙️ {label}",
        f"Сейчас надето: {current}",
        f"Страница {safe_page + 1}/{total_pages} • доступно: {total}",
        "",
        "Выбери предмет:",
    ]
    for key, title, amount in chunk:
        mark = " ✅" if title == current else ""
        bonus = ""
        if slot == "artifact":
            bonus = f" [{_artifact_bonus_short(title)}]"
        lines.append(f"• {title} x{amount}{bonus}{mark}")
    return ("\n".join(lines), slot, safe_page, total_pages, chunk)


def _unequip_artifact_to_inventory(
    storage: Storage,
    telegram_id: int,
    equipped_name: str,
    *,
    slot: str = "artifact",
) -> None:
    """Вернуть экипированный арт в инвентарь и подрезать HP под новый максимум."""
    if not equipped_name or equipped_name == "Нет":
        return
    inv_key = ARTIFACT_NAME_TO_INVENTORY.get(equipped_name)
    if inv_key is not None:
        storage.add_item(telegram_id, inv_key, 1)
        from app.artifact_features import mark_artifact_equip_cooldown

        mark_artifact_equip_cooldown(storage, telegram_id, inv_key)
    storage.set_equipment_item(telegram_id, slot, "Нет")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is not None:
        max_hp = effective_max_health(player)
        if player.health > max_hp:
            storage.change_health(telegram_id, max_hp - player.health, max_health=max_hp)
    storage.sync_gear_power(telegram_id)


def unequip_artifact(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    # Снимаем с последней занятой ячейки.
    for idx in range(len(ARTIFACT_EQUIP_SLOT_KEYS), 0, -1):
        key = ARTIFACT_EQUIP_SLOT_KEYS[idx - 1]
        name = str(player.equipment.get(key, "Нет") or "Нет")
        if name and name != "Нет":
            _unequip_artifact_to_inventory(storage, telegram_id, name, slot=key)
            suffix = f" из {idx}-й ячейки" if idx > 1 else ""
            return ActionResult(
                True,
                f"Снят артефакт{suffix}: {name}. Он вернулся в инвентарь.",
            )
    return ActionResult(False, "Артефакт не экипирован.")


def equip_weapon(storage: Storage, telegram_id: int, item_key: str) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    if item_key not in WEAPON_CATALOG:
        return ActionResult(False, "Такое оружие нельзя экипировать.")
    if not storage.remove_item(telegram_id, item_key, 1):
        return ActionResult(False, "Этого оружия нет в инвентаре.")

    weapon_name = str(WEAPON_CATALOG[item_key]["name"])
    current_weapon = str(player.equipment.get("weapon", "Нож"))
    if current_weapon == weapon_name:
        storage.add_item(telegram_id, item_key, 1)
        return ActionResult(False, f"Оружие «{weapon_name}» уже экипировано.")

    old_key = _primary_keys_by_name(WEAPON_CATALOG).get(current_weapon)
    if old_key is not None and current_weapon != "Нож":
        storage.add_item(telegram_id, old_key, 1)
    storage.set_equipment_item(telegram_id, "weapon", weapon_name)
    achievements_text = _progress_and_unlock_achievements(storage, telegram_id)
    return ActionResult(True, f"Экипировано оружие: {weapon_name}.{achievements_text}")


def equip_armor(storage: Storage, telegram_id: int, item_key: str) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    if item_key not in ARMOR_CATALOG:
        return ActionResult(False, "Такую броню нельзя экипировать.")
    if not storage.remove_item(telegram_id, item_key, 1):
        return ActionResult(False, "Этой брони нет в инвентаре.")

    armor_name = str(ARMOR_CATALOG[item_key]["name"])
    current_armor = str(player.equipment.get("armor", "Куртка новичка"))
    if current_armor == armor_name:
        storage.add_item(telegram_id, item_key, 1)
        return ActionResult(False, f"Броня «{armor_name}» уже экипирована.")

    old_key = _primary_keys_by_name(ARMOR_CATALOG).get(current_armor)
    if old_key is not None:
        storage.add_item(telegram_id, old_key, 1)
    returned = _return_armor_upgrades_to_inventory(storage, telegram_id, player)
    storage.set_equipment_item(telegram_id, "armor", armor_name)
    refreshed = storage.get_character(telegram_id, refresh_energy=False)
    trimmed: list[str] = []
    if refreshed is not None:
        trimmed = _trim_equipped_artifacts_to_cap(storage, telegram_id, refreshed)
    achievements_text = _progress_and_unlock_achievements(storage, telegram_id)
    note = ""
    if returned > 0:
        note = f" Улучшения брони сняты в инвентарь (×{returned}) — установи на новую броню."
    if trimmed:
        note += f" Лишние арты сняты в инвентарь: {', '.join(trimmed)}."
    slots_now = max_artifact_slots(refreshed) if refreshed is not None else 0
    return ActionResult(
        True,
        f"Экипирована броня: {armor_name} "
        f"(ячейки артов: {slots_now}/{MAX_ARTIFACT_EQUIP_SLOTS}).{note}{achievements_text}",
    )


def repair_gear(storage: Storage, telegram_id: int, target: str) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    if target not in {"weapon", "armor"}:
        return ActionResult(False, "Неизвестный тип ремонта.")
    item_name = str(player.equipment.get(target, "—"))
    current = _durability_percent(player, target)
    if current >= MAX_DURABILITY:
        return ActionResult(False, f"{'Оружие' if target == 'weapon' else 'Броня'} уже в идеальном состоянии.")
    missing = MAX_DURABILITY - current
    price = max(80, missing * 7)
    from app.vendors import apply_tech_repair_discount

    price, discount_pct = apply_tech_repair_discount(storage, telegram_id, price)
    if not storage.change_money(telegram_id, -price):
        return ActionResult(False, f"Недостаточно денег на ремонт ({price} RU).")
    storage.update_equipment_fields(telegram_id, {f"{target}_durability": MAX_DURABILITY})
    storage.add_player_stat(telegram_id, "trades_done", 1)
    _add_rating(storage, telegram_id, RATING_REWARD["trade_action"])
    achievements_text = _progress_and_unlock_achievements(storage, telegram_id)
    target_label = "Оружие" if target == "weapon" else "Броня"
    repaired = "отремонтировано" if target == "weapon" else "отремонтирована"
    discount_note = f" (−{discount_pct}% у техника)" if discount_pct > 0 else ""
    return ActionResult(
        True,
        f"{target_label} «{item_name}» полностью {repaired} за {price} RU{discount_note}.{achievements_text}",
    )


def upgrade_armor(storage: Storage, telegram_id: int) -> ActionResult:
    """Покупка модуля улучшения брони в инвентарь (не ставит сразу)."""
    return buy_item(storage, telegram_id, "armor_upgrade", 1)


def install_armor_upgrade(storage: Storage, telegram_id: int) -> ActionResult:
    """Установить одно улучшение из инвентаря на текущую броню."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    armor_name = str(player.equipment.get("armor", "") or "").strip()
    if not armor_name:
        return ActionResult(False, "Сначала экипируй броню.")
    if int(player.inventory.get("armor_upgrade", 0)) <= 0:
        return ActionResult(
            False,
            "Нет улучшения брони в инвентаре. Купи у техника.",
        )
    if not storage.remove_item(telegram_id, "armor_upgrade", 1):
        return ActionResult(False, "Нет улучшения брони в инвентаре.")
    new_level = armor_defense(player) + 1
    storage.update_equipment_fields(telegram_id, {"armor_upgrade_level": new_level})
    return ActionResult(
        True,
        f"На броню «{armor_name}» установлено улучшение.\n"
        f"Защита: +{new_level} (−{new_level} урона от удара).",
    )


def unequip_armor_upgrade(storage: Storage, telegram_id: int) -> ActionResult:
    """Снять одно улучшение с текущей брони обратно в инвентарь."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    level = armor_defense(player)
    if level <= 0:
        return ActionResult(False, "На броне нет установленных улучшений.")
    armor_name = str(player.equipment.get("armor", "Броня") or "Броня")
    new_level = level - 1
    storage.update_equipment_fields(telegram_id, {"armor_upgrade_level": new_level})
    storage.add_item(telegram_id, "armor_upgrade", 1)
    return ActionResult(
        True,
        f"С брони «{armor_name}» снято улучшение (вернулось в инвентарь).\n"
        f"Защита сейчас: +{new_level}.",
    )


def repair_truck(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    if not player.truck_owned:
        return ActionResult(False, "У тебя нет грузовика для ремонта.")
    current = max(0, min(100, int(player.truck_durability)))
    if current >= 100:
        return ActionResult(False, "Грузовик уже в идеальном состоянии.")
    missing = 100 - current
    # Ремонт транспорта −10% от базовой формулы missing*70 / floor 500.
    price = max(450, int(round(missing * 63)))
    from app.vendors import apply_tech_repair_discount

    price, discount_pct = apply_tech_repair_discount(storage, telegram_id, price)
    if not storage.change_money(telegram_id, -price):
        return ActionResult(False, f"Недостаточно денег на ремонт грузовика ({price} RU).")
    storage.set_truck_durability(telegram_id, 100)
    storage.add_player_stat(telegram_id, "trades_done", 1)
    _add_rating(storage, telegram_id, RATING_REWARD["trade_action"])
    achievements_text = _progress_and_unlock_achievements(storage, telegram_id)
    discount_note = f" (−{discount_pct}% у техника)" if discount_pct > 0 else ""
    return ActionResult(True, f"Грузовик полностью отремонтирован за {price} RU{discount_note}.{achievements_text}")


def repair_niva(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    if not player.niva_owned:
        return ActionResult(False, "У тебя нет Нивы для ремонта.")
    current = max(0, min(100, int(player.niva_durability)))
    if current >= 100:
        return ActionResult(False, "Нива уже в идеальном состоянии.")
    missing = 100 - current
    # Ремонт Нивы дешевле грузовика: missing×35, минимум 200 RU.
    price = max(200, int(round(missing * 35)))
    from app.vendors import apply_tech_repair_discount

    price, discount_pct = apply_tech_repair_discount(storage, telegram_id, price)
    if not storage.change_money(telegram_id, -price):
        return ActionResult(False, f"Недостаточно денег на ремонт Нивы ({price} RU).")
    storage.set_niva_durability(telegram_id, 100)
    storage.add_player_stat(telegram_id, "trades_done", 1)
    _add_rating(storage, telegram_id, RATING_REWARD["trade_action"])
    achievements_text = _progress_and_unlock_achievements(storage, telegram_id)
    discount_note = f" (−{discount_pct}% у техника)" if discount_pct > 0 else ""
    return ActionResult(True, f"Нива полностью отремонтирована за {price} RU{discount_note}.{achievements_text}")


# Полевой ремонт: наскоро подлатать технику, чтобы уехать с локации.
# Полный ремонт — только у техника; здесь доводим до минимума прочности.
FIELD_REPAIR_MIN_DURABILITY = 5


def needs_field_repair(character: Character) -> bool:
    """Сломана ли своя техника (грузовик/Нива) — нужен полевой ремонт."""
    return bool(
        (character.truck_owned and int(character.truck_durability) < FIELD_REPAIR_MIN_DURABILITY)
        or (character.niva_owned and int(character.niva_durability) < FIELD_REPAIR_MIN_DURABILITY)
    )


def field_repair_vehicle(storage: Storage, telegram_id: int) -> ActionResult:
    """Бесплатно подлатать сломанную технику до FIELD_REPAIR_MIN_DURABILITY%.

    Нужно, когда техника «умерла» вдали от базы: на ней нельзя уехать,
    а без неё не уйти (привязанный транспорт).
    """
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if is_traveling(player):
        return ActionResult(False, "Нельзя чинить технику во время перехода.")
    fixed: list[str] = []
    if player.truck_owned and int(player.truck_durability) < FIELD_REPAIR_MIN_DURABILITY:
        storage.set_truck_durability(telegram_id, FIELD_REPAIR_MIN_DURABILITY)
        fixed.append("грузовик")
    if player.niva_owned and int(player.niva_durability) < FIELD_REPAIR_MIN_DURABILITY:
        storage.set_niva_durability(telegram_id, FIELD_REPAIR_MIN_DURABILITY)
        fixed.append("Ниву")
    if not fixed:
        return ActionResult(False, "Вся техника в порядке — чинить нечего.")
    what = " и ".join(fixed)
    return ActionResult(
        True,
        f"🔧 Ты наскоро подлатал {what} до {FIELD_REPAIR_MIN_DURABILITY}% — "
        "хватит доехать до базы или гаража.",
    )


def equip_artifact(
    storage: Storage,
    telegram_id: int,
    item_key: str | None = None,
    *,
    target_slot: str | None = None,
) -> ActionResult:
    from app.artifact_features import (
        ARTIFACT_WEAR_MAX,
        _wear_field,
        artifact_bonus_entry,
        artifact_equip_cooldown_active,
        mark_artifact_equip_cooldown,
    )

    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked

    chosen_key = item_key
    if chosen_key is None or chosen_key not in ARTIFACT_DROP_KEYS:
        if chosen_key in ARTIFACT_JUNK_KEYS:
            return ActionResult(False, "Мусорные артефакты нельзя экипировать — только продать торговцу.")
        return ActionResult(False, "Выбери артефакт в меню экипировки.")

    if artifact_equip_cooldown_active(storage, telegram_id, chosen_key):
        return ActionResult(
            False,
            "Этот тип артефакта нельзя экипировать сразу после продажи/снятия — подожди 1 ч.",
        )

    if int(player.inventory.get(chosen_key, 0)) <= 0:
        return ActionResult(False, "У тебя нет этого артефакта в инвентаре.")

    artifact_name = ARTIFACT_INVENTORY_TO_NAME[chosen_key]
    cap = max_artifact_slots(player)
    if cap <= 0:
        return ActionResult(
            False,
            "На этой броне нет ячеек под артефакты.\n"
            "Нужна броня T3 (1 ячейка) или T4+ (2 ячейки); улучшение у техника даёт ещё +1 (макс. 3).",
        )

    slot_keys = ARTIFACT_EQUIP_SLOT_KEYS[:cap]
    equipped_names = [str(player.equipment.get(key, "Нет") or "Нет") for key in slot_keys]
    if artifact_name in equipped_names:
        return ActionResult(False, f"{artifact_name} уже экипирован.")

    if not storage.remove_item(telegram_id, chosen_key, 1):
        return ActionResult(False, "У тебя нет этого артефакта в инвентаре.")

    if target_slot and target_slot in slot_keys:
        target = target_slot
    else:
        target = slot_keys[0]
        for key in slot_keys:
            cur = str(player.equipment.get(key, "Нет") or "Нет")
            if not cur or cur == "Нет":
                target = key
                break
        else:
            old_name = equipped_names[0]
            if old_name and old_name != "Нет":
                old_key = ARTIFACT_NAME_TO_INVENTORY.get(old_name)
                if old_key is not None:
                    storage.add_item(telegram_id, old_key, 1)
                    mark_artifact_equip_cooldown(storage, telegram_id, old_key)
            target = slot_keys[0]

    cur_at_target = str(player.equipment.get(target, "Нет") or "Нет")
    if cur_at_target and cur_at_target != "Нет" and cur_at_target != artifact_name:
        old_key = ARTIFACT_NAME_TO_INVENTORY.get(cur_at_target)
        if old_key is not None:
            storage.add_item(telegram_id, old_key, 1)
            mark_artifact_equip_cooldown(storage, telegram_id, old_key)

    storage.set_equipment_item(telegram_id, target, artifact_name)
    storage.update_equipment_fields(telegram_id, {_wear_field(target): ARTIFACT_WEAR_MAX})
    storage.sync_gear_power(telegram_id)

    bonus = artifact_bonus_entry(artifact_name)
    bonus_parts: list[str] = []
    if bonus.get("power"):
        bonus_parts.append(f"+{bonus['power']} к силе")
    hp_bonus = int(bonus.get("hp") or 0)
    current = storage.get_character(telegram_id, refresh_energy=False)
    max_hp = effective_max_health(current) if current is not None else 100 + hp_bonus
    if current is not None:
        if current.health > max_hp:
            storage.change_health(telegram_id, max_hp - current.health, max_health=max_hp)
        elif hp_bonus > 0 and current.health < max_hp:
            heal = min(hp_bonus, max_hp - current.health)
            if heal > 0:
                storage.change_health(telegram_id, heal, max_health=max_hp)
    if hp_bonus:
        bonus_parts.append(f"+{hp_bonus} к запасу HP")
    if bonus.get("rad_pressure"):
        bonus_parts.append(f"+{bonus['rad_pressure']} рад/10 мин")
    if bonus.get("cleanse_power"):
        bonus_parts.append(f"очистка рад {bonus['cleanse_power']}")
    if chosen_key == "artifact":
        bonus_parts.append("+5% реген энергии")
    if not bonus_parts:
        bonus_parts.append("без доп. бонуса")

    achievements_text = _progress_and_unlock_achievements(storage, telegram_id)
    slot_idx = ARTIFACT_EQUIP_SLOT_KEYS.index(target) + 1
    return ActionResult(
        True,
        f"Экипирован {artifact_name} (ячейка {slot_idx}/{cap}). "
        f"Бонус: {', '.join(bonus_parts)}.{achievements_text}",
    )


def format_inventory(
    character: Character,
    *,
    rating_points: int = 0,
    storage: Storage | None = None,
) -> str:
    rating = max(0, int(rating_points))
    skin = resolve_skin(rating)
    _, next_skin, rating_left = next_skin_progress(rating)
    skin_progress = (
        f"Рейтинг: {rating} | Скин: {skin.title}\n"
        f"До «{next_skin.title}»: ещё {rating_left} рейтинга.\n"
        if next_skin is not None
        else f"Рейтинг: {rating} | Скин: {skin.title} (максимум)\n"
    )
    if character.inventory:
        items = "\n".join(
            f"• {ITEM_LABELS.get(key, key)} x{amount}"
            for key, amount in sorted(character.inventory.items())
        )
    else:
        items = "• Пусто"

    vehicle_parts: list[str] = []
    if character.truck_owned:
        vehicle_parts.append(f"Грузовик ({max(0, min(100, int(character.truck_durability)))}%)")
    if character.niva_owned:
        vehicle_parts.append(f"Нива ({max(0, min(100, int(character.niva_durability)))}%)")
    if character.bicycle_owned:
        vehicle_parts.append("Велосипед")
    vehicle = " + ".join(vehicle_parts) if vehicle_parts else "Нет транспорта"
    sleeping_bag = "Есть спальник (x2 реген энергии)" if character.sleeping_bag_owned else "Спальника нет"
    weapon_name = str(character.equipment.get("weapon", "Нож"))
    armor_name = str(character.equipment.get("armor", "Куртка новичка"))
    weapon_durability = _durability_percent(character, "weapon")
    armor_durability = _durability_percent(character, "armor")
    slots_cap = max_artifact_slots(character)
    art_lines: list[str] = []
    for idx, slot_key in enumerate(ARTIFACT_EQUIP_SLOT_KEYS, start=1):
        if idx > max(slots_cap, 1):
            break
        art_name = str(character.equipment.get(slot_key, "Нет") or "Нет")
        art_lines.append(f"• Артефакт {idx}: {art_name}")
    if slots_cap <= 0:
        art_block = "• Артефакты: нет ячеек (нужна броня T3+)"
    else:
        art_block = "\n".join(art_lines)
    equipment = (
        f"• Оружие: {weapon_name}\n"
        f"• Броня: {armor_name}\n"
        f"{art_block}"
    )
    durability_block = (
        f"• Прочность оружия: {weapon_durability}%\n"
        f"• Прочность брони: {armor_durability}%"
    )

    current_gear_power = equipment_power(character)
    survival_line = (
        f"☢ Радиация: {character.radiation}/100 | "
        f"🍗 Голод: {character.hunger}/100 | "
        f"💧 Жажда: {character.thirst}/100"
    )
    craving_notice = build_survival_craving_notice(character)
    is_leader = False
    if storage is not None and character.faction:
        is_leader = storage.get_faction_leader_id(character.faction) == character.telegram_id
    rank_title = resolve_rank_title(
        faction=character.faction,
        faction_rank=character.faction_rank,
        is_leader=is_leader,
    )
    faction_line = f"Фракция: {character.faction or 'не выбрана'}"
    if rank_title:
        faction_line = f"Фракция: {character.faction} | Звание: {rank_title}"
    medal_suffix = ""
    medal_line = ""
    if storage is not None:
        try:
            from app.player_medals import medals_nick_suffix
            from app.chat_medals import format_medal_profile_line

            medal_suffix = medals_nick_suffix(storage, character.telegram_id)
            medal_line = format_medal_profile_line(storage, character.telegram_id)
        except Exception:
            medal_suffix = ""
            medal_line = ""
    return (
        f"{craving_notice}"
        f"👤 {h(character.nickname)}{medal_suffix} ({h(character.gender)})\n"
        f"ID-адрес: {character.player_uid}\n"
        f"Telegram ID: {character.telegram_id}\n"
        f"{faction_line}\n"
        f"{medal_line}"
        f"Локация: {format_location_display(character)}\n"
        f"Здоровье: {character.health}/{effective_max_health(character)}\n"
        f"Энергия: {character.energy}/{character.max_energy}\n"
        f"Сила снаряги: {current_gear_power}\n"
        f"{skin_progress}"
        f"Баланс: {character.money} RU\n"
        f"{format_respawn_debt_line(storage, character.telegram_id) if storage is not None else ''}"
        f"Транспорт: {vehicle}\n"
        f"Спальник: {sleeping_bag}\n"
        f"Дизель: {character.diesel} | Бензин: {character.gasoline}\n"
        f"{survival_line}\n\n"
        f"Снаряга:\n{equipment}\n{durability_block}\n\n"
        f"Вещи:\n{items}"
    )


def _compute_truck_wear(distance_px: float | None, travel_minutes: int) -> int:
    if distance_px is not None:
        factor = max(0.0, min(1.0, float(distance_px) / 420.0))
    else:
        factor = max(0.0, min(1.0, (travel_minutes - 5) / 20))
    min_wear = TRUCK_WEAR_MIN + int(round(4 * factor))
    max_wear = TRUCK_WEAR_MIN + int(round(10 * factor))
    min_wear = max(TRUCK_WEAR_MIN, min(TRUCK_WEAR_MAX, min_wear))
    max_wear = max(min_wear, min(TRUCK_WEAR_MAX, max_wear))
    return random.randint(min_wear, max_wear)


def _compute_niva_wear(distance_px: float | None, travel_minutes: int) -> int:
    if distance_px is not None:
        factor = max(0.0, min(1.0, float(distance_px) / 420.0))
    else:
        factor = max(0.0, min(1.0, (travel_minutes - 5) / 20))
    min_wear = NIVA_WEAR_MIN + int(round(3 * factor))
    max_wear = NIVA_WEAR_MIN + int(round(8 * factor))
    min_wear = max(NIVA_WEAR_MIN, min(NIVA_WEAR_MAX, min_wear))
    max_wear = max(min_wear, min(NIVA_WEAR_MAX, max_wear))
    return random.randint(min_wear, max_wear)


def list_available_travel_modes(character: Character, *, bound_transport: str | None = None) -> list[tuple[str, str, float, int]]:
    """Доступные режимы: (mode, label, speed_mult, energy_cost)."""
    bound = bound_transport
    if bound in ("niva", "truck"):
        options: list[tuple[str, str, float, int]] = []
        if bound == "niva" and can_travel_by_niva(character):
            options.append(
                ("niva", f"Нива ×{TRAVEL_SPEED_NIVA:g}", float(TRAVEL_SPEED_NIVA), TRAVEL_ENERGY_NIVA)
            )
        elif bound == "truck" and can_travel_by_truck(character):
            options.append(
                (
                    "truck",
                    f"Грузовик ×{TRAVEL_SPEED_TRUCK:g}",
                    float(TRAVEL_SPEED_TRUCK),
                    TRAVEL_ENERGY_TRUCK,
                )
            )
        return options or [("foot", "Пешком ×1 (техника недоступна)", float(TRAVEL_SPEED_FOOT), TRAVEL_ENERGY_FOOT)]

    options = [
        ("foot", "Пешком ×1", float(TRAVEL_SPEED_FOOT), TRAVEL_ENERGY_FOOT),
    ]
    if can_travel_by_bicycle(character):
        options.append(
            (
                "bicycle",
                f"Велосипед ×{TRAVEL_SPEED_BICYCLE:g}",
                float(TRAVEL_SPEED_BICYCLE),
                TRAVEL_ENERGY_BICYCLE,
            )
        )
    if can_travel_by_niva(character):
        options.append(
            ("niva", f"Нива ×{TRAVEL_SPEED_NIVA:g}", float(TRAVEL_SPEED_NIVA), TRAVEL_ENERGY_NIVA)
        )
    if can_travel_by_truck(character):
        options.append(
            (
                "truck",
                f"Грузовик ×{TRAVEL_SPEED_TRUCK:g}",
                float(TRAVEL_SPEED_TRUCK),
                TRAVEL_ENERGY_TRUCK,
            )
        )
    return options


def _resolve_travel_transport(
    character: Character,
    preferred_mode: str | None = None,
    *,
    bound_transport: str | None = None,
) -> tuple[str, float, int, str | None]:
    """Выбрать режим: preferred если доступен, иначе самый быстрый из доступных."""
    options = list_available_travel_modes(character, bound_transport=bound_transport)
    by_mode = {mode: (mode, speed, energy) for mode, _label, speed, energy in options}
    notes: list[str] = []
    if character.truck_owned and character.truck_durability > 0 and character.diesel <= 0:
        notes.append("Нет дизеля — грузовик недоступен.")
    elif character.truck_owned and character.truck_durability <= 0:
        notes.append("Грузовик сломан.")
    if character.niva_owned and character.niva_durability > 0 and character.gasoline <= 0:
        notes.append("Нет бензина — Нива недоступна.")
    elif character.niva_owned and character.niva_durability <= 0:
        notes.append("Нива сломана.")
    if bound_transport in ("niva", "truck"):
        notes.append(
            f"Ты за рулём {_vehicle_label_for_key(bound_transport)} — только на ней/нём."
        )
    foot_note = " ".join(notes) if notes else None

    if preferred_mode:
        picked = by_mode.get(preferred_mode)
        if picked is None:
            fallback = "foot"
            if bound_transport in by_mode:
                fallback = bound_transport
            fb_mode, fb_speed, fb_energy = by_mode.get(
                fallback,
                (fallback, float(TRAVEL_SPEED_FOOT), TRAVEL_ENERGY_FOOT),
            )
            return fb_mode, fb_speed, fb_energy, (
                f"Режим «{preferred_mode}» недоступен." + (f" {foot_note}" if foot_note else "")
            )
        mode, speed, energy = picked
        return mode, speed, energy, foot_note

    # Автовыбор: самый быстрый (для смоков/фолбэка).
    mode, _label, speed, energy = max(options, key=lambda row: row[2])
    return mode, speed, energy, foot_note


def _compute_base_travel_minutes(
    origin: str,
    destination: str,
    locations: dict[str, dict[str, Any]],
    faction: str | None,
) -> tuple[int, float | None]:
    travel_minutes = 30
    distance_px: float | None = None
    current_point = MAP_TRAVEL_POINTS.get(origin)
    destination_point = MAP_TRAVEL_POINTS.get(destination)
    if current_point and destination_point:
        distance_px = dist(current_point, destination_point)
        travel_minutes = max(10, round(distance_px / 8))
    target = locations.get(destination) or {}
    if target.get("point_type") == "точка интереса" and target.get("controlled_by") == faction:
        travel_minutes = max(5, int(travel_minutes * 0.7))
    return travel_minutes, distance_px


def roll_arrival_encounter(storage: Storage, telegram_id: int, destination: str) -> str | None:
    """Мелкий энкаунтер по прибытии (~20% шанс чего-то случиться)."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or _is_dead(player):
        return None
    roll = random.randint(1, 100)
    if roll <= 80:
        return None
    if roll <= 88:
        amount = random.randint(20, 40)
        storage.change_money(telegram_id, amount)
        storage.add_player_stat(telegram_id, "money_earned", amount)
        return f"📦 По дороге нашёл хабар: +{amount} RU."
    if roll <= 93:
        storage.add_item(telegram_id, "bread", 1)
        return "🍞 Нашёл чёрствый хлеб у дороги."
    if roll <= 97:
        raw_loss = random.randint(1, 3)
        loss = apply_incoming_damage(raw_loss, player, min_damage=1)
        storage.change_health(telegram_id, -loss)
        return f"⚠️ Лёгкая засада мутантов: −{loss} HP."
    rad = random.randint(1, 2)
    storage.adjust_survival(telegram_id, radiation_delta=rad)
    return f"☢️ Аномальный фон на подходе к «{destination}»: +{rad} рад."


def travel_status_with_smuggle(storage: Storage, telegram_id: int) -> str | None:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return None
    base = travel_status_text(player)
    active = get_active_smuggling(storage, telegram_id)
    from app.smuggle_mission import get_smuggle_session

    grid = get_smuggle_session(storage, telegram_id)
    if base is None and active is None and grid is None:
        return None
    parts: list[str] = []
    if base:
        parts.append(base)
    if grid is not None:
        parts.append(
            f"🚚 Контрабанда → «{grid.destination}» "
            f"(маршрут {grid.route_index}/{len(grid.route)}, шанс ~{grid.success_chance}%)."
        )
    elif active and is_traveling(player):
        chance = int(active.get("success_chance") or 0)
        dest = str(active.get("destination") or player.travel_destination or "?")
        parts.append(f"🚚 Контрабанда → «{dest}» (шанс сдачи ~{chance}%).")
    return "\n".join(parts) if parts else None


def travel_to(
    storage: Storage,
    telegram_id: int,
    destination: str,
    *,
    transport_mode: str | None = None,
) -> ActionResult:
    character = storage.get_character(telegram_id)
    if character is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(character):
        return ActionResult(False, _dead_block_text())
    if is_traveling(character):
        return ActionResult(False, travel_block_text(character) or "Ты уже в пути.")
    from app.player_busy import player_busy_reason

    busy = player_busy_reason(storage, telegram_id)
    if busy:
        return ActionResult(False, busy)
    if character.location == destination:
        return ActionResult(False, f"Ты уже находишься в локации «{destination}».")

    from app.special_events import travel_blocked_by_special_event

    storm_block = travel_blocked_by_special_event(
        storage,
        from_location=str(character.location),
        to_location=str(destination),
    )
    if storm_block:
        return ActionResult(False, storm_block)

    locations = {loc["name"]: loc for loc in storage.get_locations()}
    if destination not in locations:
        return ActionResult(False, "Такой локации нет.")

    from app.monolith_war import MONOLITH_BASE, MONOLITH_FACTION

    if destination == MONOLITH_BASE and character.faction != MONOLITH_FACTION:
        return ActionResult(
            False,
            f"«{MONOLITH_BASE}» — закрытая база Монолита. Чужим вход запрещён.",
        )

    # Базы группировок: чужим вход не запрещён — это вражеская локация,
    # и на ней сработает блокпост (см. location_requires_blockpost).

    bound_transport = storage.get_bound_transport(telegram_id)
    if bound_transport in ("niva", "truck"):
        vehicle_label = _vehicle_label_for_key(bound_transport)
        if transport_mode in ("foot", "bicycle") or (
            transport_mode is not None and transport_mode != bound_transport
        ):
            return ActionResult(
                False,
                f"Ты за рулём {vehicle_label} — пешком или на другом транспорте не уйти. "
                "Сдай технику в гараж, когда закончишь.",
            )
        if transport_mode is None:
            transport_mode = bound_transport

    if transport_mode is not None:
        available = {
            mode
            for mode, *_rest in list_available_travel_modes(
                character, bound_transport=bound_transport
            )
        }
        if transport_mode not in available:
            labels = {
                "truck": "Недостаточно дизеля или грузовик недоступен.",
                "niva": "Недостаточно бензина или Нива недоступна.",
                "bicycle": "У тебя нет велосипеда.",
                "foot": "Пеший переход недоступен.",
            }
            return ActionResult(False, labels.get(transport_mode, "Этот транспорт недоступен."))

    picked_mode, speed_mult, energy_cost, foot_note = _resolve_travel_transport(
        character,
        preferred_mode=transport_mode,
        bound_transport=bound_transport,
    )
    transport_mode = picked_mode
    if transport_mode == "truck" and not can_travel_by_truck(character):
        return ActionResult(False, "Недостаточно дизеля для поездки на грузовике.")
    if transport_mode == "niva" and not can_travel_by_niva(character):
        return ActionResult(False, "Недостаточно бензина для поездки на Ниве.")
    if transport_mode == "bicycle" and not can_travel_by_bicycle(character):
        return ActionResult(False, "У тебя нет велосипеда.")

    base_minutes, distance_px = _compute_base_travel_minutes(
        character.location,
        destination,
        locations,
        character.faction,
    )
    travel_minutes = max(1, int(round(base_minutes / speed_mult)))
    travel_minutes, n2o_note = apply_n2o_to_travel_minutes(storage, telegram_id, travel_minutes)
    real_seconds = travel_minutes * TRAVEL_REAL_SECONDS_PER_GAME_MINUTE
    arrives_at = _utc_now() + timedelta(seconds=real_seconds)

    if not storage.spend_energy(telegram_id, energy_cost):
        return ActionResult(False, f"Не хватает энергии для перехода (нужно {energy_cost}).")

    vehicle_wear_text = ""
    fuel_text = ""
    if transport_mode == "truck":
        if not storage.change_diesel(telegram_id, -1):
            storage.restore_energy(telegram_id, energy_cost)
            return ActionResult(False, "Не удалось списать дизель, переход отменён.")
        fuel_text = "\nДизель: −1."
        wear = _compute_truck_wear(distance_px, travel_minutes)
        durability = storage.apply_truck_wear(telegram_id, wear)
        if durability is None:
            durability = max(0, int(character.truck_durability) - wear)
        if durability <= 0:
            vehicle_wear_text = f"\nГрузовик изношен на {wear}% и окончательно сломан."
        else:
            vehicle_wear_text = f"\nИзнос грузовика: -{wear}% (прочность: {durability}%)."
    elif transport_mode == "niva":
        if not storage.change_gasoline(telegram_id, -1):
            storage.restore_energy(telegram_id, energy_cost)
            return ActionResult(False, "Не удалось списать бензин, переход отменён.")
        fuel_text = "\nБензин: −1."
        niva_wear = _compute_niva_wear(distance_px, travel_minutes)
        niva_durability = storage.apply_niva_wear(telegram_id, niva_wear)
        if niva_durability is None:
            niva_durability = max(0, int(character.niva_durability) - niva_wear)
        if niva_durability <= 0:
            vehicle_wear_text = f"\nНива изношена на {niva_wear}% и окончательно сломана."
        else:
            vehicle_wear_text = f"\nИзнос Нивы: -{niva_wear}% (прочность: {niva_durability}%)."

    _clear_n2o_trip_boost(storage, telegram_id)
    storage.start_travel(telegram_id, destination, arrives_at, transport_mode)
    if n2o_note:
        _mark_n2o_trip_used(storage, telegram_id)
    if transport_mode in ("niva", "truck"):
        storage.set_bound_transport(telegram_id, transport_mode)
    transport_labels = {
        "foot": "пешком",
        "bicycle": f"на велосипеде (×{TRAVEL_SPEED_BICYCLE:g})",
        "niva": f"на Ниве (×{TRAVEL_SPEED_NIVA:g})",
        "truck": f"на грузовике (×{TRAVEL_SPEED_TRUCK:g})",
    }
    note_text = f"\n{foot_note}" if foot_note else ""
    return ActionResult(
        True,
        f"Выехал из «{character.location}» → «{destination}» {transport_labels[transport_mode]}.\n"
        f"Затрачено энергии: {energy_cost}."
        f"{fuel_text}{vehicle_wear_text}{note_text}{n2o_note}",
    )


def build_alliance_overview(storage: Storage, telegram_id: int) -> str:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return "Союзы доступны только после выбора группировки."
    leader_id = storage.get_faction_leader_id(player.faction)
    role_text = (
        "Ты лидер группировки."
        if leader_id is not None and leader_id == telegram_id
        else "Ты не лидер группировки."
    )
    allies = storage.list_faction_alliances(player.faction)
    if not allies:
        return f"Союзы {player.faction}: нет активных союзов.\n{role_text}"
    return (
        f"Союзы {player.faction} ({len(allies)}/{MAX_FACTION_ALLIANCES}): "
        + ", ".join(sorted(allies))
        + f"\n{role_text}"
    )


def propose_alliance(storage: Storage, telegram_id: int, target_faction: str) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if player.faction is None:
        return ActionResult(False, "Сначала выбери группировку.")
    if not target_faction or target_faction == player.faction:
        return ActionResult(False, "Нельзя заключить союз с собственной группировкой.")
    if storage.get_faction_leader_id(player.faction) != telegram_id:
        return ActionResult(False, "Заключать договор может только лидер твоей группировки.")
    target_leader_id = storage.get_faction_leader_id(target_faction)
    if target_leader_id is None:
        return ActionResult(False, f"У группировки {target_faction} не назначен лидер.")
    if storage.are_factions_allied(player.faction, target_faction):
        return ActionResult(False, f"Союз с {target_faction} уже активен.")
    if len(storage.list_faction_alliances(player.faction)) >= MAX_FACTION_ALLIANCES:
        return ActionResult(False, f"Можно иметь максимум {MAX_FACTION_ALLIANCES} союзов.")
    if not storage.create_alliance_request(player.faction, target_faction, telegram_id):
        return ActionResult(False, "Предложение уже отправлено или не удалось создать заявку.")
    notify: list[tuple[int, str]] = []
    if target_leader_id is not None:
        notify.append(
            (
                int(target_leader_id),
                f"🤝 {h(player.faction)} предлагает союз.\n"
                f"Открой «⚔️ Война» → «📘 Правила и дипломатия» → «Подтвердить входящий договор».",
            ),
        )
    return ActionResult(
        True,
        f"Предложение союза отправлено в {h(target_faction)}.\n"
        f"Лидер {h(target_faction)} должен подтвердить договор.",
        payload={"notify": notify} if notify else None,
    )


def accept_alliance(storage: Storage, telegram_id: int, from_faction: str) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if player.faction is None:
        return ActionResult(False, "Сначала выбери группировку.")
    if storage.get_faction_leader_id(player.faction) != telegram_id:
        return ActionResult(False, "Подтверждать договор может только лидер твоей группировки.")
    if storage.get_faction_leader_id(from_faction) is None:
        return ActionResult(False, f"У группировки {from_faction} не назначен лидер.")
    if not from_faction or from_faction == player.faction:
        return ActionResult(False, "Некорректная группировка для подтверждения.")
    if storage.are_factions_allied(player.faction, from_faction):
        return ActionResult(False, f"Союз с {from_faction} уже активен.")
    if len(storage.list_faction_alliances(player.faction)) >= MAX_FACTION_ALLIANCES:
        return ActionResult(False, f"Можно иметь максимум {MAX_FACTION_ALLIANCES} союзов.")
    if len(storage.list_faction_alliances(from_faction)) >= MAX_FACTION_ALLIANCES:
        return ActionResult(False, f"У группировки {from_faction} уже максимум союзов.")
    incoming = storage.list_incoming_alliance_requests(player.faction)
    has_offer = any(str(row.get("requester_faction", "")) == from_faction for row in incoming)
    if not has_offer:
        return ActionResult(False, "Активного предложения на союз не найдено.")
    if not storage.set_faction_alliance(from_faction, player.faction, allied=True):
        return ActionResult(False, "Не удалось подтвердить союз.")
    storage.remove_alliance_request(from_faction, player.faction)
    requester_leader_id = storage.get_faction_leader_id(from_faction)
    notify: list[tuple[int, str]] = []
    if requester_leader_id is not None:
        notify.append(
            (
                int(requester_leader_id),
                f"✅ {h(player.faction)} приняла союз с {h(from_faction)}.",
            ),
        )
    return ActionResult(
        True,
        f"Договор о союзе между {h(from_faction)} и {h(player.faction)} заключен.",
        payload={"notify": notify} if notify else None,
    )


def break_alliance(storage: Storage, telegram_id: int, target_faction: str) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if player.faction is None:
        return ActionResult(False, "Сначала выбери группировку.")
    if not target_faction or target_faction == player.faction:
        return ActionResult(False, "Нельзя разорвать союз с собственной группировкой.")
    if storage.get_faction_leader_id(player.faction) != telegram_id:
        return ActionResult(False, "Разрывать союз может только лидер твоей группировки.")
    if not storage.are_factions_allied(player.faction, target_faction):
        return ActionResult(False, f"Союза с {target_faction} сейчас нет.")
    if not storage.set_faction_alliance(player.faction, target_faction, allied=False):
        return ActionResult(False, "Не удалось разорвать союз.")
    target_leader_id = storage.get_faction_leader_id(target_faction)
    notify: list[tuple[int, str]] = []
    if target_leader_id is not None:
        notify.append(
            (
                int(target_leader_id),
                f"⚠️ {h(player.faction)} разорвала союз с {h(target_faction)}.",
            ),
        )
    return ActionResult(
        True,
        f"Союз между {h(player.faction)} и {h(target_faction)} разорван.",
        payload={"notify": notify} if notify else None,
    )


def list_war_enemy_factions(storage: Storage, faction: str) -> list[str]:
    """Враждебные группировки для рейдов на склад/гараж.

    Войны в игре не хранятся отдельной таблицей: `declare_war` лишь разрывает союз
    и шлёт уведомление. Поэтому признак вражды — отсутствие действующего союза
    (любая группировка, которая не является собой и не в списке союзников).
    """
    if not faction:
        return []
    allies = set(storage.list_faction_alliances(faction))
    enemies: list[str] = []
    for row in storage.get_factions():
        name = str(row.get("name") or "")
        if not name or name == faction or name in allies:
            continue
        enemies.append(name)
    return enemies


def declare_war(storage: Storage, telegram_id: int, target_faction: str) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if player.faction is None:
        return ActionResult(False, "Сначала выбери группировку.")
    if not target_faction or target_faction == player.faction:
        return ActionResult(False, "Нельзя объявить войну собственной группировке.")
    if storage.get_faction_leader_id(player.faction) != telegram_id:
        return ActionResult(False, "Объявлять войну может только лидер твоей группировки.")
    if storage.get_faction_leader_id(target_faction) is None:
        return ActionResult(False, f"У группировки {target_faction} не назначен лидер.")
    target_leader_id = storage.get_faction_leader_id(target_faction)
    war_notify: list[tuple[int, str]] = []
    if target_leader_id is not None:
        war_notify.append(
            (
                int(target_leader_id),
                f"⚔️ {h(player.faction)} объявила войну {h(target_faction)}!",
            ),
        )
    had_alliance = storage.are_factions_allied(player.faction, target_faction)
    storage.remove_alliance_request(player.faction, target_faction)
    storage.remove_alliance_request(target_faction, player.faction)
    if had_alliance:
        if not storage.set_faction_alliance(player.faction, target_faction, allied=False):
            return ActionResult(False, "Не удалось объявить войну: ошибка смены дипломатии.")
        return ActionResult(
            True,
            f"{h(player.faction)} объявила войну {h(target_faction)}.\nСоюз разорван в одностороннем порядке.",
            payload={"notify": war_notify} if war_notify else None,
        )
    return ActionResult(
        True,
        f"{h(player.faction)} объявила войну {h(target_faction)}.\nПодтверждение второй стороны не требуется.",
        payload={"notify": war_notify} if war_notify else None,
    )


def attack_location(storage: Storage, telegram_id: int, location_name: str) -> ActionResult:
    """Захват нейтральных точек — группа от 2 бойцов; занятые — только военное лобби."""
    loc = storage.get_location(location_name)
    if loc is None:
        return ActionResult(False, "Локация не найдена.")
    if loc.get("controlled_by"):
        return ActionResult(
            False,
            f"Соло-штурм занятой точки отключён. Собери военное лобби минимум из "
            f"{WAR_MIN_FACTION_MEMBERS} бойцов (раздел «⚔️ Война» → «🪖 Военные лобби»).",
        )
    from app.neutral_capture import create_or_join_ncap_lobby

    return create_or_join_ncap_lobby(storage, telegram_id, location_name)


def _weapon_rating(weapon_name: str) -> int:
    return WEAPON_RATING_BY_NAME.get(weapon_name, 1)


def _armor_rating(armor_name: str) -> int:
    return ARMOR_RATING_BY_NAME.get(armor_name, 1)


def _telegram_button_title(text: str, *, limit: int = 64) -> str:
    raw = " ".join(str(text).split())
    if len(raw) <= limit:
        return raw
    return raw[: max(1, limit - 1)].rstrip() + "…"


def shop_consumable_button_title(item_key: str, *, label: str | None = None) -> str:
    """Подпись кнопки расходника: имя (от цена)."""
    key = normalize_shop_item_key(item_key)
    item = SHOP_ITEMS.get(key)
    if item is None:
        return label or key
    name = label or str(item["name"])
    price = int(item["buy_price"])
    return _telegram_button_title(f"{name} (от {price})")


def shop_weapon_button_title(item_key: str) -> str:
    """Подпись кнопки оружия: имя · сила · дальность · цена."""
    from app.tactical_combat import weapon_shoot_range

    key = normalize_shop_item_key(item_key)
    item = WEAPON_CATALOG.get(key) or SHOP_ITEMS.get(key)
    if item is None:
        return key
    name = str(item["name"])
    price = int(item["buy_price"])
    power = _weapon_rating(name)
    shoot_range = weapon_shoot_range(name)
    return _telegram_button_title(f"{name} · сила {power} · д.{shoot_range} · {price}")


def shop_armor_button_title(item_key: str) -> str:
    """Подпись кнопки брони: имя · цена · сила · смягчение · блок%.

    Цена сразу после имени — иначе на узких клиентах обрезается хвост кнопки.
    """
    key = normalize_shop_item_key(item_key)
    item = ARMOR_CATALOG.get(key) or SHOP_ITEMS.get(key)
    if item is None:
        return key
    name = str(item["name"])
    price = int(item["buy_price"])
    power = _armor_rating(name)
    mit = int(ARMOR_MITIGATION_BY_NAME.get(name, 0))
    block = int(ARMOR_BLOCK_CHANCE_BY_NAME.get(name, 0))
    return _telegram_button_title(
        f"{name} · {price} · сила {power} · −{mit} · б{block}%"
    )


def shop_gear_button_title(item_key: str) -> str:
    """Подпись кнопки прочего: транспорт / детектор / спальник / тайник."""
    key = normalize_shop_item_key(item_key)
    item = SHOP_ITEMS.get(key)
    if item is None:
        return key
    name = str(item["name"])
    price = int(item["buy_price"])
    detector_chance = next((base for dkey, _label, base in ARTIFACT_DETECTORS if dkey == key), None)
    if detector_chance is not None:
        short = name.replace("Детектор ", "").replace("«", "").replace("»", "")
        return _telegram_button_title(f"{short} · арт.{detector_chance}% · {price}")
    if key == "bicycle":
        return _telegram_button_title(
            f"Велосипед · ×{TRAVEL_SPEED_BICYCLE:g} · нагр.×{TRANSPORT_QUEST_REWARD_MULT['bicycle']:g} · {price}"
        )
    if key == "niva":
        return _telegram_button_title(
            f"Нива · ×{TRAVEL_SPEED_NIVA:g} · нагр.×{TRANSPORT_QUEST_REWARD_MULT['niva']:g} · бензин · {price}"
        )
    if key == "truck":
        return _telegram_button_title(
            f"Грузовик · ×{TRAVEL_SPEED_TRUCK:g} · нагр.×{TRANSPORT_QUEST_REWARD_MULT['truck']:g} · дизель · {price}"
        )
    if key == "sleeping_bag":
        return _telegram_button_title(f"Спальник · энергия ×2 · {price}")
    if key == "stash_case":
        return _telegram_button_title(f"Тайник · от {price}")
    if key == "stash_coordinates":
        return _telegram_button_title(f"Координаты хабара · {price}")
    return _telegram_button_title(f"{name} · {price}")


def _safe_fromiso(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)


def _active_location_event_modifier(storage: Storage, location_name: str) -> int:
    now = datetime.now(timezone.utc)
    modifier = 0
    for event in storage.get_map_events():
        if str(event.get("location")) != location_name:
            continue
        expires_at = _safe_fromiso(str(event.get("expires_at", "")))
        if expires_at > now:
            modifier += int(event.get("modifier", 0))
    return modifier


def _location_is_friendly_to_faction(
    storage: Storage,
    location: dict[str, Any],
    faction: str,
) -> bool:
    owner = str(location.get("controlled_by") or "")
    if not owner:
        return False
    if owner == faction:
        return True
    return storage.are_factions_allied(faction, owner)


def list_assaultable_locations(storage: Storage, faction: str) -> list[dict[str, Any]]:
    return [
        loc
        for loc in storage.get_locations()
        if not _location_is_friendly_to_faction(storage, loc, faction)
    ]


def _refund_spent_energy(storage: Storage, telegram_ids: list[int], amount: int) -> None:
    for telegram_id in telegram_ids:
        storage.restore_energy(telegram_id, amount)


def resolve_open_raid_kind(open_raid: dict[str, Any]) -> str:
    """Определить тип открытого рейда: lair, warehouse или garage.

    Старые записи могли сохранить raid_kind='lair' для рейдов на склад/гараж —
    тогда ориентируемся на target_faction и подпись location.
    """
    kind = str(open_raid.get("raid_kind") or "lair").strip().lower()
    if kind in DEPOT_RAID_KINDS:
        return kind
    target = str(open_raid.get("target_faction") or "").strip()
    location = str(open_raid.get("location") or "")
    if target:
        if location.startswith("Склад"):
            return "warehouse"
        if location.startswith("Гараж"):
            return "garage"
    return "lair"


def _faction_raid_in_progress(storage: Storage, faction: str) -> dict[str, Any] | None:
    return storage.get_in_progress_raid_for_faction(faction)


def _raid_join_notify(
    storage: Storage,
    raid_id: int,
    joiner_id: int,
    *,
    raid_label: str,
    joined_now: bool,
) -> list[list[Any]]:
    if not joined_now:
        return []
    joiner = storage.get_character(joiner_id, refresh_energy=False)
    joiner_name = h(joiner.nickname) if joiner else str(joiner_id)
    member_ids = storage.get_raid_member_ids(raid_id)
    members_line = f"Состав: {len(member_ids)}/{RAID_MAX_MEMBERS}."
    notify: list[list[Any]] = [
        [
            joiner_id,
            f"✅ Ты присоединился к рейду #{raid_id} ({raid_label}).\n{members_line}",
        ]
    ]
    others_note = (
        f"👥 {joiner_name} присоединился к рейду #{raid_id} ({raid_label}).\n{members_line}"
    )
    notify.extend([[pid, others_note] for pid in member_ids if pid != joiner_id])
    return notify


def create_or_join_faction_raid(storage: Storage, telegram_id: int, location_name: str) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    if player.faction is None:
        return ActionResult(False, "Сначала выбери группировку.")

    location = storage.get_location(location_name)
    if location is None:
        return ActionResult(False, "Локация для рейда не найдена.")
    if str(location.get("point_type") or "") == "база":
        return ActionResult(
            False,
            "Базы штурмуются только через военное лобби (раздел «⚔️ Война»).",
        )
    if str(location.get("point_type") or "") != "точка ресурсов":
        return ActionResult(
            False,
            "Рейд на логово возможен только на точках ресурсов. "
            "Захват других локаций — через ⚔️ Войну или захват нейтрали.",
        )
    if _location_is_friendly_to_faction(storage, location, player.faction):
        return ActionResult(False, "Нельзя рейдить свою или союзническую точку.")

    open_raid = storage.get_open_raid_for_faction(player.faction)
    if open_raid is None:
        for ally in storage.list_faction_alliances(player.faction):
            ally_open = storage.get_open_raid_for_faction(ally)
            if ally_open is not None and str(ally_open["location"]) == location_name:
                open_raid = ally_open
                break
    if open_raid is None:
        active = _faction_raid_in_progress(storage, player.faction)
        if active is not None:
            return ActionResult(
                False,
                f"Группировка уже в тактическом рейде #{active['id']}. "
                "Нажми «🚀 Запустить рейд», чтобы открыть карту.",
            )
        raid_id = storage.create_raid(player.faction, location_name, telegram_id)
        return ActionResult(
            True,
            f"Создан рейд #{raid_id} на точку ресурсов «{location_name}».\n"
            "Позови товарищей по группировке и нажми «Запустить».",
        )

    if str(open_raid["location"]) != location_name:
        return ActionResult(
            False,
            f"У твоей группировки уже есть открытый рейд #{open_raid['id']} на «{open_raid['location']}».\n"
            "Сначала запусти или закрой его.",
        )

    raid_id = int(open_raid["id"])
    host_faction = str(open_raid["faction"])
    member_faction = player.faction
    if not storage.are_factions_allied(host_faction, member_faction) and host_faction != member_faction:
        return ActionResult(False, "К рейду можно присоединяться только своей группировкой или союзниками.")
    member_ids = storage.get_raid_member_ids(raid_id)
    joined_now = False
    if telegram_id not in member_ids:
        if len(member_ids) >= RAID_MAX_MEMBERS:
            return ActionResult(False, f"В рейде уже максимум {RAID_MAX_MEMBERS} бойцов.")
        if not storage.add_raid_member(raid_id, telegram_id):
            return ActionResult(False, "Не удалось присоединиться к рейду.")
        joined_now = True
        member_ids = storage.get_raid_member_ids(raid_id)
    notify = _raid_join_notify(
        storage,
        raid_id,
        telegram_id,
        raid_label=f"логово «{location_name}»",
        joined_now=joined_now,
    )
    return ActionResult(
        True,
        f"Ты в составе рейда #{raid_id} на «{location_name}».\n"
        f"Состав рейда: {len(member_ids)}/{RAID_MAX_MEMBERS} бойцов.",
        payload={"notify": notify} if notify else None,
    )


def _depot_has_loot(storage: Storage, target_faction: str, depot: str) -> bool:
    if depot == "warehouse":
        warehouse = storage.get_faction_warehouse(target_faction)
        return any(int(amount or 0) > 0 for amount in warehouse.values())
    garage = get_faction_garage(storage, target_faction)
    return any(int(garage.get(key, 0) or 0) > 0 for key in ("gasoline", "diesel", "niva", "truck"))


def create_or_join_depot_raid(
    storage: Storage,
    telegram_id: int,
    target_faction: str,
    depot: str = "warehouse",
) -> ActionResult:
    """Рейд отрядом своей группировки на склад или гараж вражеской группировки."""
    if depot not in DEPOT_RAID_KINDS:
        return ActionResult(False, "Неизвестный тип рейда на склад/гараж.")
    label = DEPOT_RAID_LABELS[depot]
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    if player.faction is None:
        return ActionResult(False, "Сначала выбери группировку.")

    target_faction = str(target_faction or "").strip()
    if not target_faction or target_faction == player.faction:
        return ActionResult(False, f"Нельзя рейдить {label} собственной группировки.")
    if storage.get_faction_leader_id(target_faction) is None:
        return ActionResult(False, f"Группировка «{target_faction}» не найдена.")
    if target_faction not in list_war_enemy_factions(storage, player.faction):
        return ActionResult(
            False,
            f"Рейд на {label} возможен только против враждебной группировки.\n"
            f"С «{target_faction}» сейчас союз или мир — сначала разорви союз/объяви войну "
            "(раздел «⚔️ Война» → «📘 Правила и дипломатия»).",
        )
    if not _depot_has_loot(storage, target_faction, depot):
        return ActionResult(False, f"На {label}е группировки «{target_faction}» сейчас нечего красть.")

    open_raid = storage.get_open_raid_for_faction(player.faction)
    if open_raid is None:
        for ally in storage.list_faction_alliances(player.faction):
            ally_open = storage.get_open_raid_for_faction(ally)
            if (
                ally_open is not None
                and resolve_open_raid_kind(ally_open) == depot
                and str(ally_open.get("target_faction") or "") == target_faction
            ):
                open_raid = ally_open
                break

    if open_raid is None:
        active = _faction_raid_in_progress(storage, player.faction)
        if active is not None:
            return ActionResult(
                False,
                f"Группировка уже в тактическом рейде #{active['id']}. "
                "Нажми «🚀 Запустить рейд», чтобы открыть карту.",
            )
        location_label = f"{'Склад' if depot == 'warehouse' else 'Гараж'} «{target_faction}»"
        raid_id = storage.create_raid(
            player.faction,
            location_label,
            telegram_id,
            raid_kind=depot,
            target_faction=target_faction,
        )
        return ActionResult(
            True,
            f"Создан рейд #{raid_id} на {label} группировки «{target_faction}».\n"
            "Позови товарищей по группировке и нажми «Запустить».",
        )

    open_kind = resolve_open_raid_kind(open_raid)
    open_target = str(open_raid.get("target_faction") or "")
    if open_kind != depot or open_target != target_faction:
        current_label = DEPOT_RAID_LABELS.get(open_kind, "логово")
        current_target = f" ({open_target})" if open_target else ""
        return ActionResult(
            False,
            f"У твоей группировки уже есть открытый рейд #{open_raid['id']} на {current_label}{current_target}.\n"
            "Сначала запусти или закрой его.",
        )

    raid_id = int(open_raid["id"])
    host_faction = str(open_raid["faction"])
    if not storage.are_factions_allied(host_faction, player.faction) and host_faction != player.faction:
        return ActionResult(False, "К рейду можно присоединяться только своей группировкой или союзниками.")
    member_ids = storage.get_raid_member_ids(raid_id)
    joined_now = False
    if telegram_id not in member_ids:
        if len(member_ids) >= RAID_MAX_MEMBERS:
            return ActionResult(False, f"В рейде уже максимум {RAID_MAX_MEMBERS} бойцов.")
        if not storage.add_raid_member(raid_id, telegram_id):
            return ActionResult(False, "Не удалось присоединиться к рейду.")
        joined_now = True
        member_ids = storage.get_raid_member_ids(raid_id)
    notify = _raid_join_notify(
        storage,
        raid_id,
        telegram_id,
        raid_label=f"{label} «{target_faction}»",
        joined_now=joined_now,
    )
    return ActionResult(
        True,
        f"Ты в составе рейда #{raid_id} на {label} группировки «{target_faction}».\n"
        f"Состав рейда: {len(member_ids)}/{RAID_MAX_MEMBERS} бойцов.",
        payload={"notify": notify} if notify else None,
    )


def _steal_faction_warehouse(storage: Storage, target_faction: str, attacker_faction: str) -> list[str]:
    warehouse = storage.get_faction_warehouse(target_faction)
    lines: list[str] = []
    for item_key, amount in sorted(warehouse.items()):
        amount = int(amount or 0)
        if amount <= 0:
            continue
        percent = random.randint(DEPOT_RAID_MIN_LOOT_PERCENT, DEPOT_RAID_MAX_LOOT_PERCENT)
        stolen = min(amount, max(1, (amount * percent) // 100))
        if stolen <= 0:
            continue
        storage.change_faction_warehouse_item(target_faction, item_key, -stolen)
        storage.change_faction_warehouse_item(attacker_faction, item_key, stolen)
        label = ITEM_LABELS.get(item_key, item_key)
        lines.append(f"• {label}: −{stolen} у «{target_faction}» → +{stolen} складу «{attacker_faction}»")
    return lines


def _steal_faction_garage(storage: Storage, target_faction: str, attacker_faction: str) -> list[str]:
    garage = get_faction_garage(storage, target_faction)
    attacker_garage = get_faction_garage(storage, attacker_faction)
    lines: list[str] = []
    for fuel_type, fuel_label in (("gasoline", "канистр бензина"), ("diesel", "канистр дизеля")):
        amount = int(garage.get(fuel_type, 0) or 0)
        if amount <= 0:
            continue
        percent = random.randint(DEPOT_RAID_MIN_LOOT_PERCENT, DEPOT_RAID_MAX_LOOT_PERCENT)
        stolen = min(amount, max(1, (amount * percent) // 100))
        if stolen <= 0:
            continue
        garage[fuel_type] = amount - stolen
        attacker_garage[fuel_type] = int(attacker_garage.get(fuel_type, 0) or 0) + stolen
        lines.append(f"• {fuel_label}: −{stolen} у «{target_faction}» → +{stolen} гаражу «{attacker_faction}»")

    for vehicle_key, durs_key, vehicle_label in (("niva", "niva_durs", "Нива"), ("truck", "truck_durs", "Грузовик")):
        count = int(garage.get(vehicle_key, 0) or 0)
        if count <= 0:
            continue
        if random.randint(1, 100) > DEPOT_RAID_VEHICLE_STEAL_CHANCE:
            continue
        durs = list(garage.get(durs_key) or [])
        dur = durs.pop(0) if durs else 100
        garage[durs_key] = durs
        owners_key = f"{vehicle_key}_owners"
        owners = [int(x) for x in (garage.get(owners_key) or [])]
        if owners:
            owners.pop(0)
            garage[owners_key] = owners
        garage[vehicle_key] = count - 1
        attacker_durs = list(attacker_garage.get(durs_key) or [])
        attacker_durs.append(dur)
        attacker_garage[durs_key] = attacker_durs
        attacker_garage[vehicle_key] = int(attacker_garage.get(vehicle_key, 0) or 0) + 1
        lines.append(
            f"• {vehicle_label} (прочность {dur}%): угнан у «{target_faction}» → в гараж «{attacker_faction}»"
        )

    _set_faction_garage(storage, target_faction, garage)
    _set_faction_garage(storage, attacker_faction, attacker_garage)
    return lines


def launch_open_raid(storage: Storage, telegram_id: int) -> RaidLaunchResult:
    leader = storage.get_character(telegram_id, refresh_energy=False)
    if leader is None:
        return RaidLaunchResult(False, "Сначала создай персонажа.", ())
    if _is_dead(leader):
        return RaidLaunchResult(False, _dead_block_text(), ())
    if leader.faction is None:
        return RaidLaunchResult(False, "Сначала выбери группировку.", ())

    open_raid = storage.get_open_raid_for_faction(leader.faction)
    if open_raid is None:
        return RaidLaunchResult(False, "У твоей группировки нет открытого рейда.", ())
    if int(open_raid["leader_id"]) != telegram_id:
        return RaidLaunchResult(False, "Запускать рейд может только лидер, который его создал.", ())

    raid_id = int(open_raid["id"])
    member_ids = storage.get_raid_member_ids(raid_id)
    if len(member_ids) < RAID_MIN_MEMBERS:
        return RaidLaunchResult(False, f"Для отрядного рейда нужно минимум {RAID_MIN_MEMBERS} игрока.", ())

    members = storage.get_characters_by_ids(member_ids)
    allowed_factions = {leader.faction, *storage.list_faction_alliances(leader.faction)}
    members = [member for member in members if member.faction in allowed_factions and member.health > 0]
    if len(members) < RAID_MIN_MEMBERS:
        return RaidLaunchResult(False, "Недостаточно бойцов с нормальным здоровьем для запуска рейда.", ())

    raid_kind = resolve_open_raid_kind(open_raid)
    raid_energy_cost = DEPOT_RAID_ENERGY_COST if raid_kind in DEPOT_RAID_KINDS else 18
    ready_members: list[Character] = []
    spent_ids: list[int] = []
    for member in members:
        if storage.spend_energy(member.telegram_id, raid_energy_cost):
            ready_members.append(member)
            spent_ids.append(member.telegram_id)
    if len(ready_members) < RAID_MIN_MEMBERS:
        _refund_spent_energy(storage, spent_ids, raid_energy_cost)
        return RaidLaunchResult(
            False,
            f"У бойцов не хватает энергии для начала рейда "
            f"(нужно {raid_energy_cost} каждому: логово — 18, склад/гараж — {DEPOT_RAID_ENERGY_COST}).",
            (),
        )

    if len(member_ids) > RAID_MAX_MEMBERS:
        _refund_spent_energy(storage, spent_ids, raid_energy_cost)
        return RaidLaunchResult(
            False,
            f"В рейде не более {RAID_MAX_MEMBERS} бойцов.",
            (),
        )

    member_id_list = [m.telegram_id for m in ready_members]
    from app.raid_grid import start_raid_grid

    if raid_kind in DEPOT_RAID_KINDS:
        target_faction = str(open_raid.get("target_faction") or "")
        label = DEPOT_RAID_LABELS.get(raid_kind, "склад")
        if not target_faction or target_faction == leader.faction:
            _refund_spent_energy(storage, spent_ids, raid_energy_cost)
            storage.finish_raid(raid_id, status="cancelled", result_text="Некорректная цель рейда.")
            return RaidLaunchResult(False, f"Рейд #{raid_id} отменён: некорректная цель.", tuple(member_ids))
        if target_faction not in list_war_enemy_factions(storage, leader.faction):
            _refund_spent_energy(storage, spent_ids, raid_energy_cost)
            return RaidLaunchResult(
                False,
                f"Рейд #{raid_id} отменён: с «{target_faction}» заключен мир/союз.",
                tuple(member_ids),
            )
        if not _depot_has_loot(storage, target_faction, raid_kind):
            _refund_spent_energy(storage, spent_ids, raid_energy_cost)
            storage.finish_raid(raid_id, status="failed", result_text=f"{label.capitalize()} цели уже пуст.")
            return RaidLaunchResult(
                False,
                f"Рейд #{raid_id} отменён: {label} группировки «{target_faction}» уже пуст.",
                tuple(member_ids),
            )
        home_location_name = FACTION_HOME_BASE.get(target_faction)
        base_power = 60
        if home_location_name:
            home_location = storage.get_location(home_location_name)
            if home_location is not None:
                base_power = int(home_location["npc_power"])
        depot_power = max(12, int(base_power * DEPOT_RAID_DEFENSE_POWER_RATIO))
        location_label = f"{'Склад' if raid_kind == 'warehouse' else 'Гараж'} «{target_faction}»"
        tactical_result, rgrid_session = start_raid_grid(
            storage,
            raid_id=raid_id,
            raid_kind=raid_kind,
            location_label=location_label,
            attacker_faction=leader.faction,
            player_ids=member_id_list,
            target_faction=target_faction,
            enemy_power=depot_power,
            energy_cost=raid_energy_cost,
        )
    else:
        location_name = str(open_raid["location"])
        location = storage.get_location(location_name)
        if location is None:
            _refund_spent_energy(storage, spent_ids, raid_energy_cost)
            return RaidLaunchResult(False, "Локация рейда недоступна.", ())
        if str(location.get("point_type") or "") == "база":
            _refund_spent_energy(storage, spent_ids, raid_energy_cost)
            return RaidLaunchResult(
                False,
                "Базы штурмуются только через военное лобби.",
                (),
            )
        if str(location.get("point_type") or "") != "точка ресурсов":
            _refund_spent_energy(storage, spent_ids, raid_energy_cost)
            return RaidLaunchResult(
                False,
                "Рейд возможен только на точках ресурсов. Захват локаций — через ⚔️ Войну.",
                (),
            )
        if _location_is_friendly_to_faction(storage, location, leader.faction):
            _refund_spent_energy(storage, spent_ids, raid_energy_cost)
            return RaidLaunchResult(False, "Нельзя рейдить свою или союзническую точку.", ())
        event_modifier = _active_location_event_modifier(storage, location_name)
        enemy_power = max(10, int(location["npc_power"]) + event_modifier)
        tactical_result, rgrid_session = start_raid_grid(
            storage,
            raid_id=raid_id,
            raid_kind="lair",
            location_label=location_name,
            attacker_faction=leader.faction,
            player_ids=member_id_list,
            enemy_power=enemy_power,
            energy_cost=raid_energy_cost,
        )

    if tactical_result.ok and rgrid_session is not None:
        if storage.start_raid_assault(raid_id):
            return RaidLaunchResult(
                True,
                tactical_result.text,
                tuple(member_ids),
                tactical_raid=True,
            )
        from app.raid_grid import clear_raid_grid_session

        clear_raid_grid_session(storage, rgrid_session)

    _refund_spent_energy(storage, spent_ids, raid_energy_cost)
    return RaidLaunchResult(False, tactical_result.text, tuple(member_ids))


def build_raids_overview(storage: Storage, telegram_id: int) -> str:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return "Рейды доступны только после выбора группировки."

    open_raid = storage.get_open_raid_for_faction(player.faction)
    if open_raid is None:
        war_enemies = list_war_enemy_factions(storage, player.faction)
        enemies_line = ", ".join(war_enemies) if war_enemies else "нет (со всеми мир или союз)"
        return (
            "Отрядные рейды (тактическая карта 9×9):\n"
            "• Создай рейд на точку ресурсов, позови отряд — каждый сам ходит и стреляет.\n"
            f"• Участников: {RAID_MIN_MEMBERS}–{RAID_MAX_MEMBERS} (своя группировка или союзники), "
            f"на поле 6–10 врагов (мутанты + боты).\n"
            f"• Энергия при запуске: 18 (логово) / {DEPOT_RAID_ENERGY_COST} (склад/гараж) у каждого.\n"
            f"• 1 аптечка из инвентаря на бойца; на соседней клетке — «💊 Поднять» союзника (≈40% HP).\n"
            f"• Точка ресурсов: зачисти врагов и удерживай центр {RAID_CAPTURE_TURNS} хода — вынос в казну.\n"
            f"• Успех: 1400 + 180×выживших RU в казну (контроль точки не меняется).\n"
            f"• Таймер матча: {RAID_MATCH_SECONDS // 60} мин, ход {RAID_TURN_SECONDS} сек.\n"
            f"• Артефакт: у каждого выжившего шанс {RAID_ARTIFACT_DROP_CHANCE}% "
            f"(при NPC ≥ {RAID_ARTIFACT_MIN_ENEMY_POWER}).\n"
            f"• Провал логова: −110 RU, −{RATING_REWARD['raid_fail']} рейтинга каждому участнику "
            f"(включая погибших); "
            f"склад/гараж: −{DEPOT_RAID_FAIL_MONEY_PENALTY} RU, −{RATING_REWARD['depot_raid_fail']} рейтинга.\n"
            "• «🏳 Сдаться» — провал для всего отряда.\n\n"
            "🏚 Рейды на склад/гараж врага:\n"
            f"• Зачисти врагов и удерживай клетку склада/гаража {RAID_LOOT_TURNS} хода подряд.\n"
            f"• Успех — вынос {DEPOT_RAID_MIN_LOOT_PERCENT}–{DEPOT_RAID_MAX_LOOT_PERCENT}% каждого типа ресурса со склада/канистр.\n"
            f"• Гараж: дополнительно {DEPOT_RAID_VEHICLE_STEAL_CHANCE}% шанс угнать Ниву или грузовик (если есть).\n"
            "• На базе врага стоят оборонительные боты (Т1, улучшение до Т2 — 50 000 RU из казны).\n"
            f"• Враждебные группировки: {enemies_line}."
        )

    raid_id = int(open_raid["id"])
    raid_kind = resolve_open_raid_kind(open_raid)
    member_ids = storage.get_raid_member_ids(raid_id)
    members = storage.get_characters_by_ids(member_ids)
    members_text = "\n".join(
        f"• {h(member.nickname)} (сила {equipment_power(member)}, HP {member.health})" for member in members
    )

    if raid_kind in DEPOT_RAID_KINDS:
        label = DEPOT_RAID_LABELS.get(raid_kind, "склад")
        target_faction = str(open_raid.get("target_faction") or "?")
        return (
            f"Открытый рейд #{raid_id} на {label} врага 🏚\n"
            f"Цель: {target_faction}\n"
            f"Лидер: {open_raid['leader_id']}\n"
            f"Участников: {len(member_ids)}/{RAID_MAX_MEMBERS}\n\n"
            f"Состав:\n{members_text or '• Пока пусто'}\n\n"
            f"При запуске — тактическая карта. Вынос: {DEPOT_RAID_MIN_LOOT_PERCENT}–{DEPOT_RAID_MAX_LOOT_PERCENT}% "
            f"ресурсов, {DEPOT_RAID_VEHICLE_STEAL_CHANCE}% шанс угнать машину (гараж).\n"
            "Отменить рейд может только создатель."
        )

    location_name = str(open_raid["location"])
    location = storage.get_location(location_name)
    npc_power = int(location["npc_power"]) if location else 0
    event_modifier = _active_location_event_modifier(storage, location_name)
    return (
        f"Открытый рейд #{raid_id}\n"
        f"Логово: {location_name}\n"
        f"Лидер: {open_raid['leader_id']}\n"
        f"Участников: {len(member_ids)}/{RAID_MAX_MEMBERS}\n"
        f"Сила NPC: {npc_power} (модификатор событий {event_modifier:+d})\n\n"
        f"Состав:\n{members_text or '• Пока пусто'}\n\n"
        "При запуске — тактическая карта: 6–10 врагов, каждый ходит сам.\n"
        "Отменить рейд может только создатель."
    )


def cancel_raid_by_leader(storage: Storage, telegram_id: int, raid_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if player.faction is None:
        return ActionResult(False, "Сначала выбери группировку.")

    raid = storage.get_raid(raid_id)
    if raid is None or str(raid.get("status")) != "open":
        return ActionResult(False, f"Открытый рейд #{raid_id} не найден.")
    if int(raid["leader_id"]) != telegram_id:
        return ActionResult(False, "Отменить рейд может только тот, кто его создал.")

    cancelled = storage.cancel_raid(raid_id, telegram_id)
    if cancelled is None:
        return ActionResult(False, "Не удалось отменить рейд.")
    return ActionResult(
        True,
        f"Рейд #{raid_id} на «{cancelled.get('location')}» отменён создателем.",
    )


def cancel_all_raids_by_leader(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if player.faction is None:
        return ActionResult(False, "Сначала выбери группировку.")

    open_raids = storage.list_open_raids_led_by(telegram_id)
    if not open_raids:
        return ActionResult(False, "У тебя нет открытых рейдов для отмены.")

    cancelled = storage.cancel_all_open_raids_led_by(telegram_id)
    if not cancelled:
        return ActionResult(False, "Не удалось отменить рейды.")
    lines = [f"• #{item['id']} — {item.get('location')}" for item in cancelled]
    return ActionResult(
        True,
        "Отменены твои открытые рейды:\n" + "\n".join(lines),
    )


def _normalize_item_key(item_key: str) -> str:
    return item_key if item_key in WAREHOUSE_ITEM_KEYS else "ammo_pack"


def character_rank_level(character: Character) -> int:
    rank = rank_by_key(character.faction, character.faction_rank)
    if rank is None:
        return 0
    return rank.level


def can_withdraw_faction_treasury(storage: Storage, character: Character) -> bool:
    """Вывод из казны — только лидер группировки."""
    if character.faction is None:
        return False
    return storage.get_faction_leader_id(character.faction) == character.telegram_id


def can_withdraw_faction_warehouse(storage: Storage, character: Character) -> bool:
    """Вывод со склада: лидер или звание от 5 уровня."""
    if character.faction is None:
        return False
    if storage.get_faction_leader_id(character.faction) == character.telegram_id:
        return True
    return character_rank_level(character) >= TREASURY_WITHDRAW_MIN_RANK


def deposit_to_faction_warehouse(
    storage: Storage,
    telegram_id: int,
    item_key: str,
    amount: int,
) -> ActionResult:
    if amount <= 0:
        return ActionResult(False, "Некорректное количество для склада.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Склад доступен только бойцам группировки.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    key = _normalize_item_key(item_key)
    if not storage.remove_item(telegram_id, key, amount):
        return ActionResult(False, "В инвентаре недостаточно предметов для сдачи.")
    if not storage.change_faction_warehouse_item(player.faction, key, amount):
        storage.add_item(telegram_id, key, amount)
        return ActionResult(False, "Не удалось обновить склад группировки.")
    if key.startswith("artifact_junk"):
        from app.faction_goals import record_faction_goal_event

        record_faction_goal_event(storage, player.faction, "junk_deposit", amount=amount)
    return ActionResult(True, f"На склад {player.faction} отправлено: {ITEM_LABELS.get(key, key)} x{amount}.")


def withdraw_from_faction_warehouse(
    storage: Storage,
    telegram_id: int,
    item_key: str,
    amount: int,
) -> ActionResult:
    if amount <= 0:
        return ActionResult(False, "Некорректное количество для выдачи.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Склад доступен только бойцам группировки.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    if not can_withdraw_faction_warehouse(storage, player):
        return ActionResult(
            False,
            "Забирать со склада можно с 5 ранга (или лидеру группировки).",
        )
    key = _normalize_item_key(item_key)
    if not storage.change_faction_warehouse_item(player.faction, key, -amount):
        return ActionResult(False, "На складе недостаточно ресурсов.")
    storage.add_item(telegram_id, key, amount)
    return ActionResult(True, f"Со склада получено: {ITEM_LABELS.get(key, key)} x{amount}.")


def deposit_to_faction_treasury(storage: Storage, telegram_id: int, amount: int) -> ActionResult:
    if amount <= 0:
        return ActionResult(False, "Некорректная сумма для пополнения казны.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Казна доступна только бойцам группировки.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if not storage.change_money(telegram_id, -amount):
        return ActionResult(False, f"Недостаточно RU. Нужно {amount} RU.")
    storage.change_faction_treasury(player.faction, amount)
    return ActionResult(True, f"В казну {player.faction} внесено {amount} RU.")


def withdraw_from_faction_treasury(storage: Storage, telegram_id: int, amount: int) -> ActionResult:
    if amount <= 0:
        return ActionResult(False, "Некорректная сумма для вывода из казны.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Казна доступна только бойцам группировки.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if not can_withdraw_faction_treasury(storage, player):
        return ActionResult(
            False,
            "Снимать деньги из казны может только лидер группировки.",
        )
    if not storage.withdraw_faction_treasury(player.faction, amount):
        return ActionResult(False, "В казне недостаточно денег для вывода.")
    if not storage.change_money(telegram_id, amount):
        storage.change_faction_treasury(player.faction, amount)
        return ActionResult(False, "Не удалось зачислить деньги на баланс.")
    return ActionResult(True, f"Из казны {player.faction} выведено {amount} RU в твой баланс.")


def character_rank_title(storage: Storage, character: Character) -> str | None:
    if character.faction is None:
        return None
    is_leader = storage.get_faction_leader_id(character.faction) == character.telegram_id
    return resolve_rank_title(
        faction=character.faction,
        faction_rank=character.faction_rank,
        is_leader=is_leader,
    )


def assign_faction_rank(
    storage: Storage,
    leader_telegram_id: int,
    target_telegram_id: int,
    rank_key: str,
) -> ActionResult:
    leader = storage.get_character(leader_telegram_id, refresh_energy=False)
    if leader is None or leader.faction is None:
        return ActionResult(False, "Сначала вступи в группировку.")
    if storage.get_faction_leader_id(leader.faction) != leader_telegram_id:
        return ActionResult(False, "Назначать звания может только лидер группировки.")

    target = storage.get_character(target_telegram_id, refresh_energy=False)
    if target is None or target.faction != leader.faction:
        return ActionResult(False, "Игрок не состоит в твоей группировке.")
    if target.telegram_id == leader_telegram_id:
        title = leader_title(leader.faction) or "лидер"
        return ActionResult(False, f"Тебе уже закреплено звание лидера: {title}.")

    rank = rank_by_key(leader.faction, rank_key)
    if rank is None:
        return ActionResult(False, "Некорректное звание.")
    if not storage.set_faction_rank(target.telegram_id, rank.key):
        return ActionResult(False, "Не удалось сохранить звание.")
    return ActionResult(
        True,
        f"{h(target.nickname)} теперь «{h(rank.title)}» в группировке «{h(leader.faction)}».",
        payload={
            "notify": [
                (
                    target.telegram_id,
                    f"🎖 Лидер назначил тебе звание «{h(rank.title)}» в группировке «{h(leader.faction)}».",
                ),
            ],
        },
    )


def build_faction_ranks_overview(storage: Storage, telegram_id: int) -> str:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return "Звания доступны после выбора группировки."
    if storage.get_faction_leader_id(player.faction) != telegram_id:
        own = character_rank_title(storage, player) or "без звания"
        return (
            f"Звания группировки «{player.faction}»\n"
            f"Твоё звание: {own}\n\n"
            "Назначать звания может только лидер."
        )

    ranks = ranks_for_faction(player.faction)
    leader_name = leader_title(player.faction) or "Лидер"
    lines = [
        f"🎖 Звания «{player.faction}»",
        f"Лидер: {leader_name} (ты)",
        "",
        "Выбери бойца, затем звание:",
    ]
    for rank in ranks:
        lines.append(f"{rank.level}) {rank.title}")
    return "\n".join(lines)


def build_faction_member_rank_pick_text(
    storage: Storage,
    leader_telegram_id: int,
    target_telegram_id: int,
) -> str:
    leader = storage.get_character(leader_telegram_id, refresh_energy=False)
    target = storage.get_character(target_telegram_id, refresh_energy=False)
    if leader is None or leader.faction is None:
        return "Сначала вступи в группировку."
    if target is None or target.faction != leader.faction:
        return "Игрок не в твоей группировке."
    current = character_rank_title(storage, target) or "без звания"
    return (
        f"Боец: {h(target.nickname)}\n"
        f"Сейчас: {current}\n\n"
        "Выбери новое звание:"
    )


def create_faction_auction(storage: Storage, telegram_id: int, lot_key: str) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Аукцион доступен только бойцам группировки.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if lot_key == "gear":
        return create_market_lot(storage, telegram_id, "auto", 1)
    lot = AUCTION_DEFAULT_LOTS.get(lot_key)
    if lot is None:
        return ActionResult(False, "Неизвестный тип лота.")
    item_key, amount, price = lot
    if not storage.remove_item(telegram_id, item_key, amount):
        return ActionResult(False, f"Недостаточно предметов ({ITEM_LABELS.get(item_key, item_key)}) для лота.")
    auction_id = storage.create_auction(
        seller_id=telegram_id,
        faction=player.faction,
        item_key=item_key,
        amount=amount,
        price=price,
    )
    storage.add_player_stat(telegram_id, "trades_done", 1)
    _add_rating(storage, telegram_id, RATING_REWARD["trade_action"])
    achievements_text = _progress_and_unlock_achievements(storage, telegram_id)
    return ActionResult(
        True,
        f"Ячейка №{auction_id} создана: {ITEM_LABELS.get(item_key, item_key)} x{amount} за {price} RU.\n"
        f"Комиссия при продаже: {EXCHANGE_SELL_FEE_PERCENT}%.{achievements_text}",
    )


def buy_first_faction_auction(storage: Storage, telegram_id: int) -> ActionResult:
    buyer = storage.get_character(telegram_id, refresh_energy=False)
    if buyer is None or buyer.faction is None:
        return ActionResult(False, "Покупка на аукционе доступна только бойцам группировки.")
    if _is_dead(buyer):
        return ActionResult(False, _dead_block_text())
    auctions = sorted(_list_open_exchange_lots(storage), key=lambda a: int(a["id"]))
    target = next((a for a in auctions if int(a["seller_id"]) != telegram_id), None)
    if target is None:
        return ActionResult(False, "Подходящих открытых лотов нет.")

    auction_id = int(target["id"])
    price = int(target["price"])
    item_key = str(target["item_key"])
    amount = int(target["amount"])
    seller_id = int(target["seller_id"])

    fee = max(1, int(round(price * (EXCHANGE_SELL_FEE_PERCENT / 100))))
    seller_income = max(0, price - fee)
    if not storage.complete_auction_sale(
        auction_id,
        buyer_id=telegram_id,
        seller_id=seller_id,
        price=price,
        seller_income=seller_income,
        item_key=item_key,
        amount=amount,
    ):
        return ActionResult(False, "Недостаточно денег или лот уже недоступен.")
    storage.add_player_stat(telegram_id, "trades_done", 1)
    storage.add_player_stat(seller_id, "trades_done", 1)
    _add_rating(storage, telegram_id, RATING_REWARD["trade_action"])
    _add_rating(storage, seller_id, RATING_REWARD["trade_action"])
    storage.add_player_stat(seller_id, "money_earned", seller_income)
    achievements_text = _progress_and_unlock_achievements(storage, telegram_id)
    seller_achievements = _progress_and_unlock_achievements(storage, seller_id)
    buyer = storage.get_character(telegram_id, refresh_energy=False)
    buyer_name = h(buyer.nickname) if buyer else h(str(telegram_id))
    seller_msg = (
        f"🛒 {buyer_name} купил(а) твою ячейку №{auction_id}: "
        f"{ITEM_LABELS.get(item_key, item_key)} x{amount} за {price} RU.\n"
        f"На баланс: +{seller_income} RU (комиссия {fee} RU).{seller_achievements}"
    )
    return ActionResult(
        True,
        f"Куплена ячейка №{auction_id}: {ITEM_LABELS.get(item_key, item_key)} x{amount} за {price} RU.\n"
        f"Продавец получил {seller_income} RU (комиссия {fee} RU).{achievements_text}",
        payload={"notify": [(seller_id, seller_msg)]},
    )


def cancel_own_first_auction(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Сначала создай персонажа и выбери группировку.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    auctions = sorted(_list_open_exchange_lots(storage), key=lambda a: int(a["id"]))
    target = next((a for a in auctions if int(a["seller_id"]) == telegram_id), None)
    if target is None:
        return ActionResult(False, "У тебя нет открытых лотов для отмены.")

    auction_id = int(target["id"])
    item_key = str(target["item_key"])
    amount = int(target["amount"])
    if not storage.cancel_auction_and_refund(
        auction_id,
        seller_id=telegram_id,
        item_key=item_key,
        amount=amount,
    ):
        return ActionResult(False, "Не удалось отменить лот.")
    return ActionResult(
        True,
        f"Лот #{auction_id} отменен, предметы возвращены: {ITEM_LABELS.get(item_key, item_key)} x{amount}.",
    )


def _is_equipment_item(item_key: str) -> bool:
    return item_key in WEAPON_CATALOG or item_key in ARMOR_CATALOG


def _is_custom_exchange_item(item_key: str) -> bool:
    """Предметы, которые можно выставить на биржу собственным лотом (не экипировка)."""
    if _is_equipment_item(item_key):
        return False
    if item_key in CUSTOM_EXCHANGE_ITEM_KEYS:
        return True
    return item_key.startswith(CUSTOM_EXCHANGE_ITEM_PREFIXES)


def _exchange_lot_category(item_key: str) -> str:
    if item_key.startswith("artifact"):
        return "artifact"
    if item_key in EXCHANGE_FUEL_ITEM_KEYS:
        return "fuel"
    if item_key in EXCHANGE_CONSUMABLE_ITEM_KEYS:
        return "consumable"
    return "other"


def _list_open_exchange_lots(storage: Storage) -> list[dict[str, Any]]:
    """Биржа: общие лоты расходников/артефактов (не экипировка)."""
    return [
        lot
        for lot in storage.list_open_auctions()
        if not _is_equipment_item(str(lot.get("item_key", "")))
    ]


def list_sellable_exchange_items(storage: Storage, telegram_id: int) -> list[dict[str, int | str]]:
    """Список предметов из инвентаря, доступных для выставления собственным лотом на бирже."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return []
    rows: list[dict[str, int | str]] = []
    for item_key, owned in sorted(player.inventory.items()):
        amount = int(owned)
        if amount <= 0 or not _is_custom_exchange_item(item_key):
            continue
        rows.append(
            {
                "item_key": item_key,
                "title": ITEM_LABELS.get(item_key, item_key),
                "amount": amount,
            }
        )
    return rows


def create_custom_exchange_lot(
    storage: Storage,
    telegram_id: int,
    item_key: str,
    amount: int,
    price: int,
) -> ActionResult:
    """Создает собственный лот биржи с произвольной ценой (не экипировка)."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if amount <= 0:
        return ActionResult(False, "Количество для лота должно быть больше нуля.")
    if price <= 0:
        return ActionResult(False, "Цена лота должна быть больше нуля.")
    if not _is_custom_exchange_item(item_key):
        return ActionResult(False, "Этот предмет нельзя выставить на биржу (только не экипировка).")
    item_name = ITEM_LABELS.get(item_key, item_key)
    if not storage.remove_item(telegram_id, item_key, amount):
        return ActionResult(False, f"Недостаточно предметов ({item_name}) для лота.")
    auction_id = storage.create_auction(
        seller_id=telegram_id,
        faction=player.faction or "market",
        item_key=item_key,
        amount=amount,
        price=price,
    )
    storage.add_player_stat(telegram_id, "trades_done", 1)
    _add_rating(storage, telegram_id, RATING_REWARD["trade_action"])
    achievements_text = _progress_and_unlock_achievements(storage, telegram_id)
    return ActionResult(
        True,
        f"Ячейка №{auction_id} создана: {item_name} x{amount} за {price} RU.\n"
        f"Комиссия при продаже: {EXCHANGE_SELL_FEE_PERCENT}%.{achievements_text}",
    )


def build_exchange_lots_overview(
    storage: Storage,
    telegram_id: int,
    limit: int = 12,
    *,
    category: str | None = None,
) -> tuple[str, list[dict[str, int | str]]]:
    """Список открытых лотов биржи (не экипировка) с id/ценой/предметом."""
    lots = sorted(_list_open_exchange_lots(storage), key=lambda a: int(a["id"]))
    normalized_category = (category or "all").strip().lower()
    if normalized_category not in EXCHANGE_CATEGORIES:
        normalized_category = "all"
    if normalized_category != "all":
        lots = [lot for lot in lots if _exchange_lot_category(str(lot["item_key"])) == normalized_category]
    shown = lots[: max(1, limit)]
    category_label = EXCHANGE_CATEGORY_LABELS.get(normalized_category, "все")
    if not shown:
        return (f"Открытых ячеек биржи ({category_label}) сейчас нет.", [])
    rows: list[dict[str, int | str]] = []
    lines = [f"Биржа: открытые ячейки — {category_label} (комиссия {EXCHANGE_SELL_FEE_PERCENT}%):"]
    for lot in shown:
        item_key = str(lot["item_key"])
        title = ITEM_LABELS.get(item_key, item_key)
        lot_id = int(lot["id"])
        amount = int(lot["amount"])
        price = int(lot["price"])
        seller_id = int(lot["seller_id"])
        is_own = seller_id == telegram_id
        rows.append(
            {
                "id": lot_id,
                "title": title,
                "amount": amount,
                "price": price,
                "seller_id": seller_id,
                "is_own": is_own,
            }
        )
        own_note = " (твоя)" if is_own else ""
        lines.append(f"• Ячейка №{lot_id}: {title} x{amount} — {price} RU (продавец {seller_id}){own_note}")
    return ("\n".join(lines), rows)


def _apply_bazaar_faction_tax(storage: Storage, seller_id: int, price: int, fee: int) -> tuple[int, str]:
    """Часть комиссии → казна ГП продавца. Возвращает (сумма, текст-хвост)."""
    seller = storage.get_character(seller_id, refresh_energy=False)
    faction = seller.faction if seller is not None else None
    if not faction:
        return 0, ""
    tax = max(0, min(int(fee), int(round(price * (BAZAAR_FACTION_TAX_PERCENT / 100)))))
    if tax <= 0:
        return 0, ""
    storage.change_faction_treasury(str(faction), tax)
    return tax, f"\nКазна «{faction}»: +{tax} RU (налог барахолки {BAZAAR_FACTION_TAX_PERCENT}%)."


def preview_bazaar_lot(storage: Storage, lot_id: int, *, market: bool) -> ActionResult:
    lot = storage.get_open_auction(lot_id)
    if lot is None:
        return ActionResult(False, "Ячейка не найдена или уже закрыта.")
    item_key = str(lot["item_key"])
    is_eq = _is_equipment_item(item_key)
    if market and not is_eq:
        return ActionResult(False, "Это ячейка биржи, не рынка.")
    if (not market) and is_eq:
        return ActionResult(False, "Это ячейка рынка, не биржи.")
    fee_pct = MARKET_SELL_FEE_PERCENT if market else EXCHANGE_SELL_FEE_PERCENT
    price = int(lot["price"])
    amount = int(lot["amount"])
    seller_id = int(lot["seller_id"])
    seller = storage.get_character(seller_id, refresh_energy=False)
    seller_name = h(seller.nickname) if seller else str(seller_id)
    kind = "рынка" if market else "биржи"
    return ActionResult(
        True,
        f"Подтверди покупку ячейки №{lot_id} ({kind}):\n"
        f"• {ITEM_LABELS.get(item_key, item_key)} ×{amount}\n"
        f"• Цена: {price} RU (комиссия продавца {fee_pct}%)\n"
        f"• Продавец: {seller_name}\n"
        f"• Налог {BAZAAR_FACTION_TAX_PERCENT}% от цены уйдёт в казну ГП продавца (из комиссии).",
        payload={"lot_id": lot_id, "market": market, "price": price},
    )


def buy_exchange_lot(storage: Storage, telegram_id: int, lot_id: int) -> ActionResult:
    """Покупка конкретного лота биржи по id (аналог рыночной покупки, с биржевой комиссией)."""
    buyer = storage.get_character(telegram_id, refresh_energy=False)
    if buyer is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(buyer):
        return ActionResult(False, _dead_block_text())
    lot = storage.get_open_auction(lot_id)
    if lot is None:
        return ActionResult(False, "Лот не найден или уже закрыт.")
    if _is_equipment_item(str(lot["item_key"])):
        return ActionResult(False, "Этот лот относится к рынку экипировки, а не к бирже.")
    seller_id = int(lot["seller_id"])
    if seller_id == telegram_id:
        return ActionResult(False, "Нельзя выкупить собственный лот.")
    price = int(lot["price"])
    item_key = str(lot["item_key"])
    amount = int(lot["amount"])
    fee = max(1, int(round(price * (EXCHANGE_SELL_FEE_PERCENT / 100))))
    seller_income = max(0, price - fee)
    if not storage.complete_auction_sale(
        lot_id,
        buyer_id=telegram_id,
        seller_id=seller_id,
        price=price,
        seller_income=seller_income,
        item_key=item_key,
        amount=amount,
    ):
        return ActionResult(False, "Недостаточно денег или лот уже недоступен.")
    tax, tax_note = _apply_bazaar_faction_tax(storage, seller_id, price, fee)
    storage.add_player_stat(telegram_id, "trades_done", 1)
    storage.add_player_stat(seller_id, "trades_done", 1)
    _add_rating(storage, telegram_id, RATING_REWARD["trade_action"])
    _add_rating(storage, seller_id, RATING_REWARD["trade_action"])
    storage.add_player_stat(seller_id, "money_earned", seller_income)
    achievements_text = _progress_and_unlock_achievements(storage, telegram_id)
    seller_achievements = _progress_and_unlock_achievements(storage, seller_id)
    buyer_name = h(buyer.nickname)
    item_name = ITEM_LABELS.get(item_key, item_key)
    seller_msg = (
        f"🛒 {buyer_name} купил(а) твою ячейку №{lot_id}: "
        f"{item_name} x{amount} за {price} RU.\n"
        f"На баланс: +{seller_income} RU (комиссия {fee} RU).{tax_note}{seller_achievements}"
    )
    return ActionResult(
        True,
        f"Куплена ячейка №{lot_id}: {item_name} x{amount} за {price} RU.\n"
        f"Продавец получил {seller_income} RU (комиссия {fee} RU).{tax_note}{achievements_text}",
        payload={"notify": [(seller_id, seller_msg)], "faction_tax": tax},
    )


def cancel_own_auction(storage: Storage, telegram_id: int, lot_id: int) -> ActionResult:
    """Отмена своего конкретного лота биржи по id."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    lot = storage.get_open_auction(lot_id)
    if lot is None:
        return ActionResult(False, "Лот не найден или уже закрыт.")
    if int(lot["seller_id"]) != telegram_id:
        return ActionResult(False, "Отменить можно только свой лот.")
    item_key = str(lot["item_key"])
    amount = int(lot["amount"])
    if not storage.close_auction(lot_id, buyer_id=None, status="cancelled"):
        return ActionResult(False, "Не удалось отменить лот.")
    storage.add_item(telegram_id, item_key, amount)
    return ActionResult(
        True,
        f"Лот #{lot_id} отменен, предметы возвращены: {ITEM_LABELS.get(item_key, item_key)} x{amount}.",
    )


def _equipment_sell_price(base_sell_price: int, durability: int | None = None) -> int:
    base = max(1, int(round(base_sell_price * TRADER_EQUIPMENT_SELL_RATE)))
    if durability is None:
        return base
    return _price_with_durability(base, durability)


def list_market_lots(storage: Storage, telegram_id: int) -> list[dict[str, Any]]:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return []
    return [
        lot
        for lot in storage.list_open_equipment_market_lots()
        if int(lot.get("seller_id", 0)) != telegram_id and _is_equipment_item(str(lot.get("item_key", "")))
    ]


def list_sellable_market_equipment(storage: Storage, telegram_id: int) -> list[dict[str, int | str]]:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return []
    rows: list[dict[str, int | str]] = []
    for item_key, owned in sorted(player.inventory.items()):
        amount = int(owned)
        if amount <= 0 or not _is_equipment_item(item_key):
            continue
        rows.append(
            {
                "item_key": item_key,
                "title": ITEM_LABELS.get(item_key, item_key),
                "amount": amount,
            }
        )
    return rows


def create_market_lot(
    storage: Storage,
    telegram_id: int,
    item_key: str,
    amount: int,
    price: int | None = None,
) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    if amount <= 0:
        return ActionResult(False, "Количество для лота должно быть больше нуля.")
    if item_key in {"first_gear", "auto"}:
        available_equipment_keys = [
            key
            for key, owned in sorted(player.inventory.items())
            if int(owned) > 0 and _is_equipment_item(key)
        ]
        if not available_equipment_keys:
            return ActionResult(
                False,
                "В инвентаре нет оружия или брони для выставления на рынок.",
            )
        item_key = available_equipment_keys[0]
    if not _is_equipment_item(item_key):
        return ActionResult(False, "На рынок можно выставить только оружие или броню.")
    if item_key in WEAPON_CATALOG:
        item_name = str(WEAPON_CATALOG[item_key]["name"])
    else:
        item_name = str(ARMOR_CATALOG[item_key]["name"])
    if not storage.remove_item(telegram_id, item_key, amount):
        return ActionResult(False, f"У тебя нет нужного количества: {item_name}.")
    base_sell = int(SHOP_ITEMS.get(item_key, {}).get("sell_price", 0))
    if base_sell <= 0:
        base_sell = max(1, int(float(SHOP_ITEMS[item_key]["buy_price"]) / 3))
    suggested_price = max(1, _equipment_sell_price(base_sell) * amount)
    lot_price = suggested_price if price is None else int(price)
    if lot_price <= 0:
        storage.add_item(telegram_id, item_key, amount)
        return ActionResult(False, "Цена лота должна быть больше нуля.")
    auction_id = storage.create_auction(
        seller_id=telegram_id,
        faction=player.faction or "market",
        item_key=item_key,
        amount=amount,
        price=lot_price,
    )
    return ActionResult(
        True,
        f"Рыночная ячейка №{auction_id} выставлена: {item_name} x{amount} за {lot_price} RU.\n"
        f"Комиссия при продаже: {MARKET_SELL_FEE_PERCENT}%.",
    )


def buy_first_market_lot(storage: Storage, telegram_id: int) -> ActionResult:
    buyer = storage.get_character(telegram_id, refresh_energy=False)
    if buyer is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(buyer):
        return ActionResult(False, _dead_block_text())
    auctions = list_market_lots(storage, telegram_id)
    target = auctions[0] if auctions else None
    if target is None:
        return ActionResult(False, "Открытых рыночных лотов экипировки нет.")
    auction_id = int(target["id"])
    price = int(target["price"])
    item_key = str(target["item_key"])
    amount = int(target["amount"])
    seller_id = int(target["seller_id"])
    fee = max(1, int(round(price * (MARKET_SELL_FEE_PERCENT / 100))))
    seller_income = max(0, price - fee)
    if not storage.complete_auction_sale(
        auction_id,
        buyer_id=telegram_id,
        seller_id=seller_id,
        price=price,
        seller_income=seller_income,
        item_key=item_key,
        amount=amount,
    ):
        return ActionResult(False, "Недостаточно денег или лот уже недоступен.")
    item_name = ITEM_LABELS.get(item_key, item_key)
    buyer_name = h(buyer.nickname)
    return ActionResult(
        True,
        f"Куплена ячейка №{auction_id}: {item_name} x{amount} за {price} RU.\n"
        f"Продавец получил {seller_income} RU (комиссия {fee} RU).",
        payload={
            "notify": [
                (
                    seller_id,
                    f"🛒 {buyer_name} купил(а) твою ячейку №{auction_id}: {item_name} x{amount} за {price} RU.\n"
                    f"На баланс: +{seller_income} RU (комиссия {fee} RU).",
                ),
            ],
        },
    )


def buy_market_lot(storage: Storage, telegram_id: int, lot_id: int) -> ActionResult:
    buyer = storage.get_character(telegram_id, refresh_energy=False)
    if buyer is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(buyer):
        return ActionResult(False, _dead_block_text())
    lot = storage.get_open_auction(lot_id)
    if lot is None:
        return ActionResult(False, "Лот не найден или уже закрыт.")
    if not _is_equipment_item(str(lot["item_key"])):
        return ActionResult(False, "Этот лот не относится к снаряге.")
    seller_id = int(lot["seller_id"])
    if seller_id == telegram_id:
        return ActionResult(False, "Нельзя выкупить собственный лот.")
    price = int(lot["price"])
    item_key = str(lot["item_key"])
    amount = int(lot["amount"])
    fee = max(1, int(round(price * (MARKET_SELL_FEE_PERCENT / 100))))
    seller_income = max(0, price - fee)
    if not storage.complete_auction_sale(
        lot_id,
        buyer_id=telegram_id,
        seller_id=seller_id,
        price=price,
        seller_income=seller_income,
        item_key=item_key,
        amount=amount,
    ):
        return ActionResult(False, "Недостаточно денег или лот уже недоступен.")
    tax, tax_note = _apply_bazaar_faction_tax(storage, seller_id, price, fee)
    item_name = ITEM_LABELS.get(item_key, item_key)
    buyer_name = h(buyer.nickname)
    return ActionResult(
        True,
        f"Куплена ячейка №{lot_id}: {item_name} x{amount} за {price} RU.\n"
        f"Продавец получил {seller_income} RU (комиссия {fee} RU).{tax_note}",
        payload={
            "notify": [
                (
                    seller_id,
                    f"🛒 {buyer_name} купил(а) твою ячейку №{lot_id}: {item_name} x{amount} за {price} RU.\n"
                    f"На баланс: +{seller_income} RU (комиссия {fee} RU).{tax_note}",
                ),
            ],
            "faction_tax": tax,
        },
    )


def build_market_lots_overview(
    storage: Storage,
    telegram_id: int,
    limit: int = 12,
) -> tuple[str, list[dict[str, int | str]]]:
    lots = list_market_lots(storage, telegram_id)
    shown = lots[: max(1, limit)]
    if not shown:
        return ("Открытых рыночных лотов снаряги сейчас нет.", [])
    rows: list[dict[str, int | str]] = []
    lines = ["Рынок снаряги (выбери лот по кнопке):"]
    for lot in shown:
        item_key = str(lot["item_key"])
        title = ITEM_LABELS.get(item_key, item_key)
        lot_id = int(lot["id"])
        amount = int(lot["amount"])
        price = int(lot["price"])
        seller_id = int(lot["seller_id"])
        seller = storage.get_character(seller_id, refresh_energy=False)
        seller_name = seller.nickname if seller else str(seller_id)
        rows.append(
            {
                "id": lot_id,
                "title": title,
                "amount": amount,
                "price": price,
                "seller_id": seller_id,
                "seller_name": seller_name,
            }
        )
        lines.append(f"• #{lot_id} {title} x{amount} — {price} RU (продавец {seller_name})")
    return ("\n".join(lines), rows)


def cancel_own_first_market_lot(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    auctions = [
        lot
        for lot in storage.list_open_auctions()
        if int(lot.get("seller_id", 0)) == telegram_id and _is_equipment_item(str(lot.get("item_key", "")))
    ]
    target = next(iter(auctions), None)
    if target is None:
        return ActionResult(False, "У тебя нет открытых рыночных лотов экипировки.")
    auction_id = int(target["id"])
    item_key = str(target["item_key"])
    amount = int(target["amount"])
    if not storage.close_auction(auction_id, buyer_id=None, status="cancelled"):
        return ActionResult(False, "Не удалось отменить рыночный лот.")
    storage.add_item(telegram_id, item_key, amount)
    return ActionResult(True, f"Рыночный лот #{auction_id} отменен, предметы возвращены.")


def find_open_war_lobby_for_character(storage: Storage, player: Character) -> dict[str, Any] | None:
    if player.faction is None:
        return None
    lobby = storage.get_open_war_lobby_for_faction(player.faction)
    if lobby is not None:
        return lobby
    for ally in storage.list_faction_alliances(player.faction):
        ally_lobby = storage.get_open_war_lobby_for_faction(ally)
        if ally_lobby is not None:
            return ally_lobby
    return None


def create_or_join_war_lobby(storage: Storage, telegram_id: int, location_name: str) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = _reject_if_player_busy(storage, telegram_id)
    if blocked is not None:
        return blocked
    if player.faction is None:
        return ActionResult(False, "Сначала выбери группировку.")
    location = storage.get_location(location_name)
    if location is None:
        return ActionResult(False, "Локация не найдена.")
    if _location_is_friendly_to_faction(storage, location, player.faction):
        return ActionResult(False, "Нельзя штурмовать свою или союзническую точку.")
    from app.monolith_war import get_pending_monolith_war, monolith_war_status_line

    pending = get_pending_monolith_war(storage)
    if pending is not None:
        line = monolith_war_status_line(storage) or "идёт окно боя Монолита"
        return ActionResult(
            False,
            f"Сейчас нельзя открыть новое лобби: {line}. Дождись исхода.",
        )
    open_lobby = find_open_war_lobby_for_character(storage, player)
    if open_lobby is None:
        for ally in storage.list_faction_alliances(player.faction):
            ally_lobby = storage.get_open_war_lobby_for_faction(ally)
            if ally_lobby is not None and str(ally_lobby["location"]) == location_name:
                open_lobby = ally_lobby
                break
    if open_lobby is None:
        war_id = storage.create_war_lobby(player.faction, location_name, telegram_id)
        return ActionResult(True, f"Создано военное лобби #{war_id} на «{location_name}».")
    war_id = int(open_lobby["id"])
    host_faction = str(open_lobby["host_faction"])
    if host_faction != player.faction and not storage.are_factions_allied(host_faction, player.faction):
        return ActionResult(False, "В это лобби могут вступать только союзники хоста.")
    if not storage.add_war_lobby_member(war_id, telegram_id):
        return ActionResult(False, "Не удалось вступить в военное лобби.")
    members = storage.get_war_lobby_member_ids(war_id)
    return ActionResult(True, f"Ты вступил в военное лобби #{war_id}. Участников: {len(members)}.")


def build_war_lobby_overview(storage: Storage, telegram_id: int) -> str:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return "Военное лобби доступно после выбора группировки."
    lobby = find_open_war_lobby_for_character(storage, player)
    if lobby is None:
        return (
            "Открытых военных лобби нет. Создай лобби на нужную локацию.\n"
            f"Для захвата занятой точки нужно минимум {WAR_MIN_FACTION_MEMBERS} живых бойцов в лобби "
            f"(−{WAR_LOBBY_ENERGY_COST} энергии каждому при запуске).\n"
            f"Нейтральные точки — группа от 2 («🎯 Захват нейтральных точек»): "
            f"+{NCAP_SUCCESS_PAY_RU} RU, −18 энергии."
        )
    war_id = int(lobby["id"])
    leader_id = int(lobby["leader_id"])
    creator = storage.get_character(leader_id, refresh_energy=False)
    creator_label = (
        f"{h(creator.nickname)} (ID {leader_id})"
        if creator is not None
        else f"ID {leader_id}"
    )
    member_ids = storage.get_war_lobby_member_ids(war_id)
    members = storage.get_characters_by_ids(member_ids)
    by_faction: dict[str, int] = {}
    member_lines: list[str] = []
    for member in members:
        if member.faction is None:
            continue
        by_faction[member.faction] = by_faction.get(member.faction, 0) + 1
        mark = " 👑" if member.telegram_id == leader_id else ""
        member_lines.append(f"• {h(member.nickname)} — {h(member.faction or '')}{mark}")
    total = sum(by_faction.values())
    share_lines = []
    for faction_name, count in sorted(by_faction.items()):
        percent = int(round((count / total) * 100)) if total > 0 else 0
        share_lines.append(f"• {faction_name}: {count} бойцов ({percent}%)")
    shares_block = "\n".join(share_lines) if share_lines else "• Нет данных"
    roster_block = "\n".join(member_lines) if member_lines else "• Пока никого"
    return (
        f"Военное лобби #{war_id}\n"
        f"Локация: {lobby['location']}\n"
        f"Хост: {lobby['host_faction']}\n"
        f"Создал: {creator_label}\n"
        f"Участников: {len(member_ids)} / мин. {WAR_MIN_FACTION_MEMBERS} для запуска "
        f"(−{WAR_LOBBY_ENERGY_COST} энергии каждому)\n\n"
        f"Награды при успехе: хост +{WAR_SUCCESS_PAY_RU} RU (+{RATING_REWARD['war_success']} рейт.), "
        f"союзники +{WAR_ALLY_SUCCESS_PAY_RU} RU (+{WAR_ALLY_SUCCESS_RATING} рейт.) "
        f"(только выжившим).\n\n"
        f"Бойцы в лобби:\n{roster_block}\n\n"
        f"Распределение сил:\n{shares_block}"
    )


def can_dissolve_war_lobby(storage: Storage, telegram_id: int) -> bool:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return False
    lobby = find_open_war_lobby_for_character(storage, player)
    if lobby is None:
        return False
    return int(lobby["leader_id"]) == telegram_id


def dissolve_war_lobby(storage: Storage, telegram_id: int) -> WarLobbyResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return WarLobbyResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        return WarLobbyResult(False, _dead_block_text())
    if player.faction is None:
        return WarLobbyResult(False, "Сначала выбери группировку.")
    lobby = find_open_war_lobby_for_character(storage, player)
    if lobby is None:
        return WarLobbyResult(False, "Открытого военного лобби нет.")
    war_id = int(lobby["id"])
    if int(lobby["leader_id"]) != telegram_id:
        return WarLobbyResult(False, "Распустить лобби может только его создатель.")
    member_ids = tuple(storage.get_war_lobby_member_ids(war_id))
    cancelled = storage.cancel_war_lobby(war_id, telegram_id)
    if cancelled is None:
        return WarLobbyResult(False, "Не удалось распустить лобби.")
    location = str(cancelled.get("location", lobby["location"]))
    return WarLobbyResult(
        True,
        f"Военное лобби #{war_id} на «{location}» распущено.",
        member_ids,
    )


def launch_war_lobby(storage: Storage, telegram_id: int) -> WarLobbyResult:
    leader = storage.get_character(telegram_id, refresh_energy=False)
    if leader is None:
        return WarLobbyResult(False, "Сначала создай персонажа.")
    if _is_dead(leader):
        return WarLobbyResult(False, _dead_block_text())
    if leader.faction is None:
        return WarLobbyResult(False, "Сначала выбери группировку.")
    lobby = storage.get_open_war_lobby_for_faction(leader.faction)
    if lobby is None:
        return WarLobbyResult(False, "У твоей группировки нет открытого военного лобби.")
    if int(lobby["leader_id"]) != telegram_id:
        return WarLobbyResult(False, "Запускать лобби может только лидер, который его создал.")
    war_id = int(lobby["id"])
    location_name = str(lobby["location"])
    host_faction = str(lobby.get("host_faction") or leader.faction)
    member_ids = tuple(storage.get_war_lobby_member_ids(war_id))
    members = [m for m in storage.get_characters_by_ids(member_ids) if m.health > 0 and m.faction]
    from app.monolith_war import MONOLITH_FACTION
    from app.faction_bots import get_faction_bots

    monolith_bot_pad = 0
    if host_faction == MONOLITH_FACTION:
        monolith_bot_pad = int(get_faction_bots(storage, MONOLITH_FACTION).get("count") or 0)
    if len(members) < WAR_MIN_FACTION_MEMBERS and len(members) + monolith_bot_pad < WAR_MIN_FACTION_MEMBERS:
        return WarLobbyResult(
            False,
            f"Для запуска нужно минимум {WAR_MIN_FACTION_MEMBERS} живых бойцов"
            + (" (или ботов Монолита)." if host_faction == MONOLITH_FACTION else "."),
        )
    if len(members) < 1:
        return WarLobbyResult(False, "В лобби нет живых бойцов.")
    active: list[Character] = []
    spent_ids: list[int] = []
    for member in members:
        if storage.spend_energy(member.telegram_id, WAR_LOBBY_ENERGY_COST):
            active.append(member)
            spent_ids.append(member.telegram_id)
    min_humans = 1 if (host_faction == MONOLITH_FACTION and monolith_bot_pad > 0) else WAR_MIN_FACTION_MEMBERS
    if len(active) < min_humans or (
        host_faction != MONOLITH_FACTION and len(active) < WAR_MIN_FACTION_MEMBERS
    ):
        _refund_spent_energy(storage, spent_ids, WAR_LOBBY_ENERGY_COST)
        return WarLobbyResult(False, "Недостаточно энергии у бойцов лобби.")
    if host_faction == MONOLITH_FACTION and len(active) + monolith_bot_pad < WAR_MIN_FACTION_MEMBERS:
        _refund_spent_energy(storage, spent_ids, WAR_LOBBY_ENERGY_COST)
        return WarLobbyResult(False, "Недостаточно бойцов и ботов Монолита для запуска.")
    target = storage.get_location(location_name)
    if target is None:
        _refund_spent_energy(storage, spent_ids, WAR_LOBBY_ENERGY_COST)
        return WarLobbyResult(False, "Локация лобби не найдена.")
    if _location_is_friendly_to_faction(storage, target, host_faction):
        _refund_spent_energy(storage, spent_ids, WAR_LOBBY_ENERGY_COST)
        return WarLobbyResult(False, "Нельзя штурмовать свою или союзническую точку.")
    member_id_list = [m.telegram_id for m in active]
    from app.monolith_war import (
        MONOLITH_FACTION,
        begin_monolith_war_window,
        should_defer_war_for_monolith,
    )
    from app.faction_bots import get_faction_bots

    defer_mode = should_defer_war_for_monolith(
        storage,
        host_faction=host_faction,
        location_name=location_name,
    )
    if defer_mode == "blocked":
        from app.monolith_war import monolith_war_status_line

        line = monolith_war_status_line(storage) or "идёт окно боя Монолита"
        _refund_spent_energy(storage, spent_ids, WAR_LOBBY_ENERGY_COST)
        return WarLobbyResult(
            False,
            f"Нельзя запустить штурм параллельно: {line}. Энергия возвращена.",
        )
    # Монолит может выйти с ботами, даже если людей меньше минимума.
    if defer_mode is None and host_faction == MONOLITH_FACTION:
        bots = get_faction_bots(storage, MONOLITH_FACTION)
        if len(active) + int(bots.get("count") or 0) >= WAR_MIN_FACTION_MEMBERS:
            defer_mode = "attack"

    if defer_mode in {"defend", "attack"}:
        pending = begin_monolith_war_window(
            storage,
            war_id=war_id,
            location_name=location_name,
            host_faction=host_faction,
            attacker_ids=member_id_list,
            mode=defer_mode,
            energy_spent_ids=spent_ids,
        )
        if defer_mode == "attack" and host_faction == MONOLITH_FACTION:
            # Боты всегда в деле при атаке Монолита (и при полном лобби, и при доборе).
            from app.monolith_war import save_pending_monolith_war

            bots = get_faction_bots(storage, MONOLITH_FACTION)
            pending["bots_sent"] = True
            pending["bot_count"] = int(bots.get("count") or 0)
            # Кто уже в лобби и списал энергию — сразу в monolith_ids (не нужно жать «вступить» ещё раз).
            names: list[str] = []
            mono_ids: list[int] = []
            for tid in member_id_list:
                ch = storage.get_character(tid, refresh_energy=False)
                if ch is None or ch.faction != MONOLITH_FACTION:
                    continue
                mono_ids.append(tid)
                names.append(str(ch.nickname))
            pending["monolith_ids"] = mono_ids
            pending["helper_names"] = names  # legacy key ignored
            pending["monolith_names"] = names
            save_pending_monolith_war(storage, pending)
        monolith_ids = storage.list_faction_member_ids(MONOLITH_FACTION)
        mode_ru = "оборона базы" if defer_mode == "defend" else "атака"
        return WarLobbyResult(
            True,
            (
                f"☢ Окно Монолита открыто ({mode_ru}): «{location_name}», "
                f"~15 мин. Монолит: вступи в бой или пошли ботов.\n"
                f"Без живых — авто-исход 90/10."
            ),
            tuple(member_id_list),
            monolith_pending=True,
            monolith_notify_ids=tuple(int(x) for x in monolith_ids),
        )

    from app.clan_war_grid import start_clan_war_grid

    tactical_result, cwar_session = start_clan_war_grid(
        storage,
        war_id=war_id,
        location_name=location_name,
        host_faction=host_faction,
        player_ids=member_id_list,
    )
    if tactical_result.ok and cwar_session is not None:
        if storage.start_war_lobby_assault(war_id):
            return WarLobbyResult(
                True,
                tactical_result.text,
                tuple(member_id_list),
                tactical_cwar=True,
            )
        from app.clan_war_grid import clear_cwar_session

        clear_cwar_session(storage, cwar_session)
        _refund_spent_energy(storage, spent_ids, WAR_LOBBY_ENERGY_COST)
        return WarLobbyResult(
            False,
            "Не удалось запустить тактический штурм. Энергия возвращена.",
        )
    if not tactical_result.ok:
        _refund_spent_energy(storage, spent_ids, WAR_LOBBY_ENERGY_COST)
        return WarLobbyResult(False, tactical_result.text)
    _refund_spent_energy(storage, spent_ids, WAR_LOBBY_ENERGY_COST)
    return WarLobbyResult(
        False,
        "Не удалось запустить тактический штурм. Энергия возвращена.",
    )


def upgrade_faction_base(storage: Storage, telegram_id: int) -> ActionResult:
    """Укрепить домашнюю базу группировки за счёт казны: +1 защитник и урон в тактическом штурме."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Сначала создай персонажа и выбери группировку.")
    if storage.get_faction_leader_id(player.faction) != telegram_id:
        return ActionResult(False, "Укреплять базу может только лидер группировки.")

    base_name = faction_home_base(player.faction)
    location = storage.get_location(base_name)
    if location is None:
        return ActionResult(False, f"База «{base_name}» не найдена.")
    if str(location.get("point_type") or "") != "база":
        return ActionResult(False, f"«{base_name}» не является базой.")
    if str(location.get("controlled_by") or "") != player.faction:
        return ActionResult(
            False,
            f"База «{base_name}» не под вашим контролем. Сначала верните её штурмом.",
        )

    if not storage.withdraw_faction_treasury(player.faction, BASE_FORTIFY_COST_RU):
        return ActionResult(
            False,
            f"В казне недостаточно средств. Нужно {BASE_FORTIFY_COST_RU} RU.",
        )

    new_bonus = storage.increment_location_defense_bonus(base_name, BASE_FORTIFY_POWER_BONUS)
    if new_bonus is None:
        storage.change_faction_treasury(player.faction, BASE_FORTIFY_COST_RU)
        return ActionResult(False, "Не удалось укрепить базу. Средства возвращены в казну.")

    return ActionResult(
        True,
        f"База «{base_name}» укреплена (−{BASE_FORTIFY_COST_RU} RU из казны).\n"
        f"Тактическая защита при штурме: +{new_bonus} (доп. защитники и урон).",
    )


def transfer_location_to_ally(storage: Storage, telegram_id: int, location_name: str, ally_faction: str) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Сначала создай персонажа и выбери группировку.")
    if storage.get_faction_leader_id(player.faction) != telegram_id:
        return ActionResult(False, "Передавать локацию может только лидер группировки.")
    if not storage.are_factions_allied(player.faction, ally_faction):
        return ActionResult(False, "Локацию можно передать только союзнику.")
    location = storage.get_location(location_name)
    if location is None:
        return ActionResult(False, "Локация не найдена.")
    if str(location.get("controlled_by") or "") != player.faction:
        return ActionResult(False, "Передавать можно только локацию своей группировки.")
    if str(location.get("point_type") or "") == "база":
        return ActionResult(False, "Базы нельзя передавать союзнику — только обычные точки.")
    from app.faction_bots import apply_location_control

    apply_location_control(storage, location_name, ally_faction)
    return ActionResult(True, f"Локация «{location_name}» передана союзнику: {ally_faction}.")


FACTION_GARAGE_META_PREFIX = "garage:"
FACTION_GARAGE_KEYS: tuple[str, ...] = ("niva", "truck", "gasoline", "diesel")


def _faction_garage_meta_key(faction: str) -> str:
    return f"{FACTION_GARAGE_META_PREFIX}{faction}"


def get_faction_garage(storage: Storage, faction: str) -> dict[str, Any]:
    """Гараж группировки: сданные Нивы/грузовики (с прочностью) и канистры топлива."""
    data: dict[str, Any] = {key: 0 for key in FACTION_GARAGE_KEYS}
    data["niva_durs"] = []
    data["truck_durs"] = []
    data["niva_owners"] = []
    data["truck_owners"] = []
    raw = storage.get_meta(_faction_garage_meta_key(faction))
    if raw:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            for key in FACTION_GARAGE_KEYS:
                try:
                    data[key] = max(0, int(parsed.get(key, 0) or 0))
                except (TypeError, ValueError):
                    data[key] = 0
            for dur_key in ("niva_durs", "truck_durs"):
                raw_durs = parsed.get(dur_key) or []
                cleaned: list[int] = []
                if isinstance(raw_durs, list):
                    for item in raw_durs:
                        try:
                            cleaned.append(max(0, min(100, int(item))))
                        except (TypeError, ValueError):
                            continue
                data[dur_key] = cleaned
            for owners_key in ("niva_owners", "truck_owners"):
                raw_owners = parsed.get(owners_key) or []
                cleaned_owners: list[int] = []
                if isinstance(raw_owners, list):
                    for item in raw_owners:
                        try:
                            cleaned_owners.append(int(item))
                        except (TypeError, ValueError):
                            continue
                data[owners_key] = cleaned_owners
    # Старые записи без списка прочности — считаем 100%.
    for kind, dur_key in (("niva", "niva_durs"), ("truck", "truck_durs")):
        durs = list(data.get(dur_key) or [])
        while len(durs) < int(data.get(kind) or 0):
            durs.append(100)
        data[dur_key] = durs[: int(data.get(kind) or 0)]
    # Владельцы не длиннее числа машин (хвост от старых багов обрезаем).
    for kind, owners_key in (("niva", "niva_owners"), ("truck", "truck_owners")):
        owners = list(data.get(owners_key) or [])
        data[owners_key] = owners[: int(data.get(kind) or 0)]
    return data


def _set_faction_garage(storage: Storage, faction: str, data: dict[str, Any]) -> None:
    payload: dict[str, Any] = {key: int(data.get(key, 0) or 0) for key in FACTION_GARAGE_KEYS}
    for dur_key in ("niva_durs", "truck_durs"):
        raw_durs = data.get(dur_key) or []
        cleaned: list[int] = []
        if isinstance(raw_durs, list):
            for item in raw_durs:
                try:
                    cleaned.append(max(0, min(100, int(item))))
                except (TypeError, ValueError):
                    continue
        payload[dur_key] = cleaned
    for owners_key in ("niva_owners", "truck_owners"):
        raw_owners = data.get(owners_key) or []
        cleaned_owners: list[int] = []
        if isinstance(raw_owners, list):
            for item in raw_owners:
                try:
                    cleaned_owners.append(int(item))
                except (TypeError, ValueError):
                    continue
        count_key = "niva" if owners_key.startswith("niva") else "truck"
        payload[owners_key] = cleaned_owners[: int(payload.get(count_key) or 0)]
    storage.set_meta(_faction_garage_meta_key(faction), json.dumps(payload, ensure_ascii=False))


def _load_garage_vehicle_rentals(storage: Storage) -> list[dict[str, Any]]:
    raw = storage.get_meta(GARAGE_VEHICLE_RENTALS_META)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in parsed:
        if isinstance(item, dict):
            cleaned.append(dict(item))
    return cleaned


def _save_garage_vehicle_rentals(storage: Storage, entries: list[dict[str, Any]]) -> None:
    storage.set_meta(GARAGE_VEHICLE_RENTALS_META, json.dumps(entries, ensure_ascii=False))


def _vehicle_label_for_key(vehicle_key: str) -> str:
    return {"niva": "Нива", "truck": "Грузовик"}.get(vehicle_key, vehicle_key)


def _vehicle_durs_key(vehicle_key: str) -> str:
    return "niva_durs" if vehicle_key == "niva" else "truck_durs"


def can_request_garage_vehicle_rental(storage: Storage, character: Character) -> bool:
    """Ранги 1–4 (не лидер и не 5+) могут запросить аренду из гаража."""
    if character.faction is None:
        return False
    if can_withdraw_faction_warehouse(storage, character):
        return False
    level = character_rank_level(character)
    return 1 <= level <= GARAGE_RENTAL_REQUEST_MAX_RANK


def _load_garage_rental_requests(storage: Storage) -> list[dict[str, Any]]:
    raw = storage.get_meta(GARAGE_RENTAL_REQUESTS_META)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [dict(item) for item in parsed if isinstance(item, dict)]


def _save_garage_rental_requests(storage: Storage, entries: list[dict[str, Any]]) -> None:
    storage.set_meta(GARAGE_RENTAL_REQUESTS_META, json.dumps(entries, ensure_ascii=False))


def _garage_rental_request_id(player_id: int, vehicle_key: str) -> str:
    return f"{int(player_id)}:{vehicle_key}"


def list_garage_rental_requests_for_faction(storage: Storage, faction: str) -> list[dict[str, Any]]:
    return [entry for entry in _load_garage_rental_requests(storage) if str(entry.get("faction") or "") == faction]


def _find_garage_rental_request(storage: Storage, request_id: str) -> dict[str, Any] | None:
    for entry in _load_garage_rental_requests(storage):
        if str(entry.get("id") or "") == request_id:
            return entry
    return None


def _has_active_garage_rental(storage: Storage, player_id: int, vehicle_key: str) -> bool:
    for entry in _load_garage_vehicle_rentals(storage):
        if (
            int(entry.get("player_id") or 0) == int(player_id)
            and str(entry.get("vehicle_key") or "") == vehicle_key
        ):
            return True
    return False


def _fuel_type_for_vehicle(vehicle_key: str) -> str:
    return "gasoline" if vehicle_key == "niva" else "diesel"


def _apply_garage_fuel_on_vehicle_issue(
    storage: Storage,
    telegram_id: int,
    player: Character,
    garage: dict[str, Any],
    vehicle_key: str,
) -> tuple[bool, str]:
    fuel_type = _fuel_type_for_vehicle(vehicle_key)
    amount = FUEL_CAN_GASOLINE_AMOUNT if fuel_type == "gasoline" else FUEL_CAN_DIESEL_AMOUNT
    label = _GARAGE_FUEL_LABELS[fuel_type]
    changer = storage.change_gasoline if fuel_type == "gasoline" else storage.change_diesel
    shop_key = "gasoline_can" if fuel_type == "gasoline" else "diesel_can"

    if int(garage.get(fuel_type, 0) or 0) > 0:
        garage[fuel_type] = int(garage.get(fuel_type, 0) or 0) - 1
        if not changer(telegram_id, amount):
            garage[fuel_type] = int(garage.get(fuel_type, 0) or 0) + 1
            return False, f"Не удалось выдать {label} из гаража."
        return True, f"Топливо из гаража: канистра {label} (+{amount})."

    personal = int(player.gasoline if fuel_type == "gasoline" else player.diesel)
    if personal >= 1:
        return True, f"Топливо из личного запаса ({label}: {personal})."

    buy_price = int(SHOP_ITEMS[shop_key]["buy_price"])
    if not storage.change_money(telegram_id, -buy_price):
        return False, (
            f"В гараже нет {label}, а на канистру не хватает денег ({buy_price} RU)."
        )
    if not changer(telegram_id, amount):
        storage.change_money(telegram_id, buy_price)
        return False, f"Не удалось оплатить канистру {label}."
    return True, f"Топливо оплачено самому: {buy_price} RU (+{amount} {label})."


def _issue_garage_vehicle(
    storage: Storage,
    telegram_id: int,
    vehicle_key: str,
    *,
    approver_note: str = "",
) -> ActionResult:
    if vehicle_key not in ("niva", "truck"):
        return ActionResult(False, "Неизвестный тип техники.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Гараж доступен только бойцам группировки.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    owned = player.niva_owned if vehicle_key == "niva" else player.truck_owned
    if owned:
        return ActionResult(False, f"У игрока уже есть {_vehicle_label_for_key(vehicle_key)}.")
    garage = get_faction_garage(storage, player.faction)
    if int(garage.get(vehicle_key, 0) or 0) <= 0:
        return ActionResult(False, f"В гараже нет свободных {_vehicle_label_for_key(vehicle_key)}.")

    fuel_ok, fuel_note = _apply_garage_fuel_on_vehicle_issue(
        storage, telegram_id, player, garage, vehicle_key
    )
    if not fuel_ok:
        return ActionResult(False, fuel_note)

    garage[vehicle_key] = int(garage.get(vehicle_key, 0) or 0) - 1
    durs_key = _vehicle_durs_key(vehicle_key)
    durs = list(garage.get(durs_key) or [])
    owners_key = f"{vehicle_key}_owners"
    owners = [int(x) for x in (garage.get(owners_key) or [])]
    reclaim = int(telegram_id) in owners
    if reclaim:
        idx = owners.index(int(telegram_id))
        owners.pop(idx)
        if 0 <= idx < len(durs):
            dur = durs.pop(idx)
        else:
            dur = durs.pop(0) if durs else 100
        garage[owners_key] = owners
    else:
        dur = durs.pop(0) if durs else 100
    garage[durs_key] = durs
    _set_faction_garage(storage, player.faction, garage)

    if not reclaim:
        _schedule_garage_vehicle_rental(
            storage,
            vehicle_key=vehicle_key,
            dur=dur,
            faction=player.faction,
            player_id=telegram_id,
        )
    if vehicle_key == "niva":
        storage.set_niva_owned(telegram_id)
        storage.set_niva_durability(telegram_id, dur)
    else:
        storage.set_truck_owned(telegram_id)
        storage.set_truck_durability(telegram_id, dur)
    storage.set_bound_transport(telegram_id, vehicle_key)

    extra = f"\n{approver_note}" if approver_note else ""
    if reclaim:
        return ActionResult(
            True,
            f"{_vehicle_label_for_key(vehicle_key)} возвращена тебе из гаража "
            f"(прочность {dur}%) — без аренды, это твоя техника.\n"
            f"{fuel_note}\n"
            f"В гараже осталось: {garage[vehicle_key]} шт.{extra}",
        )
    return ActionResult(
        True,
        f"{_vehicle_label_for_key(vehicle_key)} из гаража закреплена за тобой "
        f"(прочность {dur}%) на {GARAGE_VEHICLE_RENTAL_MINUTES} мин.\n"
        f"{fuel_note}\n"
        f"В гараже осталось: {garage[vehicle_key]} шт.{extra}",
    )


def request_garage_vehicle_rental(storage: Storage, telegram_id: int, vehicle_key: str) -> ActionResult:
    if vehicle_key not in ("niva", "truck"):
        return ActionResult(False, "Неизвестный тип техники.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Гараж доступен только бойцам группировки.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if not can_request_garage_vehicle_rental(storage, player):
        return ActionResult(
            False,
            "Запрос на аренду доступен бойцам 1–4 ранга. С 5 ранга — прямая выдача или подтверждение чужих запросов.",
        )
    if vehicle_key == "niva" and player.niva_owned:
        return ActionResult(False, "У тебя уже есть Нива.")
    if vehicle_key == "truck" and player.truck_owned:
        return ActionResult(False, "У тебя уже есть грузовик.")
    garage = get_faction_garage(storage, player.faction)
    if int(garage.get(vehicle_key, 0) or 0) <= 0:
        return ActionResult(False, f"В гараже нет {_vehicle_label_for_key(vehicle_key)} для аренды.")

    request_id = _garage_rental_request_id(telegram_id, vehicle_key)
    entries = _load_garage_rental_requests(storage)
    if any(str(entry.get("id") or "") == request_id for entry in entries):
        return ActionResult(False, "Такой запрос уже отправлен — жди подтверждения от 5+ ранга.")

    entries.append(
        {
            "id": request_id,
            "faction": player.faction,
            "player_id": int(telegram_id),
            "player_nickname": player.nickname,
            "vehicle_key": vehicle_key,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _save_garage_rental_requests(storage, entries)
    return ActionResult(
        True,
        f"Запрос на аренду {_vehicle_label_for_key(vehicle_key)} отправлен в «{player.faction}». "
        "Ожидай подтверждения от бойца 5+ ранга.",
    )


def approve_garage_rental_request(storage: Storage, approver_id: int, request_id: str) -> ActionResult:
    approver = storage.get_character(approver_id, refresh_energy=False)
    if approver is None or approver.faction is None:
        return ActionResult(False, "Сначала создай персонажа и выбери группировку.")
    if not can_withdraw_faction_warehouse(storage, approver):
        return ActionResult(False, "Подтверждать выдачу могут бойцы 5+ ранга (или лидер).")
    entry = _find_garage_rental_request(storage, request_id)
    if entry is None:
        return ActionResult(False, "Запрос не найден или уже обработан.")
    if str(entry.get("faction") or "") != approver.faction:
        return ActionResult(False, "Это запрос другой группировки.")

    player_id = int(entry.get("player_id") or 0)
    vehicle_key = str(entry.get("vehicle_key") or "")
    nickname = h(str(entry.get("player_nickname") or "?"))
    result = _issue_garage_vehicle(
        storage,
        player_id,
        vehicle_key,
        approver_note=f"Подтвердил: {approver.nickname}.",
    )
    if result.ok:
        entries = [
            item for item in _load_garage_rental_requests(storage) if str(item.get("id") or "") != request_id
        ]
        _save_garage_rental_requests(storage, entries)
        return ActionResult(
            True,
            f"Выдано игроку {nickname}: {_vehicle_label_for_key(vehicle_key)}.\n{result.text}",
        )
    return result


def deny_garage_rental_request(storage: Storage, approver_id: int, request_id: str) -> ActionResult:
    approver = storage.get_character(approver_id, refresh_energy=False)
    if approver is None or approver.faction is None:
        return ActionResult(False, "Сначала создай персонажа и выбери группировку.")
    if not can_withdraw_faction_warehouse(storage, approver):
        return ActionResult(False, "Отклонять запросы могут бойцы 5+ ранга (или лидер).")
    entry = _find_garage_rental_request(storage, request_id)
    if entry is None:
        return ActionResult(False, "Запрос не найден или уже обработан.")
    if str(entry.get("faction") or "") != approver.faction:
        return ActionResult(False, "Это запрос другой группировки.")
    nickname = h(str(entry.get("player_nickname") or "?"))
    vehicle_key = str(entry.get("vehicle_key") or "")
    entries = [
        item for item in _load_garage_rental_requests(storage) if str(item.get("id") or "") != request_id
    ]
    _save_garage_rental_requests(storage, entries)
    return ActionResult(
        True,
        f"Запрос {nickname} на {_vehicle_label_for_key(vehicle_key)} отклонён.",
    )


def build_garage_rental_requests_overview(storage: Storage, telegram_id: int) -> str:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return "Гараж доступен только бойцам группировки."
    requests = list_garage_rental_requests_for_faction(storage, player.faction)
    if not requests:
        return f"📋 Запросы на аренду «{player.faction}»: пусто."
    lines = [f"📋 Запросы на аренду «{player.faction}» ({len(requests)}):"]
    for entry in requests:
        nickname = h(str(entry.get("player_nickname") or "?"))
        vehicle = _vehicle_label_for_key(str(entry.get("vehicle_key") or ""))
        lines.append(f"• {nickname} — {vehicle}")
    lines.append("\nПодтверди или отклони запрос кнопками ниже.")
    return "\n".join(lines)


def _add_vehicle_to_garage_data(garage: dict[str, Any], *, vehicle_key: str, durs_key: str, dur: int) -> None:
    garage[vehicle_key] = int(garage.get(vehicle_key, 0) or 0) + 1
    durs = list(garage.get(durs_key) or [])
    durs.append(max(0, min(100, int(dur))))
    garage[durs_key] = durs


def _schedule_garage_vehicle_rental(
    storage: Storage,
    *,
    vehicle_key: str,
    dur: int,
    faction: str,
    player_id: int,
) -> None:
    _cancel_garage_vehicle_rental(storage, player_id=player_id, vehicle_key=vehicle_key)
    return_at = (datetime.now(timezone.utc) + timedelta(minutes=GARAGE_VEHICLE_RENTAL_MINUTES)).isoformat()
    entries = _load_garage_vehicle_rentals(storage)
    entries.append(
        {
            "vehicle_key": vehicle_key,
            "dur": max(0, min(100, int(dur))),
            "faction": faction,
            "player_id": int(player_id),
            "return_at": return_at,
        }
    )
    _save_garage_vehicle_rentals(storage, entries)


def _cancel_garage_vehicle_rental(storage: Storage, *, player_id: int, vehicle_key: str) -> None:
    entries = _load_garage_vehicle_rentals(storage)
    filtered = [
        entry
        for entry in entries
        if not (
            int(entry.get("player_id") or 0) == int(player_id)
            and str(entry.get("vehicle_key") or "") == vehicle_key
        )
    ]
    if len(filtered) != len(entries):
        _save_garage_vehicle_rentals(storage, filtered)


def process_due_garage_vehicle_rentals(storage: Storage) -> list[tuple[str, str]]:
    """Возвращает технику из аренды в гараж группировки по истечении срока."""
    now = datetime.now(timezone.utc)
    entries = _load_garage_vehicle_rentals(storage)
    if not entries:
        return []

    remaining: list[dict[str, Any]] = []
    messages: list[tuple[str, str]] = []
    for entry in entries:
        return_at = _safe_fromiso(str(entry.get("return_at") or ""))
        if return_at > now:
            remaining.append(entry)
            continue

        vehicle_key = str(entry.get("vehicle_key") or "")
        durs_key = _vehicle_durs_key(vehicle_key)
        faction = str(entry.get("faction") or "")
        player_id = int(entry.get("player_id") or 0)
        dur = max(0, min(100, int(entry.get("dur") or 100)))
        vehicle_label = _vehicle_label_for_key(vehicle_key)

        player = storage.get_character(player_id, refresh_energy=False)
        if player is not None:
            if vehicle_key == "niva" and player.niva_owned:
                dur = max(0, min(100, int(player.niva_durability)))
                storage.clear_niva_owned(player_id)
            elif vehicle_key == "truck" and player.truck_owned:
                dur = max(0, min(100, int(player.truck_durability)))
                storage.clear_truck_owned(player_id)
            storage.clear_bound_transport(player_id)

        if faction:
            garage = get_faction_garage(storage, faction)
            _add_vehicle_to_garage_data(garage, vehicle_key=vehicle_key, durs_key=durs_key, dur=dur)
            _set_faction_garage(storage, faction, garage)
            messages.append(
                (
                    f"🚗 {vehicle_label} (прочность {dur}%) возвращена в гараж «{faction}» "
                    f"после {GARAGE_VEHICLE_RENTAL_MINUTES} мин аренды.",
                    faction,
                )
            )

    _save_garage_vehicle_rentals(storage, remaining)
    return messages


def build_faction_garage_overview(storage: Storage, faction: str) -> str:
    garage = get_faction_garage(storage, faction)
    niva_durs = garage.get("niva_durs") or []
    truck_durs = garage.get("truck_durs") or []
    niva_note = f" (прочность: {', '.join(f'{d}%' for d in niva_durs)})" if niva_durs else ""
    truck_note = f" (прочность: {', '.join(f'{d}%' for d in truck_durs)})" if truck_durs else ""
    return (
        f"🏚 Гараж «{faction}»:\n"
        f"• Нив в гараже: {garage['niva']}{niva_note}\n"
        f"• Грузовиков в гараже: {garage['truck']}{truck_note}\n"
        f"• Канистр бензина (+{FUEL_CAN_GASOLINE_AMOUNT} каждая): {garage['gasoline']}\n"
        f"• Канистр дизеля (+{FUEL_CAN_DIESEL_AMOUNT} каждая): {garage['diesel']}\n\n"
        "Сдать канистру можно из своего запаса топлива; забрать — с 5 ранга (или лидеру).\n"
        f"Ранги 1–4 — запрос на аренду; 5+ — выдача или подтверждение запроса.\n"
        f"При выдаче топливо берётся из гаража; если канистр пуст — платишь сам.\n"
        f"Своя сданная техника возвращается без аренды; чужая/общая — аренда "
        f"{GARAGE_VEHICLE_RENTAL_MINUTES} мин.\n"
        "Перед сдачей арендованного грузовика — полный ремонт.\n"
        "На арендованной технике нельзя слезть и идти пешком."
    )


_GARAGE_FUEL_LABELS: dict[str, str] = {"gasoline": "бензина", "diesel": "дизеля"}


def garage_deposit_fuel(storage: Storage, telegram_id: int, fuel_type: str) -> ActionResult:
    if fuel_type not in ("gasoline", "diesel"):
        return ActionResult(False, "Неизвестный тип топлива для гаража.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Гараж доступен только бойцам группировки.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    amount = FUEL_CAN_GASOLINE_AMOUNT if fuel_type == "gasoline" else FUEL_CAN_DIESEL_AMOUNT
    changer = storage.change_gasoline if fuel_type == "gasoline" else storage.change_diesel
    label = _GARAGE_FUEL_LABELS[fuel_type]
    if not changer(telegram_id, -amount):
        return ActionResult(False, f"Недостаточно {label} для сдачи канистры (нужно {amount}).")
    garage = get_faction_garage(storage, player.faction)
    garage[fuel_type] = garage.get(fuel_type, 0) + 1
    _set_faction_garage(storage, player.faction, garage)
    return ActionResult(
        True,
        f"В гараж «{player.faction}» сдана канистра {label} (−{amount} из личного запаса).\n"
        f"В гараже теперь: {garage[fuel_type]} канистр {label}.",
    )


def garage_withdraw_fuel(storage: Storage, telegram_id: int, fuel_type: str) -> ActionResult:
    if fuel_type not in ("gasoline", "diesel"):
        return ActionResult(False, "Неизвестный тип топлива для гаража.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Гараж доступен только бойцам группировки.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if not can_withdraw_faction_warehouse(storage, player):
        return ActionResult(False, "Забирать из гаража можно с 5 ранга (или лидеру группировки).")
    label = _GARAGE_FUEL_LABELS[fuel_type]
    garage = get_faction_garage(storage, player.faction)
    if garage.get(fuel_type, 0) <= 0:
        return ActionResult(False, f"В гараже нет канистр {label}.")
    amount = FUEL_CAN_GASOLINE_AMOUNT if fuel_type == "gasoline" else FUEL_CAN_DIESEL_AMOUNT
    changer = storage.change_gasoline if fuel_type == "gasoline" else storage.change_diesel
    if not changer(telegram_id, amount):
        return ActionResult(False, "Не удалось получить топливо.")
    garage[fuel_type] = max(0, garage.get(fuel_type, 0) - 1)
    _set_faction_garage(storage, player.faction, garage)
    return ActionResult(
        True,
        f"Из гаража «{player.faction}» получена канистра {label} (+{amount} в личный запас).\n"
        f"В гараже осталось: {garage[fuel_type]} канистр {label}.",
    )


def garage_deposit_niva(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Гараж доступен только бойцам группировки.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if not player.niva_owned:
        return ActionResult(False, "У тебя нет Нивы, чтобы сдать её в гараж.")
    dur = max(0, min(100, int(player.niva_durability)))
    _cancel_garage_vehicle_rental(storage, player_id=telegram_id, vehicle_key="niva")
    storage.clear_niva_owned(telegram_id)
    storage.clear_bound_transport(telegram_id)
    garage = get_faction_garage(storage, player.faction)
    garage["niva"] = int(garage.get("niva", 0) or 0) + 1
    durs = list(garage.get("niva_durs") or [])
    durs.append(dur)
    garage["niva_durs"] = durs
    owners = [int(x) for x in (garage.get("niva_owners") or [])]
    owners.append(int(telegram_id))
    garage["niva_owners"] = owners
    _set_faction_garage(storage, player.faction, garage)
    return ActionResult(
        True,
        f"Нива сдана в гараж «{player.faction}» (прочность {dur}%). В гараже: {garage['niva']} шт.\n"
        f"Забрать обратно можно без аренды (кнопка «Забрать Ниву»).",
    )


def garage_withdraw_niva(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Гараж доступен только бойцам группировки.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if not can_withdraw_faction_warehouse(storage, player):
        return ActionResult(False, "Забирать из гаража можно с 5 ранга (или лидеру группировки).")
    return _issue_garage_vehicle(storage, telegram_id, "niva")


def garage_deposit_truck(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Гараж доступен только бойцам группировки.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if not player.truck_owned:
        return ActionResult(False, "У тебя нет грузовика, чтобы сдать его в гараж.")
    if _has_active_garage_rental(storage, telegram_id, "truck"):
        if int(player.truck_durability) < 100:
            return ActionResult(
                False,
                "Перед сдачей арендованного грузовика нужен полный ремонт (100% прочности).",
            )
    dur = max(0, min(100, int(player.truck_durability)))
    _cancel_garage_vehicle_rental(storage, player_id=telegram_id, vehicle_key="truck")
    storage.clear_truck_owned(telegram_id)
    storage.clear_bound_transport(telegram_id)
    garage = get_faction_garage(storage, player.faction)
    garage["truck"] = int(garage.get("truck", 0) or 0) + 1
    durs = list(garage.get("truck_durs") or [])
    durs.append(dur)
    garage["truck_durs"] = durs
    owners = [int(x) for x in (garage.get("truck_owners") or [])]
    owners.append(int(telegram_id))
    garage["truck_owners"] = owners
    _set_faction_garage(storage, player.faction, garage)
    return ActionResult(
        True,
        f"Грузовик сдан в гараж «{player.faction}» (прочность {dur}%). В гараже: {garage['truck']} шт.\n"
        f"Забрать обратно можно без аренды (кнопка «Забрать грузовик»).",
    )


def garage_withdraw_truck(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Гараж доступен только бойцам группировки.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if not can_withdraw_faction_warehouse(storage, player):
        return ActionResult(False, "Забирать из гаража можно с 5 ранга (или лидеру группировки).")
    return _issue_garage_vehicle(storage, telegram_id, "truck")


def build_faction_group_overview(storage: Storage, telegram_id: int) -> str:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return "Группировка доступна только после выбора группировки."

    income_result = apply_controlled_points_income(storage)
    income_note = f"\n{income_result.text}\n" if income_result.ok else "\n"

    warehouse = storage.get_faction_warehouse(player.faction)
    factions = storage.get_factions()
    faction_info = next((f for f in factions if f["name"] == player.faction), None)
    treasury = int(faction_info["treasury"]) if faction_info else 0
    warehouse_lines = [
        f"• {ITEM_LABELS.get(k, k)}: {v}"
        for k, v in sorted(warehouse.items())
        if v > 0
    ]
    if not warehouse_lines:
        warehouse_lines = ["• Склад пуст"]

    leader_hint = ""
    if storage.get_faction_leader_id(player.faction) == telegram_id:
        leader_hint = (
            "\nТебе доступны вывод со склада/из казны, назначение званий "
            f"и укрепление базы (−{BASE_FORTIFY_COST_RU} RU из казны)."
        )
    elif can_withdraw_faction_warehouse(storage, player):
        leader_hint = "\nТебе доступен вывод со склада (ранг 5+)."

    home_name = faction_home_base(player.faction)
    home = storage.get_location(home_name)
    if home is None:
        base_line = f"Домашняя база: «{home_name}» (нет данных)"
    else:
        owner = str(home.get("controlled_by") or "нейтрал")
        bonus = max(0, int(home.get("defense_bonus") or 0))
        control = "ваша" if owner == player.faction else f"контроль: {owner}"
        base_line = (
            f"Домашняя база: «{home_name}» ({control})\n"
            f"Укрепление: +{bonus} (доп. защитники и урон при тактическом штурме)"
        )

    # Дипломатия: союзы и войны с другими группировками.
    diplomacy_lines: list[str] = []
    for other in FACTION_RATING_ORDER:
        if other == player.faction:
            continue
        if storage.are_factions_allied(player.faction, other):
            diplomacy_lines.append(f"🟩 {other} — союзники")
        else:
            diplomacy_lines.append(f"🟥 {other} — война")

    # Живой рейтинг: пара онлайн-игроков группировки с их очками.
    online_members: list[tuple[str, int]] = []
    for member in storage.list_faction_members(player.faction):
        member_id = int(member.get("telegram_id") or 0)
        if member_id and is_player_online(storage, member_id):
            stats = storage.get_player_stats(member_id)
            online_members.append(
                (str(member.get("nickname") or str(member_id)), int(stats.get("rating_points") or 0))
            )
    online_members.sort(key=lambda row: -row[1])
    if online_members:
        live_rating_lines = [f"🟢 {h(nick)} — {rating} очк." for nick, rating in online_members[:2]]
    else:
        live_rating_lines = ["Сейчас никого в сети."]

    garage_overview = build_faction_garage_overview(storage, player.faction)
    from app.faction_bots import build_faction_bots_overview

    bots_overview = build_faction_bots_overview(storage, player.faction)

    return (
        f"Группировка «{player.faction}»\n"
        f"Казна: {treasury} RU"
        f"{income_note}"
        f"{base_line}\n\n"
        f"⚔️ Отношения:\n{chr(10).join(diplomacy_lines)}\n\n"
        f"🔥 Живой рейтинг:\n{chr(10).join(live_rating_lines)}\n\n"
        f"Склад:\n{chr(10).join(warehouse_lines)}\n\n"
        f"{garage_overview}\n\n"
        f"{bots_overview}\n\n"
        f"Пассивный доход с точек:\n"
        f"• точка ресурсов: {RESOURCE_POINT_INCOME_PER_HOUR} RU/ч\n"
        f"• база: {BASE_POINT_INCOME_PER_HOUR} RU/ч\n\n"
        f"Любой боец может сдать патроны/аптечки на склад и пополнить казну.\n"
        f"Забирать со склада — с 5 ранга (или лидер). Из казны — только лидер.\n"
        f"Лидер может укрепить базу за {BASE_FORTIFY_COST_RU} RU "
        f"(+{BASE_FORTIFY_POWER_BONUS}: доп. защитники и урон в тактическом штурме)."
        f"{leader_hint}"
    )


def build_economy_overview(storage: Storage, telegram_id: int) -> str:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return "Барахолка доступна только после выбора группировки."

    auctions = _list_open_exchange_lots(storage)
    auctions_lines = [
        f"• Ячейка №{a['id']}: {ITEM_LABELS.get(str(a['item_key']), str(a['item_key']))} "
        f"x{a['amount']} за {a['price']} RU"
        for a in auctions[:5]
    ]
    if not auctions_lines:
        auctions_lines = ["• Открытых ячеек нет"]

    market_lots = list_market_lots(storage, telegram_id)
    market_lines = [
        f"• Ячейка №{lot['id']}: {ITEM_LABELS.get(str(lot['item_key']), str(lot['item_key']))} "
        f"x{lot['amount']} за {lot['price']} RU"
        for lot in market_lots[:5]
    ]
    if not market_lines:
        market_lines = ["• Открытых ячеек нет"]

    return (
        "🏦 Барахолка\n\n"
        "⚖️ Биржа — расходники, артефакты, топливо, мусор.\n"
        f"Комиссия {EXCHANGE_SELL_FEE_PERCENT}%: покупатель платит цену, продавец получает остаток.\n"
        f"С покупки {BAZAAR_FACTION_TAX_PERCENT}% цены уходит в казну ГП продавца (из комиссии).\n"
        "Выставляй через «свой лот», покупай по номеру ячейки (с подтверждением).\n\n"
        f"🛒 Рынок — только оружие и броня.\n"
        f"Комиссия {MARKET_SELL_FEE_PERCENT}%.\n"
        f"Тот же налог {BAZAAR_FACTION_TAX_PERCENT}% в казну продавца.\n"
        "Выставляй экипировку из инвентаря, покупай по номеру ячейки.\n\n"
        f"Биржа (открытые):\n{chr(10).join(auctions_lines)}\n\n"
        f"Рынок (открытые):\n{chr(10).join(market_lines)}\n\n"
        f"{build_smuggling_overview(storage, telegram_id)}"
    )


def apply_controlled_points_income(storage: Storage) -> ActionResult:
    """Начисляет доход с контролируемых точек в казну группировок."""
    raw_last = storage.get_meta(POINTS_INCOME_META_KEY)
    now = datetime.now(timezone.utc)
    if raw_last:
        try:
            last = datetime.fromisoformat(raw_last)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
        except ValueError:
            last = now - timedelta(hours=1)
    else:
        last = now - timedelta(hours=1)

    hours = int((now - last).total_seconds() // 3600)
    if hours <= 0:
        return ActionResult(False, "Приток с точек пока не начислен (меньше часа).")
    hours = min(hours, POINTS_INCOME_MAX_HOURS)

    totals: dict[str, int] = {}
    for location in storage.get_locations():
        owner = location.get("controlled_by")
        if not owner:
            continue
        point_type = str(location.get("point_type") or "")
        if point_type == "точка ресурсов":
            income = RESOURCE_POINT_INCOME_PER_HOUR * hours
        elif point_type == "база":
            income = BASE_POINT_INCOME_PER_HOUR * hours
        else:
            continue
        storage.change_faction_treasury(str(owner), income)
        totals[str(owner)] = totals.get(str(owner), 0) + income

    paid_until = last + timedelta(hours=hours)
    storage.set_meta(POINTS_INCOME_META_KEY, paid_until.isoformat())

    if not totals:
        return ActionResult(True, f"Прошло {hours} ч., но контролируемых ресурсных точек/баз нет.")
    lines = [f"Приток за {hours} ч.:"]
    for faction, amount in sorted(totals.items()):
        lines.append(f"• {faction}: +{amount} RU в казну")
    return ActionResult(True, "\n".join(lines))


PLAYERS_PAGE_SIZE = 10
PLAYERS_FACTION_ORDER = ("Долг", "Свобода", "Нейтралы", "Бандиты", "Монолит")
PLAYERS_NO_FACTION_KEY = "_none"
PLAYERS_NO_FACTION_LABEL = "Без группировки"


def _players_nickname_sort_key(row: dict[str, Any]) -> str:
    return str(row.get("nickname") or "").casefold()


def _players_rank_sort_key(
    storage: Storage,
    row: dict[str, Any],
    *,
    leader_id: int | None,
) -> tuple[int, int, str]:
    """Сортировка по должности: лидер → выше ранг → ник."""
    telegram_id = int(row.get("telegram_id") or 0)
    is_leader = leader_id is not None and telegram_id == leader_id
    # Лидер выше всех (0), остальные — по убыванию level (инвертируем).
    leader_order = 0 if is_leader else 1
    faction = str(row.get("faction") or "").strip() or None
    rank = rank_by_key(faction, row.get("faction_rank"))
    rank_level = rank.level if rank is not None else 0
    if is_leader:
        rank_level = 99
    return (leader_order, -rank_level, _players_nickname_sort_key(row))


def group_players_by_faction(storage: Storage) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """[(faction_key, title, players_sorted), ...] с известными гп сверху."""
    rows = storage.list_players(limit=500)
    buckets: dict[str, list[dict[str, Any]]] = {key: [] for key in PLAYERS_FACTION_ORDER}
    buckets[PLAYERS_NO_FACTION_KEY] = []
    extra: dict[str, list[dict[str, Any]]] = {}
    leaders: dict[str, int | None] = {
        faction["name"]: (int(faction["leader_id"]) if faction.get("leader_id") is not None else None)
        for faction in storage.get_factions()
    }

    for row in rows:
        faction = str(row.get("faction") or "").strip()
        if not faction:
            buckets[PLAYERS_NO_FACTION_KEY].append(row)
        elif faction in buckets:
            buckets[faction].append(row)
        else:
            extra.setdefault(faction, []).append(row)

    result: list[tuple[str, str, list[dict[str, Any]]]] = []
    for faction in PLAYERS_FACTION_ORDER:
        leader_id = leaders.get(faction)
        players = sorted(
            buckets[faction],
            key=lambda row: _players_rank_sort_key(storage, row, leader_id=leader_id),
        )
        result.append((faction, faction, players))
    for faction in sorted(extra.keys(), key=lambda name: name.casefold()):
        leader_id = leaders.get(faction)
        players = sorted(
            extra[faction],
            key=lambda row: _players_rank_sort_key(storage, row, leader_id=leader_id),
        )
        result.append((faction, faction, players))
    none_players = sorted(buckets[PLAYERS_NO_FACTION_KEY], key=_players_nickname_sort_key)
    result.append((PLAYERS_NO_FACTION_KEY, PLAYERS_NO_FACTION_LABEL, none_players))
    return result


PLAYER_LAST_ACTIVE_PREFIX = "player_last_active:"
PLAYER_ONLINE_WINDOW_SEC = 15 * 60


def _player_last_active_key(telegram_id: int) -> str:
    return f"{PLAYER_LAST_ACTIVE_PREFIX}{int(telegram_id)}"


def touch_player_activity(storage: Storage, telegram_id: int) -> None:
    from datetime import datetime, timezone

    storage.set_meta(_player_last_active_key(telegram_id), datetime.now(timezone.utc).isoformat())


def is_player_online(storage: Storage, telegram_id: int) -> bool:
    from datetime import datetime, timezone

    raw = storage.get_meta(_player_last_active_key(telegram_id))
    if not raw:
        return False
    try:
        seen = datetime.fromisoformat(raw)
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - seen).total_seconds()
        return age <= PLAYER_ONLINE_WINDOW_SEC
    except (TypeError, ValueError):
        return False


def trader_balance_line(money: int) -> str:
    return f"💰 Твой баланс: {money:,} RU"


def trader_screen_text(storage: Storage, telegram_id: int, body: str) -> str:
    player = storage.get_character(telegram_id, refresh_energy=False)
    money = int(player.money) if player is not None else 0
    return f"{trader_balance_line(money)}\n\n{body}"


def build_players_root_text(storage: Storage) -> tuple[str, list[tuple[str, str, int]]]:
    groups = group_players_by_faction(storage)
    total = sum(len(players) for _, _, players in groups)
    menu_items = [(key, title, len(players)) for key, title, players in groups]
    if total == 0:
        return ("Игроков пока нет.", menu_items)

    lines = [
        "👥 Игроки Зоны",
        "Выбери группировку. Внутри — по должности (выше ранг сверху), по 10 на страницу.",
        f"Всего игроков: {total}",
        "",
    ]
    for _key, title, count in menu_items:
        lines.append(f"• {title}: {count}")
    return ("\n".join(lines), menu_items)


def build_players_faction_page_text(
    storage: Storage,
    faction_key: str,
    page: int = 0,
) -> tuple[str, str, int, int, list[dict[str, Any]]]:
    """Возвращает (text, faction_key, page, total_pages, page_players)."""
    groups = {key: (title, players) for key, title, players in group_players_by_faction(storage)}
    if faction_key not in groups:
        return ("Группировка не найдена. Вернись к списку группировок.", faction_key, 0, 1, [])

    title, players = groups[faction_key]
    total = len(players)
    if total == 0:
        return (f"👥 {title}\nПока никого нет.", faction_key, 0, 1, [])

    total_pages = max(1, (total + PLAYERS_PAGE_SIZE - 1) // PLAYERS_PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    start = safe_page * PLAYERS_PAGE_SIZE
    chunk = players[start : start + PLAYERS_PAGE_SIZE]

    lines = [
        f"👥 {title}",
        f"Страница {safe_page + 1}/{total_pages} • игроков: {total}",
        "🟢 — в сети (активность за 15 мин). Нажми «⚔️ Вызвать на дуэль» или /дуэль [telegram_id].",
        "",
    ]
    for row in chunk:
        tid = int(row["telegram_id"])
        member = storage.get_character(tid, refresh_energy=False)
        rank = character_rank_title(storage, member) if member else None
        rank_part = f" [{rank}]" if rank else ""
        online = "🟢" if is_player_online(storage, tid) else "⚫"
        lines.append(f"• {online} {row['nickname']}{rank_part} — {tid}")
    return ("\n".join(lines), faction_key, safe_page, total_pages, chunk)


def build_faction_broadcast_text(
    storage: Storage,
    telegram_id: int,
    custom_text: str | None = None,
) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Сначала выбери группировку.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if storage.get_faction_leader_id(player.faction) != telegram_id:
        return ActionResult(False, "Рассылку может делать только командир группировки.")
    body = (custom_text or "Бойцы, общий сбор!").strip()
    if not body:
        return ActionResult(False, "Текст рассылки пустой.")
    text = f"📣 [{h(player.faction or '')}] {h(player.nickname)}:\n{h(body)}"
    return ActionResult(True, text)


def list_faction_broadcast_targets(storage: Storage, telegram_id: int) -> list[int]:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return []
    return [tid for tid in storage.list_faction_member_ids(player.faction) if tid != telegram_id]


def _safe_base_location_names(storage: Storage) -> set[str]:
    return {
        str(location["name"])
        for location in storage.get_locations()
        if str(location.get("point_type") or "") == "база"
    }


def _parse_meta_datetime(raw: str | None, fallback: datetime) -> datetime:
    if not raw:
        return fallback
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


EMISSION_WAVE1_LOCATIONS: frozenset[str] = frozenset({"Радар", "Рыжий лес", "Янтарь"})
EMISSION_WAVE2_LOCATIONS: frozenset[str] = frozenset({"Болото", "НИИ Агропром"})
EMISSION_WAVE_LABELS: dict[str, str] = {
    "wave1": "1-я волна (опасные зоны: Радар, Рыжий лес, Янтарь)",
    "wave2": "2-я волна (средние зоны: Болото, НИИ Агропром)",
    "wave3": "3-я волна (остальная территория Зоны)",
}


def _emission_wave_locations(phase: str, storage: Storage) -> set[str]:
    all_names = {str(location["name"]) for location in storage.get_locations()}
    safe_bases = _safe_base_location_names(storage)
    non_base = all_names - safe_bases
    if phase == "wave1":
        return non_base & EMISSION_WAVE1_LOCATIONS
    if phase == "wave2":
        return non_base & EMISSION_WAVE2_LOCATIONS
    return non_base - EMISSION_WAVE1_LOCATIONS - EMISSION_WAVE2_LOCATIONS


def _kill_players_in_locations(
    storage: Storage,
    targets: set[str],
    safe_bases: set[str],
) -> tuple[list[str], list[int]]:
    killed: list[str] = []
    killed_ids: list[int] = []
    now = datetime.now(timezone.utc)
    for telegram_id in storage.list_player_ids():
        character = storage.get_character(telegram_id, refresh_energy=False)
        if character is None or int(character.health) <= 0:
            continue
        if character.location not in targets:
            continue
        if is_traveling(character) and character.travel_destination in safe_bases:
            continue
        if has_emission_protection(storage, telegram_id, now):
            continue
        storage.change_health(telegram_id, -int(character.health))
        remember_death_cause(storage, telegram_id, "emission")
        killed.append(character.nickname or str(telegram_id))
        killed_ids.append(telegram_id)
    return killed, killed_ids


def _execute_emission_wave(
    storage: Storage,
    phase: str,
    now: datetime,
    notify_ids: list[int],
) -> tuple[str, list[int], list[int]]:
    safe_bases = _safe_base_location_names(storage)
    targets = _emission_wave_locations(phase, storage)
    killed, killed_ids = _kill_players_in_locations(storage, targets, safe_bases)

    locations_text = ", ".join(sorted(targets)) if targets else "нет целей на этом этапе"
    killed_text = (
        ", ".join(killed[:20]) + ("…" if len(killed) > 20 else "")
        if killed
        else "никто не пострадал"
    )
    wave_label = EMISSION_WAVE_LABELS.get(phase, phase)

    next_phase = {"wave1": "wave2", "wave2": "wave3", "wave3": "resolve"}[phase]
    if next_phase == "resolve":
        next_at = now + timedelta(hours=EMISSION_INTERVAL_HOURS)
        storage.set_meta(EMISSION_META_AT, next_at.isoformat())
        storage.set_meta(EMISSION_META_WARN60, "0")
        storage.set_meta(EMISSION_META_WARN30, "0")
        storage.set_meta(EMISSION_META_PHASE, "calm")
        storage.delete_meta(EMISSION_META_WAVE_AT)
        hotspot_line = ""
        try:
            from app.artifact_features import set_post_emission_artifact_hotspot

            hotspot_loc = set_post_emission_artifact_hotspot(storage)
            if hotspot_loc:
                hotspot_line = f"\n\n🔥 Горячая точка артефактов: «{hotspot_loc}» (×1.5 к локальному дропу, 24 ч)."
        except Exception:
            logger.exception("Failed to set post-emission artifact hotspot")
        message = (
            f"💥 ВЫБРОС — {wave_label} прошла!\n"
            f"Локации: {locations_text}.\n"
            f"Погибшие: {killed_text}.\n\n"
            f"☢️ Выброс полностью завершён. Следующий примерно через {EMISSION_INTERVAL_HOURS} ч."
            f"{hotspot_line}"
        )
    else:
        next_wave_at = now + timedelta(minutes=EMISSION_WAVE_GAP_MINUTES)
        storage.set_meta(EMISSION_META_PHASE, next_phase)
        storage.set_meta(EMISSION_META_WAVE_AT, next_wave_at.isoformat())
        message = (
            f"💥 ВЫБРОС — {wave_label} прошла!\n"
            f"Локации: {locations_text}.\n"
            f"Погибшие: {killed_text}.\n"
            f"Следующая волна через ~{EMISSION_WAVE_GAP_MINUTES} мин. "
            f"Уходи на базу, если ещё в опасной зоне."
        )
    return (message, notify_ids, killed_ids)


def process_emission_cycle(storage: Storage) -> tuple[str, list[int], list[int]]:
    """Цикл Выброса: предупреждения за 60/30 мин, затем волны убийства по зонам.

    Вместо мгновенного глобального килла Выброс идёт волнами: сначала опасные
    локации (Радар/Рыжий лес/Янтарь), затем средние (Болото/НИИ Агропром), затем
    остальная территория. Между волнами пауза EMISSION_WAVE_GAP_MINUTES.

    Возвращает (текст оповещения, telegram_id для рассылки, telegram_id убитых Выбросом).
    Пустой текст — нечего слать.
    """
    now = datetime.now(timezone.utc)
    notify_ids = storage.list_player_ids()
    phase = storage.get_meta(EMISSION_META_PHASE) or "calm"

    if phase in ("wave1", "wave2", "wave3"):
        raw_wave_at = storage.get_meta(EMISSION_META_WAVE_AT)
        wave_at = _parse_meta_datetime(raw_wave_at, now)
        if now < wave_at:
            return ("", [], [])
        return _execute_emission_wave(storage, phase, now, notify_ids)

    raw_at = storage.get_meta(EMISSION_META_AT)
    warn60 = storage.get_meta(EMISSION_META_WARN60) == "1"
    warn30 = storage.get_meta(EMISSION_META_WARN30) == "1"

    if raw_at is None:
        emission_at = now + timedelta(hours=EMISSION_INTERVAL_HOURS)
        storage.set_meta(EMISSION_META_AT, emission_at.isoformat())
        storage.set_meta(EMISSION_META_WARN60, "0")
        storage.set_meta(EMISSION_META_WARN30, "0")
        return ("", [], [])

    emission_at = _parse_meta_datetime(raw_at, now + timedelta(hours=EMISSION_INTERVAL_HOURS))
    minutes_left = (emission_at - now).total_seconds() / 60.0

    if minutes_left > EMISSION_WARN_60_MINUTES:
        return ("", [], [])

    base_names = ", ".join(sorted(_safe_base_location_names(storage))) or "базы группировок"

    if minutes_left > EMISSION_WARN_30_MINUTES and not warn60:
        storage.set_meta(EMISSION_META_WARN60, "1")
        return (
            "⚠️ ВЫБРОС через 60 минут!\n"
            "Если ты не на базе к моменту Выброса — персонаж погибнет, когда волна дойдёт до твоей зоны.\n"
            f"Безопасные базы: {base_names}.",
            notify_ids,
            [],
        )

    if 0 < minutes_left <= EMISSION_WARN_30_MINUTES:
        if not warn60:
            storage.set_meta(EMISSION_META_WARN60, "1")
        if not warn30:
            storage.set_meta(EMISSION_META_WARN30, "1")
            return (
                "☢️ ВЫБРОС через 30 минут!\n"
                "Срочно уходи на базу — Выброс пойдёт волнами по зонам, начиная с самых опасных.\n"
                f"Безопасные базы: {base_names}.",
                notify_ids,
                [],
            )
        return ("", [], [])

    if minutes_left > 0:
        return ("", [], [])

    # Старт волн: первая волна выполняется сразу этим тиком.
    return _execute_emission_wave(storage, "wave1", now, notify_ids)


def build_emission_status(storage: Storage) -> str:
    now = datetime.now(timezone.utc)
    phase = storage.get_meta(EMISSION_META_PHASE) or "calm"
    if phase in ("wave1", "wave2", "wave3"):
        raw_wave_at = storage.get_meta(EMISSION_META_WAVE_AT)
        wave_at = _parse_meta_datetime(raw_wave_at, now)
        minutes_left = max(0, int((wave_at - now).total_seconds() // 60))
        wave_label = EMISSION_WAVE_LABELS.get(phase, phase)
        bases = ", ".join(sorted(_safe_base_location_names(storage))) or "базы"
        return (
            f"☢️ ВЫБРОС ИДЁТ: {wave_label}.\n"
            f"Следующая волна через ~{minutes_left} мин.\n"
            f"Безопасно только на базах: {bases}."
        )
    raw_at = storage.get_meta(EMISSION_META_AT)
    if raw_at is None:
        return (
            f"Выброс: расписание ещё не запущено "
            f"(цикл раз в {EMISSION_INTERVAL_HOURS} ч., предупреждения за 60 и 30 мин, "
            "затем волны по зонам)."
        )
    emission_at = _parse_meta_datetime(raw_at, now + timedelta(hours=EMISSION_INTERVAL_HOURS))
    minutes_left = max(0, int((emission_at - now).total_seconds() // 60))
    hours_left = minutes_left // 60
    mins = minutes_left % 60
    bases = ", ".join(sorted(_safe_base_location_names(storage))) or "базы"
    return (
        f"Выброс через ~{hours_left} ч. {mins} мин.\n"
        f"Вне базы ({bases}) Выброс убивает волнами: сначала опасные зоны, потом остальные.\n"
        "Оповещения: за 60 и 30 минут."
    )


def _roll_smuggling_loot(storage: Storage, telegram_id: int) -> list[str]:
    """Независимые роллы дропа контрабанды. Возвращает строки для отчёта."""
    drops: list[str] = []

    if random.randint(1, 100) <= SMUGGLING_CONSUMABLE_CHANCE:
        amount = random.randint(1, 2)
        storage.add_item(telegram_id, "medkit", amount)
        drops.append(f"{ITEM_LABELS['medkit']} x{amount}")

    if random.randint(1, 100) <= SMUGGLING_CONSUMABLE_CHANCE:
        food_key = random.choice(SMUGGLING_FOOD_DROP_KEYS)
        amount = random.randint(1, 2)
        storage.add_item(telegram_id, food_key, amount)
        drops.append(f"{ITEM_LABELS.get(food_key, food_key)} x{amount}")

    if random.randint(1, 100) <= SMUGGLING_CONSUMABLE_CHANCE:
        water_key = random.choice(SMUGGLING_WATER_DROP_KEYS)
        amount = random.randint(1, 2)
        storage.add_item(telegram_id, water_key, amount)
        drops.append(f"{ITEM_LABELS.get(water_key, water_key)} x{amount}")

    if random.randint(1, 100) <= SMUGGLING_ARMOR_T2_CHANCE:
        armor_key = random.choice(SMUGGLING_ARMOR_T2_KEYS)
        storage.add_item(telegram_id, armor_key, 1)
        drops.append(ITEM_LABELS.get(armor_key, armor_key))

    if random.randint(1, 100) <= SMUGGLING_WEAPON_T1_CHANCE:
        weapon_key = random.choice(SMUGGLING_WEAPON_T1_KEYS)
        storage.add_item(telegram_id, weapon_key, 1)
        drops.append(ITEM_LABELS.get(weapon_key, weapon_key))

    if random.randint(1, 100) <= SMUGGLING_OTKLIK_CHANCE:
        storage.add_item(telegram_id, "detector_otklik", 1)
        drops.append(ITEM_LABELS["detector_otklik"])

    return drops


def _smuggle_meta_key(telegram_id: int) -> str:
    return f"{SMUGGLING_META_PREFIX}{int(telegram_id)}"


def get_active_smuggling(storage: Storage, telegram_id: int) -> dict[str, Any] | None:
    raw = storage.get_meta(_smuggle_meta_key(telegram_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def clear_active_smuggling(storage: Storage, telegram_id: int) -> None:
    storage.delete_meta(_smuggle_meta_key(telegram_id))


def _release_smuggle_vehicle(storage: Storage, telegram_id: int, active: dict[str, Any]) -> None:
    transport = str(active.get("transport") or "").strip()
    if transport in ("niva", "truck") and storage.get_bound_transport(telegram_id) == transport:
        storage.clear_bound_transport(telegram_id)


def list_smuggling_destinations(storage: Storage, telegram_id: int) -> list[str]:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return []
    return sorted(
        str(loc["name"])
        for loc in storage.get_locations()
        if str(loc["name"]) != player.location and str(loc["name"]) in MAP_TRAVEL_POINTS
    )


def _smuggling_success_chance(
    character: Character,
    *,
    transport_mode: str,
    base_minutes: int,
    event_modifier: int,
) -> int:
    transport_bonus = SMUGGLING_TRANSPORT_BONUS.get(transport_mode, 0)
    # Чем длиннее путь, тем выше риск ограбления.
    distance_penalty = min(12, max(0, int(base_minutes) // 3 - 2))
    raw = (
        SMUGGLING_BASE_CHANCE
        + equipment_power(character) * 3
        + transport_bonus
        - distance_penalty
        - max(0, int(event_modifier))
    )
    return min(90, max(20, raw))


def build_smuggling_overview(storage: Storage, telegram_id: int) -> str:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return "Сначала создай персонажа."
    active = get_active_smuggling(storage, telegram_id)
    from app.smuggle_mission import get_smuggle_session

    lines = [
        "🚚 Перевозка контрабанды",
        "1) Выбери точку сдачи → 2) тактическая карта (маршрут из 3 точек) → 3) выезд с таймером прибытия.",
        f"Награда: {SMUGGLING_REWARD_MIN}–{SMUGGLING_REWARD_MAX} RU gross (⅓ в казну) + дроп.",
        "Провал / тайм-аут = ограбление (−RU, −HP).",
        "Бонусы шанса: пешком 0, велосипед +3, Нива +6, грузовик +12.",
        "",
    ]
    grid = get_smuggle_session(storage, telegram_id)
    if grid is not None:
        lines.append(
            f"Рейс на карте → «{grid.destination}» "
            f"(точки {grid.route_index}/{len(grid.route)}, ход {grid.moves}/{grid.max_moves}, "
            f"шанс ~{grid.success_chance}%)."
        )
    elif active:
        dest = str(active.get("destination") or "?")
        chance = int(active.get("success_chance") or 0)
        mode = str(active.get("mode") or "")
        if mode == "travel" and is_traveling(player):
            remaining = (
                format_remaining_travel(player.travel_arrives_at)
                if player.travel_arrives_at is not None
                else "скоро"
            )
            lines.append(
                f"Выезд к «{dest}» — прибытие через {remaining} (шанс ~{chance}%)."
            )
        else:
            lines.append(f"Активный рейс на «{dest}» (шанс ~{chance}%).")
    else:
        lines.append("Активного рейса нет — выбери точку сдачи.")
    return "\n".join(lines)


def _finalize_smuggling_roll(storage: Storage, telegram_id: int, active: dict[str, Any]) -> str:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return "Персонаж не найден."
    destination = str(active.get("destination") or player.location)
    chance = min(90, max(20, int(active.get("success_chance") or 50)))
    roll = random.randint(1, 100)
    success = roll <= chance

    if success:
        gross = random.randint(SMUGGLING_REWARD_MIN, SMUGGLING_REWARD_MAX)
        treasury_cut = gross // 3 if player.faction else 0
        reward = gross - treasury_cut
        warehouse_bonus = random.randint(1, 3)
        durability_text = _apply_durability_decay(storage, telegram_id, weapon_loss=4, armor_loss=2)
        storage.change_money(telegram_id, reward)
        treasury_line = ""
        if player.faction:
            storage.change_faction_treasury(player.faction, treasury_cut)
            storage.change_faction_warehouse_item(player.faction, "ammo_pack", warehouse_bonus)
            treasury_line = (
                f"Тебе {reward} RU, в казну {treasury_cut} RU (из {gross}), "
                f"на склад патронов +{warehouse_bonus}."
            )
        else:
            treasury_line = f"Награда: {reward} RU."
        loot_lines = _roll_smuggling_loot(storage, telegram_id)
        loot_text = (
            "\nДроп:\n" + "\n".join(f"• {line}" for line in loot_lines)
            if loot_lines
            else "\nДроп: пусто."
        )
        _add_rating(storage, telegram_id, RATING_REWARD["smuggle_success"])
        storage.add_player_stat(telegram_id, "smuggling_success", 1)
        storage.add_player_stat(telegram_id, "money_earned", reward)
        achievements_text = _progress_and_unlock_achievements(storage, telegram_id)
        return (
            f"🚚 Контрабанда доставлена на «{destination}»!\n"
            f"Шанс {chance}% (бросок {roll}) — маршрут прошёл без ограбления.\n"
            f"{treasury_line}"
            f"{loot_text}{durability_text}{achievements_text}"
        )

    penalty = random.randint(SMUGGLING_FAIL_PENALTY_MIN, SMUGGLING_FAIL_PENALTY_MAX)
    durability_text = _apply_durability_decay(storage, telegram_id, weapon_loss=5, armor_loss=3)
    taken = _apply_money_penalty(storage, telegram_id, penalty)
    player = storage.get_character(telegram_id, refresh_energy=False)
    hp_loss = apply_incoming_damage(12, player, min_damage=1) if player is not None else 12
    storage.change_health(telegram_id, -hp_loss)
    _add_rating(storage, telegram_id, -RATING_REWARD["smuggle_fail"])
    return (
        f"💀 Ограбили на маршруте до «{destination}»!\n"
        f"Шанс доставить был {chance}% (бросок {roll}).\n"
        f"Потери: {taken} RU и ранение (−{hp_loss} HP).{durability_text}"
    )


def _apply_smuggling_arrival(storage: Storage, telegram_id: int, active: dict[str, Any]) -> None:
    """Перенести игрока на точку сдачи после тактического рейса (без travel_to)."""
    destination = str(active.get("destination") or "").strip()
    if not destination:
        return
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.location == destination:
        return
    storage.set_location(telegram_id, destination)
    storage._set_pending_arrival_notice(telegram_id, destination)
    transport = str(active.get("transport") or "").strip()
    if transport:
        storage.set_last_arrival_transport(telegram_id, transport)


def begin_smuggling_travel_after_grid(storage: Storage, telegram_id: int) -> ActionResult:
    """После тактической карты — выезд к точке сдачи с таймером прибытия."""
    from app.smuggle_mission import clear_smuggle_session

    active = get_active_smuggling(storage, telegram_id)
    if active is None:
        return ActionResult(False, "Активного рейса контрабанды нет.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        clear_smuggle_session(storage, telegram_id)
        clear_active_smuggling(storage, telegram_id)
        return ActionResult(False, "Персонаж не найден.")
    if is_traveling(player):
        return ActionResult(False, travel_block_text(player) or "Ты уже в пути.")

    destination = str(active.get("destination") or "").strip()
    origin = str(active.get("origin") or player.location).strip()
    transport_mode = str(active.get("transport") or "foot")
    if not destination or destination == player.location:
        clear_smuggle_session(storage, telegram_id)
        clear_active_smuggling(storage, telegram_id)
        return ActionResult(False, "Точка сдачи недоступна.")

    locations = {loc["name"]: loc for loc in storage.get_locations()}
    base_minutes, _distance = _compute_base_travel_minutes(
        origin,
        destination,
        locations,
        player.faction,
    )
    bound_transport = storage.get_bound_transport(telegram_id)
    _picked, speed_mult, _energy, _foot_note = _resolve_travel_transport(
        player,
        preferred_mode=transport_mode,
        bound_transport=bound_transport,
    )
    travel_minutes = max(1, int(round(base_minutes / speed_mult)))
    travel_minutes, n2o_note = apply_n2o_to_travel_minutes(storage, telegram_id, travel_minutes)
    real_seconds = travel_minutes * TRAVEL_REAL_SECONDS_PER_GAME_MINUTE
    arrives_at = _utc_now() + timedelta(seconds=real_seconds)

    clear_smuggle_session(storage, telegram_id)
    _clear_n2o_trip_boost(storage, telegram_id)
    storage.start_travel(telegram_id, destination, arrives_at, transport_mode)
    if n2o_note:
        _mark_n2o_trip_used(storage, telegram_id)
    active["mode"] = "travel"
    active["grid_completed_at"] = _utc_now().isoformat()
    storage.set_meta(
        _smuggle_meta_key(telegram_id),
        json.dumps(active, ensure_ascii=False),
    )

    player = storage.get_character(telegram_id, refresh_energy=False) or player
    transport_labels = {
        "foot": "пешком",
        "bicycle": "на велосипеде",
        "niva": "на Ниве",
        "truck": "на грузовике",
    }
    remaining = (
        format_remaining_travel(player.travel_arrives_at)
        if player.travel_arrives_at is not None
        else format_remaining_travel(arrives_at)
    )
    chance = int(active.get("success_chance") or 0)
    return ActionResult(
        True,
        f"✅ Маршрут на карте пройден!\n"
        f"Выезжаешь к точке сдачи «{destination}» "
        f"({transport_labels.get(transport_mode, transport_mode)}).\n"
        f"Прибытие через {remaining}. Шанс сдачи ~{chance}%.{n2o_note}",
        payload={"mission_active": False, "mission_travel_started": True},
    )


def complete_smuggling_delivery(storage: Storage, telegram_id: int) -> str | None:
    """Завершить тактический рейс: бросок на успешную сдачу."""
    from app.smuggle_mission import clear_smuggle_session

    active = get_active_smuggling(storage, telegram_id)
    if active is None:
        return None
    _apply_smuggling_arrival(storage, telegram_id, active)
    clear_smuggle_session(storage, telegram_id)
    clear_active_smuggling(storage, telegram_id)
    return _finalize_smuggling_roll(storage, telegram_id, active)


def fail_smuggling_delivery(storage: Storage, telegram_id: int, reason: str) -> str:
    """Провал рейса (тайм-аут / срыв) — штраф как при ограблении."""
    from app.smuggle_mission import clear_smuggle_session

    active = get_active_smuggling(storage, telegram_id) or {}
    clear_smuggle_session(storage, telegram_id)
    clear_active_smuggling(storage, telegram_id)
    if active:
        _release_smuggle_vehicle(storage, telegram_id, active)
    player = storage.get_character(telegram_id, refresh_energy=False)
    destination = str(active.get("destination") or (player.location if player else "?"))
    penalty = random.randint(SMUGGLING_FAIL_PENALTY_MIN, SMUGGLING_FAIL_PENALTY_MAX)
    durability_text = _apply_durability_decay(storage, telegram_id, weapon_loss=5, armor_loss=3)
    taken = _apply_money_penalty(storage, telegram_id, penalty)
    if player is not None:
        hp_loss = apply_incoming_damage(10, player, min_damage=1)
        storage.change_health(telegram_id, -hp_loss)
    else:
        hp_loss = 10
    _add_rating(storage, telegram_id, -RATING_REWARD["smuggle_fail"])
    head = reason.strip() or "Рейс сорван."
    return (
        f"💀 {head}\n"
        f"Ограбление на подходе к «{destination}».\n"
        f"Потери: {taken} RU и ранение (−{hp_loss} HP).{durability_text}"
    )


def start_smuggling_run(
    storage: Storage,
    telegram_id: int,
    destination: str,
    *,
    transport_mode: str | None = None,
) -> ActionResult:
    """Начать тактический рейс контрабанды: карта с маршрутом из 3 точек."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if player.faction is None:
        return ActionResult(False, "Сначала выбери группировку.")
    if storage.get_active_contract(telegram_id):
        return ActionResult(False, "Сначала заверши или отмени активный контракт.")
    if get_active_smuggling(storage, telegram_id):
        return ActionResult(False, "У тебя уже есть активный рейс контрабанды.")
    from app.smuggle_mission import get_smuggle_session

    if get_smuggle_session(storage, telegram_id) is not None:
        return ActionResult(False, "У тебя уже есть активный тактический рейс.")
    if is_traveling(player):
        return ActionResult(False, travel_block_text(player) or "Ты уже в пути.")
    if not destination or destination == player.location:
        return ActionResult(False, "Выбери другую локацию для сдачи груза.")
    locations = {loc["name"]: loc for loc in storage.get_locations()}
    if destination not in locations:
        return ActionResult(False, "Такой локации нет.")

    from app.player_busy import player_busy_reason

    busy = player_busy_reason(storage, telegram_id, skip="smuggle")
    if busy:
        return ActionResult(False, busy)

    bound_transport = storage.get_bound_transport(telegram_id)
    if bound_transport in ("niva", "truck"):
        vehicle_label = _vehicle_label_for_key(bound_transport)
        if transport_mode in ("foot", "bicycle") or (
            transport_mode is not None and transport_mode != bound_transport
        ):
            return ActionResult(
                False,
                f"Ты за рулём {vehicle_label} — пешком или на другом транспорте не уйти.",
            )
        if transport_mode is None:
            transport_mode = bound_transport

    if transport_mode is not None:
        available = {
            mode for mode, *_rest in list_available_travel_modes(player, bound_transport=bound_transport)
        }
        if transport_mode not in available:
            labels = {
                "truck": "Недостаточно дизеля или грузовик недоступен.",
                "niva": "Недостаточно бензина или Нива недоступна.",
                "bicycle": "У тебя нет велосипеда.",
                "foot": "Пеший переход недоступен.",
            }
            return ActionResult(False, labels.get(transport_mode, "Этот транспорт недоступен."))

    picked_mode, _speed, energy_cost, foot_note = _resolve_travel_transport(
        player,
        preferred_mode=transport_mode,
        bound_transport=bound_transport,
    )
    transport_mode = picked_mode
    if transport_mode == "truck" and not can_travel_by_truck(player):
        return ActionResult(False, "Недостаточно дизеля для рейса на грузовике.")
    if transport_mode == "niva" and not can_travel_by_niva(player):
        return ActionResult(False, "Недостаточно бензина для рейса на Ниве.")
    if transport_mode == "bicycle" and not can_travel_by_bicycle(player):
        return ActionResult(False, "У тебя нет велосипеда.")

    if not storage.spend_energy(telegram_id, energy_cost):
        return ActionResult(False, f"Не хватает энергии для рейса (нужно {energy_cost}).")

    fuel_text = ""
    if transport_mode == "truck":
        if not storage.change_diesel(telegram_id, -1):
            storage.restore_energy(telegram_id, energy_cost)
            return ActionResult(False, "Не удалось списать дизель.")
        fuel_text = "\nДизель: −1."
        storage.apply_truck_wear(telegram_id, 2)
    elif transport_mode == "niva":
        if not storage.change_gasoline(telegram_id, -1):
            storage.restore_energy(telegram_id, energy_cost)
            return ActionResult(False, "Не удалось списать бензин.")
        fuel_text = "\nБензин: −1."
        storage.apply_niva_wear(telegram_id, 2)

    base_minutes, _distance = _compute_base_travel_minutes(
        player.location,
        destination,
        locations,
        player.faction,
    )
    event_modifier = _active_location_event_modifier(storage, player.location)
    success_chance = _smuggling_success_chance(
        player,
        transport_mode=transport_mode,
        base_minutes=base_minutes,
        event_modifier=event_modifier,
    )

    from app.smuggle_mission import (
        _build_smuggle_session,
        render_smuggle_for_player,
        save_smuggle_session,
        smuggle_status_caption,
    )

    origin = player.location
    session = _build_smuggle_session(
        origin=origin,
        destination=destination,
        transport=transport_mode,
        success_chance=success_chance,
    )
    save_smuggle_session(storage, telegram_id, session)
    storage.set_meta(
        _smuggle_meta_key(telegram_id),
        json.dumps(
            {
                "destination": destination,
                "origin": origin,
                "success_chance": success_chance,
                "transport": transport_mode,
                "mode": "grid",
                "started_at": _utc_now().isoformat(),
            },
            ensure_ascii=False,
        ),
    )
    if transport_mode in ("niva", "truck"):
        storage.set_bound_transport(telegram_id, transport_mode)

    player = storage.get_character(telegram_id, refresh_energy=False) or player
    image = render_smuggle_for_player(storage, telegram_id, session, player)
    transport_labels = {
        "foot": "пешком",
        "bicycle": "на велосипеде",
        "niva": "на Ниве",
        "truck": "на грузовике",
    }
    note = foot_note or ""
    return ActionResult(
        True,
        f"🚚 Тактический рейс контрабанды!\n"
        f"Маршрут: левый угол → правый угол → точка сдачи.\n"
        f"Груз: «{origin}» → «{destination}» ({transport_labels.get(transport_mode, transport_mode)}).\n"
        f"Шанс сдачи ~{success_chance}%. Ходов: {session.max_moves} (−⅓ от вылазки).\n"
        f"После карты — выезд к точке сдачи (таймер прибытия). Провал = ограбление.{fuel_text}"
        + (f"\n{note}" if note else ""),
        payload={
            "mission_image": image,
            "mission_active": True,
            "mission_started": True,
            "caption": smuggle_status_caption(session, player),
        },
    )


def abandon_smuggling_run(storage: Storage, telegram_id: int) -> ActionResult:
    """Сбросить груз и тактический рейс."""
    from app.smuggle_mission import clear_smuggle_session, get_smuggle_session

    active = get_active_smuggling(storage, telegram_id)
    session = get_smuggle_session(storage, telegram_id)
    if active is None and session is None:
        return ActionResult(False, "Активного рейса контрабанды нет.")
    dest = str(
        (active or {}).get("destination")
        or (session.destination if session is not None else "?")
    )
    clear_smuggle_session(storage, telegram_id)
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is not None and is_traveling(player):
        storage.clear_travel(telegram_id)
    clear_active_smuggling(storage, telegram_id)
    if active:
        _release_smuggle_vehicle(storage, telegram_id, active)
    return ActionResult(
        True,
        f"Груз сброшен — рейс на «{dest}» отменён.",
    )


def resolve_smuggling_if_pending(storage: Storage, telegram_id: int) -> str | None:
    """Legacy: доставка после старого travel-рейса (если остался в meta)."""
    active = get_active_smuggling(storage, telegram_id)
    if active is None:
        return None
    if str(active.get("mode") or "") == "grid":
        return None
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return None
    if is_traveling(player):
        return None
    destination = str(active.get("destination") or "")
    if not destination or player.location != destination:
        return None

    clear_active_smuggling(storage, telegram_id)
    return _finalize_smuggling_roll(storage, telegram_id, active)


def _schedule_next_zone_event(storage: Storage, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    delay = random.randint(ZONE_EVENT_INTERVAL_MIN_MINUTES, ZONE_EVENT_INTERVAL_MAX_MINUTES)
    next_at = now + timedelta(minutes=delay)
    storage.set_meta(ZONE_EVENT_META_NEXT_AT, next_at.isoformat())
    return next_at


def apply_dynamic_zone_event(storage: Storage) -> ActionResult:
    storage.delete_expired_map_events()
    # События Зоны не спавнятся на базах группировок — только на обычных локациях.
    locations = [
        loc
        for loc in storage.get_locations()
        if str(loc.get("point_type") or "") != "база"
    ]
    if not locations:
        return ActionResult(False, "Локации (без баз) пока недоступны.")
    target = random.choice(locations)
    event_type, modifier, description = random.choice(ZONE_EVENT_POOL)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=45)).isoformat()
    location_name = str(target["name"])
    storage.upsert_map_event(
        location=location_name,
        event_type=event_type,
        modifier=modifier,
        description=description,
        expires_at=expires_at,
    )

    # Динамический спавн/ослабление NPC под событие.
    current_power = int(target["npc_power"])
    mutated_power = max(8, current_power + random.randint(-3, 7) + modifier // 2)
    storage.set_location_npc_power(location_name, mutated_power)
    return ActionResult(
        True,
        f"Новое событие в Зоне: {location_name}\n{description}\n"
        f"Модификатор силы NPC: {modifier:+d}, текущая сила NPC: {mutated_power}.",
    )


def process_zone_event_cycle(storage: Storage) -> tuple[str, list[int]]:
    """Автогенерация событий Зоны в случайный момент (каждые 30–90 мин)."""
    now = datetime.now(timezone.utc)
    storage.delete_expired_map_events()
    raw_at = storage.get_meta(ZONE_EVENT_META_NEXT_AT)
    if raw_at is None:
        _schedule_next_zone_event(storage, now)
        return ("", [])

    next_at = _parse_meta_datetime(
        raw_at,
        now + timedelta(minutes=ZONE_EVENT_INTERVAL_MIN_MINUTES),
    )
    if next_at > now:
        return ("", [])

    result = apply_dynamic_zone_event(storage)
    _schedule_next_zone_event(storage, now)
    if not result.ok:
        return ("", [])
    return (result.text, storage.list_player_ids())


def build_events_overview(storage: Storage) -> str:
    storage.delete_expired_map_events()
    events = storage.get_map_events()
    emission_status = build_emission_status(storage)
    from app.special_events import special_events_status_line

    special_line = special_events_status_line(storage)
    if not events:
        return (
            f"{emission_status}\n\n"
            f"Особое: {special_line}\n\n"
            "Активных событий на карте нет. Зона затихла.\n"
            "Новые события появляются сами через некоторое время."
        )

    now = datetime.now(timezone.utc)
    by_location = {loc["name"]: int(loc["npc_power"]) for loc in storage.get_locations()}
    lines = [emission_status, "", f"Особое: {special_line}", "", "Активные события Зоны:"]
    for event in events:
        location = str(event.get("location"))
        expires_at = _safe_fromiso(str(event.get("expires_at", "")))
        minutes_left = max(0, int((expires_at - now).total_seconds() // 60))
        modifier = int(event.get("modifier", 0))
        npc_power = by_location.get(location, 0)
        lines.append(
            f"• {location}: {event.get('description')} (мод {modifier:+d}, NPC {npc_power}, ~{minutes_left} мин)"
        )
    return "\n".join(lines)


# --- Ежедневный вход (стрик) ---------------------------------------------

DAILY_LOGIN_STREAK_DISPLAY_CAP = 30
DAILY_LOGIN_MEDKIT_EVERY = 7


def _daily_login_key(telegram_id: int) -> str:
    return f"login:streak:{int(telegram_id)}"


def _daily_login_reward(streak: int) -> tuple[int, list[tuple[str, int]]]:
    """Награда за конкретный день стрика: (RU, [(item_key, qty), ...])."""
    if streak <= 1:
        return 150, []
    if streak == 2:
        return 200, []
    if streak == 3:
        return 300, [("ammo_pack", 1)]
    if streak == 4:
        return 400, []
    items: list[tuple[str, int]] = []
    if streak % DAILY_LOGIN_MEDKIT_EVERY == 0:
        items.append(("medkit", 1))
    return 500, items


def get_daily_login_state(storage: Storage, telegram_id: int) -> dict[str, Any]:
    raw = storage.get_meta(_daily_login_key(telegram_id))
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return {"last": data.get("last"), "streak": int(data.get("streak") or 0)}
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return {"last": None, "streak": 0}


def has_claimed_daily_login_today(storage: Storage, telegram_id: int, now: datetime | None = None) -> bool:
    today_str = (now or _utc_now()).date().isoformat()
    return get_daily_login_state(storage, telegram_id).get("last") == today_str


def _daily_login_hint_key(telegram_id: int) -> str:
    return f"login:hint_shown:{int(telegram_id)}"


def maybe_daily_login_hint(storage: Storage, telegram_id: int) -> str:
    """Одноразовая мягкая подсказка про «📅 Ежедневка» на /start (если награда не собрана сегодня)."""
    if has_claimed_daily_login_today(storage, telegram_id):
        return ""
    key = _daily_login_hint_key(telegram_id)
    if storage.get_meta(key):
        return ""
    storage.set_meta(key, "1")
    return "\n\n💡 Не забудь забрать ежедневную награду: 📟 КПК → «📅 Ежедневка»."


def claim_daily_login(storage: Storage, telegram_id: int) -> ActionResult:
    """Ежедневная награда за вход. Стрик рвётся, если пропущен хотя бы один день."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")

    today = _utc_now().date()
    today_str = today.isoformat()
    state = get_daily_login_state(storage, telegram_id)
    last_str = state.get("last")
    streak = int(state.get("streak") or 0)

    if last_str == today_str:
        capped = min(streak, DAILY_LOGIN_STREAK_DISPLAY_CAP)
        return ActionResult(
            False,
            f"📅 Ежедневная награда уже получена сегодня.\n"
            f"Твой стрик: {capped} дн. подряд. Возвращайся завтра!",
        )

    claim_guard_key = f"daily:claimed:{telegram_id}:{today_str}"
    if not storage.set_meta_if_absent(claim_guard_key, "1"):
        capped = min(streak, DAILY_LOGIN_STREAK_DISPLAY_CAP)
        return ActionResult(
            False,
            f"📅 Ежедневная награда уже получена сегодня.\n"
            f"Твой стрик: {capped} дн. подряд. Возвращайся завтра!",
        )

    yesterday_str = (today - timedelta(days=1)).isoformat()
    streak = streak + 1 if last_str == yesterday_str else 1

    reward_ru, reward_items = _daily_login_reward(streak)
    storage.change_money(telegram_id, reward_ru)
    for item_key, qty in reward_items:
        storage.add_item(telegram_id, item_key, qty)
    storage.set_meta(
        _daily_login_key(telegram_id),
        json.dumps({"last": today_str, "streak": streak}, ensure_ascii=False),
    )

    capped_streak = min(streak, DAILY_LOGIN_STREAK_DISPLAY_CAP)
    items_note = "".join(f", {ITEM_LABELS.get(k, k)} x{q}" for k, q in reward_items)
    cap_note = " (макс. для отображения)" if streak > DAILY_LOGIN_STREAK_DISPLAY_CAP else ""
    return ActionResult(
        True,
        "📅 Ежедневная награда получена!\n"
        f"Стрик: {capped_streak} дн. подряд{cap_note}.\n"
        f"Награда: +{reward_ru} RU{items_note}.",
    )


# --- Настройки уведомлений -------------------------------------------------

NOTIFY_PREF_KEYS: tuple[str, ...] = ("emission", "death", "coop", "garage", "zone_event")
NOTIFY_PREF_LABELS: dict[str, str] = {
    "emission": "☢️ Выброс",
    "death": "☠️ Смерть",
    "coop": "👥 Совместные вылазки",
    "garage": "🏚 Гараж (возврат аренды)",
    "zone_event": "⚡ События на локации",
}


def _notify_prefs_key(telegram_id: int) -> str:
    return f"notify:prefs:{int(telegram_id)}"


def get_notify_prefs(storage: Storage, telegram_id: int) -> dict[str, bool]:
    prefs = {key: True for key in NOTIFY_PREF_KEYS}
    raw = storage.get_meta(_notify_prefs_key(telegram_id))
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                for key in NOTIFY_PREF_KEYS:
                    if key in data:
                        prefs[key] = bool(data[key])
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return prefs


def set_notify_pref(storage: Storage, telegram_id: int, key: str, value: bool) -> dict[str, bool]:
    prefs = get_notify_prefs(storage, telegram_id)
    if key in NOTIFY_PREF_KEYS:
        prefs[key] = bool(value)
    storage.set_meta(_notify_prefs_key(telegram_id), json.dumps(prefs, ensure_ascii=False))
    return prefs


def toggle_notify_pref(storage: Storage, telegram_id: int, key: str) -> dict[str, bool]:
    prefs = get_notify_prefs(storage, telegram_id)
    return set_notify_pref(storage, telegram_id, key, not prefs.get(key, True))


def is_notify_enabled(storage: Storage, telegram_id: int, key: str) -> bool:
    return get_notify_prefs(storage, telegram_id).get(key, True)


def build_notify_prefs_text(prefs: dict[str, bool]) -> str:
    lines = ["🔔 Настройки уведомлений", ""]
    for key in NOTIFY_PREF_KEYS:
        state = "✅ включены" if prefs.get(key, True) else "🔕 выключены"
        lines.append(f"{NOTIFY_PREF_LABELS.get(key, key)}: {state}")
    lines.append("")
    lines.append("Нажми на пункт ниже, чтобы переключить.")
    return "\n".join(lines)


def build_player_stats_text(storage: Storage, telegram_id: int) -> str:
    stats = storage.get_player_stats(telegram_id)
    player = storage.get_character(telegram_id, refresh_energy=False)
    nick = player.nickname if player else str(telegram_id)
    lines = [
        f"📊 Статистика — {h(nick)}",
        "",
        f"Контракты: ✅ {stats['quests_completed']} / ❌ {stats['quests_failed']}",
        f"Рейды: ✅ {stats['raids_completed']} / ❌ {stats['raids_failed']}",
        f"Войны выиграно: {stats['wars_won']}",
        f"Захватов точек: {stats['enemy_bases_captured']}",
        f"Контрабанда: {stats['smuggling_success']}",
        f"Сделок: {stats['trades_done']}",
        f"Заработано RU (учёт): {stats['money_earned']}",
        f"Артефактов: {stats['artifacts_found']}",
        f"Помощь по рации: {stats['radio_helps']}",
        f"Смерти: {stats['deaths']}",
        f"Рейтинг: {stats['rating_points']} | сезон: {stats['season_rating']}",
        f"Достижений: {stats['achievements_unlocked']}",
    ]
    return "\n".join(lines)


# --- Мини-обучение (PDA) ----------------------------------------------------

TUTORIAL_PAGES: tuple[tuple[str, str], ...] = (
    (
        "Тебя сожрут за 10 минут",
        "Зона не ждёт, пока ты разберёшься в кнопках.\n"
        "Голод, жажда и радиация едят HP даже когда ты просто стоишь. "
        "Еда/вода — в инвентаре, антирад и аптечки — у медика.\n\n"
        "👉 Сейчас: 🎒 Инвентарь → расходники. Потом читай дальше — там, где лежат деньги.",
    ),
    (
        "Секрет контрактов дня",
        "В 📋 Заданиях висят 🗓 контракты дня и 📅 контракт недели — к награде бонус %.\n"
        "Это не «ещё одно задание», это жирные RU за ту же работу.\n\n"
        "Артефакты: купи «Отклик» у бармена, иначе 📡 Поиск даже не откроется.\n"
        "👉 Сначала Отклик, потом Задания → Поиск. Без ствола читай следующую страницу.",
    ),
    (
        "Ствол без патронов — палка",
        "Патроны теперь в «🔫 Оружие» у бармена, не в еде.\n"
        "Опасные контракты жрут патроны. Нож в коопе — это ты уже труп.\n\n"
        "👉 Бармен → Оружие: патроны пачкой + ствол. Броню — рядом. "
        "Хабар на базе прячет лут от смерти. Без хабара рюкзак — подарок мутантам.",
    ),
    (
        "Выброс не спрашивает имя",
        "Раз в несколько часов небо рвёт. Не в укрытии — минус HP или минус жизнь.\n"
        "База группировки = укрытие. 🔔 Уведомления включи сразу, иначе узнаешь по трупу.\n\n"
        "Смерть: 500 RU на базе или долг медикам. Журнал — КПК → ☠️ Смерти.\n"
        "👉 Не геройствуй на Радаре голым. Сначала Росток/Кордон/Свалка.",
    ),
    (
        "Казна ГП кормит войну",
        "Рейды и война льют RU в казну, не только тебе в карман.\n"
        "⭐ Пополнить → казна группировки: твои звёзды становятся патронами для всей ГП.\n"
        "Там же написано, зачем ваша группировка вообще сидит в Зоне.\n\n"
        "👉 👥 Группировка — склад, гараж, казна. Один богатый клан бьёт десять голодных.",
    ),
    (
        "Рейтинг = шкура и пушки",
        "Каждый успешный контракт, рейд и кооп капает рейтинг.\n"
        "500 / 2000 / 5000 — новые скины. Сезон 14 дней: топ-3 забирает эксклюзив, "
        "которого нет у торговца.\n\n"
        "Арена на базе — бесплатная тренировка: волны НПС, награда как за лёгкое задание.\n"
        "👉 Дочитал до конца — забери бонус на следующей странице и беги в Зону.",
    ),
    (
        "Забрал бонус — вали в Зону",
        "Кратко, если забыл:\n"
        "1) 📋 Задания на базе\n"
        "2) Бармен: Отклик + патроны в «Оружие»\n"
        "3) Схрон, еда, уведомления о Выбросе\n"
        "4) Не ходи на Радар в куртке новичка\n\n"
        "КПК → 💬 Чаты — общий чат и чат ГП. Там зовут в рейды.\n"
        "Награда за это обучение падает один раз. Дальше — сам.",
    ),
)

TUTORIAL_COMPLETE_REWARD_RU = 250
TUTORIAL_MENU_BUTTON = "🔥 Как не сдохнуть"
TUTORIAL_MENU_ALIASES = frozenset({TUTORIAL_MENU_BUTTON, "📘 Обучение"})


def _tutorial_seen_key(telegram_id: int) -> str:
    return f"tutorial:seen:{int(telegram_id)}"


def has_seen_tutorial(storage: Storage, telegram_id: int) -> bool:
    return bool(storage.get_meta(_tutorial_seen_key(telegram_id)))


def build_tutorial_page(page: int) -> tuple[str, int, int]:
    """Возвращает (текст, номер_страницы(0-based), всего_страниц)."""
    total = len(TUTORIAL_PAGES)
    page = max(0, min(page, total - 1))
    title, body = TUTORIAL_PAGES[page]
    text = f"🔥 Как не сдохнуть · {page + 1}/{total} · {title}\n\n{body}"
    return text, page, total


def claim_tutorial_completion(storage: Storage, telegram_id: int) -> str:
    """Отмечает обучение пройденным и один раз выдаёт бонус RU. Возвращает строку-приписку."""
    key = _tutorial_seen_key(telegram_id)
    if storage.get_meta(key):
        return ""
    storage.set_meta(key, "1")
    if not storage.change_money(telegram_id, TUTORIAL_COMPLETE_REWARD_RU):
        return ""
    return f"\n\n🔥 Дочитал. Награда: +{TUTORIAL_COMPLETE_REWARD_RU} RU. Теперь вали в Зону."


# --- Клановые задачи (контроль точек) --------------------------------------

CLAN_QUEST_POOL: tuple[str, ...] = (
    "Янтарь",
    "Болото",
    "НИИ Агропром",
    "Темная долина",
    "Рыжий лес",
    "Радар",
)
CLAN_QUEST_TREASURY_REWARD_MIN = 200
CLAN_QUEST_TREASURY_REWARD_MAX = 500
CLAN_QUEST_PERSONAL_REWARD_MIN = 100
CLAN_QUEST_PERSONAL_REWARD_MAX = 200
CLAN_QUEST_RATING_REWARD = 2


def _clan_quest_date_str(now: datetime | None = None) -> str:
    return (now or _utc_now()).strftime("%Y-%m-%d")


def clan_quest_daily_target(faction: str, now: datetime | None = None) -> str:
    """Детерминированная ежедневная ротация цели по группировке+дате."""
    date_str = _clan_quest_date_str(now)
    digest = hashlib.sha256(f"{faction}:{date_str}".encode("utf-8")).hexdigest()
    index = int(digest, 16) % len(CLAN_QUEST_POOL)
    return CLAN_QUEST_POOL[index]


def _clan_quest_personal_key(faction: str, date_str: str, telegram_id: int) -> str:
    return f"clan_quest:claim:{faction}:{date_str}:{int(telegram_id)}"


def _clan_quest_treasury_key(faction: str, date_str: str) -> str:
    return f"clan_quest:done:{faction}:{date_str}"


def build_clan_quest_overview(storage: Storage, telegram_id: int) -> str:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return "Сначала создай персонажа через /start."
    if not player.faction:
        return "🏛 Клановые задачи доступны после выбора группировки."

    date_str = _clan_quest_date_str()
    target = clan_quest_daily_target(player.faction)
    location = storage.get_location(target) or {}
    owner = str(location.get("controlled_by") or "нейтрал")
    controlled = owner == player.faction
    claimed = bool(storage.get_meta(_clan_quest_personal_key(player.faction, date_str, telegram_id)))

    status = (
        "✅ точка под контролем — можно забрать награду"
        if controlled
        else f"❌ точка не под контролем (сейчас: {owner})"
    )
    lines = [
        "🏛 Клановое задание дня",
        "",
        f"Группировка: {player.faction}",
        f"Цель: удерживать «{target}»",
        f"Статус: {status}",
    ]
    if claimed:
        lines.append("")
        lines.append("Награда сегодня уже получена.")
    else:
        lines.append("")
        lines.append(
            f"Награда: +{CLAN_QUEST_PERSONAL_REWARD_MIN}–{CLAN_QUEST_PERSONAL_REWARD_MAX} RU лично, "
            f"+{CLAN_QUEST_TREASURY_REWARD_MIN}–{CLAN_QUEST_TREASURY_REWARD_MAX} RU в казну, "
            f"+{CLAN_QUEST_RATING_REWARD} рейтинга."
        )
    return "\n".join(lines)


def can_claim_clan_quest(storage: Storage, telegram_id: int) -> bool:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or not player.faction or _is_dead(player):
        return False
    date_str = _clan_quest_date_str()
    target = clan_quest_daily_target(player.faction)
    location = storage.get_location(target) or {}
    owner = str(location.get("controlled_by") or "")
    if owner != player.faction:
        return False
    return not bool(storage.get_meta(_clan_quest_personal_key(player.faction, date_str, telegram_id)))


def claim_clan_quest(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if not player.faction:
        return ActionResult(False, "Сначала выбери группировку.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())

    date_str = _clan_quest_date_str()
    target = clan_quest_daily_target(player.faction)
    location = storage.get_location(target) or {}
    owner = str(location.get("controlled_by") or "")
    if owner != player.faction:
        return ActionResult(
            False,
            f"«{target}» пока не под контролем «{player.faction}». Захватите точку и возвращайтесь.",
        )

    personal_key = _clan_quest_personal_key(player.faction, date_str, telegram_id)
    if storage.get_meta(personal_key):
        return ActionResult(False, "Ты уже получил награду за клановое задание сегодня.")

    personal_reward = random.randint(CLAN_QUEST_PERSONAL_REWARD_MIN, CLAN_QUEST_PERSONAL_REWARD_MAX)
    storage.change_money(telegram_id, personal_reward)
    _add_rating(storage, telegram_id, CLAN_QUEST_RATING_REWARD)
    storage.set_meta(personal_key, "1")

    treasury_note = ""
    treasury_key = _clan_quest_treasury_key(player.faction, date_str)
    if not storage.get_meta(treasury_key):
        treasury_reward = random.randint(CLAN_QUEST_TREASURY_REWARD_MIN, CLAN_QUEST_TREASURY_REWARD_MAX)
        storage.change_faction_treasury(player.faction, treasury_reward)
        storage.set_meta(treasury_key, "1")
        treasury_note = f" В казну «{player.faction}» зачислено +{treasury_reward} RU (бонус первого сдавшего сегодня)."

    return ActionResult(
        True,
        f"🏛 Клановое задание выполнено!\n"
        f"«{target}» под контролем «{player.faction}».\n"
        f"Награда: +{personal_reward} RU, +{CLAN_QUEST_RATING_REWARD} рейтинга.{treasury_note}",
    )


from app.artifact_features import merge_extended_artifact_catalog

merge_extended_artifact_catalog()
