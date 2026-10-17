"""Уникальные способности мутантов на тактическом поле вылазок."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Callable

MOVE_DELTAS: dict[str, tuple[int, int]] = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}
OPPOSITE_DIRECTION = {"up": "down", "down": "up", "left": "right", "right": "left"}
PLAYER_LEFT_OF = {"up": "left", "down": "right", "left": "down", "right": "up"}
DOG_KINDS = frozenset({"blind_dog", "pseudodog"})
BLOODSUCKER_KIND = "bloodsucker"


@dataclass(frozen=True)
class MutantAbility:
    key: str
    title: str
    hint: str


MUTANT_ABILITIES: dict[str, MutantAbility] = {
    "blind_dog": MutantAbility(
        "blind_dog",
        "Гончая",
        "🐕 Слепой пёс — бежит и кусает по прямой; в стае бьёт сильнее.",
    ),
    "pseudodog": MutantAbility(
        "pseudodog",
        "Прыжок",
        "🐺 Псевдособака — рывок на 2 клетки по прямой за ход.",
    ),
    "tushkano": MutantAbility(
        "tushkano",
        "Рой",
        "🐀 Тушкан — ходит и бьёт только по диагонали (можно заранее просчитать угрозу).",
    ),
    "bloodsucker": MutantAbility(
        "bloodsucker",
        "Высасывание",
        "🩸 Кровосос — заходит в спину и бьёт ⬇️ со спины (+рад).",
    ),
    "flesh": MutantAbility(
        "flesh",
        "Живучесть",
        "🥩 Плоть — медленная, но часто переживает удар и остаётся на поле.",
    ),
    "controller": MutantAbility(
        "controller",
        "Пси-поле",
        "🧠 Контролёр — стоит на месте, давит разум (−HP) и сбивает направление.",
    ),
    "giant": MutantAbility(
        "giant",
        "Раздавливание",
        "👹 Псевдогигант — тяжёлый удар вблизи и топот на 2 клетки по прямой.",
    ),
    "burer": MutantAbility(
        "burer",
        "Телекинез",
        "🌀 Бюрер — держит дистанцию и бьёт пси-волной по прямой (2–3 клетки).",
    ),
    "zombie": MutantAbility(
        "zombie",
        "Заражение",
        "🧟 Зомбированный — неотступно идёт в лоб; удар накладывает рад.",
    ),
}


def _manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _normalize_facing(facing: str | None) -> str:
    key = str(facing or "down").strip().lower()
    return key if key in MOVE_DELTAS else "down"


def relative_attack_side(
    player_facing: str,
    player_pos: tuple[int, int],
    attacker_pos: tuple[int, int],
) -> str:
    if attacker_pos == player_pos:
        return "front"
    dx = attacker_pos[0] - player_pos[0]
    dy = attacker_pos[1] - player_pos[1]
    if abs(dx) >= abs(dy):
        atk_dir = "right" if dx > 0 else "left"
    else:
        atk_dir = "down" if dy > 0 else "up"
    facing = _normalize_facing(player_facing)
    if atk_dir == facing:
        return "front"
    if atk_dir == OPPOSITE_DIRECTION[facing]:
        return "back"
    if atk_dir == PLAYER_LEFT_OF[facing]:
        return "left"
    return "right"


def _behind_player_cell(session: Any) -> tuple[int, int]:
    facing = _normalize_facing(getattr(session, "player_facing", "down"))
    dx, dy = MOVE_DELTAS[OPPOSITE_DIRECTION[facing]]
    px, py = session.player
    return (px + dx, py + dy)


def _straight_line(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] == b[0] or a[1] == b[1]


def mutant_field_ability_warnings(kinds: list[str] | tuple[str, ...] | None) -> list[str]:
    if not kinds:
        return []
    seen: set[str] = set()
    lines: list[str] = []
    for kind in kinds:
        key = str(kind)
        if key in seen:
            continue
        seen.add(key)
        ability = MUTANT_ABILITIES.get(key)
        if ability is not None:
            lines.append(ability.hint)
    return lines


def mutant_chase_target(session: Any, kind: str | None) -> tuple[int, int]:
    if kind == BLOODSUCKER_KIND:
        return _behind_player_cell(session)
    return session.player


def mutant_should_move_when_chasing(session: Any, kind: str | None, pos: tuple[int, int]) -> bool:
    if kind == "controller":
        return _manhattan(pos, session.player) > 3
    if kind == "flesh":
        return random.random() < 0.55
    if kind == "burer":
        dist = _manhattan(pos, session.player)
        return dist > 3 or dist < 2
    return True


def _dog_straight_step(
    pos: tuple[int, int],
    target: tuple[int, int],
    candidates: list[tuple[int, int]],
) -> tuple[int, int] | None:
    px, py = target
    x, y = pos
    preferred: list[tuple[int, int]] = []
    if px == x:
        preferred.append((x, y + (1 if py > y else -1)))
    elif py == y:
        preferred.append((x + (1 if px > x else -1), y))
    else:
        if abs(px - x) >= abs(py - y):
            preferred.append((x + (1 if px > x else -1), y))
        else:
            preferred.append((x, y + (1 if py > y else -1)))
    for cell in preferred:
        if cell in candidates:
            return cell
    return None


def _burer_step(
    pos: tuple[int, int],
    player_pos: tuple[int, int],
    candidates: list[tuple[int, int]],
) -> tuple[int, int] | None:
    ideal = 2

    def score(cell: tuple[int, int]) -> tuple[int, int]:
        dist = _manhattan(cell, player_pos)
        straight = 0 if _straight_line(cell, player_pos) else 1
        return (abs(dist - ideal), straight, dist)

    ranked = sorted(candidates, key=score)
    return ranked[0] if ranked else None


def _is_diagonal_adjacent(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return abs(a[0] - b[0]) == 1 and abs(a[1] - b[1]) == 1


def _diagonal_cells(pos: tuple[int, int], grid: int) -> list[tuple[int, int]]:
    cells: list[tuple[int, int]] = []
    x, y = pos
    for dx in (-1, 1):
        for dy in (-1, 1):
            nx, ny = x + dx, y + dy
            if 0 <= nx < grid and 0 <= ny < grid:
                cells.append((nx, ny))
    return cells


def mutant_hostile_move_candidates(kind: str | None, pos: tuple[int, int], grid: int) -> list[tuple[int, int]]:
    """Соседние клетки для шага мутанта (тушкан — только диагональ)."""
    if kind == "tushkano":
        return _diagonal_cells(pos, grid)
    return list(iter_adjacent_cells(pos, grid))


def _diagonal_swarm_step(
    pos: tuple[int, int],
    target: tuple[int, int],
    candidates: list[tuple[int, int]],
) -> tuple[int, int] | None:
    diag = [c for c in candidates if _is_diagonal_adjacent(pos, c)]
    if not diag:
        return None
    cur = _chebyshev(pos, target)
    closer = [c for c in diag if _chebyshev(c, target) < cur]
    same = [c for c in diag if _chebyshev(c, target) == cur]
    pool = closer or same
    return random.choice(pool) if pool else None


def mutant_pick_move_step(
    session: Any,
    kind: str | None,
    pos: tuple[int, int],
    target: tuple[int, int],
    candidates: list[tuple[int, int]],
) -> tuple[int, int] | None:
    if kind == "burer":
        picked = _burer_step(pos, session.player, candidates)
        if picked is not None:
            return picked
    if kind == "tushkano":
        return _diagonal_swarm_step(pos, target, candidates)
    if kind in DOG_KINDS or kind == "giant":
        straight = _dog_straight_step(pos, target, candidates)
        if straight is not None:
            return straight
    cur_dist = _manhattan(pos, target)
    closer = [cell for cell in candidates if _manhattan(cell, target) < cur_dist]
    same = [cell for cell in candidates if _manhattan(cell, target) == cur_dist]
    if closer:
        return random.choice(closer)
    if same:
        return random.choice(same)
    return None


def mutant_extra_move_step(
    kind: str | None,
    pos: tuple[int, int],
    first_step: tuple[int, int],
    target: tuple[int, int],
    *,
    grid: int,
    occupied: set[tuple[int, int]],
    player_pos: tuple[int, int],
) -> tuple[int, int] | None:
    """Псевдособака: второй рывок по той же прямой."""
    if kind != "pseudodog" or first_step == pos:
        return None
    if _manhattan(first_step, target) <= 1:
        return None
    dx = first_step[0] - pos[0]
    dy = first_step[1] - pos[1]
    if dx == 0 and dy == 0:
        return None
    nxt = (first_step[0] + dx, first_step[1] + dy)
    if not (0 <= nxt[0] < grid and 0 <= nxt[1] < grid):
        return None
    if nxt == player_pos or nxt in occupied:
        return None
    if abs(dx) + abs(dy) != 1:
        return None
    return nxt


def _chebyshev(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def mutant_can_melee_attack(session: Any, kind: str, enemy_pos: tuple[int, int]) -> bool:
    if kind == "tushkano":
        return _is_diagonal_adjacent(enemy_pos, session.player)
    if _manhattan(enemy_pos, session.player) != 1:
        return False
    if kind in DOG_KINDS:
        return _straight_line(enemy_pos, session.player)
    if kind == BLOODSUCKER_KIND:
        return relative_attack_side(session.player_facing, session.player, enemy_pos) == "back"
    if kind in {"controller", "burer"}:
        return False
    return True


def mutant_can_ranged_attack(session: Any, kind: str, enemy_pos: tuple[int, int]) -> bool:
    dist = _manhattan(enemy_pos, session.player)
    if not _straight_line(enemy_pos, session.player):
        return False
    if kind == "giant":
        return dist == 2
    if kind == "burer":
        return 2 <= dist <= 3
    return False


def mutant_damage_multiplier(kind: str, hit_side: str, enemy_kinds: list[str] | None) -> float:
    mult = 1.0
    if kind == BLOODSUCKER_KIND and hit_side == "back":
        mult *= 1.45
    elif hit_side == "back":
        mult *= 1.22
    elif hit_side in {"left", "right"}:
        mult *= 1.08
    if kind == "blind_dog":
        pack = sum(1 for k in (enemy_kinds or []) if k in DOG_KINDS) - 1
        mult *= 1.0 + min(0.4, max(0, pack) * 0.2)
    if kind == "pseudodog" and hit_side == "front":
        mult *= 1.15
    if kind == "flesh":
        mult *= 0.78
    if kind == "giant":
        mult *= 1.28
    if kind == "zombie" and hit_side in {"left", "right", "back"}:
        mult *= 0.85
    if kind == "tushkano":
        mult *= 0.50
    return mult


def mutant_extra_radiation(kind: str, hit_side: str) -> int:
    if kind == BLOODSUCKER_KIND and hit_side == "back":
        return 6
    if kind == "zombie":
        return 4
    return 0


def mutant_survives_melee(kind: str) -> bool:
    return kind == "flesh" and random.random() < 0.4


def mutant_attack_ability_tag(kind: str, *, ranged: bool = False) -> str:
    ability = MUTANT_ABILITIES.get(kind)
    if ability is None:
        return ""
    if ranged:
        if kind == "burer":
            return " [телекинез]"
        if kind == "giant":
            return " [топот]"
    return f" [{ability.title.lower()}]"


def apply_mutant_turn_effects(storage: Any, telegram_id: int, session: Any) -> list[str]:
    """Пассивы мутантов в конце движения врагов (контролёр и т.п.)."""
    notes: list[str] = []
    for pos, kind in zip(session.enemies, session.enemy_kinds):
        if kind != "controller":
            continue
        if _manhattan(pos, session.player) > 2:
            continue
        if random.random() > 0.35:
            continue
        session.player_facing = random.choice(list(MOVE_DELTAS.keys()))
        notes.append("🧠 Контролёр сбивает ориентир — направление взгляда потеряно!")
        break
    return notes


def iter_adjacent_cells(pos: tuple[int, int], grid: int):
    for dx, dy in MOVE_DELTAS.values():
        nx, ny = pos[0] + dx, pos[1] + dy
        if 0 <= nx < grid and 0 <= ny < grid:
            yield (nx, ny)
