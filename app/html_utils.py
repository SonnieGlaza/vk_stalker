from __future__ import annotations

import re
from html import escape

from app.fsm_nav import REPLY_NAV_BUTTONS

_NICKNAME_FORBIDDEN_RE = re.compile(r'[<>&"]')


def html_safe(text: str | None) -> str:
    """Экранировать пользовательский текст для Telegram HTML."""
    if text is None:
        return ""
    return escape(str(text), quote=False)


def nickname_validation_error(nickname: str) -> str | None:
    """None если прозвище допустимо, иначе текст ошибки для игрока."""
    cleaned = (nickname or "").strip()
    if len(cleaned) < 2:
        return "Прозвище слишком короткое. Введи хотя бы 2 символа."
    if len(cleaned) > 24:
        return "Прозвище слишком длинное. Максимум 24 символа."
    if _NICKNAME_FORBIDDEN_RE.search(cleaned):
        return 'Прозвище не может содержать символы <, >, & или ".'
    if cleaned in REPLY_NAV_BUTTONS:
        return "Это кнопка меню, а не прозвище. Введи имя персонажа текстом."
    return None
