"""VK-аналог «наград»: титул как префикс ника.

- ``get_vk_title`` / ``set_vk_title`` / ``clear_vk_title`` — титул игрока (meta ``vk_title:{id}``);
- ``apply_season_titles`` — вешает титулы топ-3 сезона (Чемпион Зоны / Серебро / Бронза);
- ``title_prefix`` — «[Титул] Ник» для отображения в профиле/статусе/дуэлях.

Титулы переживают рестарт бота (хранятся в БД), очищаются командой админа
``titleoff`` или автоматически при новом сезоне для мест ниже топ-3.
"""

from __future__ import annotations

import logging
from typing import Any

from app.storage import Storage

logger = logging.getLogger(__name__)

TITLE_META_PREFIX = "vk_title:"
SEASON_TITLES: dict[int, str] = {
    1: "🏆 Чемпион Зоны",
    2: "🥈 Серебро сезона",
    3: "🥉 Бронза сезона",
}
SEASON_TITLE_KEYS = frozenset(SEASON_TITLES.values())
MAX_TITLE_LEN = 24


def _title_key(telegram_id: int) -> str:
    return f"{TITLE_META_PREFIX}{int(telegram_id)}"


def get_vk_title(storage: Storage, telegram_id: int) -> str:
    return str(storage.get_meta(_title_key(telegram_id)) or "").strip()


def set_vk_title(storage: Storage, telegram_id: int, title: str) -> str:
    clean = (title or "").strip()
    if len(clean) > MAX_TITLE_LEN:
        clean = clean[: MAX_TITLE_LEN - 1].rstrip() + "…"
    if not clean:
        storage.delete_meta(_title_key(telegram_id))
        return ""
    storage.set_meta(_title_key(telegram_id), clean)
    return clean


def clear_vk_title(storage: Storage, telegram_id: int) -> str:
    storage.delete_meta(_title_key(telegram_id))
    return ""


def title_prefix(storage: Storage, telegram_id: int, nickname: str) -> str:
    """Вернуть «[Титул] Ник» (или просто ник, если титула нет)."""
    title = get_vk_title(storage, telegram_id)
    nick = str(nickname or "").strip()
    if not title:
        return nick
    return f"[{title}] {nick}".strip()


def apply_season_titles(storage: Storage, top: list[dict[str, Any]]) -> list[tuple[int, str]]:
    """Повесить сезонные титулы на топ-3. Прошлогодние сезонные снимаются.

    Возвращает список (telegram_id, текст_уведомления) для рассылки игрокам.
    """
    granted: dict[int, str] = {}
    notes: list[tuple[int, str]] = []
    for idx, row in enumerate(top[:3], start=1):
        row_title = SEASON_TITLES.get(idx)
        if not row_title:
            continue
        try:
            telegram_id = int(row.get("telegram_id") or 0)
        except (TypeError, ValueError):
            continue
        if telegram_id <= 0:
            continue
        granted[telegram_id] = row_title
        set_vk_title(storage, telegram_id, row_title)
        nick = str(row.get("nickname") or f"Игрок {telegram_id}")
        note = (
            f"🏅 Сезонный титул получен: «{row_title}»!\n"
            "Он виден как префикс к нику в твоём профиле и статусе."
        )
        notes.append((telegram_id, f"🏁 {nick}, {note}"))

    # Снять прошлые сезонные титулы у тех, кто не в топ-3 этого сезона.
    for uid in _all_known_titled(storage):
        if uid in granted:
            continue
        current = get_vk_title(storage, uid)
        if current in SEASON_TITLE_KEYS:
            clear_vk_title(storage, uid)
    return notes


def _all_known_titled(storage: Storage) -> list[int]:
    # Мета-ключи перечислить напрямую нельзя — проходим по игрокам (лимит ~5000).
    out: list[int] = []
    for raw_id in storage.list_player_ids():
        try:
            uid = int(raw_id)
        except (TypeError, ValueError):
            continue
        if get_vk_title(storage, uid):
            out.append(uid)
    return out