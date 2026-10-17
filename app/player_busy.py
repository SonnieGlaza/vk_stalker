"""Проверка: игрок уже в другом активном режиме."""

from __future__ import annotations

from app.storage import Storage


def clear_smuggling_state(storage: Storage, telegram_id: int) -> None:
    from app.game_logic import clear_active_smuggling
    from app.smuggle_mission import clear_smuggle_session

    clear_smuggle_session(storage, telegram_id)
    clear_active_smuggling(storage, telegram_id)


def _sync_field_hp_before_unlink(storage: Storage, telegram_id: int) -> None:
    from app.tactical_hp import sync_session_hp_to_db

    pid = int(telegram_id)
    key = str(pid)

    from app.coop_mission import get_coop_session_by_player
    from app.neutral_capture import get_ncap_session
    from app.clan_war_grid import get_cwar_session_by_player
    from app.raid_grid import get_raid_grid_session_by_player

    for getter in (
        get_coop_session_by_player,
        get_ncap_session,
        get_cwar_session_by_player,
        get_raid_grid_session_by_player,
    ):
        session = getter(storage, pid)
        if session is None or getattr(session, "finished", False):
            continue
        hp_map = getattr(session, "hp", None)
        if isinstance(hp_map, dict) and key in hp_map:
            sync_session_hp_to_db(storage, pid, int(hp_map[key]), force=True)


def _unlink_from_shared_sessions(storage: Storage, telegram_id: int, *, sync_hp: bool = True) -> None:
    """Снять привязку игрока к групповому бою, оставив сессию для остальных."""
    if sync_hp:
        _sync_field_hp_before_unlink(storage, telegram_id)
    from app.clan_war_grid import unlink_player_from_cwar_session
    from app.coop_mission import unlink_player_from_coop_session
    from app.neutral_capture import unlink_player_from_ncap_session
    from app.raid_grid import unlink_player_from_raid_grid_session

    unlink_player_from_coop_session(storage, telegram_id)
    unlink_player_from_ncap_session(storage, telegram_id)
    unlink_player_from_cwar_session(storage, telegram_id)
    unlink_player_from_raid_grid_session(storage, telegram_id)


def _clear_group_lobbies(storage: Storage, telegram_id: int) -> None:
    from app.coop_mission import eject_player_from_coop_lobby
    from app.neutral_capture import eject_player_from_ncap_lobby

    eject_player_from_coop_lobby(storage, telegram_id)
    eject_player_from_ncap_lobby(storage, telegram_id)


def _clear_solo_activity(storage: Storage, telegram_id: int) -> None:
    from app.quest_mission import clear_mission_session, get_mission_session
    from app.artifact_hunt import clear_hunt_session, get_hunt_session
    from app.stash_hunt import clear_stash_session, get_stash_session
    from app.lab_mission import clear_lab_session, get_lab_session

    clear_smuggling_state(storage, telegram_id)
    _clear_group_lobbies(storage, telegram_id)

    if get_mission_session(storage, telegram_id):
        clear_mission_session(storage, telegram_id)
        storage.set_active_contract(telegram_id, None)

    if get_hunt_session(storage, telegram_id):
        clear_hunt_session(storage, telegram_id)

    if get_stash_session(storage, telegram_id):
        clear_stash_session(storage, telegram_id)

    if get_lab_session(storage, telegram_id):
        clear_lab_session(storage, telegram_id)


def _forfeit_tactical_sessions(storage: Storage, telegram_id: int) -> bool:
    """Завершить тактические режимы штатно. False — если игрок всё ещё привязан."""
    from app.duel_grid import duel_forfeit, get_duel_session_by_player
    from app.arena_grid import arena_forfeit, get_arena_session
    from app.coop_mission import coop_forfeit, get_coop_session_by_player
    from app.raid_grid import get_raid_grid_session_by_player, rgrid_forfeit
    from app.neutral_capture import get_ncap_session, ncap_forfeit
    from app.clan_war_grid import cwar_forfeit, get_cwar_session_by_player

    if get_coop_session_by_player(storage, telegram_id):
        coop_forfeit(storage, telegram_id)
    if get_raid_grid_session_by_player(storage, telegram_id):
        rgrid_forfeit(storage, telegram_id)
    if get_ncap_session(storage, telegram_id):
        ncap_forfeit(storage, telegram_id)
    if get_cwar_session_by_player(storage, telegram_id):
        cwar_forfeit(storage, telegram_id)
    if get_duel_session_by_player(storage, telegram_id):
        duel_forfeit(storage, telegram_id)
    if get_arena_session(storage, telegram_id):
        arena_forfeit(storage, telegram_id)

    return player_busy_reason(storage, telegram_id, auto_recover=False) is None


def clear_all_activity_sessions(storage: Storage, telegram_id: int) -> None:
    """Снять все активные режимы после респавна (не затирать восстановленное HP)."""
    from app.duel_grid import duel_forfeit, get_duel_session_by_player
    from app.arena_grid import arena_forfeit, get_arena_session

    _clear_solo_activity(storage, telegram_id)
    _unlink_from_shared_sessions(storage, telegram_id, sync_hp=False)

    duel = get_duel_session_by_player(storage, telegram_id)
    if duel is not None:
        duel_forfeit(storage, telegram_id)

    arena = get_arena_session(storage, telegram_id)
    if arena is not None:
        arena_forfeit(storage, telegram_id)


