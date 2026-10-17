from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.game_logic import ARTIFACT_DROP_KEYS, ITEM_LABELS, TOPUP_RATE_RU_PER_STAR, WAR_MIN_FACTION_MEMBERS, default_trader_sell_catalog_buttons
from app.secret_trader import INTEL_ITEM_KEYS, INTEL_SELL_PRICES


def gender_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Мужской", callback_data="gender:male"),
                InlineKeyboardButton(text="Женский", callback_data="gender:female"),
            ]
        ]
    )


def faction_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Долг", callback_data="faction:Долг")],
            [InlineKeyboardButton(text="Свобода", callback_data="faction:Свобода")],
            [InlineKeyboardButton(text="Нейтралы", callback_data="faction:Нейтралы")],
            [InlineKeyboardButton(text="Бандиты", callback_data="faction:Бандиты")],
        ]
    )


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📟 КПК"), KeyboardButton(text="🎒 Инвентарь")],
            [KeyboardButton(text="📡 Статус"), KeyboardButton(text="🏕 Вылазка")],
            [KeyboardButton(text="🛰 События"), KeyboardButton(text="👥 Группировка")],
            [KeyboardButton(text="⭐ Пополнить")],
        ],
        resize_keyboard=True,
    )


def group_bot_link_inline_keyboard(bot_username: str | None) -> InlineKeyboardMarkup:
    """Inline-кнопка со ссылкой на бота (в группах работает только inline, не reply)."""
    if not bot_username:
        return InlineKeyboardMarkup(inline_keyboard=[])
    username = bot_username.lstrip("@")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Ссылка на бота", url=f"https://t.me/{username}?start=from_group")],
        ]
    )


def pda_keyboard(*, is_leader: bool = False) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = [
        [KeyboardButton(text="🧾 Профиль"), KeyboardButton(text="💬 Чаты")],
        [KeyboardButton(text="🏆 Рейтинг"), KeyboardButton(text="🗺 Карта"), KeyboardButton(text="📍 Локация")],
        [KeyboardButton(text="👥 Игроки"), KeyboardButton(text="🤝 Обмен")],
        [KeyboardButton(text="💎 Находки"), KeyboardButton(text="💎 Арты")],
        [KeyboardButton(text="📊 Дроп"), KeyboardButton(text="☠️ Смерти")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📅 Ежедневка")],
        [KeyboardButton(text="🔔 Уведомления"), KeyboardButton(text="🔥 Как не сдохнуть")],
        [KeyboardButton(text="🏛 Клановые задачи"), KeyboardButton(text="ℹ️ Информация")],
    ]
    if is_leader:
        rows[-1].append(KeyboardButton(text="📣 Сбор"))
    rows.append([KeyboardButton(text="🔗 Реферальная система")])
    rows.append([KeyboardButton(text="⬅️ В меню")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def sortie_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚔️ Война"), KeyboardButton(text="🗺 Переход")],
            [KeyboardButton(text="⚔️ Арена"), KeyboardButton(text="🪖 Рейды")],
            [KeyboardButton(text="📍 Локация")],
            [KeyboardButton(text="⬅️ В меню")],
        ],
        resize_keyboard=True,
    )


def quests_keyboard(
    *,
    contract_buttons: list[tuple[str, str]] | None = None,
    vendor_buttons: list[tuple[str, str]] | None = None,
    show_work: bool = False,
    show_go_work: bool = False,
    work_location: str = "",
    show_go_home: bool = False,
    show_cancel: bool = False,
    show_help: bool = False,
    show_special: bool = False,
    special_label: str | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if show_work:
        rows.append([InlineKeyboardButton(text="⚙️ Выполнить задание", callback_data="contract:work")])
    if show_go_work and work_location:
        short = work_location if len(work_location) <= 22 else f"{work_location[:20]}…"
        rows.append(
            [InlineKeyboardButton(text=f"🗺 Ехать: {short}", callback_data="contract:travel_work")]
        )
    if show_go_home:
        rows.append([InlineKeyboardButton(text="🏠 На базу", callback_data="contract:go_home")])
    for label, callback_data in vendor_buttons or []:
        rows.append([InlineKeyboardButton(text=label, callback_data=callback_data)])
    for label, callback_data in contract_buttons or []:
        rows.append([InlineKeyboardButton(text=label, callback_data=callback_data)])
    if show_cancel:
        rows.append([InlineKeyboardButton(text="❌ Отменить контракт", callback_data="contract:cancel")])
    rows.append([InlineKeyboardButton(text="📡 Поиск артефактов", callback_data="artifact:search")])
    rows.append([InlineKeyboardButton(text="📦 Поиск хабара", callback_data="stash:search")])
    if show_help:
        rows.append([InlineKeyboardButton(text="🆘 Помочь по рации", callback_data="help_event:join")])
    if show_special:
        rows.append(
            [
                InlineKeyboardButton(
                    text=special_label or "📡 Особое событие",
                    callback_data="special_event:join",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🚚 Контрабанда", callback_data="eco:smuggle:menu")])
    rows.append([InlineKeyboardButton(text="👥 Совместная вылазка", callback_data="coop:menu")])
    rows.append(
        [
            InlineKeyboardButton(text="ℹ️ Справка", callback_data="quests:info"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data="contract:refresh"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def quests_info_keyboard(*, page: int = 0, total_pages: int = 1) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(text="⬅️ Назад", callback_data=f"quests:info:{page - 1}")
            )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(text="Далее ➡️", callback_data=f"quests:info:{page + 1}")
            )
        if nav:
            rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ К заданиям", callback_data="contract:refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def travel_in_transit_keyboard(*, show_n2o_button: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="⏱ Статус перехода", callback_data="travel:status")],
    ]
    if show_n2o_button:
        rows.append(
            [
                InlineKeyboardButton(
                    text="💨 Закись азота (N2O, ÷2 остаток)",
                    callback_data="use:nitrous_oxide",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def travel_keyboard(
    locations: list[dict[str, str | int | None]],
    *,
    traveling: bool = False,
    show_n2o_button: bool = False,
    show_field_repair: bool = False,
    back_callback: str | None = None,
) -> InlineKeyboardMarkup:
    display_names = {"Тунель": "Тунель (Барахолка)"}
    rows: list[list[InlineKeyboardButton]] = []
    if traveling:
        rows.extend(travel_in_transit_keyboard(show_n2o_button=show_n2o_button).inline_keyboard)
    else:
        for location in locations:
            name = str(location.get("name") or "")
            ptype = str(location.get("point_type") or "")
            owner = location.get("controlled_by") or "нейтрал"
            label = f"{display_names.get(name, name)} [{ptype}, {owner}]"
            rows.append([InlineKeyboardButton(text=label, callback_data=f"travel:to:{name}")])
        if show_field_repair:
            rows.append(
                [InlineKeyboardButton(text="🔧 Подлатать технику до 5%", callback_data="travel:fieldrepair")]
            )
    if back_callback:
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def travel_transport_keyboard(
    destination: str,
    modes: list[tuple[str, str]],
    *,
    back_callback: str = "travel:back",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=label, callback_data=f"travel:go:{mode}:{destination}")]
        for mode, label in modes
    ]
    rows.append([InlineKeyboardButton(text="⬅️ К локациям", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def smuggle_transport_keyboard(
    destination: str,
    modes: list[tuple[str, str]],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=label, callback_data=f"eco:smuggle:go:{mode}:{destination}")]
        for mode, label in modes
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="eco:smuggle:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def trader_keyboard(faction: str | None = None) -> InlineKeyboardMarkup:
    from app.vendors import vendor_button_title

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🍺 {vendor_button_title(faction, 'barkeep')}", callback_data="trade:vendor:barkeep")],
            [InlineKeyboardButton(text=f"🩺 {vendor_button_title(faction, 'medic')}", callback_data="trade:vendor:medic")],
            [InlineKeyboardButton(text=f"🔧 {vendor_button_title(faction, 'tech')}", callback_data="trade:vendor:tech")],
            [InlineKeyboardButton(text="🔴 Продажа", callback_data="trade:menu:sell")],
        ]
    )


TRADER_PAGE_SIZE = 5


def _trader_page_keyboard(
    items: list[tuple[str, str]],
    *,
    page: int,
    page_prefix: str,
    back_callback: str,
    back_text: str,
) -> InlineKeyboardMarkup:
    total = len(items)
    total_pages = max(1, (total + TRADER_PAGE_SIZE - 1) // TRADER_PAGE_SIZE)
    safe_page = max(0, min(page, total_pages - 1))
    start = safe_page * TRADER_PAGE_SIZE
    chunk = items[start : start + TRADER_PAGE_SIZE]

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=title, callback_data=callback)] for title, callback in chunk
    ]
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if safe_page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"{page_prefix}:{safe_page - 1}",
                )
            )
        nav.append(
            InlineKeyboardButton(
                text=f"{safe_page + 1}/{total_pages}",
                callback_data=f"{page_prefix}:{safe_page}",
            )
        )
        if safe_page + 1 < total_pages:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"{page_prefix}:{safe_page + 1}",
                )
            )
        rows.append(nav)
    rows.append([InlineKeyboardButton(text=back_text, callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def trader_buy_categories_keyboard() -> InlineKeyboardMarkup:
    """Совместимость: старое меню покупки → бармен."""
    return barkeep_menu_keyboard()


def barkeep_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🍽 Еда и вода", callback_data="trade:barkeep:food:0")],
            [InlineKeyboardButton(text="🛠 Прочее", callback_data="trade:barkeep:gear:0")],
            [InlineKeyboardButton(text="🦺 Броня", callback_data="trade:barkeep:armor:0")],
            [InlineKeyboardButton(text="🔫 Оружие", callback_data="trade:barkeep:weapons:0")],
            [InlineKeyboardButton(text="⭐ Ассортимент", callback_data="trade:upgrade:barkeep")],
            [InlineKeyboardButton(text="⬅️ Назад к торговцу", callback_data="trade:menu:root")],
        ]
    )


def medic_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💊 Аптечки и антирад", callback_data="trade:medic:buy:0")],
            [InlineKeyboardButton(text="💉 Лечение (бесплатно)", callback_data="trade:medic:heal")],
            [InlineKeyboardButton(text="⭐ Ассортимент", callback_data="trade:upgrade:medic")],
            [InlineKeyboardButton(text="⬅️ Назад к торговцу", callback_data="trade:menu:root")],
        ]
    )


