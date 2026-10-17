"""Захват нейтральной точки: группа от 2 бойцов, бандиты/мутанты стреляют как в «Танчиках»."""

from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

from app.game_logic import (
    NCAP_SUCCESS_PAY_RU,
    RATING_REWARD,
    ActionResult,
    _add_rating,
    _dead_block_text,
    _is_dead,
    apply_incoming_damage,
    h,
)
from app.tactical_combat import (
    MOVE_DELTAS,
    NPC_WEAPONS,
    STALE_TURN_MESSAGE,
    collect_player_shot_hits,
    consume_shot_ammo,
    npc_weapon_damage,
    random_hostile_shots,
    weapon_shoot_range,
)
from app.mutant_assets import pick_mutant_kind
from app.npc_assets import pick_npc_kind
from app.storage import Character, Storage
from app.tactical_hp import apply_tactical_medkit_spend, finalize_group_tactical_hp, plan_tactical_medkit
from app.enemy_hud import draw_enemy_hud, hud_slots_from_mixed_kinds
from app.tactical_render import hostile_kind_to_sprite, load_tactical_font, paste_mutant_sprite, paste_npc_sprite, paste_player_avatar

NCAP_GRID_SIZE = 6
NCAP_TURN_SECONDS = 10
NCAP_MATCH_SECONDS = 8 * 60
NCAP_HOSTILE_COUNT = 6
NCAP_ENERGY_COST = 18
NCAP_CAPTURE_TURNS = 2
NCAP_MIN_MEMBERS = 2
NCAP_MAX_MEMBERS = 5

LOBBY_PREFIX = "ncap:lobby:"
SESSION_PREFIX = "ncap:session:"
PLAYER_PREFIX = "ncap:player:"
OPEN_LOBBIES_KEY = "ncap:open_lobbies"
ACTIVE_IDS_KEY = "ncap:active_ids"
NCAP_LOCATION_LOCK_PREFIX = "ncap:loclock:"

