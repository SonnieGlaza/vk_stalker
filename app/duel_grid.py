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
    DUEL_LOSER_HP_REMAINING,
    DUEL_LOSER_MONEY_CAP,
    DUEL_LOSER_MONEY_PERCENT,
    DUEL_WINNER_WOUND_MAX,
    DUEL_WINNER_WOUND_MIN,
    RATING_REWARD,
    ActionResult,
    _add_rating,
    apply_incoming_damage,
    effective_max_health,
    equipment_power,
    h,
)
from app.storage import Character, Storage
from app.tactical_combat import (
    COVER_HIT_CHANCE,
    MOVE_DELTAS,
    STALE_TURN_MESSAGE,
    best_step_toward,
    collect_player_shot_hits,
    consume_shot_ammo,
    manhattan_distance,
    move_toward,
    spawn_edge_positions,
    weapon_damage,
    weapon_shoot_range,
)
from app.mutant_assets import pick_mutant_kind

logger = logging.getLogger(__name__)
from app.tactical_hp import apply_tactical_medkit_spend, plan_tactical_medkit, sync_session_hp_to_db
from app.enemy_hud import draw_enemy_hud, hud_slots_from_kinds
from app.tactical_render import (
    draw_tactical_cell_overlay,
    load_tactical_font,
    paste_location_field_photo,
    paste_mutant_sprite,
    paste_player_avatar,
)

DUEL_GRID_SIZE = 8
DUEL_TURN_SECONDS = 10
DUEL_MATCH_SECONDS = 3 * 60
DUEL_COVER_HIT_CHANCE = COVER_HIT_CHANCE
DUEL_WAVE_MUTANT_SPAWN = 3
DUEL_WAVE_MUTANT_STEPS = 2
DUEL_WAVE_MUTANT_MAX = 14

DUEL_SESSION_PREFIX = "duel:grid:session:"
DUEL_PLAYER_PREFIX = "duel:grid:player:"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _deadline_iso(seconds: int = DUEL_TURN_SECONDS) -> str:
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


def _duel_damage(attacker: Character) -> int:
    weapon = str(attacker.equipment.get("weapon", "Нож"))
    if weapon_shoot_range(weapon) > 0:
        return min(15, weapon_damage(weapon))
    base = max(4, equipment_power(attacker) * 2)
    lo = max(1, base - 2)
    hi = base + 2
    return random.randint(lo, hi)


