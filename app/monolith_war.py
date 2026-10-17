"""Закрытая ГП «Монолит»: окно 15 мин на вход в бой, боты, процентный исход 90/10."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from app.game_logic import (
    RATING_REWARD,
    WAR_MIN_FACTION_MEMBERS,
    WAR_SUCCESS_PAY_RU,
    ActionResult,
    WarLobbyResult,
    _add_rating,
    h,
)
from app.storage import Storage

MONOLITH_FACTION = "Монолит"
MONOLITH_BASE = "ЧАЭС"
MONOLITH_JOIN_MINUTES = 15
# Авто-исход без живых Монолита: 90% в пользу защитников стороны.
DEFENDER_PERCENT_WIN = 90

PENDING_META = "monolith_war:pending"


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


def is_monolith_base(location_name: str) -> bool:
    return str(location_name) == MONOLITH_BASE


def location_controlled_by_monolith(storage: Storage, location_name: str) -> bool:
    loc = storage.get_location(location_name)
    if loc is None:
        return False
    return str(loc.get("controlled_by") or "") == MONOLITH_FACTION


def get_pending_monolith_war(storage: Storage) -> dict[str, Any] | None:
    raw = storage.get_meta(PENDING_META)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("resolved"):
        return None
    return data


def _save_pending(storage: Storage, pending: dict[str, Any]) -> None:
    storage.set_meta(PENDING_META, json.dumps(pending, ensure_ascii=False))


def save_pending_monolith_war(storage: Storage, pending: dict[str, Any]) -> None:
    _save_pending(storage, pending)


def clear_pending_monolith_war(storage: Storage) -> None:
    storage.set_meta(PENDING_META, "")


def should_defer_war_for_monolith(
    storage: Storage,
    *,
    host_faction: str,
    location_name: str,
) -> str | None:
    """Вернуть режим 'defend' | 'attack' если нужен 15‑мин таймер Монолита.

    None = обычный штурм. 'blocked' = уже идёт окно Монолита — нельзя параллелить.
    """
    if get_pending_monolith_war(storage) is not None:
        return "blocked"
    if location_controlled_by_monolith(storage, location_name) and host_faction != MONOLITH_FACTION:
        return "defend"
    if host_faction == MONOLITH_FACTION:
        return "attack"
    return None


def _pull_monolith_energy_spenders(
    storage: Storage,
    monolith_ids: list[int],
    energy_spent: list[int],
) -> list[int]:
    """Если кто-то вступил в тактику — тянем в бой и тех, кто уже списал энергию на атаку."""
    out = list(monolith_ids)
    seen = set(out)
    for tid in energy_spent:
        if tid in seen:
            continue
        ch = storage.get_character(tid, refresh_energy=False)
        if ch is None or ch.faction != MONOLITH_FACTION or ch.health <= 0:
            continue
        out.append(tid)
        seen.add(tid)
    return out


def _pending_mode(pending: dict[str, Any]) -> str:
    return str(pending.get("mode") or "defend")


def _monolith_fighters_for_pending(storage: Storage, pending: dict[str, Any]) -> list[int]:
    """Живые монолитовцы в окне боя: атака — штурмующие, оборона — защитники."""
    monolith_ids = [int(x) for x in (pending.get("monolith_ids") or [])]
    if _pending_mode(pending) != "attack":
        return monolith_ids
    attacker_ids = [int(x) for x in (pending.get("attacker_ids") or [])]
    energy_spent = [int(x) for x in (pending.get("energy_spent_ids") or [])]
    return _pull_monolith_energy_spenders(
        storage,
        monolith_ids,
        energy_spent + attacker_ids,
    )


def monolith_join_button_label(storage: Storage) -> str:
    pending = get_pending_monolith_war(storage)
    if pending is None:
        return "☢ Вступить в бой Монолита"
    if _pending_mode(pending) == "attack":
        return "☢ Вступить в атаку Монолита"
    return "☢ Вступить в оборону Монолита"


def begin_monolith_war_window(
    storage: Storage,
    *,
    war_id: int,
    location_name: str,
    host_faction: str,
    attacker_ids: list[int],
    mode: str,
    energy_spent_ids: list[int],
) -> dict[str, Any]:
    now = _utc_now()
    pending = {
        "war_id": int(war_id),
        "location": str(location_name),
        "host_faction": str(host_faction),
        "mode": str(mode),
        "attacker_ids": [int(x) for x in attacker_ids],
        "monolith_ids": [],
        "monolith_names": [],
        "bots_sent": False,
        "bot_count": 0,
        "energy_spent_ids": [int(x) for x in energy_spent_ids],
        "started_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=MONOLITH_JOIN_MINUTES)).isoformat(),
        "resolved": False,
    }
    # Людей не префиллим: «вступить» даёт тактику; иначе при ботах — 90/10.
    _save_pending(storage, pending)
    # Лобби помечаем in_progress, чтобы не запускали второй раз.
    storage.start_war_lobby_assault(war_id)
    return pending


def format_monolith_war_call(pending: dict[str, Any]) -> str:
    loc = h(str(pending.get("location") or "?"))
    host = h(str(pending.get("host_faction") or "?"))
    mode = _pending_mode(pending)
    mins = MONOLITH_JOIN_MINUTES
    humans = len(pending.get("monolith_ids") or [])
    attackers = len(pending.get("attacker_ids") or [])
    bots = "да" if pending.get("bots_sent") else "нет"
    if mode == "defend":
        return (
            f"☢ <b>Штурм базы Монолита</b>\n"
            f"«{host}» идёт на «{loc}».\n"
            f"Окно: ~{mins} мин. Защитников Монолита в бою: {humans}.\n"
            f"Боты отправлены: {bots}. Атакующих в лобби: {attackers}.\n"
            f"Без живых защитников — авто 90/10 <b>в пользу обороны</b>."
        )
    return (
        f"☢ <b>Внимание, сталкеры!</b>\n"
        f"Был замечен отряд Монолита. По данным разведки направляется он на «{loc}»."
    )


def format_monolith_war_resolve_html(text: str) -> str:
    return f"☢ <b>Исход боя Монолита</b>\n{h(str(text or '').strip())}"


def monolith_war_status_line(storage: Storage) -> str | None:
    pending = get_pending_monolith_war(storage)
    if pending is None:
        return None
    expires = _parse_iso(str(pending.get("expires_at") or ""), _utc_now())
    left = max(0, int((expires - _utc_now()).total_seconds() // 60))
    fighters = len(_monolith_fighters_for_pending(storage, pending))
    invaders = len(pending.get("attacker_ids") or [])
    bots = "боты+" if pending.get("bots_sent") else "боты−"
    if pending.get("mode") == "defend":
        return (
            f"Монолит: оборона «{pending.get('location')}» "
            f"(~{left} мин, защитников {fighters}, атакующих {invaders}, {bots})."
        )
    return (
        f"Монолит: атака «{pending.get('location')}» "
        f"(~{left} мин, в штурме {fighters}, {bots})."
    )


def join_monolith_war(storage: Storage, telegram_id: int) -> ActionResult:
    from app.player_busy import player_busy_reason

    pending = get_pending_monolith_war(storage)
    if pending is None:
        return ActionResult(False, "Сейчас нет окна боя Монолита.")
    expires = _parse_iso(str(pending.get("expires_at") or ""), _utc_now())
    if expires <= _utc_now():
        return ActionResult(False, "Окно уже закрылось — жди авто-исход.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if player.faction != MONOLITH_FACTION:
        return ActionResult(False, "В этот бой могут войти только бойцы Монолита.")
    if player.health <= 0:
        return ActionResult(False, "Мёртвый монолитовец в бой не идёт.")
    busy = player_busy_reason(storage, telegram_id)
    if busy:
        return ActionResult(False, busy)
    mode = _pending_mode(pending)
    ids = [int(x) for x in (pending.get("monolith_ids") or [])]
    if telegram_id in ids:
        role_ru = "атакующем отряде" if mode == "attack" else "обороне"
        return ActionResult(False, f"Ты уже в {role_ru} Монолита.")
    ids.append(telegram_id)
    names = list(pending.get("monolith_names") or [])
    names.append(str(player.nickname))
    pending["monolith_ids"] = ids
    pending["monolith_names"] = names
    _save_pending(storage, pending)
    if mode == "attack":
        role_line = f"Ты в атакующем отряде Монолита ({len(ids)} чел.)."
    else:
        role_line = f"Ты в обороне Монолита ({len(ids)} чел.)."
    return ActionResult(
        True,
        f"{role_line} Дождись таймера или жми «Начать бой сейчас».",
    )


def send_monolith_bots(storage: Storage, telegram_id: int) -> ActionResult:
    from app.faction_bots import get_faction_bots

    pending = get_pending_monolith_war(storage)
    if pending is None:
        return ActionResult(False, "Сейчас нет окна боя Монолита.")
    expires = _parse_iso(str(pending.get("expires_at") or ""), _utc_now())
    if expires <= _utc_now():
        return ActionResult(False, "Окно уже закрылось.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction != MONOLITH_FACTION:
        return ActionResult(False, "Посылать ботов может только Монолит.")
    bots = get_faction_bots(storage, MONOLITH_FACTION)
    count = int(bots.get("count") or 3)
    pending["bots_sent"] = True
    pending["bot_count"] = count
    _save_pending(storage, pending)
    return ActionResult(
        True,
        f"🤖 В бой послано ботов Монолита: {count} (тир {bots.get('tier', 1)}).",
    )


def _refund_energy(storage: Storage, ids: list[int], amount: int) -> None:
    for tid in ids:
        storage.restore_energy(tid, amount)


def _percent_roll_defenders_win() -> bool:
    return random.randint(1, 100) <= DEFENDER_PERCENT_WIN


def _pay_attackers_success(storage: Storage, attacker_ids: list[int], host_faction: str, location: str) -> str:
    from app.combat_loot import grant_combat_loot

    paid = 0
    for pid in attacker_ids:
        ch = storage.get_character(pid, refresh_energy=False)
        if ch is None or ch.faction != host_faction:
            continue
        if ch.health <= 0:
            continue
        storage.add_player_stat(pid, "wars_won", 1)
        storage.change_money(pid, WAR_SUCCESS_PAY_RU)
        storage.add_player_stat(pid, "money_earned", WAR_SUCCESS_PAY_RU)
        _add_rating(storage, pid, RATING_REWARD["war_success"])
        # Труп монолита: информация (дневник/флешка) попадает в инвентарь.
        grant_combat_loot(storage, pid, npc=True, monolith=True)
        paid += 1
    from app.faction_bots import apply_location_control

    apply_location_control(storage, location, host_faction)
    return (
        f"Процентный исход: «{location}» захвачена «{host_faction}». "
        f"Награда хоста: {paid} бойц. ×{WAR_SUCCESS_PAY_RU} RU."
    )


def _fail_attackers(storage: Storage, attacker_ids: list[int], location: str, reason: str) -> str:
    for pid in attacker_ids:
        _add_rating(storage, pid, -RATING_REWARD["war_fail"])
    return (
        f"{reason} Штурм «{location}» отбит. "
        f"−{RATING_REWARD['war_fail']} рейтинга атакующим."
    )


def resolve_pending_monolith_war(storage: Storage, *, force: bool = False) -> dict[str, Any] | None:
    """Закрыть окно: процент 90/10 или тактическая сетка с людьми Монолита."""
    pending = get_pending_monolith_war(storage)
    if pending is None:
        return None
    expires = _parse_iso(str(pending.get("expires_at") or ""), _utc_now())
    if not force and expires > _utc_now():
        return None

    war_id = int(pending.get("war_id") or 0)
    location = str(pending.get("location") or "")
    host_faction = str(pending.get("host_faction") or "")
    mode = str(pending.get("mode") or "defend")
    attacker_ids = [int(x) for x in (pending.get("attacker_ids") or [])]
    monolith_ids = [int(x) for x in (pending.get("monolith_ids") or [])]
    bots_sent = bool(pending.get("bots_sent"))
    bot_count = int(pending.get("bot_count") or 0)
    energy_spent = [int(x) for x in (pending.get("energy_spent_ids") or [])]

    pending["resolved"] = True
    _save_pending(storage, pending)

    notify_ids = list(dict.fromkeys(attacker_ids + monolith_ids))
    fighter_ids = _monolith_fighters_for_pending(storage, pending)
    if fighter_ids:
        notify_ids = list(dict.fromkeys(notify_ids + fighter_ids))
    humans_ready = len(fighter_ids) > 0

    # Живые монолитовцы → тактическое поле.
    if humans_ready:
        from app.clan_war_grid import start_clan_war_grid
        from app.game_logic import WAR_LOBBY_ENERGY_COST

        if mode == "defend":
            # Боты Монолита не спавним как врагов на сетке — они только для 90/10.
            result, session = start_clan_war_grid(
                storage,
                war_id=war_id,
                location_name=location,
                host_faction=MONOLITH_FACTION,
                player_ids=fighter_ids,
                monolith_defense=True,
                invader_faction=host_faction,
                extra_defenders=0,
            )
            clear_pending_monolith_war(storage)
            if not result.ok or session is None:
                _refund_energy(storage, energy_spent, WAR_LOBBY_ENERGY_COST)
                storage.finish_war_lobby(war_id, "failed", "Не удалось стартовать оборону Монолита")
                return {
                    "kind": "error",
                    "text": result.text or "Не удалось начать бой Монолита.",
                    "notify_ids": notify_ids,
                }
            return {
                "kind": "tactical",
                "text": (
                    f"Монолит вступил в оборону «{location}» "
                    f"({len(fighter_ids)} чел."
                    + (f"; боты держали периметр до входа" if bots_sent else "")
                    + "). Тактическое поле!"
                ),
                "notify_ids": notify_ids,
                "session": session,
                "member_ids": fighter_ids,
            }

        if mode == "attack":
            # Атака: все живые монолитовцы (инициаторы + вступившие) — штурмующие.
            result, session = start_clan_war_grid(
                storage,
                war_id=war_id,
                location_name=location,
                host_faction=MONOLITH_FACTION,
                player_ids=fighter_ids,
                extra_defenders=0,
            )
            clear_pending_monolith_war(storage)
            if not result.ok or session is None:
                _refund_energy(storage, energy_spent, WAR_LOBBY_ENERGY_COST)
                storage.finish_war_lobby(war_id, "failed", "Не удалось стартовать атаку Монолита")
                return {
                    "kind": "error",
                    "text": result.text or "Не удалось начать бой Монолита.",
                    "notify_ids": notify_ids,
                }
            return {
                "kind": "tactical",
                "text": (
                    f"Монолит штурмует «{location}» "
                    f"({len(fighter_ids)} чел."
                    + (f", боты в поддержке ×{bot_count}" if bots_sent else "")
                    + ")."
                ),
                "notify_ids": list(dict.fromkeys(notify_ids + fighter_ids)),
                "session": session,
                "member_ids": fighter_ids,
            }

    # Нет людей Монолита → процентный исход 90/10 в пользу защитников.
    from app.game_logic import WAR_LOBBY_ENERGY_COST

    defenders_win = _percent_roll_defenders_win()
    # mode=defend: Монолит — защитники; mode=attack: защитники — держатели точки.
    monolith_wins = defenders_win if mode == "defend" else (not defenders_win)
    clear_pending_monolith_war(storage)

    if mode == "defend":
        if monolith_wins:
            storage.finish_war_lobby(war_id, "failed", "Монолит удержал базу (90% защитникам)")
            text = _fail_attackers(
                storage,
                attacker_ids,
                location,
                f"Авто-исход 90/10 (защитники): Монолит удержал «{location}»"
                + (" с ботами." if bots_sent else "."),
            )
            return {"kind": "percent", "text": text, "notify_ids": notify_ids, "monolith_wins": True}
        storage.finish_war_lobby(war_id, "success", f"Пробита оборона Монолита (10%): {host_faction}")
        text = _pay_attackers_success(storage, attacker_ids, host_faction, location)
        text = f"Авто-исход 90/10 (крит атакующим 10%): {text}"
        return {"kind": "percent", "text": text, "notify_ids": notify_ids, "monolith_wins": False}

    # Атака без людей: нужны посланные боты, иначе откат.
    if not bots_sent and bot_count <= 0:
        _refund_energy(storage, energy_spent, WAR_LOBBY_ENERGY_COST)
        storage.finish_war_lobby(war_id, "failed", "Монолит не вышел в бой")
        text = (
            f"Никто из Монолита не вступил и ботов не послали. "
            f"Штурм «{location}» отменён, энергия возвращена."
        )
        return {"kind": "percent", "text": text, "notify_ids": notify_ids, "monolith_wins": False}

    if monolith_wins:
        storage.finish_war_lobby(war_id, "success", "Монолит взял точку (крит атаке 10%)")
        if attacker_ids:
            text = _pay_attackers_success(storage, attacker_ids, MONOLITH_FACTION, location)
        else:
            from app.faction_bots import apply_location_control

            apply_location_control(storage, location, MONOLITH_FACTION)
            text = f"Процентный исход (крит атаке 10%): «{location}» взята Монолитом (боты ×{bot_count})."
        text = f"Авто-исход 90/10: {text}"
        return {"kind": "percent", "text": text, "notify_ids": notify_ids, "monolith_wins": True}

    storage.finish_war_lobby(war_id, "failed", "Атака Монолита отбита защитниками (90%)")
    text = _fail_attackers(
        storage,
        attacker_ids or energy_spent,
        location,
        "Авто-исход 90/10: защитники удержали точку, атака Монолита сорвалась.",
    )
    return {"kind": "percent", "text": text, "notify_ids": notify_ids, "monolith_wins": False}


def process_monolith_war_cycle(storage: Storage) -> dict[str, Any] | None:
    return resolve_pending_monolith_war(storage, force=False)


def force_start_monolith_war(storage: Storage, telegram_id: int) -> ActionResult:
    """Досрочно закрыть окно боя Монолита (кнопка «Начать сейчас»)."""
    pending = get_pending_monolith_war(storage)
    if pending is None:
        return ActionResult(False, "Сейчас нет окна боя Монолита.")
    expires = _parse_iso(str(pending.get("expires_at") or ""), _utc_now())
    if expires <= _utc_now():
        return ActionResult(False, "Окно уже закрылось — исход скоро придёт сам.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction != MONOLITH_FACTION:
        return ActionResult(False, "Досрочный старт доступен только Монолиту.")
    if player.health <= 0:
        return ActionResult(False, "Мёртвый монолитовец бой не начнёт.")

    outcome = resolve_pending_monolith_war(storage, force=True)
    if outcome is None:
        return ActionResult(False, "Не удалось закрыть окно боя Монолита.")
    return ActionResult(
        True,
        str(outcome.get("text") or "Окно боя Монолита закрыто."),
        payload={"monolith_outcome": outcome},
    )


def monolith_join_button_visible(storage: Storage, telegram_id: int) -> bool:
    pending = get_pending_monolith_war(storage)
    if pending is None:
        return False
    expires = _parse_iso(str(pending.get("expires_at") or ""), _utc_now())
    if expires <= _utc_now():
        return False
    player = storage.get_character(telegram_id, refresh_energy=False)
    return player is not None and player.faction == MONOLITH_FACTION


def filter_travel_locations_for_faction(
    locations: list[dict[str, Any]],
    faction: str | None,
) -> list[dict[str, Any]]:
    """Убираем из переходов мусорную локацию «Завод»; ЧАЭС остаётся доступной."""
    return [loc for loc in locations if str(loc.get("name") or "") != "Завод"]


def start_monolith_attack(
    storage: Storage,
    telegram_id: int,
    location_name: str,
) -> ActionResult:
    """Одна кнопка/команда: Монолит объявляет атаку на точку (окно 15 мин + боты)."""
    from app.faction_bots import get_faction_bots
    from app.game_logic import (
        WAR_LOBBY_ENERGY_COST,
        _location_is_friendly_to_faction,
        list_assaultable_locations,
    )
    from app.player_busy import player_busy_reason

    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if player.faction != MONOLITH_FACTION:
        return ActionResult(False, "Атаку Монолита может начать только боец Монолита.")
    if player.health <= 0:
        return ActionResult(False, "Мёртвый монолитовец атаку не объявит.")
    busy = player_busy_reason(storage, telegram_id)
    if busy:
        return ActionResult(False, busy)
    if get_pending_monolith_war(storage) is not None:
        return ActionResult(False, "Уже идёт окно боя Монолита. Дождись исхода.")

    loc_name = str(location_name or "").strip()
    target = storage.get_location(loc_name)
    if target is None:
        assaultable = list_assaultable_locations(storage, MONOLITH_FACTION)
        names = ", ".join(str(x["name"]) for x in assaultable[:12])
        return ActionResult(
            False,
            f"Локация «{loc_name}» не найдена.\nДоступно: {names or '—'}",
        )
    if _location_is_friendly_to_faction(storage, target, MONOLITH_FACTION):
        return ActionResult(False, f"«{loc_name}» уже своя или союзническая.")

    open_lobby = storage.get_open_war_lobby_for_faction(MONOLITH_FACTION)
    if open_lobby is not None:
        return ActionResult(
            False,
            f"У Монолита уже открыто лобби #{open_lobby['id']} на «{open_lobby['location']}».",
        )

    bots = get_faction_bots(storage, MONOLITH_FACTION)
    bot_count = int(bots.get("count") or 0)
    if bot_count < 1:
        return ActionResult(
            False,
            "Нет ботов Монолита. Набери хотя бы одного в казне ГП.",
        )

    if not storage.spend_energy(telegram_id, WAR_LOBBY_ENERGY_COST):
        return ActionResult(
            False,
            f"Недостаточно энергии (нужно {WAR_LOBBY_ENERGY_COST}).",
        )

    war_id = storage.create_war_lobby(MONOLITH_FACTION, loc_name, telegram_id)
    pending = begin_monolith_war_window(
        storage,
        war_id=war_id,
        location_name=loc_name,
        host_faction=MONOLITH_FACTION,
        attacker_ids=[telegram_id],
        mode="attack",
        energy_spent_ids=[telegram_id],
    )
    pending["monolith_ids"] = [telegram_id]
    pending["monolith_names"] = [str(player.nickname)]
    pending["bots_sent"] = True
    pending["bot_count"] = bot_count
    save_pending_monolith_war(storage, pending)

    monolith_ids = storage.list_faction_member_ids(MONOLITH_FACTION)
    return ActionResult(
        True,
        (
            f"☢ Монолит объявил атаку на «{loc_name}».\n"
            f"Ты уже в атакующем отряде. Окно {MONOLITH_JOIN_MINUTES} мин: "
            f"союзники могут вступить или ждать авто 90/10 с ботами (×{bot_count})."
        ),
        payload={
            "monolith_pending": True,
            "monolith_notify_ids": [int(x) for x in monolith_ids],
            "location": loc_name,
            "war_id": war_id,
        },
    )
