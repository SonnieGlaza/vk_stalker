#!/usr/bin/env python3
"""Подготовка иконок аномалий и целей для grid-миссий.

Исходники: assets/mission_icons/source/{anomaly,objective}.png
Запуск: python3 scripts/prepare_mission_icons.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.mission_icons import (  # noqa: E402
    ANOMALY_ICON_KEY,
    ICONS_GRID_DIR,
    ICONS_SOURCE_DIR,
    MISSION_ICON_GRID_SIZE,
    OBJECTIVE_ICON_KEY,
)

SOURCE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".PNG", ".JPG", ".JPEG", ".WEBP")
KEYS = (ANOMALY_ICON_KEY, OBJECTIVE_ICON_KEY)


def _find_source(stem: str) -> Path | None:
    for ext in SOURCE_EXTS:
        path = ICONS_SOURCE_DIR / f"{stem}{ext}"
        if path.is_file():
            return path
    return None


def _center_square(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def _prepare_one(stem: str) -> bool:
    src = _find_source(stem)
    if src is None:
        print(f"  skip {stem}: нет файла в {ICONS_SOURCE_DIR}")
        return False
    img = _center_square(Image.open(src))
    img = ImageOps.fit(
        img,
        (MISSION_ICON_GRID_SIZE, MISSION_ICON_GRID_SIZE),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    ICONS_GRID_DIR.mkdir(parents=True, exist_ok=True)
    out = ICONS_GRID_DIR / f"{stem}.png"
    img.save(out, format="PNG", optimize=True)
    print(f"  {stem}: {src.name} -> {out.relative_to(ROOT)} ({MISSION_ICON_GRID_SIZE}x{MISSION_ICON_GRID_SIZE})")
    return True


def main() -> None:
    ICONS_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    print("Mission icons:")
    for key in KEYS:
        if _prepare_one(key):
            ok += 1
    if ok == 0:
        print("Нет исходников.")
        sys.exit(1)
    print(f"Done ({ok} files).")


if __name__ == "__main__":
    main()
