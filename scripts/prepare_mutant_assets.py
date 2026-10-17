#!/usr/bin/env python3
"""Подготовка спрайтов мутантов для grid-миссий и карточек PDA.

Кладите исходники в assets/mutants/source/ (png/jpg/webp):
  blind_dog, tushkano, pseudodog, bloodsucker, flesh, controller, giant

Запуск:
  python3 scripts/prepare_mutant_assets.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.mutant_assets import (  # noqa: E402
    MISSION_MUTANT_CARD_SIZE,
    MISSION_MUTANT_GRID_SIZE,
    MUTANT_SPRITE_KEYS,
    MUTANTS_CARD_DIR,
    MUTANTS_GRID_DIR,
    MUTANTS_SOURCE_DIR,
)

SOURCE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".PNG", ".JPG", ".JPEG", ".WEBP")


def _find_source(stem: str) -> Path | None:
    for ext in SOURCE_EXTS:
        path = MUTANTS_SOURCE_DIR / f"{stem}{ext}"
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


def _prepare_one(stem: str, size: int, out_dir: Path) -> bool:
    src = _find_source(stem)
    if src is None:
        print(f"  skip {stem}: нет файла в {MUTANTS_SOURCE_DIR}")
        return False
    img = Image.open(src)
    img = _center_square(img)
    img = ImageOps.fit(img, (size, size), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{stem}.png"
    img.save(out_path, format="PNG", optimize=True)
    print(f"  {stem}: {src.name} -> {out_path.relative_to(ROOT)} ({size}x{size})")
    return True


def main() -> None:
    MUTANTS_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    print("Grid sprites:")
    for key in MUTANT_SPRITE_KEYS:
        if _prepare_one(key, MISSION_MUTANT_GRID_SIZE, MUTANTS_GRID_DIR):
            ok += 1
    print("Card sprites:")
    for key in MUTANT_SPRITE_KEYS:
        if _prepare_one(key, MISSION_MUTANT_CARD_SIZE, MUTANTS_CARD_DIR):
            ok += 1
    if ok == 0:
        print("Нет исходников. Положите файлы в assets/mutants/source/")
        sys.exit(1)
    print(f"Done ({ok} files written).")


if __name__ == "__main__":
    main()
