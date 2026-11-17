"""Отложенное удаление сообщений бота (меньше спама в чатах).

Два механизма:

* **очередь на удаление** (``ephemeral_msg_queue`` в meta) — сообщение удаляется
  через свой TTL: кадры активности держим ``FRAME_TTL_SECONDS``, уведомления —
  ``NOTICE_TTL_SECONDS``; очередь разбирает ``process_ephemeral_message_queue``
  из фонового цикла (``app.bot._zone_tick``);
* **«текущее» сообщение вида** (``eph_current:<chat>:<kind>``) — у одного игрока
  в одном чате живёт только последний кадр активности: когда приходит новый,
  предыдущий удаляется сразу (прогулка по локации, ходы боя, охота и т.п.).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.storage import Storage, utc_now

logger = logging.getLogger(__name__)

EPHEMERAL_QUEUE_META = "ephemeral_msg_queue"
#: Сколько записей держим в очереди (рассылки на много игроков быстро её заполняют).
EPHEMERAL_QUEUE_MAX = 2000
#: Сколько сообщений удаляем за один проход фонового цикла (анти-флуд VK).
EPHEMERAL_DELETE_BATCH = 40
#: Пауза между удалениями в одном проходе, сек.
EPHEMERAL_DELETE_PAUSE = 0.05

CURRENT_MESSAGE_META_PREFIX = "eph_current:"

# Бой / исход в личке — держим час.
BATTLE_MESSAGE_TTL_SECONDS = 3600
# Исход в общем чате (благодарность, resolve) — тоже час.
GROUP_OUTCOME_TTL_SECONDS = 3600
# Кадры активности (вылазка, бои, охота, схрон, лаборатория) — полчаса.
FRAME_TTL_SECONDS = 1800
# Уведомления (прибытие, выброс, события) — час.
NOTICE_TTL_SECONDS = 3600


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
    storage.set_meta(EPHEMERAL_QUEUE_META, json.dumps(queue, ensure_ascii=False))


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
    """Поставить в очередь сразу пачку сообщений (одна запись в meta вместо N)."""
    if isinstance(items, dict):
        pairs = [(int(pid), int(mid)) for pid, mid in items.items()]
    else:
        pairs = [(int(chat_id), int(mid)) for chat_id, mid in items]
    pairs = [(chat_id, mid) for chat_id, mid in pairs if mid > 0]
    if not pairs:
        return
    delete_at = (utc_now() + timedelta(seconds=max(1, int(ttl_seconds)))).isoformat()
    queue = _load_queue(storage)
    keys = {(chat_id, mid) for chat_id, mid in pairs}
    queue = [
        item
        for item in queue
        if (int(item.get("chat_id", 0)), int(item.get("message_id", 0))) not in keys
    ]
    queue.extend(
        {
            "chat_id": chat_id,
            "message_id": mid,
            "delete_at": delete_at,
            "kind": str(kind or "battle"),
        }
        for chat_id, mid in pairs
    )
    _save_queue(storage, queue)


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


def delete_message_safe(bot: Any, chat_id: int, message_id: int) -> Any:
    """Удалить сообщение, не падая на ошибках VK (учитывает async-мессенджер).

    Возвращает awaitable — вызывать через ``await``.
    """

    async def _delete() -> bool:
        try:
            result = bot.delete_message(chat_id=int(chat_id), message_id=int(message_id))
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
        except Exception:
            logger.debug("Failed to delete message %s in %s", message_id, chat_id, exc_info=True)
            return False

    return _delete()


# ---------------------------------------------------------------------------
# «Текущее» сообщение вида: у игрока в чате живёт только последний кадр
# ---------------------------------------------------------------------------


def current_message_meta_key(chat_id: int, kind: str) -> str:
    return f"{CURRENT_MESSAGE_META_PREFIX}{int(chat_id)}:{str(kind or 'frame')}"


def get_current_message(storage: Storage, chat_id: int, kind: str) -> int:
    raw = str(storage.get_meta(current_message_meta_key(chat_id, kind)) or "").strip()
    return int(raw) if raw.isdigit() else 0


def remember_current_message(storage: Storage, chat_id: int, kind: str, message_id: int) -> int:
    """Запомнить новый «текущий» кадр. Возвращает id предыдущего (0, если его не было)."""
    previous = get_current_message(storage, chat_id, kind)
    if int(message_id) > 0:
        storage.set_meta(current_message_meta_key(chat_id, kind), str(int(message_id)))
    return previous


def forget_current_message(storage: Storage, chat_id: int, kind: str, message_id: int | None = None) -> None:
    """Забыть «текущий» кадр (если message_id задан — только когда он и есть текущий)."""
    if message_id is not None and get_current_message(storage, chat_id, kind) != int(message_id):
        return
    storage.delete_meta(current_message_meta_key(chat_id, kind))


async def drop_current_message(bot: Any, storage: Storage, chat_id: int, kind: str) -> bool:
    """Удалить предыдущий кадр этого вида в чате (он уже неактуален)."""
    previous = get_current_message(storage, chat_id, kind)
    storage.delete_meta(current_message_meta_key(chat_id, kind))
    if previous <= 0:
        return False
    cancel_message_deletion(storage, chat_id, previous)
    return bool(await delete_message_safe(bot, chat_id, previous))


def remember_frame_message(
    storage: Storage,
    chat_id: int,
    kind: str,
    message_id: int,
    *,
    ttl_seconds: int = FRAME_TTL_SECONDS,
) -> None:
    """Новый кадр стал «текущим»: старый уже удалён, новый уйдёт по TTL."""
    if int(message_id) <= 0:
        return
    remember_current_message(storage, chat_id, kind, int(message_id))
    schedule_message_deletion(
        storage,
        chat_id,
        int(message_id),
        ttl_seconds=ttl_seconds,
        kind=str(kind or "frame"),
    )


async def process_ephemeral_message_queue(
    bot: Any, storage: Storage, *, limit: int = EPHEMERAL_DELETE_BATCH
) -> int:
    """Удалить сообщения, у которых истёк срок. Возвращает число успешных удалений.

    За один проход удаляем не больше ``limit`` сообщений (анти-флуд VK): остальные
    останутся в очереди до следующего тика.
    """
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
        if delete_at > now or deleted >= max(1, int(limit)):
            remaining.append(item)
            continue
        if await delete_message_safe(bot, chat_id, message_id):
            deleted += 1
        # Заодно чистим «текущий кадр», если это был он.
        forget_current_message(storage, chat_id, str(item.get("kind") or ""), message_id)
        if EPHEMERAL_DELETE_PAUSE:
            await asyncio.sleep(EPHEMERAL_DELETE_PAUSE)
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


def queue_size(storage: Storage) -> int:
    """Сколько сообщений ждёт удаления (для логов/диагностики)."""
    return len(_load_queue(storage))
