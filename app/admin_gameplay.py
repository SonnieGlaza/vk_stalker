"""Админ-настройки игрового процесса: урон НПС и количество НПС в заданиях.

Значения живут в meta-хранилище (переживают рестарт) и применяются движком:

* **урон НПС** — множитель к урону врагов-сталкеров. Категории: задания,
  лаборатории (плюс отдельно «Предел-1/2/3») и «все» (общий множитель);
* **количество НПС в заданиях** — абсолютная замена числа НПС в контрактах
  (0 — задание без НПС, None — как задумано контрактом).

Команды в боте: ``уроннпс`` и ``нпс`` (см. ``app.bot._admin_command``).
"""

from __future__ import annotations

from typing import Any

from app.storage import Storage

NPC_DAMAGE_META_PREFIX = "admin:npc_damage:"
QUEST_NPC_COUNT_META = "admin:quest_npc_count"

NPC_DAMAGE_DEFAULT = 1.0
NPC_DAMAGE_MIN = 0.0
NPC_DAMAGE_MAX = 10.0

CATEGORY_ALL = "all"
CATEGORY_QUESTS = "quests"
CATEGORY_LABS = "labs"

#: Порядок вывода в справке и в отчёте команды.
CATEGORY_ORDER: tuple[str, ...] = (CATEGORY_QUESTS, CATEGORY_LABS, CATEGORY_ALL)

#: Лаборатории: id лаборатории → категория её урона.
LAB_CATEGORIES: dict[str, str] = {
    "limit1": "lab:limit1",
    "limit2": "lab:limit2",
    "limit3": "lab:limit3",
}

_CATEGORY_ALIASES: dict[str, str] = {
    # Задания (контракты, события, кооп-миссии на квестовом движке).
    "задания": CATEGORY_QUESTS,
    "задание": CATEGORY_QUESTS,
    "квесты": CATEGORY_QUESTS,
    "квест": CATEGORY_QUESTS,
    "контракты": CATEGORY_QUESTS,
    "quests": CATEGORY_QUESTS,
    "quest": CATEGORY_QUESTS,
    # Лаборатории целиком.
    "лабы": CATEGORY_LABS,
    "лаба": CATEGORY_LABS,
    "лаборатории": CATEGORY_LABS,
    "лаборатория": CATEGORY_LABS,
    "labs": CATEGORY_LABS,
    "lab": CATEGORY_LABS,
    # Конкретная лаборатория.
    "лаба1": "lab:limit1",
    "лаба2": "lab:limit2",
    "лаба3": "lab:limit3",
    "предел1": "lab:limit1",
    "предел2": "lab:limit2",
    "предел3": "lab:limit3",
    "limit1": "lab:limit1",
    "limit2": "lab:limit2",
    "limit3": "lab:limit3",
    "lab1": "lab:limit1",
    "lab2": "lab:limit2",
    "lab3": "lab:limit3",
    # Общий множитель для всех категорий.
    "все": CATEGORY_ALL,
    "всё": CATEGORY_ALL,
    "all": CATEGORY_ALL,
}

_CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_QUESTS: "задания",
    CATEGORY_LABS: "лаборатории",
    CATEGORY_ALL: "все категории",
    "lab:limit1": "лаборатория «Предел-1»",
    "lab:limit2": "лаборатория «Предел-2»",
    "lab:limit3": "лаборатория «Предел-3»",
}


def normalize_category(raw: Any) -> str | None:
    """Привести слово админа к внутреннему имени категории (или None)."""
    word = str(raw or "").strip().lower().replace("ё", "е")
    if not word:
        return None
    if word in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[word]
    if word in _CATEGORY_LABELS:
        return word
    return None


def category_label(category: str) -> str:
    return _CATEGORY_LABELS.get(str(category), str(category))


def category_parent(category: str) -> str | None:
    """Категория-родитель: у «lab:limit1» это «labs», у остальных — общий множитель."""
    key = str(category)
    if key.startswith("lab:"):
        return CATEGORY_LABS
    if key == CATEGORY_ALL:
        return None
    return CATEGORY_ALL


def _meta_key(category: str) -> str:
    return f"{NPC_DAMAGE_META_PREFIX}{category}"


