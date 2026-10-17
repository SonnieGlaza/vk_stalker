"""Общие хелперы отрисовки тактических полей: аватары, спрайты, шрифты, подсветка игрока."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.artifact_hunt import _paste_circle
from app.mutant_assets import (
    MISSION_MUTANT_GRID_DIAMETER,
    MUTANT_SPRITE_KEYS,
    mutant_grid_diameter,
    mutant_sprite_image,
    pick_mutant_kind,
)
from app.npc_assets import MISSION_NPC_GRID_DIAMETER, NPC_SPRITE_KEYS, npc_sprite_image
from app.storage import Character, Storage

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_FONT_PATH = PROJECT_ROOT / "assets" / "fonts" / "DejaVuSans.ttf"
LOCAL_NOTO_FONT_PATH = PROJECT_ROOT / "assets" / "fonts" / "NotoSans-Regular.ttf"
FONT_CANDIDATES = (
    str(LOCAL_NOTO_FONT_PATH),
    str(LOCAL_FONT_PATH),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)

VIEWER_SQUARE_COLOR = (80, 230, 255)


def _font_supports_cyrillic(font: ImageFont.ImageFont) -> bool:
    try:
        bbox = font.getbbox("Карточка")
    except Exception:
        return False
    if bbox is None or (bbox[2] - bbox[0]) <= 0:
        return False
    samples = []
    for probe in ("К", "Я", "Ж"):
        try:
            samples.append(bytes(font.getmask(probe)))
        except Exception:
            return False
    return len(set(samples)) > 1


@lru_cache(maxsize=1)
def _read_local_font_bytes(path: Path) -> bytes | None:
    if not path.exists():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def load_tactical_font(size: int) -> ImageFont.ImageFont:
    for local_path in (LOCAL_NOTO_FONT_PATH, LOCAL_FONT_PATH):
        raw = _read_local_font_bytes(local_path)
        if raw is not None:
            try:
                font = ImageFont.truetype(BytesIO(raw), size=size)
                if _font_supports_cyrillic(font):
                    return font
            except OSError:
                continue
    for path in FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        try:
            font = ImageFont.truetype(path, size=size)
            if _font_supports_cyrillic(font):
                return font
        except OSError:
            continue
    return ImageFont.load_default()


def draw_viewer_cell_outline(
    draw: ImageDraw.ImageDraw,
    *,
    margin: int,
    cell: int,
    px: int,
    py: int,
    width: int = 4,
) -> None:
    left = margin + px * cell
    top = margin + py * cell
    draw.rectangle(
        (left + 2, top + 2, left + cell - 3, top + cell - 3),
        outline=VIEWER_SQUARE_COLOR,
        width=width,
    )


def _dim_dead_token(token: Image.Image) -> Image.Image:
    token = token.convert("RGBA")
    gray = ImageOps.grayscale(token).point(lambda p: int(p * 0.45))
    alpha = token.split()[3]
    return Image.merge("RGBA", (gray, gray, gray, alpha))


def _rating_points(storage: Storage, telegram_id: int) -> int:
    try:
        return int(storage.get_player_stats(telegram_id).get("rating_points", 0))
    except Exception:
        return 0


def paste_player_avatar(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    storage: Storage,
    *,
    pid: int,
    cx: int,
    cy: int,
    diameter: int,
    ring_color: tuple[int, int, int],
    hp: int,
    is_active: bool = False,
    viewer_cell: tuple[int, int, int, int] | None = None,
) -> None:
    """Накладывает аватар игрока; viewer_cell = (margin, cell, px, py) для квадрата «ты тут»."""
    if is_active and hp > 0:
        draw.ellipse(
            (cx - diameter // 2 - 6, cy - diameter // 2 - 6, cx + diameter // 2 + 6, cy + diameter // 2 + 6),
            outline=(255, 230, 80),
            width=3,
        )
    token: Image.Image | None = None
    character = storage.get_character(pid, refresh_energy=False)
    if character is not None:
        try:
            from app.avatar_render import render_avatar

            src = max(96, diameter * 2)
            token = render_avatar(character, rating_points=_rating_points(storage, pid), width=src, height=src)
        except Exception:
            token = None
    if token is None:
        token = Image.new("RGBA", (diameter * 2, diameter * 2), (0, 0, 0, 0))
        td = ImageDraw.Draw(token)
        td.ellipse((8, 8, diameter * 2 - 8, diameter * 2 - 8), fill=tuple(c // 2 for c in ring_color))
    is_dead = hp <= 0
    if is_dead:
        token = _dim_dead_token(token)
        ring_color = (120, 120, 120)
    _paste_circle(canvas, token, cx, cy, diameter, ring_color=ring_color, ring_width=3)
    if is_dead:
        r = diameter // 2 - 6
        draw.line((cx - r, cy - r, cx + r, cy + r), fill=(225, 45, 45, 235), width=4)
        draw.line((cx + r, cy - r, cx - r, cy + r), fill=(225, 45, 45, 235), width=4)
    if viewer_cell is not None:
        margin, cell, px, py = viewer_cell
        draw_viewer_cell_outline(draw, margin=margin, cell=cell, px=px, py=py)


def paste_mutant_sprite(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    *,
    cx: int,
    cy: int,
    kind: str | None = None,
    diameter: int = MISSION_MUTANT_GRID_DIAMETER,
    ring_color: tuple[int, int, int] = (210, 55, 45),
    wave: bool = False,
) -> None:
    key = kind or pick_mutant_kind()
    draw_diameter = mutant_grid_diameter(key, default=diameter)
    sprite = mutant_sprite_image(key)
    if sprite is not None:
        if key == "bloodsucker":
            # Плотнее иконка, без красной обводки — «полупрозрачный» силуэт клетки.
            r, g, b, a = sprite.split()
            a = a.point(lambda p: min(255, int(p * 1.25) + 48) if p > 0 else 0)
            sprite = Image.merge("RGBA", (r, g, b, a))
            _paste_circle(canvas, sprite, cx, cy, draw_diameter, ring_color=ring_color, ring_width=0)
            return
        ring = (255, 120, 80) if wave else ring_color
        ring_w = 3 if key == "giant" else 2
        _paste_circle(canvas, sprite, cx, cy, draw_diameter, ring_color=ring, ring_width=ring_w)
        return
    color = (180, 60, 40) if wave else (50, 90, 45)
    outline = (255, 120, 80) if wave else (120, 200, 80)
    if key == "bloodsucker":
        outline = color
    r = draw_diameter // 2 - 4
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color, outline=outline, width=2)


def paste_npc_sprite(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    *,
    cx: int,
    cy: int,
    kind: str | None = None,
    diameter: int = MISSION_NPC_GRID_DIAMETER,
    ring_color: tuple[int, int, int] = (210, 55, 45),
) -> None:
    key = kind or "bandit"
    if key not in NPC_SPRITE_KEYS:
        key = NPC_SPRITE_KEYS[0]
    sprite = npc_sprite_image("maloy")
    if sprite is not None:
        _paste_circle(canvas, sprite, cx, cy, diameter, ring_color=ring_color, ring_width=2)
        return
    r = diameter // 2 - 4
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(90, 90, 90), outline=(160, 160, 160), width=2)


def paste_location_field_photo(
    canvas: Image.Image,
    location: str,
    *,
    margin: int,
    grid_px: int,
    alpha: int = 150,
) -> bool:
    """Фото локации под сеткой. True, если картинка нашлась."""
    from app.artifact_hunt import _cover_crop, _load_location_thumb

    thumb = _load_location_thumb(location)
    if thumb is None:
        return False
    field = _cover_crop(thumb, grid_px, grid_px).convert("RGBA")
    field.putalpha(alpha)
    canvas.paste(field, (margin, margin), field)
    return True


def draw_tactical_cell_overlay(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    *,
    left: int,
    top: int,
    cell: int,
    has_photo: bool,
    gx: int,
    gy: int,
    fill: tuple[int, int, int] | None = None,
    overlay: tuple[int, int, int, int] = (10, 12, 14, 50),
) -> None:
    """Клетка: серый фон без фото или полупрозрачный оверлей поверх карты."""
    if has_photo:
        layer = Image.new("RGBA", (cell, cell), overlay)
        canvas.alpha_composite(layer, (left, top))
    else:
        tone = 62 + ((gx * 17 + gy * 23) % 18)
        color = fill or (tone, tone - 4, tone - 8)
        draw.rectangle((left, top, left + cell - 1, top + cell - 1), fill=color)
    draw.rectangle((left, top, left + cell - 1, top + cell - 1), outline=(28, 30, 34), width=1)


def hostile_kind_to_sprite(kind: str) -> tuple[str, bool]:
    """Вернёт (sprite_key, is_npc)."""
    if kind == "bandit":
        return "bandit", True
    if kind == "mutant":
        return pick_mutant_kind(), False
    if kind in MUTANT_SPRITE_KEYS:
        return kind, False
    if kind in NPC_SPRITE_KEYS:
        return kind, True
    return pick_mutant_kind(), False