def tech_menu_keyboard(*, can_buy_upgrade: bool = True, can_buy_artifact_slot: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="Ремонт оружия", callback_data="repair:weapon")],
        [InlineKeyboardButton(text="Ремонт брони", callback_data="repair:armor")],
        [InlineKeyboardButton(text="Ремонт грузовика", callback_data="repair:truck")],
        [InlineKeyboardButton(text="Ремонт Нивы", callback_data="repair:niva")],
    ]
    if can_buy_upgrade:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Купить улучшение брони (+1 защита, 5000 RU)",
                    callback_data="upgrade:armor",
                )
            ]
        )
    if can_buy_artifact_slot:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Доп. ячейка артефакта (15000 RU)",
                    callback_data="upgrade:artifact_slot",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⭐ Уровень сервиса", callback_data="trade:upgrade:tech")])
    rows.append([InlineKeyboardButton(text="🔧 Ремонт артефактов", callback_data="tech:repair:artifacts")])
    rows.append([InlineKeyboardButton(text="🔬 Крафт из мусора", callback_data="tech:craft:menu")])
    rows.append([InlineKeyboardButton(text="🧩 Собрать «Предел» (3 части)", callback_data="tech:craft:predel")])
    rows.append([InlineKeyboardButton(text="💨 Закись азота (N2O)", callback_data="trade:tech:buy:0")])
    rows.append([InlineKeyboardButton(text="🛡 Страховка артов (8000 RU)", callback_data="tech:insurance:buy")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к торговцу", callback_data="trade:menu:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def vendor_upgrade_keyboard(vendor: str) -> InlineKeyboardMarkup:
    back = {
        "barkeep": "trade:vendor:barkeep",
        "medic": "trade:vendor:medic",
        "tech": "trade:vendor:tech",
    }.get(vendor, "trade:menu:root")
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=back)]]
    )


BUY_CONSUMABLE_AMOUNTS: tuple[int, ...] = (1, 5, 10, 25)


def _filter_shop_rows(
    catalog: list[tuple[str, str, str]],
    unlocked_keys: set[str] | frozenset[str] | None,
) -> list[tuple[str, str]]:
    if unlocked_keys is None:
        return [(title, cb) for title, cb, _key in catalog]
    return [
        (title, cb)
        for title, cb, key in catalog
        if key in unlocked_keys
        or (key == "weapon_fort12" and "weapon_fora12" in unlocked_keys)
        or (key == "armor_zarya" and "armor_sunrise" in unlocked_keys)
        or (key == "armor_bulat" and "armor_berill5m" in unlocked_keys)
        or (key == "armor_exo" and "armor_exoskeleton" in unlocked_keys)
    ]


def trader_buy_consumables_keyboard(
    *, page: int = 0, unlocked_keys: set[str] | frozenset[str] | None = None
) -> InlineKeyboardMarkup:
    return barkeep_food_keyboard(page=page, unlocked_keys=unlocked_keys)


def barkeep_food_keyboard(
    *, page: int = 0, unlocked_keys: set[str] | frozenset[str] | None = None
) -> InlineKeyboardMarkup:
    from app.game_logic import shop_consumable_button_title

    catalog = [
        (shop_consumable_button_title("energy_drink"), "buyqty:energy_drink", "energy_drink"),
        (shop_consumable_button_title("vodka"), "buyqty:vodka", "vodka"),
        (shop_consumable_button_title("bread"), "buyqty:bread", "bread"),
        (shop_consumable_button_title("sausage"), "buyqty:sausage", "sausage"),
        (shop_consumable_button_title("stew"), "buyqty:stew", "stew"),
        (shop_consumable_button_title("water_bottle", label="Вода"), "buyqty:water_bottle", "water_bottle"),
        (shop_consumable_button_title("mineral_water"), "buyqty:mineral_water", "mineral_water"),
        (shop_consumable_button_title("beard_tea"), "buyqty:beard_tea", "beard_tea"),
        (shop_consumable_button_title("diesel_can", label="Дизель +5"), "buyqty:diesel_can", "diesel_can"),
        (shop_consumable_button_title("gasoline_can", label="Бензин +5"), "buyqty:gasoline_can", "gasoline_can"),
    ]
    items = _filter_shop_rows(catalog, unlocked_keys)
    return _trader_page_keyboard(
        items,
        page=page,
        page_prefix="trade:barkeep:food",
        back_callback="trade:vendor:barkeep",
        back_text="⬅️ Назад к бармену",
    )


def tech_supplies_keyboard(
    *, page: int = 0, unlocked_keys: set[str] | frozenset[str] | None = None
) -> InlineKeyboardMarkup:
    from app.game_logic import shop_consumable_button_title

    catalog = [
        (shop_consumable_button_title("nitrous_oxide"), "buyqty:nitrous_oxide", "nitrous_oxide"),
    ]
    items = _filter_shop_rows(catalog, unlocked_keys)
    return _trader_page_keyboard(
        items,
        page=page,
        page_prefix="trade:tech:buy",
        back_callback="trade:vendor:tech",
        back_text="⬅️ Назад к технику",
    )


def medic_buy_keyboard(
    *, page: int = 0, unlocked_keys: set[str] | frozenset[str] | None = None
) -> InlineKeyboardMarkup:
    from app.game_logic import shop_consumable_button_title

    catalog = [
        (shop_consumable_button_title("medkit"), "buyqty:medkit", "medkit"),
        (shop_consumable_button_title("medkit_army"), "buyqty:medkit_army", "medkit_army"),
        (shop_consumable_button_title("antirad"), "buyqty:antirad", "antirad"),
        (shop_consumable_button_title("medkit_science"), "buyqty:medkit_science", "medkit_science"),
        (shop_consumable_button_title("antibiotic"), "buyqty:antibiotic", "antibiotic"),
    ]
    items = _filter_shop_rows(catalog, unlocked_keys)
    return _trader_page_keyboard(
        items,
        page=page,
        page_prefix="trade:medic:buy",
        back_callback="trade:vendor:medic",
        back_text="⬅️ Назад к медику",
    )


def buy_item_qty_keyboard(
    item_key: str,
    *,
    unit_price: int,
    back_callback: str,
    back_text: str,
    buy_prefix: str = "buy",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for amount in BUY_CONSUMABLE_AMOUNTS:
        total = unit_price * amount
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"×{amount} — {total} RU",
                    callback_data=f"{buy_prefix}:{item_key}:{amount}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text=back_text, callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def trader_buy_consumable_qty_keyboard(item_key: str, *, unit_price: int, title: str) -> InlineKeyboardMarkup:
    from app.vendors import shop_item_vendor

    vendor = shop_item_vendor(item_key)
    if vendor == "medic":
        back_callback = "trade:medic:buy:0"
        back_text = "⬅️ Назад к медику"
    elif vendor == "tech":
        back_callback = "trade:tech:buy:0"
        back_text = "⬅️ Назад к технику"
    elif item_key in ("ammo_pack", "ammo_pistol", "ammo_shotgun", "ammo_rifle", "ammo_gauss"):
        back_callback = "trade:barkeep:weapons:0"
        back_text = "⬅️ Назад к оружию"
    else:
        back_callback = "trade:barkeep:food:0"
        back_text = "⬅️ Назад к бармену"
    return buy_item_qty_keyboard(
        item_key,
        unit_price=unit_price,
        back_callback=back_callback,
        back_text=back_text,
    )


def trader_buy_gear_keyboard(
    *, page: int = 0, unlocked_keys: set[str] | frozenset[str] | None = None
) -> InlineKeyboardMarkup:
    from app.game_logic import shop_gear_button_title

    catalog = [
        (shop_gear_button_title("detector_otklik"), "buy:detector_otklik", "detector_otklik"),
        (shop_gear_button_title("detector_medved"), "buy:detector_medved", "detector_medved"),
        (shop_gear_button_title("detector_veles"), "buy:detector_veles", "detector_veles"),
        (shop_gear_button_title("detector_svarog"), "buy:detector_svarog", "detector_svarog"),
        (shop_gear_button_title("niva"), "buy:niva", "niva"),
        (shop_gear_button_title("bicycle"), "buy:bicycle", "bicycle"),
        (shop_gear_button_title("truck"), "buy:truck", "truck"),
        (shop_gear_button_title("sleeping_bag"), "buy:sleeping_bag", "sleeping_bag"),
        (shop_gear_button_title("stash_case"), "buyqty:stash_case", "stash_case"),
        (shop_gear_button_title("stash_coordinates"), "buy:stash_coordinates", "stash_coordinates"),
        (shop_gear_button_title("radio_set"), "buy:radio_set", "radio_set"),
    ]
    items = _filter_shop_rows(catalog, unlocked_keys)
    return _trader_page_keyboard(
        items,
        page=page,
        page_prefix="trade:barkeep:gear",
        back_callback="trade:vendor:barkeep",
        back_text="⬅️ Назад к бармену",
    )


def trader_buy_repair_keyboard() -> InlineKeyboardMarkup:
    """Совместимость: ремонт теперь у техника."""
    return tech_menu_keyboard(can_buy_upgrade=True)


def inventory_equipment_keyboard(*, money: int | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🧰 Расходники", callback_data="inventory:consumables")],
        [InlineKeyboardButton(text="🗄 Хабар", callback_data="stash:menu")],
        [InlineKeyboardButton(text="🛒 Купить тайник (от 5000)", callback_data="invbuyqty:stash_case")],
        [InlineKeyboardButton(text="📦 Открыть тайник", callback_data="use:stash_case")],
        [InlineKeyboardButton(text="⚙️ Экипировка", callback_data="equip:root")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def blockpost_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚔️ Прорвать блокпост", callback_data="locmap:blockpost")],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="locmap:menu")],
        ]
    )


def lobby_join_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🤝 Присоединиться к лобби", callback_data=callback_data)]
        ]
    )


