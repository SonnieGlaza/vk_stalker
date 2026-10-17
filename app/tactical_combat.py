"""Общая логика тактической стрельбы: дальность оружия, луч, укрытия, патроны."""

from __future__ import annotations

import random
from typing import Callable

STALE_TURN_MESSAGE = "Ход уже обработан — нажми «Обновить»."

from app.game_logic import WEAPON_RATING_BY_NAME, _weapon_rating, apply_incoming_damage
from app.storage import Character, Storage

MOVE_DELTAS: dict[str, tuple[int, int]] = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

COVER_HIT_CHANCE = 0.5
BASE_COVER_ARMOR_BONUS = 5
NPC_MOVE_CHANCE = 0.25
NPC_MAX_SHOT_DAMAGE = 15

_PISTOL = frozenset({"ПМ", "Фора-12"})
_SHOTGUN = frozenset({"Обрез", "Чейзер-13", "СПАС-12"})
_ASSAULT = frozenset({"Гадюка-5", "АКС-74У", "АК-74", "ТРс-301", "ИЛ86", "АН-94", "ГП37", "РП-74"})
_SNIPER = frozenset({"Винтарь ВС", "СВДм-2", "ВСС «Серебряный сталкер»"})
_GAUSS = frozenset({"Гаусс-пушка", "РПК «Чемпион Зоны»"})
_RACCOON = frozenset({"Енот"})

NPC_WEAPONS = ("ПМ", "Обрез", "АК-74", "СПАС-12", "СВДм-2", "Гаусс-пушка")

AMMO_PISTOL_KEY = "ammo_pistol"
AMMO_SHOTGUN_KEY = "ammo_shotgun"
AMMO_RIFLE_KEY = "ammo_rifle"
AMMO_GAUSS_KEY = "ammo_gauss"
TYPED_AMMO_KEYS: tuple[str, ...] = (AMMO_PISTOL_KEY, AMMO_SHOTGUN_KEY, AMMO_RIFLE_KEY, AMMO_GAUSS_KEY)

WEAPON_AMMO_KEY: dict[str, str | None] = {
    "Нож": None,
    "ПМ": AMMO_PISTOL_KEY,
    "Фора-12": AMMO_PISTOL_KEY,
    "Обрез": AMMO_SHOTGUN_KEY,
    "Чейзер-13": AMMO_SHOTGUN_KEY,
    "СПАС-12": AMMO_SHOTGUN_KEY,
    "Гадюка-5": AMMO_RIFLE_KEY,
    "АКС-74У": AMMO_RIFLE_KEY,
    "АК-74": AMMO_RIFLE_KEY,
    "ТРс-301": AMMO_RIFLE_KEY,
    "ИЛ86": AMMO_RIFLE_KEY,
    "АН-94": AMMO_RIFLE_KEY,
    "ГП37": AMMO_RIFLE_KEY,
    "РП-74": AMMO_RIFLE_KEY,
    "Винтарь ВС": AMMO_RIFLE_KEY,
    "СВДм-2": AMMO_RIFLE_KEY,
    "ВСС «Серебряный сталкер»": AMMO_RIFLE_KEY,
    "Енот": AMMO_RIFLE_KEY,
    "Гаусс-пушка": AMMO_GAUSS_KEY,
    "РПК «Чемпион Зоны»": AMMO_GAUSS_KEY,
}

# Сила оружия (WEAPON_RATING_BY_NAME) выступает множителем урона: 1 сила = 5 урона.
WEAPON_DAMAGE_PER_POWER = 5
WEAPON_DAMAGE_BY_NAME: dict[str, int] = {
    name: rating * WEAPON_DAMAGE_PER_POWER for name, rating in WEAPON_RATING_BY_NAME.items()
}


def weapon_is_shotgun(weapon_name: str) -> bool:
    return weapon_name in _SHOTGUN


