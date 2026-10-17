"""In-game смена группировки (с ограничениями)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.game_logic import ActionResult, FACTION_HOME_BASE, h
from app.storage import Storage, utc_now

FACTION_CHANGE_COST_RU = 5000
FACTION_CHANGE_COOLDOWN_DAYS = 14
FACTION_CHANGE_TARGETS: tuple[str, ...] = ("Долг", "Свобода", "Нейтралы", "Бандиты", "Монолит")


def _cooldown_key(telegram_id: int) -> str:
    return f"faction_change_cd:{int(telegram_id)}"


def faction_change_available_at(storage: Storage, telegram_id: int) -> datetime | None:
    raw = storage.get_meta(_cooldown_key(telegram_id))
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def build_faction_change_text(storage: Storage, telegram_id: int) -> str:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return "Сначала выбери группировку через /start."
    available = faction_change_available_at(storage, telegram_id)
    lines = [
        "🔄 Смена группировки",
        "",
        f"Сейчас: «{player.faction}».",
        f"Стоимость: {FACTION_CHANGE_COST_RU} RU.",
        f"Кулдаун: {FACTION_CHANGE_COOLDOWN_DAYS} дн. после перевода.",
        "Сбрасывается звание; открытые рейды/лобби отменяются.",
        "",
    ]
    if available and available > utc_now():
        left = available - utc_now()
        days = max(1, int(left.total_seconds() // 86400) + (1 if left.seconds else 0))
        lines.append(f"Следующая смена через ~{days} дн.")
    else:
        lines.append("Можно сменить группировку — выбери ниже.")
    return "\n".join(lines)


def change_player_faction(
    storage: Storage,
    telegram_id: int,
    new_faction: str,
) -> ActionResult:
    from app.game_logic import admin_set_player_faction

    target = (new_faction or "").strip()
    if target not in FACTION_CHANGE_TARGETS:
        return ActionResult(
            False,
            "Неизвестная группировка. Доступно: " + ", ".join(FACTION_CHANGE_TARGETS),
        )

    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Сначала создай персонажа и выбери группировку.")
    if player.faction == target:
        return ActionResult(False, f"Ты уже в «{target}».")

    available = faction_change_available_at(storage, telegram_id)
    if available and available > utc_now():
        return ActionResult(False, "Смена группировки на кулдауне.")

    from app.player_busy import player_busy_reason

    busy = player_busy_reason(storage, telegram_id)
    if busy:
        return ActionResult(False, busy)

    if player.money < FACTION_CHANGE_COST_RU:
        return ActionResult(False, f"Нужно {FACTION_CHANGE_COST_RU} RU для перевода.")

    if not storage.change_money(telegram_id, -FACTION_CHANGE_COST_RU):
        return ActionResult(False, "Не хватило денег на перевод.")

    result = admin_set_player_faction(storage, target=str(telegram_id), faction=target)
    if not result.ok:
        storage.change_money(telegram_id, FACTION_CHANGE_COST_RU)
        return result

    next_at = utc_now() + timedelta(days=FACTION_CHANGE_COOLDOWN_DAYS)
    storage.set_meta(_cooldown_key(telegram_id), next_at.isoformat())
    home = FACTION_HOME_BASE.get(target, target)
    return ActionResult(
        True,
        f"Перевод в «{h(target)}» оформлен (−{FACTION_CHANGE_COST_RU} RU). "
        f"Ты на базе «{home}». Следующая смена через {FACTION_CHANGE_COOLDOWN_DAYS} дн.",
    )
