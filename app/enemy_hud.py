"""Плашка врагов внизу правой информационной панели: 6 индустриальных слотов."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Sequence

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.artifact_hunt import _paste_circle
from app.mutant_assets import mutant_sprite_image
from app.npc_assets import npc_sprite_image
from app.tactical_render import hostile_kind_to_sprite, load_tactical_font

HUD_SLOTS = 6
HUD_RING = (210, 45, 40)
DEFAULT_HP = 16
# Место под две нижние строки панели («Зачистка Кордона», «Стрелки — ход…»).
HUD_FOOTER_GAP = 56

# Показатель «живучести» на плашке (бой пока one-shot: слот пустеет, когда враг снят).
HP_BY_KIND: dict[str, int] = {
    "blind_dog": 16,
    "tushkano": 10,
    "pseudodog": 22,
    "bloodsucker": 28,
    "flesh": 18,
    "controller": 32,
    "giant": 100,
    "burer": 28,
    "zombie": 20,
    "maloy": 16,
    "bandit": 18,
    "mercenary": 22,
    "soldier": 24,
    "monolith": 28,
    "dark_stalker": 24,
}


@dataclass(frozen=True)
class EnemyHudSlot:
    kind: str
    is_npc: bool
    hp: int = DEFAULT_HP
    max_hp: int = DEFAULT_HP


def default_hp_for_kind(kind: str) -> int:
    return int(HP_BY_KIND.get(str(kind), DEFAULT_HP))


def hud_slots_from_kinds(
    mutant_kinds: Sequence[str] | None = None,
    npc_kinds: Sequence[str] | None = None,
) -> list[EnemyHudSlot]:
    slots: list[EnemyHudSlot] = []
    for kind in mutant_kinds or []:
        hp = default_hp_for_kind(kind)
        slots.append(EnemyHudSlot(str(kind), False, hp, hp))
    for kind in npc_kinds or []:
        hp = default_hp_for_kind(kind)
        slots.append(EnemyHudSlot(str(kind), True, hp, hp))
    return slots


def hud_slots_from_raid(
    hostile_types: Sequence[str] | None,
    hostile_kinds: Sequence[str] | None,
) -> list[EnemyHudSlot]:
    types = list(hostile_types or [])
    kinds = list(hostile_kinds or [])
    slots: list[EnemyHudSlot] = []
    for i, htype in enumerate(types):
        is_npc = str(htype) != "mutant"
        kind = kinds[i] if i < len(kinds) else ("maloy" if is_npc else "blind_dog")
        hp = default_hp_for_kind(kind)
        slots.append(EnemyHudSlot(str(kind), is_npc, hp, hp))
    return slots


def hud_slots_from_units(
    kinds: Sequence[str],
    hp_values: Sequence[int] | None = None,
    max_hp_values: Sequence[int] | None = None,
    *,
    is_npc: bool,
) -> list[EnemyHudSlot]:
    """Слоты с текущим HP юнитов; без данных — полные значения из таблицы."""
    slots: list[EnemyHudSlot] = []
    for i, kind in enumerate(kinds or []):
        full = default_hp_for_kind(str(kind))
        hp = full
        max_hp = full
        if hp_values is not None and i < len(hp_values):
            try:
                hp = max(0, int(hp_values[i]))
            except (TypeError, ValueError):
                hp = full
        if max_hp_values is not None and i < len(max_hp_values):
            try:
                max_hp = max(1, int(max_hp_values[i]))
            except (TypeError, ValueError):
                max_hp = full
        max_hp = max(1, max_hp)
        slots.append(EnemyHudSlot(str(kind), is_npc, min(hp, max_hp), max_hp))
    return slots


def hud_slots_from_mixed_kinds(kinds: Sequence[str] | None) -> list[EnemyHudSlot]:
    slots: list[EnemyHudSlot] = []
    for raw in kinds or []:
        key, is_npc = hostile_kind_to_sprite(str(raw))
        hp = default_hp_for_kind(key)
        slots.append(EnemyHudSlot(key, is_npc, hp, hp))
    return slots


def draw_enemy_hud(
    canvas: Image.Image,
    slots: Sequence[EnemyHudSlot],
    *,
    panel_left: int,
    panel_top: int,
    panel_right: int,
    panel_bottom: int,
    max_slots: int = HUD_SLOTS,
    bottom_gap: int = 8,
    y_top: int | None = None,
) -> None:
    """Рисует плашку на правой панели. y_top — верх плашки; иначе низ панели минус bottom_gap."""
    if not slots:
        return
    panel_w = max(80, panel_right - panel_left)
    panel_h = max(60, panel_bottom - panel_top)
    inset = 8
    inner_w = max(60, panel_w - inset * 2)
    gap = 2
    pad = 4
    slot_w = max(28, min(46, (inner_w - pad * 2 - (max_slots - 1) * gap) // max_slots))
    slot_h = int(slot_w * 1.2)
    if slot_h + 12 > panel_h // 3:
        slot_h = max(30, panel_h // 4)
        slot_w = max(24, int(slot_h / 1.2))
    bar_w = max_slots * slot_w + (max_slots - 1) * gap + pad * 2
    bar_h = slot_h + pad * 2
    x0 = panel_left + (panel_w - bar_w) // 2
    x1 = x0 + bar_w
    if y_top is not None:
        y0 = int(y_top)
        y1 = y0 + bar_h
    else:
        y1 = panel_bottom - max(inset, int(bottom_gap))
        y0 = y1 - bar_h
    if y0 < panel_top + inset:
        y0 = panel_top + inset
        y1 = y0 + bar_h
    if y1 > panel_bottom - inset:
        y1 = panel_bottom - inset
        y0 = y1 - bar_h

    if canvas.mode != "RGBA":
        raise ValueError("enemy HUD expects an RGBA canvas")
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # Тень + корпус плашки.
    draw.rounded_rectangle((x0 + 3, y0 + 4, x1 + 3, y1 + 4), radius=6, fill=(0, 0, 0, 140))
    body = _noise_rect(bar_w, bar_h, seed=17, lo=12, hi=28)
    body = body.filter(ImageFilter.SMOOTH)
    mask = Image.new("L", (bar_w, bar_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, bar_w - 1, bar_h - 1), radius=6, fill=255)
    layer.paste(body, (x0, y0), mask)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=6, outline=(70, 72, 68, 220), width=2)

    shown = list(slots[:max_slots])
    idx_font = load_tactical_font(max(8, slot_w // 5))
    hp_font = load_tactical_font(max(11, slot_w // 3 + 1))
    for i in range(max_slots):
        sx = x0 + pad + i * (slot_w + gap)
        sy = y0 + pad
        slot = shown[i] if i < len(shown) else None
        _draw_slot(layer, draw, sx, sy, slot_w, slot_h, index=i + 1, slot=slot, idx_font=idx_font, hp_font=hp_font)

    canvas.alpha_composite(layer)


def _noise_rect(width: int, height: int, *, seed: int, lo: int, hi: int) -> Image.Image:
    rng = random.Random(seed)
    img = Image.new("RGB", (width, height))
    pix = img.load()
    for y in range(height):
        for x in range(width):
            v = rng.randint(lo, hi)
            pix[x, y] = (v, max(0, v - 3), max(0, v - 8))
    return img.convert("RGBA")


def _slot_polygon(x: int, y: int, w: int, h: int) -> list[tuple[int, int]]:
    tab = max(8, w // 5)
    cut = max(7, w // 6)
    return [
        (x + tab, y),
        (x + w - 1, y),
        (x + w - 1, y + h - cut),
        (x + w - cut, y + h - 1),
        (x, y + h - 1),
        (x, y + tab),
        (x + tab, y + tab),
        (x + tab, y),
    ]


def _draw_slot(
    layer: Image.Image,
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    index: int,
    slot: EnemyHudSlot | None,
    idx_font: ImageFont.ImageFont,
    hp_font: ImageFont.ImageFont,
) -> None:
    poly = _slot_polygon(x, y, w, h)
    draw.polygon(poly, fill=(58, 58, 54, 245))
    # Фаска: свет сверху-слева, тень снизу-справа.
    highlight = [
        (poly[0][0], poly[0][1] + 1),
        (poly[1][0] - 1, poly[1][1] + 1),
        (poly[1][0] - 3, poly[1][1] + 4),
        (poly[0][0] + 2, poly[0][1] + 4),
    ]
    draw.line(poly[0:2], fill=(150, 150, 145, 255), width=2)
    draw.line([poly[4], poly[5], poly[6], poly[0]], fill=(150, 150, 145, 220), width=2)
    draw.line(poly[1:5], fill=(22, 22, 20, 255), width=2)
    draw.polygon(poly, outline=(18, 18, 16, 255))
    inset = 3
    inner = _slot_polygon(x + inset, y + inset, w - inset * 2, h - inset * 2 - 1)
    draw.polygon(inner, outline=(28, 28, 26, 255))
    draw.line(highlight, fill=(170, 170, 164, 80), width=1)

    pad = max(3, w // 10)
    view_x = x + pad
    view_y = y + pad + 2
    view_s = w - pad * 2
    bar_top = view_y + view_s + 3
    bar_h = max(6, h - (bar_top - y) - 6)
    if bar_top + bar_h > y + h - 5:
        extra = (bar_top + bar_h) - (y + h - 5)
        view_s = max(18, view_s - extra)
        bar_top = view_y + view_s + 3
        bar_h = max(5, y + h - 6 - bar_top)

    noise = _noise_rect(view_s, view_s, seed=40 + index * 13, lo=70, hi=118)
    noise = noise.filter(ImageFilter.SMOOTH_MORE)
    layer.paste(noise, (view_x, view_y))
    draw.rectangle((view_x, view_y, view_x + view_s - 1, view_y + view_s - 1), outline=(20, 20, 18, 220))

    # Полоска HP под экраном.
    draw.rectangle((view_x, bar_top, view_x + view_s - 1, bar_top + bar_h - 1), fill=(18, 16, 16, 255))
    segments = 9
    gap = 1
    usable = view_s - 2
    seg_w = max(2, (usable - (segments - 1) * gap) // segments)
    filled_n = 0
    if slot is not None:
        ratio = max(0.0, min(1.0, slot.hp / max(1, slot.max_hp)))
        filled_n = max(1, int(round(segments * ratio))) if slot.hp > 0 else 0
        portrait = _slot_portrait(slot, view_s - 4)
        cx = view_x + view_s // 2
        cy = view_y + view_s // 2
        _paste_circle(layer, portrait, cx, cy, view_s - 6, ring_color=HUD_RING, ring_width=max(2, view_s // 22))
        hp_text = str(int(slot.hp))
        bbox = hp_font.getbbox(hp_text)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx = cx - tw // 2
        ty = cy - th // 2 - 1
        _outlined_text(draw, (tx, ty), hp_text, font=hp_font, fill=(220, 40, 36, 255), outline=(20, 8, 8, 255))

    for s in range(segments):
        sx = view_x + 1 + s * (seg_w + gap)
        color = (196, 38, 32, 255) if s < filled_n else (22, 20, 20, 255)
        draw.rectangle((sx, bar_top + 1, sx + seg_w - 1, bar_top + bar_h - 2), fill=color)

    tab = max(8, w // 5)
    draw.text((x + 3, y + 1), str(index), fill=(230, 230, 225, 255), font=idx_font)
    # Уголок-засечка вкладки.
    draw.rectangle((x + 1, y + 1, x + tab - 1, y + tab - 1), outline=(90, 90, 86, 180))


def _outlined_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    *,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    outline: tuple[int, int, int, int],
) -> None:
    x, y = xy
    for dx in (-2, -1, 0, 1, 2):
        for dy in (-2, -1, 0, 1, 2):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


def _slot_portrait(slot: EnemyHudSlot, size: int) -> Image.Image:
    sprite = npc_sprite_image(slot.kind) if slot.is_npc else mutant_sprite_image(slot.kind)
    if sprite is not None:
        return sprite.convert("RGBA")
    return _fallback_portrait(slot.kind, slot.is_npc, max(32, size * 2))


def _fallback_portrait(kind: str, is_npc: bool, size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = size // 10
    if is_npc:
        coat = {
            "maloy": (70, 72, 58),
            "bandit": (52, 42, 36),
            "mercenary": (48, 52, 48),
            "soldier": (46, 50, 42),
        }.get(kind, (60, 58, 50))
        d.ellipse((m, m, size - m, size - m), fill=(38, 36, 32))
        d.ellipse((size // 3, size // 5, size * 2 // 3, size // 2), fill=(78, 62, 50))
        d.rectangle((size // 4, size // 2, size * 3 // 4, size - m), fill=coat)
        d.ellipse((size // 2 - 4, size // 3, size // 2 + 2, size // 3 + 5), fill=(20, 16, 14))
        d.ellipse((size // 2 + 6, size // 3, size // 2 + 12, size // 3 + 5), fill=(20, 16, 14))
        if kind in {"mercenary", "soldier"}:
            d.rectangle((size // 4, size // 6, size * 3 // 4, size // 3), fill=(36, 40, 34))
        return img

    fur = {
        "blind_dog": (92, 78, 58),
        "pseudodog": (70, 58, 48),
        "tushkano": (110, 95, 70),
        "bloodsucker": (72, 78, 62),
        "flesh": (150, 110, 100),
        "controller": (48, 42, 70),
    }.get(kind, (88, 74, 54))
    d.ellipse((m, m + 4, size - m, size - m), fill=fur)
    # Уши.
    d.polygon([(size // 5, size // 3), (size // 8, m), (size // 3, size // 4)], fill=(fur[0] - 20, fur[1] - 20, fur[2] - 16))
    d.polygon(
        [(size * 4 // 5, size // 3), (size * 7 // 8, m), (size * 2 // 3, size // 4)],
        fill=(fur[0] - 20, fur[1] - 20, fur[2] - 16),
    )
    # Морда.
    d.ellipse((size // 3, size // 2, size * 2 // 3, size * 4 // 5), fill=(fur[0] + 18, fur[1] + 12, fur[2] + 8))
    d.ellipse((size // 2 - 6, size * 3 // 5, size // 2 + 6, size * 3 // 5 + 8), fill=(30, 22, 18))
    if kind == "blind_dog":
        d.ellipse((size // 3, size // 3, size // 3 + 8, size // 3 + 6), fill=(210, 205, 190))
        d.ellipse((size * 3 // 5, size // 3, size * 3 // 5 + 8, size // 3 + 6), fill=(210, 205, 190))
    elif kind == "bloodsucker":
        d.ellipse((m + 4, m + 2, size - m - 4, size * 3 // 5), fill=(80, 90, 70))
        d.ellipse((size // 3, size // 3, size // 3 + 6, size // 3 + 10), fill=(40, 20, 20))
        d.ellipse((size * 3 // 5, size // 3, size * 3 // 5 + 6, size // 3 + 10), fill=(40, 20, 20))
    elif kind == "controller":
        d.ellipse((m, m // 2, size - m, size - m), fill=(48, 42, 70))
        d.ellipse((size * 3 // 8, size * 3 // 8, size * 5 // 8, size * 5 // 8), fill=(20, 16, 28))
        d.ellipse((size * 42 // 100, size * 45 // 100, size * 48 // 100, size * 52 // 100), fill=(180, 80, 255))
        d.ellipse((size * 55 // 100, size * 45 // 100, size * 61 // 100, size * 52 // 100), fill=(180, 80, 255))
    else:
        d.ellipse((size // 3, size // 3, size // 3 + 7, size // 3 + 6), fill=(20, 16, 12))
        d.ellipse((size * 3 // 5, size // 3, size * 3 // 5 + 7, size // 3 + 6), fill=(20, 16, 12))
    return img