def clear_stale_activity_for_dead_player(storage: Storage, telegram_id: int) -> None:
    """Мёртвый игрок не должен оставаться «занятым» вылазкой или боем."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.health > 0:
        return

    from app.duel_grid import duel_forfeit, get_duel_session_by_player
    from app.arena_grid import arena_forfeit, get_arena_session
    from app.raid_grid import clear_stale_raid_grid_session

    _clear_solo_activity(storage, telegram_id)

    duel = get_duel_session_by_player(storage, telegram_id)
    if duel is not None:
        duel_forfeit(storage, telegram_id)

    arena = get_arena_session(storage, telegram_id)
    if arena is not None:
        arena_forfeit(storage, telegram_id)

    clear_stale_raid_grid_session(storage, telegram_id)
    _unlink_from_shared_sessions(storage, telegram_id, sync_hp=True)


def force_clear_live_player_sessions(storage: Storage, telegram_id: int) -> None:
    """Сброс зависших режимов для живого игрока (/fixme) — с финализацией боёв."""
    _clear_solo_activity(storage, telegram_id)
    if not _forfeit_tactical_sessions(storage, telegram_id):
        _unlink_from_shared_sessions(storage, telegram_id, sync_hp=True)
        from app.duel_grid import clear_duel_session, get_duel_session_by_player
        from app.arena_grid import clear_arena_session, get_arena_session

        duel = get_duel_session_by_player(storage, telegram_id)
        if duel is not None:
            clear_duel_session(storage, duel)
        arena = get_arena_session(storage, telegram_id)
        if arena is not None:
            clear_arena_session(storage, arena)


def recover_stuck_player(storage: Storage, telegram_id: int, *, force_clear: bool = False) -> tuple[bool, int]:
    """Сбросить зависшие режимы. Возвращает (is_dead, health)."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    is_dead = player is not None and player.health <= 0

    if force_clear:
        if is_dead:
            clear_stale_activity_for_dead_player(storage, telegram_id)
        else:
            force_clear_live_player_sessions(storage, telegram_id)
    else:
        clear_stale_activity_for_dead_player(storage, telegram_id)

    player = storage.get_character(telegram_id, refresh_energy=True)
    if player is None:
        return False, 0
    return player.health <= 0, int(player.health)


def player_busy_reason(storage: Storage, telegram_id: int, *, skip: str | None = None, auto_recover: bool = True) -> str | None:
    """Вернёт текст блокировки или None если свободен. skip: duel|coop|quest|hunt|cwar|ncap|rgrid|arena|travel|smuggle."""
    from app.session_recovery import auto_recover_before_busy_check

    if auto_recover:
        auto_recover_before_busy_check(storage, telegram_id)
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is not None and player.health <= 0:
        return "Ты без сознания — сначала респавн (/respawn)."

    from app.duel_grid import get_duel_session_by_player
    from app.coop_mission import get_coop_session_by_player, get_coop_lobby_by_player
    from app.quest_mission import get_mission_session
    from app.artifact_hunt import get_hunt_session
    from app.clan_war_grid import get_cwar_session_by_player
    from app.neutral_capture import get_ncap_session, get_ncap_lobby_by_player
    from app.game_logic import is_traveling

    if skip != "duel" and get_duel_session_by_player(storage, telegram_id):
        return "Ты в тактической дуэли — сначала закончи бой."
    if skip != "cwar" and get_cwar_session_by_player(storage, telegram_id):
        return "Ты в тактическом штурме — сначала закончи бой."
    if skip != "rgrid":
        from app.raid_grid import get_raid_grid_session_by_player

        if get_raid_grid_session_by_player(storage, telegram_id):
            return "Ты в тактическом рейде — сначала закончи бой."
    if skip != "ncap" and get_ncap_session(storage, telegram_id):
        return "Ты захватываешь нейтральную точку — сначала закончи."
    if skip != "ncap" and get_ncap_lobby_by_player(storage, telegram_id):
        return "Ты в группе захвата нейтральной точки — сначала выйди или начни вылазку."
    if skip != "arena":
        from app.arena_grid import get_arena_session

        if get_arena_session(storage, telegram_id):
            return "Ты на арене — сначала закончи бой."
    if skip != "coop":
        if get_coop_session_by_player(storage, telegram_id):
            return "Ты на кооп-вылазке — сначала закончи миссию."
        if get_coop_lobby_by_player(storage, telegram_id):
            return "Ты в кооп-группе — выйди или начни вылазку."
    if skip != "quest" and get_mission_session(storage, telegram_id):
        return "Ты на вылазке по контракту — сначала закончи или сваливай."
    if skip != "hunt" and get_hunt_session(storage, telegram_id):
        return "Ты на охоте за артефактами — сначала закончи или сваливай."
    if skip != "stash":
        from app.stash_hunt import get_stash_session

        if get_stash_session(storage, telegram_id):
            return "Ты ищешь схрон — сначала закончи или сваливай."
    if skip != "lab":
        from app.lab_mission import get_lab_session

        if get_lab_session(storage, telegram_id):
            return "Ты в лаборатории — сначала закончи или сваливай."
    if skip != "smuggle":
        from app.game_logic import get_active_smuggling
        from app.smuggle_mission import get_smuggle_session

        if get_smuggle_session(storage, telegram_id):
            return "Ты на тактическом рейсе контрабанды — пройди маршрут или сбрось груз."
        if get_active_smuggling(storage, telegram_id):
            return "Ты на рейсе контрабанды — сначала заверши доставку."
    if skip != "travel":
        if player is not None and is_traveling(player):
            return "Ты в пути — дождись прибытия."
    return None