def _read_multiplier(storage: Storage, category: str) -> float | None:
    raw = storage.get_meta(_meta_key(category))
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = float(str(raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    if value < NPC_DAMAGE_MIN or value > NPC_DAMAGE_MAX:
        return None
    return value


def npc_damage_multiplier(storage: Storage | None, category: str) -> float:
    """Множитель урона НПС для категории: своя настройка → родительская → 1.0."""
    if storage is None:
        return NPC_DAMAGE_DEFAULT
    key = str(category)
    seen: set[str] = set()
    while key and key not in seen:
        seen.add(key)
        value = _read_multiplier(storage, key)
        if value is not None:
            return value
        parent = category_parent(key)
        key = parent or ""
    return NPC_DAMAGE_DEFAULT


def lab_damage_multiplier(storage: Storage | None, lab_id: str) -> float:
    """Множитель урона НПС конкретной лаборатории (Предел-1/2/3)."""
    category = LAB_CATEGORIES.get(str(lab_id))
    if category is None:
        return npc_damage_multiplier(storage, CATEGORY_LABS)
    return npc_damage_multiplier(storage, category)


def set_npc_damage_multiplier(storage: Storage, category: str, value: float) -> float:
    """Записать множитель (значение уже проверено вызывающим)."""
    clamped = max(NPC_DAMAGE_MIN, min(NPC_DAMAGE_MAX, float(value)))
    storage.set_meta(_meta_key(category), f"{clamped:g}")
    return clamped


def reset_npc_damage_multiplier(storage: Storage, category: str | None = None) -> list[str]:
    """Сбросить настройку категории (или все, если category=None). Возвращает список сброшенных."""
    categories = [str(category)] if category else list_npc_damage_multipliers(storage).keys()
    dropped: list[str] = []
    for key in categories:
        if storage.get_meta(_meta_key(key)) is not None:
            storage.delete_meta(_meta_key(key))
            dropped.append(key)
    return dropped


def list_npc_damage_multipliers(storage: Storage) -> dict[str, float]:
    """Текущие настройки урона НПС: только явно заданные категории."""
    result: dict[str, float] = {}
    for category in (*CATEGORY_ORDER, *LAB_CATEGORIES.values()):
        value = _read_multiplier(storage, category)
        if value is not None:
            result[category] = value
    return result


def format_npc_damage_report(storage: Storage) -> str:
    """Текст «уроннпс» без аргументов: что сейчас задано."""
    configured = list_npc_damage_multipliers(storage)
    lines = ["🎯 Урон НПС (множитель к базовому урону):"]
    for category in (*CATEGORY_ORDER, *LAB_CATEGORIES.values()):
        value = configured.get(category)
        if value is None:
            effective = npc_damage_multiplier(storage, category)
            lines.append(f"• {category_label(category)}: по умолчанию (×{effective:g})")
        else:
            lines.append(f"• {category_label(category)}: ×{value:g}")
    lines.append("")
    lines.append(f"👥 НПС в заданиях: {format_quest_npc_count(storage)}")
    return "\n".join(lines)


def quest_npc_count(storage: Storage | None) -> int | None:
    """Сколько НПС ставить в заданиях: None — как задумано контрактом."""
    if storage is None:
        return None
    raw = storage.get_meta(QUEST_NPC_COUNT_META)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return min(value, 20)


def set_quest_npc_count(storage: Storage, count: int) -> int:
    value = max(0, min(20, int(count)))
    storage.set_meta(QUEST_NPC_COUNT_META, str(value))
    return value


def reset_quest_npc_count(storage: Storage) -> bool:
    if storage.get_meta(QUEST_NPC_COUNT_META) is None:
        return False
    storage.delete_meta(QUEST_NPC_COUNT_META)
    return True


def format_quest_npc_count(storage: Storage | None) -> str:
    value = quest_npc_count(storage)
    if value is None:
        return "как в контракте"
    if value == 0:
        return "0 (задания без НПС)"
    return f"ровно {value}"


def parse_multiplier(raw: Any) -> float | None:
    """Разобрать множитель из ввода админа («1.5», «0,5», «×2»)."""
    text = str(raw or "").strip().lower().replace(",", ".").lstrip("x×х*")
    if not text:
        return None
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if value < NPC_DAMAGE_MIN or value > NPC_DAMAGE_MAX:
        return None
    return value
