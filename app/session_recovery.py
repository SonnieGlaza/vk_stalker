"""Автовосстановление при «висящем» контракте без тактической сессии."""

from __future__ import annotations

from app.storage import Storage

_RECOVER_GUARD: set[int] = set()


def try_auto_recover_orphan_contract(storage: Storage, telegram_id: int) -> str | None:
    """Сбросить контракт, если вылазка была начата, но тактическая сессия пропала."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or not player.active_contract_json:
        return None

    active = storage.get_active_contract(telegram_id)
    if not active:
        return None

    stage = str(active.get("stage", "work"))
    if stage == "return":
        return None
    if not active.get("mission_started"):
        return None

    from app.artifact_hunt import get_hunt_session
    from app.player_busy import force_clear_live_player_sessions
    from app.quest_mission import get_mission_session
    from app.smuggle_mission import get_smuggle_session

    if get_mission_session(storage, telegram_id):
        return None
    if get_hunt_session(storage, telegram_id):
        return None
    if get_smuggle_session(storage, telegram_id):
        return None

    force_clear_live_player_sessions(storage, telegram_id)
    storage.set_active_contract(telegram_id, None)
    return (
        "Контракт на карте пропал (сессия истекла). Я снял зависший контракт — "
        "можешь принять задание заново. Если снова застрянет — /fixme."
    )


def auto_recover_before_busy_check(storage: Storage, telegram_id: int) -> None:
    tid = int(telegram_id)
    if tid in _RECOVER_GUARD:
        return
    _RECOVER_GUARD.add(tid)
    try:
        try_auto_recover_orphan_contract(storage, tid)
    finally:
        _RECOVER_GUARD.discard(tid)