def lobby_join_url_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🤝 Открыть лобби в боте", url=url)]
        ]
    )


def _special_event_button(special_label: str | None) -> list[InlineKeyboardButton] | None:
    if not special_label:
        return None
    return [InlineKeyboardButton(text=special_label, callback_data="special_event:join")]


def location_zones_keyboard(
    location: str,
    zones_status: list[tuple[dict, str | None]],
    *,
    is_home_base: bool,
    show_secret_trader: bool = False,
    resupply_cooldown: str | None = None,
    special_label: str | None = None,
    work_available: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура карты локации: торговец на базе + кнопки зон локации.

    zones_status — список пар (zone_dict, remaining_cooldown_text_or_None):
    для зон обыска показываем кнопку запуска либо (при активном КД)
    информационную неактивную кнопку с остатком времени.
    show_secret_trader=True — в Припяти добавляет вход к тайному торговцу.
    resupply_cooldown — остаток кулдауна бесплатного пополнения на базе.
    work_available — активный контракт «работа» на этой локации: кнопка «Выполнить работу».
    """
    rows: list[list[InlineKeyboardButton]] = []
    if work_available:
        rows.append([InlineKeyboardButton(text="⚙️ Выполнить работу", callback_data="contract:work")])
    if is_home_base:
        rows.append(
            [InlineKeyboardButton(text="🎒 Пополнить снаряжение", callback_data="locmap:resupply")]
        )
        if resupply_cooldown:
            rows[-1] = [
                InlineKeyboardButton(
                    text=f"🎒 Пополнить снаряжение (КД {resupply_cooldown})",
                    callback_data="locmap:noop",
                )
            ]
        rows.append(
            [InlineKeyboardButton(text="📋 Задания", callback_data="locmap:quests")]
        )
        rows.append(
            [InlineKeyboardButton(text="🛒 Торговец", callback_data="locmap:vendor")]
        )
    if show_secret_trader:
        rows.append(
            [InlineKeyboardButton(text="🕵 Бунс (тайный торговец)", callback_data="locmap:secrettrader")]
        )
    for zone, remaining in zones_status:
        kind = str(zone.get("kind") or "")
        zone_id = str(zone.get("id") or "")
        label = str(zone.get("label") or zone_id)
        if kind == "anomaly":
            if remaining:
                rows.append(
                    [InlineKeyboardButton(text=f"☢ Поиск артов (КД {remaining})", callback_data="locmap:noop")]
                )
            else:
                rows.append(
                    [InlineKeyboardButton(text="☢ Поиск артов", callback_data="locmap:anomaly")]
                )
        elif kind == "bazaar":
            rows.append(
                [InlineKeyboardButton(text="🏦 Барахолка", callback_data="locmap:bazaar")]
            )
        elif kind == "forester":
            rows.append(
                [InlineKeyboardButton(text="🪵 Лесник: трофеи ×3", callback_data="locmap:forester")]
            )
        elif kind == "trade":
            rows.append(
                [InlineKeyboardButton(text="🛒 Торговец", callback_data="locmap:vendor")]
            )
        elif kind == "search":
            if remaining is None:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"🏚 Поиск хабара: {label}",
                            callback_data=f"locmap:search:{zone_id}",
                        )
                    ]
                )
            else:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"🏚 Поиск хабара: {label} (КД {remaining})",
                            callback_data="locmap:noop",
                        )
                    ]
                )
        elif kind == "lab":
            rows.append(
                [InlineKeyboardButton(text=f"🧪 {label}", callback_data=f"locmap:lab:{zone_id}")]
            )
    special_row = _special_event_button(special_label)
    if special_row:
        rows.append(special_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def location_walk_keyboard(
    walk_state: dict[str, Any] | None,
    location: str,
    zones_status: list[tuple[dict, str | None]],
    *,
    is_home: bool = False,
    show_secret_trader: bool = False,
    resupply_cooldown: str | None = None,
    special_label: str | None = None,
    work_available: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура «открытой локации»: стрелки-ходы + действие зоны, в которой стоишь.

    walk_state — словарь {"x", "y", "zone"}: текущая клетка и зона под ней
    (dict из app.location_walk.zone_at) или None, если рядом зон нет.
    work_available — активный контракт «работа» на этой локации.
    """
    del location, show_secret_trader
    zone = walk_state.get("zone") if isinstance(walk_state, dict) else None
    rows: list[list[InlineKeyboardButton]] = []
    if work_available:
        rows.append([InlineKeyboardButton(text="⚙️ Выполнить работу", callback_data="contract:work")])
    rows.extend([
        [InlineKeyboardButton(text="⬆️ Вперёд", callback_data="locwalk:up")],
        [
            InlineKeyboardButton(text="⬅️ Влево", callback_data="locwalk:left"),
            InlineKeyboardButton(text="⬇️ Назад", callback_data="locwalk:down"),
            InlineKeyboardButton(text="➡️ Вправо", callback_data="locwalk:right"),
        ],
    ])
    if isinstance(zone, dict) and zone:
        kind = str(zone.get("kind") or "")
        zone_id = str(zone.get("id") or "")
        label = str(zone.get("label") or zone_id)
        if kind == "base":
            action_row = [InlineKeyboardButton(text="🛒 Торговец", callback_data="locmap:vendor")]
            if is_home:
                action_row.append(InlineKeyboardButton(text="📋 Задания", callback_data="locmap:quests"))
                if resupply_cooldown:
                    action_row.append(
                        InlineKeyboardButton(
                            text=f"🎒 Пополнить (КД {resupply_cooldown})",
                            callback_data="locmap:noop",
                        )
                    )
                else:
                    action_row.append(
                        InlineKeyboardButton(text="🎒 Пополнить снаряжение", callback_data="locmap:resupply")
                    )
            rows.append(action_row)
        elif kind == "secrettrader":
            rows.append([InlineKeyboardButton(text="🕵 Бунс (тайный торговец)", callback_data="locmap:secrettrader")])
        elif kind == "bazaar":
            rows.append([InlineKeyboardButton(text="🏦 Барахолка", callback_data="locmap:bazaar")])
        elif kind == "forester":
            rows.append([InlineKeyboardButton(text="🪵 Лесник: трофеи ×3", callback_data="locmap:forester")])
        elif kind == "trade":
            rows.append([InlineKeyboardButton(text="🛒 Торговец", callback_data="locmap:vendor")])
        elif kind == "anomaly":
            if remaining:
                rows.append(
                    [InlineKeyboardButton(text=f"☢ Поиск артов (КД {remaining})", callback_data="locmap:noop")]
                )
            else:
                rows.append([InlineKeyboardButton(text="☢ Поиск артов", callback_data="locmap:anomaly")])
        elif kind == "lair":
            rows.append([InlineKeyboardButton(text="👹 Зачистка логова", callback_data="locmap:lair")])
        elif kind == "search":
            remaining = next(
                (rem for z, rem in zones_status if str(z.get("id") or "") == zone_id),
                None,
            )
            if remaining is None:
                rows.append(
                    [InlineKeyboardButton(text=f"🏚 Поиск хабара: {label}", callback_data=f"locmap:search:{zone_id}")]
                )
            else:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"🏚 Поиск хабара: {label} (КД {remaining})",
                            callback_data="locmap:noop",
                        )
                    ]
                )
        elif kind == "lab":
            rows.append([InlineKeyboardButton(text=f"🧪 {label}", callback_data=f"locmap:lab:{zone_id}")])
    special_row = _special_event_button(special_label)
    if special_row:
        rows.append(special_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def secret_trader_sell_keyboard(inventory_counts: dict[str, int]) -> InlineKeyboardMarkup:
    """Кнопки продажи информации тайному торговцу (позиции с количеством > 0).

    Кнопка «Продать всё» добавляется, только если есть что продавать.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for key in INTEL_ITEM_KEYS:
        count = int(inventory_counts.get(key, 0))
        if count <= 0:
            continue
        price = int(INTEL_SELL_PRICES.get(key, 0)) * count
        label = ITEM_LABELS.get(key, key)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Продать {label} x{count} — {price} RU",
                    callback_data=f"selltrade:{key}",
                )
            ]
        )
    if rows:
        rows.append([InlineKeyboardButton(text="💰 Продать всё", callback_data="selltrade:all")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def artifact_hunt_keyboard(*, deep_available: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="⬆️ Вперёд", callback_data="hunt:up")],
        [
            InlineKeyboardButton(text="⬅️ Влево", callback_data="hunt:left"),
            InlineKeyboardButton(text="⬇️ Назад", callback_data="hunt:down"),
            InlineKeyboardButton(text="➡️ Вправо", callback_data="hunt:right"),
        ],
        [
            InlineKeyboardButton(text="🔫⬆", callback_data="hunt:shoot:up"),
            InlineKeyboardButton(text="🔫⬅", callback_data="hunt:shoot:left"),
            InlineKeyboardButton(text="🔫⬇", callback_data="hunt:shoot:down"),
            InlineKeyboardButton(text="🔫➡", callback_data="hunt:shoot:right"),
        ],
        [
            InlineKeyboardButton(text="🏃 Свалить", callback_data="hunt:leave"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data="hunt:refresh"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def stash_hunt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬆️ Вперёд", callback_data="sthunt:up")],
            [
                InlineKeyboardButton(text="⬅️ Влево", callback_data="sthunt:left"),
                InlineKeyboardButton(text="⬇️ Назад", callback_data="sthunt:down"),
                InlineKeyboardButton(text="➡️ Вправо", callback_data="sthunt:right"),
            ],
            [
                InlineKeyboardButton(text="🏃 Уйти", callback_data="sthunt:leave"),
                InlineKeyboardButton(text="🔄 Обновить", callback_data="sthunt:refresh"),
            ],
        ]
    )


def lab_combat_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬆️ Вперёд", callback_data="labact:up")],
            [
                InlineKeyboardButton(text="⬅️ Влево", callback_data="labact:left"),
                InlineKeyboardButton(text="⬇️ Назад", callback_data="labact:down"),
                InlineKeyboardButton(text="➡️ Вправо", callback_data="labact:right"),
            ],
            [
                InlineKeyboardButton(text="🔫⬆", callback_data="labact:shoot:up"),
                InlineKeyboardButton(text="🔫⬅", callback_data="labact:shoot:left"),
                InlineKeyboardButton(text="🔫⬇", callback_data="labact:shoot:down"),
                InlineKeyboardButton(text="🔫➡", callback_data="labact:shoot:right"),
            ],
            [
                InlineKeyboardButton(text="🧪 Обыск/дверь", callback_data="labact:interact"),
                InlineKeyboardButton(text="💉 Аптечка", callback_data="labact:medkit"),
            ],
            [
                InlineKeyboardButton(text="🏃 Свалить", callback_data="labact:leave"),
                InlineKeyboardButton(text="🔄 Обновить", callback_data="labact:refresh"),
            ],
        ]
    )


def lab_transition_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬇ Спуститься", callback_data="labact:enter")]]
    )


def lab_ambush_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🕊 Отдать артефакт и документы", callback_data="labact:giveup")],
            [InlineKeyboardButton(text="⚔️ Принять бой", callback_data="labact:fight")],
        ]
    )


def equip_artifact_slot_picker_keyboard(item_key: str, slots_cap: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    slot_row: list[InlineKeyboardButton] = []
    for idx in range(1, min(slots_cap, 3) + 1):
        slot_row.append(
            InlineKeyboardButton(
                text=f"Ячейка {idx}",
                callback_data=f"equip:pick:artifact:{idx}:{item_key}",
            )
        )
    rows.append(slot_row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="equip:menu:artifact")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tech_craft_junk_keyboard(
    junk_items: list[tuple[str, str, int]],
    *,
    stage: str = "a",
    first_key: str = "",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key, title, amount in junk_items[:12]:
        if stage == "a":
            cb = f"tech:craft:a:{key}"
        else:
            cb = f"tech:craft:b:{first_key}:{key}"
        rows.append([InlineKeyboardButton(text=f"{title} ×{amount}", callback_data=cb)])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к технику", callback_data="trade:vendor:tech")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def smuggle_mission_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬆️ Вперёд", callback_data="smission:up")],
            [
                InlineKeyboardButton(text="⬅️ Влево", callback_data="smission:left"),
                InlineKeyboardButton(text="⬇️ Назад", callback_data="smission:down"),
                InlineKeyboardButton(text="➡️ Вправо", callback_data="smission:right"),
            ],
            [
                InlineKeyboardButton(text="🔄", callback_data="smission:refresh"),
                InlineKeyboardButton(text="📦 Сбросить груз", callback_data="smission:abandon"),
            ],
        ]
    )


def quest_mission_keyboard(*, medkits: int = 0, shoot_available: bool = False) -> InlineKeyboardMarkup:
    med_label = f"💊 Аптечка ({medkits})" if medkits > 0 else "💊 Аптечка"
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text=med_label, callback_data="qmission:medkit"),
            InlineKeyboardButton(text="⬆️ Вперёд", callback_data="qmission:up"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data="qmission:refresh"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Влево", callback_data="qmission:left"),
            InlineKeyboardButton(text="⬇️ Назад", callback_data="qmission:down"),
            InlineKeyboardButton(text="➡️ Вправо", callback_data="qmission:right"),
        ],
    ]
    if shoot_available:
        rows.extend(
            [
                [InlineKeyboardButton(text="🔫 ⬆️", callback_data="qmission:shoot:up")],
                [
                    InlineKeyboardButton(text="🔫 ⬅️", callback_data="qmission:shoot:left"),
                    InlineKeyboardButton(text="🔫 ⬇️", callback_data="qmission:shoot:down"),
                    InlineKeyboardButton(text="🔫 ➡️", callback_data="qmission:shoot:right"),
                ],
            ]
        )
    rows.append([InlineKeyboardButton(text="🏃 Свалить", callback_data="qmission:leave")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def personal_stash_menu_keyboard(*, at_home: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if at_home:
        rows.append([InlineKeyboardButton(text="📥 Положить в хабар", callback_data="stash:putlist:0")])
        rows.append([InlineKeyboardButton(text="📤 Забрать из хабара", callback_data="stash:takelist:0")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад в инвентарь", callback_data="inventory:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def personal_stash_items_keyboard(
    buttons: list[tuple[str, str]],
    *,
    page: int,
    total_pages: int,
    page_prefix: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=label, callback_data=cb)] for label, cb in buttons
    ]
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(text="⬅️", callback_data=f"{page_prefix}:{page - 1}")
            )
        nav.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}",
                callback_data=f"{page_prefix}:{page}",
            )
        )
        if page + 1 < total_pages:
            nav.append(
                InlineKeyboardButton(text="➡️", callback_data=f"{page_prefix}:{page + 1}")
            )
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ К хабару", callback_data="stash:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def personal_stash_amount_keyboard(action: str, item_key: str, max_amount: int) -> InlineKeyboardMarkup:
    """action: put | take"""
    rows: list[list[InlineKeyboardButton]] = []
    choices = [1]
    if max_amount >= 5:
        choices.append(5)
    if max_amount >= 10:
        choices.append(10)
    if max_amount not in choices:
        choices.append(max_amount)
    for qty in choices:
        if qty > max_amount:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{'📥' if action == 'put' else '📤'} {qty} шт.",
                    callback_data=f"stash:{action}qty:{item_key}:{qty}",
                )
            ]
        )
    back = "stash:putlist:0" if action == "put" else "stash:takelist:0"
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def inventory_consumables_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡ Выпить энергетик (+35 энергии)", callback_data="use:energy_drink")],
            [InlineKeyboardButton(text="💨 Активировать закись азота (N2O, ÷2 время пути)", callback_data="use:nitrous_oxide")],
            [InlineKeyboardButton(text="🩹 Использовать аптечку (+25 к здоровью)", callback_data="use:medkit")],
            [InlineKeyboardButton(text="🪖 Армейская аптечка (+50 к здоровью)", callback_data="use:medkit_army")],
            [InlineKeyboardButton(text="🔬 Научная аптечка (+75 к здоровью, −15 рад.)", callback_data="use:medkit_science")],
            [InlineKeyboardButton(text="🍸 Выпить водку (−20 рад.)", callback_data="use:vodka")],
            [InlineKeyboardButton(text="💉 Использовать антирад (−50 рад.)", callback_data="use:antirad")],
            [InlineKeyboardButton(text="💊 Принять антибиотик (защита от Выброса)", callback_data="use:antibiotic")],
            [InlineKeyboardButton(text="🍞 Поесть хлеб (голод −10)", callback_data="use:bread")],
            [InlineKeyboardButton(text="🥓 Поесть колбасу (голод −20)", callback_data="use:sausage")],
            [InlineKeyboardButton(text="🥫 Поесть тушёнку (голод −50)", callback_data="use:stew")],
            [InlineKeyboardButton(text="💧 Выпить воду (жажда −10)", callback_data="use:water_bottle")],
            [InlineKeyboardButton(text="🧴 Выпить минералку (жажда −20)", callback_data="use:mineral_water")],
            [InlineKeyboardButton(text="🍵 Выпить чай Бороды (жажда −50)", callback_data="use:beard_tea")],
            [InlineKeyboardButton(text="⬅️ Назад в инвентарь", callback_data="inventory:open")],
        ]
    )


def dead_character_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="♻️ Спасение на базе (500 RU / в долг)", callback_data="respawn:base")],
        ]
    )


def equip_root_keyboard(
    items: list[tuple[str, str, int]],
    *,
    can_install_upgrade: bool = False,
    can_remove_upgrade: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for slot_key, title, count in items:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{title} ({count})",
                    callback_data=f"equip:slot:{slot_key}:0",
                )
            ]
        )
    if can_install_upgrade:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🛡 Установить улучшение брони",
                    callback_data="equip:upgrade:install",
                )
            ]
        )
    if can_remove_upgrade:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🛡 Снять улучшение брони",
                    callback_data="equip:upgrade:remove",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад в инвентарь", callback_data="inventory:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def equip_slot_page_keyboard(
    slot: str,
    *,
    page: int,
    total_pages: int,
    options: list[tuple[str, str, int]],
    can_unequip_artifact: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item_key, title, amount in options:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Надеть: {title} (×{amount})",
                    callback_data=f"equip:put:{slot}:{item_key}",
                )
            ]
        )
    if not options:
        rows.append([InlineKeyboardButton(text="Пусто", callback_data=f"equip:slot:{slot}:{page}")])
    if slot == "artifact" and can_unequip_artifact:
        rows.append(
            [InlineKeyboardButton(text="Снять артефакт", callback_data="equip:unequip:artifact")]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"equip:slot:{slot}:{page - 1}",
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data=f"equip:slot:{slot}:{page}",
        )
    )
    if page + 1 < total_pages:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"equip:slot:{slot}:{page + 1}",
            )
        )
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ К категориям", callback_data="equip:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def equip_weapon_keyboard(available_weapons: list[tuple[str, str, int]]) -> InlineKeyboardMarkup:
    """Совместимость: старое прямое меню оружия → страница экипировки."""
    return equip_slot_page_keyboard("weapon", page=0, total_pages=1, options=available_weapons)


def equip_armor_keyboard(available_armor: list[tuple[str, str, int]]) -> InlineKeyboardMarkup:
    """Совместимость: старое прямое меню брони → страница экипировки."""
    return equip_slot_page_keyboard("armor", page=0, total_pages=1, options=available_armor)


def trader_buy_armor_keyboard(
    *, page: int = 0, unlocked_keys: set[str] | frozenset[str] | None = None
) -> InlineKeyboardMarkup:
    from app.game_logic import shop_armor_button_title

    catalog = [
        (shop_armor_button_title("armor_leather"), "buy:armor_leather", "armor_leather"),
        (shop_armor_button_title("armor_stalker_vest"), "buy:armor_stalker_vest", "armor_stalker_vest"),
        (shop_armor_button_title("armor_zarya"), "buy:armor_sunrise", "armor_zarya"),
        (shop_armor_button_title("armor_psz7d"), "buy:armor_psz7d", "armor_psz7d"),
        (shop_armor_button_title("armor_bulat"), "buy:armor_berill5m", "armor_bulat"),
        (shop_armor_button_title("armor_seva"), "buy:armor_seva", "armor_seva"),
        (shop_armor_button_title("armor_scientific"), "buy:armor_scientific", "armor_scientific"),
        (shop_armor_button_title("armor_exo"), "buy:armor_exoskeleton", "armor_exo"),
        (shop_armor_button_title("armor_nosorog"), "buy:armor_nosorog", "armor_nosorog"),
    ]
    items = _filter_shop_rows(catalog, unlocked_keys)
    return _trader_page_keyboard(
        items,
        page=page,
        page_prefix="trade:barkeep:armor",
        back_callback="trade:vendor:barkeep",
        back_text="⬅️ Назад к бармену",
    )


def trader_buy_weapons_keyboard(
    *, page: int = 0, unlocked_keys: set[str] | frozenset[str] | None = None
) -> InlineKeyboardMarkup:
    from app.game_logic import shop_consumable_button_title, shop_weapon_button_title

    catalog = [
        (shop_consumable_button_title("ammo_pistol"), "buyqty:ammo_pistol", "ammo_pistol"),
        (shop_consumable_button_title("ammo_shotgun"), "buyqty:ammo_shotgun", "ammo_shotgun"),
        (shop_consumable_button_title("ammo_rifle"), "buyqty:ammo_rifle", "ammo_rifle"),
        (shop_consumable_button_title("ammo_gauss"), "buyqty:ammo_gauss", "ammo_gauss"),
        (shop_weapon_button_title("weapon_pm"), "buy:weapon_pm", "weapon_pm"),
        (shop_weapon_button_title("weapon_fort12"), "buy:weapon_fora12", "weapon_fort12"),
        (shop_weapon_button_title("weapon_sawedoff"), "buy:weapon_sawedoff", "weapon_sawedoff"),
        (shop_weapon_button_title("weapon_mp5"), "buy:weapon_mp5", "weapon_mp5"),
        (shop_weapon_button_title("weapon_chaser13"), "buy:weapon_chaser13", "weapon_chaser13"),
        (shop_weapon_button_title("weapon_aks74u"), "buy:weapon_aks74u", "weapon_aks74u"),
        (shop_weapon_button_title("weapon_ak74"), "buy:weapon_ak74", "weapon_ak74"),
        (shop_weapon_button_title("weapon_spas12"), "buy:weapon_spas12", "weapon_spas12"),
        (shop_weapon_button_title("weapon_lr300"), "buy:weapon_lr300", "weapon_lr300"),
        (shop_weapon_button_title("weapon_il86"), "buy:weapon_il86", "weapon_il86"),
        (shop_weapon_button_title("weapon_an94"), "buy:weapon_an94", "weapon_an94"),
        (shop_weapon_button_title("weapon_gp37"), "buy:weapon_gp37", "weapon_gp37"),
        (shop_weapon_button_title("weapon_vintar"), "buy:weapon_vintar", "weapon_vintar"),
        (shop_weapon_button_title("weapon_svd"), "buy:weapon_svd", "weapon_svd"),
        (shop_weapon_button_title("weapon_rp74"), "buy:weapon_rp74", "weapon_rp74"),
        (shop_weapon_button_title("weapon_gauss"), "buy:weapon_gauss", "weapon_gauss"),
        (shop_weapon_button_title("weapon_raccoon"), "buy:weapon_raccoon", "weapon_raccoon"),
    ]
    items = _filter_shop_rows(catalog, unlocked_keys)
    return _trader_page_keyboard(
        items,
        page=page,
        page_prefix="trade:barkeep:weapons",
        back_callback="trade:vendor:barkeep",
        back_text="⬅️ Назад к бармену",
    )


def trader_sell_categories_keyboard(
    items: list[tuple[str, str]] | None = None,
) -> InlineKeyboardMarkup:
    if items is None:
        items = [
            ("🧰 Расходники", "trade:sell:consumables:0"),
            ("💎 Трофеи", "trade:sell:trophies:0"),
            ("🛠 Прочее", "trade:sell:gear:0"),
            ("🦺 Броня", "trade:sell:armor:0"),
            ("🔫 Оружие", "trade:sell:weapons:0"),
        ]
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=title, callback_data=callback)] for title, callback in items
    ]
    if not rows:
        rows.append(
            [InlineKeyboardButton(text="Нечего продавать", callback_data="trade:menu:root")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад в Торговец", callback_data="trade:menu:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _trader_sell_items_keyboard(
    items: list[tuple[str, str]],
    *,
    page: int,
    page_prefix: str,
) -> InlineKeyboardMarkup:
    if not items:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="В этой категории нечего продавать", callback_data="trade:menu:sell")],
                [InlineKeyboardButton(text="⬅️ Назад к категориям продажи", callback_data="trade:menu:sell")],
            ]
        )
    return _trader_page_keyboard(
        items,
        page=page,
        page_prefix=page_prefix,
        back_callback="trade:menu:sell",
        back_text="⬅️ Назад к категориям продажи",
    )


def trader_sell_consumables_keyboard(
    items: list[tuple[str, str]] | None = None,
    *,
    page: int = 0,
) -> InlineKeyboardMarkup:
    if items is None:
        items = default_trader_sell_catalog_buttons("consumables")
    return _trader_sell_items_keyboard(items, page=page, page_prefix="trade:sell:consumables")


def trader_sell_trophies_keyboard(
    items: list[tuple[str, str]] | None = None,
    *,
    page: int = 0,
) -> InlineKeyboardMarkup:
    if items is None:
        items = default_trader_sell_catalog_buttons("trophies")
    return _trader_sell_items_keyboard(items, page=page, page_prefix="trade:sell:trophies")


def trader_sell_gear_keyboard(
    items: list[tuple[str, str]] | None = None,
    *,
    page: int = 0,
) -> InlineKeyboardMarkup:
    if items is None:
        items = default_trader_sell_catalog_buttons("gear")
    return _trader_sell_items_keyboard(items, page=page, page_prefix="trade:sell:gear")


def trader_sell_armor_keyboard(
    items: list[tuple[str, str]] | None = None,
    *,
    page: int = 0,
) -> InlineKeyboardMarkup:
    if items is None:
        items = default_trader_sell_catalog_buttons("armor")
    return _trader_sell_items_keyboard(items, page=page, page_prefix="trade:sell:armor")


def trader_sell_weapons_keyboard(
    items: list[tuple[str, str]] | None = None,
    *,
    page: int = 0,
) -> InlineKeyboardMarkup:
    if items is None:
        items = default_trader_sell_catalog_buttons("weapons")
    return _trader_sell_items_keyboard(items, page=page, page_prefix="trade:sell:weapons")


def locations_keyboard(
    locations: list[dict[str, str | int | None]],
    mode: str,
    *,
    back_callback: str | None = None,
) -> InlineKeyboardMarkup:
    rows = []
    for location in locations:
        name = str(location["name"])
        ptype = str(location["point_type"])
        owner = location["controlled_by"] or "нейтрал"
        text = f"{name} [{ptype}, {owner}]"
        rows.append([InlineKeyboardButton(text=text, callback_data=f"{mode}:{name}")])
    if back_callback:
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topup_root_keyboard(*, has_faction: bool = False) -> InlineKeyboardMarkup:
    rate = TOPUP_RATE_RU_PER_STAR
    rows = [
        [InlineKeyboardButton(text=f"⭐ Себе (1⭐ = {rate} RU)", callback_data="topup:menu:self")],
    ]
    if has_faction:
        rows.append(
            [InlineKeyboardButton(text=f"🏛 Казна группировки (1⭐ = {rate} RU)", callback_data="topup:menu:faction")]
        )
    rows.append(
        [InlineKeyboardButton(text="🏴 Своя группировка (премиум)", callback_data="topup:menu:perks")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topup_keyboard(*, dest: str = "self") -> InlineKeyboardMarkup:
    rate = TOPUP_RATE_RU_PER_STAR
    prefix = "topup:self" if dest != "faction" else "topup:faction"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"⭐ 1 звезда ({rate} RU)", callback_data=f"{prefix}:1")],
            [InlineKeyboardButton(text=f"⭐ 5 звёзд ({5 * rate} RU)", callback_data=f"{prefix}:5")],
            [InlineKeyboardButton(text=f"⭐ 10 звёзд ({10 * rate} RU)", callback_data=f"{prefix}:10")],
            [InlineKeyboardButton(text=f"⭐ 25 звёзд ({25 * rate} RU)", callback_data=f"{prefix}:25")],
            [InlineKeyboardButton(text="⭐ Другое количество", callback_data=f"{prefix}:custom")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="topup:menu:root")],
        ]
    )


def raid_keyboard(
    locations: list[dict[str, str | int | None]],
    *,
    led_raids: list[dict[str, Any]] | None = None,
    war_enemy_factions: list[str] | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="➕ Присоединиться к рейду", callback_data="raid:join")],
        [InlineKeyboardButton(text="🤝 Присоединиться как союзник", callback_data="raid:ally:join")],
        [InlineKeyboardButton(text="🚀 Запустить рейд (мин. 2 бойца)", callback_data="raid:launch")],
    ]
    led = led_raids or []
    if led:
        for raid in led:
            raid_id = int(raid["id"])
            location = str(raid.get("location") or "?")
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"❌ Отменить рейд #{raid_id}: {location}",
                        callback_data=f"raid:cancel:{raid_id}",
                    )
                ]
            )
        rows.append(
            [InlineKeyboardButton(text="🗑 Отменить все мои рейды", callback_data="raid:cancel:all")]
        )
    for location in locations:
        if str(location.get("point_type") or "") == "база":
            continue
        name = str(location["name"])
        rows.append([InlineKeyboardButton(text=f"Создать рейд на логово: {name}", callback_data=f"raid:create:{name}")])
    for enemy in war_enemy_factions or []:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🏚 Рейд на склад врага: {enemy}",
                    callback_data=f"raid:depot:warehouse:{enemy}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🏚 Рейд на гараж врага: {enemy}",
                    callback_data=f"raid:depot:garage:{enemy}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def war_lobby_keyboard(
    locations: list[dict[str, str | int | None]],
    *,
    can_dissolve: bool = False,
    monolith_join: bool = False,
    monolith_join_label: str = "☢ Вступить в бой Монолита",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="➕ Вступить в лобби", callback_data="war_lobby:join")],
        [
            InlineKeyboardButton(
                text=f"🚀 Запустить штурм (мин. {WAR_MIN_FACTION_MEMBERS} бойцов)",
                callback_data="war_lobby:launch",
            )
        ],
    ]
    if monolith_join:
        rows.append(
            [InlineKeyboardButton(text=monolith_join_label, callback_data="monolith_war:join")]
        )
        rows.append(
            [InlineKeyboardButton(text="▶ Начать бой сейчас", callback_data="monolith_war:start")]
        )
    if can_dissolve:
        rows.append(
            [InlineKeyboardButton(text="🛑 Распустить лобби", callback_data="war_lobby:dissolve")]
        )
    for location in locations:
        name = str(location["name"])
        rows.append([InlineKeyboardButton(text=f"Создать штурм-лобби: {name}", callback_data=f"war_lobby:create:{name}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="war:section:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def war_transfer_keyboard(allies: list[str], location_name: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for ally in allies:
        rows.append(
            [InlineKeyboardButton(text=f"🎁 Отдать {location_name} → {ally}", callback_data=f"war:transfer:{ally}")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="war:section:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def war_sections_keyboard(*, monolith: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="📘 Правила и дипломатия", callback_data="war:section:scenario")],
        [InlineKeyboardButton(text="🎯 Захват нейтральных точек", callback_data="war:section:ncap")],
        [InlineKeyboardButton(text="🎁 Передача точки союзнику", callback_data="war:section:transfer")],
        [
            InlineKeyboardButton(
                text=f"🪖 Военные лобби (мин. {WAR_MIN_FACTION_MEMBERS} бойцов)",
                callback_data="war:section:lobby",
            )
        ],
    ]
    if monolith:
        rows.insert(
            0,
            [InlineKeyboardButton(text="☢ Атака Монолита", callback_data="war:section:monolith_attack")],
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def monolith_attack_targets_keyboard(
    locations: list[dict[str, str | int | None]],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for location in locations:
        name = str(location["name"])
        owner = location.get("controlled_by") or "нейтрал"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"☢ Атака: {name} [{owner}]",
                    callback_data=f"monolith_war:attack:{name}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="war:section:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _warehouse_custom_item_rows(*, action: str, artifacts_only: bool = False) -> list[list[InlineKeyboardButton]]:
    """Кнопки сдачи/выдачи предметов на склад ГП (своё количество).

    artifacts_only=True — только артефакты (подменю «💎 Артефакты»).
    """
    from app.game_logic import ITEM_LABELS

    if action == "deposit":
        emoji, verb = "📥", "Сдать"
    else:
        emoji, verb = "📤", "Забрать"
    rows: list[list[InlineKeyboardButton]] = []
    if not artifacts_only:
        rows.append([InlineKeyboardButton(text=f"{emoji} {verb} патрон — своё количество", callback_data=f"eco:warehouse:{action}:ammo_pack")])
        rows.append([InlineKeyboardButton(text=f"{emoji} {verb} аптечку — своё количество", callback_data=f"eco:warehouse:{action}:medkit")])
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{emoji} {verb} энергетик — своё количество",
                    callback_data=f"eco:warehouse:{action}:energy_drink",
                )
            ]
        )
        if action == "deposit":
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{emoji} {verb} стройматериалы — своё количество",
                        callback_data="eco:warehouse:deposit:materials",
                    )
                ]
            )
    for art_key in ARTIFACT_DROP_KEYS:
        label = ITEM_LABELS.get(art_key, art_key)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{emoji} {verb} {label} — своё количество",
                    callback_data=f"eco:warehouse:{action}:{art_key}",
                )
            ]
        )
    return rows


def warehouse_deposit_keyboard() -> InlineKeyboardMarkup:
    """Подменю сдачи на склад: расходники + кнопка «Артефакты»."""
    rows = _warehouse_custom_item_rows(action="deposit", artifacts_only=False)
    rows.append([InlineKeyboardButton(text="💎 Артефакты", callback_data="eco:warehouse:menu:deposit:artifacts")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="faction:group")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def warehouse_withdraw_keyboard() -> InlineKeyboardMarkup:
    """Подменю выдачи со склада: расходники + кнопка «Артефакты»."""
    rows = _warehouse_custom_item_rows(action="withdraw", artifacts_only=False)
    rows.append([InlineKeyboardButton(text="💎 Артефакты", callback_data="eco:warehouse:menu:withdraw:artifacts")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="faction:group")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def warehouse_artifacts_keyboard(*, action: str) -> InlineKeyboardMarkup:
    """Список артефактов для сдачи/выдачи со склада."""
    rows = _warehouse_custom_item_rows(action=action, artifacts_only=True)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="faction:group")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def faction_group_keyboard(
    *,
    is_leader: bool = False,
    can_withdraw_warehouse: bool = False,
    can_withdraw_treasury: bool = False,
    can_request_garage_rental: bool = False,
    pending_garage_requests: int = 0,
    faction: str | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="📥 Сдать на склад", callback_data="eco:warehouse:menu:deposit")],
        [InlineKeyboardButton(text="💰 Внести своё количество", callback_data="eco:treasury:deposit:custom")],
        [InlineKeyboardButton(text="🏚 Гараж: сдать канистру бензина", callback_data="faction:garage:deposit:gasoline")],
        [InlineKeyboardButton(text="🏚 Гараж: сдать канистру дизеля", callback_data="faction:garage:deposit:diesel")],
        [InlineKeyboardButton(text="🏚 Сдать Ниву в гараж", callback_data="faction:garage:deposit:niva")],
        [InlineKeyboardButton(text="🏚 Сдать грузовик в гараж", callback_data="faction:garage:deposit:truck")],
    ]
    if can_request_garage_rental:
        rows.extend(
            [
                [InlineKeyboardButton(text="🏚 Запросить аренду Нивы", callback_data="faction:garage:request:niva")],
                [
                    InlineKeyboardButton(
                        text="🏚 Запросить аренду грузовика",
                        callback_data="faction:garage:request:truck",
                    )
                ],
            ]
        )
    if can_withdraw_warehouse:
        request_label = "📋 Запросы на аренду"
        if pending_garage_requests > 0:
            request_label = f"📋 Запросы на аренду ({pending_garage_requests})"
        rows.extend(
            [
                [InlineKeyboardButton(text=request_label, callback_data="faction:garage:requests")],
                [InlineKeyboardButton(text="📤 Забрать со склада", callback_data="eco:warehouse:menu:withdraw")],
                [InlineKeyboardButton(text="🏚 Гараж: забрать канистру бензина", callback_data="faction:garage:withdraw:gasoline")],
                [InlineKeyboardButton(text="🏚 Гараж: забрать канистру дизеля", callback_data="faction:garage:withdraw:diesel")],
                [InlineKeyboardButton(text="🏚 Забрать Ниву из гаража", callback_data="faction:garage:withdraw:niva")],
                [InlineKeyboardButton(text="🏚 Забрать грузовик из гаража", callback_data="faction:garage:withdraw:truck")],
            ]
        )
    if can_withdraw_treasury:
        rows.extend(
            [
                [InlineKeyboardButton(text="🏦 Снять своё количество", callback_data="eco:treasury:withdraw:custom")],
            ]
        )
    if is_leader:
        rows.append([InlineKeyboardButton(text="🏗 Улучшения базы", callback_data="faction:upgrades:menu")])
        rows.append([InlineKeyboardButton(text="🎖 Назначить звание", callback_data="rank:menu")])
        rows.append([InlineKeyboardButton(text="📅 Цели недели", callback_data="faction:goals")])
        rows.append(
            [InlineKeyboardButton(text="🎁 Забрать бонус недели", callback_data="faction:goals:claim")]
        )
    rows.append([InlineKeyboardButton(text="🔄 Сменить группировку", callback_data="faction:change")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def faction_upgrades_keyboard(*, faction: str | None = None) -> InlineKeyboardMarkup:
    """Подменю «Улучшения базы»: постройки, боты, укрепление."""
    from app.game_logic import BASE_FORTIFY_COST_RU

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"🛡 Укрепить базу ({BASE_FORTIFY_COST_RU} RU из казны)",
                callback_data="faction:base:fortify",
            )
        ],
        [InlineKeyboardButton(text="🏗 Постройки", callback_data="faction:buildings:menu")],
    ]
    if faction != "Монолит":
        rows.append(
            [
                InlineKeyboardButton(
                    text="🤖 Улучшить ботов до 2-го тира (50000 RU)",
                    callback_data="faction:bots:upgrade",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🤖 +1 оборонительный бот (100000 RU)",
                callback_data="faction:bots:count",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="faction:group")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def faction_change_keyboard(current_faction: str | None) -> InlineKeyboardMarkup:
    from app.faction_change import FACTION_CHANGE_TARGETS

    rows: list[list[InlineKeyboardButton]] = []
    for name in FACTION_CHANGE_TARGETS:
        if name == current_faction:
            continue
        rows.append(
            [InlineKeyboardButton(text=f"→ {name}", callback_data=f"faction:change:{name}")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="faction:group")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def garage_rental_requests_keyboard(requests: list[dict[str, object]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for entry in requests:
        request_id = str(entry.get("id") or "")
        nickname = str(entry.get("player_nickname") or "?")
        vehicle = str(entry.get("vehicle_key") or "")
        vehicle_label = {"niva": "Нива", "truck": "Грузовик"}.get(vehicle, vehicle)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"✅ {nickname}: {vehicle_label}",
                    callback_data=f"faction:garage:approve:{request_id}",
                ),
                InlineKeyboardButton(
                    text="❌",
                    callback_data=f"faction:garage:deny:{request_id}",
                ),
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="faction:garage:requests:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def economy_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Биржа: список лотов", callback_data="eco:auction:list")],
            [InlineKeyboardButton(text="📦 Биржа: свой лот", callback_data="eco:auction:custom:choose")],
            [InlineKeyboardButton(text="🛑 Биржа: снять мой лот", callback_data="eco:auction:cancel:mine")],
            [InlineKeyboardButton(text="📋 Рынок: список лотов", callback_data="eco:market:list")],
            [InlineKeyboardButton(text="🛒 Рынок: выставить экипировку", callback_data="eco:market:create:choose")],
            [InlineKeyboardButton(text="🛑 Рынок: снять мой лот", callback_data="eco:market:cancel:mine")],
            [InlineKeyboardButton(text="🚚 Контрабанда", callback_data="eco:smuggle:menu")],
        ]
    )


def bazaar_confirm_buy_kb(kind: str, lot_id: int, back_cb: str) -> InlineKeyboardMarkup:
    """kind: market | auction."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить покупку",
                    callback_data=f"eco:{kind}:confirm:{lot_id}",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)],
        ]
    )


