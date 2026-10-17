#!/usr/bin/env python3
"""Подготовка спрайтов НПС (мародёров) для grid-миссий и карточек PDA.

Кладите исходники в assets/npcs/source/ (png/jpg/webp), например maloy.png.
Красный оверлей-текст снимается автоматически.

Запуск:
  python3 scripts/prepare_npc_assets.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.npc_assets import (  # noqa: E402
    MISSION_NPC_CARD_SIZE,
    MISSION_NPC_GRID_SIZE,
    NPC_SPRITE_KEYS,
    NPCS_CARD_DIR,
    NPCS_GRID_DIR,
    NPCS_SOURCE_DIR,
)

SOURCE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".PNG", ".JPG", ".JPEG", ".WEBP")


def _find_source(stem: str) -> Path | None:
    for ext in SOURCE_EXTS:
        path = NPCS_SOURCE_DIR / f"{stem}{ext}"
        if path.is_file():
            return path
    return None


def _remove_red_overlay(img: Image.Image) -> Image.Image:
    """Убрать ярко-красный текст/оверлей: маска по цвету + заливка из соседей."""
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    mp = mask.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            # Ярко-красный оверлей (как «Малой / Мародёр»), не бурая одежда.
            if a > 0 and r >= 150 and g <= 90 and b <= 90 and r >= g + 55 and r >= b + 55:
                mp[x, y] = 255
    # Чуть расширить маску, чтобы захватить антиалиасинг букв.
    mask = mask.filter(ImageFilter.MaxFilter(5))
    if mask.getbbox() is None:
        return img

    # Размытая копия без красных пикселей — источник для заливки.
    cleaned = img.copy()
    cpx = cleaned.load()
    for y in range(h):
        for x in range(w):
            if mp[x, y] > 128:
                cpx[x, y] = (0, 0, 0, 0)
    # Несколько проходов: усреднение непрозрачных соседей в дырах.
    for _ in range(18):
        nxt = cleaned.copy()
        npx = nxt.load()
        spx = cleaned.load()
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                if mp[x, y] <= 128:
                    continue
                rs = gs = bs = cnt = 0
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        rr, gg, bb, aa = spx[x + dx, y + dy]
                        if aa < 200:
                            continue
                        # не тащить остатки красного
                        if rr >= 150 and gg <= 90 and bb <= 90 and rr >= gg + 55:
                            continue
                        rs += rr
                        gs += gg
                        bs += bb
                        cnt += 1
                if cnt:
                    npx[x, y] = (rs // cnt, gs // cnt, bs // cnt, 255)
        cleaned = nxt

    # Сгладить стык маски.
    soft = mask.filter(ImageFilter.GaussianBlur(1.2))
    return Image.composite(cleaned, img, soft)


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
        print(f"  skip {stem}: нет файла в {NPCS_SOURCE_DIR}")
        return False
    img = Image.open(src)
    img = _remove_red_overlay(img)
    img = _center_square(img)
    img = ImageOps.fit(img, (size, size), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{stem}.png"
    img.save(out_path, format="PNG", optimize=True)
    print(f"  {stem}: {src.name} -> {out_path.relative_to(ROOT)} ({size}x{size})")
    return True


def main() -> None:
    NPCS_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    print("Grid sprites:")
    for key in NPC_SPRITE_KEYS:
        if _prepare_one(key, MISSION_NPC_GRID_SIZE, NPCS_GRID_DIR):
            ok += 1
    print("Card sprites:")
    for key in NPC_SPRITE_KEYS:
        if _prepare_one(key, MISSION_NPC_CARD_SIZE, NPCS_CARD_DIR):
            ok += 1
    if ok == 0:
        print("Нет исходников. Положите файлы в assets/npcs/source/")
        sys.exit(1)
    print(f"Done ({ok} files written).")


if __name__ == "__main__":
    main()
