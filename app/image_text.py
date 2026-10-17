"""Цветные эмодзи на картинках: Noto Color Emoji (Pillow рисует его только в размере 109)."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_EMOJI_FONT = PROJECT_ROOT / "assets" / "fonts" / "NotoColorEmoji.ttf"
SYSTEM_EMOJI_FONTS = (
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/noto-color-emoji/NotoColorEmoji.ttf",
)

_COLOR_EMOJI_STRIKE = 109
_PATCHED = False
_orig_text = ImageDraw.ImageDraw.text
_orig_textlength = ImageDraw.ImageDraw.textlength
_orig_textbbox = ImageDraw.ImageDraw.textbbox


def _is_regional_indicator(code: int) -> bool:
    return 0x1F1E6 <= code <= 0x1F1FF


def _is_skin_tone(code: int) -> bool:
    return 0x1F3FB <= code <= 0x1F3FF


def _is_vs(code: int) -> bool:
    return code in {0xFE0E, 0xFE0F}


def _is_tag(code: int) -> bool:
    return 0xE0020 <= code <= 0xE007F


def _is_emoji_base(code: int) -> bool:
    return (
        0x1F000 <= code <= 0x1FAFF
        or 0x2600 <= code <= 0x27BF
        or 0x2300 <= code <= 0x23FF
        or 0x2B05 <= code <= 0x2B07
        or 0x2B1B <= code <= 0x2B1C
        or 0x2B50 == code
        or 0x2B55 == code
        or 0x2934 <= code <= 0x2935
        or 0x25AA <= code <= 0x25AB
        or code in {0x25B6, 0x25C0}
        or 0x25FB <= code <= 0x25FE
        or code in {0x00A9, 0x00AE, 0x2122, 0x2139, 0x24C2, 0x3030, 0x303D, 0x3297, 0x3299}
        or _is_regional_indicator(code)
    )


def _next_emoji_len(text: str, index: int) -> int:
    n = len(text)
    if index >= n:
        return 0
    code = ord(text[index])
    if _is_regional_indicator(code) and index + 1 < n and _is_regional_indicator(ord(text[index + 1])):
        return 2
    if text[index] in "#*0123456789" and index + 1 < n:
        nxt = ord(text[index + 1])
        if nxt == 0x20E3:
            return 2
        if nxt == 0xFE0F and index + 2 < n and ord(text[index + 2]) == 0x20E3:
            return 3
    if not _is_emoji_base(code):
        return 0
    cursor = index + 1
    while cursor < n:
        nxt = ord(text[cursor])
        if _is_vs(nxt) or _is_skin_tone(nxt) or nxt == 0x20E3 or _is_tag(nxt):
            cursor += 1
            continue
        if nxt == 0x200D and cursor + 1 < n:
            follow = _next_emoji_len(text, cursor + 1)
            if follow:
                cursor += 1 + follow
                continue
        break
    return cursor - index


def iter_text_runs(text: str) -> list[tuple[bool, str]]:
    runs: list[tuple[bool, str]] = []
    i = 0
    n = len(text)
    while i < n:
        emoji_len = _next_emoji_len(text, i)
        if emoji_len:
            runs.append((True, text[i : i + emoji_len]))
            i += emoji_len
            continue
        j = i + 1
        while j < n and not _next_emoji_len(text, j):
            j += 1
        runs.append((False, text[i:j]))
        i = j
    return runs


def contains_emoji(text: str) -> bool:
    return any(is_emoji for is_emoji, _chunk in iter_text_runs(text or ""))


@lru_cache(maxsize=1)
def _emoji_font_path() -> str | None:
    if LOCAL_EMOJI_FONT.exists():
        return str(LOCAL_EMOJI_FONT)
    for path in SYSTEM_EMOJI_FONTS:
        if Path(path).exists():
            return path
    return None


@lru_cache(maxsize=1)
def _load_color_emoji_font() -> ImageFont.FreeTypeFont | None:
    path = _emoji_font_path()
    if not path:
        logger.warning("Noto Color Emoji font not found; emoji on images will stay as squares")
        return None
    try:
        return ImageFont.truetype(path, size=_COLOR_EMOJI_STRIKE)
    except OSError:
        logger.exception("Failed to load color emoji font from %s", path)
        return None


def _font_px(font: ImageFont.ImageFont | None) -> int:
    size = getattr(font, "size", None)
    if size:
        return max(10, int(size))
    if font is None:
        return 16
    try:
        bbox = font.getbbox("Hg")
    except Exception:
        return 16
    return max(10, int(bbox[3] - bbox[1]) if bbox else 16)


@lru_cache(maxsize=256)
def render_emoji_glyph(emoji: str, size: int) -> Image.Image | None:
    font = _load_color_emoji_font()
    if font is None or not emoji:
        return None
    probe = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)
    try:
        bbox = _orig_textbbox(draw, (0, 0), emoji, font=font, embedded_color=True)
    except Exception:
        return None
    width = max(1, int(bbox[2] - bbox[0]))
    height = max(1, int(bbox[3] - bbox[1]))
    glyph = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    try:
        _orig_text(
            ImageDraw.Draw(glyph),
            (-int(bbox[0]), -int(bbox[1])),
            emoji,
            font=font,
            embedded_color=True,
        )
    except Exception:
        return None
    target = max(10, int(size))
    return glyph.resize((target, target), Image.Resampling.LANCZOS)


def _paste_glyph(draw: ImageDraw.ImageDraw, xy: tuple[int, int], glyph: Image.Image) -> None:
    image = getattr(draw, "_image", None)
    if image is None:
        return
    x, y = int(xy[0]), int(xy[1])
    if glyph.mode != "RGBA":
        glyph = glyph.convert("RGBA")
    image.paste(glyph, (x, y), glyph)


def _apply_anchor(
    x: float,
    y: float,
    width: float,
    height: float,
    anchor: str | None,
) -> tuple[float, float]:
    if not anchor:
        return x, y
    ax = anchor[0] if len(anchor) >= 1 else "l"
    ay = anchor[1] if len(anchor) >= 2 else "t"
    if ax == "m":
        x -= width / 2
    elif ax == "r":
        x -= width
    if ay in {"m", "s"}:
        y -= height / 2
    elif ay in {"b", "d"}:
        y -= height
    return x, y


def _measure_mixed(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont | None) -> tuple[float, float]:
    size = _font_px(font)
    width = 0.0
    height = float(size)
    for is_emoji, chunk in iter_text_runs(text):
        if is_emoji:
            glyph = render_emoji_glyph(chunk, size)
            if glyph is None:
                bbox = _orig_textbbox(draw, (0, 0), chunk, font=font)
                width += float(bbox[2] - bbox[0])
                height = max(height, float(bbox[3] - bbox[1]))
            else:
                width += glyph.size[0] + 1
                height = max(height, float(glyph.size[1]))
            continue
        bbox = _orig_textbbox(draw, (0, 0), chunk, font=font)
        width += float(bbox[2] - bbox[0])
        height = max(height, float(bbox[3] - bbox[1]))
    return width, height


def _draw_mixed(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont | None,
    fill,
    kwargs: dict,
) -> None:
    width, height = _measure_mixed(draw, text, font)
    x, y = _apply_anchor(float(xy[0]), float(xy[1]), width, height, kwargs.get("anchor"))
    size = _font_px(font)
    text_kwargs = {key: value for key, value in kwargs.items() if key != "anchor"}
    for is_emoji, chunk in iter_text_runs(text):
        if is_emoji:
            glyph = render_emoji_glyph(chunk, size)
            if glyph is None:
                _orig_text(draw, (x, y), chunk, font=font, fill=fill, **text_kwargs)
                x += float(_orig_textlength(draw, chunk, font=font))
                continue
            gy = y + max(0.0, (height - glyph.size[1]) / 2)
            _paste_glyph(draw, (int(round(x)), int(round(gy))), glyph)
            x += glyph.size[0] + 1
            continue
        _orig_text(draw, (x, y), chunk, font=font, fill=fill, **text_kwargs)
        x += float(_orig_textlength(draw, chunk, font=font))


def _patched_text(self: ImageDraw.ImageDraw, xy, text, fill=None, font=None, *args, **kwargs):
    raw = "" if text is None else str(text)
    if args:
        return _orig_text(self, xy, raw, fill, font, *args, **kwargs)
    if not raw or not contains_emoji(raw) or _load_color_emoji_font() is None:
        return _orig_text(self, xy, raw, fill=fill, font=font, **kwargs)
    _draw_mixed(self, xy, raw, font, fill, kwargs)
    return None


def _patched_textlength(self: ImageDraw.ImageDraw, text, font=None, *args, **kwargs):
    raw = "" if text is None else str(text)
    if not raw or not contains_emoji(raw) or _load_color_emoji_font() is None:
        return _orig_textlength(self, raw, font=font, *args, **kwargs)
    width, _height = _measure_mixed(self, raw, font)
    return width


def _patched_textbbox(self: ImageDraw.ImageDraw, xy, text, font=None, *args, **kwargs):
    raw = "" if text is None else str(text)
    if not raw or not contains_emoji(raw) or _load_color_emoji_font() is None:
        return _orig_textbbox(self, xy, raw, font=font, *args, **kwargs)
    width, height = _measure_mixed(self, raw, font)
    x, y = _apply_anchor(float(xy[0]), float(xy[1]), width, height, kwargs.get("anchor"))
    return (int(x), int(y), int(x + width), int(y + height))


def enable_emoji_text() -> None:
    """Подменяет ImageDraw.text/textlength/textbbox, чтобы эмодзи не были квадратами."""
    global _PATCHED
    if _PATCHED:
        return
    ImageDraw.ImageDraw.text = _patched_text  # type: ignore[method-assign]
    ImageDraw.ImageDraw.textlength = _patched_textlength  # type: ignore[method-assign]
    ImageDraw.ImageDraw.textbbox = _patched_textbbox  # type: ignore[method-assign]
    _PATCHED = True


enable_emoji_text()
