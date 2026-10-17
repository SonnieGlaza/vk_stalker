from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkinTheme:
    key: str
    title: str
    min_rating: int
    coat_color: tuple[int, int, int]
    visor_color: tuple[int, int, int]
    accent_color: tuple[int, int, int]


# Пороги рейтинга для «повышения» внешнего вида.
# Новичок: 0–499, Опытный: 500–1999, Ветеран: 2000–4999, Легенда: 5000+
SKINS: tuple[SkinTheme, ...] = (
    SkinTheme(
        key="novice",
        title="Новичок",
        min_rating=0,
        coat_color=(95, 95, 102),
        visor_color=(125, 130, 145),
        accent_color=(130, 115, 85),
    ),
    SkinTheme(
        key="veteran",
        title="Опытный",
        min_rating=500,
        coat_color=(82, 98, 86),
        visor_color=(128, 170, 120),
        accent_color=(105, 130, 85),
    ),
    SkinTheme(
        key="heavy",
        title="Ветеран",
        min_rating=2000,
        coat_color=(78, 84, 95),
        visor_color=(120, 160, 185),
        accent_color=(95, 110, 145),
    ),
    SkinTheme(
        key="legend",
        title="Легенда",
        min_rating=5000,
        coat_color=(75, 76, 92),
        visor_color=(185, 165, 88),
        accent_color=(150, 130, 75),
    ),
)


def resolve_skin(rating_points: int) -> SkinTheme:
    rating = max(0, int(rating_points))
    selected = SKINS[0]
    for skin in SKINS:
        if rating >= skin.min_rating:
            selected = skin
    return selected


def skin_tier_for_rating(rating_points: int) -> int:
    """1–4: уровень визуала для аватара."""
    rating = max(0, int(rating_points))
    tier = 1
    for idx, skin in enumerate(SKINS, start=1):
        if rating >= skin.min_rating:
            tier = idx
    return tier


def next_skin_progress(rating_points: int) -> tuple[SkinTheme, SkinTheme | None, int]:
    """Текущий скин, следующий (или None), сколько рейтинга не хватает."""
    rating = max(0, int(rating_points))
    current = resolve_skin(rating)
    for skin in SKINS:
        if skin.min_rating > rating:
            return current, skin, skin.min_rating - rating
    return current, None, 0
