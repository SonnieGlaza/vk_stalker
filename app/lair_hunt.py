"""Мини-игра «Логово мутантов»: зачистка поля 8×8 (прототип — 2 мутанта).

Логово появляется на карте локации; вход открывает поле, где нужно
зачистить всех мутантов до истечения ходов.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.artifact_hunt import (
    _character_rating_points,
    _cover_crop,
    _draw_cell,
    _load_font,
    _load_location_thumb,
    _player_grid_token,
    _paste_circle,
)
from app.game_logic import (
    ActionResult,
    _add_rating,
    _dead_block_text,
    _is_dead,
    effective_max_health,
    is_traveling,
    travel_block_text,
)
from app.storage import Character, Storage

LAIR_META_PREFIX = "lair_hunt:"
LAIR_GRID = 8
LAIR_MAX_MOVES = 24
LAIR_MUTANT_COUNT = 2
LAIR_RAD_EVERY_STEPS = 3
LAIR_RAD_PER_TICK = 1
LAIR_REWARD_MIN_RU = 400
LAIR_REWARD_MAX_RU = 900
LAIR_REWARD_RATING = 6
LAIR_MELEE_DAMAGE = 5
LAIR_COOLDOWN_MINUTES = 20

MOVE_DELTAS: dict[str, tuple[int, int]] = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}


@dataclass
class LairSession:
    location: str
    player: tuple[int, int]
    mutants: list[tuple[int, int]]
    cleared: list[tuple[int, int]]
    moves: int
    steps: int
    rad_gained: int
    grid: int = LAIR_GRID
    max_moves: int = LAIR_MAX_MOVES

    @property
    def mutant_count(self) -> int:
        return max(1, len(self.mutants))

    @property
    def cleared_count(self) -> int:
        return len([m for m in self.mutants if m in self.cleared])

    @property
    def done(self) -> bool:
        return bool(self.mutants) and all(m in self.cleared for m in self.mutants)

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "player": list(self.player),
            "mutants": [list(m) for m in self.mutants],
            "cleared": [list(m) for m in self.cleared],
            "moves": self.moves,
            "steps": self.steps,
            "rad_gained": self.rad_gained,
            "grid": self.grid,
            "max_moves": self.max_moves,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LairSession":
        def _p(value: Any) -> tuple[int, int]:
            return int(value[0]), int(value[1])

        return cls(
            location=str(raw.get("location") or ""),
            player=_p(raw.get("player") or (3, 3)),
            mutants=[_p(m) for m in (raw.get("mutants") or [])],
            cleared=[_p(m) for m in (raw.get("cleared") or [])],
            moves=int(raw.get("moves") or 0),
            steps=int(raw.get("steps") or 0),
            rad_gained=int(raw.get("rad_gained") or 0),
            grid=int(raw.get("grid") or LAIR_GRID),
            max_moves=int(raw.get("max_moves") or LAIR_MAX_MOVES),
        )


def _meta_key(telegram_id: int) -> str:
    return f"{LAIR_META_PREFIX}{int(telegram_id)}"


def get_lair_session(storage: Storage, telegram_id: int) -> LairSession | None:
    raw = storage.get_meta(_meta_key(telegram_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return LairSession.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _save_session(storage: Storage, telegram_id: int, session: LairSession) -> None:
    storage.set_meta(_meta_key(telegram_id), json.dumps(session.to_dict(), ensure_ascii=False))


def _clear_session(storage: Storage, telegram_id: int) -> None:
    storage.delete_meta(_meta_key(telegram_id))


def _random_free_cell(grid: int, forbidden: set[tuple[int, int]]) -> tuple[int, int]:
    free = [(x, y) for x in range(grid) for y in range(grid) if (x, y) not in forbidden]
    if not free:
        return (0, 0)
    return random.choice(free)


def _chebyshev(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _build_session(character: Character) -> LairSession:
    grid = LAIR_GRID
    forbidden: set[tuple[int, int]] = set()
    player = _random_free_cell(grid, forbidden)
    forbidden.add(player)
    mutants: list[tuple[int, int]] = []
    for _ in range(LAIR_MUTANT_COUNT):
        mutant = _random_free_cell(grid, forbidden)
        tries = 0
        while _chebyshev(player, mutant) < grid // 3 and tries < 60:
            mutant = _random_free_cell(grid, forbidden)
            tries += 1
        mutants.append(mutant)
        forbidden.add(mutant)
    return LairSession(
        location=character.location,
        player=player,
        mutants=mutants,
        cleared=[],
        moves=0,
        steps=0,
        rad_gained=0,
    )


def lair_status_caption(session: LairSession, character: Character | None = None) -> str:
    lines = [
        f"Логово мутантов — {session.location}",
        f"Ход {session.moves}/{session.max_moves} · рад +{session.rad_gained}",
        f"Мутантов зачищено: {session.cleared_count}/{session.mutant_count}",
    ]
    if character is not None:
        lines.append(
            f"HP {character.health}/{effective_max_health(character)} · "
            f"☢ {character.radiation} · ⚡ {character.energy}"
        )
    lines.append("Найди и зачисти всех мутантов на поле.")
    return "\n".join(lines)


def start_lair_hunt(storage: Storage, telegram_id: int) -> ActionResult:
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if travel_block_text(player):
        return ActionResult(False, travel_block_text(player))
    if is_traveling(player):
        return ActionResult(False, "Нельзя зачищать логово в пути.")
    if get_lair_session(storage, telegram_id) is not None:
        session = get_lair_session(storage, telegram_id)
        assert session is not None
        image = render_lair_for_player(storage, telegram_id, session, player)
        return ActionResult(
            True,
            "Ты уже в логове. Продолжай зачистку.",
            payload={"lair_image": image, "lair_active": True, "caption": lair_status_caption(session, player)},
        )
    cd_left = _lair_cooldown_left(storage, telegram_id)
    if cd_left:
        return ActionResult(False, f"Логово ещё не остыло: до входа {cd_left} мин.")
    from app.player_busy import player_busy_reason

    busy = player_busy_reason(storage, telegram_id, skip="lair", auto_recover=False)
    if busy:
        return ActionResult(False, busy)

    session = _build_session(player)
    _save_session(storage, telegram_id, session)
    _set_lair_cooldown(storage, telegram_id)
    player = storage.get_character(telegram_id, refresh_energy=False) or player
    image = render_lair_for_player(storage, telegram_id, session, player)
    return ActionResult(
        True,
        "В логове 2 мутанта. Зачисти их!",
        payload={"lair_image": image, "lair_active": True, "caption": lair_status_caption(session, player), "lair_started": True},
    )


def abandon_lair_hunt(storage: Storage, telegram_id: int) -> ActionResult:
    session = get_lair_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Активной зачистки нет.")
    _clear_session(storage, telegram_id)
    return ActionResult(
        True,
        f"Ты свалил из логова на «{session.location}».\n"
        f"Зачищено мутантов: {session.cleared_count}/{session.mutant_count}, ходов: {session.moves}.",
        payload={"lair_active": False},
    )


def _finish_lair_success(storage: Storage, telegram_id: int, session: LairSession) -> ActionResult:
    _clear_session(storage, telegram_id)
    reward = random.randint(LAIR_REWARD_MIN_RU, LAIR_REWARD_MAX_RU)
    storage.change_money(telegram_id, reward)
    _add_rating(storage, telegram_id, LAIR_REWARD_RATING)
    storage.add_player_stat(telegram_id, "money_earned", reward)
    return ActionResult(
        True,
        f"Логово зачищено на «{session.location}»! Мутантов: {session.cleared_count}/{session.mutant_count}.\n"
        f"Награда: {reward} RU, рейтинг +{LAIR_REWARD_RATING}.\n"
        f"Ходов: {session.moves}, рад +{session.rad_gained}.",
        payload={"lair_active": False, "lair_done": True, "reward": reward},
    )


def move_lair_hunt(storage: Storage, telegram_id: int, direction: str) -> ActionResult:
    session = get_lair_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Сначала зайди в логово мутантов.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        _clear_session(storage, telegram_id)
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        _clear_session(storage, telegram_id)
        return ActionResult(False, _dead_block_text())

    delta = MOVE_DELTAS.get(direction)
    if delta is None:
        return ActionResult(False, "Некорректный ход.")
    nx = session.player[0] + delta[0]
    ny = session.player[1] + delta[1]
    if not (0 <= nx < session.grid and 0 <= ny < session.grid):
        image = render_lair_for_player(storage, telegram_id, session, player)
        return ActionResult(
            False,
            "Край логова — туда не пройти.",
            payload={"lair_image": image, "lair_active": True, "caption": lair_status_caption(session, player)},
        )

    session.player = (nx, ny)
    session.moves += 1
    session.steps += 1

    rad_add = 0
    if session.steps % LAIR_RAD_EVERY_STEPS == 0:
        rad_add += LAIR_RAD_PER_TICK
    if rad_add > 0:
        storage.adjust_survival(telegram_id, radiation_delta=rad_add)
        session.rad_gained += rad_add

    note = ""
    if session.player in session.mutants and session.player not in session.cleared:
        session.cleared.append(session.player)
        storage.change_health(telegram_id, -LAIR_MELEE_DAMAGE)
        note = f"🐾 Мутант повержен в ближнем бою! −{LAIR_MELEE_DAMAGE} HP."
        updated = storage.get_character(telegram_id, refresh_energy=False)
        if updated is not None and updated.health <= 0:
            storage.change_health(telegram_id, 1)
        if session.done:
            return _finish_lair_success(storage, telegram_id, session)

    if session.moves >= session.max_moves:
        _clear_session(storage, telegram_id)
        return ActionResult(
            False,
            f"Время зачистки вышло на «{session.location}».\n"
            f"Зачищено мутантов: {session.cleared_count}/{session.mutant_count}.",
            payload={"lair_active": False, "lair_done": True},
        )

    _save_session(storage, telegram_id, session)
    player = storage.get_character(telegram_id, refresh_energy=False) or player
    image = render_lair_for_player(storage, telegram_id, session, player)
    remaining = session.mutant_count - session.cleared_count
    base_note = note or (f"Осталось мутантов: {remaining}." if remaining else "Все мутанты зачищены!")
    if rad_add:
        base_note += f" Рад +{rad_add}."
    return ActionResult(
        True,
        base_note,
        payload={
            "lair_image": image,
            "lair_active": True,
            "caption": lair_status_caption(session, player),
            "move_note": base_note,
        },
    )


def _lair_cd_key(telegram_id: int) -> str:
    return f"lair_cd:{int(telegram_id)}"


def _lair_cooldown_left(storage: Storage, telegram_id: int) -> int:
    """Сколько минут осталось до входа в логово (0 — можно заходить)."""
    raw = storage.get_meta(_lair_cd_key(telegram_id))
    if not raw:
        return 0
    try:
        dt = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, int((dt - datetime.now(timezone.utc)).total_seconds() // 60))


def _set_lair_cooldown(storage: Storage, telegram_id: int) -> None:
    storage.set_meta(
        _lair_cd_key(telegram_id),
        (datetime.now(timezone.utc) + timedelta(minutes=LAIR_COOLDOWN_MINUTES)).isoformat(),
    )


def lair_cooldown_remaining_text(storage: Storage, telegram_id: int) -> str | None:
    """Человекочитаемый остаток кулдауна входа в логово; None — можно заходить."""
    minutes = _lair_cooldown_left(storage, telegram_id)
    if minutes <= 0:
        return None
    hours, rem = divmod(minutes, 60)
    if hours and rem:
        return f"{hours}ч {rem}м"
    if hours:
        return f"{hours}ч"
    return f"{minutes}м"


def shoot_lair_hunt(storage: Storage, telegram_id: int, direction: str) -> ActionResult:
    """Выстрел в направлении: первый мутант на линии погибает (прототип — 1 попадание)."""
    session = get_lair_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Сначала зайди в логово мутантов.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        _clear_session(storage, telegram_id)
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        _clear_session(storage, telegram_id)
        return ActionResult(False, _dead_block_text())
    delta = MOVE_DELTAS.get(direction)
    if delta is None:
        return ActionResult(False, "Некорректный выстрел.")
    from app.tactical_combat import consume_shot_ammo, weapon_shoot_range

    weapon = str(player.equipment.get("weapon", "Нож"))
    if weapon_shoot_range(weapon) <= 0:
        return _frame(session, storage, telegram_id, player, "Это оружие не стреляет на дистанции.")
    ammo_result = consume_shot_ammo(storage, telegram_id, weapon)
    if ammo_result is not None:
        return ammo_result
    px, py = session.player
    dx, dy = delta
    hit = None
    sx, sy = px + dx, py + dy
    while 0 <= sx < session.grid and 0 <= sy < session.grid:
        if (sx, sy) in session.mutants and (sx, sy) not in session.cleared:
            hit = (sx, sy)
            break
        sx += dx
        sy += dy
    if hit is None:
        return _frame(session, storage, telegram_id, player, "Мимо. Мутанты где-то в другом месте.")
    session.cleared.append(hit)
    session.moves += 1
    if session.done:
        return _finish_lair_success(storage, telegram_id, session)
    _save_session(storage, telegram_id, session)
    player = storage.get_character(telegram_id, refresh_energy=False) or player
    image = render_lair_for_player(storage, telegram_id, session, player)
    remaining = session.mutant_count - session.cleared_count
    note = f"🔫 Попадание! Мутант повержен. Осталось: {remaining}."
    return ActionResult(
        True,
        note,
        payload={
            "lair_image": image,
            "lair_active": True,
            "caption": lair_status_caption(session, player),
            "move_note": note,
        },
    )


def _frame(
    session: LairSession,
    storage: Storage,
    telegram_id: int,
    player: Character | None,
    text: str,
) -> ActionResult:
    image = render_lair_for_player(storage, telegram_id, session, player)
    return ActionResult(
        False,
        text,
        payload={
            "lair_image": image,
            "lair_active": True,
            "caption": lair_status_caption(session, player),
        },
    )


def render_lair_for_player(
    storage: Storage,
    telegram_id: int,
    session: LairSession,
    player: Character | None,
) -> bytes:
    return render_lair_frame(
        session,
        player,
        rating_points=_character_rating_points(storage, telegram_id),
    )


def render_lair_frame(
    session: LairSession,
    character: Character | None = None,
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
    draw.rounded_rectangle(field, radius=10, fill=(26, 28, 24, 255), outline=(70, 74, 80), width=2)

    # Фон поля — как в заданиях: превью локации из assets/locations.
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
                _draw_cell(canvas, left, top, cell, tone=52)
            else:
                overlay = Image.new("RGBA", (cell, cell), (12, 14, 16, 28))
                canvas.alpha_composite(overlay, (left, top))
                ImageDraw.Draw(canvas).rectangle(
                    (left, top, left + cell - 1, top + cell - 1),
                    outline=(28, 30, 32),
                    width=1,
                )

    from app.mutant_assets import load_mutant_grid_sprite

    for mx, my in session.mutants:
        mcx = margin + mx * cell + cell // 2
        mcy = margin + my * cell + cell // 2
        if (mx, my) in session.cleared:
            # зачищенный мутант — тёмная клетка с крестиком
            ImageDraw.Draw(canvas).rectangle(
                (margin + mx * cell + 6, margin + my * cell + 6, margin + (mx + 1) * cell - 7, margin + (my + 1) * cell - 7),
                outline=(70, 70, 70),
                width=3,
            )
        else:
            sprite = load_mutant_grid_sprite("blind_dog")
            if sprite is not None:
                img = Image.open(BytesIO(sprite)).convert("RGBA")
                img.thumbnail((cell - 8, cell - 8), Image.LANCZOS)
                canvas.alpha_composite(img, (mcx - img.size[0] // 2, mcy - img.size[1] // 2))
                ImageDraw.Draw(canvas).ellipse(
                    (mcx - 13, mcy - 13, mcx + 13, mcy + 13),
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

    px, py = session.player
    pcx = margin + px * cell + cell // 2
    pcy = margin + py * cell + cell // 2
    token = _player_grid_token(character, size=160, rating_points=rating_points)
    _paste_circle(canvas, token, pcx, pcy, 40, ring_color=(72, 220, 90), ring_width=3)

    pl = margin + grid_px + 16
    pr = width - margin
    pt = margin - 6
    pb = height - margin + 6
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((pl, pt, pr, pb), radius=14, fill=(40, 36, 34, 255), outline=(110, 100, 90), width=2)

    title = _load_font(20)
    body = _load_font(16)
    small = _load_font(13)
    draw.text((pl + 14, pt + 14), "Логово мутантов", fill=(240, 220, 190), font=title)
    draw.text((pl + 14, pt + 48), session.location, fill=(200, 200, 200), font=body)
    draw.text((pl + 14, pt + 78), f"Ход {session.moves}/{session.max_moves}", fill=(200, 200, 200), font=body)
    draw.text((pl + 14, pt + 106), f"Мутантов: {session.cleared_count}/{session.mutant_count}", fill=(210, 160, 120), font=body)
    draw.text((pl + 14, pt + 134), f"Рад +{session.rad_gained}", fill=(200, 160, 120), font=small)

    hp = int(character.health) if character else 0
    max_hp = int(effective_max_health(character)) if character else 100
    energy = int(character.energy) if character else 0
    max_energy = int(character.max_energy) if character else 100
    rad = int(character.radiation) if character else 0

    bar_top = pt + 168
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

    draw.text((pl + 14, pb - 42), "Зачисти всех мутантов", fill=(210, 210, 210), font=small)
    draw.text((pl + 14, pb - 24), "Стрелки - ход, кнопка - бросить", fill=(190, 190, 190), font=small)

    out = canvas.convert("RGB")
    buf = BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

def lair_keyboard() -> list[list[tuple[str, str]]]:
    """Ряды (текст, callback-код) для инлайн-клавиатуры (формат VK)."""
    return [
        [("⬆️ Вперёд", "lair:up")],
        [
            ("⬅️ Влево", "lair:left"),
            ("⬇️ Назад", "lair:down"),
            ("➡️ Вправо", "lair:right"),
        ],
        [
            ("🔫 ⬆️", "lair:shoot:up"),
            ("🔫 ⬅️", "lair:shoot:left"),
            ("🔫 ⬇️", "lair:shoot:down"),
            ("🔫 ➡️", "lair:shoot:right"),
        ],
        [("🚪 Бросить", "lair:leave")],
    ]
