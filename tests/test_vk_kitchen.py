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
        "faction:tz:dep:500", "faction:tz:wd:500", "faction:tz:depall", "faction:tz:wdall",
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


def test_topup_paid_keeps_target(env):
    """Пакетный топ-ап в казну не должен превращаться в «на карман» (регрессия)."""
    import json

    st, m = env
    _run(_handle_cb(bot, m, st, "topup:paid:1:treasury"))
    raw = st.get_meta(f"{bot.TOPUP_ORDER_PREFIX}{UID}")
    assert raw, "заявка на пополнение не создана"
    payload = json.loads(str(raw))
    assert payload["target"] == "treasury", f"цель потеряна: {payload}"
    assert payload["ru_amount"] == 1500
    assert payload["label"] == "🎁 Жетон сталкера"


def test_gender_choice_saved(env):
    """Кнопки пола пишут только валидные male/female и продолжают регистрацию."""
    st, m = env
    new_uid = UID + 111
    st.create_character(new_uid, "Новенький", "unknown")
    _run(_handle_cb(bot, m, st, "reg:gender:female", uid=new_uid))
    char = st.get_character(new_uid, refresh_energy=False)
    assert char is not None and char.gender == "female"
    # Мусорный payload не пишем в БД.
    another_uid = UID + 222
    st.create_character(another_uid, "Второй", "unknown")
    _run(_handle_cb(bot, m, st, "reg:gender:blah", uid=another_uid))
    char2 = st.get_character(another_uid, refresh_energy=False)
    assert char2 is not None and char2.gender == "unknown"


def test_keyboard_button_types(env):
    """Inline-кнопки VK должны быть типа callback, иначе нажатие шлёт текст в чат (регрессия)."""
    import json

    from app.vk_platform import inline_keyboard, reply_keyboard

    inline_kb = json.loads(inline_keyboard([("Жми", "x:1"), ("Ещё", "x:2")]).get_keyboard())
    assert inline_kb["inline"] is True
    for row in inline_kb["buttons"]:
        for btn in row:
            assert btn["action"]["type"] == "callback", f"inline-кнопка не callback: {btn}"
            assert "payload" in btn["action"]

    reply_kb = json.loads(reply_keyboard(["Обычная"]).get_keyboard())
    assert reply_kb["inline"] is False
    assert reply_kb["buttons"][0][0]["action"]["type"] == "text"


def test_message_event_answered(env):
    """Нажатие inline-кнопки закрывается ответом messages.sendMessageEventAnswer."""
    calls = []

    class VkStub:
        def method(self, name, params):
            calls.append((name, params))

    class M:
        vk = VkStub()

        async def send_message(self, *a, **kw):
            return 1

        async def send_photo(self, *a, **kw):
            return 1

    m = M()
    ev = SimpleNamespace(
        kind="callback", user_id=77, peer_id=77, text="", payload="x:1",
        message_id=None, raw={"event_id": "event123", "user_id": 77, "peer_id": 77},
    )
    _run(bot._answer_message_event(m, ev))
    assert calls and calls[0][0] == "messages.sendMessageEventAnswer"
    assert calls[0][1]["event_id"] == "event123"
    assert calls[0][1]["user_id"] == 77
    # Без event_id в raw — тихий пропуск (не ломаем обработку).
    ev2 = SimpleNamespace(kind="callback", user_id=77, peer_id=77, text="", payload="x:1", message_id=None, raw={})
    _run(bot._answer_message_event(m, ev2))
    assert len(calls) == 1


def test_dead_player_auto_respawn(env):
    """Мёртвый игрок при любом сообщении получает экран смерти и респавнится."""
    st, m = env
    with st._connect() as conn:
        conn.execute("UPDATE characters SET health = 0 WHERE telegram_id = ?", (UID,))
    assert not bot._player_in_tactical_session(st, UID), "мёртвый игрок не должен быть «занят»"

    async def run():
        m.sent.clear()
        await bot._handle_message(m, st, _ev("кпк"))
        merged = _merged(m.sent)
        assert "☠️" in merged, merged[:120]
        char = st.get_character(UID, refresh_energy=False)
        assert char is not None and char.health > 0

    _run(run())


def test_cancel_works_during_fsm_input(env):
    """«/cancel» работает даже пока активен FSM-ввод (сумма пополнения)."""
    st, m = env
    from app.vk_platform import VkFSM

    async def run():
        await bot._handle_callback(m, st, _ev_cb("topup:custom"))
        state = await VkFSM(UID).get_state()
        assert state == "topup_custom"
        m.sent.clear()
        await bot._handle_message(m, st, _ev("/cancel"))
        assert "отменён" in _merged(m.sent)
        state = await VkFSM(UID).get_state()
        assert state is None

    _run(run())


async def _handle_cb(mod, m, st, payload, *, uid=UID):
    ev = SimpleNamespace(kind="callback", user_id=uid, peer_id=uid, text="", payload=payload, message_id=None, raw=None)
    await mod._handle_callback(m, st, ev)


