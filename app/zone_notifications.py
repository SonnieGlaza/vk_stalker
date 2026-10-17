"""Уведомления игрокам о событиях на их локации."""

from __future__ import annotations

import logging
from typing import Any

from app.game_logic import is_notify_enabled
from app.storage import Storage

logger = logging.getLogger(__name__)


async def notify_players_at_location(
    bot: Any,
    storage: Storage,
    location: str,
    text: str,
    *,
    notify_key: str = "zone_event",
) -> int:
    """Разослать текст игрокам на локации (с учётом настроек). Возвращает число отправок."""
    loc = (location or "").strip()
    if not loc:
        return 0
    sent = 0
    for row in storage.list_characters_at_location(loc):
        try:
            telegram_id = int(row["telegram_id"])
        except (TypeError, ValueError, KeyError):
            continue
        if not is_notify_enabled(storage, telegram_id, notify_key):
            continue
        try:
            bot.send_message(telegram_id, text)
            sent += 1
        except Exception:
            logger.debug("Zone notify failed for %s", telegram_id, exc_info=True)
    return sent


async def notify_help_event_started(bot: Bot, storage: Storage, event: dict[str, Any]) -> int:
    loc = str(event.get("location") or "")
    speaker = str(event.get("speaker") or "Сталкер")
    text = (
        f"📻 Вызов на «{loc}»: {speaker} просит помощь по рации.\n"
        "Ты на месте — откликнись в общем чате или через событие."
    )
    return await notify_players_at_location(bot, storage, loc, text)


async def notify_special_event_started(bot: Bot, storage: Storage, event: dict[str, Any]) -> int:
    loc = str(event.get("location") or "")
    title = str(event.get("title") or event.get("kind") or "Событие")
    call = str(event.get("call_text") or "")[:200]
    text = f"⚡ {title} на «{loc}».\n{call}".strip()
    if loc:
        return await notify_players_at_location(bot, storage, loc, text)
    return 0
