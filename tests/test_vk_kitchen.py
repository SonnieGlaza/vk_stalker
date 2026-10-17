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
        await bot._handle_message(m, st, _ev("профиль"))
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
    """Все разделы КПК отвечают (text = как в ТГ pda_keyboard)."""
    st, m = env
    labels = [
        "📟 КПК", "🧾 Профиль", "💬 Чаты", "🏆 Рейтинг", "🗺 Карта", "📍 Локация",
        "👥 Игроки", "🤝 Обмен", "💎 Находки", "💎 Арты", "📊 Дроп", "☠️ Смерти",
        "📊 Статистика", "📅 Ежедневка", "🔔 Уведомления", "🔥 Как не сдохнуть",
        "🏛 Клановые задачи", "ℹ️ Информация", "🔗 Реферальная система", "⬅️ В меню",
    ]
    _run(_click_texts(st, m, labels))
    # Инлайн-разделы тоже живы (старые сообщения).
    _run(_click_cbs(st, m, ["pda:stats", "pda:menu"]))


def test_pda_sbor_button(env):
    """Кнопка «📣 Сбор» (только командир) в reply-меню КПК отвечает, а не «Не понял»."""
    st, m = env
    # Не-лидер — вежливый отказ.
    _run(_click_texts(st, m, ["📣 Сбор"]))
    assert "только лидер" in _merged(m.sent), _merged(m.sent)[:160]
    # Лидер группировки — объявление бойцам.
    st.set_faction_leader("Долг", UID)

    async def run():
        m.sent.clear()
        await bot._handle_message(m, st, _ev("📣 Сбор"))
        merged = _merged(m.sent)
        assert "📣 Сбор" in merged and "Тестовый" in merged, merged[:200]
        assert "Не понял" not in merged, merged[:200]

    _run(run())


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
    """Клавиатура КПК — 1 в 1 с pda_keyboard в ТГ (тот же список, тот же порядок)."""
    import json

    tg_labels = (
        "🧾 Профиль", "💬 Чаты", "🏆 Рейтинг", "🗺 Карта", "📍 Локация", "👥 Игроки",
        "🤝 Обмен", "💎 Находки", "💎 Арты", "📊 Дроп", "☠️ Смерти", "📊 Статистика",
        "📅 Ежедневка", "🔔 Уведомления", "🔥 Как не сдохнуть", "🏛 Клановые задачи",
        "ℹ️ Информация", "🔗 Реферальная система", "⬅️ В меню",
    )
    kb = json.loads(bot._pda_keyboard().get_keyboard())
    assert kb["inline"] is False, "КПК-меню должно быть reply-клавиатурой (кнопки видны всегда)"
    labels = tuple(btn["action"]["label"] for row in kb["buttons"] for btn in row)
    assert labels == tg_labels, f"Кнопки КПК не совпадают с ТГ дословно:\nVK: {labels}\nTG: {tg_labels}"
    # Командиру «📣 Сбор» встаёт ровно между «ℹ️ Информация» и «🔗 Реферальная система» (как в ТГ).
    st, m = env
    st.set_faction_leader("Долг", UID)
    kb2 = json.loads(bot._pda_keyboard_for(st, UID).get_keyboard())
    labels2 = tuple(btn["action"]["label"] for row in kb2["buttons"] for btn in row)
    pos = labels2.index("🏛 Клановые задачи")
    assert labels2[pos + 1] == "ℹ️ Информация"
    assert labels2[pos + 2] == "📣 Сбор"
    assert labels2[pos + 3] == "🔗 Реферальная система"
    assert labels2[-1] == "⬅️ В меню"


def test_sortie_and_rating_tg_labels(env):
    """«🏕 Вылазка» — сорти-меню как в ТГ; рейтинг — метки ratings_keyboard как в ТГ."""
    import json

    from app.vk_platform import keyboard_to_json

    st, m = env

    # Сорти-клавиатура — как sortie_keyboard в ТГ.
    kb = json.loads(keyboard_to_json(bot._sortie_keyboard()))
    labels = [b["action"]["label"] for row in kb["buttons"] for b in row]
    assert labels == ["⚔️ Война", "🗺 Переход", "⚔️ Арена", "🪖 Рейды", "📍 Локация", "⬅️ В меню"], labels
    # Рейтинг — метки ratings_keyboard в ТГ + «📊 Моя статистика».
    kb = json.loads(keyboard_to_json(bot._rating_keyboard()))
    labels = [b["action"]["label"] for row in kb["buttons"] for b in row]
    for want in (
        "📊 Моя статистика", "🏆 Рейтинг за всё время", "📅 Рейтинг за сезон",
        "🏅 Рейтинг группировок", "🎖 Достижения и медали",
    ):
        assert want in labels, labels
    # «📊 Моя статистика» отвечает текстом статистики.
    _run(_click_cbs(st, m, ["rating:stats"]))
    assert "Статистика" in _merged(m.sent), _merged(m.sent)[:160]


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


def test_profile_text_format(env):
    """Профиль: ник с медалями, ID STK-, фракция, звание (как в примере)."""
    st, m = env
    from app.player_medals import grant_medal, medals_nick_suffix

    grant_medal(st, UID, "season_gold")
    st.set_faction_rank(UID, "r6")
    text = bot._profile_text(st, st.get_character(UID, refresh_energy=False))
    assert text.startswith("Профиль сталкера Тестовый"), text
    assert "🥇" in text, "медали не попали в профиль: " + text
    assert f"ID: STK-{UID}" in text, text
    assert "Фракция: Долг" in text, text
    assert "Звание: Майор" in text, text

    # Через кнопку в меню тоже.
    m.sent.clear()
    _run(bot._handle_message(m, st, _ev("🧾 Профиль")))
    merged = _merged(m.sent)
    assert "ID: STK-" in merged and "Фракция: Долг" in merged, merged[:160]