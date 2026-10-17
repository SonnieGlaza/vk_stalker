from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.artifact_hunt import (
    FONT_CANDIDATES,
    PROJECT_ROOT,
    _character_rating_points,
    _cover_crop,
    _draw_cell,
    _glow,
    _load_font,
    _load_hunt_map,
    _load_location_thumb,
    _load_search_field_background,
    _paste_circle,
    _paste_rounded,
    _player_grid_token,
)
from app.game_logic import (
    ActionResult,
    ITEM_LABELS,
    STASH_CONSUMABLE_KEYS,
    STASH_GEAR_TIER_CHANCES,
    STASH_ARMOR_BY_TIER,
    STASH_WEAPON_BY_TIER,
    STASH_CONSUMABLE_DROP_CHANCE,
    STASH_CONSUMABLE_DROP_CHANCE_BY_KEY,
    _is_dead,
    _dead_block_text,
    effective_max_health,
    is_traveling,
    travel_block_text,
)
from app.storage import Storage


STASH_META_PREFIX = "stash_hunt:"
STASH_GRID_SIZE = 15
STASH_MAX_MOVES = 60
STASH_RAD_EVERY_STEPS = 3
STASH_RAD_PER_TICK = 1
STASH_MINUTE_MOVES = 10
STASH_RAD_PER_MINUTE = 5
STASH_AMBUSH_CHANCE_MIN = 2
STASH_AMBUSH_CHANCE_MAX = 5
STASH_COORDINATE_PRICE = 3500
STASH_COORDINATE_KEY = "stash_coordinates"
STASH_COORDS_DROP_CHANCE = 3
STASH_INTEL_DIARY_CHANCE = 10  # независимый шанс на дневник в обычном схроне

# Раскладка «здания»: комнаты 5×5, соединённые дверными проёмами.
# Стены — клетки сетки; walkable — всё, кроме стен. Проёмы — середины стен.
STASH_WALLS: set[tuple[int, int]] = set()
for _x in (5, 10):
    for _y in range(15):
        if _y not in (2, 7, 12):  # вертикальные стены, проёмы по серединам рядов
            STASH_WALLS.add((_x, _y))
for _y in (5, 10):
    for _x in range(15):
        if _x not in (2, 7, 12):  # горизонтальные стены, проёмы по центрам комнат
            STASH_WALLS.add((_x, _y))
for _i in range(15):  # внешний периметр
    STASH_WALLS.add((_i, 0))
    STASH_WALLS.add((_i, 14))
    STASH_WALLS.add((0, _i))
    STASH_WALLS.add((14, _i))
STASH_WALLS -= {(7, 0), (7, 14), (0, 7), (14, 7)}  # проёмы внутрь здания


STASH_WALKABLE: set[tuple[int, int]] = (
    {(x, y) for x in range(STASH_GRID_SIZE) for y in range(STASH_GRID_SIZE)} - STASH_WALLS
)

MOVE_DELTAS: dict[str, tuple[int, int]] = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

AMBUSH_TYPES: tuple[tuple[str, str, int], ...] = (
    ("Слепые псы", "слепые псы", 8),
    ("Псевдособаки", "псевдособаки", 10),
    ("Кабаны", "кабаны", 7),
    ("Бандиты", "бандиты", 12),
    ("Снорк", "снорк", 15),
    ("Кровосос", "кровосос", 18),
)


