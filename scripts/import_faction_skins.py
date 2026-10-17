#!/usr/bin/env python3
"""Импорт скинов группировок из папки вроде «скины» на рабочем столе.

Ожидаемые имена:
  Долг1 / Свобода2 / Бандиты3 / Нейтралы4
  или файлы 1.png..4.png внутри папки группировки.

Цифра = звание:
  1 Новичек, 2 Опытный, 3 Ветеран, 4 Легенда

Скины сохраняются как квадрат **250×250** (1:1).

Пример:
  python3 scripts/import_faction_skins.py "/path/to/скины"
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.avatar_render import (  # noqa: E402
    FACTION_AVATAR_DIR,
    FACTION_AVATAR_SIZE,
    FACTION_SLUGS,
    _normalize_token,
    _square_avatar_image,
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
TIER_RE = re.compile(r"(\d+)$")

# Канонические slug-папки в assets/avatars/factions/
CANONICAL_SLUG: dict[str, str] = {
    "Долг": "dolg",
    "Свобода": "svoboda",
    "Нейтралы": "neutraly",
    "Бандиты": "bandity",
}


def _faction_for_token(token: str) -> str | None:
    normalized = _normalize_token(token)
    for faction, slugs in FACTION_SLUGS.items():
        for slug in slugs:
            if normalized == _normalize_token(slug) or normalized.startswith(_normalize_token(slug)):
                slug_n = _normalize_token(slug)
                if normalized == slug_n:
                    return faction
                suffix = normalized[len(slug_n) :]
                if suffix.isdigit() or suffix.startswith("t") or suffix.startswith("tier"):
                    return faction
    return None


def _tier_from_stem(stem: str, faction: str | None = None) -> int | None:
    token = _normalize_token(stem)
    if token.isdigit():
        value = int(token)
        return value if 1 <= value <= 4 else None
    if faction:
        for slug in FACTION_SLUGS.get(faction, ()):
            slug_n = _normalize_token(slug)
            if token.startswith(slug_n):
                suffix = token[len(slug_n) :]
                if suffix.isdigit():
                    value = int(suffix)
                    return value if 1 <= value <= 4 else None
                if suffix.startswith("t") and suffix[1:].isdigit():
                    value = int(suffix[1:])
                    return value if 1 <= value <= 4 else None
                if suffix.startswith("tier") and suffix[4:].isdigit():
                    value = int(suffix[4:])
                    return value if 1 <= value <= 4 else None
    match = TIER_RE.search(token)
    if match:
        value = int(match.group(1))
        return value if 1 <= value <= 4 else None
    return None


def _iter_source_images(source: Path) -> list[tuple[str, int, Path]]:
    """Возвращает список (faction, tier, path)."""
    results: list[tuple[str, int, Path]] = []

    def consider(path: Path, folder_faction: str | None = None) -> None:
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            return
        faction = folder_faction or _faction_for_token(path.stem)
        if faction is None and folder_faction is None:
            faction = _faction_for_token(path.parent.name)
        if faction is None:
            return
        tier = _tier_from_stem(path.stem, faction=faction)
        if tier is None:
            return
        results.append((faction, tier, path))

    if source.is_file():
        consider(source)
        return results

    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        folder_faction = None
        for parent in path.parents:
            if parent == source:
                break
            maybe = _faction_for_token(parent.name)
            if maybe:
                folder_faction = maybe
                break
        consider(path, folder_faction=folder_faction)
    return results


def import_skins(source: Path, *, dry_run: bool = False) -> int:
    items = _iter_source_images(source)
    if not items:
        print(f"Не найдено скинов в: {source}")
        print("Ожидаются имена вроде Долг1.png, Свобода2.jpg или папки Долг/1.png")
        return 0

    imported = 0
    for faction, tier, path in items:
        slug = CANONICAL_SLUG[faction]
        out_dir = FACTION_AVATAR_DIR / slug
        out_path = out_dir / f"{tier}.png"
        print(f"{path.name} → {faction} / этап {tier} → {out_path.relative_to(PROJECT_ROOT)}")
        if dry_run:
            imported += 1
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            source_img = Image.open(path)
        except OSError as exc:
            print(f"  пропуск (не открылось): {exc}")
            continue
        square = _square_avatar_image(source_img, FACTION_AVATAR_SIZE)
        print(f"  размер: {square.width}×{square.height}")
        square.save(out_path, format="PNG")
        imported += 1
    return imported


def main() -> int:
    parser = argparse.ArgumentParser(description="Импорт фракционных скинов в assets/avatars/factions")
    parser.add_argument(
        "source",
        type=Path,
        help="Путь к папке «скины» (или к отдельному файлу)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Только показать, что будет импортировано")
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    if not source.exists():
        print(f"Путь не найден: {source}")
        return 1
    count = import_skins(source, dry_run=args.dry_run)
    print(f"Готово: {count} скин(ов). Размер: {FACTION_AVATAR_SIZE}×{FACTION_AVATAR_SIZE}.")
    return 0 if count else 2


if __name__ == "__main__":
    raise SystemExit(main())
