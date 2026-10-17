# -*- coding: utf-8 -*-
"""Постоянный смоук: все reply-кнопки меню, inline-переходы, админ- и текстовые команды.

Запуск: python -m pytest tests/test_vk_kitchen.py -q
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.bot as bot
from app.storage import Storage

UID = 990001


class FakeMessenger:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, *, reply_markup=None, **kw):
        self.sent.append((int(chat_id), str(text or "")))
        return 1

    async def send_photo(self, chat_id, photo, *, caption=None, reply_markup=None, **kw):
        self.sent.append((int(chat_id), "📷 " + str(caption or "")))
        return 1


def _ev(text):
    return SimpleNamespace(kind="message", user_id=UID, peer_id=UID, text=text, payload=None, message_id=None, raw=None)


def _ev_cb(payload):
    return SimpleNamespace(kind="callback", user_id=UID, peer_id=UID, text="", payload=payload, message_id=None, raw=None)


@pytest.fixture()
def env():
    old_storage, old_admin = bot._storage, bot._ADMIN_IDS
    bot._storage = None
    tmp = Path(tempfile.mkdtemp())
    st = Storage(str(tmp / "t.db"), snapshot_path=str(tmp / "t.backup.json"))
    st.init_db()
    bot._storage = st
    st.create_character(UID, "Тестовый", "male")
    st.set_faction(UID, "Долг")
    st.change_money(UID, 100_000)
    bot._ADMIN_IDS = (UID,)
    m = FakeMessenger()
    yield st, m
    bot._storage, bot._ADMIN_IDS = old_storage, old_admin


def _run(coro):
    return asyncio.run(coro)


def _merged(sent) -> str:
    return " ".join(t for _, t in sent)


async def _click_texts(st, m, labels):
    for label in labels:
        m.sent.clear()
        await bot._handle_message(m, st, _ev(label))
        assert m.sent, f"кнопка '{label}' молчит"
        assert "Не понял" not in _merged(m.sent), f"кнопка '{label}' -> Не понял"


async def _click_cbs(st, m, flows):
    for payload in flows:
        m.sent.clear()
        await bot._handle_callback(m, st, _ev_cb(payload))
        assert m.sent, f"callback {payload} молчит"
        assert "Не понял" not in _merged(m.sent), f"callback {payload} -> Не понял"


def test_menu_reply_buttons(env):
    st, m = env
    labels = [
        "📟 КПК", "🎒 Инвентарь", "📡 Статус", "🏕 Вылазка", "🛒 Торговец",
        "🏛 Группировка", "📋 Задания", "🪖 Рейды", "🏆 Рейтинг", "🏦 Барахолка",
        "🚚 Контрабанда", "🛰 События", "⚔️ Дуэль", "⚔️ Арена", "👥 Кооп",
        "📅 Ежедневка", "⭐ Пополнить", "🔔 Уведомления", "☠️ Смерти",
        "🔥 Как не сдохнуть", "ℹ️ Информация",
        "🛒 Купить", "💰 Продать", "🔧 Ремонт", "⚙️ Экипировка", "🛠 Техник", "🧑‍⚕️ Медик",
        "🏛 Обзор", "💰 Казна", "🎖 Звания", "📦 Склад", "🏗 Постройки",
        "🏛 Клановые задачи", "🤝 Союзы", "🚚 Гараж",
        "🧪 Создать лобби", "🔎 Открытые лобби", "🪖 Рейд на локацию",
        "⚠️ Война", "🔥 Захват точки", "🏰 Военное лобби", "☢ Атака Монолита",
    ]
    _run(_click_texts(st, m, labels))


def test_inline_flows(env):
    st, m = env
    flows = [
        "rating:menu", "rating:alltime:page:0", "rating:season:page:0",
        "rating:achievements", "rating:factions", "rating:seasoninfo",
        "daily:menu", "daily:claim", "notify:menu", "death:menu", "tutorial:page:1",
        "quest:menu", "quest:list", "eco:menu", "eco:exchange:list:all", "eco:market:list",
        "eco:smuggle:menu", "events:menu", "monolith:menu", "travel:open", "hunt:open",
        "sthunt:open", "stash:menu", "ref:info", "topup:menu", "topup:pack:1",
        "arena:move:up", "notify:toggle:emission", "eco:smuggle:status",
    ]
    _run(_click_cbs(st, m, flows))


def test_admin_commands(env):
    st, m = env

    async def run():
        for line in [
            "give Тестовый 100",
            "top",
            "fixme Тестовый",
            "title Тестовый Хранитель Зоны",
            "titleoff Тестовый",
            "setseason 30.12.2026 12:00",
            "confirm_topup Тестовый",
            "setfaction Тестовый Свобода",
            "leader Долг Тестовый",
            "settravel Тестовый 60",
            "deleteplayer Тестовый",
        ]:
            m.sent.clear()
            await bot._handle_message(m, st, _ev(line))
            assert m.sent, f"команда '{line}' молчит"
            assert "Не понял" not in _merged(m.sent), f"команда '{line}' -> Не понял"
        st.create_character(UID, "Тестовый", "male")
        st.set_faction(UID, "Свобода")
        st.change_money(UID, 100_000)

    _run(run())


def test_text_commands(env):
    st, m = env

    async def run():
        for line in ["/menu", "/info", "/cancel", "/respawn", "pay Тестовый 50", "сбор Привет"]:
            m.sent.clear()
            await bot._handle_message(m, st, _ev(line))
            assert m.sent, f"команда '{line}' молчит"
            assert "Не понял" not in _merged(m.sent), f"команда '{line}' -> Не понял"
        st.create_character(UID + 1, "Соперник", "male")
        st.set_faction(UID + 1, "Свобода")
        m.sent.clear()
        await bot._handle_message(m, st, _ev(f"дуэль {UID + 1}"))
        assert m.sent and "Не понял" not in _merged(m.sent)

    _run(run())