"""Сезонные custom title в чатах Зоны (админ без прав + титул)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any


from app.storage import Storage

logger = logging.getLogger(__name__)

_DEFAULT_ZONE_COMMON_CHAT_ID = -1003958853707
_DEFAULT_ZONE_FACTION_CHAT_IDS: dict[str, int] = {
    "Бандиты": -1004375297519,
    "Свобода": -1003883863150,
    "Долг": -1004377044940,
    "Нейтралы": -1004295857240,
}


def _parse_chat_id(raw: str) -> int | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _load_zone_common_chat_id() -> int:
    parsed = _parse_chat_id(os.getenv("ZONE_COMMON_CHAT_ID", ""))
    return parsed if parsed is not None else _DEFAULT_ZONE_COMMON_CHAT_ID


def _load_zone_faction_chat_ids() -> dict[str, int]:
    result = dict(_DEFAULT_ZONE_FACTION_CHAT_IDS)
    for faction in _DEFAULT_ZONE_FACTION_CHAT_IDS:
        env_key = f"ZONE_CHAT_{faction.upper()}"
        parsed = _parse_chat_id(os.getenv(env_key, ""))
        if parsed is not None:
            result[faction] = parsed
    return result


ZONE_COMMON_CHAT_ID = _load_zone_common_chat_id()
ZONE_FACTION_CHAT_IDS: dict[str, int] = _load_zone_faction_chat_ids()

# Telegram: custom_title ≤ 16 символов, без эмодзи.
SEASON_CHAT_TITLE_BY_PLACE: dict[int, str] = {
    1: "Чемпион Зоны",
    2: "Серебро сезона",
    3: "Бронза сезона",
}

SEASON_CHAT_TITLE_HOLDERS_META = "season_chat_title_holders"
SEASON_CHAT_TITLE_PENDING_META = "season_chat_title_pending"


def season_chat_title_for_place(place: int) -> str | None:
    title = SEASON_CHAT_TITLE_BY_PLACE.get(int(place))
    if not title:
        return None
    # Защита от случайного превышения лимита Telegram.
    return title[:16]


def build_season_chat_title_jobs(
    storage: Storage, top: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Список назначений: общий чат + чат группировки победителя."""
    jobs: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for place, row in enumerate(top[:3], start=1):
        title = season_chat_title_for_place(place)
        if not title:
            continue
        try:
            user_id = int(row.get("telegram_id") or 0)
        except (TypeError, ValueError):
            continue
        if user_id <= 0:
            continue
        chat_targets = [ZONE_COMMON_CHAT_ID]
        character = storage.get_character(user_id, refresh_energy=False)
        faction = str(getattr(character, "faction", "") or "") if character else ""
        faction_chat = ZONE_FACTION_CHAT_IDS.get(faction)
        if faction_chat is not None:
            chat_targets.append(int(faction_chat))
        for chat_id in chat_targets:
            key = (chat_id, user_id)
            if key in seen:
                continue
            seen.add(key)
            jobs.append(
                {
                    "chat_id": int(chat_id),
                    "user_id": user_id,
                    "title": title,
                    "place": place,
                    "faction": faction,
                }
            )
    return jobs


def queue_season_chat_titles(storage: Storage, top: list[dict[str, Any]]) -> int:
    """Сохраняет задания на титулы после конца сезона (бот применит асинхронно)."""
    jobs = build_season_chat_title_jobs(storage, top)
    storage.set_meta(SEASON_CHAT_TITLE_PENDING_META, json.dumps({"jobs": jobs}, ensure_ascii=False))
    return len(jobs)


def _load_json_meta(storage: Storage, key: str) -> dict[str, Any]:
    raw = storage.get_meta(key)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_holders(storage: Storage, holders: list[dict[str, Any]]) -> None:
    storage.set_meta(
        SEASON_CHAT_TITLE_HOLDERS_META,
        json.dumps({"holders": holders}, ensure_ascii=False),
    )


class ChatTitleError(RuntimeError):
    """Понятная причина, почему титул не встал в конкретном чате."""


# Безобидные права для «титульной» админки. Telegram ставит custom title только
# админам, которых повысил сам бот, и только правом, которое у бота есть.
_DUMMY_PROMOTE_RIGHTS: tuple[str, ...] = (
    "can_manage_video_chats",
    "can_pin_messages",
    "can_invite_users",
    "can_change_info",
)

_PROMOTE_FALSE: dict[str, bool] = {
    "is_anonymous": False,
    "can_manage_chat": False,
    "can_delete_messages": False,
    "can_manage_video_chats": False,
    "can_restrict_members": False,
    "can_promote_members": False,
    "can_change_info": False,
    "can_invite_users": False,
    "can_pin_messages": False,
    "can_manage_topics": False,
}


def _member_status(member: Any) -> str:
    raw = getattr(member, "status", "")
    return str(getattr(raw, "value", raw) or "").lower()


def zone_chat_label(chat_id: int) -> str:
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return f"чат {chat_id}"
    if cid == ZONE_COMMON_CHAT_ID:
        return "общий чат Зоны"
    for name, faction_id in ZONE_FACTION_CHAT_IDS.items():
        if int(faction_id) == cid:
            return f"чат {name}"
    return f"чат {cid}"


