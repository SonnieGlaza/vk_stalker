from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.artifact_hunt import (
    FONT_CANDIDATES,
    _cover_crop,
    _load_location_thumb,
    _paste_circle,
    _paste_rounded,
)
from app.enemy_hud import default_hp_for_kind, draw_enemy_hud, hud_slots_from_units
from app.game_logic import (
    CONTRACT_TURN_IN_BONUS_PERCENT,
    QUESTS,
    QUEST_CONTRACTS,
    ActionResult,
    QuestContractTemplate,
    QuestType,
    _dead_block_text,
    _is_dead,
    _spend_quest_resources,
    apply_contract_mission_fail,
    apply_contract_mission_success,
    apply_incoming_damage,
    effective_max_health,
    equipment_power,
    faction_home_base,
    h,
    try_auto_turn_in_contract,
    use_medkit_item,
)
from app.tactical_combat import (
    collect_player_shot_hits,
    consume_shot_ammo,
    npc_weapon_damage,
    random_hostile_shots,
    weapon_damage,
    weapon_shoot_range,
)
from app.mutant_abilities import (
    BLOODSUCKER_KIND,
    DOG_KINDS,
    apply_mutant_turn_effects,
    mutant_attack_ability_tag,
    mutant_can_melee_attack,
    mutant_can_ranged_attack,
    mutant_chase_target,
    mutant_damage_multiplier,
    mutant_extra_move_step,
    mutant_extra_radiation,
    mutant_hostile_move_candidates,
    mutant_pick_move_step,
    mutant_should_move_when_chasing,
    mutant_survives_melee,
    relative_attack_side,
)
from app.mutant_assets import (
    MISSION_MUTANT_GRID_DIAMETER,
    MUTANT_SPRITE_KEYS,
    MUTANT_SPRITES,
    mutant_grid_diameter,
    mutant_sprite_image,
    pick_mutant_kind,
)
from app.mission_icons import (
    ANOMALY_ICON_KEY,
    MISSION_ICON_GRID_DIAMETER,
    OBJECTIVE_ICON_KEY,
    mission_icon_image,
)
from app.npc_assets import (
    MISSION_NPC_GRID_DIAMETER,
    NPC_SPRITE_KEYS,
    NPC_SPRITES,
    npc_sprite_image,
    pick_npc_kind,
)
from app.storage import Character, Storage

# Тир-2 стволы с дальностью 2 клетки (автоматы), как обещано в меню заданий.
QUEST_NPC_WEAPONS: tuple[str, ...] = ("АК-74", "ТРс-301", "ИЛ86")
# Сопровождение: вокруг только T1-стволы.
QUEST_ESCORT_NPC_WEAPONS: tuple[str, ...] = ("ПМ", "Фора-12", "Обрез")
QUEST_MONOLITH_NPC_WEAPONS: tuple[str, ...] = ("Гаусс-пушка", "Винтарь ВС", "АН-94", "ГП37")
TUSHKANO_MAX_MELEE_PER_TURN = 2


MISSION_META_PREFIX = "quest_mission:"
QUEST_ACTIVE_IDS_META = "quest_mission:active_ids"
QUEST_IDLE_HOURS = 4
GRID_SIZE = 6
MAX_MOVES = 28

MOVE_DELTAS: dict[str, tuple[int, int]] = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

# Опасность локации → урон/число врагов.
LOCATION_DANGER: dict[str, int] = {
    "Кордон": 1,
    "Свалка": 1,
    "Болото": 2,
    "НИИ Агропром": 2,
    "Росток": 2,
    "Армейские склады": 2,
    "Янтарь": 3,
    "Темная долина": 3,
    "Рыжий лес": 3,
    "Радар": 4,
    "Припять": 4,
    "ЧАЭС": 5,
}

DIFFICULTY_DANGER_BONUS: dict[str, int] = {
    "easy": 0,
    "hard": 1,
    "heavy": 2,
    "impossible": 3,
}

# Сложность → состав угроз на поле: аномалии / мутанты / НПС.
DIFFICULTY_THREATS: dict[str, tuple[bool, bool, bool]] = {
    "easy": (True, False, False),
    "hard": (True, True, False),
    "heavy": (True, True, True),
    "impossible": (True, True, True),
}

KIND_LABELS: dict[str, str] = {
    "collect": "Сбор",
    "scout": "Разведка",
    "loot": "Поиск хабара",
    "clear_mutant": "Зачистка мутантов",
    "clear_marauder": "Зачистка мародёров",
    "anomaly": "Аномалии",
    "escort": "Сопровождение",
    "deliver_junk": "Сдача мусора",
}


def _mission_kind_label(kind: str, title: str = "") -> str:
    title_s = str(title or "")
    title_l = title_s.lower()
    if title_s.startswith("Колонна Монолита"):
        return "Перехват колонны Монолита"
    if title_s.startswith("Дуэль: Тёмный сталкер"):
        return "Дуэль с Тёмным сталкером"
    if "вертушк" in title_l or title_s.startswith("Обломки вертушки"):
        return "Зачистка военных"
    if title_s.startswith("Гигант:"):
        return "Охота на псевдогиганта"
    return KIND_LABELS.get(kind, kind)

HOSTILE_MOVE_CHANCE = 0.5
OPPOSITE_DIRECTION = {"up": "down", "down": "up", "left": "right", "right": "left"}
PLAYER_LEFT_OF = {"up": "left", "down": "right", "left": "down", "right": "up"}
PLAYER_RIGHT_OF = {"up": "right", "down": "left", "left": "up", "right": "down"}
ATTACK_SIDE_LABEL = {
    "front": "⬆️ в лоб",
    "back": "⬇️ со спины",
    "left": "⬅️ слева",
    "right": "➡️ справа",
}
NPC_WEAPONS_BY_DIFFICULTY: dict[str, tuple[str, ...]] = {
    "easy": ("ПМ", "Фора-12", "Обрез"),
    "hard": ("ПМ", "АК-74", "Обрез", "СПАС-12"),
    "heavy": ("АК-74", "ТРс-301", "ИЛ86", "СПАС-12"),
    "impossible": ("ТРс-301", "ИЛ86", "АН-94", "СВДм-2"),
}
NPC_SHOOT_CHANCE_BY_DIFFICULTY = {"easy": 0.62, "hard": 0.72, "heavy": 0.80, "impossible": 0.88}
NPC_DAMAGE_MULT_BY_DIFFICULTY = {"easy": 1.0, "hard": 1.12, "heavy": 1.28, "impossible": 1.45}
DIFFICULTY_COMBAT_MULT = {"easy": 1.0, "hard": 1.1, "heavy": 1.22, "impossible": 1.38}


def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _normalize_facing(facing: str | None) -> str:
    key = str(facing or "down").strip().lower()
    return key if key in MOVE_DELTAS else "down"


def _relative_attack_side(
    player_facing: str,
    player_pos: tuple[int, int],
    attacker_pos: tuple[int, int],
) -> str:
    return relative_attack_side(player_facing, player_pos, attacker_pos)


def _attack_side_text(side: str) -> str:
    return ATTACK_SIDE_LABEL.get(side, "")


def _behind_player_cell(session: QuestMissionSession | Any) -> tuple[int, int]:
    facing = _normalize_facing(getattr(session, "player_facing", "down"))
    dx, dy = MOVE_DELTAS[OPPOSITE_DIRECTION[facing]]
    px, py = session.player
    return (px + dx, py + dy)


def _npc_weapon_pool(difficulty: str) -> tuple[str, ...]:
    return NPC_WEAPONS_BY_DIFFICULTY.get(difficulty, QUEST_NPC_WEAPONS)


def _npc_shoot_chance(session: QuestMissionSession | Any) -> float:
    diff = str(getattr(session, "difficulty", "") or "easy")
    return float(NPC_SHOOT_CHANCE_BY_DIFFICULTY.get(diff, 0.62))


def _npc_damage_mult(session: QuestMissionSession | Any) -> float:
    diff = str(getattr(session, "difficulty", "") or "easy")
    return float(NPC_DAMAGE_MULT_BY_DIFFICULTY.get(diff, 1.0))


def _mutants_chase_player(session: QuestMissionSession | Any) -> bool:
    if bool(getattr(session, "mutant_chase", False)):
        return True
    return bool(getattr(session, "enemies", None))


def _npcs_chase_player(session: QuestMissionSession | Any) -> bool:
    return str(getattr(session, "difficulty", "") or "") in {"heavy", "impossible"}


# Живучесть врагов: базовая таблица HP_BY_KIND × сложность (+ опасная локация).
# До фикса врагов снимал любой удачный удар — фактически «по 1 HP».
ENEMY_HP_DIFF_MULT: dict[str, float] = {
    "easy": 0.8,
    "hard": 1.0,
    "heavy": 1.15,
    "impossible": 1.3,
}


def enemy_max_hp_for_kind(kind: str, difficulty: str, location: str = "") -> int:
    """Полный HP врага контракта: таблица default_hp_for_kind × сложность/локация."""
    base = default_hp_for_kind(kind)
    mult = float(ENEMY_HP_DIFF_MULT.get(str(difficulty), 1.0))
    mult *= 1.0 + 0.05 * max(0, LOCATION_DANGER.get(str(location), 2) - 2)
    return max(1, int(round(base * mult)))


def _unit_hp_lists(
    kinds: list[str],
    difficulty: str,
    location: str,
) -> tuple[list[int], list[int]]:
    """Полные и максимальные HP для списка юнитов (при спавне — полные)."""
    values = [enemy_max_hp_for_kind(kind, difficulty, location) for kind in kinds]
    return list(values), list(values)


@dataclass
class QuestMissionSession:
    contract_key: str
    title: str
    location: str
    kind: str
    difficulty: str
    player: tuple[int, int]
    start: tuple[int, int]
    player_facing: str = "down"
    objectives: list[tuple[int, int]] = field(default_factory=list)
    collected: list[tuple[int, int]] = field(default_factory=list)
    hazards: list[tuple[int, int]] = field(default_factory=list)  # аномалии
    enemies: list[tuple[int, int]] = field(default_factory=list)  # мутанты
    enemy_kinds: list[str] = field(default_factory=list)  # blind_dog, tushkano, …
    npcs: list[tuple[int, int]] = field(default_factory=list)  # мародёры / НПС
    npc_kinds: list[str] = field(default_factory=list)  # maloy, …
    npc_weapons: list[str] = field(default_factory=list)
    enemy_hp: list[int] = field(default_factory=list)  # текущий HP мутантов
    enemy_max_hp: list[int] = field(default_factory=list)
    npc_hp: list[int] = field(default_factory=list)  # текущий HP НПС
    npc_max_hp: list[int] = field(default_factory=list)
    escort: tuple[int, int] | None = None
    escort_kind: str = ""
    escort_alive: bool = False
    moves: int = 0
    max_moves: int = MAX_MOVES
    grid: int = GRID_SIZE
    objectives_done: bool = False
    resources_spent: bool = False
    turn_seq: int = 0
    started_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_key": self.contract_key,
            "title": self.title,
            "location": self.location,
            "kind": self.kind,
            "difficulty": self.difficulty,
            "player": list(self.player),
            "start": list(self.start),
            "player_facing": self.player_facing,
            "objectives": [list(p) for p in self.objectives],
            "collected": [list(p) for p in self.collected],
            "hazards": [list(p) for p in self.hazards],
            "enemies": [list(p) for p in self.enemies],
            "enemy_kinds": list(self.enemy_kinds),
            "npcs": [list(p) for p in self.npcs],
            "npc_kinds": list(self.npc_kinds),
            "npc_weapons": list(self.npc_weapons),
            "enemy_hp": [int(x) for x in self.enemy_hp],
            "enemy_max_hp": [int(x) for x in self.enemy_max_hp],
            "npc_hp": [int(x) for x in self.npc_hp],
            "npc_max_hp": [int(x) for x in self.npc_max_hp],
            "escort": list(self.escort) if self.escort is not None else None,
            "escort_kind": self.escort_kind,
            "escort_alive": self.escort_alive,
            "moves": self.moves,
            "max_moves": self.max_moves,
            "grid": self.grid,
            "objectives_done": self.objectives_done,
            "resources_spent": self.resources_spent,
            "turn_seq": self.turn_seq,
            "started_at": self.started_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> QuestMissionSession:
        escort_raw = raw.get("escort")
        escort: tuple[int, int] | None = None
        if isinstance(escort_raw, (list, tuple)) and len(escort_raw) >= 2:
            escort = (int(escort_raw[0]), int(escort_raw[1]))
        enemies = [(int(p[0]), int(p[1])) for p in (raw.get("enemies") or [])]
        enemy_kinds = _parse_enemy_kinds(raw.get("enemies") or [], raw.get("enemy_kinds"))
        npcs = [(int(p[0]), int(p[1])) for p in (raw.get("npcs") or [])]
        npc_kinds = _parse_npc_kinds(raw.get("npcs") or [], raw.get("npc_kinds"))
        parsed_enemy_hp, parsed_enemy_max_hp = _parse_unit_hp(
            len(enemies),
            enemy_kinds,
            raw.get("enemy_hp"),
            raw.get("enemy_max_hp"),
        )
        parsed_npc_hp, parsed_npc_max_hp = _parse_unit_hp(
            len(npcs),
            npc_kinds,
            raw.get("npc_hp"),
            raw.get("npc_max_hp"),
        )
        return cls(
            contract_key=str(raw.get("contract_key") or ""),
            title=str(raw.get("title") or ""),
            location=str(raw.get("location") or ""),
            kind=str(raw.get("kind") or "collect"),
            difficulty=str(raw.get("difficulty") or "easy"),
            player=(int(raw["player"][0]), int(raw["player"][1])),
            start=(int(raw["start"][0]), int(raw["start"][1])),
            player_facing=_normalize_facing(str(raw.get("player_facing") or "down")),
            objectives=[(int(p[0]), int(p[1])) for p in (raw.get("objectives") or [])],
            collected=[(int(p[0]), int(p[1])) for p in (raw.get("collected") or [])],
            hazards=[(int(p[0]), int(p[1])) for p in (raw.get("hazards") or [])],
            enemies=enemies,
            enemy_kinds=enemy_kinds,
            npcs=npcs,
            npc_kinds=npc_kinds,
            npc_weapons=_parse_npc_weapons(raw.get("npcs") or [], raw.get("npc_weapons")),
            enemy_hp=parsed_enemy_hp,
            enemy_max_hp=parsed_enemy_max_hp,
            npc_hp=parsed_npc_hp,
            npc_max_hp=parsed_npc_max_hp,
            escort=escort,
            escort_kind=str(raw.get("escort_kind") or ""),
            escort_alive=bool(raw.get("escort_alive")),
            moves=int(raw.get("moves") or 0),
            max_moves=int(raw.get("max_moves") or MAX_MOVES),
            grid=int(raw.get("grid") or GRID_SIZE),
            objectives_done=bool(raw.get("objectives_done")),
            resources_spent=bool(raw.get("resources_spent")),
            turn_seq=int(raw.get("turn_seq") or 0),
            started_at=raw.get("started_at"),
        )


