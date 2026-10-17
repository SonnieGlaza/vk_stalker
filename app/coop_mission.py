from __future__ import annotations

import json
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.artifact_hunt import FONT_CANDIDATES, _paste_circle
from app.enemy_hud import draw_enemy_hud, hud_slots_from_kinds
from app.game_logic import (
    RATING_REWARD,
    ActionResult,
    _add_rating,
    _dead_block_text,
    _is_dead,
    apply_incoming_damage,
    equipment_power,
    h,
)
from app.tactical_hp import apply_tactical_medkit_spend, finalize_group_tactical_hp, plan_tactical_medkit, sync_session_hp_to_db
from app.mission_icons import (
    ANOMALY_ICON_KEY,
    MISSION_ICON_GRID_DIAMETER,
    OBJECTIVE_ICON_KEY,
    mission_icon_image,
)
from app.mutant_assets import (
    MISSION_MUTANT_GRID_DIAMETER,
    MUTANT_SPRITE_KEYS,
    MUTANT_SPRITES,
    mutant_grid_diameter,
    mutant_sprite_image,
    pick_mutant_kind,
)
from app.npc_assets import (
    MISSION_NPC_GRID_DIAMETER,
    NPC_SPRITE_KEYS,
    NPC_SPRITES,
    npc_sprite_image,
    pick_npc_kind,
)
from app.quest_mission import LOCATION_DANGER, MOVE_DELTAS, _draw_enemy_icon
from app.tactical_combat import (
    STALE_TURN_MESSAGE,
    best_step_toward,
    collect_player_shot_hits,
    consume_shot_ammo,
    manhattan_distance,
    weapon_damage,
    weapon_shoot_range,
)
from app.storage import Character, Storage

COOP_MAX_PLAYERS = 3
COOP_GRID_SIZE = 6
COOP_TURN_SECONDS = 10
COOP_ENERGY_COST = 14

# Типы кооп-миссий (выбор лидером группы до старта).
COOP_MISSION_TYPES: dict[str, dict[str, str]] = {
    "collect": {
        "title": "Сбор образцов",
        "desc": "Соберите отмеченные цели на поле и выживите.",
    },
    "scout": {
        "title": "Разведка",
        "desc": "Одна точка разведки — отметьте её и держитесь вместе.",
    },
    "loot": {
        "title": "Поиск хабара",
        "desc": "Несколько тайников на карте — соберите все.",
    },
    "clear_mutant": {
        "title": "Зачистка мутантов",
        "desc": "Уничтожьте всех мутантов на поле.",
    },
    "clear_marauder": {
        "title": "Зачистка мародёров",
        "desc": "Уничтожьте всех НПС-мародёров на поле.",
    },
}

LOBBY_PREFIX = "coop:lobby:"
SESSION_PREFIX = "coop:session:"
PLAYER_PREFIX = "coop:player:"
OPEN_LOBBIES_KEY = "coop:open_lobbies"
ACTIVE_SESSIONS_KEY = "coop:active_ids"

PLAYER_COLORS = [
    (80, 200, 255),
    (255, 180, 70),
    (120, 255, 140),
]

# Яркий контрастный квадрат вокруг клетки зрителя ("ты тут").
VIEWER_SQUARE_COLOR = (80, 230, 255)

# Болтовня по рации — сталкерский сленг, изредка попадает в лог хода.
RADIO_LINES: list[str] = [
    "«Альфа, приём. Как слышно?»",
    "«{name}, приём, ты как там?»",
    "«Слева тварь, глаз не спускай!»",
    "«Держим строй, не растягиваемся!»",
    "«Аптечку бы… у кого есть?»",
    "«{name}, прикрой, захожу справа.»",
    "«Тихо… кажется, что-то шевельнулось.»",
    "«На связи. Двигаюсь к цели.»",
    "«Заряд бодрости бы не помешал.»",
    "«Внимание, аномалия впереди!»",
    "«{name}, не отставай, работаем вместе.»",
    "«Приём, база, тут жарко.»",
]