PLAYER_COLORS = [
    (80, 200, 255),
    (255, 180, 70),
    (120, 255, 140),
    (255, 120, 200),
    (180, 140, 255),
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _deadline_iso(seconds: int) -> str:
    return (_utc_now() + timedelta(seconds=seconds)).isoformat()


def _parse_deadline(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _player_ref(kind: str, ref_id: str) -> str:
    return f"{kind}:{ref_id}"


def _parse_player_ref(raw: str | None) -> tuple[str, str] | None:
    if not raw or ":" not in raw:
        return None
    kind, ref_id = raw.split(":", 1)
    if kind in {"lobby", "session"} and ref_id:
        return kind, ref_id
    return None


@dataclass
class NcapLobby:
    lobby_id: str
    host_id: int
    member_ids: list[int]
    location_name: str
    faction: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "lobby_id": self.lobby_id,
            "host_id": self.host_id,
            "member_ids": list(self.member_ids),
            "location_name": self.location_name,
            "faction": self.faction,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> NcapLobby:
        return cls(
            lobby_id=str(raw.get("lobby_id") or ""),
            host_id=int(raw.get("host_id") or 0),
            member_ids=[int(x) for x in (raw.get("member_ids") or [])],
            location_name=str(raw.get("location_name") or ""),
            faction=str(raw.get("faction") or ""),
        )


@dataclass
class NeutralCaptureSession:
    session_id: str
    host_id: int
    player_ids: list[int]
    location_name: str
    faction: str
    grid: int = NCAP_GRID_SIZE
    cover: list[tuple[int, int]] = field(default_factory=list)
    hostiles: list[tuple[int, int]] = field(default_factory=list)
    hostile_weapons: list[str] = field(default_factory=list)
    hostile_kinds: list[str] = field(default_factory=list)
    capture_point: tuple[int, int] = (2, 2)
    positions: dict[str, list[int]] = field(default_factory=dict)
    hp: dict[str, int] = field(default_factory=dict)
    medkits_used: dict[str, bool] = field(default_factory=dict)
    turn_order: list[int] = field(default_factory=list)
    active_index: int = 0
    turn_seq: int = 0
    turn_deadline: str | None = None
    match_deadline: str | None = None
    capture_progress: int = 0
    finished: bool = False
    success: bool = False
    log: list[str] = field(default_factory=list)
    message_ids: dict[str, int] = field(default_factory=dict)
    death_causes: dict[str, str] = field(default_factory=dict)
    death_killers: dict[str, str] = field(default_factory=dict)

    def active_player(self) -> int:
        from app.tactical_roster import resolve_active_player

        return resolve_active_player(self, empty_fallback=self.host_id)

    def pos(self, player_id: int) -> tuple[int, int]:
        raw = self.positions.get(str(player_id), [0, 0])
        return int(raw[0]), int(raw[1])

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "host_id": self.host_id,
            "player_ids": list(self.player_ids),
            "location_name": self.location_name,
            "faction": self.faction,
            "grid": self.grid,
            "cover": [list(p) for p in self.cover],
            "hostiles": [list(p) for p in self.hostiles],
            "hostile_weapons": self.hostile_weapons,
            "hostile_kinds": self.hostile_kinds,
            "capture_point": list(self.capture_point),
            "positions": {k: list(v) for k, v in self.positions.items()},
            "hp": {str(k): int(v) for k, v in self.hp.items()},
            "medkits_used": {str(k): bool(v) for k, v in self.medkits_used.items()},
            "turn_order": list(self.turn_order),
            "active_index": self.active_index,
            "turn_seq": self.turn_seq,
            "turn_deadline": self.turn_deadline,
            "match_deadline": self.match_deadline,
            "capture_progress": self.capture_progress,
            "finished": self.finished,
            "success": self.success,
            "log": self.log[-12:],
            "message_ids": {str(k): int(v) for k, v in self.message_ids.items()},
            "death_causes": dict(self.death_causes),
            "death_killers": dict(self.death_killers),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> NeutralCaptureSession:
        cp = raw.get("capture_point") or [2, 2]
        if raw.get("player_ids"):
            return cls(
                session_id=str(raw.get("session_id") or ""),
                host_id=int(raw.get("host_id") or 0),
                player_ids=[int(x) for x in (raw.get("player_ids") or [])],
                location_name=str(raw.get("location_name") or ""),
                faction=str(raw.get("faction") or ""),
                grid=int(raw.get("grid") or NCAP_GRID_SIZE),
                cover=[(int(p[0]), int(p[1])) for p in (raw.get("cover") or [])],
                hostiles=[(int(p[0]), int(p[1])) for p in (raw.get("hostiles") or [])],
                hostile_weapons=[str(w) for w in (raw.get("hostile_weapons") or [])],
                hostile_kinds=[str(k) for k in (raw.get("hostile_kinds") or [])],
                capture_point=(int(cp[0]), int(cp[1])),
                positions={str(k): [int(v[0]), int(v[1])] for k, v in (raw.get("positions") or {}).items()},
                hp={str(k): int(v) for k, v in (raw.get("hp") or {}).items()},
                medkits_used={str(k): bool(v) for k, v in (raw.get("medkits_used") or {}).items()},
                turn_order=[int(x) for x in (raw.get("turn_order") or [])],
                active_index=int(raw.get("active_index") or 0),
                turn_seq=int(raw.get("turn_seq") or 0),
                turn_deadline=raw.get("turn_deadline"),
                match_deadline=raw.get("match_deadline"),
                capture_progress=int(raw.get("capture_progress") or 0),
                finished=bool(raw.get("finished")),
                success=bool(raw.get("success")),
                log=[str(x) for x in (raw.get("log") or [])],
                message_ids={str(k): int(v) for k, v in (raw.get("message_ids") or {}).items()},
                death_causes={str(k): str(v) for k, v in (raw.get("death_causes") or {}).items()},
                death_killers={str(k): str(v) for k, v in (raw.get("death_killers") or {}).items()},
            )
        legacy_id = int(raw.get("telegram_id") or 0)
        pp = raw.get("player_pos") or [0, 0]
        msg_id = raw.get("message_id")
        message_ids = {str(legacy_id): int(msg_id)} if msg_id is not None else {}
        return cls(
            session_id=str(raw.get("session_id") or ""),
            host_id=legacy_id,
            player_ids=[legacy_id] if legacy_id else [],
            location_name=str(raw.get("location_name") or ""),
            faction=str(raw.get("faction") or ""),
            grid=int(raw.get("grid") or NCAP_GRID_SIZE),
            cover=[(int(p[0]), int(p[1])) for p in (raw.get("cover") or [])],
            hostiles=[(int(p[0]), int(p[1])) for p in (raw.get("hostiles") or [])],
            hostile_weapons=[str(w) for w in (raw.get("hostile_weapons") or [])],
            hostile_kinds=[str(k) for k in (raw.get("hostile_kinds") or [])],
            capture_point=(int(cp[0]), int(cp[1])),
            positions={str(legacy_id): [int(pp[0]), int(pp[1])]},
            hp={str(legacy_id): int(raw.get("hp") or 0)},
            medkits_used={str(legacy_id): bool(raw.get("medkit_used"))},
            turn_order=[legacy_id] if legacy_id else [],
            active_index=0,
            turn_seq=int(raw.get("turn_seq") or 0),
            turn_deadline=raw.get("turn_deadline"),
            match_deadline=raw.get("match_deadline"),
            capture_progress=int(raw.get("capture_progress") or 0),
            finished=bool(raw.get("finished")),
            success=bool(raw.get("success")),
            log=[str(x) for x in (raw.get("log") or [])],
            message_ids=message_ids,
        )


def _lobby_key(lobby_id: str) -> str:
    return f"{LOBBY_PREFIX}{lobby_id}"


def _session_key(sid: str) -> str:
    return f"{SESSION_PREFIX}{sid}"


def _player_key(tid: int) -> str:
    return f"{PLAYER_PREFIX}{int(tid)}"


def _ncap_location_lock_key(location_name: str) -> str:
    return f"{NCAP_LOCATION_LOCK_PREFIX}{location_name}"


def _release_ncap_location_lock(storage: Storage, location_name: str) -> None:
    storage.delete_meta(_ncap_location_lock_key(location_name))


def get_player_ncap_ref(storage: Storage, telegram_id: int) -> tuple[str, str] | None:
    raw = storage.get_meta(_player_key(telegram_id))
    if not raw:
        return None
    ref = _parse_player_ref(raw)
    if ref is not None:
        return ref
    if raw:
        return "session", raw
    return None


def get_ncap_lobby(storage: Storage, lobby_id: str) -> NcapLobby | None:
    raw = storage.get_meta(_lobby_key(lobby_id))
    if not raw:
        return None
    try:
        return NcapLobby.from_dict(json.loads(raw))
    except Exception:
        return None


def get_ncap_lobby_by_player(storage: Storage, telegram_id: int) -> NcapLobby | None:
    ref = get_player_ncap_ref(storage, telegram_id)
    if ref is None or ref[0] != "lobby":
        return None
    return get_ncap_lobby(storage, ref[1])


def get_ncap_session(storage: Storage, telegram_id: int) -> NeutralCaptureSession | None:
    ref = get_player_ncap_ref(storage, telegram_id)
    if ref is None or ref[0] != "session":
        return None
    raw = storage.get_meta(_session_key(ref[1]))
    if not raw:
        storage.delete_meta(_player_key(telegram_id))
        return None
    try:
        session = NeutralCaptureSession.from_dict(json.loads(raw))
    except Exception:
        storage.delete_meta(_player_key(telegram_id))
        return None
    if session.finished:
        return None
    return session


def _register_open_lobby(storage: Storage, lobby_id: str) -> None:
    raw = storage.get_meta(OPEN_LOBBIES_KEY)
    ids: list[str] = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                ids = [str(x) for x in parsed]
        except json.JSONDecodeError:
            ids = []
    if lobby_id not in ids:
        ids.append(lobby_id)
    storage.set_meta(OPEN_LOBBIES_KEY, json.dumps(ids, ensure_ascii=False))


def _unregister_open_lobby(storage: Storage, lobby_id: str) -> None:
    raw = storage.get_meta(OPEN_LOBBIES_KEY)
    if not raw:
        return
    try:
        ids = [str(x) for x in json.loads(raw) if str(x) != lobby_id]
    except json.JSONDecodeError:
        ids = []
    storage.set_meta(OPEN_LOBBIES_KEY, json.dumps(ids, ensure_ascii=False))


def save_ncap_lobby(storage: Storage, lobby: NcapLobby) -> None:
    storage.set_meta(_lobby_key(lobby.lobby_id), json.dumps(lobby.to_dict(), ensure_ascii=False))
    for pid in lobby.member_ids:
        storage.set_meta(_player_key(pid), _player_ref("lobby", lobby.lobby_id))


def clear_ncap_lobby(storage: Storage, lobby: NcapLobby) -> None:
    storage.delete_meta(_lobby_key(lobby.lobby_id))
    _unregister_open_lobby(storage, lobby.lobby_id)
    for pid in lobby.member_ids:
        ref = get_player_ncap_ref(storage, pid)
        if ref and ref[0] == "lobby" and ref[1] == lobby.lobby_id:
            storage.delete_meta(_player_key(pid))


def save_ncap_session(storage: Storage, session: NeutralCaptureSession) -> None:
    storage.set_meta(_session_key(session.session_id), json.dumps(session.to_dict(), ensure_ascii=False))
    for pid in session.player_ids:
        storage.set_meta(_player_key(pid), _player_ref("session", session.session_id))


def clear_ncap_session(storage: Storage, session: NeutralCaptureSession) -> None:
    _release_ncap_location_lock(storage, session.location_name)
    storage.delete_meta(_session_key(session.session_id))
    for pid in session.player_ids:
        ref = get_player_ncap_ref(storage, pid)
        if ref and ref[0] == "session" and ref[1] == session.session_id:
            storage.delete_meta(_player_key(pid))
    _unregister_active(storage, session.session_id)


def unlink_player_from_ncap_session(storage: Storage, telegram_id: int) -> None:
    from app.tactical_roster import drop_player_from_tactical_roster

    session = get_ncap_session(storage, telegram_id)
    if session is None:
        return
    drop_player_from_tactical_roster(session, telegram_id)
    storage.delete_meta(_player_key(telegram_id))
    if not session.player_ids:
        clear_ncap_session(storage, session)
        return
    save_ncap_session(storage, session)


def eject_player_from_ncap_lobby(storage: Storage, telegram_id: int) -> None:
    """Убрать игрока из лобби захвата без UI (смерть / fixme)."""
    lobby = get_ncap_lobby_by_player(storage, telegram_id)
    if lobby is None:
        return
    lobby.member_ids = [pid for pid in lobby.member_ids if pid != telegram_id]
    storage.delete_meta(_player_key(telegram_id))
    if not lobby.member_ids:
        clear_ncap_lobby(storage, lobby)
        return
    if lobby.host_id == telegram_id:
        lobby.host_id = lobby.member_ids[0]
    save_ncap_lobby(storage, lobby)


def _register_active(storage: Storage, session_id: str) -> None:
    raw = storage.get_meta(ACTIVE_IDS_KEY)
    ids: list[str] = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                ids = [str(x) for x in parsed]
        except json.JSONDecodeError:
            ids = []
    if session_id not in ids:
        ids.append(session_id)
    storage.set_meta(ACTIVE_IDS_KEY, json.dumps(ids, ensure_ascii=False))


def _unregister_active(storage: Storage, session_id: str) -> None:
    raw = storage.get_meta(ACTIVE_IDS_KEY)
    if not raw:
        return
    try:
        ids = [str(x) for x in json.loads(raw) if str(x) != session_id]
    except json.JSONDecodeError:
        ids = []
    storage.set_meta(ACTIVE_IDS_KEY, json.dumps(ids, ensure_ascii=False))


def ncap_lobby_menu_text(storage: Storage, telegram_id: int) -> str:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return "Сначала создай персонажа."
    session = get_ncap_session(storage, telegram_id)
    if session is not None:
        active = storage.get_character(session.active_player(), refresh_energy=False)
        active_name = active.nickname if active else str(session.active_player())
        return (
            f"🎯 Захват «{session.location_name}».\n"
            f"Бойцов: {len(session.player_ids)}. Ход: {h(active_name)} ({NCAP_TURN_SECONDS} сек).\n"
            f"Удержите центр {NCAP_CAPTURE_TURNS} хода. Враги стреляют наугад."
        )
    lobby = get_ncap_lobby_by_player(storage, telegram_id)
    if lobby is not None:
        names = []
        for pid in lobby.member_ids:
            ch = storage.get_character(pid, refresh_energy=False)
            names.append(h(ch.nickname) if ch else str(pid))
        host_mark = " (лидер)" if lobby.host_id == telegram_id else ""
        return (
            f"🎯 Группа захвата «{lobby.location_name}»{host_mark}.\n"
            f"Участники ({len(lobby.member_ids)}/{NCAP_MAX_MEMBERS}): {', '.join(names)}.\n"
            f"Минимум {NCAP_MIN_MEMBERS} бойца для старта. Энергия: −{NCAP_ENERGY_COST} каждому.\n"
            f"Награда выжившим: +{NCAP_SUCCESS_PAY_RU} RU, +{RATING_REWARD['war_success']} рейт."
        )
    return (
        f"🎯 Захват нейтральных точек — от {NCAP_MIN_MEMBERS} бойцов.\n"
        f"Поле {NCAP_GRID_SIZE}×{NCAP_GRID_SIZE}, −{NCAP_ENERGY_COST} энергии, {NCAP_MATCH_SECONDS // 60} мин."
    )


def create_or_join_ncap_lobby(storage: Storage, telegram_id: int, location_name: str) -> ActionResult:
    from app.player_busy import player_busy_reason

    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Нужен персонаж с группировкой.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    busy = player_busy_reason(storage, telegram_id, skip="ncap")
    if busy:
        return ActionResult(False, busy)
    if get_ncap_session(storage, telegram_id):
        return ActionResult(False, "Уже идёт захват точки.")
    loc = storage.get_location(location_name)
    if loc is None:
        return ActionResult(False, "Локация не найдена.")
    if loc.get("controlled_by"):
        return ActionResult(False, "Точка уже под контролем.")
    if storage.get_meta(_ncap_location_lock_key(location_name)):
        return ActionResult(False, "Эту нейтральную точку уже штурмуют.")

    existing = get_ncap_lobby_by_player(storage, telegram_id)
    if existing is not None:
        if existing.location_name == location_name:
            return ActionResult(True, ncap_lobby_menu_text(storage, telegram_id), payload={"ncap_lobby": True})
        return ActionResult(False, f"Ты уже в группе на «{existing.location_name}». Сначала выйди.")

    for lobby_id in json.loads(storage.get_meta(OPEN_LOBBIES_KEY) or "[]"):
        lobby = get_ncap_lobby(storage, str(lobby_id))
        if (
            lobby is not None
            and lobby.location_name == location_name
            and lobby.faction == player.faction
            and len(lobby.member_ids) < NCAP_MAX_MEMBERS
        ):
            if telegram_id not in lobby.member_ids:
                lobby.member_ids.append(telegram_id)
                save_ncap_lobby(storage, lobby)
                return ActionResult(
                    True,
                    f"Ты вступил в группу захвата «{location_name}». Участников: {len(lobby.member_ids)}.",
                    payload={"ncap_lobby": True},
                )
            return ActionResult(True, ncap_lobby_menu_text(storage, telegram_id), payload={"ncap_lobby": True})

    lobby_id = uuid.uuid4().hex[:10]
    lobby = NcapLobby(
        lobby_id=lobby_id,
        host_id=telegram_id,
        member_ids=[telegram_id],
        location_name=location_name,
        faction=str(player.faction),
    )
    save_ncap_lobby(storage, lobby)
    _register_open_lobby(storage, lobby_id)
    return ActionResult(
        True,
        f"Группа захвата «{location_name}» создана. Нужно ещё минимум {NCAP_MIN_MEMBERS - 1} боец.",
        payload={"ncap_lobby": True},
    )


def join_ncap_lobby(storage: Storage, telegram_id: int, lobby_id: str) -> ActionResult:
    from app.player_busy import player_busy_reason

    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or player.faction is None:
        return ActionResult(False, "Нужен персонаж с группировкой.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if get_ncap_session(storage, telegram_id) or get_ncap_lobby_by_player(storage, telegram_id):
        return ActionResult(False, "Сначала выйди из текущей группы или захвата.")
    busy = player_busy_reason(storage, telegram_id, skip="ncap")
    if busy:
        return ActionResult(False, busy)
    lobby = get_ncap_lobby(storage, lobby_id)
    if lobby is None:
        return ActionResult(False, "Группа не найдена.")
    if lobby.faction != player.faction:
        return ActionResult(False, "Вступать могут только бойцы своей группировки.")
    if storage.get_meta(_ncap_location_lock_key(lobby.location_name)):
        return ActionResult(False, "Эту точку уже штурмуют.")
    if telegram_id in lobby.member_ids:
        return ActionResult(True, "Ты уже в этой группе.", payload={"ncap_lobby": True})
    if len(lobby.member_ids) >= NCAP_MAX_MEMBERS:
        return ActionResult(False, f"В группе уже {NCAP_MAX_MEMBERS} бойцов.")
    lobby.member_ids.append(telegram_id)
    save_ncap_lobby(storage, lobby)
    notify: list[list[Any]] = []
    join_note = (
        f"🎯 {h(player.nickname)} вступил в группу захвата "
        f"({len(lobby.member_ids)}/{NCAP_MAX_MEMBERS}) на «{lobby.location_name}»."
    )
    for pid in lobby.member_ids:
        if pid == telegram_id:
            continue
        notify.append([pid, join_note])
    return ActionResult(
        True,
        f"Ты в группе захвата «{lobby.location_name}». Участников: {len(lobby.member_ids)}.",
        payload={"ncap_lobby": True, "notify": notify} if notify else {"ncap_lobby": True},
    )


def leave_ncap_lobby(storage: Storage, telegram_id: int) -> ActionResult:
    lobby = get_ncap_lobby_by_player(storage, telegram_id)
    if lobby is None:
        return ActionResult(False, "Ты не в группе захвата.")
    if telegram_id not in lobby.member_ids:
        return ActionResult(False, "Ты не в этой группе.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    leaving_name = h(player.nickname) if player else str(telegram_id)
    lobby.member_ids = [pid for pid in lobby.member_ids if pid != telegram_id]
    storage.delete_meta(_player_key(telegram_id))
    if not lobby.member_ids:
        clear_ncap_lobby(storage, lobby)
        return ActionResult(True, "Группа захвата распущена.")
    if lobby.host_id == telegram_id:
        lobby.host_id = lobby.member_ids[0]
    save_ncap_lobby(storage, lobby)
    notify: list[list[Any]] = []
    leave_note = (
        f"🎯 {leaving_name} вышел из группы захвата "
        f"({len(lobby.member_ids)}/{NCAP_MAX_MEMBERS}) на «{lobby.location_name}»."
    )
    for pid in lobby.member_ids:
        notify.append([pid, leave_note])
    return ActionResult(True, "Ты вышел из группы захвата.", payload={"notify": notify})


def start_ncap_from_lobby(storage: Storage, host_id: int) -> tuple[ActionResult, NeutralCaptureSession | None]:
    from app.player_busy import player_busy_reason

    lobby = get_ncap_lobby_by_player(storage, host_id)
    if lobby is None:
        return ActionResult(False, "Группа захвата не найдена."), None
    if lobby.host_id != host_id:
        return ActionResult(False, "Запускать может только лидер группы."), None
    if len(lobby.member_ids) < NCAP_MIN_MEMBERS:
        return ActionResult(False, f"Нужно минимум {NCAP_MIN_MEMBERS} бойца для захвата."), None

    loc = storage.get_location(lobby.location_name)
    if loc is None:
        return ActionResult(False, "Локация не найдена."), None
    if loc.get("controlled_by"):
        return ActionResult(False, "Точка уже под контролем."), None
    lock_key = _ncap_location_lock_key(lobby.location_name)
    if not storage.set_meta_if_absent(lock_key, lobby.lobby_id):
        return ActionResult(False, "Эту нейтральную точку уже штурмуют."), None

    for pid in lobby.member_ids:
        busy = player_busy_reason(storage, pid, skip="ncap")
        if busy:
            _release_ncap_location_lock(storage, lobby.location_name)
            ch = storage.get_character(pid, refresh_energy=False)
            who = h(ch.nickname) if ch else str(pid)
            return ActionResult(False, f"{who}: {busy.lower()}"), None

    spent: list[int] = []
    members: list[Character] = []
    for pid in lobby.member_ids:
        ch = storage.get_character(pid, refresh_energy=False)
        if ch is None or _is_dead(ch) or ch.faction != lobby.faction:
            _release_ncap_location_lock(storage, lobby.location_name)
            for refund in spent:
                storage.restore_energy(refund, NCAP_ENERGY_COST)
            return ActionResult(False, "Один из участников недоступен."), None
        if ch.energy < NCAP_ENERGY_COST:
            _release_ncap_location_lock(storage, lobby.location_name)
            for refund in spent:
                storage.restore_energy(refund, NCAP_ENERGY_COST)
            return ActionResult(False, f"У {h(ch.nickname)} не хватает энергии ({NCAP_ENERGY_COST})."), None
        members.append(ch)

    for ch in members:
        if storage.spend_energy(ch.telegram_id, NCAP_ENERGY_COST):
            spent.append(ch.telegram_id)
        else:
            _release_ncap_location_lock(storage, lobby.location_name)
            for refund in spent:
                storage.restore_energy(refund, NCAP_ENERGY_COST)
            return ActionResult(False, "Не удалось списать энергию."), None

    session_id = uuid.uuid4().hex[:12]
    player_ids = list(lobby.member_ids)
    session = NeutralCaptureSession(
        session_id=session_id,
        host_id=host_id,
        player_ids=player_ids,
        location_name=lobby.location_name,
        faction=lobby.faction,
        turn_order=list(player_ids),
        active_index=0,
        turn_deadline=_deadline_iso(NCAP_TURN_SECONDS),
        match_deadline=_deadline_iso(NCAP_MATCH_SECONDS),
    )
    for ch in members:
        session.hp[str(ch.telegram_id)] = int(ch.health)
        session.medkits_used[str(ch.telegram_id)] = False
    _build_map(session)
    first = storage.get_character(session.active_player(), refresh_energy=False)
    session.log.append(
        f"Захват «{lobby.location_name}»: {len(player_ids)} бойцов, {NCAP_HOSTILE_COUNT} врагов. "
        f"Первый ход — {h(first.nickname) if first else session.active_player()}."
    )
    clear_ncap_lobby(storage, lobby)
    save_ncap_session(storage, session)
    _register_active(storage, session_id)
    text = (
        f"🎯 Захват нейтральной точки «{lobby.location_name}»!\n"
        f"Поле {NCAP_GRID_SIZE}×{NCAP_GRID_SIZE}, бойцов: {len(player_ids)}.\n"
        f"Цель: удержать центр {NCAP_CAPTURE_TURNS} хода. "
        f"Таймер {NCAP_MATCH_SECONDS // 60} мин, ход {NCAP_TURN_SECONDS} сек.\n"
        f"Награда выжившим: +{NCAP_SUCCESS_PAY_RU} RU. Отступление: −{RATING_REWARD['war_fail']} рейтинга."
    )
    return ActionResult(
        True,
        text,
        payload={"ncap_started": True, "notify_all": player_ids},
    ), session


def _free_cell(grid: int, forbidden: set[tuple[int, int]]) -> tuple[int, int]:
    opts = [(x, y) for x in range(grid) for y in range(grid) if (x, y) not in forbidden]
    return random.choice(opts)


def _build_map(session: NeutralCaptureSession) -> None:
    grid = session.grid
    forbidden: set[tuple[int, int]] = {session.capture_point}
    for _ in range(random.randint(5, 8)):
        cell = _free_cell(grid, forbidden)
        session.cover.append(cell)
        forbidden.add(cell)
    for _ in range(NCAP_HOSTILE_COUNT):
        cell = _free_cell(grid, forbidden)
        session.hostiles.append(cell)
        kind = "bandit" if random.random() < 0.65 else "mutant"
        session.hostile_kinds.append(pick_npc_kind() if kind == "bandit" else pick_mutant_kind())
        if kind == "bandit":
            session.hostile_weapons.append(random.choice(NPC_WEAPONS))
        else:
            session.hostile_weapons.append("ПМ")
        forbidden.add(cell)
    for pid in session.player_ids:
        cell = _free_cell(grid, forbidden)
        session.positions[str(pid)] = [cell[0], cell[1]]
        forbidden.add(cell)


def _occupied(session: NeutralCaptureSession) -> set[tuple[int, int]]:
    cells = set(session.cover) | set(session.hostiles)
    for pid in session.player_ids:
        cells.add(session.pos(pid))
    return cells


def _alive_players(session: NeutralCaptureSession) -> list[int]:
    return [pid for pid in session.player_ids if session.hp.get(str(pid), 0) > 0]


def _hostile_damage(weapon: str) -> int:
    return npc_weapon_damage(weapon)


def _hostile_shoot_turn(storage: Storage, session: NeutralCaptureSession) -> list[str]:
    from app.tactical_roster import mark_new_field_deaths

    cover_set = set(session.cover)
    alive = _alive_players(session)
    alive_before = list(alive)
    if not alive:
        return []
    player_pos = {pid: session.pos(pid) for pid in alive}
    player_chars = {
        pid: ch
        for pid in alive
        if (ch := storage.get_character(pid, refresh_energy=False)) is not None
    }
    hp_snapshot = {str(pid): session.hp.get(str(pid), 0) for pid in alive}
    notes = random_hostile_shots(
        session.hostiles,
        session.hostile_weapons,
        grid=session.grid,
        player_positions=player_pos,
        player_hp=hp_snapshot,
        player_characters=player_chars,
        cover=cover_set,
        base_cover=set(),
        damage_fn=_hostile_damage,
    )
    for key, val in hp_snapshot.items():
        session.hp[key] = val
    mark_new_field_deaths(session, alive_before, cause="npc")
    return notes


def _finalize_success(storage: Storage, session: NeutralCaptureSession) -> ActionResult:
    from app.faction_bots import apply_location_control

    apply_location_control(storage, session.location_name, session.faction)
    from app.faction_goals import record_faction_goal_event

    record_faction_goal_event(storage, session.faction, "captures")
    survivors = _alive_players(session)
    for pid in survivors:
        storage.add_player_stat(pid, "wars_won", 1)
        storage.change_money(pid, NCAP_SUCCESS_PAY_RU)
        storage.add_player_stat(pid, "money_earned", NCAP_SUCCESS_PAY_RU)
        _add_rating(storage, pid, RATING_REWARD["war_success"])
    text = (
        f"🏆 Нейтральная точка «{session.location_name}» захвачена!\n"
        f"Контроль: {session.faction}. Выжившим: +{NCAP_SUCCESS_PAY_RU} RU, "
        f"+{RATING_REWARD['war_success']} рейтинга."
    )
    return ActionResult(
        True,
        text,
        payload={"ncap_done": True, "success": True, "notify_all": list(session.player_ids)},
    )


def _finalize_fail(storage: Storage, session: NeutralCaptureSession, reason: str) -> ActionResult:
    for pid in session.player_ids:
        _add_rating(storage, pid, -RATING_REWARD["war_fail"])
    return ActionResult(
        False,
        f"💀 Захват «{session.location_name}» провален.\n{reason}\n−{RATING_REWARD['war_fail']} рейтинга.",
        payload={"ncap_done": True, "success": False, "notify_all": list(session.player_ids)},
    )


def _end_session(storage: Storage, session: NeutralCaptureSession, result: ActionResult) -> ActionResult:
    payload = dict(result.payload or {})
    commit_deaths = bool(payload.get("success"))
    dead_ids, death_causes, death_killers = finalize_group_tactical_hp(
        storage,
        session,
        cause_default="ncap",
        commit_field_deaths=commit_deaths,
    )
    if dead_ids:
        payload["dead_players"] = dead_ids
        payload["death_causes"] = death_causes
        payload["death_killers"] = death_killers
    message_ids = dict(session.message_ids)
    session.finished = True
    save_ncap_session(storage, session)
    clear_ncap_session(storage, session)
    payload["message_ids"] = message_ids
    payload["notify_all"] = list(session.player_ids)
    payload["session_id"] = session.session_id
    return ActionResult(result.ok, result.text, payload=payload)


def _advance_turn(session: NeutralCaptureSession) -> None:
    session.turn_seq += 1
    session.turn_deadline = _deadline_iso(NCAP_TURN_SECONDS)
    alive = _alive_players(session)
    if not alive or not session.turn_order:
        return
    n = len(session.turn_order)
    for _ in range(n):
        session.active_index = (session.active_index + 1) % n
        pid = session.turn_order[session.active_index]
        if session.hp.get(str(pid), 0) > 0:
            break


def _check_capture(session: NeutralCaptureSession) -> bool:
    holding = any(
        session.pos(pid) == session.capture_point for pid in _alive_players(session)
    )
    if holding:
        session.capture_progress += 1
        session.log.append(f"Удержание точки: {session.capture_progress}/{NCAP_CAPTURE_TURNS}.")
        return session.capture_progress >= NCAP_CAPTURE_TURNS
    session.capture_progress = 0
    return False


def _check_team_wipe(storage: Storage, session: NeutralCaptureSession) -> ActionResult | None:
    if _alive_players(session):
        return None
    return _end_session(storage, session, _finalize_fail(storage, session, "Вся группа выведена из строя."))


def _check_end(storage: Storage, session: NeutralCaptureSession) -> ActionResult | None:
    wipe = _check_team_wipe(storage, session)
    if wipe:
        return wipe
    deadline = _parse_deadline(session.match_deadline)
    if deadline and _utc_now() > deadline:
        return _end_session(storage, session, _finalize_fail(storage, session, "Время вышло."))
    if _check_capture(session):
        return _end_session(storage, session, _finalize_success(storage, session))
    return None


def _after_turn(storage: Storage, session: NeutralCaptureSession) -> ActionResult | None:
    session.log.extend(_hostile_shoot_turn(storage, session))
    return _check_end(storage, session)


def _save_turn(storage: Storage, session: NeutralCaptureSession, expected_seq: int) -> bool:
    from app.tactical_turn import save_turn_if_seq_ok

    return save_turn_if_seq_ok(
        storage,
        meta_key=_session_key(session.session_id),
        session=session,
        from_dict=NeutralCaptureSession.from_dict,
        save_fn=save_ncap_session,
        expected_seq=expected_seq,
    )


def _require_active_turn(session: NeutralCaptureSession, telegram_id: int) -> ActionResult | None:
    if session.hp.get(str(telegram_id), 0) <= 0:
        return ActionResult(False, "Ты выведен из строя.")
    if session.active_player() != telegram_id:
        active = session.active_player()
        return ActionResult(False, f"Сейчас ход другого бойца (ID {active}).")
    return None


def ncap_move(storage: Storage, telegram_id: int, direction: str) -> ActionResult:
    session = get_ncap_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Нет активного захвата.")
    blocked_turn = _require_active_turn(session, telegram_id)
    if blocked_turn:
        return blocked_turn
    delta = MOVE_DELTAS.get(direction)
    if delta is None:
        return ActionResult(False, "Некорректное направление.")
    turn_seq = session.turn_seq
    pos = session.pos(telegram_id)
    nxt = (pos[0] + delta[0], pos[1] + delta[1])
    if not (0 <= nxt[0] < session.grid and 0 <= nxt[1] < session.grid):
        return ActionResult(False, "Край поля.")
    blocked = _occupied(session)
    blocked.discard(pos)
    if nxt in blocked and nxt not in session.cover:
        return ActionResult(False, "Клетка занята.")
    session.positions[str(telegram_id)] = [nxt[0], nxt[1]]
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player and nxt in session.hostiles:
        idx = session.hostiles.index(nxt)
        kind = session.hostile_kinds[idx] if idx < len(session.hostile_kinds) else "bandit"
        session.hostiles.pop(idx)
        if idx < len(session.hostile_weapons):
            session.hostile_weapons.pop(idx)
        if idx < len(session.hostile_kinds):
            session.hostile_kinds.pop(idx)
        dmg = apply_incoming_damage(random.randint(6, 12), player, min_damage=2)
        session.hp[str(telegram_id)] = max(0, session.hp.get(str(telegram_id), 0) - dmg)
        label = "Бандит" if kind in ("bandit", "maloy", "mercenary", "soldier") else "Мутант"
        session.log.append(f"{label} в ближнем бою: −{dmg} HP.")
    wipe = _check_team_wipe(storage, session)
    if wipe:
        return wipe
    _advance_turn(session)
    done = _after_turn(storage, session)
    if done:
        return done
    if not _save_turn(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    return ActionResult(True, "Шаг.", payload={"ncap_active": True, "notify_all": session.player_ids})


def ncap_shoot(storage: Storage, telegram_id: int, direction: str) -> ActionResult:
    session = get_ncap_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Нет активного захвата.")
    blocked_turn = _require_active_turn(session, telegram_id)
    if blocked_turn:
        return blocked_turn
    if direction not in MOVE_DELTAS:
        return ActionResult(False, "Некорректный выстрел.")
    turn_seq = session.turn_seq
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Персонаж не найден.")
    weapon = str(player.equipment.get("weapon", "Нож"))
    ammo_result = consume_shot_ammo(storage, telegram_id, weapon)
    if ammo_result is not None:
        return ammo_result
    origin = session.pos(telegram_id)
    cover_set = set(session.cover)
    targets = {pos: "host" for pos in session.hostiles}
    hits = collect_player_shot_hits(
        origin,
        direction,
        grid=session.grid,
        weapon_name=weapon,
        blockers=cover_set,
        targets=targets,
        cover=cover_set,
    )
    note = "Промах."
    if hits:
        hit_any = False
        for hit_cell, hit_kind in hits:
            if hit_kind == "host" and hit_cell in session.hostiles:
                idx = session.hostiles.index(hit_cell)
                session.hostiles.pop(idx)
                if idx < len(session.hostile_weapons):
                    session.hostile_weapons.pop(idx)
                if idx < len(session.hostile_kinds):
                    session.hostile_kinds.pop(idx)
                hit_any = True
        if hit_any:
            session.log.append(f"Попадание ({weapon})!")
            note = "Попал!"
        else:
            session.log.append("Промах.")
    else:
        session.log.append("Промах.")
    wipe = _check_team_wipe(storage, session)
    if wipe:
        return wipe
    _advance_turn(session)
    done = _after_turn(storage, session)
    if done:
        return done
    if not _save_turn(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    return ActionResult(True, note, payload={"ncap_active": True, "notify_all": session.player_ids})


def ncap_use_medkit(storage: Storage, telegram_id: int) -> ActionResult:
    session = get_ncap_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Нет активного захвата.")
    blocked_turn = _require_active_turn(session, telegram_id)
    if blocked_turn:
        return blocked_turn
    if session.medkits_used.get(str(telegram_id), False):
        return ActionResult(False, "Аптечку уже использовал.")
    turn_seq = session.turn_seq
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Персонаж не найден.")
    current_hp = session.hp.get(str(telegram_id), 0)
    result, new_hp, item_key = plan_tactical_medkit(storage, telegram_id, current_hp)
    if not result.ok:
        return result
    session.hp[str(telegram_id)] = new_hp
    session.medkits_used[str(telegram_id)] = True
    session.log.append("Аптечка.")
    wipe = _check_team_wipe(storage, session)
    if wipe:
        if not _save_turn(storage, session, turn_seq):
            return ActionResult(False, STALE_TURN_MESSAGE)
        if item_key:
            apply_tactical_medkit_spend(storage, telegram_id, item_key, result)
        return wipe
    _advance_turn(session)
    done = _after_turn(storage, session)
    if done:
        if not _save_turn(storage, session, turn_seq):
            return ActionResult(False, STALE_TURN_MESSAGE)
        if item_key:
            apply_tactical_medkit_spend(storage, telegram_id, item_key, result)
        return done
    if not _save_turn(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    if item_key:
        apply_tactical_medkit_spend(storage, telegram_id, item_key, result)
    return ActionResult(True, result.text, payload={"ncap_active": True, "notify_all": session.player_ids})


def ncap_forfeit(storage: Storage, telegram_id: int) -> ActionResult:
    session = get_ncap_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Нет активного захвата.")
    return _end_session(storage, session, _finalize_fail(storage, session, "Отступление."))


def process_ncap_turn_timeouts(storage: Storage) -> list[tuple[int, ActionResult]]:
    outcomes: list[tuple[int, ActionResult]] = []
    now = _utc_now()
    raw = storage.get_meta(ACTIVE_IDS_KEY)
    if not raw:
        return outcomes
    try:
        sids = json.loads(raw)
    except json.JSONDecodeError:
        return outcomes
    if not isinstance(sids, list):
        return outcomes
    still: list[str] = []
    for sid in sids:
        raw_s = storage.get_meta(_session_key(str(sid)))
        if not raw_s:
            continue
        try:
            session = NeutralCaptureSession.from_dict(json.loads(raw_s))
        except Exception:
            continue
        if session.finished:
            continue
        match_deadline = _parse_deadline(session.match_deadline)
        if match_deadline and now > match_deadline:
            done = _end_session(storage, session, _finalize_fail(storage, session, "Время вышло."))
            for pid in session.player_ids:
                outcomes.append((pid, done))
            continue
        deadline = _parse_deadline(session.turn_deadline)
        if deadline is None or now <= deadline:
            still.append(str(sid))
            continue
        turn_seq = session.turn_seq
        session.log.append("Тайм-аут хода.")
        _advance_turn(session)
        done = _after_turn(storage, session)
        if done:
            for pid in session.player_ids:
                outcomes.append((pid, done))
            continue
        still.append(str(sid))
        if _save_turn(storage, session, turn_seq):
            skip = ActionResult(True, "Ход пропущен.", payload={"ncap_active": True, "notify_all": session.player_ids})
            for pid in session.player_ids:
                outcomes.append((pid, skip))
    storage.set_meta(ACTIVE_IDS_KEY, json.dumps(still, ensure_ascii=False))
    return outcomes


def ncap_status_caption(session: NeutralCaptureSession, player: Character | None, viewer_id: int) -> str:
    lines = [f"🎯 Захват «{session.location_name}»"]
    deadline = _parse_deadline(session.match_deadline)
    if deadline:
        secs = max(0, int((deadline - _utc_now()).total_seconds()))
        lines.append(f"⏱ {secs // 60}:{secs % 60:02d}")
    active_pid = session.active_player()
    lines.append(f"Бойцов: {len(_alive_players(session))}/{len(session.player_ids)}")
    lines.append(f"Врагов: {len(session.hostiles)} · захват {session.capture_progress}/{NCAP_CAPTURE_TURNS}")
    if player:
        weapon = str(player.equipment.get("weapon", "Нож"))
        lines.append(f"HP {session.hp.get(str(viewer_id), 0)} · дальность {weapon_shoot_range(weapon)}")
    if active_pid == viewer_id:
        lines.append("▶️ Твой ход")
    elif active_pid > 0:
        lines.append(f"⏳ Ход бойца ID {active_pid}")
    else:
        lines.append("⏳ Ожидание хода")
    lines.append("🔷 синяя клетка на карте = вы")
    if session.log:
        lines.append(session.log[-1][:80])
    return "\n".join(lines)


def render_ncap_frame(storage: Storage, session: NeutralCaptureSession, viewer_id: int) -> bytes:
    cell = 72
    grid = session.grid
    grid_px = grid * cell
    margin = 20
    panel_w = 260
    width = margin + grid_px + 16 + panel_w + margin
    height = max(margin + grid_px + margin, 520)
    canvas = Image.new("RGBA", (width, height), (18, 20, 22, 255))
    draw = ImageDraw.Draw(canvas)
    cp = session.capture_point
    for gy in range(grid):
        for gx in range(grid):
            left = margin + gx * cell
            top = margin + gy * cell
            tone = 62 + ((gx * 17 + gy * 23) % 18)
            draw.rectangle((left, top, left + cell - 1, top + cell - 1), fill=(tone, tone - 4, tone - 8))
            draw.rectangle((left, top, left + cell - 1, top + cell - 1), outline=(28, 30, 34), width=1)
    draw.rectangle(
        (margin + cp[0] * cell + 6, margin + cp[1] * cell + 6, margin + (cp[0] + 1) * cell - 6, margin + (cp[1] + 1) * cell - 6),
        outline=(255, 200, 50),
        width=3,
    )
    for cx, cy in session.cover:
        left = margin + cx * cell + 6
        top = margin + cy * cell + 6
        draw.rounded_rectangle((left, top, left + cell - 12, top + cell - 12), radius=8, fill=(70, 62, 48, 220), outline=(110, 95, 70))
    for i, (hx, hy) in enumerate(session.hostiles):
        cx = margin + hx * cell + cell // 2
        cy = margin + hy * cell + cell // 2
        kind = session.hostile_kinds[i] if i < len(session.hostile_kinds) else "bandit"
        sprite_key, is_npc = hostile_kind_to_sprite(kind)
        if is_npc:
            paste_npc_sprite(canvas, draw, cx=cx, cy=cy, kind=sprite_key, diameter=56)
        else:
            paste_mutant_sprite(canvas, draw, cx=cx, cy=cy, kind=sprite_key, diameter=56)
    active = session.active_player()
    viewer_pos = session.pos(viewer_id)
    for idx, pid in enumerate(session.player_ids):
        px, py = session.pos(pid)
        pcx = margin + px * cell + cell // 2
        pcy = margin + py * cell + cell // 2
        color = PLAYER_COLORS[idx % len(PLAYER_COLORS)]
        paste_player_avatar(
            canvas,
            draw,
            storage,
            pid=pid,
            cx=pcx,
            cy=pcy,
            diameter=52,
            ring_color=color,
            hp=session.hp.get(str(pid), 0),
            is_active=(pid == active),
            viewer_cell=(margin, cell, viewer_pos[0], viewer_pos[1]) if pid == viewer_id else None,
        )
    pl = margin + grid_px + 16
    draw.rounded_rectangle((pl, margin, width - margin, height - margin), radius=14, fill=(44, 46, 50), outline=(90, 94, 100), width=2)
    small = load_tactical_font(13)
    y = margin + 16
    player = storage.get_character(viewer_id, refresh_energy=False)
    deadline = _parse_deadline(session.match_deadline)
    draw.text((pl + 14, y), f"Захват {session.location_name[:16]}", fill=(220, 220, 220), font=small)
    y += 18
    if deadline:
        secs = max(0, int((deadline - _utc_now()).total_seconds()))
        draw.text((pl + 14, y), f"Осталось: {secs // 60}:{secs % 60:02d}", fill=(200, 200, 120), font=small)
        y += 18
    draw.text((pl + 14, y), f"Врагов: {len(session.hostiles)}", fill=(200, 200, 200), font=small)
    y += 18
    draw.text((pl + 14, y), f"Захват: {session.capture_progress}/{NCAP_CAPTURE_TURNS}", fill=(200, 200, 200), font=small)
    y += 18
    if player:
        weapon = str(player.equipment.get("weapon", "Нож"))
        draw.text((pl + 14, y), f"HP {session.hp.get(str(viewer_id), 0)} · дальн. {weapon_shoot_range(weapon)}", fill=(200, 200, 200), font=small)
        y += 18
    draw.text((pl + 14, y), "Голубой квадрат = вы", fill=(120, 200, 230), font=small)
    y += 18
    if session.log:
        draw.text((pl + 14, y), session.log[-1][:40], fill=(170, 170, 170), font=small)
    draw_enemy_hud(
        canvas,
        hud_slots_from_mixed_kinds(session.hostile_kinds),
        panel_left=pl,
        panel_top=margin,
        panel_right=width - margin,
        panel_bottom=height - margin,
    )
    buf = BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()
