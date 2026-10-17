from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw

from app.skins import skin_tier_for_rating
from app.storage import Character


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AVATAR_DIR = PROJECT_ROOT / "assets" / "avatars"
FACTION_AVATAR_DIR = AVATAR_DIR / "factions"
# Можно положить исходную папку «скины» сюда — бот тоже её просканирует.
SKINS_DROP_DIR = AVATAR_DIR / "скины"

# Ключи папок/файлов для каждой группировки (русские и латинские варианты).
FACTION_SLUGS: dict[str, tuple[str, ...]] = {
    "Долг": ("долг", "dolg", "duty"),
    "Свобода": ("свобода", "svoboda", "freedom"),
    "Нейтралы": ("нейтралы", "нейтрал", "neutraly", "neutrals", "neutral"),
    "Бандиты": ("бандиты", "бандит", "bandity", "bandits", "bandit"),
    "Монолит": ("монолит", "monolith", "монолита"),
}

FACTION_AVATAR_SIZE = 250
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_TIER_IN_NAME = re.compile(r"(\d+)$")


def _tier(rating_points: int) -> int:
    return skin_tier_for_rating(rating_points)


def _palette(tier: int) -> dict[str, tuple[int, int, int]]:
    palettes = {
        1: {
            "suit": (98, 105, 92),
            "armor": (110, 116, 106),
            "belt": (98, 70, 50),
            "mask": (112, 118, 110),
            "accent": (122, 126, 116),
        },
        2: {
            "suit": (85, 95, 78),
            "armor": (103, 112, 95),
            "belt": (92, 66, 46),
            "mask": (106, 112, 101),
            "accent": (122, 138, 112),
        },
        3: {
            "suit": (75, 82, 86),
            "armor": (93, 99, 112),
            "belt": (82, 60, 48),
            "mask": (98, 104, 117),
            "accent": (110, 128, 151),
        },
        4: {
            "suit": (70, 72, 78),
            "armor": (98, 92, 74),
            "belt": (102, 82, 52),
            "mask": (122, 116, 97),
            "accent": (171, 149, 88),
        },
    }
    return palettes[tier]