def weapon_ammo_type(weapon_name: str) -> str | None:
    if weapon_name in WEAPON_AMMO_KEY:
        return WEAPON_AMMO_KEY[weapon_name]
    return AMMO_RIFLE_KEY if _weapon_rating(weapon_name) >= 4 else AMMO_PISTOL_KEY


def weapon_shoot_range(weapon_name: str) -> int:
    if weapon_name == "Нож":
        return 0
    if weapon_name in _GAUSS or weapon_name in _RACCOON:
        return 4
    if weapon_name in _SNIPER:
        return 3
    if weapon_name in _ASSAULT or weapon_name in _SHOTGUN:
        return 2
    if weapon_name in _PISTOL:
        return 1
    rating = _weapon_rating(weapon_name)
    if rating <= 4:
        return 1
    if rating <= 8:
        return 2
    if rating <= 9:
        return 3
    return 4


def weapon_damage(weapon_name: str, *, variance: int = 2) -> int:
    base = WEAPON_DAMAGE_BY_NAME.get(weapon_name)
    if base is None:
        base = max(4, _weapon_rating(weapon_name) * WEAPON_DAMAGE_PER_POWER)
    return max(3, base + random.randint(-variance, variance))


def npc_weapon_damage(weapon_name: str, *, mult: float = 1.0) -> int:
    raw = weapon_damage(weapon_name, variance=1)
    return min(NPC_MAX_SHOT_DAMAGE, max(4, int(raw * mult)))


def _in_grid(cell: tuple[int, int], grid: int) -> bool:
    return 0 <= cell[0] < grid and 0 <= cell[1] < grid


def shotgun_blast_cells(origin: tuple[int, int], direction: str, *, grid: int) -> list[tuple[int, int]]:
    """Дробь: 1-я клетка, 2-я по центру и с двух сторон."""
    delta = MOVE_DELTAS.get(direction)
    if delta is None:
        return []
    perp = (-delta[1], delta[0])
    cells: list[tuple[int, int]] = []
    first = (origin[0] + delta[0], origin[1] + delta[1])
    if _in_grid(first, grid):
        cells.append(first)
    second = (origin[0] + delta[0] * 2, origin[1] + delta[1] * 2)
    if _in_grid(second, grid):
        cells.append(second)
        for side in (perp, (-perp[0], -perp[1])):
            side_cell = (second[0] + side[0], second[1] + side[1])
            if _in_grid(side_cell, grid):
                cells.append(side_cell)
    return cells


def ray_cast_first_hit(
    origin: tuple[int, int],
    direction: str,
    *,
    grid: int,
    max_range: int,
    blockers: set[tuple[int, int]],
    targets: dict[tuple[int, int], str],
) -> tuple[tuple[int, int] | None, str]:
    delta = MOVE_DELTAS.get(direction)
    if delta is None:
        return None, ""
    for step in range(1, max_range + 1):
        cell = (origin[0] + delta[0] * step, origin[1] + delta[1] * step)
        if not _in_grid(cell, grid):
            break
        if cell in targets:
            return cell, targets[cell]
        if cell in blockers:
            break
    return None, ""


def collect_player_shot_hits(
    origin: tuple[int, int],
    direction: str,
    *,
    grid: int,
    weapon_name: str,
    blockers: set[tuple[int, int]],
    targets: dict[tuple[int, int], str],
    cover: set[tuple[int, int]],
) -> list[tuple[tuple[int, int], str]]:
    hits: list[tuple[tuple[int, int], str]] = []
    seen: set[tuple[int, int]] = set()
    if weapon_is_shotgun(weapon_name):
        for cell in shotgun_blast_cells(origin, direction, grid=grid):
            if cell in blockers or cell in seen:
                continue
            if cell in targets and not cover_blocks_shot(cell, cover):
                hits.append((cell, targets[cell]))
                seen.add(cell)
        return hits
    hit_cell, kind = ray_cast_first_hit(
        origin,
        direction,
        grid=grid,
        max_range=weapon_shoot_range(weapon_name),
        blockers=blockers,
        targets=targets,
    )
    if hit_cell is not None and not cover_blocks_shot(hit_cell, cover):
        hits.append((hit_cell, kind))
    return hits


