"""Отложенное удаление сообщений бота (меньше спама в чатах)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.storage import Storage, utc_now

logger = logging.getLogger(__name__)

EPHEMERAL_QUEUE_META = "ephemeral_msg_queue"
EPHEMERAL_QUEUE_MAX = 500

# Бой / исход в личке — держим час.
BATTLE_MESSAGE_TTL_SECONDS = 3600
# Исход в общем чате (благодарность, resolve) — тоже час.
GROUP_OUTCOME_TTL_SECONDS = 3600


def _parse_delete_at(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _load_queue(storage: Storage) -> list[dict[str, Any]]:
    raw = storage.get_meta(EPHEMERAL_QUEUE_META)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return []


def _save_queue(storage: Storage, queue: list[dict[str, Any]]) -> None:
    if len(queue) > EPHEMERAL_QUEUE_MAX:
        dropped = len(queue) - EPHEMERAL_QUEUE_MAX
        logger.warning(
            "Ephemeral delete queue truncated: dropped %s oldest entries (cap %s)",
            dropped,
            EPHEMERAL_QUEUE_MAX,
        )
        queue = queue[-EPHEMERAL_QUEUE_MAX:]
    if not queue:
        storage.delete_meta(EPHEMERAL_QUEUE_META)
        return
    storage.set_meta(EPHEMERAL_QUEUE_META, json.dumps(queue[-EPHEMERAL_QUEUE_MAX:], ensure_ascii=False))


def schedule_message_deletion(
    storage: Storage,
    chat_id: int,
    message_id: int,
    *,
    ttl_seconds: int = BATTLE_MESSAGE_TTL_SECONDS,
    kind: str = "battle",
) -> None:
    """Поставить сообщение в очередь на удаление через ttl_seconds."""
    if int(message_id) <= 0:
        return
    delete_at = utc_now() + timedelta(seconds=max(1, int(ttl_seconds)))
    queue = _load_queue(storage)
    key = (int(chat_id), int(message_id))
    queue = [item for item in queue if (int(item.get("chat_id", 0)), int(item.get("message_id", 0))) != key]
    queue.append(
        {
            "chat_id": int(chat_id),
            "message_id": int(message_id),
            "delete_at": delete_at.isoformat(),
            "kind": str(kind or "battle"),
        }
    )
    _save_queue(storage, queue)


def schedule_messages_deletion(
    storage: Storage,
    items: dict[str, int] | list[tuple[int, int]],
    *,
    ttl_seconds: int = BATTLE_MESSAGE_TTL_SECONDS,
    kind: str = "battle",
) -> None:
    if isinstance(items, dict):
        pairs = [(int(pid), int(mid)) for pid, mid in items.items() if int(mid) > 0]
    else:
        pairs = [(int(chat_id), int(mid)) for chat_id, mid in items if int(mid) > 0]
    for chat_id, message_id in pairs:
        schedule_message_deletion(
            storage,
            chat_id,
            message_id,
            ttl_seconds=ttl_seconds,
            kind=kind,
        )


def cancel_message_deletion(storage: Storage, chat_id: int, message_id: int) -> None:
    key = (int(chat_id), int(message_id))
    queue = _load_queue(storage)
    new_queue = [
        item
        for item in queue
        if (int(item.get("chat_id", 0)), int(item.get("message_id", 0))) != key
    ]
    if len(new_queue) != len(queue):
        _save_queue(storage, new_queue)


def delete_message_safe(bot: Any, chat_id: int, message_id: int) -> bool:
    try:
        return bool(bot.delete_message(chat_id=int(chat_id), message_id=int(message_id)))
    except Exception:
        logger.debug("Failed to delete message %s in %s", message_id, chat_id, exc_info=True)
        return False


async def process_ephemeral_message_queue(bot: Bot, storage: Storage) -> int:
    """Удалить сообщения, у которых истёк срок. Возвращает число успешных удалений."""
    now = utc_now()
    queue = _load_queue(storage)
    if not queue:
        return 0
    remaining: list[dict[str, Any]] = []
    deleted = 0
    for item in queue:
        delete_at = _parse_delete_at(str(item.get("delete_at") or ""))
        chat_id = int(item.get("chat_id") or 0)
        message_id = int(item.get("message_id") or 0)
        if delete_at is None or chat_id == 0 or message_id <= 0:
            continue
        if delete_at <= now:
            if delete_message_safe(bot, chat_id, message_id):
                deleted += 1
            continue
        remaining.append(item)
    _save_queue(storage, remaining)
    return deleted


def schedule_battle_message_deletion(storage: Storage, chat_id: int, message_id: int) -> None:
    schedule_message_deletion(
        storage,
        chat_id,
        message_id,
        ttl_seconds=BATTLE_MESSAGE_TTL_SECONDS,
        kind="battle",
    )


def schedule_group_outcome_deletion(storage: Storage, chat_id: int, message_id: int, *, kind: str) -> None:
    schedule_message_deletion(
        storage,
        chat_id,
        message_id,
        ttl_seconds=GROUP_OUTCOME_TTL_SECONDS,
        kind=kind,
    )