def _telegram_error_text(exc: BaseException) -> str:
    text = str(exc)
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    return " ".join(text.split())[:180]


def _is_title_race_error(exc: BaseException) -> bool:
    lowered = str(exc).lower()
    return (
        "not an administrator" in lowered
        or "user_not_participant" in lowered
        or "not enough rights to change custom title" in lowered
    )


def _is_custom_title_forbidden(exc: BaseException) -> bool:
    return "not enough rights to change custom title" in str(exc).lower()


def _is_promote_forbidden(exc: BaseException) -> bool:
    if _is_custom_title_forbidden(exc):
        return False
    lowered = str(exc).lower()
    return "right_forbidden" in lowered or "not enough rights" in lowered


def _title_only_promote_kwargs(dummy_right: str) -> dict[str, bool]:
    kwargs = dict(_PROMOTE_FALSE)
    if dummy_right not in kwargs:
        raise ValueError(f"unknown dummy promote right: {dummy_right}")
    kwargs[dummy_right] = True
    return kwargs


def _pick_dummy_rights(bot_member: Any) -> list[str]:
    """Права, которыми бот может выдать титульную админку (у него они сами True)."""
    if _member_status(bot_member) == "creator":
        return list(_DUMMY_PROMOTE_RIGHTS)
    known_true = [name for name in _DUMMY_PROMOTE_RIGHTS if getattr(bot_member, name, None) is True]
    if known_true:
        return known_true
    if all(getattr(bot_member, name, None) is False for name in _DUMMY_PROMOTE_RIGHTS):
        return []
    return list(_DUMMY_PROMOTE_RIGHTS)


def _promote_block_reason(bot_member: Any) -> str | None:
    status = _member_status(bot_member)
    if status == "creator":
        return None
    if status != "administrator":
        return "бот не админ в этом чате"
    if getattr(bot_member, "can_promote_members", None) is False:
        return "боту нужно право «добавлять администраторов»"
    return None


async def _get_bot_member(bot: Any, chat_id: int) -> Any:
    me = await bot.get_me()
    try:
        return await bot.get_chat_member(chat_id, me.id)
    except TelegramBadRequest as exc:
        raise ChatTitleError(f"бот не видит чат ({_telegram_error_text(exc)})") from exc


async def _bot_can_promote(bot: Any, chat_id: int) -> str | None:
    """None если можно назначать админов, иначе причина."""
    try:
        bot_member = await _get_bot_member(bot, chat_id)
    except ChatTitleError as exc:
        return str(exc)
    return _promote_block_reason(bot_member)


async def _set_custom_title_with_retry(bot: Any, chat_id: int, user_id: int, title: str) -> None:
    last_exc: BaseException | None = None
    for delay in (0.0, 0.45, 0.9, 1.6, 2.4):
        if delay:
            await asyncio.sleep(delay)
        try:
            await bot.set_chat_administrator_custom_title(
                chat_id=chat_id,
                user_id=user_id,
                custom_title=title[:16],
            )
            return
        except TelegramBadRequest as exc:
            last_exc = exc
            if _is_title_race_error(exc):
                continue
            raise
    if last_exc is not None:
        raise last_exc


async def _demote_member(bot: Any, chat_id: int, user_id: int) -> None:
    """Снять админку (все права False = demote по Bot API)."""
    await bot.promote_chat_member(chat_id=chat_id, user_id=user_id, **dict(_PROMOTE_FALSE))


async def _promote_dummy(bot: Any, chat_id: int, user_id: int, dummy_rights: list[str]) -> None:
    if not dummy_rights:
        raise ChatTitleError(
            "боту нечем выдать титульную админку: кроме «добавлять администраторов» "
            "дайте ещё безобидное право (видеочаты, закреп сообщений или приглашения)"
        )
    last_exc: BaseException | None = None
    for right in dummy_rights:
        try:
            await bot.promote_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                **_title_only_promote_kwargs(right),
            )
            return
        except TelegramBadRequest as exc:
            last_exc = exc
            if _is_promote_forbidden(exc):
                continue
            raise ChatTitleError(f"не вышло назначить админом ({_telegram_error_text(exc)})") from exc
    raise ChatTitleError(
        f"не вышло назначить админом ({_telegram_error_text(last_exc) if last_exc else 'RIGHT_FORBIDDEN'}). "
        "боту нужно хотя бы одно своё право вроде видеочатов или закрепа"
    ) from last_exc


async def _reclaim_admin(bot: Any, chat_id: int, user_id: int, dummy_rights: list[str]) -> None:
    """Снять чужую админку и выдать свою — иначе Telegram не даёт сменить титул."""
    try:
        await _demote_member(bot, chat_id, user_id)
    except TelegramBadRequest as exc:
        raise ChatTitleError(
            "админку выдавал не бот, снять её бот не может. "
            "Снимите игрока с администраторов вручную и повторите команду"
        ) from exc
    await asyncio.sleep(0.9)
    await _promote_dummy(bot, chat_id, user_id, dummy_rights)
    await asyncio.sleep(0.8)