@dataclass
class StashSession:
    location: str
    player: tuple[int, int]
    stashes: list[tuple[int, int]]
    collected: list[tuple[int, int]]
    moves: int
    steps: int
    rad_gained: int
    grid: int = STASH_GRID_SIZE
    max_moves: int = STASH_MAX_MOVES
    found: bool = False
    source: str = "found"
    mutants: list[tuple[int, int]] = field(default_factory=list)

    @property
    def stash_count(self) -> int:
        return max(1, len(self.stashes))

    @property
    def collected_count(self) -> int:
        return len(self.collected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "player": list(self.player),
            "stashes": [list(p) for p in self.stashes],
            "collected": [list(p) for p in self.collected],
            "moves": self.moves,
            "steps": self.steps,
            "rad_gained": self.rad_gained,
            "grid": self.grid,
            "max_moves": self.max_moves,
            "found": self.found,
            "source": self.source,
            "mutants": [list(m) for m in self.mutants],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StashSession:
        raw_mutants = raw.get("mutants") or []
        raw_stashes = raw.get("stashes")
        if not isinstance(raw_stashes, list) or not raw_stashes:
            # Старые сессии: один тайник в поле stash.
            old_stash = raw.get("stash")
            if isinstance(old_stash, (list, tuple)) and len(old_stash) >= 2:
                raw_stashes = [old_stash]
            else:
                raw_stashes = [(7, 7)]
        stashes = [(int(p[0]), int(p[1])) for p in raw_stashes if len(p) >= 2]
        if not stashes:
            stashes = [(7, 7)]
        collected = [(int(p[0]), int(p[1])) for p in (raw.get("collected") or []) if len(p) >= 2]
        return cls(
            location=str(raw.get("location") or ""),
            player=(int(raw["player"][0]), int(raw["player"][1])),
            stashes=stashes,
            collected=collected,
            moves=int(raw.get("moves") or 0),
            steps=int(raw.get("steps") or 0),
            rad_gained=int(raw.get("rad_gained") or 0),
            grid=int(raw.get("grid") or STASH_GRID_SIZE),
            max_moves=int(raw.get("max_moves") or STASH_MAX_MOVES),
            found=bool(raw.get("found")),
            source=str(raw.get("source") or "found"),
            mutants=[(int(m[0]), int(m[1])) for m in raw_mutants if len(m) >= 2],
        )


def _stash_meta_key(telegram_id: int) -> str:
    return f"{STASH_META_PREFIX}{int(telegram_id)}"


def get_stash_session(storage: Storage, telegram_id: int) -> StashSession | None:
    raw = storage.get_meta(_stash_meta_key(telegram_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return StashSession.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def save_stash_session(storage: Storage, telegram_id: int, session: StashSession) -> None:
    storage.set_meta(_stash_meta_key(telegram_id), json.dumps(session.to_dict(), ensure_ascii=False))


def clear_stash_session(storage: Storage, telegram_id: int) -> None:
    storage.delete_meta(_stash_meta_key(telegram_id))


def _chebyshev(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _random_free_cell(grid: int, forbidden: set[tuple[int, int]]) -> tuple[int, int]:
    free = [(x, y) for x in range(grid) for y in range(grid) if (x, y) not in forbidden and (x, y) in STASH_WALKABLE]
    if not free:
        return (0, 0)
    return random.choice(free)


def _build_stash_session(character: Any, source: str) -> StashSession:
    grid = STASH_GRID_SIZE
    forbidden: set[tuple[int, int]] = set()
    player = _random_free_cell(grid, forbidden)
    forbidden.add(player)
    # На карте 3–4 тайника — собери все, чтобы завершить поиск.
    stash_count = random.randint(3, 4)
    stashes: list[tuple[int, int]] = []
    for _ in range(stash_count):
        stash = _random_free_cell(grid, forbidden)
        attempts = 0
        while _chebyshev(player, stash) < grid // 3 and attempts < 50:
            stash = _random_free_cell(grid, forbidden)
            attempts += 1
        stashes.append(stash)
        forbidden.add(stash)
    # Мутанты на поле: 50% — один, 25% — двое (остальное — никого).
    mutants: list[tuple[int, int]] = []
    roll = random.random()
    mutant_count = 2 if roll < 0.25 else (1 if roll < 0.75 else 0)
    for _ in range(mutant_count):
        cell = _random_free_cell(grid, forbidden)
        if cell == player or cell in stashes:
            continue
        mutants.append(cell)
        forbidden.add(cell)
    return StashSession(
        location=character.location,
        player=player,
        stashes=stashes,
        collected=[],
        moves=0,
        steps=0,
        rad_gained=0,
        source=source,
        mutants=mutants,
    )


def stash_status_caption(session: StashSession, character: Any | None = None) -> str:
    lines = [
        f"Поиск схрона — {session.location}",
        f"Ход {session.moves}/{session.max_moves} · рад +{session.rad_gained}",
        f"Тайники: {session.collected_count}/{session.stash_count}",
    ]
    if character is not None:
        lines.append(
            f"HP {character.health}/{effective_max_health(character)} · "
            f"☢ {character.radiation} · ⚡ {character.energy}"
        )
    lines.append("Собери все тайники на карте. Берегись мутантов и бандитов.")
    return "\n".join(lines)


def start_stash_hunt(storage: Storage, telegram_id: int, *, source: str = "found") -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    blocked = travel_block_text(player)
    if blocked:
        return ActionResult(False, blocked)
    if is_traveling(player):
        return ActionResult(False, "Нельзя искать схроны в пути.")

    existing = get_stash_session(storage, telegram_id)
    if existing is not None and not existing.found:
        image = render_stash_for_player(storage, telegram_id, existing, player)
        return ActionResult(
            False,
            "У тебя уже идёт поиск схрона. Продолжай с карты.",
            payload={"stash_image": image, "stash_active": True, "caption": stash_status_caption(existing, player)},
        )

    from app.player_busy import player_busy_reason

    busy = player_busy_reason(storage, telegram_id, skip="stash", auto_recover=False)
    if busy:
        return ActionResult(False, busy)

    if source == "found":
        owned = int(player.inventory.get(STASH_COORDINATE_KEY, 0))
        if owned <= 0:
            return ActionResult(
                False,
                "Нет координат хабара. Найди их на охоте за артефактами или купи у торговца.",
            )
        if not storage.remove_item(telegram_id, STASH_COORDINATE_KEY, 1):
            return ActionResult(False, "Не удалось использовать координаты хабара.")

    # buy_item already deducted the price before calling us; no double-charge.

    session = _build_stash_session(player, source)
    save_stash_session(storage, telegram_id, session)
    player = storage.get_character(telegram_id, refresh_energy=False) or player
    image = render_stash_for_player(storage, telegram_id, session, player)
    caption = stash_status_caption(session, player)
    if source == "buy":
        note = f"Координаты схрона куплены за {STASH_COORDINATE_PRICE} RU."
    else:
        note = "Ты нашёл координаты схрона! Ищи на карте."
    return ActionResult(
        True,
        note,
        payload={"stash_image": image, "stash_active": True, "caption": caption, "stash_started": True},
    )


def abandon_stash_hunt(storage: Storage, telegram_id: int) -> ActionResult:
    session = get_stash_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Активного поиска схрона нет.")
    clear_stash_session(storage, telegram_id)
    return ActionResult(
        True,
        f"Ты бросил поиск схрона на «{session.location}».\n"
        f"Ходов потрачено: {session.moves}, рад +{session.rad_gained}.",
        payload={"stash_active": False},
    )


def _roll_stash_loot(storage: Storage, telegram_id: int, location: str) -> list[tuple[str, int]]:
    """Лут из схрона: список (ключ предмета, количество).

    Количество обычно 1; стройматериалы выпадают всегда, 1–3 шт. за обыск.
    """
    from app.faction_buildings import MATERIALS_ITEM_KEY

    loot: list[tuple[str, int]] = []
    if random.random() * 100 < STASH_CONSUMABLE_DROP_CHANCE:
        consumable = random.choice(STASH_CONSUMABLE_KEYS)
        if random.random() * 100 < STASH_CONSUMABLE_DROP_CHANCE_BY_KEY.get(consumable, 100):
            loot.append((consumable, 1))
    gear_roll = random.random() * 100
    cumulative = 0.0
    for tier, chance in STASH_GEAR_TIER_CHANCES:
        cumulative += chance
        if gear_roll < cumulative:
            if random.random() < 0.5:
                pool = STASH_WEAPON_BY_TIER.get(tier, ())
            else:
                pool = STASH_ARMOR_BY_TIER.get(tier, ())
            if pool:
                loot.append((random.choice(pool), 1))
            break
    if not loot:
        loot.append((random.choice(STASH_CONSUMABLE_KEYS), 1))
    # Стройматериалы: всегда 1–3 шт. за поиск.
    loot.append((MATERIALS_ITEM_KEY, random.randint(1, 3)))
    # Информация: обычный схрон — только дневник, независимый ролл ~10%.
    if random.random() * 100 < STASH_INTEL_DIARY_CHANCE:
        loot.append(("intel_diary", 1))
    return loot


def _finish_stash_success(
    storage: Storage,
    telegram_id: int,
    session: StashSession,
    *,
    collected_text: str = "",
) -> ActionResult:
    clear_stash_session(storage, telegram_id)
    if not collected_text:
        # Страховка для старых сессий: единственный тайник собирается здесь же.
        loot_keys = _roll_stash_loot(storage, telegram_id, session.location)
        labels: list[str] = []
        for key, qty in loot_keys:
            storage.add_item(telegram_id, key, qty)
            label = ITEM_LABELS.get(key, key)
            labels.append(f"{label} x{qty}" if qty != 1 else f"{label} x1")
        collected_text = ", ".join(labels)
    storage.add_player_stat(telegram_id, "quests_completed", 1)
    total = session.stash_count
    return ActionResult(
        True,
        f"Все тайники собраны на «{session.location}» ({session.collected_count}/{total})!\n"
        f"Содержимое: {collected_text}.\n"
        f"Ходов: {session.moves}, рад +{session.rad_gained}.",
        payload={"stash_active": False, "stash_done": True},
    )


def _try_ambush(storage: Storage, telegram_id: int, location: str) -> dict[str, Any] | None:
    chance = random.randint(STASH_AMBUSH_CHANCE_MIN, STASH_AMBUSH_CHANCE_MAX)
    if random.random() * 100 >= chance:
        return None
    first = random.choice(AMBUSH_TYPES)
    # Шанс, что засада устроена сразу двумя мутантами.
    second = None
    if random.random() < 0.4:
        others = [t for t in AMBUSH_TYPES if t[0] != first[0]]
        if others:
            second = random.choice(others)
    character = storage.get_character(telegram_id, refresh_energy=False)
    if character is None:
        return None
    player_power = max(1, int(character.gear_power))

    def _roll(power: int) -> int:
        if player_power >= power * 2:
            return random.randint(3, 8)
        if player_power >= power:
            return random.randint(8, 18)
        return random.randint(15, 30)

    damage = max(10, _roll(first[2]))
    if second is not None:
        damage = max(10, damage + _roll(second[2]))
    name = f"{first[0]} и {second[0]}" if second is not None else first[0]
    short = f"{first[1]} и {second[1]}" if second is not None else first[1]
    storage.change_health(telegram_id, -damage)
    survived = True
    updated = storage.get_character(telegram_id, refresh_energy=False)
    if updated is not None and updated.health <= 0:
        survived = False
    return {
        "name": name,
        "short": short,
        "damage": damage,
        "survived": survived,
    }


def move_stash_hunt(storage: Storage, telegram_id: int, direction: str) -> ActionResult:
    session = get_stash_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Сначала начни поиск схрона.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        clear_stash_session(storage, telegram_id)
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        clear_stash_session(storage, telegram_id)
        return ActionResult(False, _dead_block_text())

    delta = MOVE_DELTAS.get(direction)
    if delta is None:
        return ActionResult(False, "Некорректное направление.")
    nx = session.player[0] + delta[0]
    ny = session.player[1] + delta[1]
    if not (0 <= nx < session.grid and 0 <= ny < session.grid):
        image = render_stash_for_player(storage, telegram_id, session, player)
        return ActionResult(
            False,
            "Край карты — туда не пройти.",
            payload={"stash_image": image, "stash_active": True, "caption": stash_status_caption(session, player)},
        )
    if (nx, ny) in STASH_WALLS:
        image = render_stash_for_player(storage, telegram_id, session, player)
        return ActionResult(
            False,
            "Стена — здесь не пройти.",
            payload={"stash_image": image, "stash_active": True, "caption": stash_status_caption(session, player)},
        )

    session.player = (nx, ny)
    session.moves += 1
    session.steps += 1

    rad_add = 0
    if session.steps % STASH_RAD_EVERY_STEPS == 0:
        rad_add += STASH_RAD_PER_TICK
    if session.moves % STASH_MINUTE_MOVES == 0:
        rad_add += STASH_RAD_PER_MINUTE
    if rad_add > 0:
        storage.adjust_survival(telegram_id, radiation_delta=rad_add)
        session.rad_gained += rad_add

    # Клетка с мутантом: урон по засаде, мутант исчезает после встречи.
    if (nx, ny) in session.mutants:
        session.mutants = [m for m in session.mutants if m != (nx, ny)]
        encounter = random.choice(AMBUSH_TYPES)
        player = storage.get_character(telegram_id, refresh_energy=False) or player
        player_power = max(1, int(player.gear_power))

        def _roll(power: int) -> int:
            if player_power >= power * 2:
                return random.randint(3, 8)
            if player_power >= power:
                return random.randint(8, 18)
            return random.randint(15, 30)

        dmg = max(10, _roll(encounter[2]))
        storage.change_health(telegram_id, -dmg)
        updated = storage.get_character(telegram_id, refresh_energy=False)
        if updated is not None and updated.health <= 0:
            clear_stash_session(storage, telegram_id)
            return ActionResult(
                False,
                f"{encounter[0]} набросились: −{dmg} HP. Ты погиб.",
                payload={"stash_active": False, "stash_dead": True},
            )
        save_stash_session(storage, telegram_id, session)
        player = storage.get_character(telegram_id, refresh_energy=False) or player
        image = render_stash_for_player(storage, telegram_id, session, player)
        return ActionResult(
            True,
            f"⚔ {encounter[0]} охраняли эту клетку! −{dmg} HP.",
            payload={
                "stash_image": image,
                "stash_active": True,
                "caption": stash_status_caption(session, player),
                "move_note": f"⚔ {encounter[0]} атаковали: −{dmg} HP. Мутант повержен.",
            },
        )

    ambush = _try_ambush(storage, telegram_id, session.location)
    if ambush is not None and not ambush["survived"]:
        clear_stash_session(storage, telegram_id)
        return ActionResult(
            False,
            f"На тебя напали — {ambush['name']}!\n"
            f"Урон: {ambush['damage']} HP. Ты погиб в Зоне.\n"
            "Респавн из инвентаря (мутанты обшарят рюкзак).",
            payload={"stash_active": False, "stash_dead": True},
        )

    # Сбор тайников: подойди вплотную — каждый найденный даёт награду.
    collected_now: list[tuple[int, int]] = []
    loot_texts: list[str] = []
    for s_pos in list(session.stashes):
        if s_pos in session.collected:
            continue
        if _chebyshev(session.player, s_pos) <= 1:
            session.collected.append(s_pos)
            collected_now.append(s_pos)
            loot = _roll_stash_loot(storage, telegram_id, session.location)
            labels: list[str] = []
            for key, qty in loot:
                storage.add_item(telegram_id, key, qty)
                label = ITEM_LABELS.get(key, key)
                labels.append(f"{label} x{qty}" if qty != 1 else f"{label} x1")
            loot_texts.append(", ".join(labels))

    if collected_now:
        save_stash_session(storage, telegram_id, session)
        player = storage.get_character(telegram_id, refresh_energy=False) or player
        image = render_stash_for_player(storage, telegram_id, session, player)
        if session.collected_count >= session.stash_count:
            return _finish_stash_success(
                storage,
                telegram_id,
                session,
                collected_text=", ".join(loot_texts),
            )
        remaining_count = session.stash_count - session.collected_count
        note = f"📦 Тайник найден: {', '.join(loot_texts)}. Осталось: {remaining_count}."
        return ActionResult(
            True,
            note,
            payload={
                "stash_image": image,
                "stash_active": True,
                "caption": stash_status_caption(session, player),
                "move_note": note,
            },
        )

    # Мутанты двигаются к игроку по комнатам (по 1 клетке за ход), не выходя за стены.
    if session.mutants:
        from app.tactical_combat import best_step_toward

        moved: list[tuple[int, int]] = []
        occupied: set[tuple[int, int]] = set(session.mutants)
        for m in session.mutants:
            occupied.discard(m)
            nxt = best_step_toward(
                m,
                session.player,
                grid=session.grid,
                blocked=occupied | {session.player},
                forbidden=STASH_WALLS,
            )
            occupied.add(nxt)
            moved.append(nxt)
        session.mutants = moved

    if session.moves >= session.max_moves:
        clear_stash_session(storage, telegram_id)
        return ActionResult(
            False,
            f"Время поиска вышло на «{session.location}».\n"
            f"Схрон не найден. Рад +{session.rad_gained}.",
            payload={"stash_active": False, "stash_done": True},
        )

    save_stash_session(storage, telegram_id, session)
    player = storage.get_character(telegram_id, refresh_energy=False) or player
    image = render_stash_for_player(storage, telegram_id, session, player)

    remaining = [s for s in session.stashes if s not in session.collected]
    nearest = min((_chebyshev(session.player, s) for s in remaining), default=0) if remaining else 0
    if nearest <= 3:
        note = "Рядом! Тайник где-то совсем близко."
    elif nearest <= 6:
        note = "Тёплый след… продолжай искать."
    elif nearest <= 10:
        note = "Холодно. Тайник ещё далеко."
    else:
        note = "Очень далеко до тайника."
    if rad_add:
        note += f" Рад +{rad_add}."
    if ambush is not None:
        note += f" ⚠ Нападение: {ambush['name']}! Урон −{ambush['damage']} HP."

    return ActionResult(
        True,
        note,
        payload={
            "stash_image": image,
            "stash_active": True,
            "caption": stash_status_caption(session, player),
            "move_note": note,
        },
    )


def render_stash_for_player(
    storage: Storage,
    telegram_id: int,
    session: StashSession,
    player: Any | None,
) -> bytes:
    return render_stash_frame(
        session,
        player,
        rating_points=_character_rating_points(storage, telegram_id),
    )


def render_stash_frame(
    session: StashSession,
    character: Any | None = None,
    *,
    rating_points: int = 0,
) -> bytes:
    cell = 44
    grid = session.grid
    grid_px = grid * cell
    margin = 20
    panel_w = 280
    width = margin + grid_px + 16 + panel_w + margin
    height = max(margin + grid_px + margin, 720)
    canvas = Image.new("RGBA", (width, height), (16, 18, 20, 255))
    draw = ImageDraw.Draw(canvas)

    field = (margin - 6, margin - 6, margin + grid_px + 6, margin + grid_px + 6)
    draw.rounded_rectangle(field, radius=10, fill=(34, 36, 40, 255), outline=(70, 74, 80), width=2)

    # Фон поиска схрона — «фон для поиска.jpg»; если файла нет, карта локации.
    loc_bg = _load_search_field_background() or _load_hunt_map(session.location)
    if loc_bg is not None:
        field_img = _cover_crop(loc_bg, grid_px, grid_px).convert("RGBA")
        field_img.putalpha(225)
        canvas.paste(field_img, (margin, margin), field_img)

    for gy in range(grid):
        for gx in range(grid):
            left = margin + gx * cell
            top = margin + gy * cell
            if (gx, gy) in STASH_WALLS:
                ImageDraw.Draw(canvas).rectangle(
                    (left, top, left + cell - 1, top + cell - 1),
                    fill=(34, 32, 32),
                    outline=(22, 24, 26),
                    width=1,
                )
                continue
            if loc_bg is None:
                _draw_cell(canvas, left, top, cell)
            else:
                overlay = Image.new("RGBA", (cell, cell), (12, 14, 16, 28))
                canvas.alpha_composite(overlay, (left, top))
                ImageDraw.Draw(canvas).rectangle(
                    (left, top, left + cell - 1, top + cell - 1),
                    outline=(28, 30, 32),
                    width=1,
                )

    px, py = session.player
    pcx = margin + px * cell + cell // 2
    pcy = margin + py * cell + cell // 2
    token = _player_grid_token(character, size=160, rating_points=rating_points)
    _paste_circle(canvas, token, pcx, pcy, 40, ring_color=(72, 220, 90), ring_width=3)

    remaining = [s for s in getattr(session, "stashes", []) if s not in getattr(session, "collected", [])]
    if not remaining and getattr(session, "stashes", []):
        remaining = [session.stashes[0]]
    nearest = min((_chebyshev(session.player, s) for s in remaining), default=None) if remaining else None

    # Вместо абстрактных маркеров — ящики у каждого тайника (декор + ориентир).
    crate_color = (176, 140, 80)
    for sx, sy in remaining:
        scx = margin + sx * cell + cell // 2
        scy = margin + sy * cell + cell // 2
        for off_x, off_y in ((-1, 0), (0, 1)):
            nx, ny = sx + off_x, sy + off_y
            if 0 <= nx < grid and 0 <= ny < grid and (nx, ny) != session.player:
                bx = margin + nx * cell + 6
                by = margin + ny * cell + 6
                ImageDraw.Draw(canvas).rounded_rectangle(
                    (bx, by, bx + cell - 12, by + cell - 12),
                    radius=6,
                    fill=(90, 68, 40),
                    outline=crate_color,
                    width=3,
                )
                bar = ImageDraw.Draw(canvas)
                bar.line((bx + 6, by + 6, bx + cell - 18, by + cell - 18), fill=crate_color, width=3)
                bar.line((bx + cell - 18, by + 6, bx + 6, by + cell - 18), fill=crate_color, width=3)
        # Сам тайник — ящик с яркой меткой.
        ImageDraw.Draw(canvas).rounded_rectangle(
            (scx - cell // 2 + 4, scy - cell // 2 + 4, scx + cell // 2 - 4, scy + cell // 2 - 4),
            radius=8,
            fill=(60, 78, 58),
            outline=(150, 220, 120),
            width=4,
        )
        bar = ImageDraw.Draw(canvas)
        bar.line(
            (scx - cell // 2 + 10, scy - cell // 2 + 10, scx + cell // 2 - 10, scy + cell // 2 - 10),
            fill=(150, 220, 120),
            width=3,
        )
    if nearest is not None and nearest <= 3:
        nsx, nsy = min(remaining, key=lambda s: _chebyshev(session.player, s))
        _glow(canvas, margin + nsx * cell + cell // 2, margin + nsy * cell + cell // 2, (255, 200, 50), radius=16)

    # Мутанты на поле: красная точка-индикатор (если спрайт недоступен).
    from app.mutant_assets import load_mutant_grid_sprite

    for mx, my in getattr(session, "mutants", []):
        mcx = margin + mx * cell + cell // 2
        mcy = margin + my * cell + cell // 2
        sprite = load_mutant_grid_sprite("blind_dog")
        if sprite is not None:
            from io import BytesIO as _Bio

            img = Image.open(_Bio(sprite)).convert("RGBA")
            img.thumbnail((cell - 8, cell - 8), Image.LANCZOS)
            canvas.alpha_composite(img, (mcx - img.size[0] // 2, mcy - img.size[1] // 2))
            ImageDraw.Draw(canvas).ellipse(
                (mcx - 14, mcy - 14, mcx + 14, mcy + 14),
                outline=(230, 70, 60),
                width=3,
            )
        else:
            ImageDraw.Draw(canvas).ellipse(
                (mcx - 12, mcy - 12, mcx + 12, mcy + 12),
                fill=(40, 30, 30),
                outline=(230, 70, 60),
                width=3,
            )
            ImageDraw.Draw(canvas).text((mcx - 4, mcy - 6), "M", fill=(250, 120, 100))

    pl = margin + grid_px + 16
    pr = width - margin
    pt = margin - 6
    pb = height - margin + 6
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((pl, pt, pr, pb), radius=14, fill=(48, 50, 54, 255), outline=(100, 104, 110), width=2)

    thumb = (pl + 12, pt + 10, pr - 12, pt + 100)
    loc_img = _load_location_thumb(session.location)
    if loc_img is not None:
        _paste_rounded(canvas, loc_img, thumb, radius=8)
        ImageDraw.Draw(canvas).rounded_rectangle(thumb, radius=8, outline=(110, 120, 100), width=2)
    else:
        ImageDraw.Draw(canvas).rounded_rectangle(thumb, radius=8, fill=(30, 34, 28), outline=(90, 100, 80), width=2)

    title_font = _load_font(20)
    body = _load_font(16)
    small = _load_font(13)
    loc_font = title_font if len(session.location) <= 14 else _load_font(16)
    draw.text((pl + 14, pt + 106), session.location, fill=(245, 245, 245), font=loc_font)
    draw.text((pl + 14, pt + 132), "Поиск схрона", fill=(200, 180, 120), font=body)

    info_y = pt + 162
    draw.text(
        (pl + 14, info_y),
        f"Ход {session.moves}/{session.max_moves} · Тайники {session.collected_count}/{session.stash_count}",
        fill=(200, 200, 200),
        font=body,
    )
    draw.text((pl + 14, info_y + 24), f"Рад +{session.rad_gained}", fill=(200, 160, 120), font=small)

    if nearest is None or nearest > 10:
        hint = "🧊 Очень далеко"
    elif nearest <= 3:
        hint = "🔥 Тайник очень близко!"
    elif nearest <= 6:
        hint = "🌡 Тёплый след"
    else:
        hint = "❄ Холодно"
    draw.text((pl + 14, info_y + 48), hint, fill=(220, 220, 200), font=small)

    hp = int(character.health) if character else 0
    max_hp = int(effective_max_health(character)) if character else 100
    energy = int(character.energy) if character else 0
    max_energy = int(character.max_energy) if character else 100
    rad = int(character.radiation) if character else 0

    bar_top = info_y + 80
    draw.rounded_rectangle((pl + 12, bar_top, pr - 12, bar_top + 24), radius=6, fill=(30, 30, 34), outline=(90, 90, 95))
    fill_w = int((pr - pl - 36) * (hp / max(1, max_hp)))
    if fill_w > 0:
        draw.rounded_rectangle((pl + 14, bar_top + 2, pl + 14 + fill_w, bar_top + 22), radius=4, fill=(200, 60, 50))
    draw.text((pl + 16, bar_top + 4), f"HP {hp}/{max_hp}", fill=(255, 255, 255), font=small)

    draw.rounded_rectangle((pl + 12, bar_top + 34, pr - 12, bar_top + 58), radius=6, fill=(30, 30, 34), outline=(90, 90, 95))
    fill_w = int((pr - pl - 36) * min(1.0, rad / 100))
    if fill_w > 0:
        draw.rounded_rectangle((pl + 14, bar_top + 36, pl + 14 + fill_w, bar_top + 56), radius=4, fill=(180, 200, 40))
    draw.text((pl + 16, bar_top + 38), f"RAD {rad}", fill=(255, 255, 255), font=small)

    draw.rounded_rectangle((pl + 12, bar_top + 68, pr - 12, bar_top + 92), radius=6, fill=(30, 30, 34), outline=(90, 90, 95))
    fill_w = int((pr - pl - 36) * (energy / max(1, max_energy)))
    if fill_w > 0:
        draw.rounded_rectangle((pl + 14, bar_top + 70, pl + 14 + fill_w, bar_top + 90), radius=4, fill=(50, 120, 210))
    draw.text((pl + 16, bar_top + 72), f"EN {energy}/{max_energy}", fill=(255, 255, 255), font=small)

    draw.text((pl + 14, pb - 42), "Ищи схрон на карте", fill=(210, 210, 210), font=small)
    draw.text((pl + 14, pb - 24), "Стрелки — ход, кнопка — уйти", fill=(190, 190, 190), font=small)

    out = canvas.convert("RGB")
    buf = BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def clear_unplayed_stash_session(storage: Storage, telegram_id: int) -> None:
    """Снять «призрачную» сессию схрона, если игрок ещё не ходил по карте."""
    session = get_stash_session(storage, telegram_id)
    if session is not None and session.moves <= 0 and session.steps <= 0 and not session.found:
        clear_stash_session(storage, telegram_id)


def try_random_stash_coordinates(storage: Storage, telegram_id: int) -> bool:
    """Небольшой шанс найти координаты хабара во время охоты (предмет в инвентарь)."""
    if random.random() * 100 < STASH_COORDS_DROP_CHANCE:
        storage.add_item(telegram_id, STASH_COORDINATE_KEY, 1)
        return True
    return False
