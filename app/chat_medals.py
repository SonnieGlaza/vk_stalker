"""Медальки в беседах: custom title у ника (ставит админ командой /medal)."""

from __future__ import annotations

import json
import logging
import re


from app.season_chat_titles import (
    ZONE_COMMON_CHAT_ID,
    ZONE_FACTION_CHAT_IDS,
    ChatTitleError,
    _demote_member,
    _promote_title_only,
    _telegram_error_text,
    zone_chat_label,
)
from app.storage import Storage

logger = logging.getLogger(__name__)

CHAT_MEDAL_META_PREFIX = "chat_medal:"
CHAT_MEDAL_INDEX_META = "chat_medals:index"
CHAT_MEDAL_MAX_LEN = 16
BOT_GROUP_CHATS_META = "bot_group_chats"


def _medal_key(telegram_id: int) -> str:
    return f"{CHAT_MEDAL_META_PREFIX}{int(telegram_id)}"


def sanitize_medal_title(raw: str) -> str:
    """Telegram: custom title ≤ 16 символов, без эмодзи."""
    text = (raw or "").strip()
    text = re.sub(r"[\U00010000-\U0010ffff]", "", text)
    text = re.sub(r"[\u2600-\u27bf\u2300-\u23ff\ufe0f]", "", text)
    text = " ".join(text.split())
    return text[:CHAT_MEDAL_MAX_LEN]


def get_chat_medal(storage: Storage, telegram_id: int) -> str | None:
    raw = storage.get_meta(_medal_key(telegram_id))
    title = sanitize_medal_title(str(raw or ""))
    return title or None


def _load_index(storage: Storage) -> list[int]:
    raw = storage.get_meta(CHAT_MEDAL_INDEX_META)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[int] = []
    for item in data:
        try:
            tid = int(item)
        except (TypeError, ValueError):
            continue
        if tid > 0 and tid not in out:
            out.append(tid)
    return out


def _save_index(storage: Storage, ids: list[int]) -> None:
    storage.set_meta(CHAT_MEDAL_INDEX_META, json.dumps(ids))


def save_chat_medal(storage: Storage, telegram_id: int, title: str) -> str:
    clean = sanitize_medal_title(title)
    if not clean:
        raise ValueError("Пустой титул. Нужен текст без эмодзи, до 16 символов.")
    storage.set_meta(_medal_key(telegram_id), clean)
    ids = _load_index(storage)
    if int(telegram_id) not in ids:
        ids.append(int(telegram_id))
        _save_index(storage, ids)
    return clean


def clear_saved_chat_medal(storage: Storage, telegram_id: int) -> None:
    storage.delete_meta(_medal_key(telegram_id))
    ids = [tid for tid in _load_index(storage) if tid != int(telegram_id)]
    _save_index(storage, ids)


def medal_chat_targets(storage: Storage, telegram_id: int | None = None) -> list[int]:
    """Все супергруппы, где бот висел: чаты Зоны + любые другие, куда его добавили."""
    del telegram_id  # раньше фильтровали по фракции; титул ставим везде, где человек есть
    chats: list[int] = []
    seen: set[int] = set()
    for chat_id in (
        ZONE_COMMON_CHAT_ID,
        *ZONE_FACTION_CHAT_IDS.values(),
        *list_known_group_chat_ids(storage),
    ):
        try:
            cid = int(chat_id)
        except (TypeError, ValueError):
            continue
        if cid >= 0 or cid in seen:
            continue
        seen.add(cid)
        chats.append(cid)
    return chats


def list_known_group_chat_ids(storage: Storage) -> list[int]:
    raw = storage.get_meta(BOT_GROUP_CHATS_META)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[int] = []
    for item in data:
        try:
            cid = int(item)
        except (TypeError, ValueError):
            continue
        if cid < 0 and cid not in out:
            out.append(cid)
    return out


def remember_group_chat(storage: Storage, chat_id: int) -> None:
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return
    if cid >= 0:
        return
    ids = list_known_group_chat_ids(storage)
    if cid in ids:
        return
    ids.append(cid)
    storage.set_meta(BOT_GROUP_CHATS_META, json.dumps(ids))


def forget_group_chat(storage: Storage, chat_id: int) -> None:
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return
    ids = [item for item in list_known_group_chat_ids(storage) if item != cid]
    if ids:
        storage.set_meta(BOT_GROUP_CHATS_META, json.dumps(ids))
    else:
        storage.delete_meta(BOT_GROUP_CHATS_META)