async def _set_title_or_reclaim(
    bot: Any,
    chat_id: int,
    user_id: int,
    title: str,
    dummy_rights: list[str],
) -> None:
    try:
        await _set_custom_title_with_retry(bot, chat_id, user_id, title)
        return
    except TelegramBadRequest as exc:
        lowered = str(exc).lower()
        can_reclaim = _is_custom_title_forbidden(exc) or "not an administrator" in lowered
        if not can_reclaim:
            raise ChatTitleError(f"титул не записался ({_telegram_error_text(exc)})") from exc
        logger.info(
            "Reclaiming admin for custom title user=%s chat=%s: %s",
            user_id,
            chat_id,
            _telegram_error_text(exc),
        )
    await _reclaim_admin(bot, chat_id, user_id, dummy_rights)
    try:
        await _set_custom_title_with_retry(bot, chat_id, user_id, title)
    except TelegramBadRequest as exc:
        raise ChatTitleError(f"титул не записался ({_telegram_error_text(exc)})") from exc


async def _promote_title_only(bot: Any, chat_id: int, user_id: int, title: str) -> None:
    """Админ почти без прав + custom title. Канальные права не шлём — из-за них группа отвечает 400."""
    bot_member = await _get_bot_member(bot, chat_id)
    reason = _promote_block_reason(bot_member)
    if reason:
        raise ChatTitleError(reason)
    dummy_rights = _pick_dummy_rights(bot_member)

    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramBadRequest as exc:
        raise ChatTitleError(f"игрока нет в чате ({_telegram_error_text(exc)})") from exc

    status = _member_status(member)
    if status in {"left", "kicked"}:
        raise ChatTitleError("игрока нет в этом чате")
    if status == "creator":
        raise ChatTitleError("это владелец чата, бот не может сменить его титул")

    if status == "administrator":
        try:
            await bot.set_chat_administrator_custom_title(
                chat_id=chat_id,
                user_id=user_id,
                custom_title=title[:16],
            )
            return
        except TelegramBadRequest as exc:
            if not (_is_custom_title_forbidden(exc) or "not an administrator" in str(exc).lower()):
                raise ChatTitleError(f"титул не записался ({_telegram_error_text(exc)})") from exc
            logger.info(
                "Admin title blocked user=%s chat=%s: %s — demote and re-promote",
                user_id,
                chat_id,
                _telegram_error_text(exc),
            )
            await _reclaim_admin(bot, chat_id, user_id, dummy_rights)
            try:
                await _set_custom_title_with_retry(bot, chat_id, user_id, title)
                return
            except TelegramBadRequest as retry_exc:
                raise ChatTitleError(
                    f"титул не записался ({_telegram_error_text(retry_exc)})"
                ) from retry_exc

    await _promote_dummy(bot, chat_id, user_id, dummy_rights)
    await _set_title_or_reclaim(bot, chat_id, user_id, title, dummy_rights)


async def apply_pending_season_chat_titles(bot: Any, storage: Storage) -> list[str]:
    """Снимает прошлые титулы и ставит новые из pending. Возвращает лог-строки."""
    pending = _load_json_meta(storage, SEASON_CHAT_TITLE_PENDING_META)
    jobs = pending.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        return []

    notes: list[str] = []
    old = _load_json_meta(storage, SEASON_CHAT_TITLE_HOLDERS_META)
    old_holders = old.get("holders") if isinstance(old.get("holders"), list) else []

    for holder in old_holders:
        try:
            chat_id = int(holder.get("chat_id") or 0)
            user_id = int(holder.get("user_id") or 0)
        except (TypeError, ValueError):
            continue
        if chat_id == 0 or user_id <= 0:
            continue
        try:
            await _demote_member(bot, chat_id, user_id)
            notes.append(f"снят титул у {user_id} в {chat_id}")
        except Exception:
            logger.exception("Failed to demote season title holder %s in %s", user_id, chat_id)

    new_holders: list[dict[str, Any]] = []
    failed_jobs: list[dict[str, Any]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        try:
            chat_id = int(job.get("chat_id") or 0)
            user_id = int(job.get("user_id") or 0)
            title = str(job.get("title") or "")[:16]
        except (TypeError, ValueError):
            continue
        if chat_id == 0 or user_id <= 0 or not title:
            continue
        try:
            await _promote_title_only(bot, chat_id, user_id, title)
            new_holders.append({"chat_id": chat_id, "user_id": user_id, "title": title})
            notes.append(f"{user_id} → «{title}» в {chat_id}")
        except Exception:
            logger.exception(
                "Failed to set season title %r for %s in %s", title, user_id, chat_id
            )
            failed_jobs.append(
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "title": title,
                    "place": int(job.get("place") or 0),
                    "faction": str(job.get("faction") or ""),
                }
            )

    _save_holders(storage, new_holders)
    if failed_jobs:
        storage.set_meta(
            SEASON_CHAT_TITLE_PENDING_META,
            json.dumps({"jobs": failed_jobs}, ensure_ascii=False),
        )
    else:
        storage.set_meta(SEASON_CHAT_TITLE_PENDING_META, "")
    return notes
