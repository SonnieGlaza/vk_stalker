#!/usr/bin/env python3
"""Процедурная генерация спрайтов НПС (bandit/mercenary/soldier) через PIL.

В отличие от maloy.png (фото), эти спрайты рисуются кодом: силуэт бойца +
голова/шлем + акцентные детали, чтобы визуально различать типы врагов на
поле миссии. Стиль перекликается с фолбэком `_draw_enemy_icon` в
`app/quest_mission.py`, но детальнее и с индивидуальными цветами.

Запуск:
  python3 scripts/generate_npc_sprites.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.npc_assets import (  # noqa: E402
    MISSION_NPC_CARD_SIZE,
    MISSION_NPC_GRID_SIZE,
    NPCS_CARD_DIR,
    NPCS_GRID_DIR,
)

SS = 4  # supersampling для сглаживания.
BASE = 256  # рабочий размер перед даунскейлом (после SS -> 1024).


def _vignette_background(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        t = y / max(1, size - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(size):
            px[x, y] = (r, g, b)
    rgba = img.convert("RGBA")
    cx = cy = size / 2
    max_r = size * 0.75
    # Радиальный градиент через концентрические эллипсы (быстрее двойного цикла по пикселям).
    vignette = Image.new("L", (size, size), 255)
    steps = 40
    for i in range(steps):
        t = i / steps
        radius = max_r * (1 - t)
        shade = int(255 * (1 - t) ** 1.6)
        vd = ImageDraw.Draw(vignette)
        vd.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            fill=max(60, shade),
        )
    vignette = vignette.filter(ImageFilter.GaussianBlur(size * 0.06))
    dark = Image.new("RGBA", (size, size), (5, 6, 8, 255))
    return Image.composite(rgba, dark, vignette)


def _rounded(draw: ImageDraw.ImageDraw, box, radius, **kw) -> None:
    draw.rounded_rectangle(box, radius=radius, **kw)


def _draw_bandit(size: int) -> Image.Image:
    """Бандит: потёртая кожаная куртка, вязаная шапка, платок на лице."""
    img = _vignette_background(size, (58, 46, 34), (18, 14, 10))
    draw = ImageDraw.Draw(img)
    cx = size * 0.5

    # Плечи/торс — коричневая потёртая куртка.
    shoulders_y = size * 0.52
    torso = [
        (cx - size * 0.34, size * 1.02),
        (cx - size * 0.30, shoulders_y + size * 0.06),
        (cx - size * 0.14, shoulders_y - size * 0.05),
        (cx + size * 0.14, shoulders_y - size * 0.05),
        (cx + size * 0.30, shoulders_y + size * 0.06),
        (cx + size * 0.34, size * 1.02),
    ]
    draw.polygon(torso, fill=(96, 74, 50))
    draw.polygon(torso, outline=(56, 42, 28))
    # Потёртости/швы.
    draw.line((cx - size * 0.05, shoulders_y + size * 0.05, cx - size * 0.05, size * 0.98), fill=(70, 52, 34), width=max(1, size // 90))
    draw.line((cx + size * 0.05, shoulders_y + size * 0.05, cx + size * 0.05, size * 0.98), fill=(70, 52, 34), width=max(1, size // 90))
    # Ремень оружия по диагонали.
    draw.line(
        (cx - size * 0.30, shoulders_y - size * 0.02, cx + size * 0.20, size * 0.90),
        fill=(30, 26, 22),
        width=max(2, size // 40),
    )

    # Голова.
    head_r = size * 0.20
    head_cy = size * 0.30
    draw.ellipse(
        (cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r),
        fill=(196, 158, 122),
        outline=(120, 92, 66),
        width=max(1, size // 128),
    )
    # Вязаная шапка (верх головы + чуть ниже линии бровей).
    cap_top = head_cy - head_r * 1.1
    cap_edge = head_cy - head_r * 0.3
    beanie_bottom = head_cy - head_r * 0.05
    draw.pieslice(
        (cx - head_r * 1.05, cap_top, cx + head_r * 1.05, head_cy + head_r * 0.5),
        180,
        360,
        fill=(64, 58, 50),
    )
    draw.rectangle((cx - head_r * 1.05, cap_edge, cx + head_r * 1.05, beanie_bottom), fill=(64, 58, 50))
    draw.line((cx - head_r * 1.02, beanie_bottom, cx + head_r * 1.02, beanie_bottom), fill=(40, 36, 30), width=max(1, size // 110))

    # Платок на нижней части лица (красный акцент — фирменный "бандитский").
    mask_top = head_cy + head_r * 0.05
    draw.pieslice(
        (cx - head_r * 1.02, mask_top - head_r * 0.3, cx + head_r * 1.02, head_cy + head_r * 1.05),
        0,
        180,
        fill=(120, 34, 30),
    )
    draw.line((cx - head_r * 0.9, mask_top, cx + head_r * 0.9, mask_top), fill=(80, 20, 18), width=max(1, size // 130))

    # Глаза — узкая злая полоса.
    eye_y = head_cy - head_r * 0.05
    draw.line((cx - head_r * 0.55, eye_y, cx - head_r * 0.15, eye_y), fill=(20, 16, 12), width=max(2, size // 90))
    draw.line((cx + head_r * 0.15, eye_y, cx + head_r * 0.55, eye_y), fill=(20, 16, 12), width=max(2, size // 90))

    return img


def _draw_mercenary(size: int) -> Image.Image:
    """Наёмник: серо-зелёная тактика, балаклава, жёлтые линзы очков."""
    img = _vignette_background(size, (40, 46, 44), (14, 16, 16))
    draw = ImageDraw.Draw(img)
    cx = size * 0.5

    shoulders_y = size * 0.50
    torso = [
        (cx - size * 0.35, size * 1.02),
        (cx - size * 0.32, shoulders_y + size * 0.02),
        (cx - size * 0.16, shoulders_y - size * 0.07),
        (cx + size * 0.16, shoulders_y - size * 0.07),
        (cx + size * 0.32, shoulders_y + size * 0.02),
        (cx + size * 0.35, size * 1.02),
    ]
    draw.polygon(torso, fill=(72, 82, 76))
    draw.polygon(torso, outline=(38, 44, 40))
    # Тактический жилет — плашки/подсумки.
    for i, dy in enumerate((0.66, 0.78, 0.90)):
        w = size * (0.16 - i * 0.01)
        h = size * 0.08
        _rounded(
            draw,
            (cx - w / 2, size * dy - h / 2, cx + w / 2, size * dy + h / 2),
            radius=max(2, size // 40),
            fill=(48, 56, 50),
            outline=(30, 36, 32),
        )
    # Наплечники.
    draw.ellipse((cx - size * 0.34, shoulders_y - size * 0.03, cx - size * 0.18, shoulders_y + size * 0.09), fill=(58, 66, 60))
    draw.ellipse((cx + size * 0.18, shoulders_y - size * 0.03, cx + size * 0.34, shoulders_y + size * 0.09), fill=(58, 66, 60))

    # Голова — полностью в балаклаве.
    head_r = size * 0.195
    head_cy = size * 0.30
    draw.ellipse(
        (cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r),
        fill=(46, 52, 48),
        outline=(24, 28, 26),
        width=max(1, size // 128),
    )
    # Тактическая шапка/капюшон верх.
    draw.pieslice(
        (cx - head_r * 1.08, head_cy - head_r * 1.1, cx + head_r * 1.08, head_cy + head_r * 0.3),
        180,
        360,
        fill=(36, 42, 38),
    )
    # Очки-консервы с жёлтыми линзами.
    goggle_y = head_cy - head_r * 0.05
    goggle_h = head_r * 0.5
    strap_col = (20, 24, 22)
    draw.line((cx - head_r * 1.05, goggle_y, cx + head_r * 1.05, goggle_y), fill=strap_col, width=max(2, size // 80))
    for sign in (-1, 1):
        lx = cx + sign * head_r * 0.42
        _rounded(
            draw,
            (lx - head_r * 0.32, goggle_y - goggle_h / 2, lx + head_r * 0.32, goggle_y + goggle_h / 2),
            radius=int(head_r * 0.18),
            fill=(224, 190, 40),
            outline=(30, 26, 10),
            width=max(1, size // 150),
        )
    # Антенна рации на плече.
    draw.line((cx + size * 0.30, shoulders_y, cx + size * 0.34, shoulders_y - size * 0.16), fill=(20, 22, 20), width=max(1, size // 140))

    return img


def _draw_soldier(size: int) -> Image.Image:
    """Военный: оливковая форма, каска с креплением, разгрузка."""
    img = _vignette_background(size, (44, 50, 34), (16, 18, 12))
    draw = ImageDraw.Draw(img)
    cx = size * 0.5

    shoulders_y = size * 0.53
    torso = [
        (cx - size * 0.33, size * 1.02),
        (cx - size * 0.31, shoulders_y + size * 0.04),
        (cx - size * 0.15, shoulders_y - size * 0.05),
        (cx + size * 0.15, shoulders_y - size * 0.05),
        (cx + size * 0.31, shoulders_y + size * 0.04),
        (cx + size * 0.33, size * 1.02),
    ]
    draw.polygon(torso, fill=(78, 86, 56))
    draw.polygon(torso, outline=(40, 46, 28))
    # Разгрузка (плашки крест-накрест).
    draw.line((cx - size * 0.2, shoulders_y + size * 0.05, cx + size * 0.08, size * 0.95), fill=(50, 56, 36), width=max(3, size // 30))
    draw.line((cx + size * 0.2, shoulders_y + size * 0.05, cx - size * 0.08, size * 0.95), fill=(50, 56, 36), width=max(3, size // 30))
    for dy in (0.70, 0.84):
        w = size * 0.14
        h = size * 0.07
        _rounded(
            draw,
            (cx - w / 2, size * dy - h / 2, cx + w / 2, size * dy + h / 2),
            radius=max(2, size // 45),
            fill=(58, 64, 40),
            outline=(30, 34, 20),
        )

    # Голова + шлем.
    head_r = size * 0.20
    head_cy = size * 0.31
    draw.ellipse(
        (cx - head_r * 0.7, head_cy - head_r * 0.4, cx + head_r * 0.7, head_cy + head_r * 0.95),
        fill=(198, 160, 124),
        outline=(120, 92, 66),
        width=max(1, size // 130),
    )
    # Каска-полусфера + козырёк + ремешок.
    draw.pieslice(
        (cx - head_r * 1.12, head_cy - head_r * 1.15, cx + head_r * 1.12, head_cy + head_r * 0.25),
        180,
        360,
        fill=(70, 78, 48),
        outline=(34, 38, 22),
        width=max(1, size // 130),
    )
    draw.rectangle(
        (cx - head_r * 1.1, head_cy - head_r * 0.05, cx + head_r * 1.1, head_cy + head_r * 0.12),
        fill=(70, 78, 48),
        outline=(34, 38, 22),
    )
    draw.line(
        (cx - head_r * 0.55, head_cy + head_r * 0.55, cx - head_r * 0.4, head_cy + head_r * 0.95),
        fill=(34, 38, 22),
        width=max(1, size // 150),
    )
    draw.line(
        (cx + head_r * 0.55, head_cy + head_r * 0.55, cx + head_r * 0.4, head_cy + head_r * 0.95),
        fill=(34, 38, 22),
        width=max(1, size // 150),
    )
    # NVG-крепление спереди каски.
    mount_cx = cx
    mount_cy = head_cy - head_r * 0.15
    draw.ellipse(
        (mount_cx - head_r * 0.14, mount_cy - head_r * 0.14, mount_cx + head_r * 0.14, mount_cy + head_r * 0.14),
        fill=(20, 22, 18),
        outline=(60, 64, 40),
    )
    # Глаза.
    eye_y = head_cy + head_r * 0.35
    draw.ellipse((cx - head_r * 0.4, eye_y - 2, cx - head_r * 0.18, eye_y + 4), fill=(30, 24, 18))
    draw.ellipse((cx + head_r * 0.18, eye_y - 2, cx + head_r * 0.4, eye_y + 4), fill=(30, 24, 18))

    return img


DRAWERS = {
    "bandit": _draw_bandit,
    "mercenary": _draw_mercenary,
    "soldier": _draw_soldier,
}


def _make(stem: str, drawer) -> None:
    hi = BASE * SS
    img = drawer(hi)
    img = img.filter(ImageFilter.GaussianBlur(SS * 0.3))

    grid_img = img.resize((MISSION_NPC_GRID_SIZE, MISSION_NPC_GRID_SIZE), Image.Resampling.LANCZOS)
    card_img = img.resize((MISSION_NPC_CARD_SIZE, MISSION_NPC_CARD_SIZE), Image.Resampling.LANCZOS)

    NPCS_GRID_DIR.mkdir(parents=True, exist_ok=True)
    NPCS_CARD_DIR.mkdir(parents=True, exist_ok=True)
    grid_img.convert("RGBA").save(NPCS_GRID_DIR / f"{stem}.png", format="PNG", optimize=True)
    card_img.convert("RGBA").save(NPCS_CARD_DIR / f"{stem}.png", format="PNG", optimize=True)
    print(f"  {stem}: grid {MISSION_NPC_GRID_SIZE}x{MISSION_NPC_GRID_SIZE}, card {MISSION_NPC_CARD_SIZE}x{MISSION_NPC_CARD_SIZE}")


def main() -> None:
    print("Generating procedural NPC sprites:")
    for stem, drawer in DRAWERS.items():
        _make(stem, drawer)
    print(f"Done ({len(DRAWERS)} sprites).")


if __name__ == "__main__":
    main()
