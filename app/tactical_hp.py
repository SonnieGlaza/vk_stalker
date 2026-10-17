"""HP и аптечки в тактических режимах (дуэль, кооп) — отдельно от БД до конца боя."""

from __future__ import annotations

from typing import Any

from app.game_logic import (
    ITEM_LABELS,
    ActionResult,
    MEDKIT_EFFECTS,
    effective_max_health,
)
from app.storage import Storage


def use_tactical_medkit(storage: Storage, telegram_id: int, current_hp: int) -> tuple[ActionResult, int]:
    """Потратить аптечку и вернуть новое HP на поле (не трогая БД до конца боя)."""
    result, new_hp, item_key = plan_tactical_medkit(storage, telegram_id, current_hp)
    if not result.ok or item_key is None:
        return result, new_hp
    apply_tactical_medkit_spend(storage, telegram_id, item_key, result)
    return result, new_hp


def plan_tactical_medkit(
    storage: Storage,
    telegram_id: int,
    current_hp: int,
) -> tuple[ActionResult, int, str | None]:
    """Подобрать аптечку и новое HP без списания предмета (списать после save)."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Персонаж не найден."), current_hp, None
    max_hp = effective_max_health(player)
    if current_hp >= max_hp:
        return ActionResult(False, "Здоровье на поле уже полное."), current_hp, None
    for key in ("medkit_science", "medkit_army", "medkit"):
        if int(player.inventory.get(key, 0)) <= 0:
            continue
        effect = MEDKIT_EFFECTS.get(key)
        if effect is None:
            continue
        label = ITEM_LABELS.get(key, key)
        heal_cap = int(effect["heal"])
        heal_amount = min(heal_cap, max_hp - current_hp)
        if heal_amount <= 0:
            return ActionResult(False, "Здоровье на поле уже полное."), current_hp, None
        new_hp = min(max_hp, current_hp + heal_amount)
        rad_delta = int(effect.get("radiation", 0))
        if rad_delta < 0 and player.radiation > 0:
            rad_note = f", {rad_delta} рад"
        else:
            rad_note = ""
        return (
            ActionResult(True, f"Ты использовал {label}: +{heal_amount} HP{rad_note}."),
            new_hp,
            key,
        )
    return ActionResult(False, "Нет аптечки в инвентаре."), current_hp, None


def apply_tactical_medkit_spend(
    storage: Storage,
    telegram_id: int,
    item_key: str,
    result: ActionResult,
) -> None:
    """Списать аптечку после успешного save хода."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return
    if not storage.remove_item(telegram_id, item_key, 1):
        return
    effect = MEDKIT_EFFECTS.get(item_key)
    if effect is None:
        return
    rad_delta = int(effect.get("radiation", 0))
    if rad_delta < 0 and player.radiation > 0:
        storage.adjust_survival(telegram_id, radiation_delta=rad_delta)


def sync_session_hp_to_db(
    storage: Storage,
    telegram_id: int,
    session_hp: int,
    *,
    force: bool = False,
) -> None:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return
    session_hp = int(session_hp)
    # Респавн / отвязка: не затирать живого игрока старым HP=0 из JSON сессии.
    if not force and session_hp <= 0 and player.health > 0:
        return
    max_hp = effective_max_health(player)
    delta = session_hp - int(player.health)
    if delta != 0:
        storage.change_health(telegram_id, delta, max_health=max_hp)


def commit_tactical_death(
    storage: Storage,
    telegram_id: int,
    session_hp: int = 0,
    *,
    cause: str | None = None,
    killer_name: str | None = None,
) -> None:
    """Записать тактическое падение (HP=0) в БД — для экрана смерти и респавна."""
    from app.game_logic import remember_death_cause, remember_death_killer

    sync_session_hp_to_db(storage, telegram_id, int(session_hp), force=True)
    if int(session_hp) > 0:
        return
    if cause:
        remember_death_cause(storage, telegram_id, cause)
    if killer_name:
        remember_death_killer(storage, telegram_id, killer_name)


def finalize_group_tactical_hp(
    storage: Storage,
    session: Any,
    *,
    cause_default: str = "combat",
    commit_field_deaths: bool = False,
) -> tuple[list[int], dict[str, str], dict[str, str]]:
    """Финализация HP group-сессии: смерть на поле или ранение (1 HP) при провале."""
    dead_ids: list[int] = []
    death_causes_payload: dict[str, str] = {}
    death_killers_payload: dict[str, str] = {}
    player_ids = list(getattr(session, "player_ids", None) or [])
    hp_map: dict[str, int] = getattr(session, "hp", None) or {}
    death_causes = getattr(session, "death_causes", {}) or {}
    death_killers = getattr(session, "death_killers", {}) or {}

    for pid in player_ids:
        hp_val = hp_map.get(str(pid))
        if hp_val is None:
            continue
        field_hp = int(hp_val)
        if field_hp <= 0:
            if commit_field_deaths:
                cause = str(death_causes.get(str(pid)) or cause_default)
                killer_name = death_killers.get(str(pid))
                commit_tactical_death(
                    storage,
                    pid,
                    0,
                    cause=cause,
                    killer_name=str(killer_name) if killer_name else None,
                )
                dead_ids.append(pid)
                death_causes_payload[str(pid)] = cause
                if killer_name:
                    death_killers_payload[str(pid)] = str(killer_name)
            else:
                sync_session_hp_to_db(storage, pid, 1, force=True)
        else:
            sync_session_hp_to_db(storage, pid, field_hp, force=True)

    return dead_ids, death_causes_payload, death_killers_payload


def finalize_solo_tactical_hp(
    storage: Storage,
    telegram_id: int,
    field_hp: int,
    *,
    cause: str = "arena",
) -> None:
    """Финализация HP solo-режима (арена): смерть или синк выжившего."""
    if int(field_hp) <= 0:
        commit_tactical_death(storage, telegram_id, 0, cause=cause)
    else:
        sync_session_hp_to_db(storage, telegram_id, int(field_hp), force=True)
