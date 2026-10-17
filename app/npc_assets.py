from __future__ import annotations

import random
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NPCS_DIR = PROJECT_ROOT / "assets" / "npcs"
NPCS_GRID_DIR = NPCS_DIR / "grid"
NPCS_CARD_DIR = NPCS_DIR / "card"
NPCS_SOURCE_DIR = NPCS_DIR / "source"
MONOLITH_AVATAR_SOURCE = PROJECT_ROOT / "assets" / "avatars" / "factions" / "monolit" / "monolit.jpg"

# Те же размеры, что у мутантов (клетка миссии 108px).
MISSION_NPC_GRID_DIAMETER = 80
MISSION_NPC_GRID_SIZE = 88
MISSION_NPC_CARD_SIZE = 256

NPC_SPRITES: dict[str, str] = {
    "maloy": "Малой",
    "bandit": "Бандит",
    "mercenary": "Наёмник",
    "soldier": "Военный",
    "monolith": "Монолит",
    "dark_stalker": "Тёмный сталкер",
}

NPC_SPRITE_KEYS: tuple[str, ...] = tuple(NPC_SPRITES.keys())

# Центр кропа для тактических спрайтов (доля ширины/высоты).
NPC_SOURCE_CROP_CENTER: dict[str, tuple[float, float]] = {
    "monolith": (0.5, 0.42),
}


def pick_npc_kind(*, marauder: bool = False) -> str:
    if marauder:
        return random.choice(("bandit", "maloy", "mercenary"))
    return random.choice(NPC_SPRITE_KEYS)


def _npc_source_path(kind: str) -> Path | None:
    if kind == "monolith" and MONOLITH_AVATAR_SOURCE.is_file():
        return MONOLITH_AVATAR_SOURCE
    return None


def _center_square(img: Image.Image, *, center: tuple[float, float] = (0.5, 0.5)) -> Image.Image:
    img = img.convert("RGBA")
    w, h = img.size
    side = min(w, h)
    cx = int(w * center[0])
    cy = int(h * center[1])
    left = max(0, min(w - side, cx - side // 2))
    top = max(0, min(h - side, cy - side // 2))
    return img.crop((left, top, left + side, top + side))


def _enhance_zone_photo(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    rgb = img.convert("RGB")
    rgb = ImageEnhance.Contrast(rgb).enhance(1.08)
    rgb = ImageEnhance.Brightness(rgb).enhance(1.05)
    rgb = ImageEnhance.Color(rgb).enhance(0.95)
    out = rgb.convert("RGBA")
    out.putalpha(img.split()[-1])
    return out


def _fit_npc_sprite(img: Image.Image, size: int, *, center: tuple[float, float]) -> Image.Image:
    sq = _center_square(_enhance_zone_photo(img), center=center)
    return ImageOps.fit(sq, (size, size), method=Image.Resampling.LANCZOS, centering=center)


def render_npc_sprite_png(kind: str, size: int) -> bytes | None:
    src = _npc_source_path(kind)
    if src is None:
        return None
    center = NPC_SOURCE_CROP_CENTER.get(kind, (0.5, 0.5))
    try:
        with Image.open(src) as img:
            out = _fit_npc_sprite(img, size, center=center)
            buf = BytesIO()
            out.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
    except Exception:
        return None


def write_monolith_npc_assets(*, center: tuple[float, float] = (0.5, 0.42)) -> bool:
    """Собрать grid/card PNG для Монолита из assets/avatars/factions/monolit/monolit.jpg."""
    if not MONOLITH_AVATAR_SOURCE.is_file():
        return False
    NPC_SOURCE_CROP_CENTER["monolith"] = center
    load_npc_grid_sprite.cache_clear()
    load_npc_card_sprite.cache_clear()
    grid_png = render_npc_sprite_png("monolith", MISSION_NPC_GRID_SIZE)
    card_png = render_npc_sprite_png("monolith", MISSION_NPC_CARD_SIZE)
    if grid_png is None or card_png is None:
        return False
    NPCS_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    NPCS_GRID_DIR.mkdir(parents=True, exist_ok=True)
    NPCS_CARD_DIR.mkdir(parents=True, exist_ok=True)
    (NPCS_GRID_DIR / "monolith.png").write_bytes(grid_png)
    (NPCS_CARD_DIR / "monolith.png").write_bytes(card_png)
    with Image.open(MONOLITH_AVATAR_SOURCE) as img:
        source = _fit_npc_sprite(img, 512, center=center)
        source.save(NPCS_SOURCE_DIR / "monolith.png", format="PNG", optimize=True)
    return True


def _npc_sprite_path(kind: str) -> Path | None:
    path = NPCS_GRID_DIR / f"{kind}.png"
    if path.is_file():
        return path
    if _npc_source_path(kind) is not None:
        return path
    fallback = {"dark_stalker": "mercenary"}.get(kind)
    if fallback:
        alt = NPCS_GRID_DIR / f"{fallback}.png"
        if alt.is_file():
            return alt
    return None


def npc_sprite_fallback_kind(kind: str) -> str:
    """Ключ спрайта для отрисовки (с запасным вариантом)."""
    if (NPCS_GRID_DIR / f"{kind}.png").is_file():
        return kind
    if _npc_source_path(kind) is not None:
        return kind
    return {"dark_stalker": "mercenary"}.get(kind, kind)


@lru_cache(maxsize=32)
def load_npc_grid_sprite(kind: str) -> bytes | None:
    """PNG 88×88 для отрисовки на поле миссии."""
    path = NPCS_GRID_DIR / f"{kind}.png"
    if path.is_file():
        return path.read_bytes()
    rendered = render_npc_sprite_png(kind, MISSION_NPC_GRID_SIZE)
    if rendered is not None:
        return rendered
    fallback = {"dark_stalker": "mercenary"}.get(kind)
    if fallback:
        alt = NPCS_GRID_DIR / f"{fallback}.png"
        if alt.is_file():
            return alt.read_bytes()
    return None


@lru_cache(maxsize=32)
def load_npc_card_sprite(kind: str) -> bytes | None:
    """PNG 256×256 для карточек/PDA (будущие задания)."""
    path = NPCS_CARD_DIR / f"{kind}.png"
    if path.is_file():
        return path.read_bytes()
    rendered = render_npc_sprite_png(kind, MISSION_NPC_CARD_SIZE)
    if rendered is not None:
        return rendered
    fallback = {"dark_stalker": "mercenary"}.get(kind)
    if fallback:
        alt = NPCS_CARD_DIR / f"{fallback}.png"
        if alt.is_file():
            return alt.read_bytes()
    return None


def npc_sprite_image(kind: str, *, card: bool = False) -> Image.Image | None:
    raw = load_npc_card_sprite(kind) if card else load_npc_grid_sprite(kind)
    if raw is None:
        return None
    try:
        return Image.open(BytesIO(raw)).convert("RGBA")
    except Exception:
        return None
