from __future__ import annotations

import random
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:
    from app.storage import Storage

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MUTANTS_DIR = PROJECT_ROOT / "assets" / "mutants"
MUTANTS_GRID_DIR = MUTANTS_DIR / "grid"
MUTANTS_CARD_DIR = MUTANTS_DIR / "card"
MUTANTS_SOURCE_DIR = MUTANTS_DIR / "source"

# Размер спрайта на клетке миссии (клетка 108px, игрок ~144px).
MISSION_MUTANT_GRID_DIAMETER = 80
MISSION_GIANT_GRID_DIAMETER = 96
MISSION_MUTANT_GRID_SIZE = 88
MISSION_MUTANT_CARD_SIZE = 256

MUTANT_SPRITES: dict[str, str] = {
    "blind_dog": "Слепой пёс",
    "tushkano": "Тушкан",
    "pseudodog": "Псевдособака",
    "bloodsucker": "Кровосос",
    "flesh": "Плоть",
    "controller": "Контролёр",
    "giant": "Псевдогигант",
    "burer": "Бюрер",
    "zombie": "Зомбированный",
}

MUTANT_SPRITE_KEYS: tuple[str, ...] = tuple(MUTANT_SPRITES.keys())

# Нет отдельных PNG — рисуем ближайшим спрайтом.
MUTANT_SPRITE_FALLBACKS: dict[str, str] = {
    "burer": "controller",
    "zombie": "flesh",
}

# Веса спавна: контролёр/бюрер реже обычных тварей.
MUTANT_SPAWN_WEIGHTS: dict[str, int] = {
    "blind_dog": 24,
    "tushkano": 20,
    "pseudodog": 16,
    "bloodsucker": 12,
    "flesh": 12,
    "zombie": 8,
    "controller": 5,
    "burer": 3,
}

CONTROLLER_AURA_DAMAGE = 3


def pick_mutant_kind(*, allow_controller: bool = True) -> str:
    pool = [
        (key, weight)
        for key, weight in MUTANT_SPAWN_WEIGHTS.items()
        if allow_controller or key != "controller"
    ]
    if not pool:
        return "blind_dog"
    keys, weights = zip(*pool)
    return random.choices(list(keys), weights=list(weights), k=1)[0]


def ensure_single_controller(kinds: list[str]) -> list[str]:
    """Не больше одного контролёра в группе мутантов."""
    seen = False
    out: list[str] = []
    for kind in kinds:
        if kind == "controller":
            if seen:
                out.append(pick_mutant_kind(allow_controller=False))
                continue
            seen = True
        out.append(kind)
    return out


def controller_on_field(kinds: list[str] | tuple[str, ...] | None) -> bool:
    return bool(kinds) and "controller" in kinds


def bloodsucker_on_field(kinds: list[str] | tuple[str, ...] | None) -> bool:
    return bool(kinds) and "bloodsucker" in kinds


def mutant_field_warnings(kinds: list[str] | tuple[str, ...] | None) -> list[str]:
    from app.mutant_abilities import mutant_field_ability_warnings

    return mutant_field_ability_warnings(kinds)


def apply_controller_aura_to_hp_map(
    hp: dict[str, int],
    player_ids: list[int],
    enemy_kinds: list[str] | tuple[str, ...] | None,
    *,
    death_causes: dict[str, str] | None = None,
    death_killers: dict[str, str] | None = None,
) -> list[str]:
    """Пассив контролёра: −HP всем живым за ход. Мутирует hp in-place."""
    if not controller_on_field(enemy_kinds):
        return []
    touched = False
    for pid in player_ids:
        key = str(pid)
        cur = int(hp.get(key, 0) or 0)
        if cur <= 0:
            continue
        nxt = max(0, cur - CONTROLLER_AURA_DAMAGE)
        hp[key] = nxt
        touched = True
        if nxt <= 0:
            if death_causes is not None:
                death_causes[key] = "mutant"
            if death_killers is not None:
                death_killers[key] = "Контролёр"
    if not touched:
        return []
    return [f"🧠 Контролёр давит разум: все живые −{CONTROLLER_AURA_DAMAGE} HP."]


def apply_controller_aura_db(
    storage: Storage,
    telegram_ids: list[int],
    enemy_kinds: list[str] | tuple[str, ...] | None,
) -> list[str]:
    """Пассив контролёра для режимов, где HP в БД (квестовые вылазки)."""
    if not controller_on_field(enemy_kinds):
        return []
    touched = False
    for tid in telegram_ids:
        ch = storage.get_character(int(tid), refresh_energy=False)
        if ch is None or int(ch.health) <= 0:
            continue
        storage.change_health(int(tid), -CONTROLLER_AURA_DAMAGE)
        touched = True
    if not touched:
        return []
    return [f"🧠 Контролёр давит разум: −{CONTROLLER_AURA_DAMAGE} HP."]


def _read_sprite_file(directory: Path, name: str) -> bytes | None:
    for ext in (".png", ".jpg", ".jpeg"):
        path = directory / f"{name}{ext}"
        if path.is_file():
            return path.read_bytes()
    return None


@lru_cache(maxsize=32)
def load_mutant_grid_sprite(kind: str) -> bytes | None:
    """PNG 88×88 для отрисовки на поле миссии."""
    resolved = MUTANT_SPRITE_FALLBACKS.get(kind, kind)
    raw = _read_sprite_file(MUTANTS_GRID_DIR, resolved)
    if raw is None:
        raw = _read_sprite_file(MUTANTS_SOURCE_DIR, resolved)
    return raw


@lru_cache(maxsize=32)
def load_mutant_card_sprite(kind: str) -> bytes | None:
    """PNG 256×256 для карточек/PDA (будущие задания)."""
    resolved = MUTANT_SPRITE_FALLBACKS.get(kind, kind)
    raw = _read_sprite_file(MUTANTS_CARD_DIR, resolved)
    if raw is None:
        raw = _read_sprite_file(MUTANTS_SOURCE_DIR, resolved)
    return raw


def mutant_grid_diameter(kind: str, *, default: int = MISSION_MUTANT_GRID_DIAMETER) -> int:
    if kind == "giant":
        return MISSION_GIANT_GRID_DIAMETER
    return default


def special_event_call_photo(event_kind: str) -> bytes | None:
    """Карточка мутанта для объявления особого события в общем чате."""
    if event_kind == "giant":
        return load_mutant_card_sprite("giant")
    return None


def mutant_sprite_image(kind: str, *, card: bool = False) -> Image.Image | None:
    raw = load_mutant_card_sprite(kind) if card else load_mutant_grid_sprite(kind)
    if raw is None:
        return None
    try:
        return Image.open(BytesIO(raw)).convert("RGBA")
    except Exception:
        return None