def test_market_order_auto_confirm(env):
    """Callback market_order_new с полем amount (копейки) зачисляет RU и удаляет заявку."""
    import json

    st, m = env
    before = st.get_character(UID, refresh_energy=False).money
    st.set_meta(
        f"{bot.TOPUP_ORDER_PREFIX}{UID}",
        json.dumps({"label": "🎁 Жетон сталкера", "price_rub": 149, "ru_amount": 1500, "target": "pocket", "nickname": "Тестовый"}, ensure_ascii=False),
    )
    result = _run(bot.handle_market_order(m, st, {"user_id": UID, "id": 1, "amount": 14900}))
    assert result and "1500" in result
    after = st.get_character(UID, refresh_energy=False).money
    assert after == before + 1500
    assert st.get_meta(f"{bot.TOPUP_ORDER_PREFIX}{UID}") is None
    # Несовпадение цены: заявка остаётся, деньги не начисляются.
    st.set_meta(
        f"{bot.TOPUP_ORDER_PREFIX}{UID}",
        json.dumps({"label": "🎁 Жетон сталкера", "price_rub": 149, "ru_amount": 1500, "target": "pocket", "nickname": "Тестовый"}, ensure_ascii=False),
    )
    result2 = _run(bot.handle_market_order(m, st, {"user_id": UID, "id": 2, "amount": 999900}))
    assert result2 and "не совпала" in result2
    assert st.get_meta(f"{bot.TOPUP_ORDER_PREFIX}{UID}") is not None
    assert st.get_character(UID, refresh_energy=False).money == after


def test_pda_sections_respond(env):
    """Все разделы КПК (статистика/журналы/справка) отвечают текстом."""
    st, m = env
    flows = [
        "pda:stats", "pda:find", "pda:arts", "pda:drop", "pda:info",
        "pda:deaths", "pda:daily", "pda:notify", "locmap:zonemap", "travel:open",
    ]
    _run(_click_cbs(st, m, flows))


def test_pda_players_flow(env):
    """КПК → Игроки: список группировок и страница фракции с кнопками дуэли."""
    st, m = env
    st.create_character(UID + 1, "Соперник", "male")
    st.set_faction(UID + 1, "Долг")

    async def run():
        m.sent.clear()
        await bot._handle_callback(m, st, _ev_cb("pda:players"))
        merged = _merged(m.sent)
        assert "Игроки Зоны" in merged and "Долг" in merged, merged[:200]
        m.sent.clear()
        await bot._handle_callback(m, st, _ev_cb("players:f:Долг:0"))
        merged = _merged(m.sent)
        assert "Соперник" in merged and "Не понял" not in merged, merged[:200]

    _run(run())


def test_kpk_keyboard_has_all_sections(env):
    """Под карточкой КПК все разделы ТГ-КПК (payload-кнопки)."""
    import json

    kb = json.loads(bot._kpk_keyboard().get_keyboard())
    cbs = set()
    for row in kb["buttons"]:
        for btn in row:
            cbs.add(json.loads(btn["action"]["payload"])["cb"])
    for want in (
        "pda:stats", "pda:players", "pda:find", "pda:arts", "pda:drop",
        "pda:deaths", "pda:daily", "pda:notify", "locmap:zonemap",
        "travel:open", "ref:info", "pda:info",
    ):
        assert want in cbs, f"в КПК нет кнопки {want}"


def test_info_text_full(env):
    """«ℹ️ Информация» — полная справка как в ТГ (команды, механики, маркет)."""
    text = bot._build_info_text()
    for part in (
        "/menu", "/info", "/season", "/respawn", "pay [ид|ник]",
        "Переходы", "Выброс", "Контрабанда", "Рейды", "Сезон рейтинга",
        "VK Market", "Реферал",
    ):
        assert part in text, f"в справке нет: {part}"
    # Реальный вывод через кнопку «ℹ️ Информация».
    st, m = env
    flows = ["ℹ️ Информация", "/info"]
    _run(_click_texts(st, m, flows))


def test_send_photo_fallback_text(env):
    """Если картинка не загрузилась — уходит fallback-текст с клавиатурой, игра не молчит."""
    from app.vk_platform import VkMessenger

    class M:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, *, reply_markup=None, **kw):
            self.sent.append((int(chat_id), str(text or "")))
            return 7

    m = M()
    mid = _run(VkMessenger._photo_fallback(m, 42, "подпись", None, "запасной текст"))
    assert mid == 7
    assert m.sent and "запасной текст" in m.sent[0][1]
    assert m.sent[0][0] == 42
    # Без fallback-текста отдаём подпись; если пусто — ничего не шлём.
    m2 = M()
    assert _run(VkMessenger._photo_fallback(m2, 42, "", None, None)) is None
    assert not m2.sent


def test_normalize_photo_downscales(env):
    """Пережатие картинки под VK: RGBA → RGB, огромные размеры — в лимит."""
    from io import BytesIO

    from PIL import Image

    from app.vk_platform import _normalize_photo

    img = Image.new("RGBA", (5000, 5000), (10, 20, 30, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    out = _normalize_photo(buf.getvalue())
    assert out, "нормализация не дала результат"
    im2 = Image.open(BytesIO(out))
    assert max(im2.size) <= 4000, f"размер не уменьшен: {im2.size}"
    assert im2.mode == "RGB"


def test_players_unknown_payload_safe(env):
    """Мусорный players-payload не роняет диспетчер."""
    st, m = env
    _run(_click_cbs(st, m, ["players:f:НетТаких:999", "players:root"]))