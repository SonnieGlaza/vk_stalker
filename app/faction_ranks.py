from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FactionRank:
    key: str
    title: str
    level: int


# Доп. звания внутри группировки (назначает лидер). level 1–6.
# title лидера — отдельный display, когда игрок = leader_id.
FACTION_RANKS: dict[str, tuple[FactionRank, ...]] = {
    "Долг": (
        FactionRank("r1", "Рядовой", 1),
        FactionRank("r2", "Сержант", 2),
        FactionRank("r3", "Прапорщик", 3),
        FactionRank("r4", "Лейтенант", 4),
        FactionRank("r5", "Капитан", 5),
        FactionRank("r6", "Майор", 6),
    ),
    "Нейтралы": (
        FactionRank("r1", "Зелень", 1),
        FactionRank("r2", "Стоящий", 2),
        FactionRank("r3", "Охотник", 3),
        FactionRank("r4", "Наставник", 4),
        FactionRank("r5", "Мастер", 5),
        FactionRank("r6", "Правая рука", 6),
    ),
    "Бандиты": (
        FactionRank("r1", "Шестерка", 1),
        FactionRank("r2", "Приблатнённый", 2),
        FactionRank("r3", "Блатной", 3),
        FactionRank("r4", "Положенец", 4),
        FactionRank("r5", "Пахан", 5),
        FactionRank("r6", "Смотрящий", 6),
    ),
    "Свобода": (
        FactionRank("r1", "Ветер", 1),
        FactionRank("r2", "Дух", 2),
        FactionRank("r3", "Товарищ", 3),
        FactionRank("r4", "Свободный", 4),
        FactionRank("r5", "Командир", 5),
        FactionRank("r6", "Зам лидера", 6),
    ),
    "Монолит": (
        FactionRank("r1", "Послушник", 1),
        FactionRank("r2", "Страж", 2),
        FactionRank("r3", "Посвящённый", 3),
        FactionRank("r4", "Проповедник", 4),
        FactionRank("r5", "Исполнитель", 5),
        FactionRank("r6", "Голос Монолита", 6),
    ),
}

FACTION_LEADER_TITLES: dict[str, str] = {
    "Долг": "Полковник",
    "Нейтралы": "Лидер",
    "Бандиты": "Вор в законе",
    "Свобода": "Лидер",
    "Монолит": "Харис",
}

DEFAULT_RANK_KEY = "r1"


def ranks_for_faction(faction: str | None) -> tuple[FactionRank, ...]:
    if not faction:
        return ()
    return FACTION_RANKS.get(faction, ())


def leader_title(faction: str | None) -> str | None:
    if not faction:
        return None
    return FACTION_LEADER_TITLES.get(faction)


def rank_by_key(faction: str | None, key: str | None) -> FactionRank | None:
    if not faction or not key:
        return None
    for rank in ranks_for_faction(faction):
        if rank.key == key:
            return rank
    return None


def default_rank_key(faction: str | None) -> str | None:
    ranks = ranks_for_faction(faction)
    if not ranks:
        return None
    return ranks[0].key


def resolve_rank_title(
    *,
    faction: str | None,
    faction_rank: str | None,
    is_leader: bool,
) -> str | None:
    """Итоговое отображаемое звание: лидер → титул лидера, иначе назначенный ранг."""
    if not faction:
        return None
    if is_leader:
        return leader_title(faction) or "Лидер"
    rank = rank_by_key(faction, faction_rank) or rank_by_key(faction, DEFAULT_RANK_KEY)
    return rank.title if rank else None