def _parse_kind_list(
    positions_raw: list,
    kinds_raw: Any,
    fallback_keys: tuple[str, ...],
    *,
    valid: dict[str, str] | None = None,
) -> list[str]:
    n = len(positions_raw)
    if not n:
        return []
    if isinstance(kinds_raw, list) and len(kinds_raw) == n:
        parsed: list[str] = []
        for i, k in enumerate(kinds_raw):
            key = str(k)
            if valid is not None and key not in valid:
                key = fallback_keys[i % len(fallback_keys)]
            parsed.append(key)
        return parsed
    return [fallback_keys[i % len(fallback_keys)] for i in range(n)]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _deadline_iso(seconds: int = COOP_TURN_SECONDS) -> str:
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
class CoopLobby:
    lobby_id: str
    host_id: int
    member_ids: list[int]
    location: str
    mission_kind: str = "collect"

    def to_dict(self) -> dict[str, Any]:
        return {
            "lobby_id": self.lobby_id,
            "host_id": self.host_id,
            "member_ids": list(self.member_ids),
            "location": self.location,
            "mission_kind": self.mission_kind,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CoopLobby:
        kind = str(raw.get("mission_kind") or "collect")
        if kind not in COOP_MISSION_TYPES:
            kind = "collect"
        return cls(
            lobby_id=str(raw.get("lobby_id") or ""),
            host_id=int(raw.get("host_id") or 0),
            member_ids=[int(x) for x in (raw.get("member_ids") or [])],
            location=str(raw.get("location") or "Кордон"),
            mission_kind=kind,
        )


@dataclass
class CoopMissionSession:
    session_id: str
    lobby_id: str
    location: str
    player_ids: list[int]
    positions: dict[str, list[int]] = field(default_factory=dict)
    hp: dict[str, int] = field(default_factory=dict)
    medkits_used: dict[str, bool] = field(default_factory=dict)
    start: tuple[int, int] = (0, 0)
    objectives: list[tuple[int, int]] = field(default_factory=list)
    collected: list[tuple[int, int]] = field(default_factory=list)
    hazards: list[tuple[int, int]] = field(default_factory=list)
    enemies: list[tuple[int, int]] = field(default_factory=list)
    enemy_kinds: list[str] = field(default_factory=list)
    enemy_hp: list[int] = field(default_factory=list)
    npcs: list[tuple[int, int]] = field(default_factory=list)
    npc_kinds: list[str] = field(default_factory=list)
    npc_hp: list[int] = field(default_factory=list)
    death_causes: dict[str, str] = field(default_factory=dict)
    death_killers: dict[str, str] = field(default_factory=dict)
    carrying: dict[str, str] = field(default_factory=dict)
    evacuated: list[int] = field(default_factory=list)
    turn_order: list[int] = field(default_factory=list)
    active_index: int = 0
    turn_seq: int = 0
    turn_deadline: str | None = None
    finished: bool = False
    success: bool = False
    log: list[str] = field(default_factory=list)
    message_ids: dict[str, int] = field(default_factory=dict)
    grid: int = COOP_GRID_SIZE
    mission_kind: str = "collect"

    def active_player(self) -> int:
        from app.tactical_roster import resolve_active_player

        return resolve_active_player(self, check_evacuated=True)

    def pos(self, player_id: int) -> tuple[int, int]:
        raw = self.positions.get(str(player_id), [0, 0])
        return int(raw[0]), int(raw[1])

    def set_pos(self, player_id: int, pos: tuple[int, int]) -> None:
        self.positions[str(player_id)] = [pos[0], pos[1]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "lobby_id": self.lobby_id,
            "location": self.location,
            "player_ids": self.player_ids,
            "positions": self.positions,
            "hp": self.hp,
            "medkits_used": self.medkits_used,
            "start": list(self.start),
            "objectives": [list(p) for p in self.objectives],
            "collected": [list(p) for p in self.collected],
            "hazards": [list(p) for p in self.hazards],
            "enemies": [list(p) for p in self.enemies],
            "enemy_kinds": list(self.enemy_kinds),
            "enemy_hp": list(self.enemy_hp),
            "npcs": [list(p) for p in self.npcs],
            "npc_kinds": list(self.npc_kinds),
            "npc_hp": list(self.npc_hp),
            "death_causes": dict(self.death_causes),
            "death_killers": dict(self.death_killers),
            "carrying": dict(self.carrying),
            "evacuated": list(self.evacuated),
            "turn_order": self.turn_order,
            "active_index": self.active_index,
            "turn_seq": self.turn_seq,
            "turn_deadline": self.turn_deadline,
            "finished": self.finished,
            "success": self.success,
            "log": self.log[-14:],
            "message_ids": {str(k): int(v) for k, v in self.message_ids.items()},
            "grid": self.grid,
            "mission_kind": self.mission_kind,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CoopMissionSession:
        kind = str(raw.get("mission_kind") or "collect")
        if kind not in COOP_MISSION_TYPES:
            kind = "collect"
        return cls(
            session_id=str(raw.get("session_id") or ""),
            lobby_id=str(raw.get("lobby_id") or ""),
            location=str(raw.get("location") or "Кордон"),
            player_ids=[int(x) for x in (raw.get("player_ids") or [])],
            positions={str(k): list(v) for k, v in (raw.get("positions") or {}).items()},
            hp={str(k): int(v) for k, v in (raw.get("hp") or {}).items()},
            medkits_used={str(k): bool(v) for k, v in (raw.get("medkits_used") or {}).items()},
            start=(int(raw["start"][0]), int(raw["start"][1])) if raw.get("start") else (0, 0),
            objectives=[(int(p[0]), int(p[1])) for p in (raw.get("objectives") or [])],
            collected=[(int(p[0]), int(p[1])) for p in (raw.get("collected") or [])],
            hazards=[(int(p[0]), int(p[1])) for p in (raw.get("hazards") or [])],
            enemies=[(int(p[0]), int(p[1])) for p in (raw.get("enemies") or [])],
            enemy_kinds=_parse_kind_list(
                raw.get("enemies") or [], raw.get("enemy_kinds"), MUTANT_SPRITE_KEYS, valid=MUTANT_SPRITES
            ),
            enemy_hp=[int(x) for x in (raw.get("enemy_hp") or [])],
            npcs=[(int(p[0]), int(p[1])) for p in (raw.get("npcs") or [])],
            npc_kinds=_parse_kind_list(raw.get("npcs") or [], raw.get("npc_kinds"), NPC_SPRITE_KEYS, valid=NPC_SPRITES),
            npc_hp=[int(x) for x in (raw.get("npc_hp") or [])],
            death_causes={str(k): str(v) for k, v in (raw.get("death_causes") or {}).items()},
            death_killers={str(k): str(v) for k, v in (raw.get("death_killers") or {}).items()},
            carrying={str(k): str(v) for k, v in (raw.get("carrying") or {}).items()},
            evacuated=[int(x) for x in (raw.get("evacuated") or [])],
            turn_order=[int(x) for x in (raw.get("turn_order") or [])],
            active_index=int(raw.get("active_index") or 0),
            turn_seq=int(raw.get("turn_seq") or 0),
            turn_deadline=raw.get("turn_deadline"),
            finished=bool(raw.get("finished")),
            success=bool(raw.get("success")),
            log=[str(x) for x in (raw.get("log") or [])],
            message_ids={str(k): int(v) for k, v in (raw.get("message_ids") or {}).items()},
            grid=int(raw.get("grid") or COOP_GRID_SIZE),
            mission_kind=kind,
        )


def _lobby_key(lobby_id: str) -> str:
    return f"{LOBBY_PREFIX}{lobby_id}"


def _session_key(session_id: str) -> str:
    return f"{SESSION_PREFIX}{session_id}"


def _player_key(telegram_id: int) -> str:
    return f"{PLAYER_PREFIX}{int(telegram_id)}"


def get_player_coop_ref(storage: Storage, telegram_id: int) -> tuple[str, str] | None:
    return _parse_player_ref(storage.get_meta(_player_key(telegram_id)))


def get_coop_lobby(storage: Storage, lobby_id: str) -> CoopLobby | None:
    raw = storage.get_meta(_lobby_key(lobby_id))
    if not raw:
        return None
    try:
        return CoopLobby.from_dict(json.loads(raw))
    except Exception:
        return None


def get_coop_session_by_player(storage: Storage, telegram_id: int) -> CoopMissionSession | None:
    ref = get_player_coop_ref(storage, telegram_id)
    if ref is None or ref[0] != "session":
        return None
    raw = storage.get_meta(_session_key(ref[1]))
    if not raw:
        storage.delete_meta(_player_key(telegram_id))
        return None
    try:
        session = CoopMissionSession.from_dict(json.loads(raw))
    except Exception:
        storage.delete_meta(_player_key(telegram_id))
        return None
    if session.finished:
        return None
    return session


def get_coop_lobby_by_player(storage: Storage, telegram_id: int) -> CoopLobby | None:
    ref = get_player_coop_ref(storage, telegram_id)
    if ref is None or ref[0] != "lobby":
        return None
    return get_coop_lobby(storage, ref[1])


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


def save_coop_lobby(storage: Storage, lobby: CoopLobby) -> None:
    storage.set_meta(_lobby_key(lobby.lobby_id), json.dumps(lobby.to_dict(), ensure_ascii=False))
    for pid in lobby.member_ids:
        storage.set_meta(_player_key(pid), _player_ref("lobby", lobby.lobby_id))


def clear_coop_lobby(storage: Storage, lobby: CoopLobby) -> None:
    storage.delete_meta(_lobby_key(lobby.lobby_id))
    _unregister_open_lobby(storage, lobby.lobby_id)
    for pid in lobby.member_ids:
        ref = get_player_coop_ref(storage, pid)
        if ref and ref[0] == "lobby" and ref[1] == lobby.lobby_id:
            storage.delete_meta(_player_key(pid))


def save_coop_session(storage: Storage, session: CoopMissionSession) -> None:
    storage.set_meta(_session_key(session.session_id), json.dumps(session.to_dict(), ensure_ascii=False))
    for pid in session.player_ids:
        storage.set_meta(_player_key(pid), _player_ref("session", session.session_id))


def clear_coop_session(storage: Storage, session: CoopMissionSession) -> None:
    storage.delete_meta(_session_key(session.session_id))
    unregister_active_coop(storage, session.session_id)
    for pid in session.player_ids:
        ref = get_player_coop_ref(storage, pid)
        if ref and ref[0] == "session" and ref[1] == session.session_id:
            storage.delete_meta(_player_key(pid))


def unlink_player_from_coop_session(storage: Storage, telegram_id: int) -> None:
    """Отвязать игрока от кооп-сессии без уничтожения боя для остальных."""
    from app.tactical_roster import drop_player_from_tactical_roster

    session = get_coop_session_by_player(storage, telegram_id)
    if session is None:
        return
    drop_player_from_tactical_roster(session, telegram_id)
    storage.delete_meta(_player_key(telegram_id))
    if not session.player_ids:
        clear_coop_session(storage, session)
        return
    save_coop_session(storage, session)


def eject_player_from_coop_lobby(storage: Storage, telegram_id: int) -> None:
    """Убрать игрока из кооп-лобби без UI-уведомлений (смерть / fixme)."""
    lobby = get_coop_lobby_by_player(storage, telegram_id)
    if lobby is None:
        return
    if telegram_id in lobby.member_ids:
        lobby.member_ids = [x for x in lobby.member_ids if x != telegram_id]
    storage.delete_meta(_player_key(telegram_id))
    if not lobby.member_ids:
        clear_coop_lobby(storage, lobby)
        return
    if lobby.host_id == telegram_id:
        lobby.host_id = lobby.member_ids[0]
    save_coop_lobby(storage, lobby)


def register_active_coop(storage: Storage, session_id: str) -> None:
    raw = storage.get_meta(ACTIVE_SESSIONS_KEY)
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
    storage.set_meta(ACTIVE_SESSIONS_KEY, json.dumps(ids, ensure_ascii=False))


def unregister_active_coop(storage: Storage, session_id: str) -> None:
    raw = storage.get_meta(ACTIVE_SESSIONS_KEY)
    if not raw:
        return
    try:
        ids = [str(x) for x in json.loads(raw) if str(x) != session_id]
    except json.JSONDecodeError:
        ids = []
    storage.set_meta(ACTIVE_SESSIONS_KEY, json.dumps(ids, ensure_ascii=False))


def list_open_coop_lobbies(storage: Storage, location: str) -> list[tuple[str, str, int]]:
    raw = storage.get_meta(OPEN_LOBBIES_KEY)
    if not raw:
        return []
    try:
        ids = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(ids, list):
        return []
    result: list[tuple[str, str, int]] = []
    stale: list[str] = []
    for lobby_id in ids:
        lobby = get_coop_lobby(storage, str(lobby_id))
        if lobby is None:
            stale.append(str(lobby_id))
            continue
        if lobby.location != location:
            continue
        host = storage.get_character(lobby.host_id, refresh_energy=False)
        host_name = host.nickname if host else str(lobby.host_id)
        result.append((lobby.lobby_id, host_name, len(lobby.member_ids)))
    if stale:
        storage.set_meta(
            OPEN_LOBBIES_KEY,
            json.dumps([str(x) for x in ids if str(x) not in stale], ensure_ascii=False),
        )
    return result


def coop_menu_text(storage: Storage, telegram_id: int) -> str:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return "Сначала создай персонажа."
    session = get_coop_session_by_player(storage, telegram_id)
    if session is not None:
        active = storage.get_character(session.active_player(), refresh_energy=False)
        active_name = active.nickname if active else str(session.active_player())
        return (
            f"👥 Кооп-вылазка на «{session.location}».\n"
            f"Игроков: {len(session.player_ids)}/{COOP_MAX_PLAYERS}.\n"
            f"Ход: {h(active_name)} ({COOP_TURN_SECONDS} сек).\n"
            "Соберите цели и выживите — мутанты и НПС-мародёры идут к ближайшему живому сталкеру.\n"
            "На поле — ваши аватары со скинами, ваша клетка обведена ярким квадратом.\n"
            "📻 В эфире изредка звучит болтовня по рации — держите связь.\n"
            "🦺 Раненого напарника (0 HP) можно эвакуировать: подойди вплотную и тащи его на точку старта.\n"
            "💊 У каждого бойца 1 аптечка из инвентаря на вылазку."
        )
    lobby = get_coop_lobby_by_player(storage, telegram_id)
    if lobby is not None:
        names = []
        for pid in lobby.member_ids:
            ch = storage.get_character(pid, refresh_energy=False)
            names.append(h(ch.nickname) if ch else str(pid))
        host_mark = " (лидер)" if lobby.host_id == telegram_id else ""
        mission = COOP_MISSION_TYPES.get(lobby.mission_kind, COOP_MISSION_TYPES["collect"])
        return (
            f"👥 Группа кооп-вылазки на «{lobby.location}»{host_mark}.\n"
            f"Миссия: {mission['title']} — {mission['desc']}\n"
            f"Участники ({len(lobby.member_ids)}/{COOP_MAX_PLAYERS}): {', '.join(names)}.\n"
            f"Энергия при старте: {COOP_ENERGY_COST} у каждого.\n"
            "Лидер выбирает тип миссии и запускает вылазку, когда все готовы."
        )
    return (
        f"👥 Кооп-вылазка (до {COOP_MAX_PLAYERS} игроков).\n"
        f"Локация: «{player.location}».\n"
        f"Пошаговое поле: ход {COOP_TURN_SECONDS} сек, аномалии, мутанты и НПС-мародёры.\n"
        f"Типы миссий: сбор, разведка, хабар, зачистка мутантов/мародёров.\n"
        f"Стоимость: {COOP_ENERGY_COST} энергии.\n"
        "Игроки на поле — со своими скинами, погибшие не получают награду.\n"
        "У каждого бойца 1 аптечка из инвентаря; раненого напарника (0 HP) эвакуируют на старт.\n"
        "Создай группу или присоединись к открытой."
    )


def set_coop_lobby_mission(storage: Storage, host_id: int, mission_kind: str) -> ActionResult:
    lobby = get_coop_lobby_by_player(storage, host_id)
    if lobby is None:
        return ActionResult(False, "Кооп-группа не найдена.")
    if lobby.host_id != host_id:
        return ActionResult(False, "Тип миссии выбирает только лидер группы.")
    if mission_kind not in COOP_MISSION_TYPES:
        return ActionResult(False, "Неизвестный тип миссии.")
    lobby.mission_kind = mission_kind
    save_coop_lobby(storage, lobby)
    info = COOP_MISSION_TYPES[mission_kind]
    return ActionResult(True, f"Миссия группы: {info['title']}.\n{info['desc']}")


def create_coop_lobby(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    from app.player_busy import player_busy_reason

    busy = player_busy_reason(storage, telegram_id, skip="coop")
    if busy:
        return ActionResult(False, busy)
    if get_coop_session_by_player(storage, telegram_id) or get_coop_lobby_by_player(storage, telegram_id):
        return ActionResult(False, "Ты уже в кооп-группе или вылазке.")
    if player.energy < COOP_ENERGY_COST:
        return ActionResult(False, f"Нужно минимум {COOP_ENERGY_COST} энергии.")

    lobby_id = uuid.uuid4().hex[:10]
    lobby = CoopLobby(
        lobby_id=lobby_id,
        host_id=telegram_id,
        member_ids=[telegram_id],
        location=player.location,
        mission_kind="collect",
    )
    save_coop_lobby(storage, lobby)
    _register_open_lobby(storage, lobby_id)
    return ActionResult(
        True,
        f"Группа создана на «{player.location}». Жди напарников (до {COOP_MAX_PLAYERS}).",
        payload={"coop_lobby_id": lobby_id},
    )


def join_coop_lobby(storage: Storage, telegram_id: int, lobby_id: str) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if get_coop_session_by_player(storage, telegram_id) or get_coop_lobby_by_player(storage, telegram_id):
        return ActionResult(False, "Сначала выйди из текущей группы.")
    from app.player_busy import player_busy_reason

    busy = player_busy_reason(storage, telegram_id, skip="coop")
    if busy:
        return ActionResult(False, busy)
    lobby = get_coop_lobby(storage, lobby_id)
    if lobby is None:
        return ActionResult(False, "Группа не найдена.")
    if player.location != lobby.location:
        return ActionResult(False, f"Нужно быть на «{lobby.location}».")
    if telegram_id in lobby.member_ids:
        return ActionResult(True, "Ты уже в этой группе.")
    if len(lobby.member_ids) >= COOP_MAX_PLAYERS:
        return ActionResult(False, "Группа заполнена.")
    if player.energy < COOP_ENERGY_COST:
        return ActionResult(False, f"Нужно минимум {COOP_ENERGY_COST} энергии.")
    lobby.member_ids.append(telegram_id)
    save_coop_lobby(storage, lobby)
    notify: list[list[Any]] = []
    join_note = (
        f"👥 {h(player.nickname)} присоединился к кооп-группе "
        f"({len(lobby.member_ids)}/{COOP_MAX_PLAYERS}) на «{lobby.location}»."
    )
    for pid in lobby.member_ids:
        if pid == telegram_id:
            continue
        notify.append([pid, join_note])
    return ActionResult(
        True,
        f"Ты в группе ({len(lobby.member_ids)}/{COOP_MAX_PLAYERS}).",
        payload={"notify": notify} if notify else None,
    )


def leave_coop_lobby(storage: Storage, telegram_id: int) -> ActionResult:
    lobby = get_coop_lobby_by_player(storage, telegram_id)
    if lobby is None:
        return ActionResult(False, "Ты не в кооп-группе.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    leaving_name = h(player.nickname) if player else str(telegram_id)
    remaining_before = [x for x in lobby.member_ids if x != telegram_id]
    if telegram_id in lobby.member_ids:
        lobby.member_ids = [x for x in lobby.member_ids if x != telegram_id]
    storage.delete_meta(_player_key(telegram_id))
    if not lobby.member_ids:
        clear_coop_lobby(storage, lobby)
        return ActionResult(True, "Группа распущена.")
    if lobby.host_id == telegram_id:
        lobby.host_id = lobby.member_ids[0]
    save_coop_lobby(storage, lobby)
    notify: list[list[Any]] = []
    leave_note = (
        f"👥 {leaving_name} вышел из кооп-группы "
        f"({len(lobby.member_ids)}/{COOP_MAX_PLAYERS}) на «{lobby.location}»."
    )
    for pid in remaining_before:
        notify.append([pid, leave_note])
    return ActionResult(
        True,
        "Ты вышел из группы.",
        payload={"notify": notify} if notify else None,
    )


def _free_cell(grid: int, forbidden: set[tuple[int, int]]) -> tuple[int, int]:
    opts = [(x, y) for x in range(grid) for y in range(grid) if (x, y) not in forbidden]
    if not opts:
        raise RuntimeError("coop grid full")
    return random.choice(opts)


def _save_if_turn_ok(storage: Storage, session: CoopMissionSession, expected_seq: int) -> bool:
    from app.tactical_turn import save_turn_if_seq_ok

    return save_turn_if_seq_ok(
        storage,
        meta_key=_session_key(session.session_id),
        session=session,
        from_dict=CoopMissionSession.from_dict,
        save_fn=save_coop_session,
        expected_seq=expected_seq,
    )


def _build_coop_map(session: CoopMissionSession) -> None:
    from app.enemy_hud import default_hp_for_kind
    from app.mutant_assets import ensure_single_controller, mutant_field_warnings

    grid = session.grid
    kind = session.mission_kind if session.mission_kind in COOP_MISSION_TYPES else "collect"
    start = _free_cell(grid, set())
    session.start = start
    forbidden: set[tuple[int, int]] = {start}
    for pid in session.player_ids:
        if pid == session.player_ids[0]:
            session.set_pos(pid, start)
        else:
            cell = _free_cell(grid, forbidden)
            session.set_pos(pid, cell)
            forbidden.add(cell)

    danger = LOCATION_DANGER.get(session.location, 2)
    if kind in {"clear_mutant", "clear_marauder"}:
        pass
    elif kind == "scout":
        cell = _free_cell(grid, forbidden)
        session.objectives.append(cell)
        forbidden.add(cell)
    elif kind == "loot":
        for _ in range(2 + (1 if danger >= 3 else 0)):
            cell = _free_cell(grid, forbidden)
            session.objectives.append(cell)
            forbidden.add(cell)
    else:
        obj_n = 2 + len(session.player_ids)
        for _ in range(obj_n):
            cell = _free_cell(grid, forbidden)
            session.objectives.append(cell)
            forbidden.add(cell)

    for _ in range(2 + danger):
        cell = _free_cell(grid, forbidden)
        session.hazards.append(cell)
        forbidden.add(cell)

    mut_n = 2 + max(1, danger // 2)
    if kind == "clear_mutant":
        mut_n += 2
    elif kind == "clear_marauder":
        mut_n = max(1, mut_n - 1)
    for _ in range(mut_n):
        cell = _free_cell(grid, forbidden)
        session.enemies.append(cell)
        mk = pick_mutant_kind(allow_controller="controller" not in session.enemy_kinds)
        session.enemy_kinds.append(mk)
        session.enemy_hp.append(default_hp_for_kind(mk))
        forbidden.add(cell)
    session.enemy_kinds = ensure_single_controller(session.enemy_kinds)
    session.enemy_hp = [default_hp_for_kind(k) for k in session.enemy_kinds]

    npc_n = 1 if danger < 3 else 2
    if kind == "clear_marauder":
        npc_n += 2
    elif kind == "clear_mutant":
        npc_n = max(0, npc_n - 1)
    for _ in range(npc_n):
        cell = _free_cell(grid, forbidden)
        session.npcs.append(cell)
        nk = pick_npc_kind()
        session.npc_kinds.append(nk)
        session.npc_hp.append(default_hp_for_kind(nk))
        forbidden.add(cell)

    for note in mutant_field_warnings(session.enemy_kinds):
        session.log.append(note)


def _combat_damage(location: str, character: Character) -> int:
    danger = LOCATION_DANGER.get(location, 2)
    raw = random.randint(6 + danger * 4, 12 + danger * 7)
    soak = min(12, equipment_power(character))
    pre = max(4, raw - soak)
    return apply_incoming_damage(pre, character, min_damage=10)


def _hazard_damage(character: Character) -> int:
    return apply_incoming_damage(random.randint(14, 22), character, min_damage=10)


def _occupied(session: CoopMissionSession, *, exclude: int | None = None) -> set[tuple[int, int]]:
    blocked: set[tuple[int, int]] = set(session.hazards)
    blocked.update(session.enemies)
    blocked.update(session.npcs)
    for pid in session.player_ids:
        if exclude is not None and pid == exclude:
            continue
        blocked.add(session.pos(pid))
    return blocked


def _advance_turn(session: CoopMissionSession) -> None:
    n = len(session.turn_order)
    for _ in range(n):
        session.active_index = (session.active_index + 1) % n
        pid = session.turn_order[session.active_index]
        if session.hp.get(str(pid), 0) > 0 and pid not in session.evacuated:
            break
    session.turn_seq += 1
    session.turn_deadline = _deadline_iso()


def _move_hostile_group(
    session: CoopMissionSession, units: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Двигает мутантов/НПС к ближайшему живому неивакуированному игроку."""
    alive_cells = [
        session.pos(pid)
        for pid in session.player_ids
        if session.hp.get(str(pid), 0) > 0 and pid not in session.evacuated
    ]
    if not alive_cells:
        return list(units)
    player_cells = set(alive_cells)
    occupied = _occupied(session)
    new_positions: list[tuple[int, int]] = []
    for pos in units:
        origin = pos
        occupied.discard(origin)
        target = min(player_cells, key=lambda p: manhattan_distance(origin, p))
        step = best_step_toward(
            origin,
            target,
            grid=session.grid,
            blocked=occupied,
            forbidden=player_cells,
        )
        new_positions.append(step)
        occupied.add(step)
    return new_positions


def _move_enemies(session: CoopMissionSession) -> None:
    session.enemies = _move_hostile_group(session, session.enemies)


def _move_npcs(session: CoopMissionSession) -> None:
    session.npcs = _move_hostile_group(session, session.npcs)



def _run_hostile_phase(session: CoopMissionSession, storage: Storage) -> None:
    """Ход мутантов/НПС + пассив контролёра."""
    from app.mutant_assets import apply_controller_aura_to_hp_map

    _move_enemies(session)
    _move_npcs(session)
    session.log.extend(_enemy_attacks(session, storage))
    session.log.extend(_npc_attacks(session, storage))
    session.log.extend(
        apply_controller_aura_to_hp_map(
            session.hp,
            session.player_ids,
            session.enemy_kinds,
            death_causes=session.death_causes,
            death_killers=session.death_killers,
        )
    )


def _hostile_attacks(
    session: CoopMissionSession,
    storage: Storage,
    positions: list[tuple[int, int]],
    kinds: list[str],
    label: str,
    cause: str,
    *,
    npc: bool,
) -> list[str]:
    from app.death_flavor import killer_label_for_kind

    notes: list[str] = []
    for i, epos in enumerate(positions):
        kind = kinds[i] if i < len(kinds) else ""
        killer_name = killer_label_for_kind(kind, npc=npc) if kind else label
        for pid in session.player_ids:
            if session.hp.get(str(pid), 0) <= 0:
                continue
            if pid in session.evacuated:
                continue
            if manhattan_distance(session.pos(pid), epos) != 1:
                continue
            player = storage.get_character(pid, refresh_energy=False)
            if player is None:
                continue
            dmg = _combat_damage(session.location, player)
            new_hp = max(0, session.hp.get(str(pid), 0) - dmg)
            session.hp[str(pid)] = new_hp
            if new_hp <= 0:
                session.death_causes[str(pid)] = cause
                session.death_killers[str(pid)] = killer_name
                # Если нёс раненого — тело падает, другой может поднять.
                session.carrying.pop(str(pid), None)
            notes.append(f"{killer_name} ранит {h(player.nickname)}: −{dmg} HP.")
    return notes


def _enemy_attacks(session: CoopMissionSession, storage: Storage) -> list[str]:
    return _hostile_attacks(session, storage, session.enemies, session.enemy_kinds, "Мутант", "mutant", npc=False)


def _npc_attacks(session: CoopMissionSession, storage: Storage) -> list[str]:
    return _hostile_attacks(session, storage, session.npcs, session.npc_kinds, "Мародёр", "npc", npc=True)


def _maybe_radio_chatter(session: CoopMissionSession, storage: Storage) -> None:
    """0–1 короткая фраза по рации в лог хода — просто атмосфера."""
    if random.random() >= 0.45:
        return
    active = storage.get_character(session.active_player(), refresh_energy=False)
    name = h(active.nickname) if active else "боец"
    line = random.choice(RADIO_LINES).format(name=name)
    session.log.append(f"📻 {line}")


def _objectives_complete(session: CoopMissionSession) -> bool:
    if session.mission_kind == "clear_mutant":
        return len(session.enemies) == 0
    if session.mission_kind == "clear_marauder":
        return len(session.npcs) == 0
    return len(session.collected) >= len(session.objectives) and len(session.objectives) > 0


def _commit_coop_session_deaths(
    storage: Storage,
    session: CoopMissionSession,
) -> tuple[list[int], dict[str, str], dict[str, str]]:
    for pid in session.player_ids:
        key = str(pid)
        if key in session.death_causes:
            session.death_causes[key] = _map_death_cause(session.death_causes.get(key))
    return finalize_group_tactical_hp(
        storage,
        session,
        cause_default="coop",
        commit_field_deaths=True,
    )


def _refund_coop_energy(storage: Storage, player_ids: list[int]) -> None:
    for pid in player_ids:
        storage.restore_energy(pid, COOP_ENERGY_COST)


_COOP_DEATH_CAUSES = {"mutant", "npc", "anomaly"}


def _map_death_cause(raw: str | None) -> str:
    """Причина смерти для remember_death_cause: конкретная, если известна, иначе общий 'coop'."""
    if raw in _COOP_DEATH_CAUSES:
        return raw
    return "coop"


def _reward_players(storage: Storage, session: CoopMissionSession) -> str:
    danger = LOCATION_DANGER.get(session.location, 2)
    base_money = 1000 + danger * 80
    alive_ids = [pid for pid in session.player_ids if session.hp.get(str(pid), 0) > 0]
    per_player = base_money + 40 * max(0, len(alive_ids) - 1)
    lines = [f"✅ Кооп-вылазка на «{session.location}» успешна!"]
    for pid in session.player_ids:
        ch = storage.get_character(pid, refresh_energy=False)
        name = h(ch.nickname) if ch else str(pid)
        if session.hp.get(str(pid), 0) <= 0:
            lines.append(f"{name}: погиб на вылазке — награда не начислена.")
            continue
        storage.change_money(pid, per_player)
        _add_rating(storage, pid, RATING_REWARD.get("quest_success", 12))
        evac_mark = " (эвакуирован напарником)" if pid in session.evacuated else ""
        lines.append(f"{name}{evac_mark}: +{per_player} RU, рейтинг +{RATING_REWARD.get('quest_success', 12)}.")
    return "\n".join(lines)


def _finish_success(storage: Storage, session: CoopMissionSession) -> ActionResult:
    dead_ids, death_causes_payload, death_killers_payload = _commit_coop_session_deaths(
        storage, session
    )
    message_ids = {str(k): int(v) for k, v in session.message_ids.items()}
    session.finished = True
    session.success = True
    text = _reward_players(storage, session)
    session.log.append("Миссия выполнена!")
    player_ids = list(session.player_ids)
    save_coop_session(storage, session)
    clear_coop_session(storage, session)
    return ActionResult(
        True,
        text,
        payload={
            "coop_done": True,
            "coop_success": True,
            "notify_all": player_ids,
            "message_ids": message_ids,
            "session_id": session.session_id,
            "dead_players": dead_ids,
            "death_causes": death_causes_payload,
            "death_killers": death_killers_payload,
            "death_location": session.location,
        },
    )


def _finish_fail(
    storage: Storage,
    session: CoopMissionSession,
    reason: str,
    *,
    refund: bool = False,
) -> ActionResult:
    finalize_group_tactical_hp(
        storage,
        session,
        cause_default="coop",
        commit_field_deaths=False,
    )
    if refund:
        _refund_coop_energy(storage, session.player_ids)
    message_ids = {str(k): int(v) for k, v in session.message_ids.items()}
    session.finished = True
    session.success = False
    session.log.append(reason)
    save_coop_session(storage, session)
    player_ids = list(session.player_ids)
    clear_coop_session(storage, session)
    return ActionResult(
        False,
        reason,
        payload={
            "coop_done": True,
            "coop_success": False,
            "notify_all": player_ids,
            "message_ids": message_ids,
            "session_id": session.session_id,
            "death_location": session.location,
            "death_cause": "coop",
        },
    )


def _check_team_wipe(storage: Storage, session: CoopMissionSession) -> ActionResult | None:
    # Эвакуированные — уже в безопасности, вайп группы их не касается.
    remaining = [pid for pid in session.player_ids if pid not in session.evacuated]
    if not remaining:
        return None
    all_downed = all(session.hp.get(str(pid), 0) <= 0 for pid in remaining)
    if not all_downed:
        return None
    # Вайп всей группы — энергия, потраченная на вылазку, не возвращается.
    return _finish_fail(storage, session, "Вся группа откинулась на вылазке — медики ждут на базе.")


def can_evacuate(session: CoopMissionSession, telegram_id: int) -> bool:
    """Показывать ли кнопку «Эвак»: игрок уже тащит раненого или стоит рядом с ним."""
    if session.hp.get(str(telegram_id), 0) <= 0 or telegram_id in session.evacuated:
        return False
    if str(telegram_id) in session.carrying:
        return True
    pos = session.pos(telegram_id)
    carried_ids = {int(v) for v in session.carrying.values()}
    for pid in session.player_ids:
        if pid == telegram_id or pid in session.evacuated or pid in carried_ids:
            continue
        if session.hp.get(str(pid), 0) > 0:
            continue
        opos = session.pos(pid)
        if abs(opos[0] - pos[0]) + abs(opos[1] - pos[1]) == 1:
            return True
    return False


def start_coop_mission(storage: Storage, host_id: int) -> ActionResult:
    lobby = get_coop_lobby_by_player(storage, host_id)
    if lobby is None:
        return ActionResult(False, "Кооп-группа не найдена.")
    if lobby.host_id != host_id:
        return ActionResult(False, "Запускать может только лидер группы.")
    if len(lobby.member_ids) < 2:
        return ActionResult(False, "Нужно минимум 2 игрока для кооп-вылазки.")

    from app.player_busy import player_busy_reason

    for pid in lobby.member_ids:
        busy = player_busy_reason(storage, pid, skip="coop")
        if busy:
            ch = storage.get_character(pid, refresh_energy=False)
            who = h(ch.nickname) if ch else str(pid)
            return ActionResult(False, f"{who}: {busy.lower()}")

    spent: list[int] = []
    for pid in lobby.member_ids:
        ch = storage.get_character(pid, refresh_energy=False)
        if ch is None or _is_dead(ch):
            return ActionResult(False, "Один из участников недоступен.")
        if ch.energy < COOP_ENERGY_COST:
            return ActionResult(False, f"У {h(ch.nickname)} не хватает энергии ({COOP_ENERGY_COST}).")
    for pid in lobby.member_ids:
        if storage.spend_energy(pid, COOP_ENERGY_COST):
            spent.append(pid)
        else:
            for refund in spent:
                storage.restore_energy(refund, COOP_ENERGY_COST)
            return ActionResult(False, "Не удалось списать энергию.")

    session_id = uuid.uuid4().hex[:12]
    session = CoopMissionSession(
        session_id=session_id,
        lobby_id=lobby.lobby_id,
        location=lobby.location,
        player_ids=list(lobby.member_ids),
        turn_order=list(lobby.member_ids),
        active_index=0,
        turn_deadline=_deadline_iso(),
        mission_kind=lobby.mission_kind if lobby.mission_kind in COOP_MISSION_TYPES else "collect",
    )
    _build_coop_map(session)
    for pid in lobby.member_ids:
        ch = storage.get_character(pid, refresh_energy=False)
        if ch:
            session.hp[str(pid)] = int(ch.health)
            session.medkits_used[str(pid)] = False
    first = storage.get_character(session.active_player(), refresh_energy=False)
    mission = COOP_MISSION_TYPES.get(session.mission_kind, COOP_MISSION_TYPES["collect"])
    session.log.append(
        f"Кооп «{mission['title']}» на «{session.location}»: {len(session.player_ids)} сталкеров. "
        f"Первый ход — {h(first.nickname) if first else session.active_player()}."
    )
    clear_coop_lobby(storage, lobby)
    save_coop_session(storage, session)
    register_active_coop(storage, session_id)
    goal_txt = (
        f"мутантов: {len(session.enemies)}"
        if session.mission_kind == "clear_mutant"
        else (
            f"мародёров: {len(session.npcs)}"
            if session.mission_kind == "clear_marauder"
            else f"целей: {len(session.objectives)}"
        )
    )
    return ActionResult(
        True,
        f"Кооп-вылазка «{mission['title']}» началась! Ход {COOP_TURN_SECONDS} сек. {goal_txt}.",
        payload={"coop_started": True, "session_id": session_id, "notify_all": session.player_ids},
    )


def coop_move(storage: Storage, telegram_id: int, direction: str) -> ActionResult:
    session = get_coop_session_by_player(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Активной кооп-вылазки нет.")
    if session.hp.get(str(telegram_id), 0) <= 0:
        return ActionResult(False, "Ты выбыл.")
    if telegram_id in session.evacuated:
        return ActionResult(False, "Ты уже эвакуирован в безопасное место.")
    if session.active_player() != telegram_id:
        return ActionResult(False, "Сейчас ход другого игрока.")
    turn_seq = session.turn_seq
    delta = MOVE_DELTAS.get(direction)
    if delta is None:
        return ActionResult(False, "Некорректное направление.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Персонаж не найден.")

    pos = session.pos(telegram_id)
    nxt = (pos[0] + delta[0], pos[1] + delta[1])
    if not (0 <= nxt[0] < session.grid and 0 <= nxt[1] < session.grid):
        return ActionResult(False, "Край поля.")
    for other in session.player_ids:
        if other != telegram_id and session.pos(other) == nxt:
            return ActionResult(False, "Клетка занята напарником.")
    from app.death_flavor import encounter_phrase_for_kind, killer_label_for_kind

    moved = False
    if nxt in session.enemies:
        dmg = _combat_damage(session.location, player)
        new_hp = max(0, session.hp.get(str(telegram_id), 0) - dmg)
        session.hp[str(telegram_id)] = new_hp
        idx = session.enemies.index(nxt)
        kind = session.enemy_kinds[idx] if idx < len(session.enemy_kinds) else ""
        if new_hp <= 0:
            session.death_causes[str(telegram_id)] = "mutant"
            session.death_killers[str(telegram_id)] = killer_label_for_kind(kind, npc=False) if kind else "Мутант"
            session.carrying.pop(str(telegram_id), None)
        session.enemies.pop(idx)
        if idx < len(session.enemy_kinds):
            session.enemy_kinds.pop(idx)
        if idx < len(session.enemy_hp):
            session.enemy_hp.pop(idx)
        from app.combat_loot import grant_combat_loot

        loot = grant_combat_loot(storage, telegram_id, npc=False)
        loot_note = f" Лут: {loot}." if loot else ""
        phrase = encounter_phrase_for_kind(kind, npc=False)
        session.log.append(f"{h(player.nickname)} сразился {phrase}: −{dmg} HP.{loot_note}")
        wipe = _check_team_wipe(storage, session)
        if wipe:
            return wipe
    elif nxt in session.npcs:
        dmg = _combat_damage(session.location, player)
        new_hp = max(0, session.hp.get(str(telegram_id), 0) - dmg)
        session.hp[str(telegram_id)] = new_hp
        idx = session.npcs.index(nxt)
        kind = session.npc_kinds[idx] if idx < len(session.npc_kinds) else ""
        if new_hp <= 0:
            session.death_causes[str(telegram_id)] = "npc"
            session.death_killers[str(telegram_id)] = killer_label_for_kind(kind, npc=True) if kind else "Мародёр"
            session.carrying.pop(str(telegram_id), None)
        session.npcs.pop(idx)
        if idx < len(session.npc_kinds):
            session.npc_kinds.pop(idx)
        if idx < len(session.npc_hp):
            session.npc_hp.pop(idx)
        from app.combat_loot import grant_combat_loot

        loot = grant_combat_loot(storage, telegram_id, npc=True)
        loot_note = f" Лут: {loot}." if loot else ""
        phrase = encounter_phrase_for_kind(kind, npc=True)
        session.log.append(f"{h(player.nickname)} сразился {phrase}: −{dmg} HP.{loot_note}")
        wipe = _check_team_wipe(storage, session)
        if wipe:
            return wipe
    else:
        session.set_pos(telegram_id, nxt)
        moved = True
        carried_id_raw = session.carrying.get(str(telegram_id))
        if carried_id_raw is not None:
            session.set_pos(int(carried_id_raw), nxt)

    if moved:
        if nxt in session.objectives and nxt not in session.collected:
            session.collected.append(nxt)
            session.log.append(f"{h(player.nickname)} отметил цель.")

        if nxt in session.hazards:
            dmg = _hazard_damage(player)
            new_hp = max(0, session.hp.get(str(telegram_id), 0) - dmg)
            session.hp[str(telegram_id)] = new_hp
            if new_hp <= 0:
                session.death_causes[str(telegram_id)] = "anomaly"
                session.death_killers[str(telegram_id)] = "Аномалия"
                session.carrying.pop(str(telegram_id), None)
            session.hazards = [haz for haz in session.hazards if haz != nxt]
            session.log.append(f"Аномалия: −{dmg} HP ({h(player.nickname)}).")
            wipe = _check_team_wipe(storage, session)
            if wipe:
                return wipe

        carried_id_raw = session.carrying.get(str(telegram_id))
        if carried_id_raw is not None and nxt == session.start:
            downed_id = int(carried_id_raw)
            session.carrying.pop(str(telegram_id), None)
            session.hp[str(downed_id)] = 1
            if downed_id not in session.evacuated:
                session.evacuated.append(downed_id)
            session.death_causes.pop(str(downed_id), None)
            session.death_killers.pop(str(downed_id), None)
            downed_char = storage.get_character(downed_id, refresh_energy=False)
            downed_name = h(downed_char.nickname) if downed_char else str(downed_id)
            session.log.append(f"🦺 {h(player.nickname)} эвакуировал {downed_name} на точку сбора!")

    if _objectives_complete(session):
        return _finish_success(storage, session)

    _advance_turn(session)
    _run_hostile_phase(session, storage)
    _maybe_radio_chatter(session, storage)
    wipe = _check_team_wipe(storage, session)
    if wipe:
        return wipe

    if not _save_if_turn_ok(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    return ActionResult(True, "Ход сделан.", payload={"coop_active": True, "notify_all": session.player_ids})


def coop_shoot_available(session: CoopMissionSession) -> bool:
    return bool(session.enemies or session.npcs)


def coop_shoot(storage: Storage, telegram_id: int, direction: str) -> ActionResult:
    from app.combat_loot import grant_combat_loot
    from app.death_flavor import killer_label_for_kind

    session = get_coop_session_by_player(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Активной кооп-вылазки нет.")
    if session.active_player() != telegram_id:
        return ActionResult(False, "Сейчас ход напарника.")
    if session.hp.get(str(telegram_id), 0) <= 0:
        return ActionResult(False, "Ты ранен и не можешь стрелять.")
    if direction not in MOVE_DELTAS:
        return ActionResult(False, "Некорректное направление.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    weapon = str(player.equipment.get("weapon", "Нож"))
    shoot_range = weapon_shoot_range(weapon)
    if shoot_range <= 0:
        return ActionResult(False, "Это оружие не стреляет на дистанции.")

    ammo_result = consume_shot_ammo(storage, telegram_id, weapon)
    if ammo_result is not None:
        return ammo_result

    targets: dict[tuple[int, int], str] = {pos: "mutant" for pos in session.enemies}
    targets.update({pos: "npc" for pos in session.npcs})
    hits = collect_player_shot_hits(
        session.pos(telegram_id),
        direction,
        grid=session.grid,
        weapon_name=weapon,
        blockers=set(),
        targets=targets,
        cover=set(),
    )
    turn_seq = session.turn_seq
    dmg = weapon_damage(weapon)
    note = f"{h(player.nickname)} промахнулся ({weapon})."
    if hits:
        for hit_cell, hit_kind in hits:
            if hit_kind == "mutant" and hit_cell in session.enemies:
                idx = session.enemies.index(hit_cell)
                while len(session.enemy_hp) < len(session.enemies):
                    session.enemy_hp.append(12)
                session.enemy_hp[idx] = max(0, int(session.enemy_hp[idx]) - dmg)
                kind = session.enemy_kinds[idx] if idx < len(session.enemy_kinds) else ""
                label = killer_label_for_kind(kind, npc=False) if kind else "мутанта"
                if session.enemy_hp[idx] <= 0:
                    session.enemies.pop(idx)
                    if idx < len(session.enemy_kinds):
                        session.enemy_kinds.pop(idx)
                    session.enemy_hp.pop(idx)
                    loot = grant_combat_loot(storage, telegram_id, npc=False)
                    loot_note = f" Лут: {loot}." if loot else ""
                    note = f"{h(player.nickname)} убил {label} ({weapon}).{loot_note}"
                else:
                    note = f"{h(player.nickname)} попал в {label}: {session.enemy_hp[idx]} HP."
            elif hit_kind == "npc" and hit_cell in session.npcs:
                idx = session.npcs.index(hit_cell)
                while len(session.npc_hp) < len(session.npcs):
                    session.npc_hp.append(14)
                session.npc_hp[idx] = max(0, int(session.npc_hp[idx]) - dmg)
                kind = session.npc_kinds[idx] if idx < len(session.npc_kinds) else ""
                label = killer_label_for_kind(kind, npc=True) if kind else "НПС"
                if session.npc_hp[idx] <= 0:
                    session.npcs.pop(idx)
                    if idx < len(session.npc_kinds):
                        session.npc_kinds.pop(idx)
                    session.npc_hp.pop(idx)
                    loot = grant_combat_loot(storage, telegram_id, npc=True)
                    loot_note = f" Лут: {loot}." if loot else ""
                    note = f"{h(player.nickname)} убил {label} ({weapon}).{loot_note}"
                else:
                    note = f"{h(player.nickname)} попал в {label}: {session.npc_hp[idx]} HP."
    session.log.append(note)

    if _objectives_complete(session):
        return _finish_success(storage, session)
    _advance_turn(session)
    _run_hostile_phase(session, storage)
    _maybe_radio_chatter(session, storage)
    wipe = _check_team_wipe(storage, session)
    if wipe:
        return wipe
    if not _save_if_turn_ok(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    return ActionResult(True, note, payload={"coop_active": True, "notify_all": session.player_ids})


def coop_use_medkit(storage: Storage, telegram_id: int) -> ActionResult:
    session = get_coop_session_by_player(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Активной кооп-вылазки нет.")
    if session.hp.get(str(telegram_id), 0) <= 0:
        return ActionResult(False, "Ты выбыл.")
    if telegram_id in session.evacuated:
        return ActionResult(False, "Ты уже эвакуирован в безопасное место.")
    if session.active_player() != telegram_id:
        return ActionResult(False, "Сейчас ход другого игрока.")
    turn_seq = session.turn_seq
    if session.medkits_used.get(str(telegram_id)):
        return ActionResult(False, "Аптечку в этой вылазке уже использовал.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Персонаж не найден.")
    current_hp = session.hp.get(str(telegram_id), int(player.health))
    result, new_hp, item_key = plan_tactical_medkit(storage, telegram_id, int(current_hp))
    if not result.ok:
        return result
    session.hp[str(telegram_id)] = new_hp
    session.medkits_used[str(telegram_id)] = True
    session.log.append(f"{h(player.nickname)} использовал аптечку.")
    _advance_turn(session)
    _run_hostile_phase(session, storage)
    _maybe_radio_chatter(session, storage)
    wipe = _check_team_wipe(storage, session)
    if wipe:
        if not _save_if_turn_ok(storage, session, turn_seq):
            return ActionResult(False, STALE_TURN_MESSAGE)
        if item_key:
            apply_tactical_medkit_spend(storage, telegram_id, item_key, result)
        return wipe
    if not _save_if_turn_ok(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    if item_key:
        apply_tactical_medkit_spend(storage, telegram_id, item_key, result)
    return ActionResult(True, result.text, payload={"coop_active": True, "notify_all": session.player_ids})


def coop_evacuate(storage: Storage, telegram_id: int) -> ActionResult:
    """Взять на себя эвакуацию раненого напарника с соседней клетки."""
    session = get_coop_session_by_player(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Активной кооп-вылазки нет.")
    if session.hp.get(str(telegram_id), 0) <= 0:
        return ActionResult(False, "Ты выбыл.")
    if telegram_id in session.evacuated:
        return ActionResult(False, "Ты уже эвакуирован в безопасное место.")
    if session.active_player() != telegram_id:
        return ActionResult(False, "Сейчас ход другого игрока.")
    if str(telegram_id) in session.carrying:
        return ActionResult(False, "Ты уже тащишь раненого — веди его на точку старта.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Персонаж не найден.")

    pos = session.pos(telegram_id)
    carried_ids = {int(v) for v in session.carrying.values()}
    downed_id: int | None = None
    for pid in session.player_ids:
        if pid == telegram_id or pid in session.evacuated or pid in carried_ids:
            continue
        if session.hp.get(str(pid), 0) > 0:
            continue
        opos = session.pos(pid)
        if abs(opos[0] - pos[0]) + abs(opos[1] - pos[1]) == 1:
            downed_id = pid
            break
    if downed_id is None:
        return ActionResult(False, "Рядом нет раненых для эвакуации.")

    turn_seq = session.turn_seq
    session.carrying[str(telegram_id)] = str(downed_id)
    session.set_pos(downed_id, pos)
    downed_char = storage.get_character(downed_id, refresh_energy=False)
    downed_name = h(downed_char.nickname) if downed_char else str(downed_id)
    session.log.append(f"🦺 {h(player.nickname)} взвалил {downed_name} на плечи и тащит на точку старта.")
    _advance_turn(session)
    _run_hostile_phase(session, storage)
    _maybe_radio_chatter(session, storage)
    wipe = _check_team_wipe(storage, session)
    if wipe:
        return wipe
    if not _save_if_turn_ok(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    return ActionResult(
        True,
        f"Ты тащишь {downed_name} — доберись до точки старта.",
        payload={"coop_active": True, "notify_all": session.player_ids},
    )


def coop_forfeit(storage: Storage, telegram_id: int) -> ActionResult:
    session = get_coop_session_by_player(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Активной кооп-вылазки нет.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    note = f"{h(player.nickname) if player else telegram_id} свалил с поля."
    # Добровольный выход — в отличие от вайпа, энергию группе возвращаем.
    return _finish_fail(storage, session, note, refund=True)


def process_coop_turn_timeouts(storage: Storage) -> list[tuple[int, ActionResult]]:
    outcomes: list[tuple[int, ActionResult]] = []
    raw = storage.get_meta(ACTIVE_SESSIONS_KEY)
    if not raw:
        return outcomes
    try:
        session_ids = json.loads(raw)
    except json.JSONDecodeError:
        return outcomes
    if not isinstance(session_ids, list):
        return outcomes
    still_active: list[str] = []
    finished: set[str] = set()
    now = _utc_now()
    for session_id in session_ids:
        raw_s = storage.get_meta(_session_key(str(session_id)))
        if not raw_s:
            continue
        try:
            session = CoopMissionSession.from_dict(json.loads(raw_s))
        except Exception:
            continue
        if session.finished:
            continue
        deadline = _parse_deadline(session.turn_deadline)
        if deadline is None or now <= deadline:
            still_active.append(str(session_id))
            continue
        active = session.active_player()
        player = storage.get_character(active, refresh_energy=False)
        turn_seq = session.turn_seq
        session.log.append(f"Тайм-аут хода {h(player.nickname) if player else active}.")
        _advance_turn(session)
        _run_hostile_phase(session, storage)
        _maybe_radio_chatter(session, storage)
        wipe = _check_team_wipe(storage, session)
        if wipe:
            if str(session_id) not in finished:
                finished.add(str(session_id))
                outcomes.append((active, wipe))
            continue
        if not _save_if_turn_ok(storage, session, turn_seq):
            still_active.append(str(session_id))
            continue
        still_active.append(str(session_id))
        outcomes.append(
            (
                active,
                ActionResult(
                    True,
                    "Время вышло — ход пропущен.",
                    payload={"coop_active": True, "notify_all": session.player_ids},
                ),
            )
        )
    storage.set_meta(ACTIVE_SESSIONS_KEY, json.dumps(still_active, ensure_ascii=False))
    return outcomes


def coop_status_caption(session: CoopMissionSession, storage: Storage, viewer_id: int) -> str:
    from app.tactical_roster import format_player_name

    active_pid = session.active_player()
    active_name = format_player_name(storage, active_pid, html=True)
    mission = COOP_MISSION_TYPES.get(session.mission_kind, COOP_MISSION_TYPES["collect"])
    if session.mission_kind == "clear_mutant":
        goal_line = f"Зачистка мутантов: осталось {len(session.enemies)}"
    elif session.mission_kind == "clear_marauder":
        goal_line = f"Зачистка мародёров: осталось {len(session.npcs)}"
    else:
        left_obj = len(session.objectives) - len(session.collected)
        goal_line = f"Цели: {len(session.collected)}/{len(session.objectives)} (осталось {left_obj})"
    lines = [
        f"👥 Кооп · {mission['title']} · «{session.location}»",
        f"Ход: {h(active_name)} ({COOP_TURN_SECONDS} сек)",
        goal_line,
        f"Мутанты: {len(session.enemies)} · НПС: {len(session.npcs)} · Аномалии: {len(session.hazards)}",
    ]
    if session.enemy_hp:
        bits = "/".join(str(hp) for hp in session.enemy_hp[:6])
        lines.append(f"HP мутантов: {bits}")
    if session.npc_hp:
        bits = "/".join(str(hp) for hp in session.npc_hp[:6])
        lines.append(f"HP НПС: {bits}")
    for i, pid in enumerate(session.player_ids):
        ch = storage.get_character(pid, refresh_energy=False)
        name = ch.nickname if ch else str(pid)
        hp = session.hp.get(str(pid), 0)
        if pid in session.evacuated:
            mark = " 🦺 эвакуирован"
        elif hp <= 0:
            mark = " ☠ ранен, ждёт эвакуации"
        else:
            mark = " ◀" if pid == active_pid else ""
            if str(pid) in session.carrying:
                mark += " 🦺 несёт напарника"
        if pid == viewer_id:
            mark += " (ты)"
        lines.append(f"{h(name)}{mark}: HP {hp}")
    lines.append("🔷 синяя клетка = вы")
    return "\n".join(lines)


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _coop_rating_points(storage: Storage, telegram_id: int) -> int:
    try:
        return int(storage.get_player_stats(telegram_id).get("rating_points", 0))
    except Exception:
        return 0


def _dim_dead_token(token: Image.Image) -> Image.Image:
    """Ч/б и затемнённая версия аватара для погибшего игрока (крест рисуется отдельно)."""
    token = token.convert("RGBA")
    gray = ImageOps.grayscale(token).point(lambda p: int(p * 0.45))
    alpha = token.split()[3]
    return Image.merge("RGBA", (gray, gray, gray, alpha))


def render_coop_frame(storage: Storage, session: CoopMissionSession, viewer_id: int) -> bytes:
    cell = 88
    grid = session.grid
    grid_px = grid * cell
    margin = 20
    panel_w = 300
    width = margin + grid_px + 16 + panel_w + margin
    height = max(margin + grid_px + margin, 680)
    canvas = Image.new("RGBA", (width, height), (18, 20, 22, 255))
    draw = ImageDraw.Draw(canvas)
    from app.artifact_hunt import _load_location_thumb, _cover_crop

    thumb = _load_location_thumb(session.location)
    if thumb is not None:
        field = _cover_crop(thumb, grid_px, grid_px).convert("RGBA")
        field.putalpha(150)
        canvas.paste(field, (margin, margin), field)
    for gy in range(grid):
        for gx in range(grid):
            left = margin + gx * cell
            top = margin + gy * cell
            if thumb is None:
                tone = 62 + ((gx * 17 + gy * 23) % 18)
                draw.rectangle((left, top, left + cell - 1, top + cell - 1), fill=(tone, tone - 4, tone - 8))
            else:
                overlay = Image.new("RGBA", (cell, cell), (10, 12, 14, 50))
                canvas.alpha_composite(overlay, (left, top))
            draw.rectangle((left, top, left + cell - 1, top + cell - 1), outline=(28, 30, 34), width=1)

    sx, sy = session.start
    sl = margin + sx * cell
    st = margin + sy * cell
    draw.rectangle((sl + 3, st + 3, sl + cell - 4, st + cell - 4), outline=(80, 200, 90), width=3)

    for hx, hy in session.hazards:
        cx = margin + hx * cell + cell // 2
        cy = margin + hy * cell + cell // 2
        sprite = mission_icon_image(ANOMALY_ICON_KEY)
        if sprite is not None:
            diam = MISSION_ICON_GRID_DIAMETER
            token = sprite.convert("RGBA").resize((diam, diam), Image.Resampling.LANCZOS)
            mask = Image.new("L", (diam, diam), 0)
            ImageDraw.Draw(mask).ellipse((1, 1, diam - 2, diam - 2), fill=255)
            canvas.paste(token, (cx - diam // 2, cy - diam // 2), mask)
        else:
            draw.ellipse((cx - 20, cy - 20, cx + 20, cy + 20), fill=(200, 100, 40))

    for ox, oy in session.objectives:
        if (ox, oy) in session.collected:
            continue
        cx = margin + ox * cell + cell // 2
        cy = margin + oy * cell + cell // 2
        sprite = mission_icon_image(OBJECTIVE_ICON_KEY)
        if sprite is not None:
            _paste_circle(canvas, sprite, cx, cy, MISSION_ICON_GRID_DIAMETER, ring_color=(80, 200, 90), ring_width=3)

    enemy_ring = (210, 55, 45)
    for i, (ex, ey) in enumerate(session.enemies):
        cx = margin + ex * cell + cell // 2
        cy = margin + ey * cell + cell // 2
        kind = session.enemy_kinds[i] if i < len(session.enemy_kinds) else MUTANT_SPRITE_KEYS[i % len(MUTANT_SPRITE_KEYS)]
        sprite = mutant_sprite_image(kind)
        enemy_diameter = mutant_grid_diameter(kind, default=MISSION_MUTANT_GRID_DIAMETER)
        ring_w = 4 if kind == "giant" else 3
        if sprite is not None:
            _paste_circle(canvas, sprite, cx, cy, enemy_diameter, ring_color=enemy_ring, ring_width=ring_w)
        else:
            _draw_enemy_icon(draw, cx, cy, marauder=False)
        hp_val = session.enemy_hp[i] if i < len(session.enemy_hp) else None
        if hp_val is not None:
            draw.text((cx, cy - 28), str(hp_val), fill=(255, 210, 210), font=_load_font(13), anchor="mm")

    npc_ring = (210, 55, 45)
    for i, (nx_, ny_) in enumerate(session.npcs):
        cx = margin + nx_ * cell + cell // 2
        cy = margin + ny_ * cell + cell // 2
        kind = session.npc_kinds[i] if i < len(session.npc_kinds) else NPC_SPRITE_KEYS[i % len(NPC_SPRITE_KEYS)]
        sprite = npc_sprite_image(kind)
        if sprite is not None:
            _paste_circle(canvas, sprite, cx, cy, MISSION_NPC_GRID_DIAMETER, ring_color=npc_ring, ring_width=3)
        else:
            _draw_enemy_icon(draw, cx, cy, marauder=True)
        hp_val = session.npc_hp[i] if i < len(session.npc_hp) else None
        if hp_val is not None:
            draw.text((cx, cy - 28), str(hp_val), fill=(255, 210, 210), font=_load_font(13), anchor="mm")

    for i, pid in enumerate(session.player_ids):
        px, py = session.pos(pid)
        cx = margin + px * cell + cell // 2
        cy = margin + py * cell + cell // 2
        ring = PLAYER_COLORS[i % len(PLAYER_COLORS)]
        is_dead = session.hp.get(str(pid), 0) <= 0
        if pid == session.active_player() and not is_dead:
            draw.ellipse((cx - 32, cy - 32, cx + 32, cy + 32), outline=(255, 230, 80), width=3)

        character = storage.get_character(pid, refresh_energy=False)
        token: Image.Image | None = None
        if character is not None:
            try:
                from app.avatar_render import render_avatar

                token = render_avatar(character, rating_points=_coop_rating_points(storage, pid), width=140, height=140)
            except Exception:
                token = None
        if token is None:
            token = Image.new("RGBA", (140, 140), (0, 0, 0, 0))
            td = ImageDraw.Draw(token)
            td.ellipse((10, 10, 130, 130), fill=(ring[0] // 2, ring[1] // 2, ring[2] // 2))

        diameter = 56
        if is_dead:
            token = _dim_dead_token(token)
            ring_color = (120, 120, 120)
        else:
            ring_color = ring
        _paste_circle(canvas, token, cx, cy, diameter, ring_color=ring_color, ring_width=3)
        if is_dead:
            r = diameter // 2 - 6
            draw.line((cx - r, cy - r, cx + r, cy + r), fill=(225, 45, 45, 235), width=4)
            draw.line((cx + r, cy - r, cx - r, cy + r), fill=(225, 45, 45, 235), width=4)

        if pid == viewer_id:
            left = margin + px * cell
            top = margin + py * cell
            draw.rectangle(
                (left + 2, top + 2, left + cell - 3, top + cell - 3),
                outline=VIEWER_SQUARE_COLOR,
                width=4,
            )

    pl = margin + grid_px + 16
    draw.rounded_rectangle(
        (pl, margin, width - margin, height - margin),
        radius=14,
        fill=(44, 46, 50),
        outline=(90, 94, 100),
        width=2,
    )
    body = _load_font(16)
    small = _load_font(13)
    y = margin + 16
    draw.text((pl + 14, y), "👥 Кооп-вылазка", fill=(240, 240, 240), font=body)
    y += 28
    for line in coop_status_caption(session, storage, viewer_id).split("\n")[1:]:
        draw.text((pl + 14, y), line[:44], fill=(200, 200, 200), font=small)
        y += 18
    y += 6
    for line in session.log[-6:]:
        draw.text((pl + 14, y), line[:44], fill=(160, 160, 160), font=small)
        y += 16

    draw_enemy_hud(
        canvas,
        hud_slots_from_kinds(session.enemy_kinds, session.npc_kinds),
        panel_left=pl,
        panel_top=margin,
        panel_right=width - margin,
        panel_bottom=height - margin,
    )

    buf = BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()