def hostile_shot_hit(
    origin: tuple[int, int],
    direction: str,
    *,
    grid: int,
    weapon_name: str,
    blockers: set[tuple[int, int]],
    targets: dict[tuple[int, int], str],
    cover: set[tuple[int, int]],
) -> tuple[tuple[int, int], str] | None:
    if weapon_is_shotgun(weapon_name):
        for cell in shotgun_blast_cells(origin, direction, grid=grid):
            if cell in blockers:
                continue
            if cell in targets:
                if cover_blocks_shot(cell, cover):
                    return None
                return cell, targets[cell]
        return None
    hit_cell, kind = ray_cast_first_hit(
        origin,
        direction,
        grid=grid,
        max_range=weapon_shoot_range(weapon_name),
        blockers=blockers,
        targets=targets,
    )
    if hit_cell is None or cover_blocks_shot(hit_cell, cover):
        return None
    return hit_cell, kind


def cover_blocks_shot(cell: tuple[int, int], cover: set[tuple[int, int]]) -> bool:
    return cell in cover and random.random() > COVER_HIT_CHANCE


def extra_armor_from_cell(
    cell: tuple[int, int],
    base_cover: set[tuple[int, int]],
    *,
    bonus: int = BASE_COVER_ARMOR_BONUS,
) -> int:
    return bonus if cell in base_cover else 0


def apply_armor_bonus(raw_damage: int, extra_armor: int, *, min_damage: int = 1) -> int:
    return max(min_damage + 10, raw_damage - extra_armor)


def random_cardinal_direction() -> str:
    return random.choice(list(MOVE_DELTAS.keys()))


def aim_hostile_shot_direction(
    origin: tuple[int, int],
    *,
    grid: int,
    max_range: int,
    player_positions: dict[int, tuple[int, int]],
    blockers: set[tuple[int, int]],
    weapon_name: str = "ПМ",
) -> str | None:
    best_dir: str | None = None
    best_dist = 10**9
    targets = {player_positions[pid]: str(pid) for pid in player_positions}
    for direction in MOVE_DELTAS:
        if weapon_is_shotgun(weapon_name):
            hit = hostile_shot_hit(
                origin,
                direction,
                grid=grid,
                weapon_name=weapon_name,
                blockers=blockers,
                targets=targets,
                cover=set(),
            )
            if hit is None:
                continue
            hit_cell = hit[0]
        else:
            hit_cell, _ = ray_cast_first_hit(
                origin,
                direction,
                grid=grid,
                max_range=max_range,
                blockers=blockers,
                targets=targets,
            )
            if hit_cell is None:
                continue
        dist = abs(hit_cell[0] - origin[0]) + abs(hit_cell[1] - origin[1])
        if dist < best_dist:
            best_dist = dist
            best_dir = direction
    return best_dir


