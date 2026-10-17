from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ICONS_DIR = PROJECT_ROOT / "assets" / "mission_icons"
ICONS_GRID_DIR = ICONS_DIR / "grid"
ICONS_SOURCE_DIR = ICONS_DIR / "source"

MISSION_ICON_GRID_DIAMETER = 80
MISSION_ICON_GRID_SIZE = 88

ANOMALY_ICON_KEY = "anomaly"
OBJECTIVE_ICON_KEY = "objective"


@lru_cache(maxsize=8)
def load_mission_grid_icon(kind: str) -> bytes | None:
    path = ICONS_GRID_DIR / f"{kind}.png"
    if not path.is_file():
        return None
    return path.read_bytes()


def mission_icon_image(kind: str) -> Image.Image | None:
    raw = load_mission_grid_icon(kind)
    if raw is None:
        return None
    try:
        return Image.open(BytesIO(raw)).convert("RGBA")
    except Exception:
        return None
