"""Тактическая контрабанда: маршрут по сетке с машиной и точками сдачи."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.artifact_hunt import FONT_CANDIDATES, _load_location_thumb, _paste_circle, _paste_rounded
from app.enemy_hud import draw_enemy_hud, hud_slots_from_kinds
from app.game_logic import (
    ActionResult,
    _is_dead,
    begin_smuggling_travel_after_grid,
    clear_active_smuggling,
    fail_smuggling_delivery,
    remember_death_cause,
)
from app.mission_icons import ANOMALY_ICON_KEY, MISSION_ICON_GRID_DIAMETER, mission_icon_image
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
from app.quest_mission import (
    GRID_SIZE,
    HOSTILE_MOVE_CHANCE,
    LOCATION_DANGER,
    MAX_MOVES,
    MOVE_DELTAS,
    _adjacent_cells,
    _combat_damage,
    _draw_cell,
    _draw_enemy_icon,
    _free_cell,
    _glow,
    _hazard_damage,
    _load_font,
    _maybe_move_hostiles,
    _paste_token_circle,
)
from app.storage import Character, Storage

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SMUGGLE_ICONS_DIR = PROJECT_ROOT / "assets" / "smuggle"

_TRANSPORT_ICON_FILES: dict[str, str] = {
    "foot": "walker.png",
    "bicycle": "bicycle.png",
    "niva": "niva.png",
    "truck": "truck.png",
}

SMUGGLE_MISSION_META_PREFIX = "smuggle_mission:"
SMUGGLE_ACTIVE_IDS_META = "smuggle_mission:active_ids"
SMUGGLE_IDLE_HOURS = 4

TRANSPORT_LABELS: dict[str, str] = {
    "foot": "пешком",
    "bicycle": "велосипед",
    "niva": "Нива",
    "truck": "грузовик",
}


@dataclass
class SmuggleMissionSession:
    destination: str
    origin: str
    transport: str
    success_chance: int
    player: tuple[int, int]
    route: list[tuple[int, int]]
    player_facing: str = "down"
    route_index: int = 0
    hazards: list[tuple[int, int]] = field(default_factory=list)
    enemies: list[tuple[int, int]] = field(default_factory=list)
    enemy_kinds: list[str] = field(default_factory=list)
    npcs: list[tuple[int, int]] = field(default_factory=list)
    npc_kinds: list[str] = field(default_factory=list)
    moves: int = 0
    max_moves: int = 18
    grid: int = GRID_SIZE
    difficulty: str = "hard"
    location: str = ""
    started_at: str | None = None
    turn_seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "destination": self.destination,
            "origin": self.origin,
            "transport": self.transport,
            "success_chance": self.success_chance,
            "player": list(self.player),
            "player_facing": self.player_facing,
            "route": [list(p) for p in self.route],
            "route_index": self.route_index,
            "hazards": [list(p) for p in self.hazards],
            "enemies": [list(p) for p in self.enemies],
            "enemy_kinds": list(self.enemy_kinds),
            "npcs": [list(p) for p in self.npcs],
            "npc_kinds": list(self.npc_kinds),
            "moves": self.moves,
            "max_moves": self.max_moves,
            "grid": self.grid,
            "difficulty": self.difficulty,
            "location": self.location,
            "started_at": self.started_at,
            "turn_seq": self.turn_seq,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SmuggleMissionSession:
        return cls(
            destination=str(raw.get("destination") or ""),
            origin=str(raw.get("origin") or ""),
            transport=str(raw.get("transport") or "foot"),
            success_chance=int(raw.get("success_chance") or 50),
            player=(int(raw["player"][0]), int(raw["player"][1])),
            player_facing=str(raw.get("player_facing") or "down"),
            route=[(int(p[0]), int(p[1])) for p in (raw.get("route") or [])],
            route_index=int(raw.get("route_index") or 0),
            hazards=[(int(p[0]), int(p[1])) for p in (raw.get("hazards") or [])],
            enemies=[(int(p[0]), int(p[1])) for p in (raw.get("enemies") or [])],
            enemy_kinds=[str(k) for k in (raw.get("enemy_kinds") or [])],
            npcs=[(int(p[0]), int(p[1])) for p in (raw.get("npcs") or [])],
            npc_kinds=[str(k) for k in (raw.get("npc_kinds") or [])],
            moves=int(raw.get("moves") or 0),
            max_moves=int(raw.get("max_moves") or 18),
            grid=int(raw.get("grid") or GRID_SIZE),
            difficulty=str(raw.get("difficulty") or "hard"),
            location=str(raw.get("location") or raw.get("origin") or ""),
            started_at=raw.get("started_at"),
            turn_seq=int(raw.get("turn_seq") or 0),
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _register_active_smuggle(storage: Storage, telegram_id: int) -> None:
    raw = storage.get_meta(SMUGGLE_ACTIVE_IDS_META)
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
    storage.set_meta(SMUGGLE_ACTIVE_IDS_META, json.dumps(ids, ensure_ascii=False))


def _unregister_active_smuggle(storage: Storage, telegram_id: int) -> None:
    raw = storage.get_meta(SMUGGLE_ACTIVE_IDS_META)
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
        storage.set_meta(SMUGGLE_ACTIVE_IDS_META, json.dumps(ids, ensure_ascii=False))
    else:
        storage.delete_meta(SMUGGLE_ACTIVE_IDS_META)


def list_active_smuggle_player_ids(storage: Storage) -> list[int]:
    raw = storage.get_meta(SMUGGLE_ACTIVE_IDS_META)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [int(x) for x in parsed]
    except json.JSONDecodeError:
        pass
    return []


def _smuggle_timeout_result(storage: Storage, telegram_id: int, reason: str) -> ActionResult:
    fail_text = _fail_smuggle_run(storage, telegram_id, reason)
    return ActionResult(
        False,
        fail_text,
        payload={"mission_active": False, "mission_done": True, "smuggle_timeout": True},
    )


def check_smuggle_session_timeout(storage: Storage, telegram_id: int) -> ActionResult | None:
    session = get_smuggle_session(storage, telegram_id)
    if session is None:
        _unregister_active_smuggle(storage, telegram_id)
        return None
    if session.moves >= session.max_moves:
        return _smuggle_timeout_result(storage, telegram_id, "Время рейса вышло — ограбили.")
    if session.started_at:
        try:
            started = datetime.fromisoformat(str(session.started_at))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
        except ValueError:
            started = _utc_now()
        if _utc_now() > started + timedelta(hours=SMUGGLE_IDLE_HOURS):
            return _smuggle_timeout_result(
                storage,
                telegram_id,
                "Рейс заброшен — слишком долго без движения.",
            )
    return None


def process_smuggle_timeouts(storage: Storage) -> list[tuple[int, ActionResult]]:
    outcomes: list[tuple[int, ActionResult]] = []
    for telegram_id in list_active_smuggle_player_ids(storage):
        result = check_smuggle_session_timeout(storage, telegram_id)
        if result is not None:
            outcomes.append((telegram_id, result))
    return outcomes


def _meta_key(telegram_id: int) -> str:
    return f"{SMUGGLE_MISSION_META_PREFIX}{int(telegram_id)}"


def get_smuggle_session(storage: Storage, telegram_id: int) -> SmuggleMissionSession | None:
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
        return SmuggleMissionSession.from_dict(data)
    except Exception:
        storage.delete_meta(_meta_key(telegram_id))
        clear_active_smuggling(storage, telegram_id)
        return None


def save_smuggle_session(storage: Storage, telegram_id: int, session: SmuggleMissionSession) -> None:
    storage.set_meta(_meta_key(telegram_id), json.dumps(session.to_dict(), ensure_ascii=False))
    _register_active_smuggle(storage, telegram_id)


def _save_smuggle_if_turn_ok(
    storage: Storage,
    telegram_id: int,
    session: SmuggleMissionSession,
    expected_seq: int,
) -> bool:
    from app.tactical_turn import save_turn_if_seq_ok

    tid = int(telegram_id)
    return save_turn_if_seq_ok(
        storage,
        meta_key=_meta_key(tid),
        session=session,
        from_dict=SmuggleMissionSession.from_dict,
        save_fn=lambda st, sess: save_smuggle_session(st, tid, sess),
        expected_seq=expected_seq,
    )


def clear_smuggle_session(storage: Storage, telegram_id: int) -> None:
    storage.delete_meta(_meta_key(telegram_id))
    _unregister_active_smuggle(storage, telegram_id)


def _build_route(grid: int) -> list[tuple[int, int]]:
    """Левый нижний угол → правый нижний → случайная 3-я точка."""
    left = (0, grid - 1)
    right = (grid - 1, grid - 1)
    forbidden = {left, right}
    third = _free_cell(grid, forbidden)
    return [left, right, third]


def _scaled_max_moves(origin: str) -> int:
    danger = LOCATION_DANGER.get(origin, 2)
    base = MAX_MOVES + danger
    return max(10, base * 2 // 3)  # −⅓ ходов от обычной вылазки


def _build_smuggle_session(
    *,
    origin: str,
    destination: str,
    transport: str,
    success_chance: int,
) -> SmuggleMissionSession:
    grid = GRID_SIZE
    route = _build_route(grid)
    start = route[0]
    forbidden: set[tuple[int, int]] = set(route)
    hazards: list[tuple[int, int]] = []
    enemies: list[tuple[int, int]] = []
    enemy_kinds: list[str] = []
    npcs: list[tuple[int, int]] = []
    npc_kinds: list[str] = []
    danger = LOCATION_DANGER.get(origin, 2)
    for _ in range(2 + danger // 2):
        cell = _free_cell(grid, forbidden)
        hazards.append(cell)
        forbidden.add(cell)
    for _ in range(1 + danger // 2):
        cell = _free_cell(grid, forbidden)
        enemies.append(cell)
        enemy_kinds.append(pick_mutant_kind())
        forbidden.add(cell)
    for _ in range(1 + max(0, danger - 1)):
        cell = _free_cell(grid, forbidden)
        npcs.append(cell)
        npc_kinds.append(pick_npc_kind())
        forbidden.add(cell)
    return SmuggleMissionSession(
        destination=destination,
        origin=origin,
        transport=transport,
        success_chance=success_chance,
        player=start,
        route=route,
        route_index=1 if start == route[0] else 0,
        hazards=hazards,
        enemies=enemies,
        enemy_kinds=enemy_kinds,
        npcs=npcs,
        npc_kinds=npc_kinds,
        max_moves=_scaled_max_moves(origin),
        location=origin,
        difficulty="hard",
        started_at=_utc_now().isoformat(),
    )


def _as_quest_compat(session: SmuggleMissionSession) -> Any:
    """Адаптер для общих функций боя quest_mission (живые ссылки)."""

    class _Compat:
        @property
        def location(self) -> str:
            return session.location

        @property
        def difficulty(self) -> str:
            return session.difficulty

        @property
        def player(self) -> tuple[int, int]:
            return session.player

        @property
        def hazards(self) -> list[tuple[int, int]]:
            return session.hazards

        @property
        def enemies(self) -> list[tuple[int, int]]:
            return session.enemies

        @enemies.setter
        def enemies(self, value: list[tuple[int, int]]) -> None:
            session.enemies = value

        @property
        def enemy_kinds(self) -> list[str]:
            return session.enemy_kinds

        @enemy_kinds.setter
        def enemy_kinds(self, value: list[str]) -> None:
            session.enemy_kinds = value

        @property
        def npcs(self) -> list[tuple[int, int]]:
            return session.npcs

        @npcs.setter
        def npcs(self, value: list[tuple[int, int]]) -> None:
            session.npcs = value

        @property
        def npc_kinds(self) -> list[str]:
            return session.npc_kinds

        @npc_kinds.setter
        def npc_kinds(self, value: list[str]) -> None:
            session.npc_kinds = value

        @property
        def grid(self) -> int:
            return session.grid

        @property
        def player_facing(self) -> str:
            return session.player_facing

        @player_facing.setter
        def player_facing(self, value: str) -> None:
            session.player_facing = value

        @property
        def mutant_chase(self) -> bool:
            # Контрабанда: мутанты преследуют, как на 🟠/🔴.
            return True

    return _Compat()


def _resolve_smuggle_hostile(
    storage: Storage,
    telegram_id: int,
    session: SmuggleMissionSession,
    player: Character,
    label: str,
    unit_attr: str,
    *,
    kinds_attr: str | None = None,
    npc: bool = False,
) -> tuple[Character, str | None, ActionResult | None, int]:
    """Вернуть (player, note, dead_result, damage) без записи урона в БД."""
    from app.death_flavor import encounter_phrase_for_kind, killer_label_for_kind

    compat = _as_quest_compat(session)
    units: list[tuple[int, int]] = getattr(compat, unit_attr)
    if session.player not in units:
        return player, None, None, 0
    dmg = _combat_damage(session.location, session.difficulty, player)
    kinds: list[str] | None = getattr(compat, kinds_attr) if kinds_attr else None
    kind = ""
    if kinds is not None and len(kinds) == len(units):
        idx = units.index(session.player)
        kind = kinds[idx]
        new_units = [pos for pos in units if pos != session.player]
        new_kinds = [k for pos, k in zip(units, kinds) if pos != session.player]
        setattr(compat, unit_attr, new_units)
        setattr(compat, kinds_attr, new_kinds)
    else:
        setattr(compat, unit_attr, [e for e in units if e != session.player])
    phrase = encounter_phrase_for_kind(kind, npc=npc) if kind else f"с {label}"
    note = f"Бой {phrase}: −{dmg} HP."
    projected_hp = int(player.health) - dmg
    if projected_hp <= 0:
        remember_death_cause(storage, telegram_id, "npc" if npc else "mutant")
        killer_name = killer_label_for_kind(kind, npc=npc) if kind else label
        from app.game_logic import remember_death_killer

        remember_death_killer(storage, telegram_id, killer_name)
        return (
            player,
            note,
            ActionResult(
                False,
                f"Ограбили на маршруте «{session.origin}» → «{session.destination}».",
                payload={
                    "mission_active": False,
                    "mission_dead": True,
                    "death_location": session.location,
                    "death_cause": "npc" if npc else "mutant",
                },
            ),
            dmg,
        )
    return player, note, None, dmg


def _visit_route_checkpoint(session: SmuggleMissionSession) -> str | None:
    if session.route_index >= len(session.route):
        return None
    target = session.route[session.route_index]
    if session.player != target:
        return None
    session.route_index += 1
    labels = ("Старт", "Промежуточная", "Сдача")
    idx = min(session.route_index - 1, len(labels) - 1)
    return f"✅ Точка маршрута «{labels[idx]}» пройдена ({session.route_index}/{len(session.route)})."


def _route_complete(session: SmuggleMissionSession) -> bool:
    return session.route_index >= len(session.route)


def _load_smuggle_icon(name: str) -> Image.Image | None:
    path = SMUGGLE_ICONS_DIR / name
    if not path.is_file():
        return None
    try:
        return Image.open(path).convert("RGBA")
    except Exception:
        return None


@lru_cache(maxsize=8)
def _cached_smuggle_icon(name: str) -> Image.Image | None:
    return _load_smuggle_icon(name)


def _transport_token(
    transport: str,
    character: Character | None = None,
    *,
    size: int = 160,
    rating_points: int = 0,
) -> Image.Image:
    if transport == "foot" and character is not None:
        from app.avatar_render import render_avatar

        return render_avatar(character, rating_points=rating_points, width=size, height=size)

    icon_name = _TRANSPORT_ICON_FILES.get(transport, "walker.png")
    img = _cached_smuggle_icon(icon_name)
    if img is None:
        img = _cached_smuggle_icon("walker.png")
    if img is None:
        return Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if img.size != (size, size):
        return img.resize((size, size), Image.Resampling.LANCZOS)
    return img.copy()


def _paste_transport_token(
    canvas: Image.Image,
    token: Image.Image,
    cx: int,
    cy: int,
    diameter: int,
    *,
    is_photo: bool = False,
) -> None:
    token = token.convert("RGBA").resize((diameter, diameter), Image.Resampling.LANCZOS)
    _glow(canvas, cx, cy, (255, 210, 90), 28)
    if is_photo:
        x, y = cx - diameter // 2, cy - diameter // 2
        canvas.paste(token, (x, y), token)
        ImageDraw.Draw(canvas).rounded_rectangle(
            (x + 1, y + 1, x + diameter - 2, y + diameter - 2),
            outline=(255, 210, 90),
            width=3,
            radius=6,
        )
        return
    _paste_token_circle(canvas, token, cx, cy, diameter)


def smuggle_status_caption(session: SmuggleMissionSession, player: Character | None) -> str:
    transport = TRANSPORT_LABELS.get(session.transport, session.transport)
    return (
        f"🚚 Контрабанда → «{session.destination}»\n"
        f"Транспорт: {transport} · маршрут {session.route_index}/{len(session.route)}\n"
        f"Ход {session.moves + 1}/{session.max_moves}"
    )


def _draw_route_lines(
    draw: ImageDraw.ImageDraw,
    margin: int,
    cell: int,
    route: list[tuple[int, int]],
    route_index: int,
) -> None:
    if len(route) < 2:
        return
    points: list[tuple[int, int]] = []
    for x, y in route:
        points.append((margin + x * cell + cell // 2, margin + y * cell + cell // 2))
    for i in range(len(points) - 1):
        color = (90, 200, 120) if i < route_index - 1 else (120, 130, 150)
        draw.line((points[i], points[i + 1]), fill=color, width=3)


def render_smuggle_frame(
    session: SmuggleMissionSession,
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

    for gy in range(grid):
        for gx in range(grid):
            _draw_cell(canvas, margin + gx * cell, margin + gy * cell, cell)

    _draw_route_lines(draw, margin, cell, session.route, session.route_index)

    for i, (rx, ry) in enumerate(session.route):
        cx = margin + rx * cell + cell // 2
        cy = margin + ry * cell + cell // 2
        done = i < session.route_index
        ring = (100, 180, 110) if done else (255, 210, 70)
        _paste_circle(canvas, _checkpoint_icon(i + 1, done), cx, cy, 56, ring_color=ring, ring_width=4)

    for hx, hy in session.hazards:
        cx = margin + hx * cell + cell // 2
        cy = margin + hy * cell + cell // 2
        sprite = mission_icon_image(ANOMALY_ICON_KEY)
        if sprite is not None:
            _paste_token_circle(canvas, sprite, cx, cy, MISSION_ICON_GRID_DIAMETER)
        else:
            _glow(canvas, cx, cy, (255, 120, 40), 24)

    enemy_ring = (210, 55, 45)
    for i, (ex, ey) in enumerate(session.enemies):
        cx = margin + ex * cell + cell // 2
        cy = margin + ey * cell + cell // 2
        kind = session.enemy_kinds[i] if i < len(session.enemy_kinds) else MUTANT_SPRITE_KEYS[0]
        sprite = mutant_sprite_image(kind)
        enemy_diameter = mutant_grid_diameter(kind, default=MISSION_MUTANT_GRID_DIAMETER)
        ring_w = 4 if kind == "giant" else 3
        if sprite is not None:
            _paste_circle(canvas, sprite, cx, cy, enemy_diameter, ring_color=enemy_ring, ring_width=ring_w)
        else:
            _draw_enemy_icon(ImageDraw.Draw(canvas), cx, cy, marauder=False)

    for i, (nx_, ny_) in enumerate(session.npcs):
        cx = margin + nx_ * cell + cell // 2
        cy = margin + ny_ * cell + cell // 2
        kind = session.npc_kinds[i] if i < len(session.npc_kinds) else NPC_SPRITE_KEYS[0]
        sprite = npc_sprite_image(kind)
        if sprite is not None:
            _paste_circle(canvas, sprite, cx, cy, MISSION_NPC_GRID_DIAMETER, ring_color=enemy_ring, ring_width=3)
        else:
            _draw_enemy_icon(ImageDraw.Draw(canvas), cx, cy, marauder=True)

    px, py = session.player
    pcx = margin + px * cell + cell // 2
    pcy = margin + py * cell + cell // 2
    token = _transport_token(session.transport, character, rating_points=rating_points)
    _paste_transport_token(
        canvas,
        token,
        pcx,
        pcy,
        72,
        is_photo=session.transport in {"foot", "bicycle", "niva", "truck"}
        and (session.transport != "foot" or character is not None),
    )

    pl = margin + grid_px + 20
    pr = width - margin
    pt = margin - 8
    pb = height - margin + 8
    draw.rounded_rectangle((pl, pt, pr, pb), radius=16, fill=(48, 50, 54, 255), outline=(100, 104, 110), width=2)
    thumb = (pl + 16, pt + 14, pr - 16, pt + 120)
    loc_img = _load_location_thumb(session.origin)
    if loc_img is not None:
        _paste_rounded(canvas, loc_img, thumb, radius=10)
        ImageDraw.Draw(canvas).rounded_rectangle(thumb, radius=10, outline=(110, 120, 100), width=2)

    draw = ImageDraw.Draw(canvas)
    body = _load_font(17)
    small = _load_font(14)
    draw.text((pl + 18, pt + 128), f"→ {session.destination}", fill=(245, 245, 245), font=body)
    draw.text(
        (pl + 18, pt + 158),
        TRANSPORT_LABELS.get(session.transport, session.transport),
        fill=(255, 210, 120),
        font=body,
    )
    y = pt + 190
    draw.text((pl + 18, y), f"Маршрут: {session.route_index}/{len(session.route)}", fill=(150, 230, 170), font=body)
    draw.text((pl + 18, y + 26), f"Ход {session.moves + 1}/{session.max_moves}", fill=(200, 200, 200), font=small)
    draw.text((pl + 18, y + 48), "Жёлтые — точки, линия — путь", fill=(170, 170, 170), font=small)
    draw.text((pl + 18, y + 72), f"Шанс сдачи ~{session.success_chance}%", fill=(200, 180, 140), font=small)

    draw_enemy_hud(
        canvas,
        hud_slots_from_kinds(session.enemy_kinds, session.npc_kinds),
        panel_left=pl,
        panel_top=pt,
        panel_right=pr,
        panel_bottom=pb,
    )

    buf = BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def _checkpoint_icon(number: int, done: bool) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fill = (80, 140, 90) if done else (200, 160, 40)
    d.ellipse((4, 4, size - 4, size - 4), fill=fill, outline=(240, 240, 240), width=2)
    try:
        font = ImageFont.truetype(FONT_CANDIDATES[0], 22)
    except Exception:
        font = ImageFont.load_default()
    d.text((size // 2 - 6, size // 2 - 12), str(number), fill=(255, 255, 255), font=font)
    return img


def render_smuggle_for_player(
    storage: Storage,
    telegram_id: int,
    session: SmuggleMissionSession,
    player: Character,
) -> bytes:
    try:
        rating = max(0, int(storage.get_player_stats(telegram_id).get("rating_points", 0)))
    except Exception:
        rating = 0
    return render_smuggle_frame(session, player, rating_points=rating)


def _abort_smuggle_combat_death(storage: Storage, telegram_id: int, reason: str) -> str:
    """Смерть на маршруте: груз потерян, без доп. ограбления (урон уже получен)."""
    from app.game_logic import _apply_durability_decay

    _clear_smuggle_run(storage, telegram_id)
    _apply_durability_decay(storage, telegram_id, weapon_loss=5, armor_loss=3)
    return reason


def _fail_smuggle_run(storage: Storage, telegram_id: int, reason: str) -> str:
    """Провал рейса со штрафом (тайм-аут, срыв). Без штрафа если уже мёртв."""
    from app.game_logic import fail_smuggling_delivery, get_active_smuggling

    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is not None and player.health <= 0:
        _clear_smuggle_run(storage, telegram_id)
        return reason.strip() or "Рейс сорван."
    if get_active_smuggling(storage, telegram_id) is not None or get_smuggle_session(storage, telegram_id):
        return fail_smuggling_delivery(storage, telegram_id, reason)
    _clear_smuggle_run(storage, telegram_id)
    return reason


def _clear_smuggle_run(storage: Storage, telegram_id: int) -> None:
    clear_smuggle_session(storage, telegram_id)
    clear_active_smuggling(storage, telegram_id)


def abandon_smuggle_mission(storage: Storage, telegram_id: int) -> ActionResult:
    from app.game_logic import abandon_smuggling_run

    return abandon_smuggling_run(storage, telegram_id)


def move_smuggle_mission(storage: Storage, telegram_id: int, direction: str) -> ActionResult:
    timeout = check_smuggle_session_timeout(storage, telegram_id)
    if timeout is not None:
        return timeout
    session = get_smuggle_session(storage, telegram_id)
    if session is None:
        return ActionResult(False, "Активного тактического рейса нет.")
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        clear_smuggle_session(storage, telegram_id)
        return ActionResult(False, "Сначала создай персонажа.")
    if _is_dead(player):
        from app.game_logic import peek_death_cause

        fail_text = _abort_smuggle_combat_death(storage, telegram_id, "Рейс контрабанды сорван.")
        cause = peek_death_cause(storage, telegram_id) or "combat"
        return ActionResult(
            False,
            fail_text,
            payload={
                "mission_active": False,
                "mission_dead": True,
                "death_location": session.location,
                "death_cause": cause,
            },
        )

    delta = MOVE_DELTAS.get(direction)
    if delta is None:
        return ActionResult(False, "Некорректное направление.")
    nx = session.player[0] + delta[0]
    ny = session.player[1] + delta[1]
    if not (0 <= nx < session.grid and 0 <= ny < session.grid):
        image = render_smuggle_for_player(storage, telegram_id, session, player)
        return ActionResult(
            False,
            "Край поля — туда не проехать.",
            payload={
                "mission_image": image,
                "mission_active": True,
                "caption": smuggle_status_caption(session, player),
            },
        )

    expected_seq = session.turn_seq
    session.player = (nx, ny)
    session.player_facing = direction
    session.moves += 1
    notes: list[str] = []
    compat = _as_quest_compat(session)
    pending_damage = 0
    death_result: ActionResult | None = None

    def _fight(label: str, unit_attr: str, *, kinds_attr: str | None = None, npc: bool = False) -> None:
        nonlocal player, notes, pending_damage, death_result
        player, note, dead, dmg = _resolve_smuggle_hostile(
            storage,
            telegram_id,
            session,
            player,
            label,
            unit_attr,
            kinds_attr=kinds_attr,
            npc=npc,
        )
        pending_damage += dmg
        if note:
            notes.append(note)
        if dead is not None:
            death_result = dead

    _fight("мутантом", "enemies", kinds_attr="enemy_kinds")
    _fight("НПС", "npcs", kinds_attr="npc_kinds", npc=True)

    if session.player in session.hazards:
        dmg = _hazard_damage("scout", player)
        pending_damage += dmg
        session.hazards = [h for h in session.hazards if h != session.player]
        notes.append(f"Аномалия: −{dmg} HP.")
        if int(player.health) - pending_damage <= 0:
            remember_death_cause(storage, telegram_id, "anomaly")
            death_result = ActionResult(
                False,
                "Аномалия на маршруте. Груз потерян.",
                payload={
                    "mission_active": False,
                    "mission_dead": True,
                    "death_location": session.location,
                    "death_cause": "anomaly",
                },
            )

    route_note = _visit_route_checkpoint(session)
    if route_note:
        notes.append(route_note)

    notes.extend(_maybe_move_hostiles(compat)[0])
    _fight("мутанта", "enemies", kinds_attr="enemy_kinds")
    _fight("НПС", "npcs", kinds_attr="npc_kinds", npc=True)

    if _route_complete(session):
        session.turn_seq = expected_seq + 1
        if not _save_smuggle_if_turn_ok(storage, telegram_id, session, expected_seq):
            from app.tactical_combat import STALE_TURN_MESSAGE

            return ActionResult(False, STALE_TURN_MESSAGE)
        if pending_damage:
            storage.change_health(telegram_id, -pending_damage)
        if death_result is not None:
            fail_text = _abort_smuggle_combat_death(
                storage, telegram_id, "Погиб на маршруте — груз потерян."
            )
            payload = death_result.payload or {}
            return ActionResult(False, fail_text, payload=payload)
        delivery = begin_smuggling_travel_after_grid(storage, telegram_id)
        if not delivery.ok:
            return delivery
        return ActionResult(
            True,
            delivery.text,
            payload=delivery.payload or {"mission_active": False, "mission_travel_started": True},
        )

    if session.moves >= session.max_moves:
        session.turn_seq = expected_seq + 1
        if not _save_smuggle_if_turn_ok(storage, telegram_id, session, expected_seq):
            from app.tactical_combat import STALE_TURN_MESSAGE

            return ActionResult(False, STALE_TURN_MESSAGE)
        if pending_damage:
            storage.change_health(telegram_id, -pending_damage)
        if death_result is not None:
            fail_text = _abort_smuggle_combat_death(
                storage, telegram_id, "Погиб на маршруте — груз потерян."
            )
            payload = death_result.payload or {}
            return ActionResult(False, fail_text, payload=payload)
        return check_smuggle_session_timeout(storage, telegram_id) or ActionResult(
            False,
            _fail_smuggle_run(storage, telegram_id, "Время рейса вышло — ограбили."),
            payload={"mission_active": False, "mission_done": True},
        )

    session.turn_seq = expected_seq + 1
    if not _save_smuggle_if_turn_ok(storage, telegram_id, session, expected_seq):
        from app.tactical_combat import STALE_TURN_MESSAGE

        return ActionResult(False, STALE_TURN_MESSAGE)
    if pending_damage:
        storage.change_health(telegram_id, -pending_damage)
    if death_result is not None:
        fail_text = _abort_smuggle_combat_death(
            storage, telegram_id, "Погиб на маршруте — груз потерян."
        )
        payload = death_result.payload or {}
        return ActionResult(False, fail_text, payload=payload)
    player = storage.get_character(telegram_id, refresh_energy=False) or player
    image = render_smuggle_for_player(storage, telegram_id, session, player)
    note = " ".join(notes) if notes else "Дорога чистая."
    return ActionResult(
        True,
        note,
        payload={
            "mission_image": image,
            "mission_active": True,
            "caption": smuggle_status_caption(session, player),
            "move_note": note,
        },
    )