@dataclass
class DuelGridSession:
    duel_id: str
    challenger_id: int
    target_id: int
    grid: int = DUEL_GRID_SIZE
    cover: list[tuple[int, int]] = field(default_factory=list)
    mutants: list[tuple[int, int]] = field(default_factory=list)
    mutant_kinds: list[str] = field(default_factory=list)
    positions: dict[str, list[int]] = field(default_factory=dict)
    hp: dict[str, int] = field(default_factory=dict)
    medkits_used: dict[str, bool] = field(default_factory=dict)
    turn_order: list[int] = field(default_factory=list)
    active_index: int = 0
    turn_seq: int = 0
    turn_deadline: str | None = None
    match_deadline: str | None = None
    wave_mode: bool = False
    finished: bool = False
    winner_id: int | None = None
    loser_id: int | None = None
    log: list[str] = field(default_factory=list)
    message_ids: dict[str, int] = field(default_factory=dict)

    def active_player(self) -> int:
        from app.tactical_roster import resolve_active_player

        return resolve_active_player(self)

    def opponent_of(self, player_id: int) -> int:
        return self.target_id if player_id == self.challenger_id else self.challenger_id

    def pos(self, player_id: int) -> tuple[int, int]:
        raw = self.positions.get(str(player_id), [0, 0])
        return int(raw[0]), int(raw[1])

    def set_pos(self, player_id: int, pos: tuple[int, int]) -> None:
        self.positions[str(player_id)] = [pos[0], pos[1]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "duel_id": self.duel_id,
            "challenger_id": self.challenger_id,
            "target_id": self.target_id,
            "grid": self.grid,
            "cover": [list(p) for p in self.cover],
            "mutants": [list(p) for p in self.mutants],
            "mutant_kinds": list(self.mutant_kinds),
            "positions": self.positions,
            "hp": self.hp,
            "medkits_used": self.medkits_used,
            "turn_order": self.turn_order,
            "active_index": self.active_index,
            "turn_seq": self.turn_seq,
            "turn_deadline": self.turn_deadline,
            "match_deadline": self.match_deadline,
            "wave_mode": self.wave_mode,
            "finished": self.finished,
            "winner_id": self.winner_id,
            "loser_id": self.loser_id,
            "log": self.log[-12:],
            "message_ids": {str(k): int(v) for k, v in self.message_ids.items()},
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DuelGridSession:
        return cls(
            duel_id=str(raw.get("duel_id") or ""),
            challenger_id=int(raw.get("challenger_id") or 0),
            target_id=int(raw.get("target_id") or 0),
            grid=int(raw.get("grid") or DUEL_GRID_SIZE),
            cover=[(int(p[0]), int(p[1])) for p in (raw.get("cover") or [])],
            mutants=[(int(p[0]), int(p[1])) for p in (raw.get("mutants") or [])],
            mutant_kinds=[str(k) for k in (raw.get("mutant_kinds") or [])],
            positions={str(k): list(v) for k, v in (raw.get("positions") or {}).items()},
            hp={str(k): int(v) for k, v in (raw.get("hp") or {}).items()},
            medkits_used={str(k): bool(v) for k, v in (raw.get("medkits_used") or {}).items()},
            turn_order=[int(x) for x in (raw.get("turn_order") or [])],
            active_index=int(raw.get("active_index") or 0),
            turn_seq=int(raw.get("turn_seq") or 0),
            turn_deadline=raw.get("turn_deadline"),
            match_deadline=raw.get("match_deadline"),
            wave_mode=bool(raw.get("wave_mode")),
            finished=bool(raw.get("finished")),
            winner_id=int(raw["winner_id"]) if raw.get("winner_id") is not None else None,
            loser_id=int(raw["loser_id"]) if raw.get("loser_id") is not None else None,
            log=[str(x) for x in (raw.get("log") or [])],
            message_ids={str(k): int(v) for k, v in (raw.get("message_ids") or {}).items()},
        )


def _session_key(duel_id: str) -> str:
    return f"{DUEL_SESSION_PREFIX}{duel_id}"


def _player_key(telegram_id: int) -> str:
    return f"{DUEL_PLAYER_PREFIX}{int(telegram_id)}"


def get_duel_session_by_player(storage: Storage, telegram_id: int) -> DuelGridSession | None:
    duel_id = storage.get_meta(_player_key(telegram_id))
    if not duel_id:
        return None
    raw = storage.get_meta(_session_key(duel_id))
    if not raw:
        storage.delete_meta(_player_key(telegram_id))
        return None
    try:
        session = DuelGridSession.from_dict(json.loads(raw))
    except Exception:
        storage.delete_meta(_player_key(telegram_id))
        return None
    if session.finished:
        return None
    _ensure_mutant_kinds(session)
    return session


def save_duel_session(storage: Storage, session: DuelGridSession) -> None:
    storage.set_meta(_session_key(session.duel_id), json.dumps(session.to_dict(), ensure_ascii=False))
    for pid in (session.challenger_id, session.target_id):
        storage.set_meta(_player_key(pid), session.duel_id)


def clear_duel_session(storage: Storage, session: DuelGridSession) -> None:
    storage.delete_meta(_session_key(session.duel_id))
    storage.delete_meta(_player_key(session.challenger_id))
    storage.delete_meta(_player_key(session.target_id))


def _free_cell(grid: int, forbidden: set[tuple[int, int]]) -> tuple[int, int]:
    opts = [(x, y) for x in range(grid) for y in range(grid) if (x, y) not in forbidden]
    if not opts:
        raise RuntimeError("duel grid full")
    return random.choice(opts)


def _save_if_turn_ok(storage: Storage, session: DuelGridSession, expected_seq: int) -> bool:
    from app.tactical_turn import save_turn_if_seq_ok

    return save_turn_if_seq_ok(
        storage,
        meta_key=_session_key(session.duel_id),
        session=session,
        from_dict=DuelGridSession.from_dict,
        save_fn=save_duel_session,
        expected_seq=expected_seq,
    )


def _build_duel_map(session: DuelGridSession) -> None:
    grid = session.grid
    forbidden: set[tuple[int, int]] = set()
    c_pos = _free_cell(grid, forbidden)
    forbidden.add(c_pos)
    t_pos = _free_cell(grid, forbidden)
    forbidden.add(t_pos)
    session.set_pos(session.challenger_id, c_pos)
    session.set_pos(session.target_id, t_pos)
    cover_n = random.randint(8, 12)
    for _ in range(cover_n):
        cell = _free_cell(grid, forbidden)
        session.cover.append(cell)
        forbidden.add(cell)


def _finalize_duel_rewards(storage: Storage, winner_id: int, loser_id: int) -> tuple[str, str]:
    winner = storage.get_character(winner_id, refresh_energy=False)
    loser = storage.get_character(loser_id, refresh_energy=False)
    if winner is None or loser is None:
        return ("Дуэль завершена.", "Дуэль завершена.")
    winner_max_hp = effective_max_health(winner)
    loser_max_hp = effective_max_health(loser)
    wound = random.randint(DUEL_WINNER_WOUND_MIN, DUEL_WINNER_WOUND_MAX)
    storage.change_health(winner_id, -wound, max_health=winner_max_hp)
    loser_hp_target = min(DUEL_LOSER_HP_REMAINING, loser_max_hp)
    loser_hp_delta = loser_hp_target - int(loser.health)
    if loser_hp_delta != 0:
        storage.change_health(loser_id, loser_hp_delta, max_health=loser_max_hp)
    money_taken = max(0, int(loser.money * DUEL_LOSER_MONEY_PERCENT // 100))
    money_taken = min(money_taken, DUEL_LOSER_MONEY_CAP)
    if money_taken > 0:
        if storage.change_money(loser_id, -money_taken):
            if not storage.change_money(winner_id, money_taken):
                if not storage.change_money(loser_id, money_taken):
                    logger.exception(
                        "Failed to refund duel stake %s RU to loser %s after winner credit failed",
                        money_taken,
                        loser_id,
                    )
                money_taken = 0
        else:
            money_taken = 0
    _add_rating(storage, winner_id, RATING_REWARD["duel_win"])
    _add_rating(storage, loser_id, -RATING_REWARD["duel_lose"])
    winner_after = storage.get_character(winner_id, refresh_energy=False)
    loser_after = storage.get_character(loser_id, refresh_energy=False)
    common = (
        f"⚔️ Тактическая дуэль завершена.\n"
        f"Победитель: {h(winner.nickname)} (HP {winner_after.health if winner_after else 0}, "
        f"ранение −{wound}{f', +{money_taken} RU' if money_taken else ''}).\n"
        f"Проигравший: {h(loser.nickname)} (HP {loser_after.health if loser_after else 0}"
        f"{f', −{money_taken} RU' if money_taken else ''})."
    )
    win_text = f"🏆 Ты победил!\n{common}"
    lose_text = f"💀 Ты проиграл.\n{common}"
    return win_text, lose_text


def start_duel_grid(
    storage: Storage,
    challenger_id: int,
    target_id: int,
) -> tuple[ActionResult, DuelGridSession | None]:
    challenger = storage.get_character(challenger_id, refresh_energy=False)
    target = storage.get_character(target_id, refresh_energy=False)
    if challenger is None or target is None:
        return ActionResult(False, "Один из бойцов не найден."), None
    if get_duel_session_by_player(storage, challenger_id) or get_duel_session_by_player(storage, target_id):
        return ActionResult(False, "Один из бойцов уже в дуэли."), None

    from app.player_busy import player_busy_reason

    for pid in (challenger_id, target_id):
        busy = player_busy_reason(storage, pid, skip="duel")
        if busy:
            return ActionResult(False, busy), None

    duel_id = uuid.uuid4().hex[:12]
    session = DuelGridSession(
        duel_id=duel_id,
        challenger_id=challenger_id,
        target_id=target_id,
        turn_order=[challenger_id, target_id],
        active_index=0,
        turn_deadline=_deadline_iso(),
        match_deadline=_deadline_iso(DUEL_MATCH_SECONDS),
    )
    _build_duel_map(session)
    session.hp[str(challenger_id)] = int(challenger.health)
    session.hp[str(target_id)] = int(target.health)
    session.medkits_used[str(challenger_id)] = False
    session.medkits_used[str(target_id)] = False
    session.log.append(
        f"Дуэль: {h(challenger.nickname)} vs {h(target.nickname)}. "
        f"Таймер боя {DUEL_MATCH_SECONDS // 60} мин. Укрытие = 50% промах."
    )
    save_duel_session(storage, session)
    register_active_duel(storage, duel_id)
    text = (
        f"⚔️ Тактическая дуэль началась!\n"
        f"{h(challenger.nickname)} vs {h(target.nickname)}\n"
        f"Дальность: пистолет/дробовик 1 · автомат 2 · снайперка 3 · гаус 4 клетки (90°).\n"
        f"Таймер боя: {DUEL_MATCH_SECONDS // 60} мин — потом волна мутантов.\n"
        f"Урон ≈ сила×2 ±2. Аптечка — 1 раз за бой.\n"
        f"Первый ход: {h(challenger.nickname)}."
    )
    return ActionResult(True, text, payload={"duel_id": duel_id}), session


def _end_duel(
    storage: Storage,
    session: DuelGridSession,
    winner_id: int,
    loser_id: int,
    note: str,
    *,
    expected_seq: int | None = None,
) -> ActionResult:
    if session.finished:
        return ActionResult(
            True,
            note,
            payload={
                "duel_done": True,
                "winner_id": session.winner_id,
                "loser_id": session.loser_id,
                "duel_id": session.duel_id,
                "message_ids": {str(k): int(v) for k, v in session.message_ids.items()},
            },
        )
    winner_hp = session.hp.get(str(winner_id))
    if winner_hp is not None:
        sync_session_hp_to_db(storage, winner_id, int(winner_hp))
    session.finished = True
    session.winner_id = winner_id
    session.loser_id = loser_id
    session.log.append(note)
    seq = int(expected_seq if expected_seq is not None else session.turn_seq)
    if expected_seq is not None:
        if not _save_if_turn_ok(storage, session, seq):
            raw = storage.get_meta(_session_key(session.duel_id))
            if raw:
                try:
                    fresh = DuelGridSession.from_dict(json.loads(raw))
                    if fresh.finished:
                        return ActionResult(
                            True,
                            note,
                            payload={
                                "duel_done": True,
                                "winner_id": fresh.winner_id,
                                "loser_id": fresh.loser_id,
                                "duel_id": fresh.duel_id,
                                "message_ids": {str(k): int(v) for k, v in fresh.message_ids.items()},
                            },
                        )
                except Exception:
                    logger.exception("Failed to re-read finished duel session %s", session.duel_id)
            return ActionResult(False, STALE_TURN_MESSAGE)
    else:
        save_duel_session(storage, session)
    win_text, lose_text = _finalize_duel_rewards(storage, winner_id, loser_id)
    message_ids = {str(k): int(v) for k, v in session.message_ids.items()}
    clear_duel_session(storage, session)
    unregister_active_duel(storage, session.duel_id)
    winner = storage.get_character(winner_id, refresh_energy=False)
    loser = storage.get_character(loser_id, refresh_energy=False)
    payload = {
        "duel_done": True,
        "winner_id": winner_id,
        "loser_id": loser_id,
        "winner_text": win_text,
        "loser_text": lose_text,
        "winner_name": winner.nickname if winner else str(winner_id),
        "loser_name": loser.nickname if loser else str(loser_id),
        "message_ids": message_ids,
        "duel_id": session.duel_id,
    }
    return ActionResult(True, note, payload=payload)


def _end_duel_wave(storage: Storage, session: DuelGridSession, note: str, *, expected_seq: int | None = None) -> ActionResult:
    """Оба проиграли волне — победитель с большим HP (или ничья → challenger wins by HP tie)."""
    c_hp = session.hp.get(str(session.challenger_id), 0)
    t_hp = session.hp.get(str(session.target_id), 0)
    if c_hp > t_hp:
        winner_id, loser_id = session.challenger_id, session.target_id
    elif t_hp > c_hp:
        winner_id, loser_id = session.target_id, session.challenger_id
    else:
        winner_id, loser_id = session.challenger_id, session.target_id
    return _end_duel(storage, session, winner_id, loser_id, note, expected_seq=expected_seq)


def _check_hp_end(storage: Storage, session: DuelGridSession, expected_seq: int) -> ActionResult | None:
    alive = [pid for pid in (session.challenger_id, session.target_id) if session.hp.get(str(pid), 0) > 0]
    if len(alive) == 1:
        winner_id = alive[0]
        loser_id = session.opponent_of(winner_id)
        return _end_duel(
            storage,
            session,
            winner_id,
            loser_id,
            f"{h(storage.get_character(winner_id).nickname if storage.get_character(winner_id) else str(winner_id))} победил — HP противника 0.",
            expected_seq=expected_seq,
        )
    if len(alive) == 0:
        return _end_duel_wave(storage, session, "Оба бойца пали.", expected_seq=expected_seq)
    return None


def _after_turn_mutants(storage: Storage, session: DuelGridSession, expected_seq: int) -> ActionResult | None:
    _move_mutants(session)
    session.log.extend(_mutants_attack(session, storage))
    return _check_hp_end(storage, session, expected_seq)


def _occupied(session: DuelGridSession, *, exclude: int | None = None) -> set[tuple[int, int]]:
    blocked = set(session.cover)
    blocked.update(session.mutants)
    for pid in (session.challenger_id, session.target_id):
        if exclude is not None and pid == exclude:
            continue
        blocked.add(session.pos(pid))
    return blocked


def _advance_turn(session: DuelGridSession) -> None:
    session.active_index = (session.active_index + 1) % len(session.turn_order)
    session.turn_seq += 1
    session.turn_deadline = _deadline_iso()


def _ensure_mutant_kinds(session: DuelGridSession) -> None:
    while len(session.mutant_kinds) < len(session.mutants):
        session.mutant_kinds.append(pick_mutant_kind())
    if len(session.mutant_kinds) > len(session.mutants):
        session.mutant_kinds = session.mutant_kinds[: len(session.mutants)]


def _remove_mutant_at(session: DuelGridSession, pos: tuple[int, int]) -> None:
    if pos not in session.mutants:
        return
    idx = session.mutants.index(pos)
    session.mutants.pop(idx)
    if idx < len(session.mutant_kinds):
        session.mutant_kinds.pop(idx)


def _spawn_wave_mutants(session: DuelGridSession) -> None:
    if len(session.mutants) >= DUEL_WAVE_MUTANT_MAX:
        return
    forbidden = _occupied(session)
    slots = DUEL_WAVE_MUTANT_MAX - len(session.mutants)
    new_spawns = spawn_edge_positions(session.grid, min(DUEL_WAVE_MUTANT_SPAWN, slots), forbidden)
    session.mutants.extend(new_spawns)
    session.mutant_kinds.extend(pick_mutant_kind() for _ in new_spawns)


def _move_mutants(session: DuelGridSession) -> None:
    if not session.mutants and session.wave_mode:
        _spawn_wave_mutants(session)
    occupied = _occupied(session)
    new_positions: list[tuple[int, int]] = []
    steps = DUEL_WAVE_MUTANT_STEPS if session.wave_mode else 1
    player_cells = {session.pos(session.challenger_id), session.pos(session.target_id)}
    for pos in session.mutants:
        origin = pos
        occupied.discard(origin)
        target = min(player_cells, key=lambda p: manhattan_distance(origin, p))
        current = origin
        for _ in range(steps):
            step = best_step_toward(
                current,
                target,
                grid=session.grid,
                blocked=occupied,
                forbidden=player_cells,
            )
            if step == current:
                break
            current = step
        new_positions.append(current)
        occupied.add(current)
    session.mutants = new_positions
    if session.wave_mode:
        _spawn_wave_mutants(session)


def _mutants_attack(session: DuelGridSession, storage: Storage) -> list[str]:
    notes: list[str] = []
    wave_dmg = (14, 22) if session.wave_mode else (6, 12)
    for mpos in session.mutants:
        for pid in (session.challenger_id, session.target_id):
            ppos = session.pos(pid)
            if manhattan_distance(mpos, ppos) != 1:
                continue
            player = storage.get_character(pid, refresh_energy=False)
            if player is None:
                continue
            dmg = apply_incoming_damage(random.randint(*wave_dmg), player, min_damage=2)
            session.hp[str(pid)] = max(0, session.hp.get(str(pid), 0) - dmg)
            label = "Волна мутантов" if session.wave_mode else "Мутант"
            notes.append(f"{label} ранит {h(player.nickname)}: −{dmg} HP.")
    return notes


def _start_wave_mode(session: DuelGridSession) -> None:
    if session.wave_mode:
        return
    session.wave_mode = True
    session.mutants.clear()
    session.mutant_kinds.clear()
    _spawn_wave_mutants(session)
    session.log.append("⏱ Время вышло! Бесконечная волна мутантов — бегите или погибните!")


def _check_match_timeout(storage: Storage, session: DuelGridSession, expected_seq: int) -> ActionResult | None:
    if session.wave_mode:
        return None
    deadline = _parse_deadline(session.match_deadline)
    if deadline is None or _utc_now() <= deadline:
        return None
    c_alive = session.hp.get(str(session.challenger_id), 0) > 0
    t_alive = session.hp.get(str(session.target_id), 0) > 0
    if c_alive and t_alive:
        _start_wave_mode(session)
        session.log.extend(_mutants_attack(session, storage))
        return _check_hp_end(storage, session, expected_seq)
    return None


def duel_move(storage: Storage, telegram_id: int, direction: str) -> ActionResult:
    session = get_duel_session_by_player(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Активной дуэли нет.")
    if session.active_player() != telegram_id:
        return ActionResult(False, "Сейчас ход соперника.")
    turn_seq = session.turn_seq
    delta = MOVE_DELTAS.get(direction)
    if delta is None:
        return ActionResult(False, "Некорректное направление.")
    pos = session.pos(telegram_id)
    nxt = (pos[0] + delta[0], pos[1] + delta[1])
    if not (0 <= nxt[0] < session.grid and 0 <= nxt[1] < session.grid):
        return ActionResult(False, "Край поля.")
    blocked = _occupied(session, exclude=telegram_id)
    opp = session.opponent_of(telegram_id)
    if nxt == session.pos(opp):
        return ActionResult(False, "Нельзя встать на клетку соперника — стреляй.")
    if nxt in blocked and nxt not in session.cover:
        return ActionResult(False, "Клетка занята.")
    session.set_pos(telegram_id, nxt)
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player and nxt in session.mutants:
        dmg = apply_incoming_damage(random.randint(8, 14), player, min_damage=3)
        session.hp[str(telegram_id)] = max(0, session.hp.get(str(telegram_id), 0) - dmg)
        session.log.append(f"{h(player.nickname)} задел мутанта: −{dmg} HP.")
    done = _check_hp_end(storage, session, turn_seq)
    if done:
        return done
    match_done = _check_match_timeout(storage, session, turn_seq)
    if match_done:
        if session.finished:
            return match_done
        if not _save_if_turn_ok(storage, session, turn_seq):
            return ActionResult(False, STALE_TURN_MESSAGE)
        return match_done
    _advance_turn(session)
    done = _after_turn_mutants(storage, session, turn_seq)
    if done:
        return done
    if not _save_if_turn_ok(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    return ActionResult(True, "Сделал шаг.", payload={"duel_active": True, "duel_id": session.duel_id})


def duel_shoot(storage: Storage, telegram_id: int, direction: str) -> ActionResult:
    session = get_duel_session_by_player(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Активной дуэли нет.")
    if session.active_player() != telegram_id:
        return ActionResult(False, "Сейчас ход соперника.")
    turn_seq = session.turn_seq
    if direction not in MOVE_DELTAS:
        return ActionResult(False, "Некорректный выстрел.")
    attacker = storage.get_character(telegram_id, refresh_energy=False)
    if attacker is None:
        return ActionResult(False, "Персонаж не найден.")
    weapon = str(attacker.equipment.get("weapon", "Нож"))
    rng = weapon_shoot_range(weapon)
    if rng <= 0:
        return ActionResult(False, "Это оружие не стреляет на дистанции.")
    ammo_result = consume_shot_ammo(storage, telegram_id, weapon)
    if ammo_result is not None:
        return ammo_result
    origin = session.pos(telegram_id)
    cover_set = set(session.cover)
    targets = {session.pos(session.opponent_of(telegram_id)): "player"}
    for mpos in session.mutants:
        targets[mpos] = "mutant"
    hits = collect_player_shot_hits(
        origin,
        direction,
        grid=session.grid,
        weapon_name=weapon,
        blockers=cover_set,
        targets=targets,
        cover=cover_set,
    )
    if not hits:
        session.log.append(f"{h(attacker.nickname)} промахнулся.")
        _advance_turn(session)
        done = _after_turn_mutants(storage, session, turn_seq)
        if done:
            return done
        if not _save_if_turn_ok(storage, session, turn_seq):
            return ActionResult(False, STALE_TURN_MESSAGE)
        return ActionResult(True, "Промах — пуля не нашла цель.", payload={"duel_active": True})

    note = "Попадание."
    player_damaged = False
    for hit_cell, hit_kind in hits:
        if hit_kind == "mutant":
            if hit_cell in session.mutants and not session.wave_mode:
                _remove_mutant_at(session, hit_cell)
            session.log.append(f"{h(attacker.nickname)} попал в мутанта.")
            note = "Мутант поражён." if not session.wave_mode else "Мутант снова встанет в волне."
        elif hit_kind == "player":
            defender_id = session.opponent_of(telegram_id)
            defender = storage.get_character(defender_id, refresh_energy=False)
            if defender is None:
                return ActionResult(False, "Соперник не найден.")
            raw = _duel_damage(attacker)
            dmg = apply_incoming_damage(raw, defender, min_damage=1)
            session.hp[str(defender_id)] = max(0, session.hp.get(str(defender_id), 0) - dmg)
            session.log.append(f"{h(attacker.nickname)} попал в {h(defender.nickname)}: −{dmg} HP.")
            note = f"Попадание: −{dmg} HP."
            player_damaged = True

    if player_damaged:
        done = _check_hp_end(storage, session, turn_seq)
        if done:
            return done

    match_done = _check_match_timeout(storage, session, turn_seq)
    if match_done:
        if session.finished:
            return match_done
        if not _save_if_turn_ok(storage, session, turn_seq):
            return ActionResult(False, STALE_TURN_MESSAGE)
        return match_done
    _advance_turn(session)
    done = _after_turn_mutants(storage, session, turn_seq)
    if done:
        return done
    if not _save_if_turn_ok(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    return ActionResult(True, note, payload={"duel_active": True, "duel_id": session.duel_id})


def duel_use_medkit(storage: Storage, telegram_id: int) -> ActionResult:
    session = get_duel_session_by_player(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Активной дуэли нет.")
    if session.active_player() != telegram_id:
        return ActionResult(False, "Сейчас ход соперника.")
    turn_seq = session.turn_seq
    if session.medkits_used.get(str(telegram_id)):
        return ActionResult(False, "Аптечку в этой дуэли уже использовал.")
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
    done = _after_turn_mutants(storage, session, turn_seq)
    if done:
        if not _save_if_turn_ok(storage, session, turn_seq):
            return ActionResult(False, STALE_TURN_MESSAGE)
        if item_key:
            apply_tactical_medkit_spend(storage, telegram_id, item_key, result)
        return done
    if not _save_if_turn_ok(storage, session, turn_seq):
        return ActionResult(False, STALE_TURN_MESSAGE)
    if item_key:
        apply_tactical_medkit_spend(storage, telegram_id, item_key, result)
    return ActionResult(True, result.text, payload={"duel_active": True})


def duel_forfeit(storage: Storage, telegram_id: int) -> ActionResult:
    session = get_duel_session_by_player(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Активной дуэли нет.")
    winner_id = session.opponent_of(telegram_id)
    player = storage.get_character(telegram_id, refresh_energy=False)
    note = f"{h(player.nickname) if player else telegram_id} сдался."
    return _end_duel(
        storage, session, winner_id, telegram_id, note, expected_seq=session.turn_seq
    )


def process_duel_turn_timeouts(storage: Storage) -> list[tuple[int, ActionResult]]:
    """Автопропуск хода по таймеру и проверка волны мутантов."""
    outcomes: list[tuple[int, ActionResult]] = []
    now = _utc_now()
    raw = storage.get_meta("duel:grid:active_ids")
    if not raw:
        return outcomes
    try:
        duel_ids = json.loads(raw)
    except json.JSONDecodeError:
        return outcomes
    if not isinstance(duel_ids, list):
        return outcomes
    still_active: list[str] = []
    finished: set[str] = set()
    for duel_id in duel_ids:
        raw_s = storage.get_meta(_session_key(str(duel_id)))
        if not raw_s:
            continue
        try:
            session = DuelGridSession.from_dict(json.loads(raw_s))
        except Exception:
            continue
        if session.finished:
            continue

        turn_seq = session.turn_seq
        match_done = _check_match_timeout(storage, session, turn_seq)
        if match_done:
            if session.finished:
                if str(duel_id) not in finished:
                    finished.add(str(duel_id))
                    outcomes.append((session.challenger_id, match_done))
                continue
            if not _save_if_turn_ok(storage, session, turn_seq):
                still_active.append(str(duel_id))
                continue
            still_active.append(str(duel_id))
            outcomes.append((session.active_player(), ActionResult(True, "Волна мутантов!", payload={"duel_active": True})))
            continue

        deadline = _parse_deadline(session.turn_deadline)
        if deadline is None or now <= deadline:
            still_active.append(str(duel_id))
            continue
        active = session.active_player()
        player = storage.get_character(active, refresh_energy=False)
        turn_seq = session.turn_seq
        session.log.append(f"Тайм-аут хода {h(player.nickname) if player else active}.")
        _advance_turn(session)
        done = _after_turn_mutants(storage, session, turn_seq)
        if done:
            if str(duel_id) not in finished:
                finished.add(str(duel_id))
                outcomes.append((active, done))
            continue
        if not _save_if_turn_ok(storage, session, turn_seq):
            still_active.append(str(duel_id))
            continue
        still_active.append(str(duel_id))
        outcomes.append(
            (
                active,
                ActionResult(True, "Время вышло — ход пропущен.", payload={"duel_active": True}),
            )
        )
    storage.set_meta("duel:grid:active_ids", json.dumps(still_active, ensure_ascii=False))
    return outcomes


def register_active_duel(storage: Storage, duel_id: str) -> None:
    raw = storage.get_meta("duel:grid:active_ids")
    ids: list[str] = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                ids = [str(x) for x in parsed]
        except json.JSONDecodeError:
            ids = []
    if duel_id not in ids:
        ids.append(duel_id)
    storage.set_meta("duel:grid:active_ids", json.dumps(ids, ensure_ascii=False))


def unregister_active_duel(storage: Storage, duel_id: str) -> None:
    raw = storage.get_meta("duel:grid:active_ids")
    if not raw:
        return
    try:
        ids = [str(x) for x in json.loads(raw) if str(x) != duel_id]
    except json.JSONDecodeError:
        ids = []
    storage.set_meta("duel:grid:active_ids", json.dumps(ids, ensure_ascii=False))


def _match_seconds_left(session: DuelGridSession) -> int | None:
    if session.wave_mode:
        return 0
    deadline = _parse_deadline(session.match_deadline)
    if deadline is None:
        return None
    return max(0, int((deadline - _utc_now()).total_seconds()))


def _duel_field_location(storage: Storage, session: DuelGridSession) -> str:
    for pid in (session.challenger_id, session.target_id):
        ch = storage.get_character(pid, refresh_energy=False)
        loc = str(ch.location or "").strip() if ch is not None else ""
        if loc:
            return loc
    return "Кордон"


def render_duel_frame(
    storage: Storage,
    session: DuelGridSession,
    viewer_id: int,
) -> bytes:
    _ensure_mutant_kinds(session)
    cell = 72
    grid = session.grid
    grid_px = grid * cell
    margin = 20
    panel_w = 300
    width = margin + grid_px + 16 + panel_w + margin
    height = max(margin + grid_px + margin, 640)
    canvas = Image.new("RGBA", (width, height), (18, 20, 22, 255))
    draw = ImageDraw.Draw(canvas)
    cover_font = load_tactical_font(11)
    location = _duel_field_location(storage, session)
    has_photo = paste_location_field_photo(canvas, location, margin=margin, grid_px=grid_px)
    for gy in range(grid):
        for gx in range(grid):
            left = margin + gx * cell
            top = margin + gy * cell
            draw_tactical_cell_overlay(
                canvas,
                draw,
                left=left,
                top=top,
                cell=cell,
                has_photo=has_photo,
                gx=gx,
                gy=gy,
            )
    for cx, cy in session.cover:
        left = margin + cx * cell + 6
        top = margin + cy * cell + 6
        draw.rounded_rectangle(
            (left, top, left + cell - 12, top + cell - 12),
            radius=8,
            fill=(70, 62, 48, 220),
            outline=(110, 95, 70),
            width=2,
        )
        draw.text((left + 8, top + cell // 2 - 8), "УКР", fill=(180, 160, 120), font=cover_font)
    for i, (mx, my) in enumerate(session.mutants):
        cx = margin + mx * cell + cell // 2
        cy = margin + my * cell + cell // 2
        kind = session.mutant_kinds[i] if i < len(session.mutant_kinds) else None
        paste_mutant_sprite(
            canvas,
            draw,
            cx=cx,
            cy=cy,
            kind=kind,
            diameter=56,
            wave=session.wave_mode,
        )
    colors = {session.challenger_id: (80, 200, 255), session.target_id: (255, 120, 90)}
    for pid in (session.challenger_id, session.target_id):
        px, py = session.pos(pid)
        cx = margin + px * cell + cell // 2
        cy = margin + py * cell + cell // 2
        ring = colors.get(pid, (200, 200, 200))
        viewer_cell = (margin, cell, px, py) if pid == viewer_id else None
        paste_player_avatar(
            canvas,
            draw,
            storage,
            pid=pid,
            cx=cx,
            cy=cy,
            diameter=52,
            ring_color=ring,
            hp=session.hp.get(str(pid), 0),
            is_active=pid == session.active_player(),
            viewer_cell=viewer_cell,
        )
    pl = margin + grid_px + 16
    draw.rounded_rectangle((pl, margin, width - margin, height - margin), radius=14, fill=(44, 46, 50), outline=(90, 94, 100), width=2)
    body = load_tactical_font(16)
    small = load_tactical_font(13)
    y = margin + 16
    title = f"Дуэль · {location[:16]}" if location else "Тактическая дуэль"
    draw.text((pl + 14, y), title, fill=(240, 240, 240), font=body)
    y += 28
    secs = _match_seconds_left(session)
    if session.wave_mode:
        draw.text((pl + 14, y), "ВОЛНА МУТАНТОВ", fill=(255, 100, 80), font=small)
    elif secs is not None:
        draw.text((pl + 14, y), f"До волны: {secs // 60}:{secs % 60:02d}", fill=(200, 200, 120), font=small)
    y += 20
    draw.text((pl + 14, y), "Синяя клетка = вы", fill=(120, 200, 230), font=small)
    y += 18
    for pid in (session.challenger_id, session.target_id):
        ch = storage.get_character(pid, refresh_energy=False)
        name = ch.nickname if ch else str(pid)
        hp = session.hp.get(str(pid), 0)
        mark = " < ход" if pid == session.active_player() else ""
        draw.text((pl + 14, y), f"{h(name)}{mark}: HP {hp}", fill=(210, 210, 210), font=small)
        y += 20
    y += 8
    for line in session.log[-8:]:
        draw.text((pl + 14, y), line[:42], fill=(170, 170, 170), font=small)
        y += 16
    draw_enemy_hud(
        canvas,
        hud_slots_from_kinds(session.mutant_kinds, []),
        panel_left=pl,
        panel_top=margin,
        panel_right=width - margin,
        panel_bottom=height - margin,
    )
    buf = BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()
