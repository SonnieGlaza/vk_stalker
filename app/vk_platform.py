"""VK-платформа для перенесённого бота (замена aiogram).

- ``VkMessenger`` — отправка текста/фото, редактирование, удаление. Имена методов
  совместимы с вызовами ``bot.*`` из Telegram-версии, чтобы перенос механик игры
  не менял вызовы ``bot.send_message(...)``, ``bot.send_photo(...)`` и т.п
- ``build_keyboard``/``reply_keyboard``/``inline_keyboard`` — клавиатуры ВК
- ``VkFSM`` — мини-FSM для стадий ввода (аналог aiogram FSMContext)

Поведение кнопок ВК:
 кнопки **с payload** (inline-клавиатура) приходят событием MESSAGE_EVENT («callback»);
 кнопки **без payload** (reply-клавиатура) приходят обычным сообщением с текстом кнопки —
 поэтому фильтры меню по тексту работают без изменений.

"""

from __future__ import annotations

import asyncio
import dataclasses
import html
import json
import logging
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Sequence

import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.upload import VkUpload

from app.config import Settings

logger = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 4096

_TAG_RE = re.compile(r"<[^>]+>")


def vk_plain(text: str | None) -> str:
    """VK не рендерит HTML: снимаем теги и возвращаем html-сущности в обычные символы."""
    if not text:
        return ""
    s = str(text)
    s = _TAG_RE.sub("", s)
    try:
        s = html.unescape(s)
    except Exception:
        pass
    return s.strip()


