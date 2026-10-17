#!/usr/bin/env python3
"""Реалистичные ассеты из S.T.A.L.K.E.R. и Wikimedia для НПС и транспорта."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.npc_assets import (  # noqa: E402
    MISSION_NPC_CARD_SIZE,
    MISSION_NPC_GRID_SIZE,
    NPCS_CARD_DIR,
    NPCS_GRID_DIR,
    NPCS_SOURCE_DIR,
)

REFS = ROOT / "assets" / "stalker_refs"
SMUGGLE_DIR = ROOT / "assets" / "smuggle"
AVATARS = ROOT / "assets" / "avatars"

# (путь, центр кропа)
NPC_SOURCES: dict[str, tuple[Path, tuple[float, float]]] = {
    "bandit": (REFS / "bandit_wiki.png", (0.52, 0.52)),
    "mercenary": (AVATARS / "скины" / "Нейтралы" / "нейтралы2.jpg", (0.5, 0.45)),
    "soldier": (REFS / "military_swamps.jpg", (0.28, 0.55)),
    "monolith": (AVATARS / "factions" / "monolit" / "monolit.jpg", (0.5, 0.42)),
}

SMUGGLE_SOURCES: dict[str, tuple[Path, tuple[float, float]]] = {
    "niva": (REFS / "uaz469.png", (0.55, 0.55)),
    "truck": (REFS / "zil131.png", (0.50, 0.52)),
    "bicycle": (REFS / "rusty_bicycle.jpg", (0.5, 0.55)),
}


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
    rgb = ImageEnhance.Contrast(rgb).enhance(1.1)
    rgb = ImageEnhance.Brightness(rgb).enhance(1.1)
    rgb = ImageEnhance.Color(rgb).enhance(0.92)
    out = rgb.convert("RGBA")
    out.putalpha(img.split()[-1])
    return out


def _fit_photo(img: Image.Image, size: int, *, center: tuple[float, float]) -> Image.Image:
    sq = _center_square(_enhance_zone_photo(img), center=center)
    return ImageOps.fit(sq, (size, size), method=Image.Resampling.LANCZOS, centering=center)


def _write_png(path: Path, img: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG", optimize=True)


def prepare_npc_realistic() -> None:
    for key, (src, center) in NPC_SOURCES.items():
        if not src.is_file():
            print(f"  skip {key}: нет {src}")
            continue
        img = Image.open(src)
        _write_png(NPCS_SOURCE_DIR / f"{key}.png", _fit_photo(img, 512, center=center))
        _write_png(NPCS_GRID_DIR / f"{key}.png", _fit_photo(img, MISSION_NPC_GRID_SIZE, center=center))
        _write_png(NPCS_CARD_DIR / f"{key}.png", _fit_photo(img, MISSION_NPC_CARD_SIZE, center=center))
        print(f"  npc {key} <- {src.name}")


def prepare_smuggle_realistic() -> None:
    for key, (src, center) in SMUGGLE_SOURCES.items():
        if not src.is_file():
            print(f"  skip {key}: нет {src}")
            continue
        img = Image.open(src)
        _write_png(SMUGGLE_DIR / f"{key}.png", _fit_photo(img, 256, center=center))
        print(f"  smuggle {key} <- {src.name}")


def main() -> None:
    missing = [p for p, _ in list(NPC_SOURCES.values()) + list(SMUGGLE_SOURCES.values()) if not p.is_file()]
    if missing:
        print("Не хватает файлов:")
        for p in missing:
            print(f"  - {p}")
        sys.exit(1)
    print("NPC:")
    prepare_npc_realistic()
    print("Transport:")
    prepare_smuggle_realistic()
    from app.npc_assets import load_npc_grid_sprite
    from app.smuggle_mission import _cached_smuggle_icon

    load_npc_grid_sprite.cache_clear()
    _cached_smuggle_icon.cache_clear()
    print("Done.")


if __name__ == "__main__":
    main()
