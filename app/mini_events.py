"""Радиомини-ивенты помощи в общем чате Зоны."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from app.game_logic import (
    QUESTS,
    ActionResult,
    FACTION_HOME_BASE,
    h,
)
from app.storage import Storage

HELP_EVENT_META = "help_event:active"
HELP_EVENT_NEXT_META = "help_event:next_at"
HELP_EVENT_INTERVAL_MIN_MINUTES = 40
HELP_EVENT_INTERVAL_MAX_MINUTES = 90
HELP_EVENT_DURATION_MINUTES = 15
HELP_EVENT_MAX_HELPERS = 3

HELP_THANKS_SPEAKERS: dict[str, str] = {
    "scientists": "Группа учёных",
    "stalkers": "Группа сталкеров",
    "duty": "Патруль «Долга»",
}

HELP_EVENT_LOCATIONS: tuple[str, ...] = (
    "Болото",
    "НИИ Агропром",
    "Темная долина",
    "Янтарь",
    "Рыжий лес",
)

# Базы группировок исключены из локаций ивентов: иначе чужим на базу не пройти
# и часть игроков не сможет поучаствовать в событии (запросы игроков, см. git log).
def _event_locations_without_bases() -> tuple[str, ...]:
    bases = set(FACTION_HOME_BASE.values())
    return tuple(loc for loc in HELP_EVENT_LOCATIONS if loc not in bases)

HELP_CALLS: tuple[tuple[str, str, str], ...] = (
    (
        "scientists",
        "Учёные",
        'Приём, всем кто слышит! На нас напали, это плоти, их слишком много! '
        'Требуется помощь. Мы находимся на локации «{location}». '
        "Если кто-нибудь может помочь, отзовитесь. Мы продержимся не больше 15 минут.",
    ),
    (
        "stalkers",
        "Сталкеры",
        "Приём, это сталкеры! Засели в укрытии на «{location}», мутанты давят. "
        "Нужна огневая поддержка. Держимся минут 15, не больше.",
    ),
    (
        "duty",
        "Долг",
        "Говорит патруль «Долга». На «{location}» орда тварей. "
        "Союзникам Зоны: нужна помощь, иначе закрепиться не выйдет. 15 минут.",
    ),
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(raw: str | None, fallback: datetime) -> datetime:
    if not raw:
        return fallback
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return fallback


def schedule_next_help_event(storage: Storage, now: datetime | None = None) -> None:
    now = now or _utc_now()
    delay = random.randint(HELP_EVENT_INTERVAL_MIN_MINUTES, HELP_EVENT_INTERVAL_MAX_MINUTES)
    storage.set_meta(HELP_EVENT_NEXT_META, (now + timedelta(minutes=delay)).isoformat())


def get_active_help_event(storage: Storage) -> dict[str, Any] | None:
    raw = storage.get_meta(HELP_EVENT_META)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    expires = _parse_iso(str(data.get("expires_at") or ""), _utc_now())
    if expires <= _utc_now():
        return None
    return data


def help_event_is_joinable(storage: Storage, telegram_id: int) -> bool:
    event = get_active_help_event(storage)
    if event is None:
        return False
    helpers = [int(x) for x in (event.get("helpers") or [])]
    if int(telegram_id) in helpers:
        return False
    return len(helpers) < HELP_EVENT_MAX_HELPERS


def start_help_event(storage: Storage) -> dict[str, Any]:
    kind, speaker, template = random.choice(HELP_CALLS)
    location = random.choice(_event_locations_without_bases())
    now = _utc_now()
    event = {
        "id": now.strftime("%Y%m%d%H%M%S"),
        "kind": kind,
        "speaker": speaker,
        "thanks_speaker": HELP_THANKS_SPEAKERS.get(kind, speaker),
        "location": location,
        "call_text": template.format(location=location),
        "started_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=HELP_EVENT_DURATION_MINUTES)).isoformat(),
        "helpers": [],
        "helper_names": [],
        "helper_factions": [],
        "resolved": False,
    }
    storage.set_meta(HELP_EVENT_META, json.dumps(event, ensure_ascii=False))
    return event


def _save_event(storage: Storage, event: dict[str, Any]) -> None:
    storage.set_meta(HELP_EVENT_META, json.dumps(event, ensure_ascii=False))


def join_help_event(storage: Storage, telegram_id: int) -> ActionResult:
    from app.player_busy import player_busy_reason
    from app.quest_mission import start_or_resume_quest_mission

    event = get_active_help_event(storage)
    if event is None:
        return ActionResult(False, "Сейчас никто не зовёт на помощь по рации.")
    helpers = [int(x) for x in (event.get("helpers") or [])]
    if telegram_id in helpers:
        return ActionResult(False, "Ты уже откликнулся на этот вызов.")
    if len(helpers) >= HELP_EVENT_MAX_HELPERS:
        return ActionResult(False, "Группа помощи уже собрана.")

    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    busy = player_busy_reason(storage, telegram_id)
    if busy:
        return ActionResult(False, busy)
    # Отклик по рации — полевая вылазка: сначала доехать до локации события,
    # потом помогать (иначе зачистка «по рации» с другой локации обесценивает переходы).
    if player.location != str(event.get("location") or ""):
        return ActionResult(
            False,
            f"Нужно быть на локации «{event.get('location')}», чтобы помочь. Сейчас ты на «{player.location}».",
        )

    from app.game_logic import QuestContractTemplate

    template = QuestContractTemplate(
        key=f"help_{event['id']}",
        difficulty="hard",
        title=f"Помощь: {event['speaker']} на {event['location']}",
        work_location=str(event["location"]),
        return_home=False,
        mission_kind="clear_mutant",
    )
    tid = int(telegram_id)
    nickname = str(player.nickname)
    faction = str(player.faction or "Нейтралы")

    def _reserve(data: dict[str, Any]) -> bool:
        current = [int(x) for x in (data.get("helpers") or [])]
        if tid in current:
            return False
        if len(current) >= HELP_EVENT_MAX_HELPERS:
            return False
        current.append(tid)
        names = list(data.get("helper_names") or [])
        factions = list(data.get("helper_factions") or [])
        names.append(nickname)
        factions.append(faction)
        data["helpers"] = current
        data["helper_names"] = names
        data["helper_factions"] = factions
        return True

    if not storage.update_json_meta(HELP_EVENT_META, _reserve):
        return ActionResult(False, "Группа помощи уже собрана или вызов закончился.")

    quest = QUESTS["hard"]
    result = start_or_resume_quest_mission(storage, telegram_id, template, quest)
    if not result.ok:
        def _unreserve(data: dict[str, Any]) -> bool:
            current = [int(x) for x in (data.get("helpers") or [])]
            if tid not in current:
                return False
            idx = current.index(tid)
            current.pop(idx)
            names = list(data.get("helper_names") or [])
            factions = list(data.get("helper_factions") or [])
            if idx < len(names):
                names.pop(idx)
            if idx < len(factions):
                factions.pop(idx)
            data["helpers"] = current
            data["helper_names"] = names
            data["helper_factions"] = factions
            return True

        storage.update_json_meta(HELP_EVENT_META, _unreserve)
        return result

    event = get_active_help_event(storage) or event
    return ActionResult(
        True,
        f"Ты откликнулся: {event['speaker']} на «{event['location']}». Уничтожь мутантов на поле.",
        payload=result.payload,
    )


def complete_help_event_if_helper(storage: Storage, telegram_id: int) -> str | None:
    """Пометить помощника после успешной зачистки (деньги/рейтинг уже даёт контракт)."""
    raw = storage.get_meta(HELP_EVENT_META)
    if not raw:
        return None
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    helpers = [int(x) for x in (event.get("helpers") or [])]
    if int(telegram_id) not in helpers:
        return None
    done = {int(x) for x in (event.get("done_helpers") or [])}
    if telegram_id in done:
        return None
    done.add(telegram_id)
    event["done_helpers"] = list(done)
    _save_event(storage, event)
    storage.add_player_stat(telegram_id, "radio_helps", 1)
    return "Помощь по рации засчитана."


def thanks_text(event: dict[str, Any]) -> str:
    names = [str(n) for n in (event.get("helper_names") or [])]
    factions = [str(f) for f in (event.get("helper_factions") or [])]
    speaker = str(event.get("thanks_speaker") or event.get("speaker") or "Группа")
    if not names:
        return (
            f"{speaker}: Никто не пришёл… Вызов на «{event.get('location')}» затих."
        )
    people = []
    for name, faction in zip(names, factions):
        people.append(f"группировке «{faction}» и лично бойцу «{name}»")
    who = " и ".join(people)
    return (
        f"{speaker}: Отбились! Выражаем глубокую признательность {who}. "
        "Без вашей поддержки наши замеры закончились бы трагедией."
    )


def process_help_event_cycle(storage: Storage) -> dict[str, Any] | None:
    """Возвращает payload для бота: call / thanks / None."""
    now = _utc_now()
    raw_event = storage.get_meta(HELP_EVENT_META)
    if raw_event:
        try:
            event = json.loads(raw_event)
        except json.JSONDecodeError:
            event = None
        else:
            if isinstance(event, dict) and not event.get("resolved"):
                expires = _parse_iso(str(event.get("expires_at") or ""), now)
                if expires <= now:
                    event["resolved"] = True
                    _save_event(storage, event)
                    schedule_next_help_event(storage, now)
                    return {"kind": "thanks", "text": thanks_text(event), "event": event}
                return None

    next_raw = storage.get_meta(HELP_EVENT_NEXT_META)
    if next_raw is None:
        schedule_next_help_event(storage, now)
        return None
    next_at = _parse_iso(next_raw, now + timedelta(minutes=HELP_EVENT_INTERVAL_MIN_MINUTES))
    if next_at > now:
        return None
    event = start_help_event(storage)
    return {"kind": "call", "text": f"{event['speaker']}: {event['call_text']}", "event": event}


def attach_help_call_message(storage: Storage, chat_id: int, message_id: int) -> None:
    raw = storage.get_meta(HELP_EVENT_META)
    if not raw:
        return
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(event, dict) or event.get("resolved"):
        return
    event["call_chat_id"] = int(chat_id)
    event["call_message_id"] = int(message_id)
    _save_event(storage, event)


def pop_help_call_message(storage: Storage) -> tuple[int, int] | None:
    raw = storage.get_meta(HELP_EVENT_META)
    if not raw:
        return None
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    chat_id = int(event.get("call_chat_id") or 0)
    message_id = int(event.get("call_message_id") or 0)
    if chat_id and message_id:
        event.pop("call_chat_id", None)
        event.pop("call_message_id", None)
        _save_event(storage, event)
        return chat_id, message_id
    return None


def format_help_call_html(event: dict[str, Any]) -> str:
    speaker = h(str(event.get("speaker") or "Рация"))
    body = h(str(event.get("call_text") or ""))
    return f"📡 <b>{speaker}</b>\n{body}"


def format_help_thanks_html(text: str) -> str:
    return f"📡 {h(text)}"