def resolve_group_title(storage: Storage, telegram_id: int) -> str | None:
    """Приоритет: явный /medal, иначе титул с игровой медали /badge."""
    explicit = get_chat_medal(storage, telegram_id)
    if explicit:
        return explicit
    from app.player_medals import chat_title_for_player

    return chat_title_for_player(storage, telegram_id)


async def _apply_title_in_chats(
    bot: Any,
    telegram_id: int,
    title: str,
    chat_ids: list[int],
) -> list[str]:
    if not chat_ids:
        return ["нет известных групп — напиши /start в нужном чате или сделай бота админом"]
    notes: list[str] = []
    ok = 0
    fails = 0
    for chat_id in chat_ids:
        label = zone_chat_label(chat_id)
        try:
            await _promote_title_only(bot, chat_id, telegram_id, title)
            notes.append(f"ок: «{title}» — {label}")
            ok += 1
        except ChatTitleError as exc:
            text = str(exc)
            if "игрока нет" in text.lower():
                notes.append(f"пропуск: {label} — {exc}")
            else:
                notes.append(f"нет: {label} — {exc}")
                fails += 1
        except Exception as exc:
            logger.exception("Failed to set title %r for %s in %s", title, telegram_id, chat_id)
            notes.append(f"нет: {label} — {_telegram_error_text(exc)}")
            fails += 1
    if ok == 0:
        if fails:
            notes.insert(
                0,
                "Титул в группах не встал. Telegram ставит плашку только админу, "
                "которого повысил сам бот: нужно право «добавлять администраторов» "
                "плюс видеочаты/закреп, человек — участник (не админ от другого человека).",
            )
        else:
            notes.insert(0, "Человека нет ни в одном известном чате — титул ставить негде.")
    return notes


async def apply_chat_medal(bot: Any, storage: Storage, telegram_id: int, title: str) -> list[str]:
    clean = save_chat_medal(storage, telegram_id, title)
    return await _apply_title_in_chats(bot, telegram_id, clean, medal_chat_targets(storage))


async def apply_resolved_chat_title(
    bot: Any,
    storage: Storage,
    telegram_id: int,
    *,
    chat_ids: list[int] | None = None,
) -> list[str]:
    title = resolve_group_title(storage, telegram_id)
    if not title:
        return []
    targets = chat_ids if chat_ids is not None else medal_chat_targets(storage)
    return await _apply_title_in_chats(bot, int(telegram_id), title, targets)


async def remove_chat_medal(bot: Any, storage: Storage, telegram_id: int) -> list[str]:
    notes: list[str] = []
    for chat_id in medal_chat_targets(storage):
        try:
            await _demote_member(bot, chat_id, telegram_id)
            notes.append(f"снята в чате {chat_id}")
        except Exception:
            logger.exception("Failed to clear medal for %s in %s", telegram_id, chat_id)
            notes.append(f"не снялась в {chat_id}")
    clear_saved_chat_medal(storage, telegram_id)
    leftover = resolve_group_title(storage, telegram_id)
    if leftover:
        notes.extend(await apply_resolved_chat_title(bot, storage, telegram_id))
        notes.append(f"вместо /medal стоит титул с медали: «{leftover}»")
    return notes


def titled_player_ids(storage: Storage) -> list[int]:
    from app.player_medals import chat_title_for_player

    ids: list[int] = []
    seen: set[int] = set()
    for tid in _load_index(storage):
        if tid not in seen:
            seen.add(tid)
            ids.append(tid)
    for tid in storage.list_player_ids():
        if tid in seen:
            continue
        if chat_title_for_player(storage, tid):
            seen.add(tid)
            ids.append(tid)
    return ids


async def sync_all_chat_titles(bot: Any, storage: Storage) -> list[str]:
    """Проставить актуальные титулы во всех известных группах."""
    notes: list[str] = []
    for telegram_id in titled_player_ids(storage):
        notes.extend(await apply_resolved_chat_title(bot, storage, telegram_id))
    return notes


async def reapply_all_chat_medals(bot: Any, storage: Storage) -> list[str]:
    """После сезонных титулов вернуть /medal и /badge (они приоритетнее)."""
    return await sync_all_chat_titles(bot, storage)


def format_medal_profile_line(storage: Storage, telegram_id: int) -> str:
    title = resolve_group_title(storage, telegram_id)
    if not title:
        return ""
    return f"🎖 Титул в беседах: {title}\n"