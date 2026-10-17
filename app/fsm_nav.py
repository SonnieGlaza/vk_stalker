"""Сброс мини-FSM при нажатии кнопок навигации во время ввода числа/текста (VK-версия)."""

from __future__ import annotations

from typing import Any, Callable


def normalize_nav_button_text(value: str | None) -> str:
    """Убрать variation selector (🗺 vs 🗺️) и лишние пробелы у текста reply-кнопки."""
    return (value or "").replace("\ufe0f", "").strip()


REPLY_NAV_BUTTONS: frozenset[str] = frozenset(
    {
        "📟 КПК",
        "🎒 Инвентарь",
        "📡 Статус",
        "📋 Задания",
        "🛒 Торговец",
        "🏕 Вылазка",
        "🛰 События",
        "👥 Группировка",
        "🏦 Экономика",
        "🏦 Барахолка",
        "ℹ️ Информация",
        "⭐ Пополнить",
        "🧾 Профиль",
        "💬 Чаты",
        "🏆 Рейтинг",
        "🗺 Карта",
        "📍 Локация",
        "👥 Игроки",
        "🤝 Обмен",
        "💎 Находки",
        "💎 Арты",
        "📊 Дроп",
        "☠️ Смерти",
        "📊 Статистика",
        "📅 Ежедневка",
        "🔔 Уведомления",
        "📘 Обучение",
        "🔥 Как не сдохнуть",
        "🏛 Клановые задачи",
        "📣 Сбор",
        "🔗 Реферальная система",
        "⬅️ В меню",
        "⚔️ Война",
        "⚔️ Арена",
        "🗺 Переход",
        "🪖 Рейды",
        "👥 Совместная вылазка",
        "⚡ Выпить энергетик",
    }
)

_NAV_BUTTONS_NORMALIZED = frozenset(normalize_nav_button_text(button) for button in REPLY_NAV_BUTTONS)


def is_reply_menu_button(text: str | None) -> bool:
    return normalize_nav_button_text(text) in _NAV_BUTTONS_NORMALIZED


def nav_button(*labels: str) -> Callable[[str | None], bool]:
    """Предикат для сообщений по тексту reply-кнопки с учётом emoji variation selector."""
    expected = frozenset(normalize_nav_button_text(label) for label in labels)
    return lambda text: normalize_nav_button_text(text) in expected


async def abort_fsm_if_nav(fsm: Any, text: str | None) -> bool:
    """Отменить ввод, если игрок нажал кнопку меню. Возвращает True, если ввод отменён."""
    if not is_reply_menu_button(text):
        return False
    current = await fsm.get_state()
    if current is None:
        return False
    await fsm.clear()
    return True