def _parse_enemy_kinds(enemies_raw: list, kinds_raw: Any) -> list[str]:
    n = len(enemies_raw)
    if not n:
        return []
    if isinstance(kinds_raw, list) and len(kinds_raw) == n:
        parsed: list[str] = []
        for i, k in enumerate(kinds_raw):
            key = str(k)
            parsed.append(
                key if key in MUTANT_SPRITES else MUTANT_SPRITE_KEYS[i % len(MUTANT_SPRITE_KEYS)]
            )
        return parsed
    return [MUTANT_SPRITE_KEYS[i % len(MUTANT_SPRITE_KEYS)] for i in range(n)]


def _parse_npc_weapons(npcs_raw: list, weapons_raw: Any) -> list[str]:
    n = len(npcs_raw)
    if not n:
        return []
    if isinstance(weapons_raw, list) and len(weapons_raw) == n:
        return [str(w) for w in weapons_raw]
    return [random.choice(QUEST_NPC_WEAPONS) for _ in range(n)]


def _parse_npc_kinds(npcs_raw: list, kinds_raw: Any) -> list[str]:
    n = len(npcs_raw)
    if not n:
        return []
    if isinstance(kinds_raw, list) and len(kinds_raw) == n:
        parsed: list[str] = []
        for i, k in enumerate(kinds_raw):
            key = str(k)
            parsed.append(key if key in NPC_SPRITES else NPC_SPRITE_KEYS[i % len(NPC_SPRITE_KEYS)])
        return parsed
    return [NPC_SPRITE_KEYS[i % len(NPC_SPRITE_KEYS)] for i in range(n)]


def _parse_unit_hp(
    n: int,
    kinds: list[str],
    hp_raw: Any,
    max_raw: Any,
) -> tuple[list[int], list[int]]:
    """HP юнитов по таблице default_hp_for_kind.

    Старые сессии без HP-списков (или с рассинхроном длины) получают полные
    значения из таблицы — а не «1 HP», из-за которого враги умирали мгновенно.
    """
    if n <= 0:
        return [], []
    defaults = [default_hp_for_kind(kinds[i] if i < len(kinds) else "") for i in range(n)]

    def _nums(raw: Any) -> list[int]:
        if not isinstance(raw, (list, tuple)) or len(raw) != n:
            return []
        out: list[int] = []
        for value in raw:
            try:
                out.append(max(0, int(value)))
            except (TypeError, ValueError):
                out.append(0)
        return out

    hp = _nums(hp_raw)
    if not hp:
        hp = list(defaults)
    mx = _nums(max_raw)
    if not mx:
        mx = [max(current, full) for current, full in zip(hp, defaults)]
    return hp, mx


def _meta_key(telegram_id: int) -> str:
    return f"{MISSION_META_PREFIX}{int(telegram_id)}"