def trade_offer_keyboard(from_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Принять", callback_data=f"trade:accept:{from_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"trade:decline:{from_id}"),
            ],
            [InlineKeyboardButton(text="🚫 Отменить (инициатор)", callback_data=f"trade:cancel:{from_id}")],
        ]
    )


def smuggling_keyboard(
    destinations: list[str],
    *,
    has_active: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_active:
        rows.append([InlineKeyboardButton(text="📦 Сбросить груз", callback_data="eco:smuggle:abandon")])
        rows.append([InlineKeyboardButton(text="⏱ Статус рейса", callback_data="eco:smuggle:status")])
    else:
        for name in destinations:
            rows.append(
                [InlineKeyboardButton(text=f"→ {name}", callback_data=f"eco:smuggle:to:{name}")]
            )
    rows.append([InlineKeyboardButton(text="⬅️ Назад в барахолку", callback_data="eco:menu:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def market_lots_keyboard(lots: list[dict[str, str | int]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for lot in lots[:20]:
        lot_id = int(lot["id"])
        title = str(lot["title"])
        price = int(lot["price"])
        amount = int(lot["amount"])
        seller_name = str(lot.get("seller_name") or lot.get("seller_id") or "?")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Ячейка №{lot_id} · {title} ×{amount} · {price} RU · {seller_name}",
                    callback_data=f"eco:market:buy:{lot_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад в барахолку", callback_data="eco:menu:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def market_lot_keyboard(lots: list[dict[str, str | int]]) -> InlineKeyboardMarkup:
    return market_lots_keyboard(lots)


EXCHANGE_FILTER_BUTTONS: tuple[tuple[str, str], ...] = (
    ("all", "Все"),
    ("artifact", "Артефакты"),
    ("consumable", "Расходники"),
    ("fuel", "Топливо"),
)


def exchange_lots_keyboard(
    lots: list[dict[str, Any]],
    *,
    category: str = "all",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    filter_row: list[InlineKeyboardButton] = []
    for filter_key, label in EXCHANGE_FILTER_BUTTONS:
        text = f"• {label}" if filter_key == category else label
        callback_data = "eco:auction:list" if filter_key == "all" else f"eco:auction:list:{filter_key}"
        filter_row.append(InlineKeyboardButton(text=text, callback_data=callback_data))
    rows.append(filter_row)
    lot_rows: list[list[InlineKeyboardButton]] = []
    for lot in lots[:20]:
        lot_id = int(lot["id"])
        title = str(lot["title"])
        price = int(lot["price"])
        amount = int(lot["amount"])
        seller_id = int(lot["seller_id"])
        if lot.get("is_own"):
            lot_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"🛑 Ячейка №{lot_id} · {title} ×{amount} · {price} RU (снять)",
                        callback_data=f"eco:auction:cancel:{lot_id}",
                    )
                ]
            )
        else:
            lot_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Ячейка №{lot_id} · {title} ×{amount} · {price} RU · игрок {seller_id}",
                        callback_data=f"eco:auction:buy:{lot_id}",
                    )
                ]
            )
    rows.extend(lot_rows)
    rows.append([InlineKeyboardButton(text="⬅️ Назад в барахолку", callback_data="eco:menu:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def market_create_select_keyboard(items: list[dict[str, str | int]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in items[:20]:
        item_key = str(item["item_key"])
        title = str(item["title"])
        amount = int(item["amount"])
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{title} (×{amount})",
                    callback_data=f"eco:market:create:{item_key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад в барахолку", callback_data="eco:menu:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def exchange_custom_select_keyboard(items: list[dict[str, str | int]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in items[:20]:
        item_key = str(item["item_key"])
        title = str(item["title"])
        amount = int(item["amount"])
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{title} (×{amount})",
                    callback_data=f"eco:auction:custom:{item_key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад в барахолку", callback_data="eco:menu:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ratings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Моя статистика", callback_data="ratings:stats")],
            [InlineKeyboardButton(text="🎖 Достижения и медали", callback_data="ratings:achievements")],
            [InlineKeyboardButton(text="🏅 Рейтинг группировок", callback_data="ratings:factions")],
            [InlineKeyboardButton(text="🏆 Рейтинг за всё время", callback_data="rating:alltime:page:0")],
            [InlineKeyboardButton(text="📅 Рейтинг за сезон", callback_data="rating:season:page:0")],
        ]
    )