def _normalize_token(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", (value or "").strip().casefold())


def _faction_slugs(faction: str | None) -> tuple[str, ...]:
    if not faction:
        return ()
    known = FACTION_SLUGS.get(faction)
    if known:
        return known
    token = _normalize_token(faction)
    return (token,) if token else ()


def _stem_matches_faction_tier(stem: str, slugs: tuple[str, ...], tier: int) -> bool:
    """Долг1 / dolg_1 / duty-t1 / 1 — совпадение с группировкой и этапом звания."""
    token = _normalize_token(stem)
    if not token:
        return False
    if token == str(tier) or token in {f"t{tier}", f"tier{tier}"}:
        return True
    for slug in slugs:
        slug_token = _normalize_token(slug)
        if not slug_token:
            continue
        if token == f"{slug_token}{tier}":
            return True
        if token.startswith(slug_token):
            suffix = token[len(slug_token) :]
            if suffix in {str(tier), f"t{tier}", f"tier{tier}"}:
                return True
            match = _TIER_IN_NAME.search(token)
            if match and int(match.group(1)) == tier and token.startswith(slug_token):
                return True
    return False


def _iter_image_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    files: list[Path] = []
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES:
            files.append(path)
    return files


def _scan_named_faction_skins(tier: int, faction: str | None) -> list[Path]:
    """Ищет Долг1.png и т.п. в assets/avatars, factions/* и assets/avatars/скины."""
    slugs = _faction_slugs(faction)
    if not slugs:
        return []

    search_roots: list[Path] = [AVATAR_DIR, FACTION_AVATAR_DIR, SKINS_DROP_DIR]
    for slug in slugs:
        search_roots.extend(
            [
                FACTION_AVATAR_DIR / slug,
                SKINS_DROP_DIR / slug,
                AVATAR_DIR / slug,
            ]
        )
    if faction:
        name = faction.strip()
        search_roots.extend(
            [
                FACTION_AVATAR_DIR / name,
                SKINS_DROP_DIR / name,
                AVATAR_DIR / name,
            ]
        )

    found: list[Path] = []
    seen: set[Path] = set()
    for root in search_roots:
        for path in _iter_image_files(root):
            resolved = path.resolve()
            if resolved in seen:
                continue
            if _stem_matches_faction_tier(path.stem, slugs, tier):
                seen.add(resolved)
                found.append(path)
        # Один уровень вложенности: скины/Долг/*.png
        if root.is_dir():
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                child_token = _normalize_token(child.name)
                if child_token not in {_normalize_token(s) for s in slugs}:
                    continue
                for path in _iter_image_files(child):
                    resolved = path.resolve()
                    if resolved in seen:
                        continue
                    if _stem_matches_faction_tier(path.stem, slugs, tier):
                        seen.add(resolved)
                        found.append(path)
    return found


def _avatar_candidates(tier: int, faction: str | None = None) -> tuple[Path, ...]:
    """Порядок поиска: скин группировки+звания, затем общий тир, затем default."""
    candidates: list[Path] = []
    slugs = _faction_slugs(faction)
    faction_name = (faction or "").strip()

    for slug in slugs:
        for base in (FACTION_AVATAR_DIR / slug, SKINS_DROP_DIR / slug, AVATAR_DIR / slug):
            candidates.extend(
                [
                    base / f"{tier}.png",
                    base / f"t{tier}.png",
                    base / f"stalker_t{tier}.png",
                    base / f"{slug}{tier}.png",
                    base / f"{slug}_{tier}.png",
                    base / f"{slug}-t{tier}.png",
                ]
            )
        # Файлы прямо в assets/avatars: Долг1.png / dolg1.png / dolg_1.png
        candidates.extend(
            [
                AVATAR_DIR / f"{slug}{tier}.png",
                AVATAR_DIR / f"{slug}_{tier}.png",
                AVATAR_DIR / f"{slug}-t{tier}.png",
                SKINS_DROP_DIR / f"{slug}{tier}.png",
                SKINS_DROP_DIR / f"{slug}_{tier}.png",
            ]
        )

    if faction_name:
        candidates.extend(
            [
                AVATAR_DIR / f"{faction_name}{tier}.png",
                AVATAR_DIR / f"{faction_name}_{tier}.png",
                SKINS_DROP_DIR / f"{faction_name}{tier}.png",
                SKINS_DROP_DIR / f"{faction_name}_{tier}.png",
                FACTION_AVATAR_DIR / faction_name / f"{tier}.png",
                FACTION_AVATAR_DIR / faction_name / f"{faction_name}{tier}.png",
                SKINS_DROP_DIR / faction_name / f"{tier}.png",
                SKINS_DROP_DIR / faction_name / f"{faction_name}{tier}.png",
            ]
        )

    # Гибкий скан по имени файла (Долг1, Свобода2, ...).
    candidates.extend(_scan_named_faction_skins(tier, faction))

    candidates.extend(
        [
            AVATAR_DIR / f"stalker_t{tier}.png",
            AVATAR_DIR / f"stalker_{tier}.png",
            AVATAR_DIR / f"tier_{tier}.png",
            AVATAR_DIR / "stalker_default.png",
        ]
    )

    # Убираем дубликаты, сохраняя порядок.
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        key = Path(str(path))
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return tuple(unique)


def _square_avatar_image(source: Image.Image, size: int = FACTION_AVATAR_SIZE) -> Image.Image:
    """Привести скин группировки к квадрату size×size (1:1)."""
    img = source.convert("RGBA")
    width, height = img.size
    if width <= 0 or height <= 0:
        return Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if width != height:
        side = min(width, height)
        left = (width - side) // 2
        top = (height - side) // 2
        img = img.crop((left, top, left + side, top + side))
    if img.size != (size, size):
        img = img.resize((size, size), Image.Resampling.LANCZOS)
    return img


def _fit_avatar_image(source: Image.Image, width: int, height: int) -> Image.Image:
    """Обрезать/масштабировать в точный слот (cover): один размер, без полей.

    Горизонтально — по центру, вертикально — прижато к низу (ноги у края),
    как было до center-crop.
    """
    img = source.convert("RGBA")
    if img.width <= 0 or img.height <= 0:
        return Image.new("RGBA", (width, height), (0, 0, 0, 0))

    scale = max(width / img.width, height / img.height)
    new_w = max(1, int(round(img.width * scale)))
    new_h = max(1, int(round(img.height * scale)))
    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    left = max(0, (new_w - width) // 2)
    top = max(0, new_h - height)
    right = left + width
    bottom = top + height
    cropped = resized.crop((left, top, right, bottom))
    if cropped.size != (width, height):
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.paste(cropped, (0, 0), cropped)
        return canvas
    return cropped


def _resolve_existing_image(candidate: Path) -> Path | None:
    if candidate.exists() and candidate.suffix.lower() in _IMAGE_SUFFIXES:
        return candidate
    if candidate.suffix.lower() == ".png":
        for alt_suffix in (".jpg", ".jpeg", ".webp"):
            alt = candidate.with_suffix(alt_suffix)
            if alt.exists():
                return alt
    return None


def _load_avatar_asset(
    tier: int,
    width: int,
    height: int,
    faction: str | None = None,
) -> Image.Image | None:
    for candidate in _avatar_candidates(tier, faction=faction):
        resolved = _resolve_existing_image(candidate)
        if resolved is None:
            continue
        try:
            source = Image.open(resolved).convert("RGBA")
        except OSError:
            continue
        if faction:
            return _square_avatar_image(source)
        return _fit_avatar_image(source, width, height)
    return None


def _render_stalker_avatar_fallback(
    character: Character,
    *,
    rating_points: int = 0,
    width: int = 260,
    height: int = 360,
) -> Image.Image:
    tier = _tier(rating_points)
    p = _palette(tier)

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    center_x = width // 2

    # Legs / pants
    draw.rounded_rectangle((center_x - 64, 218, center_x - 12, 350), radius=18, fill=p["suit"])
    draw.rounded_rectangle((center_x + 12, 218, center_x + 64, 350), radius=18, fill=p["suit"])
    # Boots
    draw.rounded_rectangle((center_x - 68, 336, center_x - 10, 358), radius=8, fill=(45, 45, 48))
    draw.rounded_rectangle((center_x + 10, 336, center_x + 68, 358), radius=8, fill=(45, 45, 48))

    # Torso suit
    draw.rounded_rectangle((center_x - 80, 120, center_x + 80, 242), radius=26, fill=p["suit"])
    # Tactical vest
    draw.rounded_rectangle((center_x - 72, 116, center_x + 72, 208), radius=16, fill=p["armor"], outline=(210, 210, 210), width=2)
    for i in range(4):
        y = 128 + i * 18
        draw.rounded_rectangle((center_x - 58, y, center_x + 58, y + 10), radius=4, fill=p["accent"])

    # Belt
    draw.rounded_rectangle((center_x - 84, 208, center_x + 84, 232), radius=8, fill=p["belt"])
    draw.rounded_rectangle((center_x - 10, 211, center_x + 10, 229), radius=4, fill=(140, 130, 116))

    # Arms
    draw.rounded_rectangle((center_x - 122, 138, center_x - 76, 260), radius=18, fill=p["suit"])
    draw.rounded_rectangle((center_x + 76, 138, center_x + 122, 260), radius=18, fill=p["suit"])
    draw.ellipse((center_x - 125, 246, center_x - 96, 274), fill=(175, 148, 130))
    draw.ellipse((center_x + 96, 246, center_x + 125, 274), fill=(175, 148, 130))

    # Gas mask / head
    draw.ellipse((center_x - 42, 42, center_x + 42, 126), fill=p["mask"], outline=(212, 212, 212), width=2)
    draw.ellipse((center_x - 32, 56, center_x - 8, 80), fill=(44, 44, 44))
    draw.ellipse((center_x + 8, 56, center_x + 32, 80), fill=(44, 44, 44))
    draw.ellipse((center_x - 18, 78, center_x + 18, 116), fill=(58, 58, 60), outline=(195, 195, 195), width=2)

    # Shoulder armor on high tiers
    if tier >= 3:
        draw.polygon(
            [(center_x - 94, 122), (center_x - 62, 122), (center_x - 74, 156), (center_x - 102, 150)],
            fill=p["armor"],
        )
        draw.polygon(
            [(center_x + 94, 122), (center_x + 62, 122), (center_x + 74, 156), (center_x + 102, 150)],
            fill=p["armor"],
        )
    if tier == 4:
        draw.ellipse((center_x - 8, 88, center_x + 8, 104), fill=(240, 220, 118))

    return image


def render_avatar(
    character: Character,
    *,
    rating_points: int = 0,
    width: int = 260,
    height: int = 360,
) -> Image.Image:
    tier = _tier(rating_points)
    asset_avatar = _load_avatar_asset(
        tier,
        width=width,
        height=height,
        faction=character.faction,
    )
    if asset_avatar is not None:
        return asset_avatar
    return _render_stalker_avatar_fallback(
        character,
        rating_points=rating_points,
        width=width,
        height=height,
    )
