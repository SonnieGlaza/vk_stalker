"""Арена на домашней базе: бесконечные волны НПС, тренировка без потерь."""

from __future__ import annotations

import json
import logging
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

from app.game_logic import (
    ActionResult,
    _add_rating,
    effective_max_health,
    faction_home_base,
    h,
)
from app.storage import Storage
from app.tactical_hp import sync_session_hp_to_db

logger = logging.getLogger(__name__)
from app.tactical_combat import (
    MOVE_DELTAS,
    NPC_MOVE_CHANCE,
    STALE_TURN_MESSAGE,
    best_step_toward,
    collect_player_shot_hits,
    manhattan_distance,
    npc_weapon_damage,
    random_hostile_shots,
    spawn_edge_positions,
)
from app.enemy_hud import draw_enemy_hud, hud_slots_from_kinds
from app.tactical_render import load_tactical_font, paste_npc_sprite, paste_player_avatar

ARENA_GRID_SIZE = 8
ARENA_TURN_SECONDS = 12
ARENA_START_MEDKITS = 3
ARENA_MEDKIT_HEAL = 45
ARENA_RENDER_CELL = 80
ARENA_SPRITE_DIAMETER = 62

SESSION_PREFIX = "arena:session:"
PLAYER_PREFIX = "arena:player:"
ACTIVE_IDS_KEY = "arena:active_ids"