def rating_page_keyboard(*, mode: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    safe_mode = mode if mode in {"alltime", "season"} else "alltime"
    rows: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"rating:{safe_mode}:page:{page - 1}",
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data=f"rating:{safe_mode}:page:{page}",
        )
    )
    if page + 1 < total_pages:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"rating:{safe_mode}:page:{page + 1}",
            )
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ К разделам рейтинга", callback_data="ratings:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def alliance_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🕊️ Предложить союз", callback_data="alliance:menu:propose")],
            [InlineKeyboardButton(text="✅ Подтвердить входящий договор", callback_data="alliance:menu:confirm")],
            [InlineKeyboardButton(text="⚔️ Объявить войну", callback_data="alliance:menu:declare_war")],
            [InlineKeyboardButton(text="💔 Разорвать союз", callback_data="alliance:menu:break")],
            [InlineKeyboardButton(text="⬅️ К разделам войны", callback_data="war:section:root")],
        ]
    )


def alliance_target_keyboard(
    factions: list[dict[str, int | str]],
    current_faction: str,
    mode: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for faction in factions:
        name = str(faction.get("name", ""))
        if not name or name == current_faction:
            continue
        if mode == "propose":
            rows.append(
                [InlineKeyboardButton(text=f"🕊️ Предложить союз: {name}", callback_data=f"alliance:propose:{name}")]
            )
        elif mode == "declare_war":
            rows.append([InlineKeyboardButton(text=f"⚔️ Объявить войну: {name}", callback_data=f"alliance:war:{name}")])
        else:
            rows.append([InlineKeyboardButton(text=f"💔 Разорвать с {name}", callback_data=f"alliance:break:{name}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="alliance:menu:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def alliance_pending_keyboard(pending_from: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for faction_name in pending_from:
        rows.append(
            [InlineKeyboardButton(text=f"✅ Подтвердить союз с {faction_name}", callback_data=f"alliance:confirm:{faction_name}")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="alliance:menu:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def duel_challenge_keyboard(challenger_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принять дуэль",
                    callback_data=f"duel:accept:{challenger_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"duel:decline:{challenger_id}",
                )
            ],
        ]
    )


def duel_grid_keyboard(*, is_active_turn: bool, medkit_available: bool) -> InlineKeyboardMarkup:
    return _tactical_grid_keyboard("dgrid", is_active_turn=is_active_turn, medkit_available=medkit_available)


def _tactical_grid_keyboard(
    prefix: str,
    *,
    is_active_turn: bool,
    medkit_available: bool,
    forfeit_label: str | None = "🏳 Сдаться",
    medkit_label: str = "💊 Аптечка (1×)",
    revive_targets: list[tuple[int, str]] | None = None,
) -> InlineKeyboardMarkup:
    refresh_btn = InlineKeyboardButton(text="🔄 Обновить", callback_data=f"{prefix}:refresh")
    forfeit_btn = (
        InlineKeyboardButton(text=forfeit_label, callback_data=f"{prefix}:forfeit")
        if forfeit_label
        else None
    )
    rows: list[list[InlineKeyboardButton]] = []
    # Выход/отступление — первой строкой: на длинной клавиатуре нижние кнопки
    # на телефоне часто уезжают за край экрана.
    if forfeit_btn is not None:
        rows.append([forfeit_btn])
    if not is_active_turn:
        rows.append([refresh_btn])
        return InlineKeyboardMarkup(inline_keyboard=rows)
    rows.extend(
        [
            [InlineKeyboardButton(text="🚶 ⬆️", callback_data=f"{prefix}:move:up")],
            [
                InlineKeyboardButton(text="🚶 ⬅️", callback_data=f"{prefix}:move:left"),
                InlineKeyboardButton(text="🚶 ⬇️", callback_data=f"{prefix}:move:down"),
                InlineKeyboardButton(text="🚶 ➡️", callback_data=f"{prefix}:move:right"),
            ],
            [InlineKeyboardButton(text="🔫 ⬆️", callback_data=f"{prefix}:shoot:up")],
            [
                InlineKeyboardButton(text="🔫 ⬅️", callback_data=f"{prefix}:shoot:left"),
                InlineKeyboardButton(text="🔫 ⬇️", callback_data=f"{prefix}:shoot:down"),
                InlineKeyboardButton(text="🔫 ➡️", callback_data=f"{prefix}:shoot:right"),
            ],
        ]
    )
    if medkit_available:
        rows.append([InlineKeyboardButton(text=medkit_label, callback_data=f"{prefix}:medkit")])
    for target_id, label in revive_targets or []:
        rows.append(
            [InlineKeyboardButton(text=f"💊 Поднять {label}", callback_data=f"{prefix}:revive:{target_id}")]
        )
    rows.append([refresh_btn])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cwar_grid_keyboard(*, is_active_turn: bool, medkit_available: bool) -> InlineKeyboardMarkup:
    return _tactical_grid_keyboard(
        "cwar",
        is_active_turn=is_active_turn,
        medkit_available=medkit_available,
        forfeit_label="🏃 Отступить",
    )


def rgrid_keyboard(
    *,
    is_active_turn: bool,
    medkit_available: bool,
    revive_targets: list[tuple[int, str]] | None = None,
) -> InlineKeyboardMarkup:
    return _tactical_grid_keyboard(
        "rgrid",
        is_active_turn=is_active_turn,
        medkit_available=medkit_available,
        revive_targets=revive_targets,
    )


def ncap_grid_keyboard(*, is_active_turn: bool, medkit_available: bool) -> InlineKeyboardMarkup:
    return _tactical_grid_keyboard(
        "ncap",
        is_active_turn=is_active_turn,
        medkit_available=medkit_available,
        forfeit_label="🏃 Отступить",
    )


def ncap_lobby_keyboard(*, in_lobby: bool, is_host: bool, lobby_id: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if in_lobby and lobby_id:
        if is_host:
            rows.append([InlineKeyboardButton(text="▶️ Начать захват", callback_data=f"ncap:start:{lobby_id}")])
        rows.append([InlineKeyboardButton(text="🚪 Выйти из группы", callback_data="ncap:leave")])
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="ncap:refresh")])
    rows.append([InlineKeyboardButton(text="⬅️ К войне", callback_data="war:section:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def arena_grid_keyboard(*, medkit_available: bool) -> InlineKeyboardMarkup:
    return _tactical_grid_keyboard(
        "agrid",
        is_active_turn=True,
        medkit_available=medkit_available,
        forfeit_label="🏃 Покинуть арену",
        medkit_label="💊 Аптечка арены",
    )


def coop_menu_keyboard(
    *,
    in_lobby: bool,
    is_host: bool,
    lobby_id: str | None = None,
    mission_kind: str | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if in_lobby and lobby_id:
        if is_host:
            rows.append([InlineKeyboardButton(text="🎯 Тип миссии", callback_data="coop:missions")])
            rows.append([InlineKeyboardButton(text="▶️ Начать вылазку", callback_data=f"coop:start:{lobby_id}")])
        rows.append([InlineKeyboardButton(text="🚪 Выйти из группы", callback_data="coop:leave")])
    else:
        rows.append([InlineKeyboardButton(text="➕ Создать группу", callback_data="coop:create")])
        rows.append([InlineKeyboardButton(text="🔍 Найти группу", callback_data="coop:list")])
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="coop:refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def coop_mission_pick_keyboard() -> InlineKeyboardMarkup:
    from app.coop_mission import COOP_MISSION_TYPES

    rows: list[list[InlineKeyboardButton]] = []
    for key, info in COOP_MISSION_TYPES.items():
        rows.append(
            [InlineKeyboardButton(text=info["title"], callback_data=f"coop:mission:{key}")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="coop:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def coop_lobby_list_keyboard(lobbies: list[tuple[str, str, int]]) -> InlineKeyboardMarkup:
    """(lobby_id, host_nickname, member_count)"""
    rows: list[list[InlineKeyboardButton]] = []
    for lobby_id, host_name, count in lobbies[:8]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"👥 {host_name} ({count}/3)",
                    callback_data=f"coop:join:{lobby_id}",
                )
            ]
        )
    if not rows:
        rows.append([InlineKeyboardButton(text="Групп нет", callback_data="coop:refresh")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="coop:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def coop_mission_keyboard(
    *, is_active_turn: bool, medkit_available: bool, evac_available: bool = False, shoot_available: bool = False
) -> InlineKeyboardMarkup:
    refresh_row = [
        InlineKeyboardButton(text="🔄", callback_data="coop:refresh"),
        InlineKeyboardButton(text="🏃 Свалить", callback_data="coop:forfeit"),
    ]
    if not is_active_turn:
        return InlineKeyboardMarkup(inline_keyboard=[refresh_row])
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="⬆️", callback_data="coop:move:up")],
        [
            InlineKeyboardButton(text="⬅️", callback_data="coop:move:left"),
            InlineKeyboardButton(text="⬇️", callback_data="coop:move:down"),
            InlineKeyboardButton(text="➡️", callback_data="coop:move:right"),
        ],
    ]
    if shoot_available:
        rows.extend(
            [
                [InlineKeyboardButton(text="🔫 ⬆️", callback_data="coop:shoot:up")],
                [
                    InlineKeyboardButton(text="🔫 ⬅️", callback_data="coop:shoot:left"),
                    InlineKeyboardButton(text="🔫 ⬇️", callback_data="coop:shoot:down"),
                    InlineKeyboardButton(text="🔫 ➡️", callback_data="coop:shoot:right"),
                ],
            ]
        )
    if medkit_available:
        rows.append([InlineKeyboardButton(text="💊 Аптечка", callback_data="coop:medkit")])
    if evac_available:
        rows.append([InlineKeyboardButton(text="🦺 Эвакуация", callback_data="coop:evac")])
    rows.append(refresh_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def players_factions_keyboard(items: list[tuple[str, str, int]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for faction_key, title, count in items:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{title} ({count})",
                    callback_data=f"players:f:{faction_key}:0",
                )
            ]
        )
    if not rows:
        rows.append([InlineKeyboardButton(text="Игроков нет", callback_data="players:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def players_faction_page_keyboard(
    faction_key: str,
    *,
    page: int,
    total_pages: int,
    players: list[dict[str, Any]] | None = None,
    self_id: int | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for row in players or []:
        target_id = int(row["telegram_id"])
        if self_id is not None and target_id == self_id:
            continue
        nickname = str(row.get("nickname") or target_id)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"⚔️ Вызвать на дуэль: {nickname}",
                    callback_data=f"duel:challenge:{target_id}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"players:f:{faction_key}:{page - 1}",
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data=f"players:f:{faction_key}:{page}",
        )
    )
    if page + 1 < total_pages:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"players:f:{faction_key}:{page + 1}",
            )
        )
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ К группировкам", callback_data="players:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def faction_ranks_members_keyboard(
    members: list[dict[str, str | int]],
    *,
    leader_id: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for member in members[:30]:
        telegram_id = int(member["telegram_id"])
        nickname = str(member.get("nickname") or telegram_id)
        if telegram_id == leader_id:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"⭐ {nickname} (лидер)",
                        callback_data="rank:leader:info",
                    )
                ]
            )
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=nickname,
                    callback_data=f"rank:member:{telegram_id}",
                )
            ]
        )
    if not rows:
        rows.append([InlineKeyboardButton(text="Бойцов нет", callback_data="faction:menu:root")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад в группировку", callback_data="faction:menu:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def faction_rank_pick_keyboard(target_id: int, ranks: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key, title in ranks:
        rows.append(
            [
                InlineKeyboardButton(
                    text=title,
                    callback_data=f"rank:set:{target_id}:{key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ К списку бойцов", callback_data="rank:menu")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад в группировку", callback_data="faction:menu:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


NOTIFY_PREF_TOGGLE_LABELS: dict[str, str] = {
    "emission": "☢️ Выброс",
    "death": "☠️ Смерть",
    "coop": "👥 Совместная вылазка",
    "garage": "🏚 Гараж (аренда)",
}


def notify_prefs_keyboard(prefs: dict[str, bool]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key, label in NOTIFY_PREF_TOGGLE_LABELS.items():
        mark = "✅" if prefs.get(key, True) else "🔕"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {label}",
                    callback_data=f"notify:toggle:{key}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tutorial_keyboard(page: int, total: int) -> InlineKeyboardMarkup:
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"tutorial:page:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total}", callback_data=f"tutorial:page:{page}"))
    if page + 1 < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"tutorial:page:{page + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[nav])


def clan_quest_keyboard(*, can_claim: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if can_claim:
        rows.append([InlineKeyboardButton(text="🎁 Забрать награду", callback_data="clanquest:claim")])
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="clanquest:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
