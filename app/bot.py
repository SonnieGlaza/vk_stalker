"""VK-бот «Зона: Сталкер» — диспетчер Long Poll.

Перенос Telegram-бота на VK: ядро (storage/game_logic/сетки/рендер) осталось
без изменений, слой отправки заменён на app.vk_platform.

Запуск:
    python run_vk.py
    (или) python -c "from app.bot import main; main()"
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.config import Settings, load_settings
from app.storage import NicknameTakenError, Storage
from app.vk_platform import (
    VkEvent,
    VkFSM,
    VkMessenger,
    inline_keyboard,
    reply_keyboard,
    run_longpoll,
    vk_plain,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Хранилище (один синглтон на процесс)
# ---------------------------------------------------------------------------

_storage: Storage | None = None


def get_storage(settings: Settings | None = None) -> Storage:
    global _storage
    if _storage is None:
        cfg = settings or load_settings()
        _storage = Storage(
            db_path=cfg.db_path,
            snapshot_path=cfg.snapshot_path,
            database_url=cfg.database_url,
        )
        # Создаём схему (SQLite/Postgres) при старте, чтобы сервер работал с готовой БД.
        _storage.init_db()
    return _storage


def _storage_reset() -> None:
    global _storage
    _storage = None


def _probe_db() -> None:
    """Живая проверка БД при старте: если хостинг-БД недоступна (Railway Postgres упал/
    перезапустился), видно сразу в логе — команды игроков будут молча падать (вебхук отвечает
    200, но обработчик кидает исключение и юзер не получает ответа)."""
    try:
        st = get_storage()
        st.get_meta("__startup_probe__")
        logger.info("Подключение к БД: OK (backend=%s)", getattr(st, "backend", "?"))
    except Exception:
        logger.exception("Подключение к БД: ОШИБКА — на сообщения не будет ответов, пока БД недоступна")


# ---------------------------------------------------------------------------
# Клавиатуры
# ---------------------------------------------------------------------------


def _menu_keyboard():
    return reply_keyboard(
        ["📟 КПК", "🎒 Инвентарь", "📡 Статус"],
        ["🏕 Вылазка", "🛒 Торговец", "👥 Группировка"],
        ["📋 Задания", "🪖 Рейды", "🏆 Рейтинг"],
        ["🏦 Барахолка", "🚚 Контрабанда", "🛰 События"],
        ["⚔️ Дуэль", "⚔️ Арена", "👥 Кооп"],
        ["📅 Ежедневка", "⭐ Пополнить", "🔔 Уведомления"],
        ["☠️ Смерти", "🔥 Как не сдохнуть", "ℹ️ Информация"],
    )


def _gendre_keyboard():
    return inline_keyboard(
        [("👨 Мужской", "reg:gender:male"), ("👩 Женский", "reg:gender:female")]
    )


def _faction_keyboard():
    return reply_keyboard(
        ["🏛 Долг", "🏔 Свобода"],
        ["🧍 Нейтралы", "🔫 Бандиты"],
    )


def _walk_keyboard():
    """Клавиатура вылазки: стрелки движения по сетке локации."""
    return inline_keyboard(*_walk_keyboard_rows())


# ---------------------------------------------------------------------------
# Регистрация и меню
# ---------------------------------------------------------------------------


async def cmd_start(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    player = storage.get_character(int(event.user_id), refresh_energy=True)
    if player is not None:
        await _menu(bot, storage, event)
        return
    await bot.send_message(
        event.peer_id,
        "🧬 Зона зовёт, сталкер!\nВведите ник персонажа:",
    )


async def _menu(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    await bot.send_message(event.peer_id, "Главное меню.", reply_markup=_menu_keyboard())


def _sortie_keyboard():
    """Клавиатура «🏕 Вылазка» — как sortie_keyboard в ТГ."""
    return reply_keyboard(
        ["⚔️ Война", "🗺 Переход"],
        ["⚔️ Арена", "🪖 Рейды"],
        ["📍 Локация"],
        ["⬅️ В меню"],
    )


async def _sortie_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    """Раздел «🏕 Вылазка» (как show_sortie в ТГ): война, переходы, арена, рейды."""
    player = storage.get_character(int(event.user_id), refresh_energy=True)
    if player is None:
        await bot.send_message(event.peer_id, "Сначала создай персонажа через «Старт».")
        return
    await bot.send_message(
        event.peer_id,
        "🏕 Вылазка\nВойна, переходы по Зоне, арена, рейды и кооп.",
        reply_markup=_sortie_keyboard(),
    )


def _pda_keyboard(*, is_leader: bool = False):
    """Клавиатура раздела КПК — как pda_keyboard в ТГ (reply-кнопки, без лимита 6 строк)."""
    rows: list[list[str]] = [
        ["🧾 Профиль", "💬 Чаты"],
        ["🏆 Рейтинг", "🗺 Карта", "📍 Локация"],
        ["👥 Игроки", "🤝 Обмен"],
        ["💎 Находки", "💎 Арты"],
        ["📊 Дроп", "☠️ Смерти"],
        ["📊 Статистика", "📅 Ежедневка"],
        ["🔔 Уведомления", "🔥 Как не сдохнуть"],
        ["🏛 Клановые задачи", "ℹ️ Информация"],
    ]
    if is_leader:
        rows[-1].append("📣 Сбор")
    rows.append(["🔗 Реферальная система"])
    rows.append(["⬅️ В меню"])
    return reply_keyboard(*[tuple(r) for r in rows])


def _pda_keyboard_for(storage: Storage, user_id: int, player: Any | None = None):
    if player is None:
        player = storage.get_character(int(user_id), refresh_energy=False)
    faction = getattr(player, "faction", None) if player is not None else None
    is_leader = bool(
        player is not None
        and faction
        and storage.get_faction_leader_id(faction) == int(user_id)
    )
    return _pda_keyboard(is_leader=is_leader)


def _profile_text(storage: Storage, player: Any) -> str:
    """Текстовый профиль: ник с титулом и медалями, ID, фракция, звание."""
    from app.game_logic import character_rank_title
    from app.player_medals import medals_nick_suffix
    from app.vk_titles import title_prefix

    user_id = int(player.telegram_id)
    nick = title_prefix(storage, user_id, player.nickname)
    medals = medals_nick_suffix(storage, user_id)
    faction = getattr(player, "faction", None) or "не выбрана"
    rank = character_rank_title(storage, player) or "—"
    return (
        f"Профиль сталкера {nick}{medals}\n"
        f"ID: STK-{user_id}\n"
        f"Фракция: {faction}\n"
        f"Звание: {rank}"
    )


async def _profile(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    """Раздел КПК «🧾 Профиль»: PNG-карточка (как в ТГ) + меню КПК."""
    player = storage.get_character(int(event.user_id), refresh_energy=True)
    if player is None:
        await bot.send_message(event.peer_id, "Сначала создай персонажа через «Старт».")
        return
    kb = _pda_keyboard_for(storage, int(event.user_id))
    text = _profile_text(storage, player)
    try:
        from app.profile_card import build_character_card

        rating = int((storage.get_player_stats(int(event.user_id)) or {}).get("rating_points", 0))
        # Рендер карточки — тяжёлый (Pillow, шрифты): в поток, чтобы не блокировать
        # ответы другим игрокам (пул БД thread-safe, storage в нём можно звать из потока).
        card_png = await asyncio.to_thread(
            build_character_card,
            player,
            rating_points=rating,
            storage=storage,
        )
        await bot.send_photo(
            event.peer_id,
            card_png,
            caption=text,
            reply_markup=kb,
            fallback_text=text,
        )
    except Exception as exc:
        logger.warning("profile card render failed: %s", exc)
        await bot.send_message(event.peer_id, text, reply_markup=kb)


async def _pda_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    """Раздел КПК (как в ТГ): заголовок + reply-меню разделов."""
    player = storage.get_character(int(event.user_id), refresh_energy=True)
    if player is None:
        await bot.send_message(event.peer_id, "Сначала создай персонажа через «Старт».")
        return
    await bot.send_message(
        event.peer_id,
        "📟 КПК сталкера\nПрофиль, связь, рейтинг, карта, журналы, артефакты, игроки и рефералка.",
        reply_markup=_pda_keyboard_for(storage, int(event.user_id), player=player),
    )


FACTION_CHATS = {
    "Нейтралы": "https://vk.me/join/JrBlhRBbIp1LtqzvHHhkLz0EFE2cEMtj6Jc=",
    "Бандиты": "https://vk.me/join/exRV3PXAkiB9zwYWkCRL71Cpgh_XKMkzBD0=",
    "Долг": "https://vk.me/join/e7Ofe/ROYEmeapHytOfwnZQjkeJie_RoQao=",
    "Свобода": "https://vk.me/join/2o5NwLRHRGk44bDfR/pfxGAciAJNsT_vuZk=",
}
COMMON_CHAT = "https://vk.me/join/Q9/ZhnAbkyCWEpCSNHU2MVLL0lnoLshrRcA="


async def _pda_chats(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    """Раздел КПК «💬 Чаты» — как «📟 КПК — связь» в ТГ: канал, общий чат, чат группировки."""
    player = storage.get_character(int(event.user_id), refresh_energy=True)
    if player is None:
        await bot.send_message(event.peer_id, "Сначала создай персонажа через «Старт».")
        return
    group_id = getattr(bot, "group_id", None)
    update_channel = f"https://vk.com/club{abs(int(group_id))}" if group_id else "— ссылка появится, когда сообщество откроет страницу."
    faction_chat = FACTION_CHATS.get(player.faction or "")
    lines = [
        "📟 КПК — связь",
        "",
        f"📢 Канал обновлений бота:\n{update_channel}",
        "",
        f"🌐 Общий чат Зоны:\n{COMMON_CHAT}",
    ]
    if faction_chat:
        lines.extend(
            [
                "",
                f"🛡️ Чат группировки «{player.faction}»:\n{faction_chat}",
            ]
        )
    elif player.faction == "Монолит":
        lines.extend(
            [
                "",
                "🛡️ «Монолит» — закрытая группировка: публичная ссылка на чат не публикуется.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "🛡️ Чат группировки появится после выбора группировки.",
            ]
        )
    await bot.send_message(
        int(event.peer_id),
        "\n".join(lines),
        reply_markup=_pda_keyboard_for(storage, int(event.user_id)),
    )


async def _pda_exchange(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    text = (
        "🤝 Обмен\n\n"
        "• Перевод RU: pay <ник|ид> <сумма> (комиссия 20%).\n"
        "• Вызвать на дуэль: «👥 Игроки» → «⚔️ Вызвать» или текстом: дуэль <ник|ид>."
    )
    await bot.send_message(
        int(event.peer_id),
        text,
        reply_markup=_pda_keyboard_for(storage, int(event.user_id)),
    )


async def _pda_deaths(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_death_log_text

    text = build_death_log_text(storage, int(event.user_id))
    await bot.send_message(
        int(event.peer_id),
        text,
        reply_markup=_pda_keyboard_for(storage, int(event.user_id)),
    )


async def _pda_referral(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    await _referral_info(bot, storage, event)


# ---------------------------------------------------------------------------
# КПК: разделы под карточкой (аналог ТГ-КПК: статистика, игроки, журналы, карта)
# ---------------------------------------------------------------------------


async def _pda_stats(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_player_stats_text

    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None:
        await bot.send_message(event.peer_id, "Сначала создай персонажа.")
        return
    text = build_player_stats_text(storage, user_id)
    await bot.send_message(event.peer_id, text, reply_markup=_pda_keyboard_for(storage, int(event.user_id)))


async def _pda_find(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_artifact_find_log_text

    text = build_artifact_find_log_text(storage, int(event.user_id))
    await bot.send_message(event.peer_id, text, reply_markup=_pda_keyboard_for(storage, int(event.user_id)))


async def _pda_arts(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.artifact_features import build_my_artifacts_text

    text = build_my_artifacts_text(storage, int(event.user_id))
    await bot.send_message(event.peer_id, text, reply_markup=_pda_keyboard_for(storage, int(event.user_id)))


async def _pda_drop(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.artifact_features import build_artifact_drop_table_text
    from app.game_logic import best_detector_base_chance

    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None:
        await bot.send_message(event.peer_id, "Сначала создай персонажа.")
        return
    text = build_artifact_drop_table_text(
        storage,
        str(player.location),
        best_detector_base_chance(player) or 17,
    )
    await bot.send_message(event.peer_id, text, reply_markup=_pda_keyboard_for(storage, int(event.user_id)))


async def _pda_players(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_players_root_text

    text, items = build_players_root_text(storage)
    rows: list[list] = []
    for faction_key, title, count in items:
        rows.append([(f"{title} ({count})", f"players:f:{faction_key}:0")])
    if not rows:
        rows.append([("Игроков нет", "pda:players")])
    await bot.send_message(int(event.peer_id), text, reply_markup=inline_keyboard(*rows))


async def _pda_players_faction(
    bot: VkMessenger, storage: Storage, event: VkEvent, faction_key: str, page: int = 0
) -> None:
    from app.game_logic import build_players_faction_page_text
    from app.vk_titles import title_prefix

    user_id = int(event.user_id)
    text, safe_key, safe_page, total_pages, page_players = build_players_faction_page_text(
        storage, faction_key, page
    )
    rows: list[list] = []
    for row in page_players or []:
        tid = int(row.get("telegram_id") or 0)
        if not tid or tid == user_id:
            continue
        nick = str(row.get("nickname") or f"id{tid}")
        rows.append([(f"⚔️ Вызвать на дуэль: {title_prefix(storage, tid, nick)}", f"duel:start:{tid}")])
    nav: list[tuple[str, str]] = []
    if safe_page > 0:
        nav.append(("⬅️", f"players:f:{safe_key}:{safe_page - 1}"))
    nav.append((f"{safe_page + 1}/{total_pages}", f"players:f:{safe_key}:{safe_page}"))
    if safe_page < total_pages - 1:
        nav.append(("➡️", f"players:f:{safe_key}:{safe_page + 1}"))
    rows.append(nav)
    rows.append([("⬅️ К группировкам", "pda:players")])
    await bot.send_message(int(event.peer_id), text, reply_markup=inline_keyboard(*rows))


async def _pda_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "menu":
        await _pda_open(bot, storage, event)
    elif cmd == "stats":
        await _pda_stats(bot, storage, event)
    elif cmd == "players":
        await _pda_players(bot, storage, event)
    elif cmd == "find":
        await _pda_find(bot, storage, event)
    elif cmd == "arts":
        await _pda_arts(bot, storage, event)
    elif cmd == "drop":
        await _pda_drop(bot, storage, event)
    elif cmd == "deaths":
        await _pda_deaths(bot, storage, event)
    elif cmd == "chats":
        await _pda_chats(bot, storage, event)
    elif cmd == "exchange":
        await _pda_exchange(bot, storage, event)
    elif cmd == "referral":
        await _pda_referral(bot, storage, event)
    elif cmd == "daily":
        await _daily_open(bot, storage, event)
    elif cmd == "notify":
        await _notify_open(bot, storage, event)
    elif cmd == "info":
        await _cmd_help(bot, storage, event)


async def _players_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    # players:root | players:f:<faction_key>:<page>
    if payload == "players:root":
        await _pda_players(bot, storage, event)
        return
    parts = payload.split(":")
    if len(parts) >= 4 and parts[1] == "f":
        page = int(parts[-1]) if parts[-1].isdigit() else 0
        await _pda_players_faction(bot, storage, event, parts[2], page)
        return
    await _pda_players(bot, storage, event)


async def _status(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=True)
    if player is None:
        await bot.send_message(event.peer_id, "Сначала создай персонажа.")
        return
    try:
        from app.game_logic import build_quick_status_text

        text = build_quick_status_text(storage, user_id)
    except Exception as exc:
        logger.warning("status render failed (%s): %s", user_id, exc, exc_info=True)
        text = f"📡 {player.nickname}\nHP {player.health}/100\nЛокация: {player.location}"
    await bot.send_message(event.peer_id, text, reply_markup=_menu_keyboard())


async def _inventory(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    player = storage.get_character(int(event.user_id), refresh_energy=True)
    if player is None:
        await bot.send_message(event.peer_id, "Сначала создай персонажа.")
        return
    from app.game_logic import format_inventory

    text = format_inventory(
        player,
        rating_points=int(storage.get_player_stats(int(event.user_id)).get("rating_points", 0)),
        storage=storage,
    )
    rows: list[list] = [
        [("🧰 Расходники", "inventory:consumables")],
        [("🗄 Хабар", "stash:menu")],
        [("🛒 Купить тайник (от 5000)", "trade:buyitem:stash_case")],
        [("📦 Открыть тайник", "inventory:use:stash_case")],
        [("⚙️ Экипировка", "trade:equip")],
    ]
    await bot.send_message(event.peer_id, text, reply_markup=inline_keyboard(*rows))


# ---------------------------------------------------------------------------
# Вылазка (сетка локации, app.location_walk)
# ---------------------------------------------------------------------------


async def _walk_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    player = storage.get_character(int(event.user_id), refresh_energy=True)
    if player is None:
        await bot.send_message(event.peer_id, "Сначала создай персонажа.")
        return
    if await _blockpost_gate(bot, storage, event):
        return
    await _walk_render(bot, storage, event, player)


def _walk_zone_action_rows(storage: Storage, player: Any) -> list[list]:
    """Кнопки действий зоны под игроком (база/аномалия/домик/лаборатория/барахолка)."""
    try:
        from app.location_walk import get_walk_position as _gp, zone_at

        x, y = _gp(storage, int(player.telegram_id))
        zone = zone_at(str(player.location), x, y, faction=player.faction, storage=storage)
    except Exception:
        zone = None
    kind = str((zone or {}).get("kind") or "")
    zone_id = str((zone or {}).get("id") or "")
    if kind == "base":
        return [
            [("🛒 Торговец", "locmap:vendor"), ("📋 Задания", "locmap:quests")],
            [("🎒 Пополнить снаряжение", "locmap:resupply")],
        ]
    if kind in ("bazaar", "trade"):
        return [[("🏦 Барахолка", "locmap:bazaar"), ("🛒 Торговец", "locmap:vendor")]]
    if kind == "anomaly":
        return [[("☢ Поиск артов", "locmap:anomaly")]]
    if kind == "search":
        label = str((zone or {}).get("label") or "хабара")
        return [[(f"🏚 Поиск хабара: {label}", f"locmap:search:{zone_id}")]]
    if kind == "lab":
        label = str((zone or {}).get("label") or "лаборатория")
        return [[(f"🧪 {label}", f"locmap:lab:{zone_id}")]]
    if kind == "secrettrader":
        return [[("🕵 Бунс (тайный торговец)", "locmap:secrettrader")]]
    if kind == "forester":
        return [[("🪵 Лесник: трофеи ×3", "locmap:forester")]]
    if kind == "lair":
        return [[("👹 Зачистка логова", "locmap:lair")]]
    return []


async def _walk_render(bot: VkMessenger, storage: Storage, event: VkEvent, player: Any) -> None:
    try:
        from app.location_walk import get_walk_position as _gp, render_walk_frame, zone_at

        frame = render_walk_frame(storage, player)
        x, y = _gp(storage, int(event.user_id))
        zone = zone_at(str(player.location), x, y, faction=player.faction, storage=storage)
        from app.game_logic import FACTION_HOME_BASE
        from app.secret_trader import SECRET_TRADER_LOCATION

        lines = [f"📍 Локация: {player.location}"]
        faction = getattr(player, "faction", None) or ""
        home = FACTION_HOME_BASE.get(faction) if faction else None
        if home and str(player.location) == home:
            lines.append("Здесь расположена база твоей группировки — торговец и 🎒 пополнение снаряжения.")
        else:
            for _owner, _base in FACTION_HOME_BASE.items():
                if str(player.location) == _base and _owner != faction:
                    lines.append(
                        f"⛔ Это база группировки «{_owner}» — чужим вход запрещён, пополнение недоступно."
                    )
        if str(player.location) == SECRET_TRADER_LOCATION:
            lines.append("Здесь орудует Бунс — тайный торговец, скупает информацию")
        caption = "\n".join(lines) + f"\n\n🗺 Ты на клетке X {x + 1} · Y {y + 1}"
        if zone:
            caption += f"\n📍 Ты в зоне: {zone.get('label') or zone.get('id')}"
        rows: list[list] = []
        zone_actions = _walk_zone_action_rows(storage, player)
        if zone_actions:
            rows.extend(zone_actions)
        fallback_text = (
            f"📍 Локация: {player.location}\n\n"
            "Картинка временно не загрузилась — стрелки ходят по локации, кнопки зоны работают."
        )
        await bot.send_photo(
            event.peer_id,
            frame,
            caption=caption,
            reply_markup=inline_keyboard(*(rows + _walk_keyboard_rows())),
            fallback_text=fallback_text,
        )
    except Exception as exc:
        logger.warning("walk render failed: %s", exc, exc_info=True)
        await bot.send_message(
            event.peer_id,
            f"🌲 {getattr(player, 'location', '?')} — карта локации временно недоступна.\n"
            "Пока можно пользоваться переходами, поиском артов и схроном:",
            reply_markup=inline_keyboard(
                [("🗺 Переходы", "travel:open"), ("📡 Поиск артов", "hunt:open")],
                [("🏚 Схрон", "sthunt:open"), ("⬅️ В меню", "walk:exit")],
            ),
        )


def _walk_keyboard_rows() -> list[list]:
    """Строки клавиатуры прогулки — как в ТГ: только стрелки (действия зоны добавляются отдельно)."""
    return [
        [("⬆️ Вперёд", "walk:up")],
        [("⬅️ Влево", "walk:left"), ("⬇️ Назад", "walk:down"), ("➡️ Вправо", "walk:right")],
    ]


async def _walk_move(bot: VkMessenger, storage: Storage, event: VkEvent, direction: str) -> None:
    from app.location_walk import move_walk_position

    player = storage.get_character(int(event.user_id), refresh_energy=True)
    if player is None:
        return
    move_walk_position(storage, int(event.user_id), direction)
    await _walk_render(bot, storage, event, player)


async def _walk_inspect(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.location_walk import get_walk_position as _gp, zone_at

    player = storage.get_character(int(event.user_id), refresh_energy=True)
    if player is None:
        return
    x, y = _gp(storage, int(event.user_id))
    zone = zone_at(player.location, x, y)
    label = zone.get("label", "пустое место") if zone else "пустое место"
    await bot.send_message(event.peer_id, f"🔍 Под тобой: {label}", reply_markup=_walk_keyboard())


def _walk_position(storage: Storage, user_id: int):
    from app.location_walk import get_walk_position as _gp

    return _gp(storage, user_id)


# ---------------------------------------------------------------------------
# Торговец (app/game_logic: buy/sell/repair/equip)
# ---------------------------------------------------------------------------

_TRADE_CATEGORIES = ("consumables", "trophies", "gear", "armor", "weapons")


def _trade_cat_label(category: str) -> str:
    labels = {
        "consumables": "🍽 Расходники",
        "trophies": "🦴 Трофеи",
        "gear": "🎒 Снаряга",
        "armor": "🦺 Броня",
        "weapons": "🔫 Оружие",
    }
    return labels.get(category, category)


def _shop_title(key: str) -> str:
    from app.game_logic import SHOP_ITEMS

    item = SHOP_ITEMS.get(key)
    if item and item.get("name"):
        return str(item["name"])
    return key


def _shop_buy_price(key: str) -> int:
    from app.game_logic import SHOP_ITEMS

    item = SHOP_ITEMS.get(key) or {}
    return int(item.get("buy_price", 0))


def _shop_sell_price(key: str) -> int:
    from app.game_logic import SHOP_ITEMS

    item = SHOP_ITEMS.get(key) or {}
    return int(item.get("sell_price", 0))


async def _trader_menu(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    """Корневое меню торговца (reply-клавиатура)."""
    keyboard = reply_keyboard(
        ["🛒 Купить", "💰 Продать"],
        ["🔧 Ремонт", "⚙️ Экипировка"],
        ["🛠 Техник", "🧑‍⚕️ Медик"],
        ["⬅️ В меню"],
    )
    await bot.send_message(event.peer_id, "🛒 Торговец. Что делаем?", reply_markup=keyboard)


async def _trader_buy(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    """Выбор категории товаров на покупку."""
    rows = [(_trade_cat_label(cat), f"trade:buycat:{cat}") for cat in _TRADE_CATEGORIES]
    rows.append(("⬅️ Назад", "trade:root"))
    await bot.send_message(event.peer_id, "🛒 Что покупаем?", reply_markup=inline_keyboard(*[[r] for r in rows]))


async def _trader_buy_category(bot: VkMessenger, storage: Storage, event: VkEvent, category: str) -> None:
    from app.game_logic import TRADER_SELL_CATALOG

    keys = list(TRADER_SELL_CATALOG.get(category, []))
    rows = []
    for key in keys[:18]:
        price = _shop_buy_price(key)
        label = f"{_shop_title(key)} — {price} RU"
        rows.append([(label, f"trade:buyitem:{key}")])
    rows.append([("⬅️ Назад", "trade:buy")])
    await bot.send_message(event.peer_id, f"🛒 {_trade_cat_label(category)}:", reply_markup=inline_keyboard(*rows))


async def _trader_buy_item(bot: VkMessenger, storage: Storage, event: VkEvent, key: str) -> None:
    from app.game_logic import buy_item

    result = buy_item(storage, int(event.user_id), key, amount=1)
    await bot.send_message(event.peer_id, result.text, reply_markup=inline_keyboard([("⬅️ Назад", "trade:buy")]))


async def _trader_sell(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    rows = [(_trade_cat_label(cat), f"trade:sellcat:{cat}") for cat in _TRADE_CATEGORIES]
    rows.append(("⬅️ Назад", "trade:root"))
    await bot.send_message(event.peer_id, "💰 Что продаём?", reply_markup=inline_keyboard(*[[r] for r in rows]))


async def _trader_sell_category(bot: VkMessenger, storage: Storage, event: VkEvent, category: str) -> None:
    from app.game_logic import TRADER_SELL_CATALOG, sell_item

    player = storage.get_character(int(event.user_id), refresh_energy=False)
    keys = list(TRADER_SELL_CATALOG.get(category, []))
    rows = []
    if player is not None:
        for key in keys:
            amount = int((player.inventory or {}).get(key, 0))
            if amount > 0:
                price = _shop_sell_price(key)
                label = f"Продать {_shop_title(key)} ×{amount} ({price})"
                rows.append([(label, f"trade:sellitem:{key}")])
    if not rows:
        await bot.send_message(event.peer_id, "В этой категории продавать нечего.", reply_markup=inline_keyboard([("⬅️ Назад", "trade:sell")]))
        return
    rows.append([("⬅️ Назад", "trade:sell")])
    await bot.send_message(event.peer_id, f"💰 {_trade_cat_label(category)}:", reply_markup=inline_keyboard(*rows))


async def _trader_sell_item(bot: VkMessenger, storage: Storage, event: VkEvent, key: str) -> None:
    from app.game_logic import sell_item

    result = sell_item(storage, int(event.user_id), key)
    await bot.send_message(event.peer_id, result.text, reply_markup=inline_keyboard([("⬅️ Назад", "trade:sell")]))


async def _trader_repair(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    keyboard = inline_keyboard(
        [("🔫 Оружие", "trade:repair:weapon"), ("🦺 Броня", "trade:repair:armor")],
        [("🚛 Грузовик", "trade:repair:truck"), ("🚙 Нива", "trade:repair:niva")],
        [("⬅️ Назад", "trade:root")],
    )
    await bot.send_message(event.peer_id, "🔧 Что чиним?", reply_markup=keyboard)


async def _trader_repair_target(bot: VkMessenger, storage: Storage, event: VkEvent, target: str) -> None:
    from app.game_logic import repair_gear, repair_niva, repair_truck

    if target == "truck":
        result = repair_truck(storage, int(event.user_id))
    elif target == "niva":
        result = repair_niva(storage, int(event.user_id))
    else:
        result = repair_gear(storage, int(event.user_id), target)
    await bot.send_message(event.peer_id, result.text, reply_markup=inline_keyboard([("⬅️ Назад", "trade:repair")]))


async def _trader_equip(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import has_any_equipped_artifact

    player = storage.get_character(int(event.user_id), refresh_energy=False)
    rows: list[list] = [
        [("🔫 Выбрать оружие", "trade:equiplist:weapon"), ("🦺 Выбрать броню", "trade:equiplist:armor")],
        [("💎 Выбрать артефакт", "trade:equiplist:artifact")],
    ]
    if player is not None and has_any_equipped_artifact(player):
        rows.append([("⛔ Снять артефакт", "trade:uneqart")])
    rows.append([("⬅️ Назад", "trade:root")])
    await bot.send_message(event.peer_id, "⚙️ Экипировка:", reply_markup=inline_keyboard(*rows))


async def _trader_equip_list(bot: VkMessenger, storage: Storage, event: VkEvent, slot: str) -> None:
    from app.game_logic import list_equippable_armor, list_equippable_artifacts, list_equippable_weapons

    player = storage.get_character(int(event.user_id), refresh_energy=False)
    if player is None:
        return
    if slot == "weapon":
        options = list_equippable_weapons(player)
    elif slot == "artifact":
        options = list_equippable_artifacts(player)
    else:
        options = list_equippable_armor(player)
    rows = [[(f"{title} ×{amount}", f"trade:eqitem:{slot}:{key}")] for key, title, amount in options[:18]]
    if not rows:
        await bot.send_message(event.peer_id, "Экипировать нечего (купи сначала).", reply_markup=inline_keyboard([("⬅️ Назад", "trade:equip")]))
        return
    rows.append([("⬅️ Назад", "trade:equip")])
    label = {"weapon": "оружие", "armor": "броня", "artifact": "артефакт"}.get(slot, slot)
    await bot.send_message(event.peer_id, f"⚙️ Твои {label}:", reply_markup=inline_keyboard(*rows))


async def _trader_equip_item(bot: VkMessenger, storage: Storage, event: VkEvent, slot: str, key: str) -> None:
    from app.game_logic import equip_armor, equip_artifact, equip_weapon

    if slot == "weapon":
        result = equip_weapon(storage, int(event.user_id), key)
    elif slot == "artifact":
        result = equip_artifact(storage, int(event.user_id), key)
    else:
        result = equip_armor(storage, int(event.user_id), key)
    await bot.send_message(event.peer_id, result.text, reply_markup=inline_keyboard([("⬅️ Назад", "trade:equip")]))


async def _trader_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    if len(parts) < 2:
        await _trader_menu(bot, storage, event)
        return
    cmd = parts[1]
    if cmd == "root":
        await _trader_menu(bot, storage, event)
    elif cmd == "buy":
        await _trader_buy(bot, storage, event)
    elif cmd == "buycat":
        await _trader_buy_category(bot, storage, event, parts[2])
    elif cmd == "buyitem":
        await _trader_buy_item(bot, storage, event, parts[2])
    elif cmd == "sell":
        await _trader_sell(bot, storage, event)
    elif cmd == "sellcat":
        await _trader_sell_category(bot, storage, event, parts[2])
    elif cmd == "sellitem":
        await _trader_sell_item(bot, storage, event, parts[2])
    elif cmd == "repair":
        if len(parts) >= 3 and parts[2]:
            await _trader_repair_target(bot, storage, event, parts[2])
        else:
            await _trader_repair(bot, storage, event)
    elif cmd == "equip":
        await _trader_equip(bot, storage, event)
    elif cmd == "equiplist":
        await _trader_equip_list(bot, storage, event, parts[2])
    elif cmd == "eqitem":
        await _trader_equip_item(bot, storage, event, parts[2], parts[3])
    elif cmd == "uneqart":
        from app.game_logic import unequip_artifact

        result = unequip_artifact(storage, int(event.user_id))
        await bot.send_message(int(event.peer_id), result.text, reply_markup=inline_keyboard([("⬅️ Назад", "trade:equip")]))
    elif cmd == "tech":
        await _tech_menu(bot, storage, event)
    elif cmd in ("techbuy",):
        await _tech_buy(bot, storage, event, parts[2])
    elif cmd == "medic":
        await _medic_menu(bot, storage, event)
    elif cmd == "medicheal":
        from app.vendors import medic_heal

        result = medic_heal(storage, int(event.user_id))
        await bot.send_message(int(event.peer_id), result.text, reply_markup=inline_keyboard([("⬅️ Назад", "trade:medic")]))
    else:
        await _trader_menu(bot, storage, event)


# ---------------------------------------------------------------------------
# Дуэли (app/duel_grid — тактическая сетка)
# ---------------------------------------------------------------------------


def _duel_fight_keyboard():
    """Клавиатура боя: движение, стрельба, аптечка, сдача."""
    return inline_keyboard(
        [("⬆️", "duel:move:up")],
        [("⬅️", "duel:move:left"), ("⬇️", "duel:move:down"), ("➡️", "duel:move:right")],
        [("🔫 ⬆️", "duel:shoot:up")],
        [("🔫 ⬅️", "duel:shoot:left"), ("🔫 ⬇️", "duel:shoot:down"), ("🔫 ➡️", "duel:shoot:right")],
        [("💊 Аптечка", "duel:medkit"), ("🏳 Сдаться", "duel:forfeit")],
        [("🔄 Обновить", "duel:refresh"), ("⬅️ В меню", "duel:exit")],
    )


def _duel_caption(storage: Storage, session: Any) -> str:
    """Чей ход и HP участников."""
    order = list(session.turn_order or [])
    idx = int(getattr(session, "active_index", 0) or 0)
    active_id = order[idx] if order and idx < len(order) else None
    active_name = "?"
    if active_id is not None:
        ch = storage.get_character(int(active_id), refresh_energy=False)
        active_name = ch.nickname if ch is not None else f"id{active_id}"
    hp = dict(session.hp or {})

    def _hp_label(pid: int) -> str:
        ch = storage.get_character(int(pid), refresh_energy=False)
        name = ch.nickname if ch is not None else f"id{pid}"
        value = hp.get(str(pid), 0)
        return f"{name}: {value} HP"

    lines = ["⚔️ Тактическая дуэль", "", "Ход: " + active_name]
    for pid in order:
        lines.append(_hp_label(int(pid)))
    return "\n".join(lines)


async def _duel_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    """Список игроков для вызова."""
    me = int(event.user_id)
    players = storage.list_players(limit=50)
    rows = []
    for row in players:
        pid = int(row.get("telegram_id") or 0)
        if pid == me:
            continue
        from app.vk_titles import title_prefix

        nick = title_prefix(storage, pid, str(row.get("nickname") or f"id{pid}"))
        rows.append([(f"⚔️ {nick}", f"duel:start:{pid}")])
    if not rows:
        await bot.send_message(event.peer_id, "Пока некого вызвать — нет других игроков.")
        return
    await bot.send_message(
        event.peer_id,
        "Выбери соперника:",
        reply_markup=inline_keyboard(*rows),
    )


async def _duel_start(bot: VkMessenger, storage: Storage, event: VkEvent, target_id: int) -> None:
    from app.duel_grid import start_duel_grid

    me = int(event.user_id)
    if me == int(target_id):
        await bot.send_message(event.peer_id, "Нельзя вызвать сам себя.")
        return
    result, session = start_duel_grid(storage, me, int(target_id))
    if not result.ok or session is None:
        await bot.send_message(event.peer_id, result.text)
        return
    await _duel_send_frames(bot, storage, session, me, int(target_id))
    await bot.send_message(event.peer_id, result.text)


async def _duel_send_frames(bot: VkMessenger, storage: Storage, session: Any, a_id: int, b_id: int) -> None:
    from app.duel_grid import render_duel_frame

    caption = _duel_caption(storage, session)
    kb = _duel_fight_keyboard()
    try:
        png_a = render_duel_frame(storage, session, a_id)
        await bot.send_photo(a_id, png_a, caption=caption, reply_markup=kb, fallback_text=caption)
    except Exception as exc:
        logger.warning("duel frame a failed: %s", exc)
        await bot.send_message(a_id, caption, reply_markup=kb)
    try:
        png_b = render_duel_frame(storage, session, b_id)
        await bot.send_photo(b_id, png_b, caption=caption, reply_markup=kb, fallback_text=caption)
    except Exception as exc:
        logger.warning("duel frame b failed: %s", exc)
        await bot.send_message(b_id, caption, reply_markup=kb)


async def _duel_apply(bot: VkMessenger, storage: Storage, event: VkEvent, action: str) -> None:
    """Применить ход и перерисовать кадр обоим, если дуэль продолжается."""
    from app.duel_grid import (
        duel_forfeit,
        duel_move,
        duel_shoot,
        duel_use_medkit,
        get_duel_session_by_player,
    )

    me = int(event.user_id)
    if action == "move":
        direction = event.payload.split(":")[-1]
        result = duel_move(storage, me, direction)
    elif action == "shoot":
        direction = event.payload.split(":")[-1]
        result = duel_shoot(storage, me, direction)
    elif action == "medkit":
        result = duel_use_medkit(storage, me)
    elif action == "forfeit":
        result = duel_forfeit(storage, me)
    else:
        result = None
    if result is not None:
        await bot.send_message(event.peer_id, result.text)

    session = get_duel_session_by_player(storage, me)
    if session is None:
        await _menu(bot, storage, event)
        await _maybe_respawn_dead(bot, storage, event)
        return
    await _duel_send_frames(bot, storage, session, int(session.challenger_id), int(session.target_id))


async def _duel_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "start":
        target = parts[2] if len(parts) > 2 else "0"
        await _duel_start(bot, storage, event, int(target))
    elif cmd == "move":
        await _duel_apply(bot, storage, event, "move")
    elif cmd == "shoot":
        await _duel_apply(bot, storage, event, "shoot")
    elif cmd == "medkit":
        await _duel_apply(bot, storage, event, "medkit")
    elif cmd == "forfeit":
        await _duel_apply(bot, storage, event, "forfeit")
    elif cmd == "refresh":
        session = None
        from app.duel_grid import get_duel_session_by_player

        session = get_duel_session_by_player(storage, int(event.user_id))
        if session is not None:
            await _duel_send_frames(
                bot,
                storage,
                session,
                int(session.challenger_id),
                int(session.target_id),
            )
        else:
            await _menu(bot, storage, event)
    elif cmd == "exit":
        await _menu(bot, storage, event)


# ---------------------------------------------------------------------------
# Арена (app/arena_grid — волны НПС)
# ---------------------------------------------------------------------------


def _arena_keyboard():
    return inline_keyboard(
        [("⬆️", "arena:move:up")],
        [("⬅️", "arena:move:left"), ("⬇️", "arena:move:down"), ("➡️", "arena:move:right")],
        [("🔫 ⬆️", "arena:shoot:up")],
        [("🔫 ⬅️", "arena:shoot:left"), ("🔫 ⬇️", "arena:shoot:down"), ("🔫 ➡️", "arena:shoot:right")],
        [("💊 Аптечка", "arena:medkit"), ("🏳 Уйти", "arena:forfeit")],
        [("🔄 Обновить", "arena:refresh")],
    )


async def _arena_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.arena_grid import get_arena_session, start_arena

    result = start_arena(storage, int(event.user_id))
    if not result.ok:
        await bot.send_message(event.peer_id, result.text)
        return
    sess = get_arena_session(storage, int(event.user_id))
    if sess is None:
        await bot.send_message(event.peer_id, result.text)
        return
    await _arena_send_frame(bot, storage, event, sess)


async def _arena_send_frame(bot: VkMessenger, storage: Storage, event: VkEvent, sess: Any) -> None:
    from app.arena_grid import arena_status_caption, render_arena_frame

    caption = arena_status_caption(sess)
    kb = _arena_keyboard()
    try:
        png = render_arena_frame(storage, sess)
        await bot.send_photo(int(event.peer_id), png, caption=caption, reply_markup=kb, fallback_text=caption)
    except Exception as exc:
        logger.warning("arena frame failed: %s", exc)
        await bot.send_message(int(event.peer_id), caption, reply_markup=kb)


async def _arena_apply(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.arena_grid import (
        arena_forfeit,
        arena_move,
        arena_shoot,
        arena_use_medkit,
        get_arena_session,
    )

    payload = (event.payload or "").strip()
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "move":
        result = arena_move(storage, int(event.user_id), parts[2])
    elif cmd == "shoot":
        result = arena_shoot(storage, int(event.user_id), parts[2])
    elif cmd == "medkit":
        result = arena_use_medkit(storage, int(event.user_id))
    elif cmd == "forfeit":
        result = arena_forfeit(storage, int(event.user_id))
    else:
        result = None
    if result is not None:
        await bot.send_message(int(event.peer_id), result.text)
    sess = get_arena_session(storage, int(event.user_id))
    if sess is None:
        await bot.send_message(
            int(event.peer_id),
            "🏟 Арена завершена.",
            reply_markup=_menu_keyboard(),
        )
        await _maybe_respawn_dead(bot, storage, event)
        return
    await _arena_send_frame(bot, storage, event, sess)


async def _arena_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    from app.arena_grid import get_arena_session

    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "refresh":
        sess = get_arena_session(storage, int(event.user_id))
        if sess is not None:
            await _arena_send_frame(bot, storage, event, sess)
        else:
            await bot.send_message(int(event.peer_id), "Арена не активна.", reply_markup=_menu_keyboard())
    else:
        await _arena_apply(bot, storage, event)


# ---------------------------------------------------------------------------
# Кооп-миссии (app/coop_mission — общая вылазка до 4 игроков)
# ---------------------------------------------------------------------------


def _coop_menu_keyboard():
    return reply_keyboard(
        ["🧪 Создать лобби", "🔎 Открытые лобби"],
        ["⬅️ В меню"],
    )


def _coop_mission_keyboard():
    from app.coop_mission import COOP_MISSION_TYPES

    rows = []
    for key in COOP_MISSION_TYPES:
        rows.append((COOP_MISSION_TYPES[key].get("title", key), f"coop:mission:{key}"))
    return inline_keyboard(*[[row] for row in rows], [("⬅️ Назад", "coop:menu")])


def _coop_fight_keyboard():
    return inline_keyboard(
        [("⬆️", "coop:move:up")],
        [("⬅️", "coop:move:left"), ("⬇️", "coop:move:down"), ("➡️", "coop:move:right")],
        [("🔫 ⬆️", "coop:shoot:up")],
        [("🔫 ⬅️", "coop:shoot:left"), ("🔫 ⬇️", "coop:shoot:down"), ("🔫 ➡️", "coop:shoot:right")],
        [("💊 Аптечка", "coop:medkit"), ("🚁 Эвакуация", "coop:evac")],
        [("🏳 Завершить", "coop:forfeit"), ("🔄 Обновить", "coop:refresh")],
    )


async def _coop_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.coop_mission import get_coop_lobby_by_player

    lobby = get_coop_lobby_by_player(storage, int(event.user_id))
    if lobby is not None:
        text = f"👥 Ты уже в группе #{lobby.lobby_id} ({lobby.location})."
        if lobby.host_id == int(event.user_id):
            text += "\nВыбери миссию или стартуй:"
            await bot.send_message(
                int(event.peer_id),
                text,
                reply_markup=inline_keyboard(
                    [("🎯 Выбор миссии", "coop:missions"), ("🚀 Старт", "coop:start")],
                    [("🚪 Покинуть", "coop:leave")],
                ),
            )
        else:
            await bot.send_message(int(event.peer_id), text, reply_markup=_coop_menu_keyboard())
        return
    await bot.send_message(int(event.peer_id), "👥 Кооп-вылазка:", reply_markup=_coop_menu_keyboard())


async def _coop_create(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.coop_mission import create_coop_lobby

    result = create_coop_lobby(storage, int(event.user_id))
    if not result.ok:
        await bot.send_message(int(event.peer_id), result.text)
        return
    await bot.send_message(
        int(event.peer_id),
        result.text,
        reply_markup=inline_keyboard(
            [("🎯 Выбор миссии", "coop:missions"), ("🚀 Старт", "coop:start")],
            [("🚪 Покинуть", "coop:leave")],
        ),
    )


async def _coop_missions(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    await bot.send_message(int(event.peer_id), "🎯 Миссия группы:", reply_markup=_coop_mission_keyboard())


async def _coop_set_mission(bot: VkMessenger, storage: Storage, event: VkEvent, kind: str) -> None:
    from app.coop_mission import set_coop_lobby_mission

    result = set_coop_lobby_mission(storage, int(event.user_id), kind)
    await bot.send_message(int(event.peer_id), result.text, reply_markup=_coop_menu_keyboard())


async def _coop_list(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.coop_mission import get_coop_lobby_by_player, list_open_coop_lobbies

    player = storage.get_character(int(event.user_id), refresh_energy=False)
    if player is None:
        return
    lobbies = list_open_coop_lobbies(storage, player.location)
    if not lobbies:
        await bot.send_message(
            int(event.peer_id),
            "🔎 Открытых групп на твоей локации нет.",
            reply_markup=_coop_menu_keyboard(),
        )
        return
    rows = []
    for lobby_id, host_nick, count in lobbies[:15]:
        rows.append([(f"{host_nick} ({count}/4)", f"coop:join:{lobby_id}")])
    await bot.send_message(int(event.peer_id), "🔎 Выбери группу:", reply_markup=inline_keyboard(*rows))


async def _coop_join(bot: VkMessenger, storage: Storage, event: VkEvent, lobby_id: str) -> None:
    from app.coop_mission import join_coop_lobby

    result = join_coop_lobby(storage, int(event.user_id), lobby_id)
    await bot.send_message(int(event.peer_id), result.text, reply_markup=_coop_menu_keyboard())


async def _coop_leave(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.coop_mission import leave_coop_lobby

    result = leave_coop_lobby(storage, int(event.user_id))
    await bot.send_message(int(event.peer_id), result.text, reply_markup=_coop_menu_keyboard())


async def _coop_start(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.coop_mission import get_coop_session_by_player, start_coop_mission

    result = start_coop_mission(storage, int(event.user_id))
    if not result.ok:
        await bot.send_message(int(event.peer_id), result.text)
        return
    sess = get_coop_session_by_player(storage, int(event.user_id))
    if sess is None:
        await bot.send_message(int(event.peer_id), result.text)
        return
    await _coop_send_frames(bot, storage, event, sess)


async def _coop_send_frames(bot: VkMessenger, storage: Storage, event: VkEvent, sess: Any) -> None:
    from app.coop_mission import coop_status_caption, render_coop_frame

    player_ids = list(getattr(sess, "player_ids", []) or [])
    for pid in player_ids:
        try:
            caption = coop_status_caption(sess, storage, int(pid))
        except Exception:
            caption = "👥 Кооп"
        png = None
        try:
            png = render_coop_frame(storage, sess, int(pid))
        except Exception:
            png = None
        if png:
            await bot.send_photo(int(pid), png, caption=caption, reply_markup=_coop_fight_keyboard(), fallback_text=caption)
        else:
            await bot.send_message(int(pid), caption, reply_markup=_coop_fight_keyboard())


async def _coop_apply(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.coop_mission import (
        coop_evacuate,
        coop_forfeit,
        coop_move,
        coop_shoot,
        coop_use_medkit,
        get_coop_session_by_player,
    )

    payload = (event.payload or "").strip()
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "move":
        result = coop_move(storage, int(event.user_id), parts[2])
    elif cmd == "shoot":
        result = coop_shoot(storage, int(event.user_id), parts[2])
    elif cmd == "medkit":
        result = coop_use_medkit(storage, int(event.user_id))
    elif cmd == "evac":
        result = coop_evacuate(storage, int(event.user_id))
    elif cmd == "forfeit":
        result = coop_forfeit(storage, int(event.user_id))
    else:
        result = None
    if result is not None:
        await bot.send_message(int(event.peer_id), result.text)
    sess = get_coop_session_by_player(storage, int(event.user_id))
    if sess is None:
        await bot.send_message(int(event.peer_id), "Вылазка завершена.", reply_markup=_menu_keyboard())
        return
    if getattr(sess, "finished", False):
        await bot.send_message(int(event.peer_id), "Вылазка завершена.", reply_markup=_menu_keyboard())
        return
    await _coop_send_frames(bot, storage, event, sess)


async def _coop_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "menu":
        await _coop_open(bot, storage, event)
    elif cmd == "create":
        await _coop_create(bot, storage, event)
    elif cmd == "list":
        await _coop_list(bot, storage, event)
    elif cmd == "join":
        await _coop_join(bot, storage, event, parts[2])
    elif cmd == "leave":
        await _coop_leave(bot, storage, event)
    elif cmd == "missions":
        await _coop_missions(bot, storage, event)
    elif cmd == "mission":
        await _coop_set_mission(bot, storage, event, parts[2])
    elif cmd == "start":
        await _coop_start(bot, storage, event)
    elif cmd == "refresh":
        from app.coop_mission import get_coop_session_by_player

        sess = get_coop_session_by_player(storage, int(event.user_id))
        if sess is not None:
            await _coop_send_frames(bot, storage, event, sess)
        else:
            await bot.send_message(int(event.peer_id), "Вылазка не активна.", reply_markup=_coop_menu_keyboard())
    else:
        await _coop_apply(bot, storage, event)


# ---------------------------------------------------------------------------
# Рейды (app/game_logic + app/raid_grid — отряд 2–5 бойцов)
# ---------------------------------------------------------------------------


def _raid_menu_keyboard():
    return reply_keyboard(
        ["🪖 Рейд на локацию", "🏚 Рейд на склад врага"],
        ["🚀 Запустить рейд", "❌ Отменить рейд"],
        ["⬅️ В меню"],
    )


def _raid_fight_keyboard():
    return inline_keyboard(
        [("⬆️", "raid:move:up")],
        [("⬅️", "raid:move:left"), ("⬇️", "raid:move:down"), ("➡️", "raid:move:right")],
        [("🔫 ⬆️", "raid:shoot:up")],
        [("🔫 ⬅️", "raid:shoot:left"), ("🔫 ⬇️", "raid:shoot:down"), ("🔫 ➡️", "raid:shoot:right")],
        [("💊 Аптечка", "raid:medkit"), ("💉 Поднять", "raid:revive")],
        [("🏳 Сдаться", "raid:forfeit"), ("🔄 Обновить", "raid:refresh")],
    )


async def _raid_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_raids_overview

    overview = build_raids_overview(storage, int(event.user_id))
    text = overview or "🪖 Рейды"
    await bot.send_message(int(event.peer_id), text, reply_markup=_raid_menu_keyboard())


async def _raid_locations(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    rows = []
    for loc in storage.get_locations():
        name = str(loc.get("name") or "")
        ptype = str(loc.get("point_type") or "")
        if ptype == "база":
            continue
        rows.append([(f"🪖 {name}", f"raid:loc:{name}")])
    if not rows:
        await bot.send_message(int(event.peer_id), "Нет подходящих локаций для рейда.", reply_markup=_raid_menu_keyboard())
        return
    rows.append([("⬅️ Назад", "raid:menu")])
    await bot.send_message(int(event.peer_id), "🪖 Куда рейд?", reply_markup=inline_keyboard(*rows))


async def _raid_factions(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    rows = []
    for faction in ("Долг", "Свобода", "Нейтралы", "Бандиты"):
        rows.append([(f"🏚 {faction}", f"raid:depot:{faction}")])
    rows.append([("⬅️ Назад", "raid:menu")])
    await bot.send_message(int(event.peer_id), "🏚 Склад какой группировки?", reply_markup=inline_keyboard(*rows))


async def _raid_join_location(bot: VkMessenger, storage: Storage, event: VkEvent, location: str) -> None:
    from app.game_logic import create_or_join_faction_raid

    result = create_or_join_faction_raid(storage, int(event.user_id), location)
    await bot.send_message(int(event.peer_id), result.text, reply_markup=_raid_menu_keyboard())


async def _raid_join_depot(bot: VkMessenger, storage: Storage, event: VkEvent, faction: str) -> None:
    from app.game_logic import create_or_join_depot_raid

    result = create_or_join_depot_raid(storage, int(event.user_id), faction, depot="warehouse")
    await bot.send_message(int(event.peer_id), result.text, reply_markup=_raid_menu_keyboard())


async def _raid_launch(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import launch_open_raid

    result = launch_open_raid(storage, int(event.user_id))
    if not result.ok:
        await bot.send_message(int(event.peer_id), result.text)
        return
    await bot.send_message(int(event.peer_id), result.text)
    if result.tactical_raid:
        for pid in result.notify_member_ids:
            await _raid_send_frame_to(bot, storage, int(pid))


async def _raid_send_frame_to(bot: VkMessenger, storage: Storage, user_id: int) -> None:
    from app.raid_grid import get_raid_grid_session_by_player, rgrid_status_caption

    sess = get_raid_grid_session_by_player(storage, int(user_id))
    if sess is None:
        return
    try:
        caption = rgrid_status_caption(storage, sess, int(user_id))
    except Exception:
        caption = "🪖 Тактический рейд"
    try:
        png = render_rgrid_frame_local(storage, sess, int(user_id))
        await bot.send_photo(int(user_id), png, caption=caption, reply_markup=_raid_fight_keyboard(), fallback_text=caption)
    except Exception as exc:
        logger.warning("raid frame failed for %s: %s", user_id, exc)
        await bot.send_message(int(user_id), caption, reply_markup=_raid_fight_keyboard())


def render_rgrid_frame_local(storage: Storage, sess: Any, viewer_id: int):
    from app.raid_grid import render_rgrid_frame

    return render_rgrid_frame(storage, sess, viewer_id)


async def _raid_send_frames(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.raid_grid import get_raid_grid_session_by_player

    sess = get_raid_grid_session_by_player(storage, int(event.user_id))
    if sess is None:
        return
    for pid in list(getattr(sess, "player_ids", []) or []):
        await _raid_send_frame_to(bot, storage, int(pid))


async def _raid_apply(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.raid_grid import rgrid_forfeit, rgrid_move, rgrid_revive_ally, rgrid_shoot, rgrid_use_medkit

    payload = (event.payload or "").strip()
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "move":
        result = rgrid_move(storage, int(event.user_id), parts[2])
    elif cmd == "shoot":
        result = rgrid_shoot(storage, int(event.user_id), parts[2])
    elif cmd == "medkit":
        result = rgrid_use_medkit(storage, int(event.user_id))
    elif cmd == "forfeit":
        result = rgrid_forfeit(storage, int(event.user_id))
    elif cmd == "revive":
        result = rgrid_revive_ally(storage, int(event.user_id))
    else:
        result = None
    if result is not None:
        await bot.send_message(int(event.peer_id), result.text)
    await _raid_send_frames(bot, storage, event)
    sess = get_raid_grid_session_from_user(storage, int(event.user_id))
    if sess is None:
        await bot.send_message(int(event.peer_id), "Рейд завершён.", reply_markup=_menu_keyboard())


def get_raid_grid_session_from_user(storage: Storage, user_id: int):
    from app.raid_grid import get_raid_grid_session_by_player

    return get_raid_grid_session_by_player(storage, int(user_id))


async def _raid_cancel(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import cancel_raid_by_leader

    user_id = int(event.user_id)
    raids = list_open_raids_led_by_user(storage, user_id)
    if not raids:
        await bot.send_message(int(event.peer_id), "У тебя нет открытых рейдов.", reply_markup=_raid_menu_keyboard())
        return
    first = raids[0]
    raid_id = int(first.get("raid_id") if isinstance(first, dict) else first)
    result = cancel_raid_by_leader(storage, user_id, raid_id)
    await bot.send_message(int(event.peer_id), result.text, reply_markup=_raid_menu_keyboard())


def list_open_raids_led_by_user(storage: Storage, user_id: int):
    try:
        return storage.list_open_raids_led_by(int(user_id))
    except Exception:
        return []


async def _raid_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "menu":
        await _raid_open(bot, storage, event)
    elif cmd == "locations":
        await _raid_locations(bot, storage, event)
    elif cmd == "factions":
        await _raid_factions(bot, storage, event)
    elif cmd == "loc":
        await _raid_join_location(bot, storage, event, parts[2])
    elif cmd == "depot":
        await _raid_join_depot(bot, storage, event, parts[2])
    elif cmd == "launch":
        await _raid_launch(bot, storage, event)
    elif cmd == "cancel":
        await _raid_cancel(bot, storage, event)
    elif cmd == "refresh":
        await _raid_send_frames(bot, storage, event)
    else:
        await _raid_apply(bot, storage, event)


# ---------------------------------------------------------------------------
# Война за точки (app/neutral_capture + app/clan_war_grid + app/game_logic)
# ---------------------------------------------------------------------------


async def _war_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_war_lobby_overview

    rows = []
    for loc in storage.get_locations():
        name = str(loc.get("name") or "")
        ptype = str(loc.get("point_type") or "")
        if ptype != "база":
            rows.append([(f"🎯 {name}", f"war:ncap:{name}")])
    overview = build_war_lobby_overview(storage, int(event.user_id))
    text = (overview or "⚔️ Война за точки") + "\n\nВыбери цель захвата (нейтральные точки):"
    rows.append([("⬅️ В меню", "war:menu")])
    await bot.send_message(int(event.peer_id), text, reply_markup=inline_keyboard(*rows))


async def _war_ncap_join(bot: VkMessenger, storage: Storage, event: VkEvent, location: str) -> None:
    from app.neutral_capture import create_or_join_ncap_lobby, get_ncap_lobby_by_player

    result = create_or_join_ncap_lobby(storage, int(event.user_id), location)
    if not result.ok:
        await bot.send_message(int(event.peer_id), result.text, reply_markup=_war_menu_keyboard())
        return
    lobby = get_ncap_lobby_by_player(storage, int(event.user_id))
    kb = inline_keyboard(
        [("🚀 Запустить захват", "war:ncap:start")],
        [("🚪 Покинуть", "war:ncap:leave")],
        [("🔄 Обзор", "war:open")],
    ) if lobby is not None and lobby.host_id == int(event.user_id) else _war_menu_keyboard()
    await bot.send_message(int(event.peer_id), result.text, reply_markup=kb)


async def _war_ncap_start(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.neutral_capture import start_ncap_from_lobby

    result, _sess = start_ncap_from_lobby(storage, int(event.user_id))
    if not result.ok:
        await bot.send_message(int(event.peer_id), result.text)
        return
    await bot.send_message(int(event.peer_id), result.text)
    await _war_ncap_send_frames(bot, storage, event)


async def _war_ncap_leave(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.neutral_capture import leave_ncap_lobby

    result = leave_ncap_lobby(storage, int(event.user_id))
    await bot.send_message(int(event.peer_id), result.text, reply_markup=_war_menu_keyboard())


async def _war_ncap_send_frames(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.neutral_capture import get_ncap_session, render_ncap_frame

    sess = get_ncap_session(storage, int(event.user_id))
    if sess is None:
        await bot.send_message(int(event.peer_id), "Захват завершён.", reply_markup=_menu_keyboard())
        await _maybe_respawn_dead(bot, storage, event)
        return
    kb = _war_ncap_keyboard()
    for pid in list(getattr(sess, "player_ids", []) or []):
        try:
            player = storage.get_character(int(pid), refresh_energy=False)
            caption = ncap_status_caption_local(sess, player, int(pid))
        except Exception:
            caption = "🎯 Захват точки"
        try:
            png = render_ncap_frame(storage, sess, int(pid))
            await bot.send_photo(int(pid), png, caption=caption, reply_markup=kb, fallback_text=caption)
        except Exception:
            await bot.send_message(int(pid), caption, reply_markup=kb)


def ncap_status_caption_local(sess: Any, player: Any, viewer_id: int) -> str:
    from app.neutral_capture import ncap_status_caption

    return ncap_status_caption(sess, player, viewer_id)


def _war_menu_keyboard():
    return reply_keyboard(
        ["⚠️ Война", "🔥 Захват точки", "🏰 Военное лобби"],
        ["☢ Атака Монолита", "⬅️ В меню"],
    )


def _war_ncap_keyboard():
    return inline_keyboard(
        [("⬆️", "war:ncap:move:up")],
        [("⬅️", "war:ncap:move:left"), ("⬇️", "war:ncap:move:down"), ("➡️", "war:ncap:move:right")],
        [("🔫 ⬆️", "war:ncap:shoot:up")],
        [("🔫 ⬅️", "war:ncap:shoot:left"), ("🔫 ⬇️", "war:ncap:shoot:down"), ("🔫 ➡️", "war:ncap:shoot:right")],
        [("💊 Аптечка", "war:ncap:medkit"), ("🏳 Сдаться", "war:ncap:forfeit")],
        [("🔄 Обновить", "war:ncap:refresh")],
    )


def _war_cwar_keyboard():
    return inline_keyboard(
        [("⬆️", "war:cwar:move:up")],
        [("⬅️", "war:cwar:move:left"), ("⬇️", "war:cwar:move:down"), ("➡️", "war:cwar:move:right")],
        [("🔫 ⬆️", "war:cwar:shoot:up")],
        [("🔫 ⬅️", "war:cwar:shoot:left"), ("🔫 ⬇️", "war:cwar:shoot:down"), ("🔫 ➡️", "war:cwar:shoot:right")],
        [("💊 Аптечка", "war:cwar:medkit"), ("🏳 Сдаться", "war:cwar:forfeit")],
        [("🔄 Обновить", "war:cwar:refresh")],
    )


async def _war_war_lobby(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    rows = []
    for loc in storage.get_locations():
        name = str(loc.get("name") or "")
        rows.append([("🏰 " + name, f"war:warlobby:{name}")])
    rows.append([("⬅️ Назад", "war:open")])
    await bot.send_message(int(event.peer_id), "🏰 Военное лобби (занятые точки и базы):", reply_markup=inline_keyboard(*rows))


async def _war_war_lobby_join(bot: VkMessenger, storage: Storage, event: VkEvent, location: str) -> None:
    from app.game_logic import create_or_join_war_lobby

    result = create_or_join_war_lobby(storage, int(event.user_id), location)
    kb = inline_keyboard(
        [("🚀 Запустить штурм", "war:warlobby:launch")],
        [("🔄 Обзор", "war:open")],
    ) if result.ok else _war_menu_keyboard()
    await bot.send_message(int(event.peer_id), result.text, reply_markup=kb)


async def _war_war_lobby_launch(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import launch_war_lobby

    result = launch_war_lobby(storage, int(event.user_id))
    if not result.ok:
        await bot.send_message(int(event.peer_id), result.text)
        return
    await bot.send_message(int(event.peer_id), result.text)
    if result.tactical_cwar:
        for pid in result.notify_member_ids:
            await _war_cwar_send_frame_to(bot, storage, int(pid))


async def _war_cwar_send_frame_to(bot: VkMessenger, storage: Storage, user_id: int) -> None:
    from app.clan_war_grid import cwar_status_caption, get_cwar_session_by_player, render_cwar_frame

    sess = get_cwar_session_by_player(storage, int(user_id))
    if sess is None:
        return
    try:
        caption = cwar_status_caption(storage, sess, int(user_id))
    except Exception:
        caption = "⚔️ Тактический штурм"
    try:
        png = render_cwar_frame(storage, sess, int(user_id))
        await bot.send_photo(int(user_id), png, caption=caption, reply_markup=_war_cwar_keyboard(), fallback_text=caption)
    except Exception as exc:
        logger.warning("cwar frame failed for %s: %s", user_id, exc)
        await bot.send_message(int(user_id), caption, reply_markup=_war_cwar_keyboard())


async def _war_cwar_send_frames(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.clan_war_grid import get_cwar_session_by_player

    sess = get_cwar_session_by_player(storage, int(event.user_id))
    if sess is None:
        return
    for pid in list(getattr(sess, "player_ids", []) or []):
        await _war_cwar_send_frame_to(bot, storage, int(pid))
    if getattr(sess, "finished", False):
        await bot.send_message(int(event.peer_id), "⚔️ Штурм завершён.", reply_markup=_menu_keyboard())
        await _maybe_respawn_dead(bot, storage, event)


async def _war_ncap_apply(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.neutral_capture import ncap_forfeit, ncap_move, ncap_shoot, ncap_use_medkit

    payload = (event.payload or "").strip()
    parts = payload.split(":")
    action = parts[2] if len(parts) > 2 else ""
    if action == "move":
        result = ncap_move(storage, int(event.user_id), parts[3])
    elif action == "shoot":
        result = ncap_shoot(storage, int(event.user_id), parts[3])
    elif action == "medkit":
        result = ncap_use_medkit(storage, int(event.user_id))
    elif action == "forfeit":
        result = ncap_forfeit(storage, int(event.user_id))
    else:
        result = None
    if result is not None:
        await bot.send_message(int(event.peer_id), result.text)
    await _war_ncap_send_frames(bot, storage, event)


async def _war_cwar_apply(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.clan_war_grid import cwar_forfeit, cwar_move, cwar_shoot, cwar_use_medkit

    payload = (event.payload or "").strip()
    parts = payload.split(":")
    action = parts[2] if len(parts) > 2 else ""
    if action == "move":
        result = cwar_move(storage, int(event.user_id), parts[3])
    elif action == "shoot":
        result = cwar_shoot(storage, int(event.user_id), parts[3])
    elif action == "medkit":
        result = cwar_use_medkit(storage, int(event.user_id))
    elif action == "forfeit":
        result = cwar_forfeit(storage, int(event.user_id))
    else:
        result = None
    if result is not None:
        await bot.send_message(int(event.peer_id), result.text)
    await _war_cwar_send_frames(bot, storage, event)


async def _war_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "menu":
        await _menu(bot, storage, event)
    elif cmd == "open":
        await _war_open(bot, storage, event)
    elif cmd == "ncap":
        action = parts[2] if len(parts) > 2 else ""
        if action == "start":
            await _war_ncap_start(bot, storage, event)
        elif action == "leave":
            await _war_ncap_leave(bot, storage, event)
        elif action == "refresh":
            await _war_ncap_send_frames(bot, storage, event)
        elif action in ("move", "shoot", "medkit", "forfeit"):
            await _war_ncap_apply(bot, storage, event)
        else:
            await _war_ncap_join(bot, storage, event, parts[2])
    elif cmd == "warlobby":
        action = parts[2] if len(parts) > 2 else ""
        if action == "launch":
            await _war_war_lobby_launch(bot, storage, event)
        else:
            await _war_war_lobby_join(bot, storage, event, parts[2])
    elif cmd == "cwar":
        action = parts[2] if len(parts) > 2 else ""
        if action in ("move", "shoot", "medkit", "forfeit"):
            await _war_cwar_apply(bot, storage, event)
        elif action == "refresh":
            await _war_cwar_send_frames(bot, storage, event)


# ---------------------------------------------------------------------------
# Группировка / клан (казное, склады, звания)
# ---------------------------------------------------------------------------


def _faction_menu_keyboard():
    return reply_keyboard(
        ["🏛 Обзор", "💰 Казна"],
        ["🎖 Звания", "📦 Склад"],
        ["🏗 Постройки", "🏛 Клановые задачи"],
        ["🤝 Союзы", "🚚 Гараж"],
        ["⬅️ В меню"],
    )


async def _faction_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    player = storage.get_character(int(event.user_id), refresh_energy=False)
    if player is None:
        await bot.send_message(int(event.peer_id), "Сначала создай персонажа.")
        return
    elif not player.faction:
        await bot.send_message(
            int(event.peer_id),
            "Ты пока без группировки. Выбери её кнопками ниже:",
            reply_markup=_faction_keyboard(),
        )
        return
    await _faction_overview(bot, storage, event)


async def _faction_overview(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_faction_group_overview

    text = build_faction_group_overview(storage, int(event.user_id))
    await bot.send_message(int(event.peer_id), text, reply_markup=_faction_menu_keyboard())


async def _faction_treasury(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    player = storage.get_character(int(event.user_id), refresh_energy=False)
    if player is None:
        return
    kb = inline_keyboard(
        [("📥 Внести 500", "faction:tz:dep:500"), ("📥 Внести всё", "faction:tz:depall")],
        [("📤 Вывести 500", "faction:tz:wd:500" ), ("📤 Вывести всё", "faction:tz:wdall")],
        [("⬅️ Назад", "faction:overview")],
    )
    await bot.send_message(
        int(event.peer_id),
        f"💰 Казна группировки «{player.faction}»\nВнести можно сколько хочешь, вывести — лидер или ранг 5+.",
        reply_markup=kb,
    )


async def _faction_treasury_apply(bot: VkMessenger, storage: Storage, event: VkEvent, amount: str) -> None:
    from app.game_logic import deposit_to_faction_treasury, withdraw_from_faction_treasury

    user_id = int(event.user_id)
    if amount == "depall":
        player = storage.get_character(user_id, refresh_energy=False)
        amount_val = int(getattr(player, "money", 0) or 0)
        result = deposit_to_faction_treasury(storage, user_id, amount_val)
    elif amount == "wdall":
        result = withdraw_from_faction_treasury(storage, user_id, 999_999_999)
    elif amount.startswith("dep:"):
        result = deposit_to_faction_treasury(storage, user_id, int(amount.split(":")[-1]))
    elif amount.startswith("wd:"):
        result = withdraw_from_faction_treasury(storage, user_id, int(amount.split(":")[-1]))
    else:
        result = None
    if result is not None:
        await bot.send_message(int(event.peer_id), result.text, reply_markup=_faction_menu_keyboard())


async def _faction_ranks(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_faction_ranks_overview

    text = build_faction_ranks_overview(storage, int(event.user_id))
    await bot.send_message(int(event.peer_id), text, reply_markup=_faction_menu_keyboard())


async def _faction_warehouse(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_faction_group_overview

    text = build_faction_group_overview(storage, int(event.user_id))
    await bot.send_message(
        int(event.peer_id),
        text + "\n\n📦 Склад: пополнение/выдача будет здесь.",
        reply_markup=_faction_menu_keyboard(),
    )


async def _faction_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "overview":
        await _faction_overview(bot, storage, event)
    elif cmd == "treasury":
        await _faction_treasury(bot, storage, event)
    elif cmd == "ranks":
        await _faction_ranks(bot, storage, event)
    elif cmd == "warehouse":
        await _faction_warehouse(bot, storage, event)
    elif cmd == "tz":
        # Собираем через ":", чтобы amount совпал с ветками "dep:*"/"wd:*"
        # (payload "faction:tz:dep:500" -> amount "dep:500").
        await _faction_treasury_apply(bot, storage, event, ":".join(parts[2:]))
    elif cmd == "goals":
        await _faction_goals(bot, storage, event)
    elif cmd == "goalsclaim":
        await _faction_goals_claim(bot, storage, event)
    elif cmd == "garage":
        await _garage_open(bot, storage, event)
    elif cmd == "garagedep" and len(parts) >= 3:
        await _garage_deposit(bot, storage, event, parts[2])
    elif cmd == "garagewd" and len(parts) >= 3:
        await _garage_withdraw(bot, storage, event, parts[2])
    elif cmd == "garagereq" and len(parts) >= 3:
        await _garage_rental_request(bot, storage, event, parts[2])
    elif cmd == "garagereqs":
        await _garage_requests(bot, storage, event)
    elif cmd == "garageapp":
        await _garage_approve(bot, storage, event, ":".join(parts[2:]))
    elif cmd == "garagedeny":
        await _garage_deny(bot, storage, event, ":".join(parts[2:]))
    elif cmd == "menu":
        await bot.send_message(int(event.peer_id), "🏛 Группировка:", reply_markup=_faction_menu_keyboard())


# ---------------------------------------------------------------------------
# Постройки базы (app/faction_buildings)
# ---------------------------------------------------------------------------


def _buildings_menu_keyboard():
    return reply_keyboard(
        ["🏗 Статус построек", "🔨 Построить"],
        ["⬅️ В меню"],
    )


def _buildings_build_keyboard():
    from app.faction_buildings import BUILDING_KEYS, BUILDING_TITLES

    rows = []
    for key in BUILDING_KEYS:
        rows.append([(f"🏗 {BUILDING_TITLES.get(key, key)}", f"build:do:{key}")])
    rows.append([("⬅️ Назад", "build:menu")])
    return inline_keyboard(*rows)


async def _buildings_status(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.faction_buildings import faction_buildings_status_text

    player = storage.get_character(int(event.user_id), refresh_energy=False)
    if player is None:
        return
    text = faction_buildings_status_text(storage, player.faction)
    await bot.send_message(int(event.peer_id), text, reply_markup=_buildings_menu_keyboard())


async def _buildings_choose(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    await bot.send_message(int(event.peer_id), "🔨 Что построить на базе?", reply_markup=_buildings_build_keyboard())


async def _buildings_build(bot: VkMessenger, storage: Storage, event: VkEvent, key: str) -> None:
    from app.faction_buildings import build_building

    result = build_building(storage, int(event.user_id), key)
    await bot.send_message(int(event.peer_id), result.text, reply_markup=_buildings_menu_keyboard())


async def _buildings_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "menu":
        await _buildings_status(bot, storage, event)
    elif cmd == "status":
        await _buildings_status(bot, storage, event)
    elif cmd == "choose":
        await _buildings_choose(bot, storage, event)
    elif cmd == "do":
        await _buildings_build(bot, storage, event, parts[2])
    else:
        await _buildings_status(bot, storage, event)


# ---------------------------------------------------------------------------
# Обработчики событий
# ---------------------------------------------------------------------------


async def handle_event(bot: VkMessenger, event: VkEvent) -> None:
    storage = get_storage()
    if event.kind == "callback":
        # VK держит нажатую inline-кнопку в состоянии «загрузки», пока бот не ответит
        # на message_event вызовом messages.sendMessageEventAnswer. Отвечаем сразу:
        # долгая обработка (рендер кадров дуэлей/арены) не должна превысить лимит ответа.
        await _answer_message_event(bot, event)
        await _handle_callback(bot, storage, event)
    else:
        await _handle_message(bot, storage, event)


async def _answer_message_event(bot: VkMessenger, event: VkEvent) -> None:
    """Закрыть «загрузку» у inline-кнопки после обработки нажатия."""
    raw = event.raw
    if not isinstance(raw, dict):
        return
    event_id = raw.get("event_id")
    if not event_id:
        return
    try:
        bot.vk.method(
            "messages.sendMessageEventAnswer",
            {
                "event_id": str(event_id),
                "user_id": int(event.user_id),
                "peer_id": int(event.peer_id),
            },
        )
    except Exception as exc:
        # Ошибка не критична: VK сам закроет загрузку по таймауту.
        logger.debug("sendMessageEventAnswer failed: %s", exc)


async def _daily_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    """«📅 Ежедневка» (как в ТГ): награда выдаётся сразу при открытии раздела."""
    player = storage.get_character(int(event.user_id), refresh_energy=True)
    if player is None:
        await bot.send_message(int(event.peer_id), "Сначала создай персонажа.")
        return
    from app.game_logic import claim_daily_login

    result = claim_daily_login(storage, int(event.user_id))
    await bot.send_message(
        int(event.peer_id),
        result.text,
        reply_markup=_pda_keyboard_for(storage, int(event.user_id)),
    )


async def _daily_claim(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    await _daily_open(bot, storage, event)


async def _daily_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    await _daily_open(bot, storage, event)


# ---------------------------------------------------------------------------
# Рейтинг и сезоны (app/game_logic — build_rating_*, build_season_*)
# ---------------------------------------------------------------------------


def _rating_keyboard():
    return inline_keyboard(
        [("📊 Моя статистика", "rating:stats")],
        [("🏆 Рейтинг за всё время", "rating:alltime:page:0")],
        [("📅 Рейтинг за сезон", "rating:season:page:0")],
        [("🏅 Рейтинг группировок", "rating:factions"), ("🎖 Достижения и медали", "rating:achievements")],
        [("📅 Когда кончится сезон", "rating:seasoninfo")],
        [("⬅️ В меню", "rating:exit")],
    )


async def _rating_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_rating_menu_text

    await bot.send_message(int(event.peer_id), build_rating_menu_text(), reply_markup=_rating_keyboard())


async def _rating_stats(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    """«📊 Моя статистика» в рейтинге (как ratings:stats в ТГ)."""
    from app.game_logic import build_player_stats_text

    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None:
        await bot.send_message(int(event.peer_id), "Сначала создай персонажа.")
        return
    text = build_player_stats_text(storage, user_id)
    await bot.send_message(
        int(event.peer_id),
        text,
        reply_markup=inline_keyboard([("⬅️ К разделам рейтинга", "rating:menu")]),
    )


async def _rating_alltime(bot: VkMessenger, storage: Storage, event: VkEvent, page: int = 0) -> None:
    from app.game_logic import build_rating_overview

    text, safe_page, total_pages = build_rating_overview(storage, int(event.user_id), page=int(page or 0))
    nav: list[tuple[str, str]] = []
    if safe_page > 0:
        nav.append(("⬅️", f"rating:alltime:page:{safe_page - 1}"))
    nav.append((f"{safe_page + 1}/{total_pages}", "rating:menu"))
    if safe_page < total_pages - 1:
        nav.append(("➡️", f"rating:alltime:page:{safe_page + 1}"))
    await bot.send_message(
        int(event.peer_id),
        text,
        reply_markup=inline_keyboard(
            nav,
            [("⬅️ К разделам рейтинга", "rating:menu")],
        ),
    )


async def _rating_season(bot: VkMessenger, storage: Storage, event: VkEvent, page: int = 0) -> None:
    from app.game_logic import build_season_rating_overview

    text, safe_page, total_pages = build_season_rating_overview(storage, int(event.user_id), page=int(page or 0))
    nav: list[tuple[str, str]] = []
    if safe_page > 0:
        nav.append(("⬅️", f"rating:season:page:{safe_page - 1}"))
    nav.append((f"{safe_page + 1}/{total_pages}", "rating:menu"))
    if safe_page < total_pages - 1:
        nav.append(("➡️", f"rating:season:page:{safe_page + 1}"))
    await bot.send_message(
        int(event.peer_id),
        text,
        reply_markup=inline_keyboard(
            nav,
            [("⬅️ К разделам рейтинга", "rating:menu")],
        ),
    )


async def _rating_achievements(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_achievements_overview

    text = build_achievements_overview(storage, int(event.user_id))
    await bot.send_message(
        int(event.peer_id),
        text,
        reply_markup=inline_keyboard([("⬅️ К разделам рейтинга", "rating:menu")]),
    )


async def _rating_factions(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_faction_rating_overview

    text = build_faction_rating_overview(storage, int(event.user_id))
    await bot.send_message(
        int(event.peer_id),
        text,
        reply_markup=inline_keyboard([("⬅️ К разделам рейтинга", "rating:menu")]),
    )


async def _rating_seasoninfo(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_season_schedule_text

    text = build_season_schedule_text(storage)
    await bot.send_message(
        int(event.peer_id),
        text,
        reply_markup=inline_keyboard([("⬅️ К разделам рейтинга", "rating:menu")]),
    )


async def _rating_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "alltime":
        page = int(parts[-1]) if parts[-1].isdigit() else 0
        await _rating_alltime(bot, storage, event, page)
    elif cmd == "season":
        page = int(parts[-1]) if parts[-1].isdigit() else 0
        await _rating_season(bot, storage, event, page)
    elif cmd == "factions":
        await _rating_factions(bot, storage, event)
    elif cmd == "achievements":
        await _rating_achievements(bot, storage, event)
    elif cmd == "seasoninfo":
        await _rating_seasoninfo(bot, storage, event)
    elif cmd == "stats":
        await _rating_stats(bot, storage, event)
    elif cmd == "menu":
        await _rating_open(bot, storage, event)
    else:
        await _menu(bot, storage, event)


# ---------------------------------------------------------------------------
# Задания: контракты дня/недели, поездка на точку, вылазка 6×6
# (app/game_logic + app/quest_mission)
# ---------------------------------------------------------------------------

_QUEST_PAGE_SIZE = 6


def _quest_status_text(storage: Storage, player: Any) -> str:
    from app.game_logic import (
        DAILY_CONTRACT_BONUS_PERCENT,
        QUEST_CONTRACTS,
        WEEKLY_CONTRACT_BONUS_PERCENT,
        daily_artifact_hunt_done_today,
        faction_home_base,
        get_daily_contract_keys,
        get_weekly_contract_key,
        is_traveling,
    )

    user_id = int(player.telegram_id)
    lines = ["📋 Задания"]
    active = storage.get_active_contract(user_id)
    if active:
        template = QUEST_CONTRACTS.get(str(active.get("template_key", "")))
        stage = str(active.get("stage", "work"))
        title = template.title if template else "контракт"
        lines.append(f"📌 {title} · этап: {stage}")
        if template and stage == "work":
            lines.append(f"🗺 Точка: «{template.work_location}»")
            if str(player.location) == template.work_location and not is_traveling(player):
                lines.append("✅ Ты на месте — жми «⚙️ Выполнить задание».")
            else:
                lines.append("Доберись до точки (кнопка «🗺 Ехать на точку»).")
        elif template and stage == "return":
            home = faction_home_base(player.faction)
            lines.append(f"🏠 Сдай отчёт на базе «{home}» — сдастся сам при прибытии.")
        if is_traveling(player):
            lines.append("⏱ Ты в пути — прибудет уведомление.")
    else:
        lines.append("Нет активного контракта — бери на домашней базе из списка.")

    if daily_artifact_hunt_done_today(storage, user_id):
        lines.append("🗓 Ежедневный поиск арта: уже получал награду сегодня.")
    else:
        lines.append("🗓 Ежедневный поиск арта: первый ценный арт за сутки +бонус.")

    daily_keys = get_daily_contract_keys(storage)
    if daily_keys:
        names: list[str] = []
        for key in daily_keys:
            tpl = QUEST_CONTRACTS.get(key)
            if tpl:
                names.append(tpl.title)
        lines.append(
            f"🗓 Контракты дня (+{DAILY_CONTRACT_BONUS_PERCENT}% RU): "
            + (", ".join(names) if names else "нет")
        )
    weekly_key = get_weekly_contract_key(storage)
    if weekly_key:
        tpl = QUEST_CONTRACTS.get(weekly_key)
        if tpl:
            lines.append(
                f"📅 Контракт недели (+{WEEKLY_CONTRACT_BONUS_PERCENT}% RU): {tpl.title}"
            )
    return "\n".join(lines)


def _quest_menu_keyboard(storage: Storage, player: Any):
    from app.game_logic import QUEST_CONTRACTS, faction_home_base, is_traveling

    active = storage.get_active_contract(int(player.telegram_id))
    rows: list[list] = []
    if active:
        stage = str(active.get("stage", "work"))
        template = QUEST_CONTRACTS.get(str(active.get("template_key", "")))
        if stage == "work" and template is not None:
            if str(player.location) == template.work_location and not is_traveling(player):
                rows.append([("⚙️ Выполнить задание", "quest:work")])
            elif not is_traveling(player):
                rows.append([("🗺 Ехать на точку", "quest:travel_work")])
        elif stage == "return":
            home = faction_home_base(player.faction)
            if str(player.location) != home and not is_traveling(player):
                rows.append([("🏠 На базу", "quest:go_home")])
        rows.append([("❌ Отменить контракт", "quest:cancel")])
    else:
        rows.append([("📋 Список контрактов", "quest:list")])
    rows.append([("🔄 Обновить", "quest:refresh"), ("⬅️ В меню", "quest:exit")])
    return inline_keyboard(*rows)


async def _quest_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import try_auto_turn_in_contract

    user_id = int(event.user_id)
    try:
        auto = try_auto_turn_in_contract(storage, user_id)
    except Exception as exc:
        logger.debug("auto turn-in failed (%s): %s", user_id, exc)
        auto = None
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None:
        await bot.send_message(int(event.peer_id), "Сначала создай персонажа.")
        return
    text = _quest_status_text(storage, player)
    if auto:
        text = f"{auto}\n\n{text}"
    await bot.send_message(int(event.peer_id), text, reply_markup=_quest_menu_keyboard(storage, player))


async def _quest_list(bot: VkMessenger, storage: Storage, event: VkEvent, page: int = 0) -> None:
    from app.game_logic import build_quest_overview, list_quest_contracts_for_character

    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None:
        await bot.send_message(int(event.peer_id), "Сначала создай персонажа.")
        return
    overview = build_quest_overview(storage, player)
    await bot.send_message(
        int(event.peer_id),
        overview,
        reply_markup=inline_keyboard([("⬅️ Назад", "quest:menu")]),
    )

    templates = list_quest_contracts_for_character(player)
    total_pages = max(1, (len(templates) + _QUEST_PAGE_SIZE - 1) // _QUEST_PAGE_SIZE)
    safe_page = max(0, min(int(page or 0), total_pages - 1))
    chunk = templates[safe_page * _QUEST_PAGE_SIZE : (safe_page + 1) * _QUEST_PAGE_SIZE]
    rows: list[list] = []
    for tpl in chunk:
        label = f"📋 {tpl.title}"
        if len(label) > 40:
            label = label[:39] + "…"
        rows.append([(label, f"quest:accept:{tpl.key}")])
    nav: list[tuple[str, str]] = []
    if safe_page > 0:
        nav.append(("⬅️", f"quest:list:{safe_page - 1}"))
    if safe_page < total_pages - 1:
        nav.append(("➡️", f"quest:list:{safe_page + 1}"))
    nav.append(("⬅️ В меню", "quest:exit"))
    rows.append(nav)
    await bot.send_message(
        int(event.peer_id),
        f"📋 Выбери контракт ({safe_page + 1}/{total_pages}). Принять можно только на базе:",
        reply_markup=inline_keyboard(*rows),
    )


async def _quest_accept(bot: VkMessenger, storage: Storage, event: VkEvent, contract_key: str) -> None:
    from app.game_logic import accept_quest_contract

    # Заказы у техника: растёт авторитет техника → открываются upgrade предметы.
    result = accept_quest_contract(storage, int(event.user_id), contract_key, vendor="tech")
    await bot.send_message(int(event.peer_id), result.text)
    if result.ok:
        await _quest_open(bot, storage, event)


async def _quest_cancel(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import cancel_quest_contract

    result = cancel_quest_contract(storage, int(event.user_id))
    await bot.send_message(int(event.peer_id), result.text)
    await _quest_open(bot, storage, event)


async def _quest_work(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import run_contract_work

    user_id = int(event.user_id)
    result = run_contract_work(storage, user_id)
    payload = result.payload or {}
    image = payload.get("mission_image")
    if image and payload.get("mission_active"):
        note = result.text if payload.get("mission_started") else None
        await _qmission_send_frame(
            bot,
            storage,
            event,
            image,
            caption=str(payload.get("caption") or ""),
            note=note,
        )
        return
    await bot.send_message(int(event.peer_id), result.text)
    if result.ok or payload.get("mission_done"):
        await _quest_open(bot, storage, event)


async def _quest_travel_work(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import QUEST_CONTRACTS, travel_to

    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None:
        await bot.send_message(int(event.peer_id), "Сначала создай персонажа.")
        return
    active = storage.get_active_contract(user_id)
    template = QUEST_CONTRACTS.get(str((active or {}).get("template_key", ""))) if active else None
    if template is None:
        await bot.send_message(int(event.peer_id), "Нет активного контракта.")
        return
    if str(player.location) == template.work_location:
        await bot.send_message(int(event.peer_id), "Ты уже на точке задания.")
        return
    bound = storage.get_bound_transport(user_id)
    preferred = bound if bound in ("niva", "truck") else None
    result = travel_to(storage, user_id, template.work_location, transport_mode=preferred)
    await bot.send_message(int(event.peer_id), result.text)
    if result.ok:
        await _quest_open(bot, storage, event)


async def _quest_go_home(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import faction_home_base, travel_to

    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None:
        await bot.send_message(int(event.peer_id), "Сначала создай персонажа.")
        return
    home = faction_home_base(player.faction)
    if str(player.location) == home:
        await bot.send_message(int(event.peer_id), "Ты уже на базе.")
        return
    active = storage.get_active_contract(user_id)
    if not active or str(active.get("stage", "")) != "return":
        await bot.send_message(int(event.peer_id), "Сейчас нечего везти на базу.")
        return
    bound = storage.get_bound_transport(user_id)
    preferred = bound if bound in ("niva", "truck") else None
    result = travel_to(storage, user_id, home, transport_mode=preferred)
    await bot.send_message(int(event.peer_id), result.text)
    if result.ok:
        await _quest_open(bot, storage, event)


async def _quest_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "list":
        page = int(parts[-1]) if len(parts) > 2 and parts[-1].isdigit() else 0
        await _quest_list(bot, storage, event, page)
    elif cmd == "menu":
        await _quest_open(bot, storage, event)
    elif cmd == "accept" and len(parts) >= 3:
        await _quest_accept(bot, storage, event, parts[2])
    elif cmd == "work":
        await _quest_work(bot, storage, event)
    elif cmd == "travel_work":
        await _quest_travel_work(bot, storage, event)
    elif cmd == "go_home":
        await _quest_go_home(bot, storage, event)
    elif cmd == "cancel":
        await _quest_cancel(bot, storage, event)
    elif cmd == "refresh":
        await _quest_open(bot, storage, event)
    elif cmd == "exit":
        await _menu(bot, storage, event)


# ---------------------------------------------------------------------------
# Квестовая вылазка 6×6 (app/quest_mission)
# ---------------------------------------------------------------------------


def _qmission_keyboard(storage: Storage, user_id: int):
    from app.quest_mission import get_mission_session, mission_shoot_available

    player = storage.get_character(int(user_id), refresh_energy=False)
    meds = 0
    if player is not None:
        meds = sum(int((player.inventory or {}).get(k, 0)) for k in ("medkit", "medkit_army", "medkit_science"))
    session = get_mission_session(storage, int(user_id))
    shoot = mission_shoot_available(session) if session is not None else False
    med_label = f"💊 Аптечка ({meds})" if meds > 0 else "💊 Аптечка"
    rows: list[list] = [
        [(med_label, "qmission:medkit"), ("⬆️ Вперёд", "qmission:up"), ("🔄 Обновить", "qmission:refresh")],
        [("⬅️ Влево", "qmission:left"), ("⬇️ Назад", "qmission:down"), ("➡️ Вправо", "qmission:right")],
    ]
    if shoot:
        rows.append([("🔫 ⬆️", "qmission:shoot:up")])
        rows.append([("🔫 ⬅️", "qmission:shoot:left"), ("🔫 ⬇️", "qmission:shoot:down"), ("🔫 ➡️", "qmission:shoot:right")])
    rows.append([("🏃 Свалить", "qmission:leave")])
    return inline_keyboard(*rows)


async def _qmission_send_frame(
    bot: VkMessenger,
    storage: Storage,
    event: VkEvent,
    image: Any,
    *,
    caption: str,
    note: str | None = None,
) -> None:
    text = caption if not note else f"{caption}\n\n{note}"
    kb = _qmission_keyboard(storage, int(event.user_id))
    try:
        await bot.send_photo(int(event.peer_id), image, caption=text, reply_markup=kb, fallback_text=text)
    except Exception as exc:
        logger.warning("quest frame failed: %s", exc)
        await bot.send_message(int(event.peer_id), text, reply_markup=kb)


async def _qmission_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    from app.quest_mission import (
        abandon_quest_mission,
        clear_mission_session,
        get_mission_session,
        mission_status_caption,
        move_quest_mission,
        render_mission_for_player,
        shoot_quest_mission,
        use_mission_medkit,
    )

    user_id = int(event.user_id)
    action = payload[len("qmission:"):].strip() if payload.startswith("qmission:") else payload

    if action == "leave":
        result = abandon_quest_mission(storage, user_id)
        await bot.send_message(int(event.peer_id), result.text)
        await _quest_open(bot, storage, event)
        return

    if action == "refresh":
        session = get_mission_session(storage, user_id)
        player = storage.get_character(user_id, refresh_energy=False)
        if session is None or player is None:
            await bot.send_message(int(event.peer_id), "Активной вылазки по контракту нет.")
            return
        try:
            image = render_mission_for_player(storage, user_id, session, player)
        except Exception as exc:
            logger.warning("quest render failed: %s", exc)
            await bot.send_message(int(event.peer_id), "Не удалось отрисовать поле — обнови попозже.")
            return
        await _qmission_send_frame(bot, storage, event, image, caption=mission_status_caption(session, player))
        return

    if action == "medkit":
        result = use_mission_medkit(storage, user_id)
    elif action.startswith("shoot:"):
        direction = action[len("shoot:"):]
        if direction not in ("up", "down", "left", "right"):
            return
        result = shoot_quest_mission(storage, user_id, direction)
    elif action in ("up", "down", "left", "right"):
        result = move_quest_mission(storage, user_id, action)
    else:
        return

    payload = result.payload or {}
    if payload.get("mission_dead"):
        await bot.send_message(int(event.peer_id), result.text)
        clear_mission_session(storage, user_id)
        await _quest_open(bot, storage, event)
        return
    if payload.get("mission_done") or payload.get("mission_active") is False:
        await bot.send_message(int(event.peer_id), result.text)
        await _quest_open(bot, storage, event)
        return
    image = payload.get("mission_image")
    if image:
        await _qmission_send_frame(
            bot,
            storage,
            event,
            image,
            caption=str(payload.get("caption") or ""),
            note=str(payload.get("move_note") or result.text),
        )
        return
    await bot.send_message(int(event.peer_id), result.text)


# ---------------------------------------------------------------------------
# Переходы между локациями (app/game_logic — travel_to и список локаций)
# ---------------------------------------------------------------------------

_TRAVEL_PAGE_SIZE = 5


def _travel_locations(storage: Storage, player: Any) -> list[dict[str, Any]]:
    from app.monolith_war import filter_travel_locations_for_faction

    try:
        return list(filter_travel_locations_for_faction(storage.get_locations(), player.faction))
    except Exception:
        return list(storage.get_locations())


def _travel_location_label(location: dict[str, Any]) -> str:
    name = str(location.get("name") or "?")
    ptype = str(location.get("point_type") or "")
    owner = str(location.get("controlled_by") or "нейтрал")
    display = "Тунель (Барахолка)" if name == "Тунель" else name
    label = f"{display} [{ptype}, {owner}]"
    return label if len(label) <= 45 else label[:44] + "…"


async def _travel_open(bot: VkMessenger, storage: Storage, event: VkEvent, page: int = 0) -> None:
    from app.game_logic import (
        TRAVEL_SPEED_BICYCLE,
        TRAVEL_SPEED_NIVA,
        TRAVEL_SPEED_TRUCK,
        TRANSPORT_QUEST_REWARD_MULT,
        describe_travel_fuel_status,
        can_use_n2o_during_travel,
        format_location_display,
        is_traveling,
        needs_field_repair,
    )

    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None:
        await bot.send_message(int(event.peer_id), "Сначала создай персонажа.")
        return
    traveling = is_traveling(player)
    if traveling:
        can_n2o = can_use_n2o_during_travel(storage, user_id)
        kb_rows: list[list] = [[("⏱ Статус перехода", "travel:status")]]
        if can_n2o:
            kb_rows.append([("💨 Закись азота (N2O, ÷2 остаток)", "travel:n2o")])
        kb_rows.append([("⬅️ В меню", "walk:exit")])
        text = (
            f"{format_location_display(player)}\n"
            "⏱ Отсчёт времени — в отдельном сообщении с таймером.\n\n"
            "Пока идёт переход, другие действия на точке недоступны."
        )
        await bot.send_message(int(event.peer_id), text, reply_markup=inline_keyboard(*kb_rows))
        return

    locations = _travel_locations(storage, player)
    total_pages = max(1, (len(locations) + _TRAVEL_PAGE_SIZE - 1) // _TRAVEL_PAGE_SIZE)
    safe_page = max(0, min(int(page or 0), total_pages - 1))
    chunk = locations[safe_page * _TRAVEL_PAGE_SIZE : (safe_page + 1) * _TRAVEL_PAGE_SIZE]
    rows: list[list] = []
    for location in chunk:
        name = str(location.get("name") or "")
        if name and name != player.location:
            rows.append([(_travel_location_label(location), f"travel:to:{name}")])
    nav: list[tuple[str, str]] = []
    if safe_page > 0:
        nav.append(("⬅️", f"travel:page:{safe_page - 1}"))
    if safe_page < total_pages - 1:
        nav.append(("➡️", f"travel:page:{safe_page + 1}"))
    nav.append(("⬅️ В меню", "walk:exit"))
    rows.append(nav)

    rules = (
        "Выбери локацию, затем транспорт (велик доступен даже если есть Нива/грузовик).\n"
        f"Пешком ×1, велосипед ×{TRAVEL_SPEED_BICYCLE:g} "
        f"(+награда контракта ×{TRANSPORT_QUEST_REWARD_MULT['bicycle']:g} если доехал на нём), "
        f"Нива ×{TRAVEL_SPEED_NIVA:g}, грузовик ×{TRAVEL_SPEED_TRUCK:g} (+ дизель).\n"
        "Переход занимает реальное время (1 игровая мин ≈ 10 сек).\n\n"
        f"{describe_travel_fuel_status(player)}"
    )
    if needs_field_repair(player):
        rules += "\n\n⚠️ Техника сломана — подлатай её кнопкой ниже (бесплатно, до 5%), чтобы уехать."
        rows.append([("🔧 Подлатать технику до 5%", "travel:fieldrepair")])
    await bot.send_message(
        int(event.peer_id),
        rules,
        reply_markup=inline_keyboard(*rows),
    )


async def _travel_pick(bot: VkMessenger, storage: Storage, event: VkEvent, destination: str) -> None:
    from app.game_logic import list_available_travel_modes

    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None:
        await bot.send_message(int(event.peer_id), "Сначала создай персонажа.")
        return
    modes = list_available_travel_modes(player, bound_transport=storage.get_bound_transport(user_id))
    rows: list[list] = []
    for mode, label, _speed, energy in modes:
        rows.append([(f"{label} (−{energy} эн.)", f"travel:go:{mode}:{destination}")])
    rows.append([("⬅️ К локациям", "travel:open")])
    await bot.send_message(
        int(event.peer_id),
        f"Куда: «{destination}».\nВыбери транспорт:",
        reply_markup=inline_keyboard(*rows),
    )


async def _travel_go(
    bot: VkMessenger, storage: Storage, event: VkEvent, mode: str, destination: str
) -> None:
    from app.game_logic import travel_to

    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None:
        await bot.send_message(int(event.peer_id), "Сначала создай персонажа.")
        return
    bound = storage.get_bound_transport(user_id)
    preferred = None
    if bound in ("niva", "truck") and mode in ("foot", "bicycle"):
        preferred = bound
    result = travel_to(storage, user_id, destination, transport_mode=mode if mode != preferred else preferred)
    await bot.send_message(int(event.peer_id), result.text)
    if result.ok:
        await _travel_open(bot, storage, event, 0)


async def _travel_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "open":
        await _travel_open(bot, storage, event, 0)
    elif cmd == "page":
        page = int(parts[-1]) if len(parts) > 2 and parts[-1].isdigit() else 0
        await _travel_open(bot, storage, event, page)
    elif cmd == "to" and len(parts) >= 3:
        await _travel_pick(bot, storage, event, ":".join(parts[2:]))
    elif cmd == "go" and len(parts) >= 4:
        await _travel_go(bot, storage, event, parts[2], ":".join(parts[3:]))
    elif cmd == "status":
        user_id = int(event.user_id)
        player = storage.get_character(user_id, refresh_energy=False)
        if player is None:
            return
        from app.game_logic import travel_status_text

        status = travel_status_text(player)
        await bot.send_message(
            int(event.peer_id),
            status or "Сейчас ты никуда не едешь.",
            reply_markup=inline_keyboard([("🗺 Переходы", "travel:open")]),
        )
    elif cmd == "fieldrepair":
        from app.game_logic import field_repair_vehicle

        result = field_repair_vehicle(storage, int(event.user_id))
        await bot.send_message(int(event.peer_id), getattr(result, "text", str(result)))
        await _travel_open(bot, storage, event, 0)
    elif cmd == "n2o":
        from app.game_logic import apply_n2o_to_active_travel

        result = apply_n2o_to_active_travel(storage, int(event.user_id))
        await bot.send_message(int(event.peer_id), getattr(result, "text", str(result)))
        await _travel_open(bot, storage, event, 0)


# ---------------------------------------------------------------------------
# Поиск артефактов (app/artifact_hunt — охота 8×8 с детектором)
# ---------------------------------------------------------------------------


def _hunt_keyboard():
    return inline_keyboard(
        [("⬆️ Вперёд", "hunt:up")],
        [("⬅️ Влево", "hunt:left"), ("⬇️ Назад", "hunt:down"), ("➡️ Вправо", "hunt:right")],
        [("🔫⬆", "hunt:shoot:up")],
        [("🔫⬅", "hunt:shoot:left"), ("🔫⬇", "hunt:shoot:down"), ("🔫➡", "hunt:shoot:right")],
        [("🏃 Свалить", "hunt:leave"), ("🔄 Обновить", "hunt:refresh")],
        [("⬅️ В меню", "hunt:exit")],
    )


async def _hunt_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.artifact_hunt import start_artifact_hunt

    result = start_artifact_hunt(storage, int(event.user_id))
    image = (result.payload or {}).get("hunt_image")
    if image and (result.payload or {}).get("hunt_active"):
        await _hunt_send_frame(bot, storage, event, image, caption=str((result.payload or {}).get("caption") or ""))
        return
    await bot.send_message(int(event.peer_id), result.text)


async def _hunt_send_frame(
    bot: VkMessenger, storage: Storage, event: VkEvent, image: Any, *, caption: str, note: str | None = None
) -> None:
    text = caption if not note else f"{caption}\n\n{note}"
    try:
        await bot.send_photo(int(event.peer_id), image, caption=text, reply_markup=_hunt_keyboard(), fallback_text=text)
    except Exception as exc:
        logger.warning("hunt frame failed: %s", exc)
        await bot.send_message(int(event.peer_id), text, reply_markup=_hunt_keyboard())


async def _hunt_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    from app.artifact_hunt import (
        abandon_artifact_hunt,
        clear_hunt_session,
        get_hunt_session,
        hunt_status_caption,
        move_artifact_hunt,
        render_hunt_for_player,
        shoot_artifact_hunt,
    )

    user_id = int(event.user_id)
    action = payload[len("hunt:"):].strip() if payload.startswith("hunt:") else payload
    if action == "open":
        await _hunt_open(bot, storage, event)
        return
    if action == "exit":
        await _menu(bot, storage, event)
        return
    if action == "leave":
        result = abandon_artifact_hunt(storage, user_id)
        await bot.send_message(int(event.peer_id), result.text)
        return
    if action == "refresh":
        session = get_hunt_session(storage, user_id)
        player = storage.get_character(user_id, refresh_energy=False)
        if session is None or player is None:
            await bot.send_message(int(event.peer_id), "Активной вылазки по артефактам нет.")
            return
        try:
            image = render_hunt_for_player(storage, user_id, session, player)
        except Exception as exc:
            logger.warning("hunt render failed: %s", exc)
            await bot.send_message(int(event.peer_id), "Не удалось отрисовать поле.")
            return
        await _hunt_send_frame(bot, storage, event, image, caption=hunt_status_caption(session, player))
        return
    if action.startswith("shoot:"):
        direction = action[len("shoot:"):]
        if direction not in ("up", "down", "left", "right"):
            return
        result = shoot_artifact_hunt(storage, user_id, direction)
    elif action in ("up", "down", "left", "right"):
        result = move_artifact_hunt(storage, user_id, action)
    else:
        return
    result_payload = result.payload or {}
    if result_payload.get("hunt_dead"):
        await bot.send_message(int(event.peer_id), result.text)
        clear_hunt_session(storage, user_id)
        await _maybe_respawn_dead(bot, storage, event)
        return
    image = result_payload.get("hunt_image")
    if result_payload.get("hunt_done") or result_payload.get("hunt_active") is False:
        await bot.send_message(int(event.peer_id), result.text)
        return
    if image:
        await _hunt_send_frame(
            bot,
            storage,
            event,
            image,
            caption=str(result_payload.get("caption") or ""),
            note=str(result_payload.get("move_note") or result.text),
        )
        return
    await bot.send_message(int(event.peer_id), result.text)


# ---------------------------------------------------------------------------
# Поиск хабара (app/stash_hunt — мини-игра схрона)
# ---------------------------------------------------------------------------


def _stash_keyboard():
    return inline_keyboard(
        [("⬆️ Вперёд", "sthunt:up")],
        [("⬅️ Влево", "sthunt:left"), ("⬇️ Назад", "sthunt:down"), ("➡️ Вправо", "sthunt:right")],
        [("🏃 Уйти", "sthunt:leave"), ("🔄 Обновить", "sthunt:refresh")],
        [("⬅️ В меню", "sthunt:exit")],
    )


async def _stash_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.stash_hunt import start_stash_hunt

    result = start_stash_hunt(storage, int(event.user_id), source="found")
    image = (result.payload or {}).get("stash_image")
    if image and (result.payload or {}).get("stash_active"):
        note = result.text if (result.payload or {}).get("stash_started") else None
        await _stash_send_frame(
            bot,
            storage,
            event,
            image,
            caption=str((result.payload or {}).get("caption") or ""),
            note=note,
        )
        return
    await bot.send_message(int(event.peer_id), result.text)


async def _stash_send_frame(
    bot: VkMessenger, storage: Storage, event: VkEvent, image: Any, *, caption: str, note: str | None = None
) -> None:
    text = caption if not note else f"{caption}\n\n{note}"
    try:
        await bot.send_photo(int(event.peer_id), image, caption=text, reply_markup=_stash_keyboard(), fallback_text=text)
    except Exception as exc:
        logger.warning("stash frame failed: %s", exc)
        await bot.send_message(int(event.peer_id), text, reply_markup=_stash_keyboard())


async def _stash_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    from app.stash_hunt import (
        abandon_stash_hunt,
        clear_stash_session,
        get_stash_session,
        move_stash_hunt,
        render_stash_for_player,
        stash_status_caption,
    )

    user_id = int(event.user_id)
    action = payload[len("sthunt:"):].strip() if payload.startswith("sthunt:") else payload
    if action == "open":
        await _stash_open(bot, storage, event)
        return
    if action == "exit":
        await _menu(bot, storage, event)
        return
    if action == "leave":
        result = abandon_stash_hunt(storage, user_id)
        await bot.send_message(int(event.peer_id), result.text)
        return
    if action == "refresh":
        session = get_stash_session(storage, user_id)
        player = storage.get_character(user_id, refresh_energy=False)
        if session is None or player is None:
            await bot.send_message(int(event.peer_id), "Активного поиска хабара нет.")
            return
        try:
            image = render_stash_for_player(storage, user_id, session, player)
        except Exception as exc:
            logger.warning("stash render failed: %s", exc)
            await bot.send_message(int(event.peer_id), "Не удалось отрисовать схрон.")
            return
        await _stash_send_frame(bot, storage, event, image, caption=stash_status_caption(session, player))
        return
    if action not in ("up", "down", "left", "right"):
        return
    result = move_stash_hunt(storage, user_id, action)
    result_payload = result.payload or {}
    if result_payload.get("stash_dead"):
        await bot.send_message(int(event.peer_id), result.text)
        clear_stash_session(storage, user_id)
        await _maybe_respawn_dead(bot, storage, event)
        return
    image = result_payload.get("stash_image")
    if result_payload.get("stash_done") or result_payload.get("stash_active") is False:
        await bot.send_message(int(event.peer_id), result.text)
        return
    if image:
        await _stash_send_frame(
            bot,
            storage,
            event,
            image,
            caption=str(result_payload.get("caption") or ""),
            note=str(result_payload.get("move_note") or result.text),
        )
        return
    await bot.send_message(int(event.peer_id), result.text)


# ---------------------------------------------------------------------------
# События Зоны (app/game_logic — build_events_overview)
# ---------------------------------------------------------------------------


async def _events_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_events_overview
    from app.mini_events import help_event_is_joinable
    from app.special_events import special_event_is_joinable

    user_id = int(event.user_id)
    text = build_events_overview(storage)
    if special_event_is_joinable(storage, user_id):
        rows: list[list] = [[("⚡ Участвовать в особом событии", "events:join:special")]]
        rows.append([("🔄 Обновить", "events:refresh")])
        await bot.send_message(int(event.peer_id), text, reply_markup=inline_keyboard(*rows))
    elif help_event_is_joinable(storage, user_id):
        rows = [[("📻 Откликнуться на рацию", "events:join:help")], [("🔄 Обновить", "events:refresh")]]
        await bot.send_message(int(event.peer_id), text, reply_markup=inline_keyboard(*rows))
    else:
        await bot.send_message(int(event.peer_id), text)


async def _events_join(bot: VkMessenger, storage: Storage, event: VkEvent, kind: str) -> None:
    if kind == "special":
        from app.special_events import join_special_event

        result = join_special_event(storage, int(event.user_id))
    else:
        from app.mini_events import join_help_event

        result = join_help_event(storage, int(event.user_id))
    image = (result.payload or {}).get("mission_image")
    if image and (result.payload or {}).get("mission_active"):
        await _qmission_send_frame(
            bot,
            storage,
            event,
            image,
            caption=str((result.payload or {}).get("caption") or ""),
            note=result.text if (result.payload or {}).get("mission_started") else None,
        )
        return
    await bot.send_message(int(event.peer_id), result.text)


async def _events_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "join" and len(parts) >= 3:
        await _events_join(bot, storage, event, parts[2])
    else:
        await _events_open(bot, storage, event)


# ---------------------------------------------------------------------------
# Обучение новичка (app/game_logic — TUTORIAL_PAGES)
# ---------------------------------------------------------------------------


def _tutorial_nav_keyboard(page: int, total: int):
    """Навигация обучения как tutorial_keyboard в ТГ: только стрелки и счётчик."""
    nav: list[tuple[str, str]] = []
    if page > 0:
        nav.append(("⬅️", f"tutorial:page:{page - 1}"))
    nav.append((f"{page + 1}/{total}", f"tutorial:page:{page}"))
    if page + 1 < total:
        nav.append(("➡️", f"tutorial:page:{page + 1}"))
    return inline_keyboard(nav)


async def _tutorial_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_tutorial_page

    text, page, total = build_tutorial_page(0)
    await bot.send_message(int(event.peer_id), text, reply_markup=_tutorial_nav_keyboard(page, total))


async def _tutorial_page(bot: VkMessenger, storage: Storage, event: VkEvent, page: int) -> None:
    from app.game_logic import build_tutorial_page, claim_tutorial_completion

    text, safe_page, total = build_tutorial_page(int(page or 0))
    bonus_note = ""
    if safe_page == total - 1:
        # Как в ТГ: награда выдаётся автоматически, когда дошли до последней страницы.
        bonus_note = claim_tutorial_completion(storage, int(event.user_id))
    await bot.send_message(
        int(event.peer_id),
        text + bonus_note,
        reply_markup=_tutorial_nav_keyboard(safe_page, total),
    )


async def _tutorial_claim(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import TUTORIAL_PAGES, build_tutorial_page, claim_tutorial_completion

    text, _page, _total = build_tutorial_page(len(TUTORIAL_PAGES) - 1)
    bonus = claim_tutorial_completion(storage, int(event.user_id))
    await bot.send_message(int(event.peer_id), text + bonus)


async def _tutorial_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "page":
        page = int(parts[-1]) if len(parts) > 2 and parts[-1].isdigit() else 0
        await _tutorial_page(bot, storage, event, page)
    elif cmd == "claim":
        await _tutorial_claim(bot, storage, event)
    elif cmd == "exit":
        await _menu(bot, storage, event)


# ---------------------------------------------------------------------------
# Настройки уведомлений (app/game_logic — notify prefs)
# ---------------------------------------------------------------------------


# Метки переключателей уведомлений — как NOTIFY_PREF_TOGGLE_LABELS в ТГ.
_NOTIFY_TOGGLE_LABELS: dict[str, str] = {
    "emission": "☢️ Выброс",
    "death": "☠️ Смерть",
    "coop": "👥 Совместная вылазка",
    "garage": "🏚 Гараж (аренда)",
}


def _notify_keyboard(prefs: dict[str, bool]):
    rows: list[list] = []
    for key, label in _NOTIFY_TOGGLE_LABELS.items():
        mark = "✅" if prefs.get(key, True) else "🔕"
        rows.append([(f"{mark} {label}", f"notify:toggle:{key}")])
    rows.append([("⬅️ В меню", "notify:exit")])
    return inline_keyboard(*rows)


async def _notify_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_notify_prefs_text, get_notify_prefs

    prefs = get_notify_prefs(storage, int(event.user_id))
    await bot.send_message(int(event.peer_id), build_notify_prefs_text(prefs), reply_markup=_notify_keyboard(prefs))


async def _notify_toggle(bot: VkMessenger, storage: Storage, event: VkEvent, key: str) -> None:
    from app.game_logic import build_notify_prefs_text, get_notify_prefs, set_notify_pref

    prefs = get_notify_prefs(storage, int(event.user_id))
    prefs = set_notify_pref(storage, int(event.user_id), key, not prefs.get(key, True))
    await bot.send_message(int(event.peer_id), build_notify_prefs_text(prefs), reply_markup=_notify_keyboard(prefs))


async def _notify_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "toggle" and len(parts) >= 3:
        await _notify_toggle(bot, storage, event, parts[2])
    elif cmd == "exit":
        await _menu(bot, storage, event)
    else:
        await _notify_open(bot, storage, event)


# ---------------------------------------------------------------------------
# Журнал смертей (app/game_logic — death log)
# ---------------------------------------------------------------------------


async def _death_log_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_death_log_text

    text = build_death_log_text(storage, int(event.user_id))
    await bot.send_message(
        int(event.peer_id),
        text,
        reply_markup=inline_keyboard([("⬅️ В меню", "death:exit")]),
    )


async def _death_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    if (payload or "").startswith("death:exit"):
        await _menu(bot, storage, event)
        return
    await _death_log_open(bot, storage, event)


# ---------------------------------------------------------------------------
# Лаборатории «Предел-1/2/3» (app/lab_mission)
# ---------------------------------------------------------------------------


def _lab_stage_keyboard(stage: str):
    if stage == "transition":
        return inline_keyboard(
            [("⤵️ Спуститься", "labact:enter")],
            [("🏳 Покинуть лабу", "labact:leave")],
        )
    if stage == "ambush_choice":
        return inline_keyboard(
            [("⚔️ Дать бой", "labact:fight"), ("🏳 Сдаться", "labact:giveup")],
        )
    return inline_keyboard(
        [("⬆️", "labact:up"), ("⬅️", "labact:left"), ("⬇️", "labact:down"), ("➡️", "labact:right")],
        [("🔫 ⬆️", "labact:shoot:up")],
        [("🔫 ⬅️", "labact:shoot:left"), ("🔫 ⬇️", "labact:shoot:down"), ("🔫 ➡️", "labact:shoot:right")],
        [("🚪 Дверь/обыск", "labact:interact"), ("💊 Аптечка", "labact:medkit")],
        [("🏳 Покинуть лабу", "labact:leave"), ("🔄 Обновить", "labact:refresh")],
    )


async def _lab_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.lab_mission import LAB_DEFS, start_lab

    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None:
        await bot.send_message(int(event.peer_id), "Сначала создай персонажа.")
        return
    # Найти лабораторию, привязанную к текущей локации.
    lab_id = None
    for key, cfg in LAB_DEFS.items():
        if str(cfg.get("location") or "") == str(player.location):
            lab_id = key
            break
    result = start_lab(storage, user_id, lab_id or "limit1")
    image = (result.payload or {}).get("lab_image")
    if image and (result.payload or {}).get("lab_active"):
        await _lab_send_frame(
            bot,
            storage,
            event,
            image,
            caption=str((result.payload or {}).get("caption") or ""),
            note=result.text if (result.payload or {}).get("lab_started") else None,
            stage=str((result.payload or {}).get("lab_stage") or "combat"),
        )
        return
    await bot.send_message(int(event.peer_id), result.text)


async def _lab_send_frame(
    bot: VkMessenger,
    storage: Storage,
    event: VkEvent,
    image: Any,
    *,
    caption: str,
    note: str | None = None,
    stage: str = "combat",
) -> None:
    from app.lab_mission import get_lab_session

    text = caption if not note else f"{caption}\n\n{note}"
    session = get_lab_session(storage, int(event.user_id))
    if session is not None:
        stage = str(session.stage or stage or "combat")
    try:
        await bot.send_photo(int(event.peer_id), image, caption=text, reply_markup=_lab_stage_keyboard(stage), fallback_text=text)
    except Exception as exc:
        logger.warning("lab frame failed: %s", exc)
        await bot.send_message(int(event.peer_id), text, reply_markup=_lab_stage_keyboard(stage))


async def _lab_apply(bot: VkMessenger, storage: Storage, event: VkEvent, action: str) -> None:
    from app.lab_mission import (
        get_lab_session,
        lab_abandon,
        lab_ambush_choice,
        lab_enter_level,
        lab_interact,
        lab_medkit,
        lab_move,
        lab_shoot,
        render_lab_for_player,
    )

    user_id = int(event.user_id)
    if action == "abandon":
        result = lab_abandon(storage, user_id)
        await bot.send_message(int(event.peer_id), result.text)
        await _menu(bot, storage, event)
        return
    if action == "refresh":
        session = get_lab_session(storage, user_id)
        player = storage.get_character(user_id, refresh_energy=False)
        if session is None or player is None:
            await bot.send_message(int(event.peer_id), "Активной вылазки в лаборатории нет.")
            return
        try:
            image = render_lab_for_player(storage, user_id, session, player)
        except Exception as exc:
            logger.warning("lab render failed: %s", exc)
            await bot.send_message(int(event.peer_id), "Не удалось отрисовать лабораторию.")
            return
        from app.lab_mission import lab_status_caption

        await _lab_send_frame(bot, storage, event, image, caption=lab_status_caption(session, player))
        return
    if action == "medkit":
        result = lab_medkit(storage, user_id)
    elif action == "interact":
        result = lab_interact(storage, user_id)
    elif action == "enter":
        result = lab_enter_level(storage, user_id)
    elif action == "fight":
        result = lab_ambush_choice(storage, user_id, give_up=False)
    elif action == "giveup":
        result = lab_ambush_choice(storage, user_id, give_up=True)
    elif action.startswith("shoot:"):
        direction = action[len("shoot:"):]
        if direction not in ("up", "down", "left", "right"):
            return
        result = lab_shoot(storage, user_id, direction)
    elif action in ("up", "down", "left", "right"):
        result = lab_move(storage, user_id, action)
    else:
        return

    result_payload = result.payload or {}
    if result_payload.get("lab_done") or result_payload.get("lab_dead"):
        await bot.send_message(int(event.peer_id), result.text)
        await _menu(bot, storage, event)
        return
    image = result_payload.get("lab_image")
    if image and result_payload.get("lab_active"):
        await _lab_send_frame(
            bot,
            storage,
            event,
            image,
            caption=str(result_payload.get("caption") or ""),
            note=str(result_payload.get("lab_note") or result.text),
            stage=str(result_payload.get("lab_stage") or "combat"),
        )
        return
    if result_payload.get("lab_active"):
        # Действия без картинки (записки, переходы) — рисуем текущее поле.
        session = get_lab_session(storage, user_id)
        player = storage.get_character(user_id, refresh_energy=False)
        if session is not None and player is not None:
            try:
                image = render_lab_for_player(storage, user_id, session, player)
                from app.lab_mission import lab_status_caption

                await _lab_send_frame(
                    bot,
                    storage,
                    event,
                    image,
                    caption=lab_status_caption(session, player),
                    note=result.text,
                )
                return
            except Exception:
                pass
    await bot.send_message(int(event.peer_id), result.text)


async def _lab_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    action = payload[len("labact:"):].strip() if payload.startswith("labact:") else payload
    await _lab_apply(bot, storage, event, action)


# ---------------------------------------------------------------------------
# Экономика/Барахолка (app/game_logic + app/storage)
# ---------------------------------------------------------------------------


def _eco_root_keyboard():
    return inline_keyboard(
        [("📦 Биржа — открытые лоты", "eco:exchange:list:all")],
        [("🔫 Рынок — экипировка", "eco:market:list")],
        [("➕ Биржа: выставить лот", "eco:auction:custom:choose")],
        [("➕ Рынок: выставить ствол/броню", "eco:market:create:choose")],
        [("📤 Отменить свой лот", "eco:auction:cancel:mine")],
        [("🚚 Контрабанда", "eco:smuggle:menu")],
        [("⬅️ В меню", "eco:exit")],
    )


async def _eco_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_economy_overview

    text = build_economy_overview(storage, int(event.user_id))
    await bot.send_message(int(event.peer_id), text, reply_markup=_eco_root_keyboard())


async def _eco_exchange_list(bot: VkMessenger, storage: Storage, event: VkEvent, category: str) -> None:
    from app.game_logic import EXCHANGE_CATEGORIES, EXCHANGE_CATEGORY_LABELS, build_exchange_lots_overview

    if category not in EXCHANGE_CATEGORIES:
        category = "all"
    text, lots = build_exchange_lots_overview(storage, int(event.user_id), limit=12, category=None if category == "all" else category)
    rows: list[list] = []
    for lot in lots:
        label = f"Ячейка №{lot.get('id')} · {lot.get('title')} ×{lot.get('amount')} · {lot.get('price')} RU"
        if len(label) > 45:
            label = label[:44] + "…"
        if lot.get("is_own"):
            rows.append([(f"🛑 Снять: {label}", f"eco:auction:cancel:{lot.get('id')}")])
        else:
            rows.append([(label, f"eco:auction:buy:{lot.get('id')}")])
    nav: list[tuple[str, str]] = []
    for key in EXCHANGE_CATEGORIES:
        mark = "▪️" if key == category else key
        nav.append((EXCHANGE_CATEGORY_LABELS.get(key, key), f"eco:exchange:list:{key}"))
    rows.append(nav)
    rows.append([("⬅️ В меню барахолки", "eco:menu")])
    await bot.send_message(int(event.peer_id), text, reply_markup=inline_keyboard(*rows))


async def _eco_auction_buy(bot: VkMessenger, storage: Storage, event: VkEvent, lot_id: str) -> None:
    from app.game_logic import preview_bazaar_lot

    try:
        lot_id_i = int(lot_id)
    except (TypeError, ValueError):
        return
    result = preview_bazaar_lot(storage, lot_id_i, market=False)
    if not result.ok:
        await bot.send_message(int(event.peer_id), result.text)
        return
    price = int((result.payload or {}).get("price") or 0)
    await bot.send_message(
        int(event.peer_id),
        result.text + f"\n\nПокупаем за {price} RU?",
        reply_markup=inline_keyboard(
            [("✅ Подтвердить покупку", f"eco:auction:confirm:{lot_id}")],
            [("⬅️ К лотам", "eco:exchange:list:all")],
        ),
    )


async def _eco_auction_confirm(bot: VkMessenger, storage: Storage, event: VkEvent, lot_id: str) -> None:
    from app.game_logic import buy_exchange_lot

    try:
        lot_id_i = int(lot_id)
    except (TypeError, ValueError):
        return
    result = buy_exchange_lot(storage, int(event.user_id), lot_id_i)
    await bot.send_message(int(event.peer_id), result.text)
    await _eco_exchange_list(bot, storage, event, "all")


async def _eco_auction_cancel(bot: VkMessenger, storage: Storage, event: VkEvent, lot_id: str | None) -> None:
    if lot_id and lot_id != "mine":
        from app.game_logic import cancel_own_auction

        try:
            result = cancel_own_auction(storage, int(event.user_id), int(lot_id))
        except (TypeError, ValueError):
            return
    else:
        from app.game_logic import cancel_own_first_auction

        result = cancel_own_first_auction(storage, int(event.user_id))
    await bot.send_message(int(event.peer_id), result.text)
    await _eco_exchange_list(bot, storage, event, "all")


async def _eco_auction_custom_choose(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import list_sellable_exchange_items

    items = list_sellable_exchange_items(storage, int(event.user_id))
    if not items:
        await bot.send_message(int(event.peer_id), "Нет предметов, которые можно выставить на биржу.")
        return
    rows: list[list] = []
    for item in items[:15]:
        rows.append([(f"{item.get('title')} ×{item.get('amount')}", f"eco:auction:custom:{item.get('item_key')}")])
    rows.append([("⬅️ В меню барахолки", "eco:menu")])
    await bot.send_message(int(event.peer_id), "Что выставляем на биржу?", reply_markup=inline_keyboard(*rows))


async def _eco_auction_custom_pick(bot: VkMessenger, storage: Storage, event: VkEvent, item_key: str) -> None:
    from app.game_logic import ITEM_LABELS

    fsm = VkFSM(int(event.user_id))
    await fsm.set_state("eco_auction_price")
    await fsm.update_data(eco_item_key=item_key, eco_market=False)
    label = ITEM_LABELS.get(item_key, item_key)
    await bot.send_message(
        int(event.peer_id),
        f"Выставь «{label}» на биржу. Введи цену в RU (целое число):",
    )


async def _eco_market_create_choose(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import list_sellable_market_equipment

    items = list_sellable_market_equipment(storage, int(event.user_id))
    if not items:
        await bot.send_message(int(event.peer_id), "Нет оружия/брони для продажи на рынке.")
        return
    rows: list[list] = []
    for item in items[:15]:
        rows.append([(f"{item.get('title')} ×{item.get('amount')}", f"eco:market:create:{item.get('item_key')}")])
    rows.append([("⬅️ В меню барахолки", "eco:menu")])
    await bot.send_message(int(event.peer_id), "Что выставляем на рынок?", reply_markup=inline_keyboard(*rows))


async def _eco_market_create_pick(bot: VkMessenger, storage: Storage, event: VkEvent, item_key: str) -> None:
    from app.game_logic import ITEM_LABELS

    fsm = VkFSM(int(event.user_id))
    await fsm.set_state("eco_auction_price")
    await fsm.update_data(eco_item_key=item_key, eco_market=True)
    label = ITEM_LABELS.get(item_key, item_key)
    await bot.send_message(
        int(event.peer_id),
        f"Выставь «{label}» на рынок. Введи цену в RU (целое число):",
    )


async def _eco_market_list(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_market_lots_overview

    text, lots = build_market_lots_overview(storage, int(event.user_id), limit=12)
    rows: list[list] = []
    for lot in lots:
        label = f"Ствол/броня №{lot.get('id')} · {lot.get('title')} ×{lot.get('amount')} · {lot.get('price')} RU"
        if len(label) > 45:
            label = label[:44] + "…"
        rows.append([(label, f"eco:market:buy:{lot.get('id')}")])
    rows.append([("⬅️ В меню барахолки", "eco:menu")])
    await bot.send_message(int(event.peer_id), text, reply_markup=inline_keyboard(*rows))


async def _eco_market_buy(bot: VkMessenger, storage: Storage, event: VkEvent, lot_id: str) -> None:
    from app.game_logic import preview_bazaar_lot

    try:
        lot_id_i = int(lot_id)
    except (TypeError, ValueError):
        return
    result = preview_bazaar_lot(storage, lot_id_i, market=True)
    if not result.ok:
        await bot.send_message(int(event.peer_id), result.text)
        return
    price = int((result.payload or {}).get("price") or 0)
    await bot.send_message(
        int(event.peer_id),
        result.text + f"\n\nПокупаем за {price} RU?",
        reply_markup=inline_keyboard(
            [("✅ Подтвердить покупку", f"eco:market:confirm:{lot_id}")],
            [("⬅️ К рынку", "eco:market:list")],
        ),
    )


async def _eco_market_confirm(bot: VkMessenger, storage: Storage, event: VkEvent, lot_id: str) -> None:
    from app.game_logic import buy_market_lot

    try:
        lot_id_i = int(lot_id)
    except (TypeError, ValueError):
        return
    result = buy_market_lot(storage, int(event.user_id), lot_id_i)
    await bot.send_message(int(event.peer_id), result.text)
    await _eco_market_list(bot, storage, event)


async def _eco_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "menu" or cmd == "exit":
        if cmd == "exit":
            await _menu(bot, storage, event)
        else:
            await _eco_open(bot, storage, event)
    elif cmd == "exchange" and len(parts) >= 4:
        await _eco_exchange_list(bot, storage, event, parts[3])
    elif cmd == "auction" and len(parts) >= 3:
        sub = parts[2]
        rest = parts[3] if len(parts) > 3 else None
        if sub == "buy":
            await _eco_auction_buy(bot, storage, event, rest or "0")
        elif sub == "confirm":
            await _eco_auction_confirm(bot, storage, event, rest or "0")
        elif sub == "cancel":
            await _eco_auction_cancel(bot, storage, event, rest)
        elif sub == "custom" and rest and rest != "choose":
            await _eco_auction_custom_pick(bot, storage, event, rest)
        elif sub == "custom":
            await _eco_auction_custom_choose(bot, storage, event)
    elif cmd == "market" and len(parts) >= 3:
        sub = parts[2]
        rest = parts[3] if len(parts) > 3 else None
        if sub == "list":
            await _eco_market_list(bot, storage, event)
        elif sub == "buy":
            await _eco_market_buy(bot, storage, event, rest or "0")
        elif sub == "confirm":
            await _eco_market_confirm(bot, storage, event, rest or "0")
        elif sub == "create" and rest and rest != "choose":
            await _eco_market_create_pick(bot, storage, event, rest)
        elif sub == "create":
            await _eco_market_create_choose(bot, storage, event)
        elif sub == "cancel":
            from app.game_logic import cancel_own_first_market_lot

            result = cancel_own_first_market_lot(storage, int(event.user_id))
            await bot.send_message(int(event.peer_id), result.text)
            await _eco_market_list(bot, storage, event)
    elif cmd == "smuggle":
        await _smuggle_menu(bot, storage, event)


async def _eco_price_input(bot: VkMessenger, storage: Storage, event: VkEvent, text: str) -> None:
    """Обработка ввода цены для лота биржи/рынка (FSM)."""
    fsm = VkFSM(int(event.user_id))
    data = await fsm.get_data()
    item_key = str(data.get("eco_item_key") or "")
    market = bool(data.get("eco_market"))
    await fsm.clear()
    raw = (text or "").strip().replace(" ", "").replace("\u00a0", "")
    if not raw.isdigit():
        await bot.send_message(int(event.peer_id), "Введи целое число — цену в RU:")
        return
    price = int(raw)
    if price < 1 or price > 10_000_000:
        await bot.send_message(int(event.peer_id), "Цена должна быть от 1 до 10 000 000 RU.")
        return
    if market:
        from app.game_logic import create_market_lot

        result = create_market_lot(storage, int(event.user_id), item_key, 1, price=price)
    else:
        from app.game_logic import create_custom_exchange_lot

        result = create_custom_exchange_lot(storage, int(event.user_id), item_key, 1, price)
    await bot.send_message(int(event.peer_id), result.text)
    await _eco_open(bot, storage, event)


# ---------------------------------------------------------------------------
# Контрабанда (app/game_logic + app/smuggle_mission)
# ---------------------------------------------------------------------------


async def _smuggle_menu(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import list_smuggling_destinations
    from app.smuggle_mission import get_smuggle_session

    user_id = int(event.user_id)
    from app.game_logic import build_smuggling_overview, get_active_smuggling

    text = build_smuggling_overview(storage, user_id)
    rows: list[list] = []
    if get_smuggle_session(storage, user_id) is not None or get_active_smuggling(storage, user_id):
        rows.append([("⏱ Статус", "eco:smuggle:status"), ("🏳 Бросить рейс", "eco:smuggle:abandon")])
    for dest in list_smuggling_destinations(storage, user_id)[:12]:
        rows.append([(f"🚚 Сдать в «{dest}»", f"eco:smuggle:to:{dest}")])
    rows.append([("⬅️ В меню барахолки", "eco:menu")])
    await bot.send_message(int(event.peer_id), text, reply_markup=inline_keyboard(*rows))


async def _smuggle_status(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_smuggling_overview

    text = build_smuggling_overview(storage, int(event.user_id))
    await bot.send_message(int(event.peer_id), text, reply_markup=inline_keyboard([("⬅️ Назад", "eco:smuggle:menu")]))


async def _smuggle_abandon(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import abandon_smuggling_run

    result = abandon_smuggling_run(storage, int(event.user_id))
    await bot.send_message(int(event.peer_id), result.text)
    await _smuggle_menu(bot, storage, event)


async def _smuggle_go(bot: VkMessenger, storage: Storage, event: VkEvent, destination: str) -> None:
    from app.game_logic import start_smuggling_run

    result = start_smuggling_run(storage, int(event.user_id), destination, transport_mode=None)
    image = (result.payload or {}).get("mission_image")
    if image and (result.payload or {}).get("mission_active"):
        await _smuggle_send_frame(
            bot,
            storage,
            event,
            image,
            caption=str((result.payload or {}).get("caption") or ""),
            note=result.text if (result.payload or {}).get("mission_started") else None,
        )
        return
    await bot.send_message(int(event.peer_id), result.text)


async def _smuggle_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    sub = parts[2] if len(parts) > 2 else ""
    if sub == "menu":
        await _smuggle_menu(bot, storage, event)
    elif sub == "status":
        await _smuggle_status(bot, storage, event)
    elif sub == "abandon":
        await _smuggle_abandon(bot, storage, event)
    elif sub == "to" and len(parts) >= 4:
        await _smuggle_go(bot, storage, event, ":".join(parts[3:]))


async def _smuggle_send_frame(
    bot: VkMessenger, storage: Storage, event: VkEvent, image: Any, *, caption: str, note: str | None = None
) -> None:
    text = caption if not note else f"{caption}\n\n{note}"
    try:
        await bot.send_photo(int(event.peer_id), image, caption=text, reply_markup=_smuggle_kb(), fallback_text=text)
    except Exception as exc:
        logger.warning("smuggle frame failed: %s", exc)
        await bot.send_message(int(event.peer_id), text, reply_markup=_smuggle_kb())


def _smuggle_kb():
    return inline_keyboard(
        [("⬆️", "smission:up")],
        [("⬅️", "smission:left"), ("⬇️", "smission:down"), ("➡️", "smission:right")],
        [("🔄 Обновить", "smission:refresh"), ("🏳 Бросить", "smission:abandon")],
    )


async def _smission_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    from app.smuggle_mission import (
        abandon_smuggle_mission,
        get_smuggle_session,
        move_smuggle_mission,
        render_smuggle_for_player,
        smuggle_status_caption,
    )

    user_id = int(event.user_id)
    action = payload[len("smission:"):].strip() if payload.startswith("smission:") else payload
    if action == "abandon":
        result = abandon_smuggle_mission(storage, user_id)
        await bot.send_message(int(event.peer_id), result.text)
        return
    if action == "refresh":
        session = get_smuggle_session(storage, user_id)
        player = storage.get_character(user_id, refresh_energy=False)
        if session is None or player is None:
            await bot.send_message(int(event.peer_id), "Активного рейса нет.")
            return
        try:
            image = render_smuggle_for_player(storage, user_id, session, player)
        except Exception as exc:
            logger.warning("smuggle render failed: %s", exc)
            await bot.send_message(int(event.peer_id), "Не удалось отрисовать карту рейса.")
            return
        await _smuggle_send_frame(bot, storage, event, image, caption=smuggle_status_caption(session, player))
        return
    if action not in ("up", "down", "left", "right"):
        return
    result = move_smuggle_mission(storage, user_id, action)
    result_payload = result.payload or {}
    if result_payload.get("mission_dead"):
        await bot.send_message(int(event.peer_id), result.text)
        await _maybe_respawn_dead(bot, storage, event)
        return
    if result_payload.get("mission_travel_started"):
        await bot.send_message(int(event.peer_id), result.text)
        return
    if result_payload.get("mission_done") or result_payload.get("mission_active") is False:
        await bot.send_message(int(event.peer_id), result.text)
        await _smuggle_menu(bot, storage, event)
        return
    image = result_payload.get("mission_image")
    if image:
        await _smuggle_send_frame(
            bot,
            storage,
            event,
            image,
            caption=str(result_payload.get("caption") or ""),
            note=str(result_payload.get("move_note") or result.text),
        )
        return
    await bot.send_message(int(event.peer_id), result.text)


# ---------------------------------------------------------------------------
# Личный хабар (app/game_logic — personal stash)
# ---------------------------------------------------------------------------


async def _stash_menu(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import format_personal_stash, faction_home_base, is_traveling

    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None:
        await bot.send_message(int(event.peer_id), "Сначала создай персонажа.")
        return
    text = format_personal_stash(storage, user_id)
    at_home = str(player.location) == faction_home_base(player.faction) and not is_traveling(player)
    if at_home:
        text += "\n\nТы на базе — можно складывать и забирать."
    else:
        text += "\n\nХабар доступен только на домашней базе."
    rows: list[list] = []
    if at_home:
        rows.append([("📥 Положить", "stash:putlist:0"), ("📤 Забрать", "stash:takelist:0")])
    rows.append([("⬅️ В меню", "stash:exit")])
    await bot.send_message(int(event.peer_id), text, reply_markup=inline_keyboard(*rows))


async def _stash_putlist(bot: VkMessenger, storage: Storage, event: VkEvent, page: int = 0) -> None:
    from app.game_logic import list_stash_deposit_buttons

    player = storage.get_character(int(event.user_id), refresh_energy=False)
    if player is None:
        return
    buttons, safe_page, total = list_stash_deposit_buttons(player, page=int(page or 0))
    rows: list[list] = []
    for label, cb in buttons:
        rows.append([(label, str(cb))])
    nav: list[tuple[str, str]] = []
    if safe_page > 0:
        nav.append(("⬅️", f"stash:putlist:{safe_page - 1}"))
    if safe_page < total - 1:
        nav.append(("➡️", f"stash:putlist:{safe_page + 1}"))
    nav.append(("⬅️ К хабару", "stash:menu"))
    rows.append(nav)
    await bot.send_message(int(event.peer_id), f"Что кладём? ({safe_page + 1}/{total})", reply_markup=inline_keyboard(*rows))


async def _stash_takelist(bot: VkMessenger, storage: Storage, event: VkEvent, page: int = 0) -> None:
    from app.game_logic import list_stash_withdraw_buttons

    buttons, safe_page, total = list_stash_withdraw_buttons(storage, int(event.user_id), page=int(page or 0))
    rows: list[list] = []
    for label, cb in buttons:
        rows.append([(label, str(cb))])
    nav: list[tuple[str, str]] = []
    if safe_page > 0:
        nav.append(("⬅️", f"stash:takelist:{safe_page - 1}"))
    if safe_page < total - 1:
        nav.append(("➡️", f"stash:takelist:{safe_page + 1}"))
    nav.append(("⬅️ К хабару", "stash:menu"))
    rows.append(nav)
    await bot.send_message(int(event.peer_id), f"Что забираем? ({safe_page + 1}/{total})", reply_markup=inline_keyboard(*rows))


async def _stash_qty(bot: VkMessenger, storage: Storage, event: VkEvent, direction: str, item_key: str) -> None:
    from app.game_logic import ITEM_LABELS

    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None:
        return
    label = ITEM_LABELS.get(item_key, item_key)
    if direction == "put":
        have = int((player.inventory or {}).get(item_key, 0))
    else:
        stash = storage.get_personal_stash(user_id)
        have = int(stash.get(item_key, 0))
    if have <= 0:
        await bot.send_message(int(event.peer_id), f"«{label}» нет.")
        return
    amounts: list[int] = [1]
    if have >= 5:
        amounts.append(5)
    if have >= 10:
        amounts.append(10)
    if have > 10:
        amounts.append(have)
    rows: list[list] = []
    for qty in amounts:
        rows.append([(f"{qty} шт.", f"stash:{direction}qty:{item_key}:{qty}")])
    rows.append([("⬅️ Назад", f"stash:{direction}list:0")])
    await bot.send_message(
        int(event.peer_id),
        f"«{label}» — сколько {('положить' if direction == 'put' else 'забрать')}? Есть {have}.",
        reply_markup=inline_keyboard(*rows),
    )


async def _stash_qty_apply(bot: VkMessenger, storage: Storage, event: VkEvent, direction: str, item_key: str, qty: int) -> None:
    if direction == "put":
        from app.game_logic import deposit_to_personal_stash

        result = deposit_to_personal_stash(storage, int(event.user_id), item_key, int(qty))
    else:
        from app.game_logic import withdraw_from_personal_stash

        result = withdraw_from_personal_stash(storage, int(event.user_id), item_key, int(qty))
    await bot.send_message(int(event.peer_id), result.text)
    if result.ok:
        await _stash_menu(bot, storage, event)


async def _personal_stash_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "menu":
        await _stash_menu(bot, storage, event)
    elif cmd == "exit":
        await _menu(bot, storage, event)
    elif cmd == "putlist":
        page = int(parts[-1]) if len(parts) > 2 and parts[-1].isdigit() else 0
        await _stash_putlist(bot, storage, event, page)
    elif cmd == "takelist":
        page = int(parts[-1]) if len(parts) > 2 and parts[-1].isdigit() else 0
        await _stash_takelist(bot, storage, event, page)
    elif cmd == "put" and len(parts) >= 3:
        await _stash_qty(bot, storage, event, "put", parts[2])
    elif cmd == "take" and len(parts) >= 3:
        await _stash_qty(bot, storage, event, "take", parts[2])
    elif cmd == "putqty" and len(parts) >= 4:
        await _stash_qty_apply(bot, storage, event, "put", parts[2], int(parts[3]))
    elif cmd == "takeqty" and len(parts) >= 4:
        await _stash_qty_apply(bot, storage, event, "take", parts[2], int(parts[3]))


# ---------------------------------------------------------------------------
# Действия зон локации (app/location_zones + app/location_walk)
# ---------------------------------------------------------------------------


async def _locmap_resupply(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.location_zones import resupply_equipment

    player = storage.get_character(int(event.user_id), refresh_energy=False)
    if player is None:
        return
    result = resupply_equipment(storage, int(event.user_id), str(player.location))
    await bot.send_message(int(event.peer_id), result.text)
    await _walk_render(bot, storage, event, storage.get_character(int(event.user_id), refresh_energy=False) or player)


async def _locmap_search(bot: VkMessenger, storage: Storage, event: VkEvent, zone_id: str) -> None:
    from app.location_zones import start_search_zone

    player = storage.get_character(int(event.user_id), refresh_energy=False)
    if player is None:
        return
    result = start_search_zone(storage, int(event.user_id), str(player.location), zone_id)
    image = (result.payload or {}).get("stash_image")
    if image and (result.payload or {}).get("stash_active"):
        await _stash_send_frame(
            bot,
            storage,
            event,
            image,
            caption=str((result.payload or {}).get("caption") or ""),
            note=result.text if (result.payload or {}).get("stash_started") else None,
        )
        return
    await bot.send_message(int(event.peer_id), result.text)


async def _locmap_lab(bot: VkMessenger, storage: Storage, event: VkEvent, zone_id: str) -> None:
    from app.lab_mission import start_lab

    lab_id = zone_id[len("lab_") :] if zone_id.startswith("lab_") else "limit1"
    result = start_lab(storage, int(event.user_id), lab_id)
    image = (result.payload or {}).get("lab_image")
    if image and (result.payload or {}).get("lab_active"):
        await _lab_send_frame(
            bot,
            storage,
            event,
            image,
            caption=str((result.payload or {}).get("caption") or ""),
            note=result.text if (result.payload or {}).get("lab_started") else None,
            stage=str((result.payload or {}).get("lab_stage") or "combat"),
        )
        return
    await bot.send_message(int(event.peer_id), result.text)


async def _locmap_zonemap(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.zone_map import build_zone_map_image

    player = storage.get_character(int(event.user_id), refresh_energy=False)
    if player is None:
        return
    caption = (
        "Карта Зоны.\n"
        "Метка: тип территории; контроль; сила NPC.\n"
        "Кольцо = тип точки, заливка = фракция."
    )
    kb = inline_keyboard(
        [("🗺 Переходы", "travel:open"), ("🔄 Обновить", "locmap:zonemap")],
        [("⬅️ В меню", "walk:exit")],
    )
    try:
        image = build_zone_map_image(
            storage.get_locations(),
            current_location=str(player.location),
            player_faction=player.faction,
            show_markers=True,
        )
        await bot.send_photo(
            int(event.peer_id),
            image,
            caption=caption,
            reply_markup=kb,
            fallback_text=caption,
        )
    except Exception as exc:
        logger.warning("zone map failed: %s", exc)
        await bot.send_message(int(event.peer_id), caption, reply_markup=kb)


async def _locmap_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "vendor":
        await _trader_menu(bot, storage, event)
    elif cmd == "quests":
        await _quest_open(bot, storage, event)
    elif cmd == "resupply":
        await _locmap_resupply(bot, storage, event)
    elif cmd == "anomaly":
        await _hunt_open(bot, storage, event)
    elif cmd == "bazaar":
        await _eco_open(bot, storage, event)
    elif cmd == "zonemap":
        await _locmap_zonemap(bot, storage, event)
    elif cmd == "search" and len(parts) >= 3:
        await _locmap_search(bot, storage, event, parts[2])
    elif cmd == "lab" and len(parts) >= 3:
        await _locmap_lab(bot, storage, event, parts[2])
    elif cmd == "secrettrader":
        await _secret_trader_menu(bot, storage, event)
    elif cmd == "forester":
        await _forester_sell(bot, storage, event)
    elif cmd == "lair":
        await _lair_open(bot, storage, event)
    elif cmd == "blockpost":
        await _blockpost_fight(bot, storage, event)


# ---------------------------------------------------------------------------
# Тайный торговец (app/secret_trader), лесник, логово, блокпост
# ---------------------------------------------------------------------------


async def _secret_trader_menu(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.secret_trader import INTEL_ITEM_KEYS, ITEM_LABELS, secret_trader_menu_text

    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None:
        await bot.send_message(int(event.peer_id), "Сначала создай персонажа.")
        return
    text = secret_trader_menu_text(storage, user_id)
    rows: list[list] = []
    for key in INTEL_ITEM_KEYS:
        count = int((player.inventory or {}).get(key, 0))
        if count > 0:
            rows.append([(f"💽 Продать {ITEM_LABELS.get(key, key)} ×{count}", f"secrettrader:sell:{key}")])
    intel_total = sum(int((player.inventory or {}).get(k, 0)) for k in INTEL_ITEM_KEYS)
    if intel_total > 0:
        rows.append([("💽 Продать всю инту", "secrettrader:sellall")])
    rows.append([("⬅️ В меню", "secrettrader:exit")])
    if not rows[:-1]:
        await bot.send_message(int(event.peer_id), text)
        return
    await bot.send_message(int(event.peer_id), text, reply_markup=inline_keyboard(*rows))


async def _secret_trader_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    from app.secret_trader import sell_all_intel, sell_intel_item

    user_id = int(event.user_id)
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "sellall":
        result = sell_all_intel(storage, user_id)
        await bot.send_message(int(event.peer_id), result.text)
    elif cmd == "sell" and len(parts) >= 3:
        result = sell_intel_item(storage, user_id, parts[2], 1)
        await bot.send_message(int(event.peer_id), result.text)
    else:
        await _menu(bot, storage, event)
        return
    await _secret_trader_menu(bot, storage, event)


async def _forester_sell(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import ITEM_LABELS, SHOP_ITEMS

    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None:
        return
    trophy_keys = [
        k
        for k in SHOP_ITEMS
        if k.startswith("mutant_") and int(SHOP_ITEMS[k].get("sell_price", 0)) > 0
    ]
    sold: list[str] = []
    total = 0
    for key in trophy_keys:
        count = int((player.inventory or {}).get(key, 0))
        if count <= 0:
            continue
        price = int(SHOP_ITEMS[key]["sell_price"]) * 3
        storage.remove_item(user_id, key, count)
        storage.change_money(user_id, price * count)
        total += price * count
        sold.append(f"{ITEM_LABELS.get(key, key)} ×{count} — {price * count} RU")
    if not sold:
        await bot.send_message(
            int(event.peer_id),
            "🪵 Лесник: «Трофеев мутантов нет. Принеси клыки и шкуры — заплачу втрое.»",
        )
        return
    await bot.send_message(int(event.peer_id), "🪵 Лесник забрал трофеи:\n" + "\n".join(sold) + f"\nИтого: {total} RU.")


def _lair_kb():
    return inline_keyboard(
        [("⬆️ Вперёд", "lair:up")],
        [("⬅️ Влево", "lair:left"), ("⬇️ Назад", "lair:down"), ("➡️ Вправо", "lair:right")],
        [("🔫 ⬆️", "lair:shoot:up")],
        [("🔫 ⬅️", "lair:shoot:left"), ("🔫 ⬇️", "lair:shoot:down"), ("🔫 ➡️", "lair:shoot:right")],
        [("🚪 Бросить", "lair:leave"), ("🔄 Обновить", "lair:refresh")],
    )


async def _lair_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.lair_hunt import start_lair_hunt

    result = start_lair_hunt(storage, int(event.user_id))
    image = (result.payload or {}).get("lair_image")
    if image and (result.payload or {}).get("lair_active"):
        await _lair_send_frame(bot, storage, event, image, caption=str((result.payload or {}).get("caption") or ""))
        return
    await bot.send_message(int(event.peer_id), result.text)


async def _lair_send_frame(
    bot: VkMessenger, storage: Storage, event: VkEvent, image: Any, *, caption: str, note: str | None = None
) -> None:
    text = caption if not note else f"{caption}\n\n{note}"
    try:
        await bot.send_photo(int(event.peer_id), image, caption=text, reply_markup=_lair_kb(), fallback_text=text)
    except Exception as exc:
        logger.warning("lair frame failed: %s", exc)
        await bot.send_message(int(event.peer_id), text, reply_markup=_lair_kb())


async def _lair_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    from app.lair_hunt import (
        abandon_lair_hunt,
        get_lair_session,
        lair_status_caption,
        move_lair_hunt,
        render_lair_for_player,
        shoot_lair_hunt,
    )

    user_id = int(event.user_id)
    action = payload[len("lair:"):].strip() if payload.startswith("lair:") else payload
    if action == "leave":
        result = abandon_lair_hunt(storage, user_id)
        await bot.send_message(int(event.peer_id), result.text)
        return
    if action == "refresh":
        session = get_lair_session(storage, user_id)
        player = storage.get_character(user_id, refresh_energy=False)
        if session is None or player is None:
            await bot.send_message(int(event.peer_id), "Логово не активно.")
            return
        try:
            image = render_lair_for_player(storage, user_id, session, player)
        except Exception as exc:
            logger.warning("lair render failed: %s", exc)
            await bot.send_message(int(event.peer_id), "Не удалось отрисовать логово.")
            return
        await _lair_send_frame(bot, storage, event, image, caption=lair_status_caption(session, player))
        return
    if action.startswith("shoot:"):
        direction = action[len("shoot:"):]
        if direction not in ("up", "down", "left", "right"):
            return
        result = shoot_lair_hunt(storage, user_id, direction)
    elif action in ("up", "down", "left", "right"):
        result = move_lair_hunt(storage, user_id, action)
    else:
        return
    result_payload = result.payload or {}
    image = result_payload.get("lair_image")
    if result_payload.get("lair_done") or result_payload.get("lair_active") is False:
        await bot.send_message(int(event.peer_id), result.text)
        return
    if image:
        await _lair_send_frame(
            bot,
            storage,
            event,
            image,
            caption=str(result_payload.get("caption") or ""),
            note=str(result_payload.get("move_note") or result.text),
        )
        return
    await bot.send_message(int(event.peer_id), result.text)


async def _blockpost_fight(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.location_zones import fight_blockpost

    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None:
        return
    result = fight_blockpost(storage, user_id, str(player.location))
    await bot.send_message(int(event.peer_id), result.text)
    player = storage.get_character(user_id, refresh_energy=False) or player
    await _walk_render(bot, storage, event, player)


async def _blockpost_gate(bot: VkMessenger, storage: Storage, event: VkEvent) -> bool:
    """Если локация под чужим контролем — сначала блокпост. Вернёт True, если блокируем."""
    from app.location_zones import location_requires_blockpost

    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None:
        return False
    if not location_requires_blockpost(storage, player):
        return False
    location = str(player.location)
    owner = str((storage.get_location(location) or {}).get("controlled_by") or "")
    text = (
        f"🚧 Блокпост «{owner}»\n\n"
        "Локация под контролем чужой группировки. Вход только через блокпост:\n"
        "2 лёгких бойца. Прорвись силой (урон и шанс зависят от твоей силы) — "
        "и ходи по локации свободно.\n\n"
        "После прорыва блокпост остаётся прорванным для тебя."
    )
    await bot.send_message(
        int(event.peer_id),
        text,
        reply_markup=inline_keyboard(
            [("⚔️ Прорвать блокпост", "locmap:blockpost")],
            [("⬅️ В меню", "walk:exit")],
        ),
    )
    return True


# ---------------------------------------------------------------------------
# Клановые задачи (app/faction_goals)
# ---------------------------------------------------------------------------


async def _faction_goals(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.faction_goals import build_faction_goals_text

    player = storage.get_character(int(event.user_id), refresh_energy=False)
    if player is None or not player.faction:
        await bot.send_message(int(event.peer_id), "Сначала выбери группировку.")
        return
    text = build_faction_goals_text(storage, player.faction)
    is_leader = storage.get_faction_leader_id(player.faction) == int(event.user_id)
    rows: list[list] = []
    if is_leader:
        rows.append([("💰 Забрать награду казне", "faction:goalsclaim")])
    rows.append([("⬅️ В группировку", "faction:menu")])
    await bot.send_message(int(event.peer_id), text, reply_markup=inline_keyboard(*rows))


async def _faction_goals_claim(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.faction_goals import try_claim_faction_goal_reward

    player = storage.get_character(int(event.user_id), refresh_energy=False)
    if player is None or not player.faction:
        return
    leader_id = storage.get_faction_leader_id(player.faction)
    if leader_id != int(event.user_id):
        await bot.send_message(int(event.peer_id), "Награду казне забирает только лидер группировки.")
        return
    ok, text = try_claim_faction_goal_reward(storage, player.faction, leader_id)
    await bot.send_message(int(event.peer_id), text)
    await _faction_goals(bot, storage, event)


# ---------------------------------------------------------------------------
# Админ-команды (текстовые, ADMIN_IDS из .env)
# ---------------------------------------------------------------------------

_ADMIN_IDS: tuple[int, ...] = ()
APP_VERSION = "1.0.30"

# Дедуп фоновых уведомлений о смерти: не спамить сообщениями чаще раза в 30 мин.
_offline_death_notify: dict[int, float] = {}


def _init_admin_ids(settings: Any) -> None:
    global _ADMIN_IDS
    _ADMIN_IDS = tuple(getattr(settings, "admin_ids", ()) or ())


def is_admin_user(user_id: int) -> bool:
    return int(user_id) in _ADMIN_IDS


async def _admin_command(bot: VkMessenger, storage: Storage, event: VkEvent, lowered: str, raw: str) -> bool:
    """Обработать админ-команду (текст). Возвращает True, если команда обработана."""
    if not is_admin_user(int(event.user_id)):
        return False

    async def _resolve_target(token: str) -> tuple[int | None, Any]:
        uid = storage.find_telegram_id_by_nickname(token)
        if uid is None:
            try:
                uid = int(token)
            except (TypeError, ValueError):
                uid = None
        if uid is None:
            return None, None
        return uid, storage.get_character(uid, refresh_energy=False)

    parts = raw.split()
    cmd = parts[0].lower() if parts else ""
    args = parts[1:]

    if cmd in ("title", "/title", "titleoff", "/titleoff", "setseason", "/setseason", "confirm_topup", "/confirm_topup"):
        await _admin_title_cmd(bot, storage, event, parts)
        return True

    if cmd in ("give", "/give"):
        if len(args) < 2:
            await bot.send_message(int(event.peer_id), "Использование: give [прозвище|id] [сумма RU]")
            return True
        uid, target = await _resolve_target(args[0])
        if uid is None or target is None:
            await bot.send_message(int(event.peer_id), f"Игрок «{args[0]}» не найден.")
            return True
        try:
            amount = int(args[1])
        except ValueError:
            await bot.send_message(int(event.peer_id), "Сумма должна быть числом.")
            return True
        if amount <= 0 or amount > 10_000_000:
            await bot.send_message(int(event.peer_id), "Сумма от 1 до 10 000 000 RU.")
            return True
        storage.change_money(uid, amount)
        await bot.send_message(int(event.peer_id), f"Выдано {amount} RU игроку {target.nickname}.")
        return True

    if cmd in ("fixme", "/fixme", "unstick", "/unstick"):
        if not args:
            await bot.send_message(int(event.peer_id), "Использование: fixme [прозвище|id]")
            return True
        uid, _target = await _resolve_target(args[0])
        if uid is None:
            await bot.send_message(int(event.peer_id), f"Игрок «{args[0]}» не найден.")
            return True
        from app.player_busy import recover_stuck_player

        try:
            recover_stuck_player(storage, uid, force_clear=True)
            await bot.send_message(int(event.peer_id), "Зависшие режимы сброшены.")
        except Exception as exc:
            logger.warning("fixme failed: %s", exc)
            await bot.send_message(int(event.peer_id), "Не удалось сбросить.")
        return True

    if cmd in ("deleteplayer", "/deleteplayer"):
        if not args:
            await bot.send_message(int(event.peer_id), "Использование: deleteplayer [прозвище|id]")
            return True
        uid, _target = await _resolve_target(args[0])
        if uid is None:
            await bot.send_message(int(event.peer_id), f"Игрок «{args[0]}» не найден.")
            return True
        from app.game_logic import admin_delete_player_account

        result = admin_delete_player_account(storage, uid)
        await bot.send_message(int(event.peer_id), result.text)
        return True

    if cmd in ("top", "/top"):
        await _rating_alltime(bot, storage, event, 0)
        await _rating_season(bot, storage, event, 0)
        return True

    if cmd in ("setfaction", "/setfaction"):
        if len(args) < 2:
            await bot.send_message(int(event.peer_id), "Формат: setfaction [прозвище|id] [Долг|Свобода|Нейтралы|Бандиты|Монолит]")
            return True
        uid_target, target = await _resolve_admin_target(storage, args[0])
        if uid_target is None or target is None:
            await bot.send_message(int(event.peer_id), f"Игрок «{args[0]}» не найден.")
            return True
        faction = _faction_from_word(args[1])
        if faction is None:
            await bot.send_message(int(event.peer_id), "Неизвестная группировка.")
            return True
        storage.set_faction(uid_target, faction)
        await bot.send_message(
            int(event.peer_id),
            f"Игрок {target.nickname} переведён в «{faction}».\n"
            f"Его база: {storage.get_character(uid_target, refresh_energy=False).location if storage.get_character(uid_target, refresh_energy=False) else '?'}",
        )
        return True

    if cmd in ("leader", "/leader", "commander", "/commander"):
        if len(args) < 2:
            await bot.send_message(int(event.peer_id), "Формат: leader [группировка] [прозвище|id]")
            return True
        faction = _faction_from_word(args[0])
        if faction is None:
            await bot.send_message(int(event.peer_id), "Неизвестная группировка.")
            return True
        uid_target, target = await _resolve_admin_target(storage, args[1])
        if uid_target is None or target is None:
            await bot.send_message(int(event.peer_id), f"Игрок «{args[1]}» не найден.")
            return True
        ok = storage.set_faction_leader(faction, uid_target)
        await bot.send_message(
            int(event.peer_id),
            f"{target.nickname} назначен командиром «{faction}»." if ok else "Не удалось назначить (проверь фракцию игрока).",
        )
        return True

    if cmd in ("settravel", "/settravel"):
        if len(args) < 2:
            await bot.send_message(int(event.peer_id), "Формат: settravel [прозвище|id] [секунды]")
            return True
        uid_target, target = await _resolve_admin_target(storage, args[0])
        if uid_target is None or target is None:
            await bot.send_message(int(event.peer_id), f"Игрок «{args[0]}» не найден.")
            return True
        try:
            seconds = max(0, int(args[1]))
        except (TypeError, ValueError):
            await bot.send_message(int(event.peer_id), "Секунды должны быть числом.")
            return True
        from datetime import timedelta

        from app.storage import utc_now

        arrives = (utc_now() + timedelta(seconds=seconds)).isoformat()
        with storage._connect() as conn:
            conn.execute(
                "UPDATE characters SET travel_arrives_at = ? WHERE telegram_id = ?",
                (arrives, uid_target),
            )
        await bot.send_message(int(event.peer_id), f"Время прибытия {target.nickname} обновлено (+{seconds} сек).")
        return True

    if cmd in ("всем", "/всем", "all", "/all"):
        announcement = " ".join(args)
        if not announcement:
            await bot.send_message(int(event.peer_id), "Формат: всем <текст>")
            return True
        text = f"📢 {announcement}"
        sent = 0
        for raw in storage.list_player_ids():
            try:
                await bot.send_message(int(raw), text)
                sent += 1
            except Exception as exc:
                logger.debug("broadcast failed (%s): %s", raw, exc)
        await bot.send_message(int(event.peer_id), f"Объявление отправлено {sent} игрокам.")
        return True

    return False


# ---------------------------------------------------------------------------
# Титулы (VK-аналог наград, app/vk_titles) и админские команды титулов
# ---------------------------------------------------------------------------


async def _admin_title_cmd(
    bot: VkMessenger, storage: Storage, event: VkEvent, parts: list[str]
) -> None:
    """title ник ТИТУЛ / titleoff ник / setseason ДД.ММ.ГГГГ ЧЧ:ММ / confirm_topup ник."""
    cmd = parts[0].lower()
    args = parts[1:]
    user_id = int(event.user_id)
    if cmd in ("setseason", "/setseason"):
        from datetime import datetime as _dt

        if len(args) < 2:
            await bot.send_message(user_id, "Формат: setseason ДД.ММ.ГГГГ ЧЧ:ММ\nПример: setseason 22.08.2026 00:00")
            return
        raw_date = " ".join(args[:2])
        try:
            ends_msk = _dt.strptime(raw_date, "%d.%m.%Y %H:%M")
        except ValueError:
            await bot.send_message(user_id, "Не могу разобрать дату. Формат: ДД.ММ.ГГГГ ЧЧ:ММ")
            return
        from datetime import timezone as _tz

        from app.game_logic import build_season_schedule_text, set_season_end

        ends_utc = ends_msk.replace(tzinfo=_tz.utc)
        season = set_season_end(storage, ends_utc)
        await bot.send_message(
            user_id,
            f"Сезон #{season.get('id')} теперь закончится:\n{build_season_schedule_text(storage)}",
        )
        return
    if cmd in ("season", "/season"):
        from app.game_logic import build_season_schedule_text

        await bot.send_message(user_id, build_season_schedule_text(storage))
        return
    if cmd in ("confirm_topup", "/confirm_topup"):
        await _confirm_topup(bot, storage, event, args)
        return
    if cmd in ("title", "/title"):
        if len(args) < 2:
            await bot.send_message(user_id, "Формат: title [прозвище|id] Титул")
            return
        uid, target = await _resolve_admin_target(storage, args[0])
        if uid is None or target is None:
            await bot.send_message(user_id, f"Игрок «{args[0]}» не найден.")
            return
        from app.vk_titles import set_vk_title

        title = " ".join(args[1:])
        set_vk_title(storage, uid, title)
        await bot.send_message(user_id, f"Титул «{title}» выдан игроку {target.nickname}.")
        try:
            await bot.send_message(uid, f"🏅 Админ выдал тебе титул «{title}» — он виден как префикс ника.")
        except Exception:
            pass
        return
    if cmd in ("titleoff", "/titleoff"):
        if not args:
            await bot.send_message(user_id, "Формат: titleoff [прозвище|id]")
            return
        uid, target = await _resolve_admin_target(storage, args[0])
        if uid is None or target is None:
            await bot.send_message(user_id, f"Игрок «{args[0]}» не найден.")
            return
        from app.vk_titles import clear_vk_title

        clear_vk_title(storage, uid)
        await bot.send_message(user_id, f"Титул игрока {target.nickname} снят.")
        return


async def _resolve_admin_target(storage: Storage, token: str) -> tuple[int | None, Any]:
    uid = storage.find_telegram_id_by_nickname(token)
    if uid is None:
        try:
            uid = int(token)
        except (TypeError, ValueError):
            uid = None
    if uid is None:
        return None, None
    return uid, storage.get_character(uid, refresh_energy=False)


def _faction_from_word(word: str) -> str | None:
    clean = (word or "").strip().lower()
    mapping = {
        "долг": "Долг",
        "свобода": "Свобода",
        "нейтралы": "Нейтралы", "нейтрал": "Нейтралы", "нейтрал": "Нейтралы",
        "бандиты": "Бандиты", "бандит": "Бандиты",
        "монолит": "Монолит",
    }
    return mapping.get(clean)


# ---------------------------------------------------------------------------
# Текстовые команды для всех (аналог ТГ-команд)
# ---------------------------------------------------------------------------


def _build_info_text() -> str:
    """Полная справка по боту — порт ТГ-справки с адаптацией под VK."""
    from app.game_logic import (
        ARTIFACT_SEARCH_ENERGY_COST,
        BASE_POINT_INCOME_PER_HOUR,
        BAZAAR_FACTION_TAX_PERCENT,
        DAILY_CONTRACT_BONUS_PERCENT,
        DEPOT_RAID_ENERGY_COST,
        DUEL_LOSER_HP_REMAINING,
        DUEL_LOSER_MONEY_CAP,
        DUEL_LOSER_MONEY_PERCENT,
        RATING_REWARD,
        REFERRAL_INVITER_BONUS_RU,
        RESOURCE_POINT_INCOME_PER_HOUR,
        TRAVEL_SPEED_BICYCLE,
        TRAVEL_SPEED_NIVA,
        TRAVEL_SPEED_TRUCK,
        TRANSFER_FEE_PERCENT,
        WAR_ALLY_SUCCESS_PAY_RU,
        WAR_ALLY_SUCCESS_RATING,
        WAR_LOBBY_ENERGY_COST,
        WAR_SUCCESS_PAY_RU,
        WEEKLY_CONTRACT_BONUS_PERCENT,
    )
    from app.neutral_capture import NCAP_MIN_MEMBERS

    try:
        from app.game_logic import TRANSPORT_QUEST_REWARD_MULT
    except ImportError:
        TRANSPORT_QUEST_REWARD_MULT = {}

    mult = TRANSPORT_QUEST_REWARD_MULT or {}

    return (
        "ℹ️ Информация по игре\n\n"
        "Разделы меню:\n"
        "• 📟 КПК — раздел сталкера: 🧾 Профиль (медали, ID, фракция, звание), "
        "💬 Чаты, 🏆 Рейтинг, 🗺 Карта Зоны, 📍 Локация, 👥 Игроки, 🤝 Обмен, "
        "💎 Находки/Арты, 📊 Дроп, ☠️ Смерти, 📊 Статистика, 📅 Ежедневка, "
        "🔔 Уведомления, 🔥 Как не сдохнуть, 🏛 Клановые задачи, 🔗 Рефералка.\n"
        "• 🏕 Вылазка — стрелки по локации и кнопки зоны (торговец/аптека, "
        "поиск артов, схрон, лаборатория, логово, блокпост), «🗺 Переходы» — смена локации.\n"
        "  ⚔️ Арена — тренировка 8×8 на базе; 👥 кооп до 3 игроков; "
        "🪖 рейды — тактика 9×9 на логово/склад врага.\n"
        "• 👥 Группировка — склад/казна/гараж, звания, постройки, клановые задачи, союзы.\n"
        "• 🏦 Барахолка — биржа расходников/артефактов/топлива, рынок снаряжения "
        f"(налог {BAZAAR_FACTION_TAX_PERCENT}% в казну ГП продавца), перевозка контрабанды.\n"
        "• 📋 Задания — контракты с переездом (🗓 контракты дня и 📅 недели "
        f"с бонусом +{DAILY_CONTRACT_BONUS_PERCENT}%/+{WEEKLY_CONTRACT_BONUS_PERCENT}% RU); "
        "контрабанда — рисковый курьерский рейс.\n\n"
        "Команды (текстом в чат):\n"
        "• /menu — главное меню.\n"
        "• /cancel — отменить ввод суммы/количества.\n"
        "• /info — эта справка.\n"
        "• /season — когда закончится сезонный рейтинг (дата и время).\n"
        f"• pay [ид|ник] [сумма] — перевод RU (комиссия {TRANSFER_FEE_PERCENT}%).\n"
        "• дуэль [ид|ник] — вызвать на дуэль (ID виден в «👥 Игроки»).\n"
        f"  Проигравший: HP {DUEL_LOSER_HP_REMAINING}, −{DUEL_LOSER_MONEY_PERCENT}% денег "
        f"(макс. {DUEL_LOSER_MONEY_CAP} RU).\n"
        "• /respawn — возродиться, если HP=0.\n"
        "• сбор [текст] — оповестить бойцов (только командир группировки).\n\n"
        "Механики:\n"
        "• 🗺 Переходы: 1 игровая минута ≈ 10 сек реально; "
        f"пешком ×1, велосипед ×{TRAVEL_SPEED_BICYCLE:g}, Нива ×{TRAVEL_SPEED_NIVA:g} + бензин, "
        f"грузовик ×{TRAVEL_SPEED_TRUCK:g} + дизель. "
        f"Награда контракта: пешком ×{mult.get('foot', 1):g}, велосипед ×{mult.get('bicycle', 2):g}, "
        f"Нива ×{mult.get('niva', 4):g}, грузовик ×{mult.get('truck', 6):g}. "
        "На арендованной технике нельзя слезть и идти пешком.\n"
        "• ☢️ Выброс: предупреждения за 60 и 30 мин, затем волны убийства по зонам — "
        "сначала опасные (Радар, Рыжий лес, Янтарь), на базе всегда безопасно.\n"
        "• 🚚 Контрабанда: перевозка; ограбление в пути = провал; лут важнее чистого RU.\n"
        "• 🏚 Рейды (тактика 9×9, 2–5 бойцов): логово, склад/гараж врага; "
        f"−18 энергии (логово) / −{DEPOT_RAID_ENERGY_COST} (склад/гараж); "
        "1 аптечка/боец; HP после боя сохраняется. Успех логова: 1400 + 180×выживших RU в казну; "
        "провал: −110 RU и −8 рейтинга каждому.\n"
        f"• 💰 Пассив казны: ресурсы {RESOURCE_POINT_INCOME_PER_HOUR} RU/ч, "
        f"база {BASE_POINT_INCOME_PER_HOUR} RU/ч.\n"
        f"• 🔗 Реферал: пригласившему +{REFERRAL_INVITER_BONUS_RU} RU, другу — стартовый набор.\n"
        "• 🛏 Спальник — ×2 реген энергии.\n"
        f"• 💎 Артефакты — «📡 Поиск артефактов»: тактическая охота "
        f"(−{ARTIFACT_SEARCH_ENERGY_COST} энергии, до 24 ходов, нужен детектор).\n"
        f"• ⚔️ Война: нейтральные — захват от {NCAP_MIN_MEMBERS} бойцов на 6×6, занятые — "
        f"лобби (−{WAR_LOBBY_ENERGY_COST} энергии, 1 аптечка/боец, хост +{WAR_SUCCESS_PAY_RU} RU/"
        f"+{RATING_REWARD.get('war_success', 15)} рейт., союзники +{WAR_ALLY_SUCCESS_PAY_RU} RU/"
        f"+{WAR_ALLY_SUCCESS_RATING} рейт.).\n"
        "• ⚔️ Арена: на домашней базе, поле 8×8, бесконечные волны; 3 аптечки арены (+45 HP); "
        "тренировка без штрафов смерти; награда как лёгкое задание.\n"
        "• 🎖 Скины по рейтингу: 0 / 500 / 2000 / 5000.\n"
        "• 📅 Сезон рейтинга: раз в 14 дней топ-3 получает эксклюзивную снарягу "
        "(🥇 пушка+броня, 🥈 пушка, 🥉 броня; у торговца не продаётся); топ-3 ГП — RU в казну.\n"
        "• ⭐ Пополнение: «⭐ Пополнить» → товары сообщества (VK Market) "
        "149/249/499/999 ₽, на карман или в казну ГП; после оплаты RU зачисляются автоматически.\n\n"
        "Чаты и поддержка: уточняй у администратора сообщества.\n"
        f"🛠 Сборка: v{APP_VERSION}"
    )


async def _cmd_help(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    try:
        text = _build_info_text()
    except Exception as exc:
        logger.warning("info text build failed: %s", exc)
        text = (
            "📖 Как играть\n\n"
            "• /menu — главное меню, /info — эта справка, /cancel — отменить ввод,\n"
            "  /respawn — возродиться, /season — конец сезона,\n"
            "  pay <ник/ид> <сумма> — перевод RU, дуэль <ник/ид> — вызов на дуэль,\n"
            "  сбор <текст> — лидеру оповестить группировку.\n\n"
            "• Секции: КПК, Инвентарь, Вылазка (с переходами), Торговец, Группировка, "
            "Задания, Рейды, Рейтинг, Барахолка, Контрабанда, События, Дуэль, Арена, "
            "Кооп, Ежедневка, Пополнение, Уведомления, Смерти, Обучение."
        )
    await bot.send_message(int(event.peer_id), text)


async def _cmd_cancel(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    fsm = VkFSM(int(event.user_id))
    await fsm.clear()
    await bot.send_message(int(event.peer_id), "Ввод отменён.", reply_markup=_menu_keyboard())


async def _cmd_respawn(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import respawn_character

    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None:
        await bot.send_message(int(event.peer_id), "Сначала создай персонажа.")
        return
    if player.health > 0:
        await bot.send_message(int(event.peer_id), "Ты жив — респавн не нужен.")
        return
    result = respawn_character(storage, user_id)
    await bot.send_message(int(event.peer_id), result.text, reply_markup=_menu_keyboard())


async def _cmd_duel(bot: VkMessenger, storage: Storage, event: VkEvent, target: str) -> None:
    try:
        target_id = int(target)
    except (TypeError, ValueError):
        target_id = storage.find_telegram_id_by_nickname(target)
    if target_id is None or target_id <= 0:
        await bot.send_message(int(event.peer_id), "Игрок не найден. Формат: дуэль <ник|id>")
        return
    await _duel_start(bot, storage, event, target_id)


async def _cmd_pay(bot: VkMessenger, storage: Storage, event: VkEvent, args: list[str]) -> None:
    from app.game_logic import transfer_money_with_fee

    user_id = int(event.user_id)
    if len(args) < 2:
        await bot.send_message(int(event.peer_id), "Формат: pay <ник|ид> <сумма>")
        return
    target_id, _target = await _resolve_admin_target(storage, args[0])
    if target_id is None:
        await bot.send_message(int(event.peer_id), f"Игрок «{args[0]}» не найден.")
        return
    try:
        amount = int(args[1])
    except (TypeError, ValueError):
        await bot.send_message(int(event.peer_id), "Сумма должна быть числом.")
        return
    if amount <= 0:
        await bot.send_message(int(event.peer_id), "Сумма должна быть больше нуля.")
        return
    result = transfer_money_with_fee(storage, user_id, target_id, amount)
    await bot.send_message(int(event.peer_id), result.text)


async def _cmd_sbor(bot: VkMessenger, storage: Storage, event: VkEvent, text: str) -> None:
    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None or not player.faction:
        await bot.send_message(int(event.peer_id), "Ты без группировки.")
        return
    if storage.get_faction_leader_id(player.faction) != user_id:
        await bot.send_message(int(event.peer_id), "Сбор может объявить только лидер группировки.")
        return
    body = (text or "").strip()
    message = f"📣 Сбор! {player.nickname} зовёт: {body}" if body else f"📣 Сбор! {player.nickname} созвал бойцов."
    sent = 0
    for row in storage.list_faction_members(player.faction):
        try:
            await bot.send_message(int(row["telegram_id"]), message)
            await asyncio.sleep(0.05)  # анти-флуд VK при рассылке по группировке
            sent += 1
        except Exception as exc:
            logger.debug("sbor notify failed: %s", exc)
    await bot.send_message(int(event.peer_id), f"📣 Объявление отправлено {sent} бойцам.")



# ---------------------------------------------------------------------------
# Пополнение «Товаром» через ВК (VK Market; подтверждает админ)
# ---------------------------------------------------------------------------

# (название товара, цена в ₽, начисляемые RU)
TOPUP_PACKAGES: tuple[tuple[str, int, int], ...] = (
    ("🎁 Жетон сталкера", 149, 1500),
    ("🎁 Поддон RU", 249, 3000),
    ("🎁 Ящик контрабанды", 499, 6000),
    ("🎁 Сундук Зоны", 999, 12000),
)
TOPUP_ORDER_PREFIX = "topup:pending:"


def _topup_market_url() -> str:
    try:
        from app.config import load_settings as _ls

        return str(getattr(_ls(), "market_url", "") or "").strip()
    except Exception:
        return ""


async def _topup_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    text = (
        "⭐ Пополнение через товары ВК\n\n"
        "Купи товар сообщества (VK Market), потом нажми «Я оплатил» — "
        "админ проверит и зачислит RU. Адреса товаров выдаёт админ (VK_MARKET_URL).\n\n"
        "Пакеты:\n"
        + "\n".join(
            f"{label} — {price} ₽ = {ru} RU" for label, price, ru in TOPUP_PACKAGES
        )
    )
    rows: list[list] = []
    for idx, (label, price, ru) in enumerate(TOPUP_PACKAGES, start=1):
        rows.append([(f"{label} · {price} ₽ → {ru} RU", f"topup:pack:{idx}")])
    rows.append([("⭐ Другое количество", "topup:custom")])
    rows.append([("⬅️ В меню", "topup:exit")])
    await bot.send_message(int(event.peer_id), text, reply_markup=inline_keyboard(*rows))


async def _topup_pick_target(bot: VkMessenger, storage: Storage, event: VkEvent, idx: str) -> None:
    try:
        n = int(idx) - 1
        label, price, ru = TOPUP_PACKAGES[n]
    except (ValueError, IndexError):
        await _topup_open(bot, storage, event)
        return
    await bot.send_message(
        int(event.peer_id),
        f"{label}: {price} ₽ → {ru} RU.\nКуда зачисляем?",
        reply_markup=inline_keyboard(
            [("👤 Себе на карман", f"topup:target:{idx}:pocket")],
            [("🏛 В казну группировки", f"topup:target:{idx}:treasury")],
            [("⬅️ Назад", "topup:menu")],
        ),
    )


async def _topup_confirm_package(bot: VkMessenger, storage: Storage, event: VkEvent, idx: str, target: str) -> None:
    try:
        n = int(idx) - 1
        label, price, ru = TOPUP_PACKAGES[n]
    except (ValueError, IndexError):
        await _topup_open(bot, storage, event)
        return
    url = _topup_market_url()
    text = (
        f"{label}: {price} ₽ → {ru} RU\n"
        f"Цель: {'карман' if target == 'pocket' else 'казна группировки'}.\n\n"
    )
    if url:
        text += f"🛒 Товар сообщества: {url}\n\n"
    else:
        text += "🛒 Ссылку на товар даёт админ (переменная VK_MARKET_URL).\n\n"
    text += (
        "Купи товар, затем нажми «Я оплатил». Админ проверит заявку и зачислит RU "
        "(команда confirm_topup)."
    )
    await bot.send_message(
        int(event.peer_id),
        text,
        reply_markup=inline_keyboard(
            [("✅ Я оплатил", f"topup:paid:{idx}:{target}")],
            [("⬅️ Назад", "topup:menu")],
        ),
    )


async def _topup_mark_paid(
    bot: VkMessenger, storage: Storage, event: VkEvent, idx: str, ru_arg: str, target: str | None = None
) -> None:
    if idx == "custom":
        try:
            ru_amount = int(ru_arg)
        except (TypeError, ValueError):
            await _topup_open(bot, storage, event)
            return
        target = target or "pocket"
        label = f"🎁 Товар на {ru_amount} RU"
        price = max(149, int(round(ru_amount / 1500 * 149)))
    else:
        try:
            n = int(idx) - 1
            label, price, ru_amount = TOPUP_PACKAGES[n]
        except (ValueError, IndexError):
            await _topup_open(bot, storage, event)
            return
        target = target or "pocket"
    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    payload = {
        "label": label,
        "price_rub": price,
        "ru_amount": ru_amount,
        "target": target,
        "nickname": str(player.nickname if player else user_id),
    }
    import json as _json

    storage.set_meta(f"{TOPUP_ORDER_PREFIX}{user_id}", _json.dumps(payload, ensure_ascii=False))
    await bot.send_message(
        user_id,
        f"Заявка создана: {label} ({price} ₽) → {ru_amount} RU в {('карман' if target == 'pocket' else 'казну')}.\n"
        "Ожидай подтверждения админа.",
    )
    for admin_id in _ADMIN_IDS:
        try:
            await bot.send_message(
                int(admin_id),
                f"🛒 Заявка на пополнение: {payload['nickname']} (#{user_id}) — {label} {price} ₽ → {ru_amount} RU "
                f"({target}). Подтвердить: confirm_topup {payload['nickname']}",
            )
        except Exception as exc:
            logger.debug("topup notify admin failed (%s): %s", admin_id, exc)
    await _topup_open(bot, storage, event)


def _apply_topup_payload(storage: Storage, user_id: int, payload: dict[str, Any]) -> str:
    """Зачислить RU по заявке топ-апа. Общая логика для админ-подтверждения и Callback API."""
    label = str(payload.get("label") or "Пакет")
    try:
        ru = int(payload.get("ru_amount") or 0)
    except (TypeError, ValueError):
        ru = 0
    target_kind = str(payload.get("target") or "pocket")
    if ru <= 0:
        return "Некорректная заявка."
    target = storage.get_character(user_id, refresh_energy=False)
    if target_kind == "treasury" and target is not None and target.faction:
        storage.change_faction_treasury(target.faction, ru)
        return f"В казну «{target.faction}» зачислено {ru} RU ({label})."
    storage.change_money(user_id, ru)
    return f"Игроку {getattr(target, 'nickname', user_id)} начислено {ru} RU ({label})."


async def _confirm_topup(bot: VkMessenger, storage: Storage, event: VkEvent, args: list[str]) -> None:
    """confirm_topup [прозвище|id] — зачислить последнюю заявку игрока."""
    if not args:
        await bot.send_message(int(event.user_id), "Использование: confirm_topup [прозвище|id]")
        return
    uid, target = await _resolve_admin_target(storage, args[0])
    if uid is None or target is None:
        await bot.send_message(int(event.user_id), f"Игрок «{args[0]}» не найден.")
        return
    import json as _json

    raw = storage.get_meta(f"{TOPUP_ORDER_PREFIX}{uid}")
    if not raw:
        await bot.send_message(int(event.user_id), f"У игрока {target.nickname} нет заявки на пополнение.")
        return
    try:
        payload = _json.loads(str(raw))
    except (TypeError, ValueError, _json.JSONDecodeError):
        payload = {}
    storage.delete_meta(f"{TOPUP_ORDER_PREFIX}{uid}")
    result_text = _apply_topup_payload(storage, uid, payload)
    if "Некорректная" in result_text:
        await bot.send_message(int(event.user_id), result_text)
        return
    label = str(payload.get("label") or "Пакет")
    target_kind = str(payload.get("target") or "pocket")
    await bot.send_message(int(event.user_id), result_text)
    try:
        if target_kind == "treasury" and target.faction:
            await bot.send_message(uid, f"🏛 {label} подтверждён: +{payload.get('ru_amount')} RU в казну «{target.faction}»!")
        else:
            await bot.send_message(uid, f"⭐ {label} подтверждён: +{payload.get('ru_amount')} RU на карман!")
    except Exception:
        pass


async def handle_market_order(
    bot: VkMessenger, storage: Storage, payload: dict[str, Any]
) -> str | None:
    """Callback market_order_new: покупка товара → автоподтверждение пополнения.

    Возвращает текст результата (или None, если заявки нет/нечего делать).
    """
    import json as _json

    try:
        user_id = int(payload.get("user_id") or 0)
    except (TypeError, ValueError):
        return None
    if user_id <= 0:
        return None
    raw = storage.get_meta(f"{TOPUP_ORDER_PREFIX}{user_id}")
    if not raw:
        return None
    try:
        pending = _json.loads(str(raw))
    except (TypeError, ValueError, _json.JSONDecodeError):
        pending = {}
    try:
        # Официальное поле VK market_order_new — amount (сумма в копейках);
        # total оставляем как fallback (старые записи/план).
        amount_raw = payload.get("amount")
        if amount_raw is None:
            amount_raw = payload.get("total") or 0
        total = int(amount_raw or 0)
    except (TypeError, ValueError):
        total = 0
    # VK возвращает цены в копейках; защита от опечаток округлением.
    price_rub = total // 100 if total >= 1000 else total
    try:
        expected_price = int(pending.get("price_rub") or 0)
    except (TypeError, ValueError):
        expected_price = 0
    if expected_price and abs(price_rub - expected_price) > 1:
        note = (
            f"🛒 Покупка#{payload.get('id')}: цена {price_rub}₽ не совпала с заявкой "
            f"(ожидалось {expected_price}₽) — проверь вручную: confirm_topup <ник>."
        )
        for admin_id in _ADMIN_IDS:
            try:
                await bot.send_message(int(admin_id), note)
            except Exception as exc:
                logger.debug("topup mismatch notify failed (%s): %s", admin_id, exc)
        return note

    storage.delete_meta(f"{TOPUP_ORDER_PREFIX}{user_id}")
    result_text = _apply_topup_payload(storage, user_id, pending)
    label = str(pending.get("label") or "Пакет")
    try:
        await bot.send_message(user_id, f"⭐ {label} оплачен и зачислен: {result_text}")
    except Exception as exc:
        logger.debug("topup auto confirm notify failed (%s): %s", user_id, exc)
    return result_text


async def _topup_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "menu":
        await _topup_open(bot, storage, event)
    elif cmd == "exit":
        await _menu(bot, storage, event)
    elif cmd == "pack" and len(parts) >= 3:
        await _topup_pick_target(bot, storage, event, parts[2])
    elif cmd == "target" and len(parts) >= 4:
        await _topup_confirm_package(bot, storage, event, parts[2], parts[3])
    elif cmd == "custconfirm" and len(parts) >= 4:
        await _topup_confirm_custom(bot, storage, event, parts[2], parts[3])
    elif cmd == "paid" and len(parts) >= 3:
        # Пакет: topup:paid:<idx>:<target>; кастом: topup:paid:custom:<ru>:<target>.
        # Раньше target для пакетов попадал в ru_arg и терялся (всегда «карман»).
        if parts[2] == "custom":
            if len(parts) < 4:
                await _topup_open(bot, storage, event)
                return
            await _topup_mark_paid(
                bot, storage, event, "custom",
                parts[3],
                parts[4] if len(parts) > 4 else None,
            )
        else:
            await _topup_mark_paid(
                bot, storage, event, parts[2], "pocket",
                parts[3] if len(parts) > 3 else None,
            )
    elif cmd == "custom":
        fsm = VkFSM(int(event.user_id))
        await fsm.set_state("topup_custom")
        await bot.send_message(
            int(event.peer_id),
            "Введи целое число RU, которое хочешь получить (минимум 1500).\n"
            "Цена товара: 149 ₽ за каждые 1500 RU.",
        )


async def _topup_custom_input(bot: VkMessenger, storage: Storage, event: VkEvent, text: str) -> None:
    fsm = VkFSM(int(event.user_id))
    await fsm.clear()
    raw = (text or "").strip().replace(" ", "").replace("\u00a0", "")
    if not raw.isdigit():
        await bot.send_message(int(event.peer_id), "Введи целое число RU.")
        return
    ru = int(raw)
    if ru < 1500:
        await bot.send_message(int(event.peer_id), "Минимум 1500 RU.")
        return
    base_ru, base_price = 1500, 149
    price = max(149, int(round(ru / base_ru * base_price)))
    await bot.send_message(
        int(event.peer_id),
        f"⭐ Товар на {ru} RU — цена ~{price} ₽.\nКуда зачисляем?",
        reply_markup=inline_keyboard(
            [("👤 Себе на карман", f"topup:custconfirm:{ru}:pocket")],
            [("🏛 В казну группировки", f"topup:custconfirm:{ru}:treasury")],
            [("⬅️ Назад", "topup:menu")],
        ),
    )


async def _topup_confirm_custom(bot: VkMessenger, storage: Storage, event: VkEvent, ru: str, target: str) -> None:
    try:
        ru_i = int(ru)
    except (TypeError, ValueError):
        await _topup_open(bot, storage, event)
        return
    base_ru, base_price = 1500, 149
    price = max(149, int(round(ru_i / base_ru * base_price)))
    url = _topup_market_url()
    label = f"🎁 Товар на {ru_i} RU"
    text = (
        f"{label}: ~{price} ₽ → {ru_i} RU\n"
        f"Цель: {'карман' if target == 'pocket' else 'казна группировки'}.\n\n"
    )
    text += f"🛒 Товар сообщества: {url}\n\n" if url else "🛒 Ссылку на товар даёт админ (VK_MARKET_URL).\n\n"
    text += "Купи товар, затем нажми «Я оплатил». Админ проверит заявку и зачислит RU."
    await bot.send_message(
        int(event.peer_id),
        text,
        reply_markup=inline_keyboard(
            [("✅ Я оплатил", f"topup:paid:custom:{ru_i}:{target}")],
            [("⬅️ Назад", "topup:menu")],
        ),
    )


# ---------------------------------------------------------------------------
# Инвентарь: «употребить» расходники (вне боя)
# ---------------------------------------------------------------------------

_USE_ITEM_MAP: dict[str, str] = {
    "medkit": "use_medkit",
    "medkit_army": "use_medkit_army",
    "medkit_science": "use_medkit_science",
    "energy_drink": "use_energy_drink",
    "nitrous_oxide": "use_nitrous_oxide",
    "bread": "use_bread",
    "sausage": "use_sausage",
    "stew": "use_stew",
    "water_bottle": "use_water",
    "mineralka": "use_mineralka",
    "beard_tea": "use_beard_tea",
    "vodka": "use_vodka",
    "antirad": "use_antirad",
    "antibiotic": "use_antibiotic",
}


async def _inventory_use(bot: VkMessenger, storage: Storage, event: VkEvent, item_key: str) -> None:
    import app.game_logic as _gl

    fn_name = _USE_ITEM_MAP.get(item_key)
    if not fn_name:
        await bot.send_message(int(event.peer_id), "Этот предмет нельзя употребить вне боя.")
        return
    fn = getattr(_gl, fn_name, None)
    if fn is None:
        return
    user_id = int(event.user_id)
    try:
        result = fn(storage, user_id)
    except TypeError:
        result = fn(storage, user_id, item_key)
    await bot.send_message(int(event.peer_id), getattr(result, "text", str(result)))
    await _inventory(bot, storage, event)


async def _inventory_consumables(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    """Экран «🧰 Расходники» (как в ТГ): только предметы, которые можно употребить."""
    from app.game_logic import ITEM_LABELS

    player = storage.get_character(int(event.user_id), refresh_energy=False)
    if player is None:
        await bot.send_message(int(event.peer_id), "Сначала создай персонажа.")
        return
    inv = getattr(player, "inventory", {}) or {}
    rows: list[list] = []
    for key in _USE_ITEM_MAP:
        count = int(inv.get(key, 0))
        if count <= 0:
            continue
        rows.append([(f"💊 {ITEM_LABELS.get(key, key)} ×{count}", f"inventory:use:{key}")])
    if not rows:
        rows.append([("Расходников нет", "inventory:root")])
    rows.append([("⬅️ В инвентарь", "inventory:root")])
    await bot.send_message(int(event.peer_id), "🧰 Расходники", reply_markup=inline_keyboard(*rows))


async def _inventory_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "use" and len(parts) >= 3:
        if parts[2] == "stash_case":
            # «📦 Открыть тайник» — отдельный движковый флоу открытия кейса.
            from app.game_logic import open_stash

            result = open_stash(storage, int(event.user_id))
            await bot.send_message(int(event.peer_id), getattr(result, "text", str(result)))
            await _inventory(bot, storage, event)
            return
        await _inventory_use(bot, storage, event, parts[2])
    elif cmd == "consumables":
        await _inventory_consumables(bot, storage, event)
    elif cmd == "root":
        await _inventory(bot, storage, event)
    else:
        await _inventory(bot, storage, event)


# ---------------------------------------------------------------------------
# Техник и медик (app/vendors)
# ---------------------------------------------------------------------------


async def _tech_menu(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.vendors import vendor_assortment_blurb

    user_id = int(event.user_id)
    text = f"🛠 Техник.\n{vendor_assortment_blurb(storage, user_id, 'tech')}"
    await bot.send_message(
        int(event.peer_id),
        text,
        reply_markup=inline_keyboard(
            [("🛡 Улучшить броню (+1 защита)", "trade:techbuy:armor_upgrade"), ("🎒 Улучшить снарягу", "trade:techbuy:gear_upgrade")],
            [("💎 Доп. ячейка артефакта", "trade:techbuy:artifact_slot"), ("💨 Купить N2O", "trade:buyitem:nitrous_oxide")],
            [("🔧 Ремонт", "trade:repair")],
            [("⬅️ В меню торговца", "trade:root")],
        ),
    )


async def _tech_buy(bot: VkMessenger, storage: Storage, event: VkEvent, key: str) -> None:
    from app.game_logic import buy_item

    result = buy_item(storage, int(event.user_id), key, amount=1)
    await bot.send_message(int(event.peer_id), result.text)
    await _tech_menu(bot, storage, event)


async def _medic_menu(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.vendors import vendor_assortment_blurb

    user_id = int(event.user_id)
    text = f"🧑‍⚕️ Медик.\n{vendor_assortment_blurb(storage, user_id, 'medic')}"
    await bot.send_message(
        int(event.peer_id),
        text,
        reply_markup=inline_keyboard(
            [("💉 Лечение (с этапом вкачки)", "trade:medicheal")],
            [("🛒 Купить аптечки", "trade:buy")],
            [("⬅️ В меню торговца", "trade:root")],
        ),
    )


# ---------------------------------------------------------------------------
# Союзы и дипломатия ГП (app/game_logic)
# ---------------------------------------------------------------------------


async def _alliance_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import build_alliance_overview

    text = build_alliance_overview(storage, int(event.user_id))
    await bot.send_message(
        int(event.peer_id),
        text,
        reply_markup=inline_keyboard(
            [("🤝 Предложить союз", "alliance:list:propose"), ("📥 Входящие", "alliance:list:confirm")],
            [("💔 Разорвать союз", "alliance:list:break"), ("⚔️ Объявить войну", "alliance:list:war")],
            [("⬅️ В группировку", "alliance:back")],
        ),
    )


async def _alliance_target_list(bot: VkMessenger, storage: Storage, event: VkEvent, mode: str) -> None:
    player = storage.get_character(int(event.user_id), refresh_energy=False)
    if player is None or not player.faction:
        await bot.send_message(int(event.peer_id), "Сначала выбери группировку.")
        return
    targets = [
        str(f["name"])
        for f in storage.get_factions()
        if str(f.get("name") or "") and str(f["name"]) != player.faction
    ]
    if mode == "confirm":
        incoming = storage.list_incoming_alliance_requests(player.faction)
        targets = [str(r["requester_faction"]) for r in incoming]
        if not targets:
            await bot.send_message(int(event.peer_id), "Входящих предложений на союз нет.")
            return
    if not targets:
        await bot.send_message(int(event.peer_id), "Нет доступных группировок.")
        return
    title = {
        "propose": "Предложить договор о союзе:",
        "confirm": "Принять предложение:",
        "break": "Разорвать союз с:",
        "war": "Объявить войну:",
    }.get(mode, "Выбери группировку:")
    rows: list[list] = [[(f, f"alliance:do:{mode}:{f}")] for f in targets]
    rows.append([("⬅️ Назад", "alliance:menu")])
    await bot.send_message(int(event.peer_id), title, reply_markup=inline_keyboard(*rows))


async def _alliance_do(bot: VkMessenger, storage: Storage, event: VkEvent, mode: str, faction: str) -> None:
    from app.game_logic import accept_alliance, break_alliance, declare_war, propose_alliance

    user_id = int(event.user_id)
    if mode == "propose":
        result = propose_alliance(storage, user_id, faction)
    elif mode == "confirm":
        result = accept_alliance(storage, user_id, faction)
    elif mode == "break":
        result = break_alliance(storage, user_id, faction)
    elif mode == "war":
        result = declare_war(storage, user_id, faction)
    else:
        return
    await bot.send_message(int(event.peer_id), result.text)
    await _alliance_open(bot, storage, event)


async def _alliance_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "menu":
        await _alliance_open(bot, storage, event)
    elif cmd == "back":
        await bot.send_message(int(event.peer_id), "🏛 Группировка:", reply_markup=_faction_menu_keyboard())
    elif cmd == "list" and len(parts) >= 3:
        await _alliance_target_list(bot, storage, event, parts[2])
    elif cmd == "do" and len(parts) >= 4:
        await _alliance_do(bot, storage, event, parts[2], parts[3])


# ---------------------------------------------------------------------------
# Гараж группировки и аренда техники (app/game_logic)
# ---------------------------------------------------------------------------


async def _garage_open(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import get_faction_garage

    player = storage.get_character(int(event.user_id), refresh_energy=False)
    if player is None or not player.faction:
        await bot.send_message(int(event.peer_id), "Сначала выбери группировку.")
        return
    garage = get_faction_garage(storage, player.faction)
    niva_n = int(garage.get("niva", 0))
    truck_n = int(garage.get("truck", 0))
    lines = [
        f"🚚 Гараж «{player.faction}»",
        "",
        f"• Нивы на ходу: {niva_n}",
        f"• Грузовики на ходу: {truck_n}",
        f"• Бензин: {int(garage.get('gasoline', 0))} канистр",
        f"• Дизель: {int(garage.get('diesel', 0))} канистр",
        "",
        "Технику в гараж может сдать любой боец. Забрать Ниву/грузовик — только 5+ ранг или лидер.",
    ]
    await bot.send_message(
        int(event.peer_id),
        "\n".join(lines),
        reply_markup=inline_keyboard(
            [("📥 Сдать Ниву", "faction:garagedep:niva"), ("📥 Сдать грузовик", "faction:garagedep:truck")],
            [("📥 Бензин", "faction:garagedep:gasoline"), ("📥 Дизель", "faction:garagedep:diesel")],
            [("📤 Забрать Ниву", "faction:garagewd:niva"), ("📤 Забрать грузовик", "faction:garagewd:truck")],
            [("📤 Бензин", "faction:garagewd:gasoline"), ("📤 Дизель", "faction:garagewd:diesel")],
            [("🚗 Аренда техники", "faction:garagereq:niva"), ("📋 Заявки на аренду", "faction:garagereqs")],
            [("⬅️ В группировку", "faction:menu")],
        ),
    )


async def _garage_deposit(bot: VkMessenger, storage: Storage, event: VkEvent, kind: str) -> None:
    from app.game_logic import garage_deposit_fuel, garage_deposit_niva, garage_deposit_truck

    user_id = int(event.user_id)
    if kind == "niva":
        result = garage_deposit_niva(storage, user_id)
    elif kind == "truck":
        result = garage_deposit_truck(storage, user_id)
    elif kind in ("gasoline", "diesel"):
        result = garage_deposit_fuel(storage, user_id, kind)
    else:
        result = None
    if result is not None:
        await bot.send_message(int(event.peer_id), result.text)
    await _garage_open(bot, storage, event)


async def _garage_withdraw(bot: VkMessenger, storage: Storage, event: VkEvent, kind: str) -> None:
    from app.game_logic import garage_withdraw_fuel, garage_withdraw_niva, garage_withdraw_truck

    user_id = int(event.user_id)
    if kind == "niva":
        result = garage_withdraw_niva(storage, user_id)
    elif kind == "truck":
        result = garage_withdraw_truck(storage, user_id)
    elif kind in ("gasoline", "diesel"):
        result = garage_withdraw_fuel(storage, user_id, kind)
    else:
        result = None
    if result is not None:
        await bot.send_message(int(event.peer_id), result.text)
    await _garage_open(bot, storage, event)


async def _garage_rental_request(bot: VkMessenger, storage: Storage, event: VkEvent, kind: str) -> None:
    from app.game_logic import request_garage_vehicle_rental

    result = request_garage_vehicle_rental(storage, int(event.user_id), kind)
    await bot.send_message(int(event.peer_id), result.text)
    await _garage_open(bot, storage, event)


async def _garage_requests(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import (
        build_garage_rental_requests_overview,
        can_withdraw_faction_warehouse,
        list_garage_rental_requests_for_faction,
    )

    player = storage.get_character(int(event.user_id), refresh_energy=False)
    if player is None or not player.faction:
        return
    if not can_withdraw_faction_warehouse(storage, player):
        await bot.send_message(int(event.peer_id), "Заявки видят бойцы 5+ ранга и лидер.")
        return
    requests = list_garage_rental_requests_for_faction(storage, player.faction)
    text = build_garage_rental_requests_overview(storage, int(event.user_id))
    rows: list[list] = []
    for req in requests:
        rid = str(req.get("id") or "")
        who = str(req.get("player_nickname") or req.get("player_id") or "?")
        vehicle = str(req.get("vehicle_key") or "")
        label = f"{who} · {vehicle}"
        rows.append([(f"✅ {label}", f"faction:garageapp:{rid}"), (f"❌ {label}", f"faction:garagedeny:{rid}")])
    rows.append([("⬅️ В гараж", "faction:garage")])
    if not requests:
        await bot.send_message(int(event.peer_id), text, reply_markup=inline_keyboard([("⬅️ В гараж", "faction:garage")]))
        return
    await bot.send_message(int(event.peer_id), text, reply_markup=inline_keyboard(*rows))


async def _garage_approve(bot: VkMessenger, storage: Storage, event: VkEvent, request_id: str) -> None:
    from app.game_logic import approve_garage_rental_request

    result = approve_garage_rental_request(storage, int(event.user_id), request_id)
    await bot.send_message(int(event.peer_id), result.text)
    await _garage_requests(bot, storage, event)


async def _garage_deny(bot: VkMessenger, storage: Storage, event: VkEvent, request_id: str) -> None:
    from app.game_logic import deny_garage_rental_request

    result = deny_garage_rental_request(storage, int(event.user_id), request_id)
    await bot.send_message(int(event.peer_id), result.text)
    await _garage_requests(bot, storage, event)


# ---------------------------------------------------------------------------
# Атака Монолита (app/monolith_war)
# ---------------------------------------------------------------------------


async def _monolith_menu(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    from app.game_logic import list_assaultable_locations
    from app.monolith_war import MONOLITH_FACTION, get_pending_monolith_war, monolith_war_status_line

    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    pending = get_pending_monolith_war(storage)
    status = monolith_war_status_line(storage)
    text = status or "☢ Бои с Монолитом: окно атаки открывает боец Монолита, вступить могут все его противники."
    rows: list[list] = []
    if pending is not None:
        rows.append([("👥 Вступить в бой", "monolith:join")])
        rows.append([("🤖 Позвать ботов", "monolith:bots")])
        rows.append([("🚀 Начать бой", "monolith:start")])
    elif player is not None and player.faction == MONOLITH_FACTION:
        targets = []
        try:
            targets = list_assaultable_locations(storage, MONOLITH_FACTION)[:12]
        except Exception:
            targets = []
        for t in targets:
            rows.append([("☢ Атака: " + str(t.get("name") or "?"), "monolith:attack:" + str(t.get("name") or ""))])
        if not rows:
            rows.append([("Нет целей для атаки", "monolith:menu")])
    rows.append([("🔄 Обновить", "monolith:menu"), ("⬅️ В меню", "monolith:exit")])
    await bot.send_message(int(event.peer_id), text, reply_markup=inline_keyboard(*rows))


async def _monolith_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    from app.monolith_war import (
        force_start_monolith_war,
        join_monolith_war,
        send_monolith_bots,
        start_monolith_attack,
    )

    user_id = int(event.user_id)
    action = payload[len("monolith:"):].strip() if payload.startswith("monolith:") else payload
    if action == "menu":
        await _monolith_menu(bot, storage, event)
        return
    if action == "exit":
        await _menu(bot, storage, event)
        return
    if action == "join":
        result = join_monolith_war(storage, user_id)
        await bot.send_message(int(event.peer_id), result.text)
        await _monolith_menu(bot, storage, event)
        return
    if action == "bots":
        result = send_monolith_bots(storage, user_id)
        await bot.send_message(int(event.peer_id), result.text)
        await _monolith_menu(bot, storage, event)
        return
    if action == "start":
        result = force_start_monolith_war(storage, user_id)
        await bot.send_message(int(event.peer_id), result.text)
        outcome = (result.payload or {}).get("monolith_outcome") or {}
        text = str(outcome.get("text") or result.text)
        for uid in outcome.get("notify_ids") or []:
            try:
                await bot.send_message(int(uid), text)
            except Exception as exc:
                logger.debug("monolith outcome notify failed (%s): %s", uid, exc)
        if outcome.get("kind") == "tactical":
            for uid in outcome.get("notify_ids") or []:
                try:
                    await _war_cwar_send_frame_to(bot, storage, int(uid))
                except Exception:
                    pass
        return
    if action.startswith("attack:"):
        location = action[len("attack:"):].strip()
        result = start_monolith_attack(storage, user_id, location)
        await bot.send_message(int(event.peer_id), result.text)
        if result.ok:
            for uid in (result.payload or {}).get("monolith_notify_ids") or []:
                try:
                    await bot.send_message(int(uid), result.text)
                except Exception as exc:
                    logger.debug("monolith attack notify failed (%s): %s", uid, exc)
        return


# ---------------------------------------------------------------------------
# Смерть и авто-респавн (выживание обрабатывается в фоне и при действиях)
# ---------------------------------------------------------------------------


def _player_in_tactical_session(storage: Storage, user_id: int) -> bool:
    """Жив ли игрок в тактической сессии — смерти там обрабатывает движок сам."""
    try:
        from app.quest_mission import get_mission_session
        from app.artifact_hunt import get_hunt_session
        from app.stash_hunt import get_stash_session
        from app.arena_grid import get_arena_session
        from app.duel_grid import get_duel_session_by_player
        from app.smuggle_mission import get_smuggle_session
        from app.lab_mission import get_lab_session
        from app.player_busy import player_busy_reason

        if get_mission_session(storage, user_id):
            return True
        if get_hunt_session(storage, user_id):
            return True
        if get_stash_session(storage, user_id):
            return True
        if get_smuggle_session(storage, user_id):
            return True
        if get_lab_session(storage, user_id):
            return True
        if get_arena_session(storage, user_id):
            return True
        if get_duel_session_by_player(storage, user_id):
            return True
        # skip="dead": мёртвый игрок не «занят» сам по себе — иначе авто-респавн
        # (экран смерти, оффлайн-тик) никогда не сработает.
        if player_busy_reason(storage, user_id, skip="dead"):
            return True
    except Exception:
        pass
    return False


async def _maybe_respawn_dead(bot: VkMessenger, storage: Storage, event: VkEvent) -> bool:
    """Если персонаж мёртв и не в тактической сессии — death-флейвор, респавн и True."""
    user_id = int(event.user_id)
    player = storage.get_character(user_id, refresh_energy=False)
    if player is None or player.health > 0:
        return False
    if _player_in_tactical_session(storage, user_id):
        return False
    try:
        from app.game_logic import (
            append_death_log_once,
            build_dead_character_text,
            peek_death_cause,
            respawn_character,
        )

        cause = peek_death_cause(storage, user_id) or "combat"
        story = build_dead_character_text(
            player,
            where=player.location,
            cause=cause,
            storage=storage,
        )
        try:
            append_death_log_once(storage, user_id, story)
        except Exception:
            pass
        result = respawn_character(storage, user_id)
        if not result.ok:
            await bot.send_message(int(event.peer_id), result.text, reply_markup=_menu_keyboard())
        else:
            await bot.send_message(
                int(event.peer_id),
                f"{story}\n\n{result.text}",
                reply_markup=_menu_keyboard(),
            )
        return True
    except Exception as exc:
        logger.warning("respawn failed (%s): %s", user_id, exc)
        return False


async def _zone_tick(bot: VkMessenger) -> None:
    """Фон: раз в минуту завершаем переходы, проверяем сезон, таймауты и события Зоны."""
    tick = 0
    while True:
        await asyncio.sleep(60)
        tick += 1
        storage = get_storage()
        try:
            from app.game_logic import process_due_travels, try_auto_turn_in_contract

            for user_id, dest in process_due_travels(storage):
                try:
                    destination = storage.pop_arrival_notice(int(user_id)) or dest
                    text = f"🏁 Ты прибыл в «{destination}»."
                    try:
                        from app.game_logic import resolve_smuggling_if_pending

                        smuggle_text = resolve_smuggling_if_pending(storage, int(user_id))
                        if smuggle_text:
                            text += "\n\n" + smuggle_text.strip()
                    except Exception as exc:
                        logger.debug("smuggle resolve failed (%s): %s", user_id, exc)
                    auto = try_auto_turn_in_contract(storage, int(user_id))
                    if auto:
                        text += "\n\n" + auto
                    await bot.send_message(int(user_id), text)
                except Exception as exc:
                    logger.debug("travel arrival notify failed (%s): %s", user_id, exc)
        except Exception:
            logger.exception("Travel arrival tick failed")
        try:
            from datetime import datetime, timezone

            from app.game_logic import get_rating_season, process_rating_season

            season = get_rating_season(storage)
            # Если сезон истёк — сохранить топ до сброса (для титулов).
            try:
                ends_at = datetime.fromisoformat(str(season.get("ends_at") or ""))
                if ends_at.tzinfo is None:
                    ends_at = ends_at.replace(tzinfo=timezone.utc)
            except ValueError:
                ends_at = None
            closed = ends_at is not None and datetime.now(timezone.utc) >= ends_at
            if closed:
                import json as _json

                top = storage.get_season_rating_leaderboard(limit=3)
                storage.set_meta(
                    f"season:top:{int(season.get('id') or 0)}",
                    _json.dumps([dict(row) for row in top], ensure_ascii=False),
                )

            season_message = process_rating_season(storage)
            if season_message:
                for user_id in storage.list_player_ids():
                    try:
                        await bot.send_message(int(user_id), season_message)
                        await asyncio.sleep(0.05)  # анти-флуд VK при массовой рассылке
                    except Exception as exc:
                        logger.debug("season notify failed (%s): %s", user_id, exc)
                # VK-аналог наград: титулы топ-3 завершённого сезона.
                try:
                    import json as _json

                    from app.vk_titles import apply_season_titles

                    raw_top = storage.get_meta(f"season:top:{int(season.get('id') or 0)}")
                    if raw_top:
                        try:
                            top_data = _json.loads(str(raw_top))
                        except (TypeError, ValueError, _json.JSONDecodeError):
                            top_data = []
                        storage.delete_meta(f"season:top:{int(season.get('id') or 0)}")
                        notes = apply_season_titles(storage, top_data if isinstance(top_data, list) else [])
                        for uid, title_text in notes:
                            try:
                                await bot.send_message(int(uid), title_text)
                            except Exception as exc:
                                logger.debug("season title notify failed (%s): %s", uid, exc)
                except Exception as exc:
                    logger.debug("season titles failed: %s", exc)
        except Exception:
            logger.exception("Rating season tick failed")
        try:
            from app.quest_mission import process_quest_timeouts

            for user_id, result in process_quest_timeouts(storage):
                try:
                    await bot.send_message(int(user_id), result.text)
                except Exception as exc:
                    logger.debug("quest timeout notify failed (%s): %s", user_id, exc)
        except Exception:
            logger.exception("Quest timeout tick failed")
        try:
            from app.artifact_hunt import process_hunt_timeouts

            for user_id, result in process_hunt_timeouts(storage):
                try:
                    await bot.send_message(int(user_id), result.text)
                except Exception as exc:
                    logger.debug("hunt timeout notify failed (%s): %s", user_id, exc)
        except Exception:
            logger.exception("Hunt timeout tick failed")
        try:
            # События Зоны: старты/завершения рассылаются всем игрокам.
            from app.mini_events import (
                format_help_call_html,
                format_help_thanks_html,
                process_help_event_cycle,
            )
            from app.special_events import (
                format_special_call_html,
                format_special_resolve_html,
                process_special_event_cycle,
            )

            help_payload = process_help_event_cycle(storage)
            event_payloads: list[str] = []
            if help_payload:
                event = help_payload.get("event") or {}
                if help_payload.get("kind") == "call":
                    event_payloads.append(vk_plain(format_help_call_html(event)))
                else:
                    event_payloads.append(vk_plain(format_help_thanks_html(str(help_payload.get("text") or ""))))
            special_payload = process_special_event_cycle(storage)
            if special_payload:
                event = special_payload.get("event") or {}
                if special_payload.get("kind") == "call":
                    event_payloads.append(vk_plain(format_special_call_html(event)))
                else:
                    event_payloads.append(vk_plain(format_special_resolve_html(str(special_payload.get("text") or ""))))
            for text in event_payloads:
                for user_id in storage.list_player_ids():
                    try:
                        await bot.send_message(int(user_id), text)
                        await asyncio.sleep(0.05)  # анти-флуд VK
                    except Exception as exc:
                        logger.debug("event notify failed (%s): %s", user_id, exc)
        except Exception:
            logger.exception("Zone event cycle tick failed")
        # Динамические события Зоны: модификаторы локаций каждые 30–90 мин.
        if tick % 3 == 0:
            try:
                from app.game_logic import process_zone_event_cycle

                text, ids = process_zone_event_cycle(storage)
                if text:
                    for uid in ids:
                        try:
                            await bot.send_message(int(uid), text)
                            await asyncio.sleep(0.05)  # анти-флуд VK
                        except Exception as exc:
                            logger.debug("zone event notify failed (%s): %s", uid, exc)
            except Exception as exc:
                logger.debug("zone event cycle failed: %s", exc)
        # Ежедневный бонус владельца (~раз в 10 мин).
        if tick % 10 == 0:
            try:
                from app.game_logic import process_daily_rating_grants

                for owner_id, amount in process_daily_rating_grants(storage):
                    try:
                        await bot.send_message(
                            int(owner_id),
                            f"⚡ Ежедневный бонус владельца: +{int(amount)} рейтинга.",
                        )
                    except Exception as exc:
                        logger.debug("owner rating notify failed (%s): %s", owner_id, exc)
            except Exception as exc:
                logger.debug("owner rating tick failed: %s", exc)
        # Оффлайн-выживание: голод/жажда/радиация умирает и авто-респавн (~раз в 5 мин).
        if tick % 5 == 0:
            try:
                from app.game_logic import respawn_character

                for raw_id in storage.list_player_ids():
                    try:
                        user_id = int(raw_id)
                        before = storage.get_character(user_id, refresh_energy=False)
                        if before is None:
                            continue
                        # refresh_energy=True тянет и выживание (голод/жажда/радиация).
                        after = storage.get_character(user_id, refresh_energy=True)
                        if after is None or after.health > 0:
                            continue
                        if _player_in_tactical_session(storage, user_id):
                            continue
                        # Антиспам: если уже уведомляли о смерти недавно — респавним тихо.
                        if time.time() - _offline_death_notify.get(user_id, 0) < 1800:
                            respawn_character(storage, user_id)
                            continue
                        _offline_death_notify[user_id] = time.time()
                        result = respawn_character(storage, user_id)
                        await bot.send_message(
                            user_id,
                            f"☠️ Ты погиб в Зоне…\n\n{result.text}",
                            reply_markup=_menu_keyboard(),
                        )
                    except Exception as exc:
                        logger.debug("offline survival/respawn failed (%s): %s", user_id, exc)
            except Exception:
                logger.exception("Offline survival tick failed")


async def _handle_message(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    text = vk_plain(event.text).strip()
    lowered = text.lower()
    if lowered in ("старт", "start", "/start", "начать"):
        await cmd_start(bot, storage, event)
        return
    player = storage.get_character(int(event.user_id), refresh_energy=False)
    if player is None:
        # Регистрация: ждём ник.
        await _create_by_nick(bot, storage, event, text)
        return
    if player.health <= 0:
        # Мёртвый — сначала респавн, потом действие.
        if await _maybe_respawn_dead(bot, storage, event):
            return
    # Команды управления работают ДАЖЕ во время FSM-ввода (иначе «/cancel»
    # трактовался бы как сумма/цена и игрок не мог бы выйти из ввода).
    lowered_word0 = lowered.split()[0] if lowered.split() else ""
    if lowered_word0 in ("/cancel", "отмена", "отменить"):
        await _cmd_cancel(bot, storage, event)
        return
    if lowered_word0 in ("/menu", "/меню"):
        await _menu(bot, storage, event)
        return
    # FSM: ввод цены лота биржи/рынка или суммы пополнения.
    fsm = VkFSM(int(event.user_id))
    fsm_state = await fsm.get_state()
    if fsm_state == "eco_auction_price":
        await _eco_price_input(bot, storage, event, text)
        return
    if fsm_state == "topup_custom":
        await _topup_custom_input(bot, storage, event, text)
        return
    if fsm_state == "reg_referral":
        await _handle_referral_input(bot, storage, event, text)
        return
    # Новичок без группировки: выбираем её кнопками (Долг/Свобода/Нейтралы/Бандиты).
    if player.faction is None:
        if await _apply_faction_choice(bot, storage, event, text):
            return
        await bot.send_message(
            int(event.peer_id),
            "Выбери группировку кнопками ниже:",
            reply_markup=_faction_keyboard(),
        )
        return
    # Админ-команды (give / fixme / top / deleteplayer / title / setseason ...).
    lowered_word = lowered.split()[0] if lowered.split() else ""
    if lowered_word in (
        "give", "/give", "top", "/top", "fixme", "/fixme",
        "unstick", "/unstick", "deleteplayer", "/deleteplayer",
        "title", "/title", "titleoff", "/titleoff",
        "setseason", "/setseason", "confirm_topup", "/confirm_topup",
        "setfaction", "/setfaction", "leader", "/leader", "commander", "/commander",
        "settravel", "/settravel", "всем", "/всем", "all", "/all",
    ):
        if await _admin_command(bot, storage, event, lowered, text):
            return
    # «Сезон» — все игроки.
    if lowered in ("сезон", "/season", "season"):
        from app.game_logic import build_season_schedule_text

        await bot.send_message(int(event.peer_id), build_season_schedule_text(storage))
        return
    # Не вырезаем \ufe0f: reply-кнопки VK возвращают текст ровно как в клавиатуре,
    # а наши тексты меню написаны с вариативным селектором эмодзи.
    norm = text.strip()
    norm_lower = norm.lower()
    # Универсальная навигация и алиасы, чтобы reply-кнопки всегда срабатывали.
    if norm_lower in ("кпк", "пда", "паспорт"):
        await _pda_open(bot, storage, event)
        return
    if norm_lower in ("профиль",):
        await _profile(bot, storage, event)
        return
    if norm_lower in ("статус", "ст"):
        await _status(bot, storage, event)
        return
    if norm_lower in ("⬅️ назад", "назад", "в меню", "меню"):
        await _menu(bot, storage, event)
        return
    if norm_lower in ("📋 список контрактов", "список контрактов", "📋 контракты"):
        await _quest_list(bot, storage, event, 0)
        return
    # Текстовые команды для всех (аналог ТГ-команд).
    parts_cmd = norm.split()
    first_word = parts_cmd[0].lower() if parts_cmd else ""
    if first_word in ("/menu", "/меню"):
        await _menu(bot, storage, event)
        return
    if first_word in ("/info", "/help", "/справка", "help", "помощь"):
        await _cmd_help(bot, storage, event)
        return
    if first_word in ("/cancel", "отмена", "отменить"):
        await _cmd_cancel(bot, storage, event)
        return
    if first_word in ("/respawn", "респавн"):
        await _cmd_respawn(bot, storage, event)
        return
    if first_word in ("/дуэль", "/duel", "дуэль", "вызвать") and len(parts_cmd) >= 2:
        await _cmd_duel(bot, storage, event, parts_cmd[1])
        return
    if first_word in ("/pay", "pay", "перевод") and len(parts_cmd) >= 2:
        await _cmd_pay(bot, storage, event, parts_cmd[1:])
        return
    if first_word in ("/сбор", "/sbor", "сбор"):
        await _cmd_sbor(bot, storage, event, " ".join(parts_cmd[1:]))
        return
    if norm in ("🧾 Профиль", "📟 КПК"):
        # «📟 КПК» из главного меню открывает раздел, «🧾 Профиль» внутри КПК — сам профиль.
        if norm == "📟 КПК":
            await _pda_open(bot, storage, event)
        else:
            await _profile(bot, storage, event)
    elif norm == "💬 Чаты":
        await _pda_chats(bot, storage, event)
    elif norm in ("🗺 Карта", "карта"):
        await _locmap_zonemap(bot, storage, event)
    elif norm in ("📍 Локация", "локация"):
        await _walk_open(bot, storage, event)
    elif norm in ("🗺 Переход", "🗺 Переходы", "переход", "переходы"):
        await _travel_open(bot, storage, event, 0)
    elif norm in ("👥 Игроки", "игроки"):
        await _pda_players(bot, storage, event)
    elif norm in ("🤝 Обмен", "обмен"):
        await _pda_exchange(bot, storage, event)
    elif norm in ("💎 Находки", "находки"):
        await _pda_find(bot, storage, event)
    elif norm in ("💎 Арты", "арты", "мои арты"):
        await _pda_arts(bot, storage, event)
    elif norm in ("📊 Дроп", "дроп"):
        await _pda_drop(bot, storage, event)
    elif norm in ("📊 Статистика", "статистика"):
        await _pda_stats(bot, storage, event)
    elif norm == "🔗 Реферальная система":
        await _pda_referral(bot, storage, event)
    elif norm == "📡 Статус":
        await _status(bot, storage, event)
    elif norm == "🎒 Инвентарь":
        await _inventory(bot, storage, event)
    elif norm in ("🏕 Вылазка", "вылазка"):
        await _sortie_open(bot, storage, event)
    elif norm in ("🛒 Торговец", "торговец"):
        await _trader_menu(bot, storage, event)
    elif norm in ("🛠 Техник", "техник"):
        await _tech_menu(bot, storage, event)
    elif norm in ("🧑‍⚕️ Медик", "медик"):
        await _medic_menu(bot, storage, event)
    elif norm in ("🛒 Купить", "купить"):
        await _trader_buy(bot, storage, event)
    elif norm in ("💰 Продать", "продать"):
        await _trader_sell(bot, storage, event)
    elif norm in ("🔧 Ремонт", "ремонт"):
        await _trader_repair(bot, storage, event)
    elif norm in ("⚙️ Экипировка", "экипировка"):
        await _trader_equip(bot, storage, event)
    elif norm == "⬅️ В меню":
        await _menu(bot, storage, event)
    elif norm in ("🏛 Группировка", "👥 Группировка", "группировка"):
        await _faction_open(bot, storage, event)
    elif norm in ("🏛 Обзор", "обзор"):
        await _faction_overview(bot, storage, event)
    elif norm in ("💰 Казна", "казна"):
        await _faction_treasury(bot, storage, event)
    elif norm in ("🎖 Звания", "звания"):
        await _faction_ranks(bot, storage, event)
    elif norm in ("📦 Склад", "склад"):
        await _faction_warehouse(bot, storage, event)
    elif norm in ("🏗 Постройки", "постройки"):
        await _buildings_status(bot, storage, event)
    elif norm == "🏗 Статус построек":
        await _buildings_status(bot, storage, event)
    elif norm == "🔨 Построить":
        await _buildings_choose(bot, storage, event)
    elif norm in ("🏛 Клановые задачи", "клановые задачи"):
        await _faction_goals(bot, storage, event)
    elif norm in ("📣 Сбор", "📣сбор"):
        # Кнопка «📣 Сбор» из reply-меню КПК (только для командира): приходит как текст.
        await _cmd_sbor(bot, storage, event, "")
    elif norm in ("🤝 Союзы", "союзы", "дипломатия"):
        await _alliance_open(bot, storage, event)
    elif norm in ("🚚 Гараж", "гараж"):
        await _garage_open(bot, storage, event)
    elif norm in ("☢ Атака Монолита", "атака монолита"):
        await _monolith_menu(bot, storage, event)
    elif norm == "ℹ️ Информация":
        await _cmd_help(bot, storage, event)
    elif norm in ("⚔️ Дуэль", "дуэль"):
        await _duel_open(bot, storage, event)
    elif norm in ("⚔️ Арена", "арена"):
        await _arena_open(bot, storage, event)
    elif norm in ("📅 Ежедневка", "ежедневка"):
        await _daily_open(bot, storage, event)
    elif norm in ("👥 Кооп", "кооп"):
        await _coop_open(bot, storage, event)
    elif norm in ("🪖 Рейды", "рейды"):
        await _raid_open(bot, storage, event)
    elif norm in ("⚠️ Война", "⚔️ Война", "война"):
        await _war_open(bot, storage, event)
    elif norm in ("🔥 Захват точки", "захват точки"):
        await _war_open(bot, storage, event)
    elif norm in ("🏰 Военное лобби", "военное лобби"):
        await _war_war_lobby(bot, storage, event)
    elif norm == "🪖 Рейд на локацию":
        await _raid_locations(bot, storage, event)
    elif norm == "🏚 Рейд на склад врага":
        await _raid_factions(bot, storage, event)
    elif norm == "🚀 Запустить рейд":
        await _raid_launch(bot, storage, event)
    elif norm == "❌ Отменить рейд":
        await _raid_cancel(bot, storage, event)
    elif norm == "🧪 Создать лобби":
        await _coop_create(bot, storage, event)
    elif norm == "🔎 Открытые лобби":
        await _coop_list(bot, storage, event)
    elif norm in ("🛰 События", "события"):
        await _events_open(bot, storage, event)
    elif norm in ("🏆 Рейтинг", "рейтинг"):
        await _rating_open(bot, storage, event)
    elif norm in ("📋 Задания", "задания", "контракты"):
        await _quest_open(bot, storage, event)
    elif norm in ("🔔 Уведомления", "уведомления"):
        await _notify_open(bot, storage, event)
    elif norm in ("☠️ Смерти", "смерти", "журнал смертей"):
        await _death_log_open(bot, storage, event)
    elif norm in ("🏦 Барахолка", "барахолка", "экономика"):
        await _eco_open(bot, storage, event)
    elif norm in ("🚚 Контрабанда", "контрабанда"):
        await _smuggle_menu(bot, storage, event)
    elif norm in ("⭐ Пополнить", "пополнить"):
        await _topup_open(bot, storage, event)
    elif norm in ("🔥 Как не сдохнуть", "как не сдохнуть", "📘 Обучение", "обучение"):
        await _tutorial_open(bot, storage, event)
    else:
        await bot.send_message(event.peer_id, "Не понял. Используй кнопки меню.", reply_markup=_menu_keyboard())


def _match_faction(text: str) -> str | None:
    clean = (text or "").strip().lower()
    mapping = {
        "🏛 долг": "Долг", "долг": "Долг",
        "🏔 свобода": "Свобода", "свобода": "Свобода",
        "🧍 нейтралы": "Нейтралы", "нейтралы": "Нейтралы",
        "🔫 бандиты": "Бандиты", "бандиты": "Бандиты",
    }
    for key, value in mapping.items():
        if key in clean:
            return value
    return None


async def _apply_faction_choice(bot: VkMessenger, storage: Storage, event: VkEvent, text: str) -> bool:
    """Выбор группировки reply-кнопкой во время регистрации. True, если обработано."""
    faction = _match_faction(text)
    if faction is None:
        return False
    try:
        storage.set_faction(int(event.user_id), faction)
    except Exception as exc:
        logger.warning("set_faction failed (%s): %s", event.user_id, exc)
        return True
    await bot.send_message(
        int(event.peer_id),
        f"Группировка выбрана: {faction} ✅",
        reply_markup=_menu_keyboard(),
    )
    return True


async def _create_by_nick(bot: VkMessenger, storage: Storage, event: VkEvent, text: str) -> None:
    nickname = sanitize_nickname(text)
    if not nickname:
        await bot.send_message(event.peer_id, "Введите ник (2+ символа):")
        return
    try:
        storage.create_character(int(event.user_id), nickname=nickname, gender="unknown")
    except NicknameTakenError:
        await bot.send_message(event.peer_id, "Такой ник занят. Попробуй другой:")
        return
    await _gender_step(bot, storage, event)


async def _gender_step(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    """Спросить пол персонажа (после ника, до реферала и группировки)."""
    await bot.send_message(
        int(event.peer_id),
        "👤 Персонаж создан. Выбери пол:",
        reply_markup=_gendre_keyboard(),
    )


async def _start_referral_step(bot: VkMessenger, storage: Storage, event: VkEvent, nickname: str | None = None) -> None:
    """Спросить ник пригласившего (форма рефералки), затем выбрать группировку."""
    fsm = VkFSM(int(event.user_id))
    await fsm.set_state("reg_referral")
    nick = nickname or ""
    text = (
        f"Персонаж «{nick}» создан.\n\n"
        "🤝 Кто тебя пригласил в Зону? Напиши ник пригласившего сталкера.\n"
        "За это и ты, и он получите бонусы. Чтобы пропустить — напиши «—»."
    )
    await bot.send_message(
        int(event.peer_id),
        text,
        reply_markup=inline_keyboard([("Пропустить", "reg:noreferral")]),
    )


_SKIP_REFERRAL_WORDS = frozenset({"-", "—", "-", "skip", "нет", "никто", "пропустить", "дальше"})


async def _handle_referral_input(bot: VkMessenger, storage: Storage, event: VkEvent, text: str) -> None:
    from app.game_logic import apply_referral_rewards

    user_id = int(event.user_id)
    raw = (text or "").strip()
    if raw.lower() in _SKIP_REFERRAL_WORDS:
        fsm = VkFSM(user_id)
        await fsm.clear()
        await bot.send_message(int(event.peer_id), "Ок, без реферала. Выбери группировку:", reply_markup=_faction_keyboard())
        return
    referrer_id = storage.find_telegram_id_by_nickname(raw)
    if referrer_id is None:
        await bot.send_message(
            int(event.peer_id),
            f"Игрок с ником «{raw}» не найден. Попробуй ещё раз или напиши «—», чтобы пропустить.",
        )
        return
    result = apply_referral_rewards(storage, user_id, referrer_id)
    if result.ok:
        referrer = storage.get_character(referrer_id, refresh_energy=False)
        try:
            await bot.send_message(
                referrer_id,
                f"🕵 {getattr(referrer, 'nickname', referrer_id)}, новый сталкер пришёл по твоему нику. "
                f"+1250 RU начислены!",
            )
        except Exception as exc:
            logger.debug("referrer notify failed (%s): %s", referrer_id, exc)
    fsm = VkFSM(user_id)
    await fsm.clear()
    await bot.send_message(
        int(event.peer_id),
        result.text + "\n\nВыбери группировку:",
        reply_markup=_faction_keyboard(),
    )


async def _referral_info(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    player = storage.get_character(int(event.user_id), refresh_energy=False)
    if player is None:
        return
    referrer_id, invited_count = _referral_stats(storage, int(event.user_id))
    lines = [
        "🔗 Реферальная система",
        "",
        "Приглашай друзей: новый игрок при регистрации вводит ТВОЙ ник — ",
        "ты получишь +1250 RU, он — стартовый набор (тушёнка ×2, антирад, вода, аптечка).",
        "",
        f"🧾 Твой ник-приглашение: {player.nickname}",
        f"🙋 Приглашено игроков: {invited_count}",
    ]
    if referrer_id:
        referrer = storage.get_character(referrer_id, refresh_energy=False)
        referrer_nick = referrer.nickname if referrer else str(referrer_id)
        lines.append(f"🤝 Тебя пригласил: {referrer_nick}")
    await bot.send_message(
        int(event.peer_id),
        "\n".join(lines),
        reply_markup=inline_keyboard([("📟 В КПК", "pda:menu")]),
    )


def _referral_stats(storage: Storage, user_id: int) -> tuple[int | None, int]:
    """(referrer_id игрока, сколько игроков он пригласил)."""
    referrer_id: int | None = None
    invited = 0
    try:
        with storage._connect() as conn:
            row = conn.execute(
                "SELECT referrer_id FROM referrals WHERE invitee_id = ?",
                (int(user_id),),
            ).fetchone()
            if row is not None and row["referrer_id"] is not None:
                referrer_id = int(row["referrer_id"])
            cnt = conn.execute(
                "SELECT COUNT(*) AS c FROM referrals WHERE referrer_id = ?",
                (int(user_id),),
            ).fetchone()
            invited = int(cnt["c"]) if cnt else 0
    except Exception as exc:
        logger.debug("referral stats failed: %s", exc)
    return referrer_id, invited


async def _referral_callback(bot: VkMessenger, storage: Storage, event: VkEvent, payload: str) -> None:
    parts = payload.split(":")
    cmd = parts[1] if len(parts) > 1 else ""
    if cmd == "info":
        await _referral_info(bot, storage, event)
    else:
        await _menu(bot, storage, event)


def sanitize_nickname(raw: str) -> str:
    import re

    cleaned = re.sub(r"<[^>]+>", "", str(raw or "")).strip()
    cleaned = cleaned.replace("&", "").replace('"', "").replace("'", "")
    return cleaned[:32]


# ---------------------------------------------------------------------------
# Callback-события (нажатия inline-кнопок)
# ---------------------------------------------------------------------------


async def _handle_callback(bot: VkMessenger, storage: Storage, event: VkEvent) -> None:
    payload = (event.payload or "").strip()
    # Мёртвый вне тактической сессии — респавн и выход из боевого цикла.
    player = storage.get_character(int(event.user_id), refresh_energy=False)
    if player is not None and player.health <= 0 and not payload.startswith(("respawn:",)):
        if not _player_in_tactical_session(storage, int(event.user_id)):
            if await _maybe_respawn_dead(bot, storage, event):
                return
    if payload.startswith("inventory:"):
        await _inventory_callback(bot, storage, event, payload)
        return
    if payload.startswith("alliance:"):
        await _alliance_callback(bot, storage, event, payload)
        return
    if payload.startswith("monolith:"):
        await _monolith_callback(bot, storage, event, payload)
        return
    if payload.startswith("topup:"):
        await _topup_callback(bot, storage, event, payload)
        return
    if payload.startswith("daily:"):
        await _daily_callback(bot, storage, event, payload)
        return
    if payload.startswith("rating:"):
        await _rating_callback(bot, storage, event, payload)
        return
    if payload.startswith("quest:"):
        await _quest_callback(bot, storage, event, payload)
        return
    if payload.startswith("qmission:"):
        await _qmission_callback(bot, storage, event, payload)
        return
    if payload.startswith("travel:"):
        await _travel_callback(bot, storage, event, payload)
        return
    if payload.startswith("hunt:"):
        await _hunt_callback(bot, storage, event, payload)
        return
    if payload.startswith("sthunt:"):
        await _stash_callback(bot, storage, event, payload)
        return
    if payload.startswith("events:"):
        await _events_callback(bot, storage, event, payload)
        return
    if payload.startswith("tutorial:"):
        await _tutorial_callback(bot, storage, event, payload)
        return
    if payload.startswith("notify:"):
        await _notify_callback(bot, storage, event, payload)
        return
    if payload.startswith("death:"):
        await _death_callback(bot, storage, event, payload)
        return
    if payload.startswith("stash:") and not payload.startswith("sthunt:"):
        # Внимание: sthunt:* обрабатывается выше; здесь — личный хабар stash:*.
        await _personal_stash_callback(bot, storage, event, payload)
        return
    if payload.startswith("eco:"):
        await _eco_callback(bot, storage, event, payload)
        return
    if payload.startswith("smission:"):
        await _smission_callback(bot, storage, event, payload)
        return
    if payload.startswith("labact:"):
        await _lab_callback(bot, storage, event, payload)
        return
    if payload.startswith("lair:"):
        await _lair_callback(bot, storage, event, payload)
        return
    if payload.startswith("secrettrader:"):
        await _secret_trader_callback(bot, storage, event, payload)
        return
    if payload.startswith("locmap:"):
        await _locmap_callback(bot, storage, event, payload)
        return
    if payload.startswith("trade:"):
        await _trader_callback(bot, storage, event, payload)
        return
    if payload.startswith("duel:"):
        await _duel_callback(bot, storage, event, payload)
        return
    if payload.startswith("arena:"):
        await _arena_callback(bot, storage, event, payload)
        return
    if payload.startswith("coop:"):
        await _coop_callback(bot, storage, event, payload)
        return
    if payload.startswith("raid:"):
        await _raid_callback(bot, storage, event, payload)
        return
    if payload.startswith("war:"):
        await _war_callback(bot, storage, event, payload)
        return
    if payload.startswith("faction:"):
        await _faction_callback(bot, storage, event, payload)
        return
    if payload.startswith("build:"):
        await _buildings_callback(bot, storage, event, payload)
        return
    if payload.startswith("reg:noreferral"):
        fsm = VkFSM(int(event.user_id))
        await fsm.clear()
        await bot.send_message(
            event.peer_id,
            "Ок, без реферала. Выбери группировку:",
            reply_markup=_faction_keyboard(),
        )
        return
    if payload.startswith("reg:gender"):
        gender = payload.split(":")[-1]
        # Только валидные значения; остальное игнорируем (не пишем мусор в БД).
        if gender in ("male", "female"):
            try:
                with storage._connect() as conn:
                    conn.execute(
                        "UPDATE characters SET gender = ? WHERE telegram_id = ?",
                        (gender, int(event.user_id)),
                    )
            except Exception as exc:
                logger.warning("gender update failed: %s", exc)
        await _start_referral_step(bot, storage, event)
        return
    if payload.startswith("ref:"):
        await _referral_callback(bot, storage, event, payload)
        return
    if payload.startswith("pda:"):
        await _pda_callback(bot, storage, event, payload)
        return
    if payload.startswith("players:"):
        await _players_callback(bot, storage, event, payload)
        return
    if payload.startswith("walk:"):
        cmd = payload.split(":")[-1]
        if cmd in ("up", "left", "down", "right"):
            await _walk_move(bot, storage, event, cmd)
        elif cmd == "inspect":
            await _walk_inspect(bot, storage, event)
        elif cmd == "refresh":
            player = storage.get_character(int(event.user_id), refresh_energy=True)
            if player is not None:
                await _walk_render(bot, storage, event, player)
        elif cmd == "exit":
            await _menu(bot, storage, event)
        return
    await _menu(bot, storage, event)


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def main(use_webhook: bool | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    _init_admin_ids(settings)
    get_storage(settings)
    _probe_db()
    # VK_USE_CALLBACK=1 (или флаг --webhook) вводит webhook-режим;
    # флаг --longpoll (use_webhook=False) форсирует Long Poll даже при VK_USE_CALLBACK=1.
    if use_webhook is True:
        use_callback_mode = True
    elif use_webhook is False:
        use_callback_mode = False
    else:
        use_callback_mode = bool(settings.use_callback)
    logger.info("Запуск vk_stalker (version=%s, mode=%s)", APP_VERSION, "webhook" if use_callback_mode else "longpoll")
    if use_callback_mode:
        from app.vk_platform import run_webhook

        logger.info("VK Callback webhook стартует (port=%s)", settings.webhook_port)
        run_webhook(settings, handle_event, background=[_zone_tick])
    else:
        logger.info("VK Long Poll стартует (group_id=%s)", settings.group_id)
        run_longpoll(settings, handle_event, background=[_zone_tick])


if __name__ == "__main__":
    main()