# (оружие игрока, уровень брони 0–3, число НПС, оружие НПС)
ARENA_WAVE_LOADOUTS: list[tuple[str, int, int, str]] = [
    ("ПМ", 0, 2, "ПМ"),
    ("Обрез", 0, 2, "ПМ"),
    ("ПМ", 1, 3, "ПМ"),
    ("АКС-74У", 1, 3, "Обрез"),
    ("АК-74", 2, 4, "АКС-74У"),
    ("АН-94", 3, 4, "АК-74"),
    ("СПАС-12", 3, 5, "АК-74"),
    ("ТРс-301", 3, 5, "АН-94"),
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


@dataclass
class ArenaGridSession:
    session_id: str
    telegram_id: int
    home_base: str
    grid: int = ARENA_GRID_SIZE
    cover: list[tuple[int, int]] = field(default_factory=list)
    base_cover: list[tuple[int, int]] = field(default_factory=list)
    hostiles: list[tuple[int, int]] = field(default_factory=list)
    hostile_weapons: list[str] = field(default_factory=list)
    player_pos: tuple[int, int] = (1, 3)
    hp: int = 100
    max_hp: int = 100
    entry_hp: int = 100  # HP в БД на входе — восстанавливаем при любом выходе
    arena_medkits: int = ARENA_START_MEDKITS
    player_weapon: str = "ПМ"
    player_armor_level: int = 0
    wave: int = 1
    waves_cleared: int = 0
    turn_seq: int = 0
    turn_deadline: str | None = None
    finished: bool = False
    log: list[str] = field(default_factory=list)
    message_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "telegram_id": self.telegram_id,
            "home_base": self.home_base,
            "grid": self.grid,
            "cover": [list(p) for p in self.cover],
            "base_cover": [list(p) for p in self.base_cover],
            "hostiles": [list(p) for p in self.hostiles],
            "hostile_weapons": list(self.hostile_weapons),
            "player_pos": list(self.player_pos),
            "hp": self.hp,
            "max_hp": self.max_hp,
            "entry_hp": self.entry_hp,
            "arena_medkits": self.arena_medkits,
            "player_weapon": self.player_weapon,
            "player_armor_level": self.player_armor_level,
            "wave": self.wave,
            "waves_cleared": self.waves_cleared,
            "turn_seq": self.turn_seq,
            "turn_deadline": self.turn_deadline,
            "finished": self.finished,
            "log": list(self.log[-16:]),
            "message_id": self.message_id,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ArenaGridSession:
        pp = raw.get("player_pos") or [1, 3]
        max_hp = int(raw.get("max_hp") or 100)
        entry_raw = raw.get("entry_hp")
        entry_hp = int(entry_raw) if entry_raw is not None else max_hp
        return cls(
            session_id=str(raw.get("session_id") or ""),
            telegram_id=int(raw.get("telegram_id") or 0),
            home_base=str(raw.get("home_base") or ""),
            grid=int(raw.get("grid") or ARENA_GRID_SIZE),
            cover=[(int(p[0]), int(p[1])) for p in (raw.get("cover") or [])],
            base_cover=[(int(p[0]), int(p[1])) for p in (raw.get("base_cover") or [])],
            hostiles=[(int(p[0]), int(p[1])) for p in (raw.get("hostiles") or [])],
            hostile_weapons=[str(w) for w in (raw.get("hostile_weapons") or [])],
            player_pos=(int(pp[0]), int(pp[1])),
            hp=int(raw.get("hp") or 0),
            max_hp=max_hp,
            entry_hp=max(1, entry_hp),
            arena_medkits=int(raw.get("arena_medkits") or 0),
            player_weapon=str(raw.get("player_weapon") or "ПМ"),
            player_armor_level=int(raw.get("player_armor_level") or 0),
            wave=int(raw.get("wave") or 1),
            waves_cleared=int(raw.get("waves_cleared") or 0),
            turn_seq=int(raw.get("turn_seq") or 0),
            turn_deadline=raw.get("turn_deadline"),
            finished=bool(raw.get("finished")),
            log=[str(x) for x in (raw.get("log") or [])],
            message_id=int(raw["message_id"]) if raw.get("message_id") is not None else None,
        )


def _session_key(sid: str) -> str:
    return f"{SESSION_PREFIX}{sid}"


def _player_key(tid: int) -> str:
    return f"{PLAYER_PREFIX}{int(tid)}"


def get_arena_session(storage: Storage, telegram_id: int) -> ArenaGridSession | None:
    sid = storage.get_meta(_player_key(telegram_id))
    if not sid:
        return None
    raw = storage.get_meta(_session_key(sid))
    if not raw:
        storage.delete_meta(_player_key(telegram_id))
        return None
    try:
        session = ArenaGridSession.from_dict(json.loads(raw))
    except Exception:
        storage.delete_meta(_player_key(telegram_id))
        return None
    if session.finished:
        return None
    return session


def save_arena_session(storage: Storage, session: ArenaGridSession) -> None:
    storage.set_meta(_session_key(session.session_id), json.dumps(session.to_dict(), ensure_ascii=False))
    storage.set_meta(_player_key(session.telegram_id), session.session_id)


def patch_arena_message_id(storage: Storage, session_id: str, message_id: int | None) -> None:
    """Обновить только message_id в JSON meta арены."""
    from app.tactical_turn import patch_session_json_field

    patch_session_json_field(
        storage,
        meta_key=_session_key(session_id),
        field="message_id",
        value=message_id,
    )


def clear_arena_session(storage: Storage, session: ArenaGridSession) -> None:
    storage.delete_meta(_session_key(session.session_id))
    storage.delete_meta(_player_key(session.telegram_id))
    raw = storage.get_meta(ACTIVE_IDS_KEY)
    ids: list[str] = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                ids = [str(x) for x in parsed if str(x) != session.session_id]
        except json.JSONDecodeError:
            ids = []
    storage.set_meta(ACTIVE_IDS_KEY, json.dumps(ids, ensure_ascii=False))


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


def _wave_loadout(wave: int) -> tuple[str, int, int, str]:
    idx = min(wave - 1, len(ARENA_WAVE_LOADOUTS) - 1)
    weapon, armor, npc_n, npc_weapon = ARENA_WAVE_LOADOUTS[idx]
    extra = max(0, wave - len(ARENA_WAVE_LOADOUTS))
    npc_n += extra // 2
    return weapon, armor, max(2, npc_n), npc_weapon


def _build_arena_map(session: ArenaGridSession) -> None:
    grid = session.grid
    forbidden: set[tuple[int, int]] = set()
    for y in range(grid):
        for x in range(grid - 2, grid):
            session.base_cover.append((x, y))
    for y in range(1, grid - 1):
        for x in range(grid - 2, grid):
            if random.random() < 0.55:
                cell = (x, y)
                if cell not in forbidden:
                    session.cover.append(cell)
                    forbidden.add(cell)
    for cell in session.base_cover:
        if cell not in forbidden:
            session.cover.append(cell)
            forbidden.add(cell)
    for _ in range(random.randint(6, 10)):
        x, y = random.randint(0, grid - 3), random.randint(0, grid - 1)
        cell = (x, y)
        if cell not in forbidden:
            session.cover.append(cell)
            forbidden.add(cell)
    session.player_pos = (1, grid // 2)


def _spawn_wave_hostiles(session: ArenaGridSession) -> None:
    session.hostiles.clear()
    session.hostile_weapons.clear()
    _, _, npc_n, npc_weapon = _wave_loadout(session.wave)
    forbidden = set(session.cover) | {session.player_pos}
    edge = spawn_edge_positions(session.grid, count=npc_n, forbidden=forbidden)
    while len(edge) < npc_n:
        x, y = random.randint(session.grid - 3, session.grid - 1), random.randint(0, session.grid - 1)
        cell = (x, y)
        if cell not in forbidden:
            edge.append(cell)
            forbidden.add(cell)
    for cell in edge[:npc_n]:
        session.hostiles.append(cell)
        session.hostile_weapons.append(npc_weapon)


def _apply_wave_loadout(session: ArenaGridSession) -> None:
    weapon, armor, _, _ = _wave_loadout(session.wave)
    session.player_weapon = weapon
    session.player_armor_level = armor


def _hostile_move_blocked(session: ArenaGridSession) -> set[tuple[int, int]]:
    blocked = set(session.hostiles)
    blocked.add(session.player_pos)
    return blocked


def _npc_damage(weapon: str) -> int:
    return npc_weapon_damage(weapon)


def _hostile_turn(storage: Storage, session: ArenaGridSession) -> list[str]:
    notes: list[str] = []
    cover_set = set(session.cover)
    player_pos = {session.telegram_id: session.player_pos}
    player_cells = {session.player_pos}

    occupied = _hostile_move_blocked(session)
    new_hostiles: list[tuple[int, int]] = []
    new_weapons: list[str] = []
    for i, pos in enumerate(session.hostiles):
        origin = pos
        weapon = session.hostile_weapons[i] if i < len(session.hostile_weapons) else "ПМ"
        occupied.discard(origin)
        current = origin
        if random.random() < NPC_MOVE_CHANCE:
            current = best_step_toward(
                origin,
                session.player_pos,
                grid=session.grid,
                blocked=occupied,
                forbidden=player_cells,
            )
        new_hostiles.append(current)
        new_weapons.append(weapon)
        occupied.add(current)
    session.hostiles = new_hostiles
    session.hostile_weapons = new_weapons

    player_hp = {str(session.telegram_id): session.hp}
    armor_level = session.player_armor_level
    notes.extend(
        random_hostile_shots(
            session.hostiles,
            session.hostile_weapons,
            grid=session.grid,
            player_positions=player_pos,
            player_hp=player_hp,
            player_characters={session.telegram_id: None},
            cover=cover_set,
            base_cover=set(session.base_cover),
            damage_fn=lambda weapon: _arena_apply_damage(_npc_damage(weapon), armor_level),
        )
    )
    session.hp = max(0, int(player_hp[str(session.telegram_id)]))

    return notes


def _arena_apply_damage(raw: int, armor_level: int) -> int:
    return max(1, int(raw) - max(0, armor_level))


def _finalize_arena_reward(storage: Storage, session: ArenaGridSession, *, reason: str) -> ActionResult:
    """Награда за ≥1 волну; смерть на арене не отменяет выплату и не даёт штрафов."""
    fell = session.hp <= 0
    if session.waves_cleared >= 1:
        # 1 рейтинг + 100 RU за каждую пройденную волну.
        reward = 100 * session.waves_cleared
        rating_gain = session.waves_cleared
        storage.change_money(session.telegram_id, reward)
        _add_rating(storage, session.telegram_id, rating_gain)
        storage.add_player_stat(session.telegram_id, "money_earned", reward)
        outcome = "Падение на арене." if fell else reason
        text = (
            f"🏟 Арена «{session.home_base}» завершена.\n"
            f"{outcome}\n"
            f"Пройдено волн: {session.waves_cleared}.\n"
            f"Награда: {reward} RU, рейтинг +{rating_gain} (за каждую волну: 100 RU и 1 рейтинг).\n"
            "Тренировка: HP и ресурсы как при входе, без штрафов смерти."
        )
    else:
        outcome = "Падение на арене." if fell else reason
        text = (
            f"🏟 Арена «{session.home_base}» завершена.\n"
            f"{outcome}\n"
            f"Волны не пройдены — награды нет.\n"
            "Тренировка: HP и ресурсы как при входе, без штрафов смерти."
        )
    return ActionResult(
        True,
        text,
        payload={
            "arena_done": True,
            "telegram_id": session.telegram_id,
            "waves_cleared": session.waves_cleared,
            "message_id": session.message_id,
            "soft_exit": True,
        },
    )


def _restore_arena_entry_hp(storage: Storage, session: ArenaGridSession) -> None:
    """Вернуть HP персонажа к значению на входе в арену (без смерти/лута)."""
    player = storage.get_character(session.telegram_id, refresh_energy=False)
    if player is None:
        return
    max_hp = effective_max_health(player)
    restore_to = max(1, min(max_hp, int(session.entry_hp or player.health or 1)))
    sync_session_hp_to_db(storage, session.telegram_id, restore_to, force=True)


def _end_session(
    storage: Storage,
    session: ArenaGridSession,
    result: ActionResult,
    *,
    expected_seq: int | None = None,
) -> ActionResult:
    if session.finished:
        payload = dict(result.payload or {})
        payload["message_id"] = session.message_id
        return ActionResult(result.ok, result.text, payload=payload)
    # Арена — тренировка: не пишем полевое HP/смерть в БД, только откат к входу.
    _restore_arena_entry_hp(storage, session)
    session.finished = True
    if expected_seq is not None:
        if not _save_turn(storage, session, expected_seq):
            raw = storage.get_meta(_session_key(session.session_id))
            if raw:
                try:
                    fresh = ArenaGridSession.from_dict(json.loads(raw))
                    if fresh.finished:
                        fp = dict(result.payload or {})
                        fp["message_id"] = fresh.message_id
                        return ActionResult(result.ok, result.text, payload=fp)
                except Exception:
                    logger.exception("Failed to re-read finished arena session %s", session.session_id)
            return ActionResult(False, STALE_TURN_MESSAGE)
    else:
        save_arena_session(storage, session)
    msg_id = session.message_id
    clear_arena_session(storage, session)
    payload = dict(result.payload or {})
    payload["message_id"] = msg_id
    payload["soft_exit"] = True
    return ActionResult(result.ok, result.text, payload=payload)


def _advance_wave(session: ArenaGridSession) -> None:
    session.waves_cleared += 1
    session.wave += 1
    _apply_wave_loadout(session)
    _spawn_wave_hostiles(session)
    session.log.append(f"✅ Волна {session.waves_cleared} зачищена! Следующая: {session.wave}.")


def _check_wave_clear(session: ArenaGridSession) -> bool:
    return len(session.hostiles) == 0


def _after_turn(storage: Storage, session: ArenaGridSession, expected_seq: int) -> ActionResult | None:
    if session.hp <= 0:
        player = storage.get_character(session.telegram_id, refresh_energy=False)
        name = h(player.nickname) if player else str(session.telegram_id)
        return _end_session(
            storage,
            session,
            _finalize_arena_reward(storage, session, reason=f"{name} выведен из строя."),
            expected_seq=expected_seq,
        )
    wave_advanced = False
    while _check_wave_clear(session):
        _advance_wave(session)
        wave_advanced = True
    if not wave_advanced:
        session.log.extend(_hostile_turn(storage, session))
    if session.hp <= 0:
        player = storage.get_character(session.telegram_id, refresh_energy=False)
        name = h(player.nickname) if player else str(session.telegram_id)
        return _end_session(
            storage,
            session,
            _finalize_arena_reward(storage, session, reason=f"{name} пал на волне {session.wave}."),
            expected_seq=expected_seq,
        )
    return None


def start_arena(storage: Storage, telegram_id: int) -> ActionResult:
    from app.player_busy import player_busy_reason

    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if player.faction is None:
        return ActionResult(False, "Сначала выбери группировку.")
    if player.health <= 0:
        return ActionResult(False, "Сначала восстановись после ранения.")
    home = faction_home_base(player.faction)
    if player.location != home or player.travel_destination:
        return ActionResult(False, f"Арена доступна только на базе «{home}».")
    busy = player_busy_reason(storage, telegram_id, skip="arena")
    if busy:
        return ActionResult(False, busy)
    if get_arena_session(storage, telegram_id) is not None:
        return ActionResult(False, "Ты уже на арене.")

    session_id = uuid.uuid4().hex[:12]
    max_hp = effective_max_health(player)
    entry_hp = max(1, min(max_hp, int(player.health)))
    session = ArenaGridSession(
        session_id=session_id,
        telegram_id=telegram_id,
        home_base=home,
        max_hp=max_hp,
        hp=max_hp,
        entry_hp=entry_hp,
        arena_medkits=ARENA_START_MEDKITS,
    )
    _build_arena_map(session)
    _apply_wave_loadout(session)
    _spawn_wave_hostiles(session)
    session.turn_deadline = _deadline_iso(ARENA_TURN_SECONDS)
    weapon, _, npc_n, npc_w = _wave_loadout(1)
    session.log.append(
        f"🏟 Арена «{home}»: волна 1. Снаряжение: {weapon}. "
        f"НПС: {npc_n}× ({npc_w}). Аптечки арены: {ARENA_START_MEDKITS}."
    )
    save_arena_session(storage, session)
    _register_active(storage, session_id)
    return ActionResult(
        True,
        session.log[-1],
        payload={"arena_started": True, "session_id": session_id},
    )


def arena_move(storage: Storage, telegram_id: int, direction: str) -> ActionResult:
    session = get_arena_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Нет активной арены.")
    delta = MOVE_DELTAS.get(direction)
    if delta is None:
        return ActionResult(False, "Некорректное направление.")
    turn_seq = session.turn_seq
    pos = session.player_pos
    nxt = (pos[0] + delta[0], pos[1] + delta[1])
    if not (0 <= nxt[0] < session.grid and 0 <= nxt[1] < session.grid):
        return ActionResult(False, "Край поля.")
    blocked = set(session.hostiles) | set(session.cover)
    if nxt in session.hostiles:
        idx = session.hostiles.index(nxt)
        session.hostiles.pop(idx)
        if idx < len(session.hostile_weapons):
            session.hostile_weapons.pop(idx)
        dmg = _arena_apply_damage(random.randint(8, 14), session.player_armor_level)
        session.hp = max(0, session.hp - dmg)
        session.log.append(f"Ближний бой: −{dmg} HP.")
    elif nxt in blocked and nxt not in session.cover and nxt not in session.base_cover:
        return ActionResult(False, "Клетка занята.")
    else:
        session.player_pos = nxt
    session.turn_seq += 1
    session.turn_deadline = _deadline_iso(ARENA_TURN_SECONDS)
    done = _after_turn(storage, session, turn_seq)
    if done:
        return done
    if not _save_turn(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    return ActionResult(True, "Шаг.", payload={"arena_active": True})


def arena_shoot(storage: Storage, telegram_id: int, direction: str) -> ActionResult:
    session = get_arena_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Нет активной арены.")
    if direction not in MOVE_DELTAS:
        return ActionResult(False, "Некорректный выстрел.")
    turn_seq = session.turn_seq
    weapon = session.player_weapon
    # Арена — тренировка: патроны не расходуются (бесплатные пули).
    origin = session.player_pos
    cover_set = set(session.cover) | set(session.base_cover)
    targets = {pos: "host" for pos in session.hostiles}
    hits = collect_player_shot_hits(
        origin,
        direction,
        grid=session.grid,
        weapon_name=weapon,
        blockers=set(session.cover),
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
                hit_any = True
        if hit_any:
            session.log.append(f"Попадание ({weapon})!")
            note = "Попал!"
        else:
            session.log.append("Промах.")
    else:
        session.log.append("Промах.")
    session.turn_seq += 1
    session.turn_deadline = _deadline_iso(ARENA_TURN_SECONDS)
    done = _after_turn(storage, session, turn_seq)
    if done:
        return done
    if not _save_turn(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    return ActionResult(True, note, payload={"arena_active": True})


def arena_use_medkit(storage: Storage, telegram_id: int) -> ActionResult:
    session = get_arena_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Нет активной арены.")
    if session.arena_medkits <= 0:
        return ActionResult(False, "Аптечки арены закончились.")
    turn_seq = session.turn_seq
    if session.hp >= session.max_hp:
        return ActionResult(False, "Здоровье уже полное.")
    heal = min(ARENA_MEDKIT_HEAL, session.max_hp - session.hp)
    session.hp += heal
    session.arena_medkits -= 1
    session.log.append(f"Аптечка арены: +{heal} HP (осталось {session.arena_medkits}).")
    session.turn_seq += 1
    session.turn_deadline = _deadline_iso(ARENA_TURN_SECONDS)
    done = _after_turn(storage, session, turn_seq)
    if done:
        return done
    if not _save_turn(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    return ActionResult(
        True,
        f"Аптечка арены: +{heal} HP.",
        payload={"arena_active": True},
    )


def arena_forfeit(storage: Storage, telegram_id: int) -> ActionResult:
    session = get_arena_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Нет активной арены.")
    return _end_session(
        storage,
        session,
        _finalize_arena_reward(storage, session, reason="Ты покинул арену."),
    )


def _save_turn(storage: Storage, session: ArenaGridSession, expected_seq: int) -> bool:
    from app.tactical_turn import save_turn_if_seq_ok

    return save_turn_if_seq_ok(
        storage,
        meta_key=_session_key(session.session_id),
        session=session,
        from_dict=ArenaGridSession.from_dict,
        save_fn=save_arena_session,
        expected_seq=expected_seq,
    )


def process_arena_turn_timeouts(storage: Storage) -> list[tuple[int, ActionResult]]:
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
            session = ArenaGridSession.from_dict(json.loads(raw_s))
        except Exception:
            continue
        if session.finished:
            continue
        deadline = _parse_deadline(session.turn_deadline)
        if deadline is None or now <= deadline:
            still.append(str(sid))
            continue
        turn_seq = session.turn_seq
        session.turn_seq += 1
        session.turn_deadline = _deadline_iso(ARENA_TURN_SECONDS)
        session.log.append("⏱ Время хода истекло — пропуск.")
        done = _after_turn(storage, session, turn_seq)
        if done:
            outcomes.append((session.telegram_id, done))
            continue
        still.append(str(sid))
        if _save_turn(storage, session, turn_seq):
            outcomes.append(
                (
                    session.telegram_id,
                    ActionResult(True, "Ход пропущен.", payload={"arena_active": True}),
                )
            )
    storage.set_meta(ACTIVE_IDS_KEY, json.dumps(still, ensure_ascii=False))
    return outcomes


def arena_status_caption(session: ArenaGridSession) -> str:
    weapon, armor, npc_n, npc_w = _wave_loadout(session.wave)
    lines = [
        f"🏟 Арена «{session.home_base}» · волна {session.wave}",
        f"Снаряжение волны: {session.player_weapon} · броня {session.player_armor_level}/3",
        f"HP {session.hp}/{session.max_hp} · аптечки арены: {session.arena_medkits}",
        f"НПС на поле: {len(session.hostiles)} · пройдено волн: {session.waves_cleared}",
    ]
    deadline = _parse_deadline(session.turn_deadline)
    if deadline:
        secs = max(0, int((deadline - _utc_now()).total_seconds()))
        lines.append(f"⏱ Ход: {secs} сек")
    if session.log:
        lines.append(session.log[-1][:80])
    return "\n".join(lines)


def render_arena_frame(storage: Storage, session: ArenaGridSession) -> bytes:
    cell = ARENA_RENDER_CELL
    grid = session.grid
    margin = 16
    panel_w = 260
    grid_px = grid * cell
    width = margin + grid_px + 12 + panel_w + margin
    height = max(margin + grid_px + margin, 580)
    canvas = Image.new("RGBA", (width, height), (18, 20, 22, 255))
    draw = ImageDraw.Draw(canvas)
    for gy in range(grid):
        for gx in range(grid):
            left = margin + gx * cell
            top = margin + gy * cell
            tone = 62 + ((gx * 13 + gy * 19) % 16)
            fill = (tone, tone - 4, tone - 8)
            if (gx, gy) in session.base_cover:
                fill = (tone - 8, tone - 12, tone - 6)
            draw.rectangle((left, top, left + cell - 1, top + cell - 1), fill=fill)
            draw.rectangle((left, top, left + cell - 1, top + cell - 1), outline=(28, 30, 34), width=1)
    for cx, cy in session.cover:
        left = margin + cx * cell + 4
        top = margin + cy * cell + 4
        draw.rounded_rectangle((left, top, left + cell - 8, top + cell - 8), radius=6, fill=(70, 62, 48, 200), outline=(100, 90, 70))
    for bx, by in session.base_cover:
        if (bx, by) in session.cover:
            continue
        left = margin + bx * cell + 2
        top = margin + by * cell + 2
        draw.rectangle((left, top, left + cell - 4, top + cell - 4), outline=(80, 100, 140), width=1)
    for hx, hy in session.hostiles:
        cxp = margin + hx * cell + cell // 2
        cyp = margin + hy * cell + cell // 2
        paste_npc_sprite(canvas, draw, cx=cxp, cy=cyp, kind="maloy", diameter=ARENA_SPRITE_DIAMETER)
    px, py = session.player_pos
    paste_player_avatar(
        canvas,
        draw,
        storage,
        pid=session.telegram_id,
        cx=margin + px * cell + cell // 2,
        cy=margin + py * cell + cell // 2,
        diameter=ARENA_SPRITE_DIAMETER - 6,
        ring_color=(80, 200, 255),
        hp=session.hp,
        is_active=True,
        viewer_cell=(margin, cell, px, py),
    )
    pl = margin + grid_px + 12
    draw.rounded_rectangle((pl, margin, width - margin, height - margin), radius=12, fill=(44, 46, 50), outline=(90, 94, 100), width=2)
    small = load_tactical_font(12)
    y = margin + 12
    for line in arena_status_caption(session).split("\n")[:8]:
        draw.text((pl + 10, y), line[:34], fill=(220, 220, 220), font=small)
        y += 18
    draw_enemy_hud(
        canvas,
        hud_slots_from_kinds([], ["maloy"] * len(session.hostiles)),
        panel_left=pl,
        panel_top=margin,
        panel_right=width - margin,
        panel_bottom=height - margin,
    )
    buf = BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