def random_hostile_shots(
    hostiles: list[tuple[int, int]],
    weapons: list[str],
    *,
    grid: int,
    player_positions: dict[int, tuple[int, int]],
    player_hp: dict[str, int],
    player_characters: dict[int, Character | None],
    cover: set[tuple[int, int]],
    base_cover: set[tuple[int, int]],
    damage_fn: Callable[[str], int],
    shoot_chance: float = 0.55,
    aim_at_players: bool = False,
) -> list[str]:
    notes: list[str] = []
    if not hostiles:
        return notes
    for idx, pos in enumerate(hostiles):
        if random.random() > shoot_chance:
            continue
        weapon = weapons[idx] if idx < len(weapons) else "ПМ"
        rng = weapon_shoot_range(weapon)
        direction = None
        if aim_at_players:
            direction = aim_hostile_shot_direction(
                pos,
                grid=grid,
                max_range=rng,
                player_positions=player_positions,
                blockers=cover,
                weapon_name=weapon,
            )
        if direction is None:
            direction = random_cardinal_direction()
        targets = {player_positions[pid]: str(pid) for pid in player_positions}
        hit = hostile_shot_hit(
            pos,
            direction,
            grid=grid,
            weapon_name=weapon,
            blockers=cover,
            targets=targets,
            cover=cover,
        )
        if hit is None:
            continue
        hit_cell, hit_kind = hit
        pid = int(hit_kind)
        raw = damage_fn(weapon)
        extra = extra_armor_from_cell(hit_cell, base_cover)
        pre = apply_armor_bonus(raw, extra)
        character = player_characters.get(pid)
        if character is not None:
            dmg = apply_incoming_damage(pre, character, min_damage=1)
        else:
            dmg = pre
        key = str(pid)
        player_hp[key] = max(0, player_hp.get(key, 0) - dmg)
        if dmg <= 0:
            notes.append(f"Вражеский огонь ({weapon}): броня сбила удар.")
        else:
            notes.append(f"Вражеский огонь ({weapon}): −{dmg} HP.")
    return notes


def consume_shot_ammo(storage: Storage, telegram_id: int, weapon_name: str):
    from app.game_logic import ActionResult, ITEM_LABELS

    ammo_key = weapon_ammo_type(weapon_name)
    if ammo_key is None:
        return None
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Персонаж не найден.")
    stock = int(player.inventory.get(ammo_key, 0))
    if stock <= 0:
        label = ITEM_LABELS.get(ammo_key, ammo_key)
        return ActionResult(
            False,
            f"Нет подходящих патронов ({label}). Купи у бармена — каждый тип только под своё оружие.",
        )
    if not storage.remove_item(telegram_id, ammo_key, 1):
        return ActionResult(False, "Не удалось списать патрон.")
    return None


def move_toward(pos: tuple[int, int], target: tuple[int, int], steps: int = 1) -> tuple[int, int]:
    x, y = pos
    for _ in range(steps):
        dx = 0 if x == target[0] else (1 if target[0] > x else -1)
        dy = 0 if y == target[1] else (1 if target[1] > y else -1)
        if abs(target[0] - x) >= abs(target[1] - y):
            x += dx
        else:
            y += dy
    return x, y


def spawn_edge_positions(grid: int, count: int, forbidden: set[tuple[int, int]]) -> list[tuple[int, int]]:
    edges: list[tuple[int, int]] = []
    for x in range(grid):
        for y in (0, grid - 1):
            cell = (x, y)
            if cell not in forbidden:
                edges.append(cell)
    for y in range(1, grid - 1):
        for x in (0, grid - 1):
            cell = (x, y)
            if cell not in forbidden:
                edges.append(cell)
    random.shuffle(edges)
    return edges[:count]


def manhattan_distance(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def iter_adjacent_cells(pos: tuple[int, int], grid: int):
    for dx, dy in MOVE_DELTAS.values():
        nx, ny = pos[0] + dx, pos[1] + dy
        if 0 <= nx < grid and 0 <= ny < grid:
            yield (nx, ny)


def best_step_toward(
    origin: tuple[int, int],
    target: tuple[int, int],
    *,
    grid: int,
    blocked: set[tuple[int, int]],
    forbidden: set[tuple[int, int]] | None = None,
) -> tuple[int, int]:
    forbidden = forbidden or set()
    best = origin
    best_dist = manhattan_distance(origin, target)
    for nxt in iter_adjacent_cells(origin, grid):
        if nxt in forbidden or nxt in blocked:
            continue
        dist_new = manhattan_distance(nxt, target)
        if dist_new < best_dist:
            best_dist = dist_new
            best = nxt
    return best