def get_mission_session(storage: Storage, telegram_id: int) -> QuestMissionSession | None:
    raw = storage.get_meta(_meta_key(telegram_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return QuestMissionSession.from_dict(data)
    except Exception:
        storage.delete_meta(_meta_key(telegram_id))
        return None


def save_mission_session(storage: Storage, telegram_id: int, session: QuestMissionSession) -> None:
    storage.set_meta(_meta_key(telegram_id), json.dumps(session.to_dict(), ensure_ascii=False))
    _register_active_quest(storage, telegram_id)


def clear_mission_session(storage: Storage, telegram_id: int) -> None:
    storage.delete_meta(_meta_key(telegram_id))
    _unregister_active_quest(storage, telegram_id)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _register_active_quest(storage: Storage, telegram_id: int) -> None:
    raw = storage.get_meta(QUEST_ACTIVE_IDS_META)
    ids: list[int] = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                ids = [int(x) for x in parsed]
        except json.JSONDecodeError:
            ids = []
    tid = int(telegram_id)
    if tid not in ids:
        ids.append(tid)
    storage.set_meta(QUEST_ACTIVE_IDS_META, json.dumps(ids, ensure_ascii=False))


def _unregister_active_quest(storage: Storage, telegram_id: int) -> None:
    raw = storage.get_meta(QUEST_ACTIVE_IDS_META)
    if not raw:
        return
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return
        ids = [int(x) for x in parsed if int(x) != int(telegram_id)]
    except json.JSONDecodeError:
        return
    if ids:
        storage.set_meta(QUEST_ACTIVE_IDS_META, json.dumps(ids, ensure_ascii=False))
    else:
        storage.delete_meta(QUEST_ACTIVE_IDS_META)


def list_active_quest_player_ids(storage: Storage) -> list[int]:
    raw = storage.get_meta(QUEST_ACTIVE_IDS_META)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [int(x) for x in parsed]
    except json.JSONDecodeError:
        pass
    return []


def check_quest_session_timeout(storage: Storage, telegram_id: int) -> ActionResult | None:
    session = get_mission_session(storage, telegram_id)
    if session is None:
        _unregister_active_quest(storage, telegram_id)
        return None
    if session.moves >= session.max_moves:
        clear_mission_session(storage, telegram_id)
        quest = QUESTS.get(session.difficulty) or QUESTS["easy"]
        fail_result = apply_contract_mission_fail(
            storage,
            telegram_id,
            quest=quest,
            work_location=session.location,
            title=session.title,
            reason="Время вылазки вышло.",
        )
        storage.set_active_contract(telegram_id, None)
        return ActionResult(
            False,
            fail_result.text,
            payload={"mission_active": False, "mission_done": True, "quest_timeout": True},
        )
    if session.started_at:
        try:
            started = datetime.fromisoformat(str(session.started_at))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
        except ValueError:
            started = _utc_now()
        if _utc_now() > started + timedelta(hours=QUEST_IDLE_HOURS):
            clear_mission_session(storage, telegram_id)
            quest = QUESTS.get(session.difficulty) or QUESTS["easy"]
            fail_result = apply_contract_mission_fail(
                storage,
                telegram_id,
                quest=quest,
                work_location=session.location,
                title=session.title,
                reason="Вылазка заброшена — слишком долго без движения.",
            )
            storage.set_active_contract(telegram_id, None)
            return ActionResult(
                False,
                fail_result.text,
                payload={"mission_active": False, "mission_done": True, "quest_timeout": True},
            )
    return None


def process_quest_timeouts(storage: Storage) -> list[tuple[int, ActionResult]]:
    outcomes: list[tuple[int, ActionResult]] = []
    for telegram_id in list_active_quest_player_ids(storage):
        result = check_quest_session_timeout(storage, telegram_id)
        if result is not None:
            outcomes.append((telegram_id, result))
    return outcomes


def _save_mission_if_turn_ok(
    storage: Storage,
    telegram_id: int,
    session: QuestMissionSession,
    expected_seq: int,
) -> bool:
    from app.tactical_turn import save_turn_if_seq_ok

    tid = int(telegram_id)
    return save_turn_if_seq_ok(
        storage,
        meta_key=_meta_key(tid),
        session=session,
        from_dict=QuestMissionSession.from_dict,
        save_fn=lambda st, sess: save_mission_session(st, tid, sess),
        expected_seq=expected_seq,
    )


def _location_danger(location: str, difficulty: str) -> int:
    return LOCATION_DANGER.get(location, 2) + DIFFICULTY_DANGER_BONUS.get(difficulty, 0)


def _difficulty_threat_flags(difficulty: str, kind: str) -> tuple[bool, bool, bool]:
    """Аномалии / мутанты / НПС по сложности (+ принудительно для зачисток/эскорта)."""
    if kind == "escort":
        # Аномалии всегда 100%. Вокруг — либо мутанты, либо T1-НПС (выбирается при спавне).
        return True, True, True
    anom, mutants, npcs = DIFFICULTY_THREATS.get(difficulty, (True, False, False))
    if kind == "clear_mutant":
        mutants = True
    if kind == "clear_marauder":
        # Зачистка людей: только НПС, без мутантов (даже на hard/heavy).
        npcs = True
        mutants = False
    return anom, mutants, npcs


def _free_cell(grid: int, forbidden: set[tuple[int, int]]) -> tuple[int, int]:
    free = [(x, y) for x in range(grid) for y in range(grid) if (x, y) not in forbidden]
    if not free:
        return 0, 0
    return random.choice(free)


def _farthest_cell(grid: int, origin: tuple[int, int], forbidden: set[tuple[int, int]]) -> tuple[int, int]:
    best: tuple[int, int] | None = None
    best_d = -1
    for x in range(grid):
        for y in range(grid):
            cell = (x, y)
            if cell in forbidden:
                continue
            d = _manhattan(origin, cell)
            if d > best_d:
                best_d = d
                best = cell
    return best if best is not None else _free_cell(grid, forbidden)


def _adjacent_cells(pos: tuple[int, int], grid: int) -> list[tuple[int, int]]:
    x, y = pos
    cells: list[tuple[int, int]] = []
    for dx, dy in MOVE_DELTAS.values():
        nx, ny = x + dx, y + dy
        if 0 <= nx < grid and 0 <= ny < grid:
            cells.append((nx, ny))
    return cells


def _spawn_n(
    n: int,
    grid: int,
    forbidden: set[tuple[int, int]],
    into: list[tuple[int, int]],
) -> None:
    for _ in range(max(0, n)):
        cell = _free_cell(grid, forbidden)
        into.append(cell)
        forbidden.add(cell)


def _spawn_mutants(
    n: int,
    grid: int,
    forbidden: set[tuple[int, int]],
    enemies: list[tuple[int, int]],
    kinds: list[str],
) -> None:
    from app.mutant_assets import ensure_single_controller

    for _ in range(max(0, n)):
        cell = _free_cell(grid, forbidden)
        enemies.append(cell)
        kinds.append(pick_mutant_kind(allow_controller="controller" not in kinds))
        forbidden.add(cell)
    kinds[:] = ensure_single_controller(kinds)


def _spawn_npcs(
    n: int,
    grid: int,
    forbidden: set[tuple[int, int]],
    npcs: list[tuple[int, int]],
    kinds: list[str],
    weapons: list[str],
    *,
    marauder: bool = False,
    weapon_pool: tuple[str, ...] | None = None,
    difficulty: str = "hard",
    fixed_kind: str | None = None,
) -> None:
    pool = weapon_pool or _npc_weapon_pool(difficulty)
    for _ in range(max(0, n)):
        cell = _free_cell(grid, forbidden)
        npcs.append(cell)
        if fixed_kind:
            kinds.append(fixed_kind)
        else:
            kinds.append(pick_npc_kind(marauder=marauder))
        weapons.append(random.choice(pool))
        forbidden.add(cell)


def _pick_monolith_weapon() -> str:
    return random.choices(
        QUEST_MONOLITH_NPC_WEAPONS,
        weights=(45, 25, 20, 10),
        k=1,
    )[0]


def _spawn_monolith_npcs(
    n: int,
    grid: int,
    forbidden: set[tuple[int, int]],
    npcs: list[tuple[int, int]],
    kinds: list[str],
    weapons: list[str],
) -> None:
    for _ in range(max(0, n)):
        cell = _free_cell(grid, forbidden)
        npcs.append(cell)
        kinds.append("monolith")
        weapons.append(_pick_monolith_weapon())
        forbidden.add(cell)


def _mirror_weapon_for_dark_stalker(player: Character) -> str:
    weapon = str(player.equipment.get("weapon") or "ПМ")
    if weapon in {"", "Нож"}:
        return "ПМ"
    return weapon


def _apply_special_mission_spawn_overrides(
    template: QuestContractTemplate,
    session: QuestMissionSession,
    player: Character,
) -> None:
    title = str(template.title or "")
    if title.startswith("Дуэль: Тёмный сталкер"):
        mirror_weapon = _mirror_weapon_for_dark_stalker(player)
        session.npcs = session.npcs[:1]
        session.npc_kinds = ["dark_stalker"] if session.npcs else []
        session.npc_weapons = [mirror_weapon] if session.npcs else []
        session.npc_hp, session.npc_max_hp = _unit_hp_lists(
            session.npc_kinds, session.difficulty, session.location
        )
        return
    if title.startswith("Колонна Монолита"):
        for i in range(len(session.npc_kinds)):
            session.npc_kinds[i] = "monolith"
        for i in range(len(session.npc_weapons)):
            session.npc_weapons[i] = _pick_monolith_weapon()
        session.npc_hp, session.npc_max_hp = _unit_hp_lists(
            session.npc_kinds, session.difficulty, session.location
        )


def _build_session(template: QuestContractTemplate, quest: QuestType) -> QuestMissionSession:
    kind = template.mission_kind
    difficulty = template.difficulty
    danger = _location_danger(template.work_location, difficulty)
    grid = GRID_SIZE
    start = (random.randrange(grid), random.randrange(grid))
    forbidden: set[tuple[int, int]] = {start}

    objectives: list[tuple[int, int]] = []
    hazards: list[tuple[int, int]] = []
    enemies: list[tuple[int, int]] = []
    enemy_kinds: list[str] = []
    npcs: list[tuple[int, int]] = []
    npc_kinds: list[str] = []
    npc_weapons: list[str] = []
    escort: tuple[int, int] | None = None
    escort_kind = ""
    escort_alive = False

    # Цели задания — по типу контракта.
    if kind in {"clear_mutant", "clear_marauder"}:
        pass  # цель = зачистить соответствующих врагов
    elif kind == "escort":
        dest = _farthest_cell(grid, start, forbidden)
        objectives.append(dest)
        forbidden.add(dest)
        adj = [c for c in _adjacent_cells(start, grid) if c not in forbidden]
        escort = random.choice(adj) if adj else _free_cell(grid, forbidden)
        forbidden.add(escort)
        escort_kind = pick_npc_kind(marauder=False)
        escort_alive = True
    elif kind == "scout":
        objectives.append(_free_cell(grid, forbidden))
        forbidden.add(objectives[0])
    elif kind == "anomaly":
        _spawn_n(3, grid, forbidden, objectives)
    elif kind == "loot":
        _spawn_n(2 + (1 if danger >= 3 else 0), grid, forbidden, objectives)
    else:
        # collect / fallback
        obj_n = 2 if difficulty == "easy" else 3
        _spawn_n(obj_n, grid, forbidden, objectives)

    # Угрозы — строго по сложности (+ добор для зачисток / эскорта).
    want_anom, want_mut, want_npc = _difficulty_threat_flags(difficulty, kind)
    base_n = 2 + max(0, danger // 2)
    giant_hunt = "Гигант" in str(template.title or "")
    heli_crash = "вертушк" in str(template.title or "").lower() or str(template.title or "").startswith(
        "Обломки вертушки"
    )
    monolith_march = str(template.title or "").startswith("Колонна Монолита")
    dark_stalker_duel = str(template.title or "").startswith("Дуэль: Тёмный сталкер")
    monolith_escort = "пленн" in str(template.title or "").lower() or "Монолит" in str(
        template.title or ""
    )
    if dark_stalker_duel:
        want_mut = False
        want_npc = True
    if monolith_march:
        want_mut = False
        want_npc = True
    if kind == "escort":
        # Аномалии всегда; вокруг — мутанты ИЛИ T1-НПС.
        anom_n = base_n + 1 + (2 if monolith_escort else 0)
        _spawn_n(anom_n, grid, forbidden, hazards)
        host_n = base_n + (2 if monolith_escort else 0)
        # Спасение пленного — плотные атаки: мутанты + мародёры Монолита.
        if monolith_escort or random.random() < 0.5:
            _spawn_mutants(host_n, grid, forbidden, enemies, enemy_kinds)
            if monolith_escort:
                _spawn_monolith_npcs(
                    max(2, host_n // 2),
                    grid,
                    forbidden,
                    npcs,
                    npc_kinds,
                    npc_weapons,
                )
        else:
            _spawn_npcs(
                host_n,
                grid,
                forbidden,
                npcs,
                npc_kinds,
                npc_weapons,
                marauder=True,
                weapon_pool=QUEST_ESCORT_NPC_WEAPONS,
            )
    else:
        if want_anom:
            anom_n = base_n + (1 if kind == "anomaly" else 0)
            _spawn_n(anom_n, grid, forbidden, hazards)
        if want_mut:
            mut_n = base_n + (1 if kind == "clear_mutant" else 0)
            if giant_hunt:
                mut_n += 3
            _spawn_mutants(mut_n, grid, forbidden, enemies, enemy_kinds)
            if giant_hunt and enemy_kinds:
                # Босс — псевдогигант; остальные — бюреры и зомбированные.
                enemy_kinds[0] = "giant"
                for i in range(1, len(enemy_kinds)):
                    if i <= 2:
                        enemy_kinds[i] = "burer"
                    elif i <= 5:
                        enemy_kinds[i] = "zombie"
        if want_npc:
            if dark_stalker_duel:
                npc_n = 1
                _spawn_npcs(
                    npc_n,
                    grid,
                    forbidden,
                    npcs,
                    npc_kinds,
                    npc_weapons,
                    marauder=False,
                    fixed_kind="dark_stalker",
                    difficulty=difficulty,
                )
            elif monolith_march:
                npc_n = max(base_n + 2, 5 if difficulty == "heavy" else 4)
                _spawn_monolith_npcs(npc_n, grid, forbidden, npcs, npc_kinds, npc_weapons)
            else:
                npc_n = base_n + (1 if kind == "clear_marauder" else 0)
                if heli_crash:
                    npc_n = max(npc_n, base_n + 2)
                _spawn_npcs(
                    npc_n,
                    grid,
                    forbidden,
                    npcs,
                    npc_kinds,
                    npc_weapons,
                    marauder=False if heli_crash else (kind == "clear_marauder" or want_npc),
                    difficulty=difficulty,
                )
                if heli_crash and npc_kinds:
                    # Охрана обломков — только военные.
                    for i in range(len(npc_kinds)):
                        npc_kinds[i] = "soldier"

    enemy_hp, enemy_max_hp = _unit_hp_lists(enemy_kinds, difficulty, template.work_location)
    npc_hp, npc_max_hp = _unit_hp_lists(npc_kinds, difficulty, template.work_location)
    return QuestMissionSession(
        contract_key=template.key,
        title=template.title,
        location=template.work_location,
        kind=kind,
        difficulty=difficulty,
        player=start,
        start=start,
        objectives=objectives,
        hazards=hazards,
        enemies=enemies,
        enemy_kinds=enemy_kinds,
        npcs=npcs,
        npc_kinds=npc_kinds,
        npc_weapons=npc_weapons,
        enemy_hp=enemy_hp,
        enemy_max_hp=enemy_max_hp,
        npc_hp=npc_hp,
        npc_max_hp=npc_max_hp,
        escort=escort,
        escort_kind=escort_kind,
        escort_alive=escort_alive,
        max_moves=MAX_MOVES + danger,
        resources_spent=False,
        started_at=_utc_now().isoformat(),
    )


def _objectives_complete(session: QuestMissionSession) -> bool:
    if session.kind == "clear_mutant":
        return len(session.enemies) == 0
    if session.kind == "clear_marauder":
        return len(session.npcs) == 0
    remaining = [p for p in session.objectives if p not in session.collected]
    return len(remaining) == 0


def _occupied_for_hostile_move(session: QuestMissionSession) -> set[tuple[int, int]]:
    """Клетки, на которые врагам лучше не становиться (кроме клетки игрока)."""
    blocked: set[tuple[int, int]] = set(session.hazards)
    blocked.update(session.enemies)
    blocked.update(session.npcs)
    return blocked


def _move_hostile_units(
    units: list[tuple[int, int]],
    session: QuestMissionSession,
    kinds: list[str] | None = None,
    *,
    chase_player: bool = False,
    allow_player_cell: bool = True,
) -> tuple[list[tuple[int, int]], list[str] | None, int, list[tuple[str, tuple[int, int]]]]:
    """Сдвинуть юниты на соседнюю клетку (у каждого типа — своя «абилка» движения)."""
    moved = 0
    result: list[tuple[int, int]] = []
    result_kinds: list[str] = []
    onto_player: list[tuple[str, tuple[int, int]]] = []
    occupied = _occupied_for_hostile_move(session)
    player_pos = session.player
    for i, pos in enumerate(units):
        kind = kinds[i] if kinds is not None and i < len(kinds) else None
        occupied.discard(pos)
        if not chase_player and random.random() >= HOSTILE_MOVE_CHANCE:
            result.append(pos)
            if kind is not None:
                result_kinds.append(kind)
            occupied.add(pos)
            continue
        if chase_player and not mutant_should_move_when_chasing(session, kind, pos):
            result.append(pos)
            if kind is not None:
                result_kinds.append(kind)
            occupied.add(pos)
            continue

        candidates: list[tuple[int, int]] = []
        for cell in mutant_hostile_move_candidates(kind, pos, session.grid):
            if cell == player_pos:
                if allow_player_cell and not chase_player:
                    candidates.append(cell)
                continue
            if cell in occupied:
                continue
            candidates.append(cell)

        if not candidates:
            result.append(pos)
            if kind is not None:
                result_kinds.append(kind)
            occupied.add(pos)
            continue

        nxt: tuple[int, int] | None = None
        if chase_player:
            target = mutant_chase_target(session, kind)
            nxt = mutant_pick_move_step(session, kind, pos, target, candidates)
            if nxt is None:
                result.append(pos)
                if kind is not None:
                    result_kinds.append(kind)
                occupied.add(pos)
                continue
            extra = mutant_extra_move_step(
                kind,
                pos,
                nxt,
                target,
                grid=session.grid,
                occupied=occupied | {player_pos},
                player_pos=player_pos,
            )
            if extra is not None:
                occupied.add(nxt)
                nxt = extra
        else:
            nxt = random.choice(candidates)

        result.append(nxt)
        if kind is not None:
            result_kinds.append(kind)
        occupied.add(nxt)
        if nxt != pos:
            moved += 1
        if nxt == player_pos and kind is not None:
            onto_player.append((kind, pos))
    out_kinds = result_kinds if kinds is not None else None
    return result, out_kinds, moved, onto_player


def _maybe_npc_shots(
    storage: Storage,
    telegram_id: int,
    session: QuestMissionSession,
    player: Character,
) -> tuple[list[str], int]:
    if not session.npcs:
        return [], 0
    weapons = session.npc_weapons
    if len(weapons) != len(session.npcs):
        weapons = [random.choice(_npc_weapon_pool(session.difficulty)) for _ in session.npcs]
    hp_map = {str(telegram_id): player.health}
    notes = random_hostile_shots(
        list(session.npcs),
        weapons,
        grid=session.grid,
        player_positions={telegram_id: session.player},
        player_hp=hp_map,
        player_characters={telegram_id: player},
        cover=set(),
        base_cover=set(),
        damage_fn=lambda w: npc_weapon_damage(w, mult=_npc_damage_mult(session)),
        shoot_chance=_npc_shoot_chance(session),
        aim_at_players=True,
    )
    new_hp = int(hp_map.get(str(telegram_id), player.health))
    return notes, max(0, player.health - new_hp)


def _maybe_move_hostiles(
    session: QuestMissionSession,
    storage: Storage | None = None,
    telegram_id: int | None = None,
) -> tuple[list[str], list[tuple[str, tuple[int, int]]]]:
    notes: list[str] = []
    onto_player: list[tuple[str, tuple[int, int]]] = []
    chase = _mutants_chase_player(session)
    session.enemies, session.enemy_kinds, mut_moved, mut_onto = _move_hostile_units(
        list(session.enemies),
        session,
        list(session.enemy_kinds),
        chase_player=chase,
        allow_player_cell=not chase,
    )
    onto_player.extend(mut_onto)
    npc_chase = _npcs_chase_player(session)
    session.npcs, session.npc_kinds, npc_moved, npc_onto = _move_hostile_units(
        list(session.npcs),
        session,
        list(session.npc_kinds),
        chase_player=npc_chase,
        allow_player_cell=not npc_chase,
    )
    onto_player.extend(npc_onto)
    if mut_moved:
        notes.append(f"Мутанты идут на тебя ({mut_moved}).")
    if npc_moved:
        if npc_chase:
            notes.append(f"НПС сдвигаются к тебе ({npc_moved}).")
        else:
            notes.append(f"НПС сдвинулись ({npc_moved}).")
    if storage is not None and telegram_id is not None:
        from app.mutant_assets import apply_controller_aura_db

        notes.extend(apply_controller_aura_db(storage, [telegram_id], session.enemy_kinds))
    if storage is not None and telegram_id is not None:
        notes.extend(apply_mutant_turn_effects(storage, telegram_id, session))
    return notes, onto_player


def _mutant_can_adjacent_attack(
    session: QuestMissionSession,
    kind: str,
    enemy_pos: tuple[int, int],
) -> bool:
    return mutant_can_melee_attack(session, kind, enemy_pos)


def _apply_unit_damage(
    session: QuestMissionSession,
    idx: int,
    dmg: int,
    *,
    npc: bool,
) -> tuple[int, int]:
    """Снять HP у юнита. Возвращает (остаток, максимум).

    HP всегда из таблицы default_hp_for_kind: рассинхрон списков достраивается
    полными значениями, а не единицей.
    """
    if npc:
        hp_list, max_list, kinds = session.npc_hp, session.npc_max_hp, session.npc_kinds
    else:
        hp_list, max_list, kinds = session.enemy_hp, session.enemy_max_hp, session.enemy_kinds
    full = enemy_max_hp_for_kind(
        kinds[idx] if idx < len(kinds) else "", session.difficulty, session.location
    )
    while len(hp_list) <= idx:
        hp_list.append(full)
    while len(max_list) <= idx:
        max_list.append(full)
    hp_list[idx] = max(0, int(hp_list[idx]) - max(0, int(dmg)))
    max_list[idx] = max(1, int(max_list[idx]))
    return int(hp_list[idx]), int(max_list[idx])


def _remove_enemy_at_index(session: QuestMissionSession, idx: int) -> str:
    pos = session.enemies[idx]
    kind = session.enemy_kinds[idx] if idx < len(session.enemy_kinds) else ""
    session.enemies.pop(idx)
    if idx < len(session.enemy_kinds):
        session.enemy_kinds.pop(idx)
    if idx < len(session.enemy_hp):
        session.enemy_hp.pop(idx)
    if idx < len(session.enemy_max_hp):
        session.enemy_max_hp.pop(idx)
    from app.death_flavor import killer_label_for_kind

    return killer_label_for_kind(kind, npc=False) if kind else "мутанта"


def _apply_mutant_hit(
    storage: Storage,
    telegram_id: int,
    session: QuestMissionSession,
    player: Character,
    kind: str,
    attacker_pos: tuple[int, int],
    *,
    prior_damage: int,
    pending: int,
    ranged: bool = False,
) -> tuple[int, str | None, ActionResult | None]:
    """Один удар мутанта: урон, рад, текст. Возвращает (new_pending, note, death)."""
    from app.death_flavor import encounter_phrase_for_kind, killer_label_for_kind

    side = _relative_attack_side(session.player_facing, session.player, attacker_pos)
    dmg = _combat_damage(
        session.location,
        session.difficulty,
        player,
        kind=kind,
        hit_side=side,
        npc=False,
        enemy_kinds=session.enemy_kinds,
    )
    if ranged:
        dmg = max(1, int(dmg * 0.72))
    pending += dmg
    rad = mutant_extra_radiation(kind, side)
    if rad > 0:
        storage.adjust_survival(telegram_id, radiation_delta=rad)
    phrase = encounter_phrase_for_kind(kind, npc=False)
    side_txt = _attack_side_text(side)
    ability = mutant_attack_ability_tag(kind, ranged=ranged)
    rad_txt = f" ☢+{rad}" if rad > 0 else ""
    note = f"{'Удар' if ranged else 'Бой'} {phrase} ({side_txt}){ability}: −{dmg} HP.{rad_txt}"
    if int(player.health) - max(0, int(prior_damage)) - pending <= 0:
        from app.game_logic import remember_death_cause, remember_death_killer

        remember_death_cause(storage, telegram_id, "mutant")
        remember_death_killer(storage, telegram_id, killer_label_for_kind(kind, npc=False))
        return (
            pending,
            note,
            ActionResult(
                False,
                f"Ты пал в бою на «{session.location}».\nКонтракт сорван.",
                payload={
                    "mission_active": False,
                    "mission_dead": True,
                    "death_location": session.location,
                    "death_cause": "mutant",
                },
            ),
        )
    return pending, note, None


def _resolve_mutant_adjacent_attacks(
    storage: Storage,
    telegram_id: int,
    session: QuestMissionSession,
    player: Character,
    *,
    prior_damage: int = 0,
    onto_player: list[tuple[str, tuple[int, int]]] | None = None,
) -> tuple[list[str], int, ActionResult | None]:
    """Удары мутантов: дальний (бюрер/гигант), с соседней клетки и заход на игрока."""
    from app.combat_loot import grant_combat_loot
    from app.death_flavor import killer_label_for_kind

    notes: list[str] = []
    pending = 0
    death_result: ActionResult | None = None
    tushkano_melee_hits = 0

    for kind, from_pos in onto_player or []:
        if kind == "tushkano" and tushkano_melee_hits >= TUSHKANO_MAX_MELEE_PER_TURN:
            continue
        if kind == "tushkano":
            tushkano_melee_hits += 1
        pending, note, death_result = _apply_mutant_hit(
            storage,
            telegram_id,
            session,
            player,
            kind,
            from_pos,
            prior_damage=prior_damage,
            pending=pending,
        )
        if note:
            # Зашедшего на клетку мутанта снимает обмен в контакте ниже
            # (_fight_on_cell → _resolve_hostile_contact): он больше не умирает мгновенно.
            notes.append(note)
        if death_result is not None:
            return notes, pending, death_result

    i = 0
    while i < len(session.enemies):
        pos = session.enemies[i]
        kind = session.enemy_kinds[i] if i < len(session.enemy_kinds) else ""
        if mutant_can_ranged_attack(session, kind, pos):
            pending, note, death_result = _apply_mutant_hit(
                storage,
                telegram_id,
                session,
                player,
                kind,
                pos,
                prior_damage=prior_damage,
                pending=pending,
                ranged=True,
            )
            if note:
                notes.append(note)
            i += 1
            if death_result is not None:
                return notes, pending, death_result
            continue
        if not _mutant_can_adjacent_attack(session, kind, pos):
            i += 1
            continue
        if kind == "tushkano" and tushkano_melee_hits >= TUSHKANO_MAX_MELEE_PER_TURN:
            i += 1
            continue
        if kind == "tushkano":
            tushkano_melee_hits += 1
        pending, note, death_result = _apply_mutant_hit(
            storage,
            telegram_id,
            session,
            player,
            kind,
            pos,
            prior_damage=prior_damage,
            pending=pending,
        )
        if note:
            if mutant_survives_melee(kind):
                notes.append(note + " Плоть отшатнулась — ещё жива!")
            else:
                counter = weapon_damage(str(player.equipment.get("weapon", "Нож")))
                remaining, full = _apply_unit_damage(session, i, counter, npc=False)
                if remaining <= 0:
                    label = _remove_enemy_at_index(session, i)
                    loot = grant_combat_loot(storage, telegram_id, npc=False)
                    loot_note = f" Лут: {loot}." if loot else ""
                    notes.append(note + f" Ответный удар снимает {label}.{loot_note}")
                    if death_result is not None:
                        return notes, pending, death_result
                    continue
                notes.append(
                    note + f" Ответный удар: у мутанта осталось {remaining}/{full} HP."
                )
        if death_result is not None:
            return notes, pending, death_result
        i += 1

    return notes, pending, death_result


def _resolve_hostile_contact(
    storage: Storage,
    telegram_id: int,
    session: QuestMissionSession,
    player: Character,
    label: str,
    unit_attr: str,
    *,
    kinds_attr: str | None = None,
    npc: bool = False,
    prior_damage: int = 0,
    attacker_from: tuple[int, int] | None = None,
) -> tuple[Character, str | None, ActionResult | None, int]:
    """Бой на клетке игрока. Урон в БД не пишется — только (note, dead, dmg)."""
    from app.death_flavor import encounter_phrase_for_kind, killer_label_for_kind

    units: list[tuple[int, int]] = getattr(session, unit_attr)
    if session.player not in units:
        return player, None, None, 0
    kinds: list[str] | None = getattr(session, kinds_attr) if kinds_attr else None
    kind = ""
    if kinds is not None and len(kinds) == len(units):
        idx = units.index(session.player)
        kind = kinds[idx]
    side = "front"
    if attacker_from is not None:
        side = _relative_attack_side(session.player_facing, session.player, attacker_from)
    elif kind == BLOODSUCKER_KIND:
        side = "back"
    dmg = _combat_damage(
        session.location,
        session.difficulty,
        player,
        kind=kind,
        hit_side=side,
        npc=npc,
        enemy_kinds=session.enemy_kinds if not npc else None,
    )
    # Враги на клетке игрока больше не умирают мгновенно: ответный удар снимает
    # HP из таблицы, убираем только тех, у кого HP иссякло.
    weapon = str(player.equipment.get("weapon", "Нож"))
    counter = weapon_damage(weapon)
    n_units = len(units)
    for i in range(n_units):
        # Выравниваем HP-списки по таблице (0 урона — только нормализация).
        _apply_unit_damage(session, i, 0, npc=npc)
    survivors: list[int] = []
    killed_labels: list[str] = []
    wounded_bits: list[str] = []
    for i in range(n_units):
        if units[i] != session.player:
            survivors.append(i)
            continue
        unit_kind = kinds[i] if kinds is not None and i < len(kinds) else ""
        remaining, full = _apply_unit_damage(session, i, counter, npc=npc)
        unit_label = killer_label_for_kind(unit_kind, npc=npc) if unit_kind else label
        if remaining <= 0:
            killed_labels.append(unit_label)
        else:
            wounded_bits.append(f"{unit_label} {remaining}/{full}")
    if npc:
        session.npc_hp = [session.npc_hp[i] for i in survivors]
        session.npc_max_hp = [session.npc_max_hp[i] for i in survivors]
    else:
        session.enemy_hp = [session.enemy_hp[i] for i in survivors]
        session.enemy_max_hp = [session.enemy_max_hp[i] for i in survivors]
    new_units = [units[i] for i in survivors]
    setattr(session, unit_attr, new_units)
    if kinds is not None and len(kinds) == n_units:
        setattr(session, kinds_attr, [kinds[i] for i in survivors])
    if npc:
        weapons = list(session.npc_weapons)
        if len(weapons) == n_units:
            session.npc_weapons = [weapons[i] for i in survivors]
    phrase = encounter_phrase_for_kind(kind, npc=npc) if kind else f"с {label}"
    from app.combat_loot import grant_combat_loot

    loot_bits: list[str] = []
    for _ in killed_labels:
        loot = grant_combat_loot(storage, telegram_id, npc=npc)
        if loot:
            loot_bits.append(str(loot))
    loot_note = (" Лут: " + ", ".join(loot_bits) + ".") if loot_bits else ""
    side_txt = _attack_side_text(side)
    exchange = ""
    if killed_labels:
        exchange = " Снят: " + ", ".join(killed_labels) + "."
    elif wounded_bits:
        exchange = " Твой ответ: " + ", ".join(wounded_bits) + "."
    note = f"Бой {phrase} ({side_txt}): −{dmg} HP.{exchange}{loot_note}"
    # Учитываем уже накопленный урон хода (выстрелы НПС).
    if int(player.health) - max(0, int(prior_damage)) - dmg <= 0:
        from app.game_logic import remember_death_cause, remember_death_killer

        death_cause = "npc" if npc else "mutant"
        remember_death_cause(storage, telegram_id, death_cause)
        killer_name = killer_label_for_kind(kind, npc=npc) if kind else label
        remember_death_killer(storage, telegram_id, killer_name)
        return (
            player,
            note,
            ActionResult(
                False,
                f"Ты пал в бою на «{session.location}».\nКонтракт сорван.",
                payload={
                    "mission_active": False,
                    "mission_dead": True,
                    "death_location": session.location,
                    "death_cause": death_cause,
                },
            ),
            dmg,
        )
    return player, note, None, dmg


def _apply_quest_mission_damage(
    storage: Storage,
    telegram_id: int,
    pending_damage: int,
) -> None:
    if pending_damage > 0:
        storage.change_health(telegram_id, -pending_damage)


def _quest_death_from_pending_damage(
    storage: Storage,
    telegram_id: int,
    session: QuestMissionSession,
    *,
    death_result: ActionResult | None,
) -> ActionResult | None:
    """Если урон хода убил игрока, а death_result ещё нет (выстрелы НПС) — оформить смерть."""
    if death_result is not None:
        return death_result
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None or int(player.health) > 0:
        return None
    from app.game_logic import remember_death_cause, remember_death_killer

    remember_death_cause(storage, telegram_id, "npc")
    remember_death_killer(storage, telegram_id, "НПС")
    return ActionResult(
        False,
        f"Ты пал в бою на «{session.location}».\nКонтракт сорван.",
        payload={
            "mission_active": False,
            "mission_dead": True,
            "death_location": session.location,
            "death_cause": "npc",
        },
    )


def _finalize_quest_death_result(
    storage: Storage,
    telegram_id: int,
    death_result: ActionResult,
) -> ActionResult:
    clear_mission_session(storage, telegram_id)
    storage.set_active_contract(telegram_id, None)
    return death_result


def _combat_damage(
    location: str,
    difficulty: str,
    character: Character,
    *,
    kind: str = "",
    hit_side: str = "front",
    npc: bool = False,
    enemy_kinds: list[str] | None = None,
) -> int:
    from app.artifact_features import artifact_outgoing_damage_mult

    danger = _location_danger(location, difficulty)
    base_lo = 6 + danger * 4
    base_hi = 12 + danger * 7
    raw = random.randint(base_lo, base_hi)
    diff_mult = float(DIFFICULTY_COMBAT_MULT.get(difficulty, 1.0))
    if npc:
        diff_mult *= 1.1
    raw = int(raw * diff_mult)
    if not npc and kind:
        raw = max(1, int(raw * mutant_damage_multiplier(kind, hit_side, enemy_kinds)))
    else:
        side_mult = {"front": 1.0, "left": 1.08, "right": 1.08, "back": 1.22}.get(hit_side, 1.0)
        raw = max(1, int(raw * side_mult))
    if difficulty in {"heavy", "impossible"} and not npc:
        soak = 0
        pre_defense = raw
        min_after_armor = 10
    else:
        soak = min(12, equipment_power(character))
        pre_defense = max(4, raw - soak)
        min_after_armor = 1
    dmg = apply_incoming_damage(pre_defense, character, min_damage=min_after_armor)
    return max(min_after_armor, int(dmg / artifact_outgoing_damage_mult(character)))


def _hazard_damage(kind: str, character: Character) -> int:
    if kind == "scout":
        raw = 20
    elif kind == "loot":
        raw = 15
    elif kind == "escort":
        raw = 22
    else:
        raw = 25
    return apply_incoming_damage(raw, character, min_damage=1)


def _fail_escort_mission(
    storage: Storage,
    telegram_id: int,
    session: QuestMissionSession,
    *,
    reason: str,
) -> ActionResult:
    session.escort_alive = False
    clear_mission_session(storage, telegram_id)
    quest = QUESTS.get(session.difficulty) or QUESTS["easy"]
    fail_result = apply_contract_mission_fail(
        storage,
        telegram_id,
        quest=quest,
        work_location=session.location,
        title=session.title,
        reason=reason,
    )
    storage.set_active_contract(telegram_id, None)
    return ActionResult(
        False,
        fail_result.text,
        payload={"mission_active": False, "mission_done": True, "escort_failed": True},
    )


def _escort_hit_by_hostiles(session: QuestMissionSession) -> bool:
    if session.kind != "escort" or not session.escort_alive or session.escort is None:
        return False
    return session.escort in session.enemies or session.escort in session.npcs


def _mission_rating(storage: Storage, telegram_id: int) -> int:
    try:
        return int(storage.get_player_stats(telegram_id).get("rating_points", 0))
    except Exception:
        return 0


def render_mission_for_player(
    storage: Storage,
    telegram_id: int,
    session: QuestMissionSession,
    player: Character,
) -> bytes:
    return render_mission_frame(session, player, rating_points=_mission_rating(storage, telegram_id))


def _render_for_player(storage: Storage, telegram_id: int, session: QuestMissionSession, player: Character) -> bytes:
    return render_mission_for_player(storage, telegram_id, session, player)


def mission_status_caption(session: QuestMissionSession, character: Character | None = None) -> str:
    lines = [
        f"Ход {session.moves}/{session.max_moves}",
    ]
    threat_bits: list[str] = []
    if session.hazards:
        threat_bits.append(f"аномалии {len(session.hazards)}")
    if session.enemies:
        threat_bits.append(f"мутанты {len(session.enemies)}")
    if session.npcs:
        threat_bits.append(f"НПС {len(session.npcs)}")
    if threat_bits:
        lines.append("Угрозы: " + ", ".join(threat_bits))
    if session.enemies:
        bits = "/".join(str(hp) for hp in session.enemy_hp[:6])
        lines.append(f"Мутанты: {len(session.enemies)} · HP {bits or '?'}")
    if session.npcs:
        bits = "/".join(str(hp) for hp in session.npc_hp[:6])
        lines.append(f"НПС: {len(session.npcs)} · HP {bits or '?'}")
    if session.kind == "clear_mutant":
        lines.append(f"Зачистка: мутанты осталось {len(session.enemies)}")
    elif session.kind == "clear_marauder":
        if "вертушк" in str(session.title or "").lower():
            lines.append(f"Зачистка: военных осталось {len(session.npcs)}")
        else:
            lines.append(f"Зачистка: НПС осталось {len(session.npcs)}")
    elif session.kind == "escort":
        status = "жив, следует за тобой" if session.escort_alive else "потерян"
        lines.append(f"Подопечный: {status}")
        left = len([p for p in session.objectives if p not in session.collected])
        lines.append(f"Точка B: {'достигнута' if left == 0 else 'ещё впереди'}")
    else:
        left = len([p for p in session.objectives if p not in session.collected])
        lines.append(f"Цели: {len(session.collected)}/{len(session.objectives)} (осталось {left})")
    if session.objectives_done:
        lines.append("Цель взята — вернись на стартовую клетку (зелёная рамка).")
    elif session.kind == "escort":
        lines.append("Доведи подопечного до точки B, затем вернись на старт.")
    else:
        lines.append("Собери цели / зачисти поле, затем вернись на старт.")
    if character is not None:
        lines.append(
            f"HP {character.health}/{effective_max_health(character)} · "
            f"☢ {character.radiation} · ⚡ {character.energy}"
        )
    return "\n".join(lines)


def _is_help_event_mission(session: QuestMissionSession) -> bool:
    return str(session.contract_key).startswith("help_") or str(session.title).startswith("Помощь:")


_SPECIAL_EVENT_KEY_PREFIXES: tuple[str, ...] = (
    "heli_",
    "storm_",
    "bandit_den_",
    "dark_",
    "rescue_",
    "giant_",
    "march_",
)


def _is_special_event_mission(session: QuestMissionSession) -> bool:
    key = str(session.contract_key or "")
    if any(key.startswith(prefix) for prefix in _SPECIAL_EVENT_KEY_PREFIXES):
        return True
    title = str(session.title or "")
    return (
        title.startswith("Гигант:")
        or title.startswith("Обломки вертушки")
        or title.startswith("Поиск прохода в шторме")
        or title.startswith("Логово бандитов")
        or title.startswith("Дуэль: Тёмный сталкер")
        or title.startswith("Спасение пленного")
        or title.startswith("Колонна Монолита")
    )


def _finish_success(storage: Storage, telegram_id: int, session: QuestMissionSession) -> ActionResult:
    clear_mission_session(storage, telegram_id)
    template = QUEST_CONTRACTS.get(session.contract_key)
    quest = QUESTS.get(session.difficulty)
    is_help = _is_help_event_mission(session)
    is_special = _is_special_event_mission(session)
    is_ephemeral = is_help or is_special
    # Эфемерные миссии (помощь / особые события) не обязаны быть в QUEST_CONTRACTS.
    if template is None and not is_ephemeral:
        storage.set_active_contract(telegram_id, None)
        return ActionResult(False, "Контракт повреждён после миссии.")
    if quest is None:
        quest = QUESTS["hard"]

    result = apply_contract_mission_success(
        storage,
        telegram_id,
        quest=quest,
        work_location=session.location,
        title=session.title,
    )
    reward = int((result.payload or {}).get("reward", 0))
    character = storage.get_character(telegram_id, refresh_energy=False)
    if is_ephemeral or template is None or not template.return_home:
        storage.set_active_contract(telegram_id, None)
        return ActionResult(
            True,
            result.text,
            payload={"mission_active": False, "mission_done": True, "reward": reward},
        )
    if template.return_home:
        storage.set_active_contract(
            telegram_id,
            {
                "template_key": template.key,
                "stage": "return",
                "pending_reward": reward,
            },
        )
        home = faction_home_base(character.faction if character else None)
        # Если уже на базе (редкий случай) — сразу закрываем отчёт.
        auto = try_auto_turn_in_contract(storage, telegram_id)
        if auto:
            return ActionResult(
                True,
                result.text + "\n\n" + auto,
                payload={"mission_active": False, "mission_done": True, "reward": reward, "turned_in": True},
            )
        return ActionResult(
            True,
            result.text
            + f"\n\nВернись на «{home}» — отчёт сдастся автоматически при прибытии "
            + f"(+{CONTRACT_TURN_IN_BONUS_PERCENT}% RU).",
            payload={"mission_active": False, "mission_done": True, "reward": reward},
        )
    storage.set_active_contract(telegram_id, None)
    return ActionResult(
        True,
        result.text,
        payload={"mission_active": False, "mission_done": True, "reward": reward},
    )


def start_or_resume_quest_mission(
    storage: Storage,
    telegram_id: int,
    template: QuestContractTemplate,
    quest: QuestType,
) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        if get_mission_session(storage, telegram_id):
            clear_mission_session(storage, telegram_id)
            storage.set_active_contract(telegram_id, None)
        return ActionResult(False, _dead_block_text())

    existing = get_mission_session(storage, telegram_id)
    if existing is not None:
        if existing.contract_key != template.key:
            clear_mission_session(storage, telegram_id)
            storage.set_active_contract(telegram_id, None)
            return ActionResult(
                False,
                "Предыдущая вылазка сброшена — выбери контракт и начни заново.",
            )
        else:
            image = _render_for_player(storage, telegram_id, existing, player)
            return ActionResult(
                True,
                "Продолжай вылазку по контракту.",
                payload={
                    "mission_image": image,
                    "mission_active": True,
                    "caption": mission_status_caption(existing, player),
                },
            )

    from app.player_busy import player_busy_reason

    busy = player_busy_reason(storage, telegram_id, skip="quest")
    if busy:
        return ActionResult(False, busy)

    # Новая миссия — списываем ресурсы один раз.
    spend_err = _spend_quest_resources(storage, telegram_id, quest)
    if spend_err is not None:
        return spend_err

    if template.min_gear_power > 0 and equipment_power(player) < template.min_gear_power:
        return ActionResult(
            False,
            f"Нужна сила снаряги ≥ {template.min_gear_power} (сейчас {equipment_power(player)}).",
        )

    session = _build_session(template, quest)
    _apply_special_mission_spawn_overrides(template, session, player)
    session.resources_spent = True
    save_mission_session(storage, telegram_id, session)
    active_contract = storage.get_active_contract(telegram_id) or {}
    storage.set_active_contract(
        telegram_id,
        {**active_contract, "mission_started": True},
    )
    player = storage.get_character(telegram_id, refresh_energy=False) or player
    image = _render_for_player(storage, telegram_id, session, player)
    want_anom, want_mut, want_npc = _difficulty_threat_flags(template.difficulty, template.mission_kind)
    threat_parts: list[str] = []
    if template.mission_kind == "escort":
        threat_txt = "аномалии (всегда) + мутанты или НПС с T1-стволами"
    else:
        if want_anom:
            threat_parts.append("аномалии")
        if want_mut:
            threat_parts.append("мутанты")
        if want_npc:
            threat_parts.append("НПС")
        threat_txt = ", ".join(threat_parts) if threat_parts else "без угроз"
    escort_hint = ""
    if template.mission_kind == "escort":
        escort_hint = (
            "\nПодопечный (голубое кольцо) идёт за тобой клетка в клетку. "
            "Доведи его до точки B — если его схватят или он погибнет, контракт сорван."
        )
    from app.mutant_assets import mutant_field_warnings

    warn_block = ""
    warns = mutant_field_warnings(session.enemy_kinds)
    if warns:
        warn_block = "\n" + "\n".join(warns)
    return ActionResult(
        True,
        f"Вылазка: «{template.title}» на «{template.work_location}».\n"
        f"{_mission_kind_label(template.mission_kind, template.title)}. "
        f"Энергия −{quest.energy_cost}.\n"
        f"Угрозы ({template.difficulty}): {threat_txt}."
        f"{escort_hint}{warn_block}\n"
        "Зелёная обводка — ты и цели (собери их). Красная — враги. Аномалии без обводки.\n"
        "Дойди до целей и вернись на старт (зелёная рамка клетки).\n"
        "Мутанты каждый ход идут к тебе — у каждого типа своя «абилка» (см. подсказки при старте). "
        "НПС чаще стреляют и сильнее на высокой сложности; на 🟠/🔴 ещё и сдвигаются к тебе. "
        "Ты смотришь в сторону хода: ⬆️ лоб / ⬅️➡️ бок / ⬇️ спина."
        + (
            " 🔫 Мутантов и НПС можно расстреливать с места — кнопки стрельбы по направлениям."
            if (want_mut or want_npc or template.mission_kind == "escort")
            else ""
        ),
        payload={
            "mission_image": image,
            "mission_active": True,
            "mission_started": True,
            "caption": mission_status_caption(session, player),
        },
    )


def abandon_quest_mission(storage: Storage, telegram_id: int) -> ActionResult:
    session = get_mission_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Активной вылазки по контракту нет.")
    clear_mission_session(storage, telegram_id)
    quest = QUESTS.get(session.difficulty) or QUESTS["easy"]
    result = apply_contract_mission_fail(
        storage,
        telegram_id,
        quest=quest,
        work_location=session.location,
        title=session.title,
        reason="Ты свалил с поля.",
    )
    storage.set_active_contract(telegram_id, None)
    return ActionResult(
        False,
        result.text,
        payload={"mission_active": False, "mission_done": True},
    )


def use_mission_medkit(storage: Storage, telegram_id: int) -> ActionResult:
    session = get_mission_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Сначала начни вылазку по контракту.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа.")
    preferred = ("medkit_science", "medkit_army", "medkit")
    chosen = next((key for key in preferred if int(player.inventory.get(key, 0)) > 0), None)
    if chosen is None:
        return ActionResult(
            False,
            "Нет аптечек в инвентаре (обычная / армейская / научная).",
            payload={"mission_active": True},
        )
    expected_seq = session.turn_seq
    notes: list[str] = []
    move_notes, onto_player = _maybe_move_hostiles(session, storage, telegram_id)
    notes.extend(move_notes)
    pending_damage = 0
    death_result: ActionResult | None = None
    mut_notes, mut_dmg, mut_dead = _resolve_mutant_adjacent_attacks(
        storage,
        telegram_id,
        session,
        player,
        prior_damage=0,
        onto_player=onto_player,
    )
    notes.extend(mut_notes)
    pending_damage += mut_dmg
    if mut_dead is not None:
        death_result = mut_dead
    shot_notes, shot_damage = _maybe_npc_shots(storage, telegram_id, session, player)
    notes.extend(shot_notes)
    pending_damage += shot_damage
    from app.game_logic import MEDKIT_EFFECTS

    effect = MEDKIT_EFFECTS.get(chosen)
    if effect is None:
        return ActionResult(False, "Неизвестный тип аптечки.", payload={"mission_active": True})
    max_hp = effective_max_health(player)
    needs_heal = player.health < max_hp
    needs_rad = int(effect.get("radiation", 0)) < 0 and player.radiation > 0
    if not needs_heal and not needs_rad:
        if int(effect.get("radiation", 0)) < 0:
            return ActionResult(
                False,
                "Здоровье полное и радиации нет — аптечка не нужна.",
                payload={"mission_active": True},
            )
        return ActionResult(
            False,
            "Здоровье уже полное, аптечка не требуется.",
            payload={"mission_active": True},
        )
    result = use_medkit_item(storage, telegram_id, chosen, skip_busy="quest")
    notes.insert(0, result.text)
    if not result.ok:
        return ActionResult(result.ok, result.text, payload={"mission_active": True})
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return result
    prior = int(pending_damage)
    for label, unit_attr, kinds_attr, is_npc in (
        ("мутантом", "enemies", "enemy_kinds", False),
        ("НПС", "npcs", "npc_kinds", True),
    ):
        player, note, dead, dmg = _resolve_hostile_contact(
            storage,
            telegram_id,
            session,
            player,
            label,
            unit_attr,
            kinds_attr=kinds_attr,
            npc=is_npc,
            prior_damage=prior,
        )
        pending_damage += dmg
        prior += dmg
        if note is not None:
            notes.append(note)
        if dead is not None:
            death_result = dead
    if session.kind == "escort" and session.escort_alive and _escort_hit_by_hostiles(session):
        session.turn_seq = expected_seq + 1
        if not _save_mission_if_turn_ok(storage, telegram_id, session, expected_seq):
            from app.tactical_combat import STALE_TURN_MESSAGE

            return ActionResult(False, STALE_TURN_MESSAGE, payload={"mission_active": True})
        _apply_quest_mission_damage(storage, telegram_id, pending_damage)
        return _fail_escort_mission(
            storage,
            telegram_id,
            session,
            reason="Подопечного схватили на маршруте.",
        )
    session.turn_seq = expected_seq + 1
    if not _save_mission_if_turn_ok(storage, telegram_id, session, expected_seq):
        from app.tactical_combat import STALE_TURN_MESSAGE

        return ActionResult(False, STALE_TURN_MESSAGE, payload={"mission_active": True})
    _apply_quest_mission_damage(storage, telegram_id, pending_damage)
    death_result = _quest_death_from_pending_damage(
        storage, telegram_id, session, death_result=death_result
    )
    if death_result is not None:
        return _finalize_quest_death_result(storage, telegram_id, death_result)
    image = _render_for_player(storage, telegram_id, session, player)
    note_text = " ".join(notes)
    return ActionResult(
        result.ok,
        note_text,
        payload={
            "mission_image": image,
            "mission_active": True,
            "caption": mission_status_caption(session, player),
            "move_note": note_text,
        },
    )


def mission_shoot_available(session: QuestMissionSession) -> bool:
    """Стрельба доступна, пока на поле есть мутанты или НПС."""
    return len(session.npcs) > 0 or len(session.enemies) > 0


def _remove_npc_at_index(session: QuestMissionSession, idx: int) -> str:
    kind = session.npc_kinds[idx] if idx < len(session.npc_kinds) else ""
    session.npcs.pop(idx)
    if idx < len(session.npc_kinds):
        session.npc_kinds.pop(idx)
    if idx < len(session.npc_weapons):
        session.npc_weapons.pop(idx)
    if idx < len(session.npc_hp):
        session.npc_hp.pop(idx)
    if idx < len(session.npc_max_hp):
        session.npc_max_hp.pop(idx)
    from app.death_flavor import killer_label_for_kind

    return killer_label_for_kind(kind, npc=True) if kind else "НПС"


def _complete_quest_turn_after_action(
    storage: Storage,
    telegram_id: int,
    session: QuestMissionSession,
    player: Character,
    expected_seq: int,
    notes: list[str],
    pending_damage: int,
    death_result: ActionResult | None,
) -> ActionResult:
    def _fight_on_cell(
        label: str,
        unit_attr: str,
        *,
        kinds_attr: str | None = None,
        npc: bool = False,
    ) -> None:
        nonlocal player, notes, pending_damage, death_result
        p, note, dead, dmg = _resolve_hostile_contact(
            storage,
            telegram_id,
            session,
            player,
            label,
            unit_attr,
            kinds_attr=kinds_attr,
            npc=npc,
            prior_damage=pending_damage,
        )
        player = p
        pending_damage += dmg
        if note is not None:
            notes.append(note)
        if dead is not None:
            death_result = dead

    move_notes, onto_player = _maybe_move_hostiles(session, storage, telegram_id)
    notes.extend(move_notes)
    mut_notes, mut_dmg, mut_dead = _resolve_mutant_adjacent_attacks(
        storage,
        telegram_id,
        session,
        player,
        prior_damage=pending_damage,
        onto_player=onto_player,
    )
    notes.extend(mut_notes)
    pending_damage += mut_dmg
    if mut_dead is not None:
        death_result = mut_dead
    shot_notes, shot_damage = _maybe_npc_shots(storage, telegram_id, session, player)
    notes.extend(shot_notes)
    pending_damage += shot_damage
    _fight_on_cell("мутанта", "enemies", kinds_attr="enemy_kinds")
    _fight_on_cell("НПС", "npcs", kinds_attr="npc_kinds", npc=True)

    if session.kind == "escort" and session.escort_alive and _escort_hit_by_hostiles(session):
        session.turn_seq = expected_seq + 1
        if not _save_mission_if_turn_ok(storage, telegram_id, session, expected_seq):
            from app.tactical_combat import STALE_TURN_MESSAGE

            return ActionResult(False, STALE_TURN_MESSAGE, payload={"mission_active": True})
        _apply_quest_mission_damage(storage, telegram_id, pending_damage)
        return _fail_escort_mission(
            storage,
            telegram_id,
            session,
            reason="Подопечного схватили на маршруте.",
        )

    if _objectives_complete(session):
        session.objectives_done = True

    def _commit_turn_and_damage() -> ActionResult | None:
        nonlocal death_result, player
        session.turn_seq = expected_seq + 1
        if not _save_mission_if_turn_ok(storage, telegram_id, session, expected_seq):
            from app.tactical_combat import STALE_TURN_MESSAGE

            return ActionResult(False, STALE_TURN_MESSAGE, payload={"mission_active": True})
        from app.artifact_features import artifact_quest_heal_per_turn
        from app.game_logic import effective_max_health

        heal = artifact_quest_heal_per_turn(player)
        if heal > 0:
            storage.change_health(telegram_id, heal, max_health=effective_max_health(player))
            notes.append(f"💚 Артефакты: +{heal} HP")
            player = storage.get_character(telegram_id, refresh_energy=False) or player
        _apply_quest_mission_damage(storage, telegram_id, pending_damage)
        death_result = _quest_death_from_pending_damage(
            storage, telegram_id, session, death_result=death_result
        )
        if death_result is not None:
            return _finalize_quest_death_result(storage, telegram_id, death_result)
        return None

    if session.objectives_done and session.player == session.start:
        stale = _commit_turn_and_damage()
        if stale is not None:
            return stale
        return _finish_success(storage, telegram_id, session)

    if session.moves >= session.max_moves:
        stale = _commit_turn_and_damage()
        if stale is not None:
            return stale
        clear_mission_session(storage, telegram_id)
        quest = QUESTS.get(session.difficulty) or QUESTS["easy"]
        fail_result = apply_contract_mission_fail(
            storage,
            telegram_id,
            quest=quest,
            work_location=session.location,
            title=session.title,
            reason="Время вылазки вышло.",
        )
        storage.set_active_contract(telegram_id, None)
        return ActionResult(
            False,
            fail_result.text,
            payload={"mission_active": False, "mission_done": True},
        )

    stale = _commit_turn_and_damage()
    if stale is not None:
        return stale
    player = storage.get_character(telegram_id, refresh_energy=False) or player
    image = _render_for_player(storage, telegram_id, session, player)
    note = " ".join(notes) if notes else "Тихо."
    if session.objectives_done:
        note += " Цель есть — на старт!"
    return ActionResult(
        True,
        note,
        payload={
            "mission_image": image,
            "mission_active": True,
            "caption": mission_status_caption(session, player),
            "move_note": note,
        },
    )


def shoot_quest_mission(storage: Storage, telegram_id: int, direction: str) -> ActionResult:
    session = get_mission_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Сначала начни работу по контракту.")
    if not mission_shoot_available(session):
        return ActionResult(False, "Врагов на поле нет — стрелять некого.", payload={"mission_active": True})
    if direction not in MOVE_DELTAS:
        return ActionResult(False, "Некорректное направление.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        clear_mission_session(storage, telegram_id)
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        clear_mission_session(storage, telegram_id)
        storage.set_active_contract(telegram_id, None)
        from app.game_logic import peek_death_cause

        return ActionResult(
            False,
            _dead_block_text(),
            payload={
                "mission_active": False,
                "mission_dead": True,
                "death_location": session.location,
                "death_cause": peek_death_cause(storage, telegram_id) or "combat",
            },
        )

    weapon = str(player.equipment.get("weapon", "Нож"))
    shoot_range = weapon_shoot_range(weapon)
    if shoot_range <= 0:
        return ActionResult(False, "Это оружие не стреляет на дистанции.", payload={"mission_active": True})

    ammo_result = consume_shot_ammo(storage, telegram_id, weapon)
    if ammo_result is not None:
        return ammo_result

    targets: dict[tuple[int, int], str] = {
        pos: "mutant" for pos in session.enemies
    }
    targets.update({pos: "npc" for pos in session.npcs})
    hits = collect_player_shot_hits(
        session.player,
        direction,
        grid=session.grid,
        weapon_name=weapon,
        blockers=set(),
        targets=targets,
        cover=set(),
    )

    expected_seq = session.turn_seq
    session.player_facing = direction
    session.moves += 1
    notes: list[str] = []
    if hits:
        from app.combat_loot import grant_combat_loot
        from app.death_flavor import killer_label_for_kind

        # Выстрел снимает HP из таблицы; враг умирает только при HP ≤ 0.
        dmg = weapon_damage(weapon)
        for hit_cell, hit_kind in hits:
            if hit_kind == "npc":
                if hit_cell not in session.npcs:
                    continue
                idx = session.npcs.index(hit_cell)
                kind = session.npc_kinds[idx] if idx < len(session.npc_kinds) else ""
                label = killer_label_for_kind(kind, npc=True) if kind else "НПС"
                remaining, full = _apply_unit_damage(session, idx, dmg, npc=True)
                if remaining <= 0:
                    label = _remove_npc_at_index(session, idx)
                    loot = grant_combat_loot(storage, telegram_id, npc=True)
                    loot_note = f" Лут: {loot}." if loot else ""
                    notes.append(f"{h(player.nickname)} поразил {label} ({weapon}).{loot_note}")
                    if session.kind == "clear_marauder" and not session.npcs:
                        notes.append("Зона зачищена от мародёров.")
                else:
                    notes.append(
                        f"{h(player.nickname)} поразил {label} ({weapon}): "
                        f"осталось {remaining}/{full} HP."
                    )
            elif hit_kind == "mutant":
                if hit_cell not in session.enemies:
                    continue
                idx = session.enemies.index(hit_cell)
                kind = session.enemy_kinds[idx] if idx < len(session.enemy_kinds) else ""
                label = killer_label_for_kind(kind, npc=False) if kind else "мутанта"
                remaining, full = _apply_unit_damage(session, idx, dmg, npc=False)
                if remaining <= 0:
                    label = _remove_enemy_at_index(session, idx)
                    loot = grant_combat_loot(storage, telegram_id, npc=False)
                    loot_note = f" Лут: {loot}." if loot else ""
                    notes.append(f"{h(player.nickname)} поразил {label} ({weapon}).{loot_note}")
                    if session.kind == "clear_mutant" and not session.enemies:
                        notes.append("Зона зачищена от мутантов.")
                else:
                    notes.append(
                        f"{h(player.nickname)} поразил {label} ({weapon}): "
                        f"осталось {remaining}/{full} HP."
                    )
    else:
        notes.append(f"{h(player.nickname)} промахнулся ({weapon}).")

    return _complete_quest_turn_after_action(
        storage,
        telegram_id,
        session,
        player,
        expected_seq,
        notes,
        pending_damage=0,
        death_result=None,
    )


def move_quest_mission(storage: Storage, telegram_id: int, direction: str) -> ActionResult:
    session = get_mission_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Сначала начни работу по контракту.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        clear_mission_session(storage, telegram_id)
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        clear_mission_session(storage, telegram_id)
        storage.set_active_contract(telegram_id, None)
        from app.game_logic import peek_death_cause

        return ActionResult(
            False,
            _dead_block_text(),
            payload={
                "mission_active": False,
                "mission_dead": True,
                "death_location": session.location,
                "death_cause": peek_death_cause(storage, telegram_id) or "combat",
            },
        )

    delta = MOVE_DELTAS.get(direction)
    if delta is None:
        return ActionResult(False, "Некорректное направление.")
    nx = session.player[0] + delta[0]
    ny = session.player[1] + delta[1]
    if not (0 <= nx < session.grid and 0 <= ny < session.grid):
        image = _render_for_player(storage, telegram_id, session, player)
        return ActionResult(
            False,
            "Край поля — туда не пройти.",
            payload={
                "mission_image": image,
                "mission_active": True,
                "caption": mission_status_caption(session, player),
            },
        )

    expected_seq = session.turn_seq
    old_pos = session.player
    session.player = (nx, ny)
    session.player_facing = direction
    session.moves += 1
    notes: list[str] = []
    pending_damage = 0
    death_result: ActionResult | None = None

    if session.kind == "escort" and session.escort_alive:
        session.escort = old_pos
        notes.append("Подопечный следует за тобой.")

    # Цель.
    if session.player in session.objectives and session.player not in session.collected:
        session.collected.append(session.player)
        if session.kind == "escort":
            notes.append("Точка B достигнута — веди подопечного обратно на старт.")
        else:
            notes.append("Цель отмечена.")

    def _fight_on_cell(
        label: str,
        unit_attr: str,
        *,
        kinds_attr: str | None = None,
        npc: bool = False,
    ) -> None:
        nonlocal player, notes, pending_damage, death_result
        player, note, dead, dmg = _resolve_hostile_contact(
            storage,
            telegram_id,
            session,
            player,
            label,
            unit_attr,
            kinds_attr=kinds_attr,
            npc=npc,
            prior_damage=pending_damage,
        )
        pending_damage += dmg
        if note is not None:
            notes.append(note)
        if dead is not None:
            death_result = dead

    _fight_on_cell("мутантом", "enemies", kinds_attr="enemy_kinds", npc=False)
    _fight_on_cell("НПС", "npcs", kinds_attr="npc_kinds", npc=True)

    if session.kind == "escort" and session.escort_alive and _escort_hit_by_hostiles(session):
        # Враг уже стоял на клетке, куда встал подопечный.
        return _complete_quest_turn_after_action(
            storage,
            telegram_id,
            session,
            player,
            expected_seq,
            notes + ["Подопечного накрыли!"],
            pending_damage,
            death_result,
        )

    # Аномалия.
    if session.player in session.hazards:
        if session.kind in {"collect", "anomaly"}:
            pending_damage += max(int(player.health), 1)
            from app.game_logic import remember_death_cause

            remember_death_cause(storage, telegram_id, "anomaly")
            death_result = ActionResult(
                False,
                f"Аномалия на «{session.location}». Сознание гаснет…\nКонтракт сорван.",
                payload={
                    "mission_active": False,
                    "mission_dead": True,
                    "death_location": session.location,
                    "death_cause": "anomaly",
                },
            )
        else:
            dmg = _hazard_damage(session.kind, player)
            pending_damage += dmg
            session.hazards = [h for h in session.hazards if h != session.player]
            notes.append(f"Аномалия: −{dmg} HP.")
            if int(player.health) - pending_damage <= 0:
                from app.game_logic import remember_death_cause

                remember_death_cause(storage, telegram_id, "anomaly")
                death_result = ActionResult(
                    False,
                    f"Раны оказались смертельными на «{session.location}».\nКонтракт сорван.",
                    payload={
                        "mission_active": False,
                        "mission_dead": True,
                        "death_location": session.location,
                        "death_cause": "anomaly",
                    },
                )

    return _complete_quest_turn_after_action(
        storage,
        telegram_id,
        session,
        player,
        expected_seq,
        notes,
        pending_damage,
        death_result,
    )


# --- Render -----------------------------------------------------------------

def _load_font(size: int) -> ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _draw_cell(base: Image.Image, left: int, top: int, size: int, tone: int = 70) -> None:
    pix = base.load()
    for yy in range(size):
        for xx in range(size):
            n = ((left + xx) * 17 + (top + yy) * 31) % 28
            shade = max(40, min(120, tone + n - 10))
            pix[left + xx, top + yy] = (shade, shade - 2, max(30, shade - 8), 255)
    draw = ImageDraw.Draw(base)
    draw.rectangle((left, top, left + size - 1, top + size - 1), outline=(30, 30, 32), width=2)


def _glow(img: Image.Image, cx: int, cy: int, color: tuple[int, int, int], radius: int = 22) -> None:
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    r, g, b = color
    for i, a in ((radius + 14, 35), (radius + 6, 90), (radius, 200), (max(4, radius // 2), 240)):
        d.ellipse((cx - i, cy - i, cx + i, cy + i), fill=(r, g, b, a))
    img.alpha_composite(overlay)


def _paste_token_circle(
    canvas: Image.Image,
    token: Image.Image,
    cx: int,
    cy: int,
    diameter: int,
) -> None:
    """Круглый спрайт без цветной обводки (аномалии)."""
    token = token.convert("RGBA").resize((diameter, diameter), Image.Resampling.LANCZOS)
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((1, 1, diameter - 2, diameter - 2), fill=255)
    canvas.paste(token, (cx - diameter // 2, cy - diameter // 2), mask)


def _draw_enemy_icon(draw: ImageDraw.ImageDraw, cx: int, cy: int, *, marauder: bool) -> None:
    if marauder:
        # Мародёр — тёмная фигура + красный акцент.
        draw.ellipse((cx - 16, cy - 22, cx + 16, cy + 10), fill=(55, 45, 40), outline=(180, 60, 50), width=2)
        draw.rectangle((cx - 12, cy + 8, cx + 12, cy + 22), fill=(70, 50, 40))
        draw.ellipse((cx - 6, cy - 14, cx + 6, cy - 2), fill=(200, 80, 60))
    else:
        # Мутант — зеленоватый.
        draw.ellipse((cx - 18, cy - 16, cx + 18, cy + 16), fill=(50, 90, 45), outline=(120, 200, 80), width=2)
        draw.ellipse((cx - 8, cy - 8, cx - 2, cy - 2), fill=(220, 230, 100))
        draw.ellipse((cx + 2, cy - 8, cx + 8, cy - 2), fill=(220, 230, 100))
        draw.polygon([(cx - 10, cy + 6), (cx, cy + 16), (cx + 10, cy + 6)], fill=(40, 70, 35))


def render_mission_frame(
    session: QuestMissionSession,
    character: Character | None = None,
    *,
    rating_points: int = 0,
) -> bytes:
    cell = 108
    grid = session.grid
    grid_px = grid * cell
    margin = 24
    panel_w = 320
    width = margin + grid_px + 20 + panel_w + margin
    height = max(margin + grid_px + margin, 760)
    canvas = Image.new("RGBA", (width, height), (16, 18, 20, 255))
    draw = ImageDraw.Draw(canvas)

    field = (margin - 8, margin - 8, margin + grid_px + 8, margin + grid_px + 8)
    draw.rounded_rectangle(field, radius=14, fill=(34, 36, 40, 255), outline=(70, 74, 80), width=2)

    loc_bg = _load_location_thumb(session.location)
    if loc_bg is not None:
        field_img = _cover_crop(loc_bg, grid_px, grid_px).convert("RGBA")
        field_img.putalpha(160)
        canvas.paste(field_img, (margin, margin), field_img)

    for gy in range(grid):
        for gx in range(grid):
            left = margin + gx * cell
            top = margin + gy * cell
            if loc_bg is None:
                _draw_cell(canvas, left, top, cell)
            else:
                overlay = Image.new("RGBA", (cell, cell), (12, 14, 16, 55))
                canvas.alpha_composite(overlay, (left, top))
                ImageDraw.Draw(canvas).rectangle(
                    (left, top, left + cell - 1, top + cell - 1),
                    outline=(28, 30, 32),
                    width=1,
                )

    # Старт — зелёная рамка.
    sx, sy = session.start
    sl = margin + sx * cell
    st = margin + sy * cell
    draw.rectangle((sl + 3, st + 3, sl + cell - 4, st + cell - 4), outline=(80, 200, 90), width=3)

    for hx, hy in session.hazards:
        cx = margin + hx * cell + cell // 2
        cy = margin + hy * cell + cell // 2
        sprite = mission_icon_image(ANOMALY_ICON_KEY)
        if sprite is not None:
            # Аномалия — спрайт без цветной обводки (не цель и не враг).
            _paste_token_circle(canvas, sprite, cx, cy, MISSION_ICON_GRID_DIAMETER)
        else:
            _glow(canvas, cx, cy, (255, 120, 40), 24)

    enemy_ring = (210, 55, 45)
    for i, (ex, ey) in enumerate(session.enemies):
        cx = margin + ex * cell + cell // 2
        cy = margin + ey * cell + cell // 2
        kind = (
            session.enemy_kinds[i]
            if i < len(session.enemy_kinds)
            else MUTANT_SPRITE_KEYS[i % len(MUTANT_SPRITE_KEYS)]
        )
        sprite = mutant_sprite_image(kind)
        enemy_diameter = mutant_grid_diameter(kind, default=MISSION_MUTANT_GRID_DIAMETER)
        ring_w = 4 if kind == "giant" else 3
        if sprite is not None:
            _paste_circle(
                canvas,
                sprite,
                cx,
                cy,
                enemy_diameter,
                ring_color=enemy_ring,
                ring_width=ring_w,
            )
        else:
            _draw_enemy_icon(ImageDraw.Draw(canvas), cx, cy, marauder=False)

    for i, (nx_, ny_) in enumerate(session.npcs):
        cx = margin + nx_ * cell + cell // 2
        cy = margin + ny_ * cell + cell // 2
        sprite = npc_sprite_image(session.npc_kinds[i] if i < len(session.npc_kinds) else "maloy")
        if sprite is not None:
            _paste_circle(
                canvas,
                sprite,
                cx,
                cy,
                MISSION_NPC_GRID_DIAMETER,
                ring_color=enemy_ring,
                ring_width=3,
            )
        else:
            _draw_enemy_icon(ImageDraw.Draw(canvas), cx, cy, marauder=False)

    objective_ring = (72, 220, 90)
    for ox, oy in session.objectives:
        if (ox, oy) in session.collected:
            continue
        cx = margin + ox * cell + cell // 2
        cy = margin + oy * cell + cell // 2
        sprite = mission_icon_image(OBJECTIVE_ICON_KEY)
        if sprite is not None:
            _paste_circle(
                canvas,
                sprite,
                cx,
                cy,
                MISSION_ICON_GRID_DIAMETER,
                ring_color=objective_ring,
                ring_width=4,
            )
        else:
            _glow(canvas, cx, cy, (70, 230, 110), 22)

    # Подопечный эскорта — NPC со голубым кольцом (не враг).
    if session.kind == "escort" and session.escort_alive and session.escort is not None:
        ex, ey = session.escort
        ecx = margin + ex * cell + cell // 2
        ecy = margin + ey * cell + cell // 2
        escort_sprite = npc_sprite_image(session.escort_kind or "maloy")
        escort_ring = (80, 190, 230)
        if escort_sprite is not None:
            _paste_circle(
                canvas,
                escort_sprite,
                ecx,
                ecy,
                MISSION_NPC_GRID_DIAMETER,
                ring_color=escort_ring,
                ring_width=4,
            )
        else:
            _draw_enemy_icon(ImageDraw.Draw(canvas), ecx, ecy, marauder=False)
            ImageDraw.Draw(canvas).ellipse(
                (ecx - 28, ecy - 28, ecx + 28, ecy + 28),
                outline=escort_ring,
                width=3,
            )

    # Игрок — аватар персонажа в зелёном кольце.
    px, py = session.player
    pcx = margin + px * cell + cell // 2
    pcy = margin + py * cell + cell // 2
    token = None
    if character is not None:
        try:
            from app.avatar_render import render_avatar

            token = render_avatar(character, rating_points=rating_points, width=160, height=160)
        except Exception:
            token = None
    if token is None:
        token = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
        td = ImageDraw.Draw(token)
        td.ellipse((20, 10, 140, 130), fill=(75, 85, 65), outline=(30, 35, 28), width=3)
        td.ellipse((45, 35, 115, 85), fill=(40, 48, 40))
        td.rectangle((45, 120, 115, 155), fill=(95, 75, 50))
    _paste_circle(canvas, token, pcx, pcy, 72, ring_color=(72, 220, 90), ring_width=5)
    facing = _normalize_facing(session.player_facing)
    fdx, fdy = MOVE_DELTAS[facing]
    tip = (pcx + fdx * 52, pcy + fdy * 52)
    base_l = (pcx - fdy * 12 - fdx * 8, pcy + fdx * 12 - fdy * 8)
    base_r = (pcx + fdy * 12 - fdx * 8, pcy - fdx * 12 - fdy * 8)
    draw.polygon([tip, base_l, base_r], fill=(255, 230, 90), outline=(50, 45, 30))
    # Панель.
    pl = margin + grid_px + 20
    pr = width - margin
    pt = margin - 8
    pb = height - margin + 8
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((pl, pt, pr, pb), radius=16, fill=(48, 50, 54, 255), outline=(100, 104, 110), width=2)

    thumb = (pl + 16, pt + 14, pr - 16, pt + 120)
    loc_img = _load_location_thumb(session.location)
    if loc_img is not None:
        _paste_rounded(canvas, loc_img, thumb, radius=10)
        ImageDraw.Draw(canvas).rounded_rectangle(thumb, radius=10, outline=(110, 120, 100), width=2)
    else:
        ImageDraw.Draw(canvas).rounded_rectangle(thumb, radius=10, fill=(30, 34, 28), outline=(90, 100, 80), width=2)

    draw = ImageDraw.Draw(canvas)
    title_font = _load_font(22)
    body = _load_font(17)
    small = _load_font(14)
    loc_font = title_font if len(session.location) <= 14 else _load_font(18)
    draw.text((pl + 18, pt + 128), session.location, fill=(245, 245, 245), font=loc_font)
    draw.text(
        (pl + 18, pt + 158),
        _mission_kind_label(session.kind, session.title),
        fill=(180, 200, 150),
        font=body,
    )

    y = pt + 190
    if session.kind == "clear_mutant":
        draw.text((pl + 18, y), f"Мутанты: {len(session.enemies)}", fill=(230, 180, 160), font=body)
    elif session.kind == "clear_marauder":
        draw.text((pl + 18, y), f"НПС: {len(session.npcs)}", fill=(230, 180, 160), font=body)
    elif session.kind == "escort":
        escort_txt = "Подопечный: жив" if session.escort_alive else "Подопечный: потерян"
        draw.text((pl + 18, y), escort_txt, fill=(140, 210, 235), font=body)
    else:
        done = len(session.collected)
        total = len(session.objectives)
        draw.text((pl + 18, y), f"Цели: {done}/{total}", fill=(150, 230, 170), font=body)
    threat_y = y + 26
    bits: list[str] = []
    if session.hazards:
        bits.append(f"ан.{len(session.hazards)}")
    if session.enemies:
        bits.append(f"мут.{len(session.enemies)}")
    if session.npcs:
        bits.append(f"нпс.{len(session.npcs)}")
    if bits:
        draw.text((pl + 18, threat_y), " ".join(bits), fill=(200, 170, 140), font=small)
    draw.text((pl + 18, y + 48), f"Ход {session.moves}/{session.max_moves}", fill=(200, 200, 200), font=small)
    if session.objectives_done:
        draw.text((pl + 18, y + 72), ">> Вернись на старт!", fill=(120, 255, 140), font=body)
    elif session.kind == "escort":
        draw.text((pl + 18, y + 72), "Веди к точке B (голубой — подопечный)", fill=(170, 200, 210), font=small)
    else:
        draw.text((pl + 18, y + 72), "Зелёные — цели, красные — враги", fill=(170, 170, 170), font=small)

    hp = int(character.health) if character else 0
    max_hp = int(effective_max_health(character)) if character else 100
    energy = int(character.energy) if character else 0
    max_energy = int(character.max_energy) if character else 100
    meds = 0
    if character is not None:
        meds = sum(int(character.inventory.get(k, 0)) for k in ("medkit", "medkit_army", "medkit_science"))

    bar_top = y + 120
    # HP bar
    draw.rounded_rectangle((pl + 18, bar_top, pr - 18, bar_top + 28), radius=8, fill=(30, 30, 34), outline=(90, 90, 95))
    fill_w = int((pr - pl - 44) * (hp / max(1, max_hp)))
    if fill_w > 0:
        draw.rounded_rectangle((pl + 20, bar_top + 2, pl + 20 + fill_w, bar_top + 26), radius=6, fill=(200, 60, 50))
    draw.text((pl + 24, bar_top + 5), f"HP {hp}/{max_hp}", fill=(255, 255, 255), font=small)

    draw.rounded_rectangle((pl + 18, bar_top + 40, pr - 18, bar_top + 68), radius=8, fill=(30, 30, 34), outline=(90, 90, 95))
    fill_w = int((pr - pl - 44) * (energy / max(1, max_energy)))
    if fill_w > 0:
        draw.rounded_rectangle((pl + 20, bar_top + 42, pl + 20 + fill_w, bar_top + 66), radius=6, fill=(50, 120, 210))
    draw.text((pl + 24, bar_top + 45), f"EN {energy}/{max_energy}", fill=(255, 255, 255), font=small)

    draw.text((pl + 18, bar_top + 84), f"Аптечки: {meds}", fill=(180, 200, 180), font=small)
    draw.text((pl + 18, pb - 50), session.title[:28], fill=(210, 210, 210), font=small)
    draw.text((pl + 18, pb - 28), "Стрелки - ход, кнопка - аптечка", fill=(190, 190, 190), font=small)

    enemy_slots = hud_slots_from_units(
        session.enemy_kinds, session.enemy_hp, session.enemy_max_hp, is_npc=False
    )
    enemy_slots += hud_slots_from_units(
        session.npc_kinds, session.npc_hp, session.npc_max_hp, is_npc=True
    )
    draw_enemy_hud(
        canvas,
        enemy_slots,
        panel_left=pl,
        panel_top=pt,
        panel_right=pr,
        panel_bottom=pb,
        y_top=bar_top + 112,
    )

    out = canvas.convert("RGB")
    buf = BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