def split_long_text(text: str | None, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Режет текст на куски по лимит, стараясь не рвать строки."""
    s = str(text or "")
    if len(s) <= limit:
        return [s] if s else []
    parts: list[str] = []
    while len(s) > limit:
        cut = limit
        nl = s.rfind("\n", 0, cut + 1)
        if nl > limit // 2:
            cut = nl
        parts.append(s[:cut].rstrip())
        s = s[cut:].lstrip("\n")
    if s:
        parts.append(s.rstrip())
    return parts


# --------------------------------------------------------------------------
# Клавиатуры
# --------------------------------------------------------------------------


def _payload(cb: str) -> str:
    return json.dumps({"cb": cb}, ensure_ascii=False)


def parse_callback_payload(raw: Any) -> str | None:
    """Достать callback-код из payload кнопки."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return str(raw.get("cb") or "")
    try:
        data = json.loads(str(raw))
        if isinstance(data, dict):
            return str(data.get("cb") or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return str(raw).strip() if str(raw).strip() else None


def build_keyboard(
    *rows: Sequence[str],
    inline: bool = False,
    one_time: bool = False,
    color: VkKeyboardColor = VkKeyboardColor.SECONDARY,
) -> VkKeyboard:



    kb = VkKeyboard(one_time=one_time, inline=inline)

    first_row = True
    for row in rows:



        items = [str(x) for x in row if str(x or "").strip()]

        if not items:
            continue
        if not first_row:
            kb.add_line()
        first_row = False
        for label in items:
            kb.add_button(label, color=color)




    return kb


def reply_keyboard(*rows: Sequence[str]) -> VkKeyboard:

    """Постоянная reply-клавиатура: кнопки без payload, нажатие приходит обычным сообщением."""
    return build_keyboard(*rows, inline=False, one_time=False)


MAX_INLINE_LINES = 6  # жёсткий лимит VK: не больше 6 строк в inline-клавиатуре


def inline_keyboard(*rows: Sequence) -> VkKeyboard:
    """Inline keyboard from (label, cb) pairs; payload buttons -> MESSAGE_EVENT.

    Большие списки (>6 кнопок) упаковываются в столбцы, чтобы не превышать
    лимит VK на 6 строк inline-клавиатуры.
    """
    flat: list[tuple[str, str]] = []
    for row in rows:
        for label, cb in row:
            label = str(label or "").strip()
            if label:
                flat.append((label, str(cb)))
    n = len(flat)
    if n <= MAX_INLINE_LINES:
        columns = 1
    elif n <= MAX_INLINE_LINES * 2:
        columns = 2
    elif n <= MAX_INLINE_LINES * 3:
        columns = 3
    else:
        columns = 4
    if n > MAX_INLINE_LINES * columns:
        logger.warning("inline_keyboard обрезан: %d кнопок не помещаются в 6 строк", n)
        flat = flat[: MAX_INLINE_LINES * columns]
    kb = VkKeyboard(one_time=False, inline=True)
    for i in range(0, len(flat), columns):
        for label, cb in flat[i : i + columns]:
            kb.add_button(label, color=VkKeyboardColor.PRIMARY, payload=_payload(cb))
        if i + columns < len(flat):
            kb.add_line()
    return kb


def keyboard_to_json(kb: VkKeyboard | None) -> str | None:
    return kb.get_keyboard() if kb is not None else None


# --------------------------------------------------------------------------
# Мини-FSM
# --------------------------------------------------------------------------


class VkFSM:
    """Хранилище стадии ввода по VK user_id (аналог FSMContext из aiogram)."""

    def __init__(self, user_id: int):
        self.user_id = int(user_id)
        self._state: str | None = _FSM_STATES.get(self.user_id)
        self._data: dict[str, Any] = _FSM_DATA.setdefault(self.user_id, {})

    async def get_state(self) -> str | None:
        return self._state

    async def set_state(self, state: str | None) -> None:
        self._state = str(state) if state else None
        if self._state:
            _FSM_STATES[self.user_id] = self._state
        else:
            _FSM_STATES.pop(self.user_id, None)

    async def get_data(self) -> dict[str, Any]:
        return dict(self._data)

    async def update_data(self, **kwargs: Any) -> dict[str, Any]:
        self._data.update(kwargs)
        return self._data

    async def clear(self) -> None:
        self._state = None
        _FSM_STATES.pop(self.user_id, None)
        _FSM_DATA.pop(self.user_id, None)


_FSM_STATES: dict[int, str] = {}
_FSM_DATA: dict[int, dict[str, Any]] = {}


# --------------------------------------------------------------------------
# Мессенджер
# --------------------------------------------------------------------------


class VkMessenger:
    """Обёртка над VK Bot API. Методы названы как у aiogram Bot, чтобы перенос механик
    не трогал вызовы ``bot.send_message``, ``bot.send_photo`` и т.п.
"""

    def __init__(self, settings: Settings):
        self.token = (settings.vk_token or "").strip()
        self.group_id = settings.group_id
        if not self.token:
            raise ValueError(
                "VK_TOKEN пуст или не задан. Проверь файл .env на хостинге "
                "(переменная VK_TOKEN) или переменные окружения и перезапусти бота."
            )
        # Токены vk1.a.… нового формата бывают длиннее 85 символов — длину жёстко не проверяем.
        if len(self.token) < 20:
            raise ValueError(
                "VK_TOKEN слишком короткий (" + str(len(self.token)) + " симв.) — похоже, это не токен.\n"
                "Настоящий ключ доступа сообщества начинается с vk1.a.… Получи его в "
                "Настройки → Работа с API → Ключи доступа."
            )
        self.vk = vk_api.VkApi(token=self.token, api_version="5.131")
        # НЕ вызываем auth(): в vk_api 11+ auth(token_only=True) требует login и падает
        # LoginRequired даже с валидным токеном. Токен уже передан в конструктор;
        # вместо этого проверяем токен живым запросом.
        try:
            self.vk.method("groups.getById", {"group_id": int(self.group_id)})
        except vk_api.exceptions.ApiError as exc:
            raise ValueError(
                "VK вернул ошибку при проверке токена: " + str(exc) + "\n"
                "Проверь, что VK_TOKEN — действующий ключ доступа сообщества (vk1.a.…)\n"
                "с правами: сообщения, управление сообществом, Long Poll API, Callback, фото, документы."
            ) from exc
        self._uploader = VkUpload(self.vk)





    def _new_random_id(self) -> int:
        return secrets.randbelow((2 ** 31) - 1) + 1

    def _send(self, peer_id: int, message: str | None = None, *, keyboard: str | None = None, attachment: str | None = None, **extra: Any) -> int | None:
        params: dict[str, Any] = {"peer_id": int(peer_id), "random_id": self._new_random_id()}
        if message is not None:
            params["message"] = message
        if keyboard:
            params["keyboard"] = keyboard
        if attachment:
            params["attachment"] = attachment
        params.update(extra)
        try:
            resp = self.vk.method("messages.send", params)
            return int(resp) if resp else  0
        except Exception as exc:
            logger.warning("messages.send failed (peer %s): %s", peer_id, exc)
            return None

    def send_message(self, chat_id: int, text: str | None, *, reply_markup: VkKeyboard | None = None, attachment: str | None = None, **unused: Any) -> int | None:
        """Аналог bot.send_message: длинные тексты режет на куски; клавиатура/фото — к первому куску."""
        plain = vk_plain(text)
        chunks = split_long_text(plain)
        if not chunks:
            chunks = [""]
        sent = None
        for i, chunk in enumerate(chunks):

            kb_json = keyboard_to_json(reply_markup) if i == 0 else None

            att = attachment if i == 0 else None
            sent = self._send(int(chat_id), chunk, keyboard=kb_json, attachment=att)




        return sent

    def send_photo(self, chat_id: int, photo: Any, *, caption: str | None = None, reply_markup: VkKeyboard | None = None, **unused: Any) -> int | None:
        """Аналог bot.send_photo: фото — bytes/BytesIO/path; подпись — plain-текст."""
        try:
            data = bytes()
            if isinstance(photo, (str, Path)):
                with open(photo, "rb") as fh:
                    data = fh.read()
            elif isinstance(photo, (bytes, bytearray)):
                data = bytes(photo)
            elif hasattr(photo, "read"):
                data = photo.read()
            else:
                data = bytes(photo)


            if not data:
                logger.warning("send_photo: пустые данные фото (peer %s)", chat_id)
                return None

            bio = __import__("io").BytesIO(data)
            bio.name = "photo.png"
            resp = self._uploader.photo_messages([bio], peer_id=int(chat_id))

            attachment = self._photo_attachment(resp, chat_id)
            if not attachment:
                return None
            return self.send_message(int(chat_id), caption or "", reply_markup=reply_markup, attachment=attachment)




        except Exception as exc:
            logger.warning("send_photo failed (peer %s): %s", chat_id, exc)
            return None

    def _photo_attachment(self, resp: Any, chat_id: int) -> str | None:
        try:
            item = resp[0] if isinstance(resp, list) and resp else resp
            if not isinstance(item, dict):
                return None
            if isinstance(item.get("photo"), dict):
                item = item["photo"]
            if isinstance(item, dict):
                owner = int(item.get("owner_id", 0) or -(abs(int(self.group_id))))
                pid = int(item.get("id", 0))
                if not pid:
                    return None
                att = f"photo{owner}_{pid}"
                access_key = str(item.get("access_key") or "")
                if access_key:
                    att += f"_{access_key}"
                return att


        except (TypeError, ValueError, AttributeError):
            return None


    def edit_message_text(self, text: str | None, chat_id: int, message_id: int, *, reply_markup: VkKeyboard | None = None, **unused: Any) -> bool:
        """Аналог bot.edit_message_text (сработает там, где ВК позволяет; иначе — False)."""

        try:
            params: dict[str, Any] = {

                "peer_id": int(chat_id),

                "message_id": int(message_id),

                "random_id": self._new_random_id(),

            }
            if text is not None:
                params["message"] = vk_plain(text)

            if reply_markup is not None:
                params["keyboard"] = keyboard_to_json(reply_markup)




            self.vk.method("messages.edit", params)



            return True
        except Exception as exc:
            logger.debug("messages.edit failed (peer %s, msg %s): %s", chat_id, message_id, exc)
            return False

    def delete_message(self, chat_id: int, message_id: int) -> bool:
        """Аналог bot.delete_message (в ЛС ВК удалять сообщения обычно нельзя — вернёт False)."""
        try:
            self.vk.method("messages.delete", {


                "peer_id": int(chat_id),

                "message_id": int(message_id),

                "delete_for_all": 1,

            })
            return True




        except Exception as exc:
            logger.debug("messages.delete failed (peer %s): %s", chat_id, exc)
            return False


# --------------------------------------------------------------------------
# События Long Poll
# --------------------------------------------------------------------------


@dataclasses.dataclass
class VkEvent:

    kind: str            # "message" | "callback"
    user_id: int
    peer_id: int
    text: str | None = None
    payload: str | None = None
    message_id: int | None = None
    raw: Any = None



def _convert_event(event: Any) -> VkEvent | None:
    typ = event.type
    if typ == VkBotEventType.MESSAGE_NEW:
        obj = event.obj
        message = obj.message if hasattr(obj, "message") else obj
        if not message:
            return None
        msg = dict(message)
        if msg.get("out") == 1:
            return None
        user_id = int(msg.get("from_id") or 0)
        if not user_id:
            return None
        peer_id = int(msg.get("peer_id") or 0) or user_id
        text = str(msg.get("text") or "")
        payload = parse_callback_payload(msg.get("payload"))
        mid = int(msg.get("conversation_message_id") or 0) or int(msg.get("id") or 0) or None
        return VkEvent("message", user_id, peer_id, text=text, payload=payload, message_id=mid, raw=msg)
    if typ == VkBotEventType.MESSAGE_EVENT:

        obj = event.obj
        if not isinstance(obj, dict):
            return None
        user_id = int(obj.get("user_id") or 0)
        if not user_id:
            return None
        peer_id = int(obj.get("peer_id") or 0) or user_id
        payload = parse_callback_payload(obj.get("payload"))
        mid = int(obj.get("conversation_message_id") or 0) or None
        return VkEvent("callback", user_id, peer_id, payload=payload, message_id=mid, raw=obj)
    return None


def _poll_worker(settings: Settings, queue: "asyncio.Queue[VkEvent]", stop: "threading.Event") -> None:
    """Слушает Long Poll в отдельном потоке и кладёт события в asyncio.Queue."""
    try:
        vk = vk_api.VkApi(token=settings.vk_token, api_version="5.131")
        # auth() не вызываем (см. VkMessenger.__init__): токен уже в конструкторе.
        lp = VkBotLongPoll(vk, group_id=settings.group_id, wait=25)
        logger.info("VK Long Poll запущен (group_id=%s)", settings.group_id)
        while not stop.is_set():
            try:
                for event in lp.listen():
                    if stop.is_set():
                        break
                    vk_event = _convert_event(event)
                    if vk_event is not None:
                        queue.put_nowait(vk_event)
            except Exception as exc:
                logger.warning("Long Poll error: %s", exc)
                time.sleep(1)
    except Exception as exc:
        logger.exception("Long Poll worker завершился: %s", exc)
    finally:
        stop.set()


async def _async_main(
    settings: Settings,
    handle_event: Callable[[VkEvent, VkMessenger], Awaitable[None]],
    background: Iterable[Callable[[VkMessenger], Awaitable[None]]] = (),
) -> None:
    messenger = VkMessenger(settings)
    queue:"asyncio.Queue[VkEvent]" = asyncio.Queue()
    stop = threading.Event()
    worker_thread = threading.Thread(target=_poll_worker, args=(settings, queue, stop), daemon=True)
    worker_thread.start()
    tasks = [asyncio.create_task(fn(messenger)) for fn in background]
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            try:
                await handle_event(event, messenger)
            except Exception:
                logger.exception("Ошибка обработки VK-события (%s)", event.kind)
    finally:
        stop.set()
        for task in tasks:
            task.cancel()


def run_longpoll(
    settings: Settings,
    handle_event: Callable[[VkEvent, VkMessenger], Awaitable[None]],
    background: Iterable[Callable[[VkMessenger], Awaitable[None]]] = (),
) -> None:
    """Запустить бота: Long Poll в фоне, обработка событий и фоновые задачи в одном asyncio-цикле."""
    asyncio.run(_async_main(settings, handle_event, background))


# ---------------------------------------------------------------------------
# Callback API (webhook). Требует aiohttp; запуск: run_vk.py --webhook
# ---------------------------------------------------------------------------


def _convert_callback_event(typ: str, obj: Any) -> VkEvent | None:
    """Конвертация событий Callback API (message_new / message_event) в VkEvent."""
    if typ == "message_new":
        msg = obj.get("message") if isinstance(obj, dict) else None
        if not isinstance(msg, dict):
            return None
        if msg.get("out") == 1:
            return None
        user_id = int(msg.get("from_id") or 0)
        if not user_id:
            return None
        peer_id = int(msg.get("peer_id") or 0) or user_id
        text = str(msg.get("text") or "")
        payload = parse_callback_payload(msg.get("payload"))
        mid = int(msg.get("conversation_message_id") or 0) or int(msg.get("id") or 0) or None
        return VkEvent("message", user_id, peer_id, text=text, payload=payload, message_id=mid, raw=msg)
    if typ == "message_event" and isinstance(obj, dict):
        user_id = int(obj.get("user_id") or 0)
        if not user_id:
            return None
        peer_id = int(obj.get("peer_id") or 0) or user_id
        payload = parse_callback_payload(obj.get("payload"))
        mid = int(obj.get("conversation_message_id") or 0) or None
        return VkEvent("callback", user_id, peer_id, payload=payload, message_id=mid, raw=obj)
    return None


def run_webhook(
    settings: Settings,
    handle_event: Callable[[VkEvent, VkMessenger], Awaitable[None]],
    background: Iterable[Callable[[VkMessenger], Awaitable[None]]] = (),
) -> None:
    """Запустить бота через Callback API (webhook POST /callback).

    Обрабатывает: confirmation, message_new, message_event (тот же диспетчер
    ``handle_event``), market_order_new (автоподтверждение пополнения).
    Фоновые задачи (``background``) запускаются в том же asyncio-цикле.
    """
    import aiohttp  # noqa: F401
    from aiohttp import web

    messenger = VkMessenger(settings)

    async def _on_callback(request: web.Request) -> web.Response:
        path = request.path
        try:
            data = await request.json()
        except Exception:
            logger.debug("callback: не-JSON POST на %s", path)
            return web.Response(text="ok")
        if not isinstance(data, dict):
            return web.Response(text="ok")
        typ = str(data.get("type") or "")
        if typ == "confirmation":
            code = (settings.confirmation_code or "").strip()
            if not code:
                logger.error(
                    "Callback подтверждение получено, но VK_CONFIRMATION_CODE не задан! "
                    "Код от VK впиши в .env и перезапусти бота."
                )
                return web.Response(text="ok")
            logger.info("Callback: confirmation OK (группа %s), отвечаю кодом.", data.get("group_id"))
            return web.Response(text=code, content_type="text/plain")
        if settings.callback_secret and str(data.get("secret") or "") != settings.callback_secret:
            logger.warning("Callback: неверный secret (тип %s)", typ)
            return web.Response(status=403, text="forbidden")
        logger.debug("Callback: событие %s (группа %s)", typ, data.get("group_id"))
        obj = data.get("object") or {}
        if typ in ("message_new", "message_event"):
            vk_event = _convert_callback_event(typ, obj)
            if vk_event is not None:
                try:
                    await handle_event(messenger, vk_event)
                except Exception:
                    logger.exception("Callback event handling failed (%s)", typ)
        elif typ == "market_order_new" and isinstance(obj, dict):
            try:
                from app.bot import get_storage, handle_market_order

                result = await handle_market_order(messenger, get_storage(), obj)
                if result:
                    logger.info("market_order auto-confirmed: %s", result[:80])
            except Exception:
                logger.exception("market_order_new handling failed")
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_post("/callback", _on_callback)
    app.router.add_post("/", _on_callback)
    app.router.add_post("/{tail:.*}", _on_callback)

    async def _ping(request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app.router.add_get("/", _ping)

    async def _run() -> None:
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=int(settings.webhook_port or 8080))
        try:
            await site.start()
        except OSError as exc:
            if getattr(exc, "errno", None) in (98, 10048):
                raise SystemExit(
                    "Порт " + str(settings.webhook_port) + " уже занят — вероятно, webhook уже запущен "
                    "(контейнер/второй процесс). Один экземпляр должен слушать порт; лишний запуск не нужен."
                ) from exc
            raise
        logger.info("VK Callback webhook запущен на 0.0.0.0:%s", settings.webhook_port)
        tasks = [asyncio.create_task(fn(messenger)) for fn in background]
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await runner.cleanup()
            for task in tasks:
                task.cancel()

    asyncio.run(_run())