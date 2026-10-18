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
import io
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
            # ВАЖНО: для inline-клавиатур VK нужны кнопки типа callback, иначе
            # нажатие шлёт текст кнопки в чат вместо события message_event
            # (vk_api.add_button всегда создаёт type: text).
            kb.add_callback_button(label, color=VkKeyboardColor.PRIMARY, payload=_payload(cb))
        if i + columns < len(flat):
            kb.add_line()
    return kb


def keyboard_to_json(kb: VkKeyboard | None) -> str | None:
    return kb.get_keyboard() if kb is not None else None


# Ограничение VK на размеры картинки в сообщении (ширина/высота): больше 4000 px
# грузить смысла нет — клиент всё равно показывает превью, а сервер может отклонить.
_PHOTO_MAX_DIM = 4000


def _normalize_photo(data: bytes) -> bytes | None:
    """PIL: пережатие картинки под VK-загрузку — RGB, разумные размеры, JPEG.

    Чинит PNG с альфой/палитрой, мусорные моды и слишком большие размеры:
    именно такие картинки VK часто не принимает в сообщения.
    """
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        img.load()
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (16, 18, 22, 255))
            img = Image.alpha_composite(bg, rgba).convert("RGB")
        elif img.mode != "RGB":
            img = img.convert("RGB")
        width, height = img.size
        if width <= 0 or height <= 0:
            return None
        if width > _PHOTO_MAX_DIM or height > _PHOTO_MAX_DIM:
            scale = _PHOTO_MAX_DIM / max(width, height)
            img = img.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.Resampling.LANCZOS,
            )
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=88, optimize=True)
        return out.getvalue()
    except Exception as exc:
        logger.debug("photo normalize failed: %s", exc)
        return None


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
            return int(resp) if resp else 0
        except Exception as exc:
            if "911" in str(exc) or "Keyboard" in str(exc) or "too much" in str(exc):
                # Клавиатура слишком большая — повторяем без неё, чтобы сообщение дошло.
                params.pop("keyboard", None)
                try:
                    resp = self.vk.method("messages.send", params)
                    return int(resp) if resp else 0
                except Exception as exc2:
                    logger.warning("messages.send без клавиатуры failed (peer %s): %s", peer_id, exc2)
                    return None
            logger.warning("messages.send failed (peer %s): %s", peer_id, exc)
            return None

    async def send_message(self, chat_id: int, text: str | None, *, reply_markup: VkKeyboard | None = None, attachment: str | None = None, **unused: Any) -> int | None:
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

    async def send_photo(self, chat_id: int, photo: Any, *, caption: str | None = None, reply_markup: VkKeyboard | None = None, fallback_text: str | None = None, **unused: Any) -> int | None:
        """Аналог bot.send_photo: фото — bytes/BytesIO/path; подпись — plain-текст.

        Загрузка идёт каскадом, чтобы картинка ушла в любом случае:
        1. штатный vk_api.photo_messages (старые серверы upload.php);
        2. пережатие в JPEG (RGB, разумные размеры) и повтор;
        3. картинка как документ с превью (docs.getMessagesUploadServer — стабильный путь,
           работает и когда VK отдаёт новый v2/bulk_upload вместо upload.php);
        если не помогло ничего и задан fallback_text — отправляем текст с клавиатурой,
        чтобы игра не «молчала» без картинки.
        """
        try:
            if isinstance(photo, (str, Path)):
                with open(photo, "rb") as fh:
                    data = fh.read()
            elif isinstance(photo, (bytes, bytearray)):
                data = bytes(photo)
            elif hasattr(photo, "read"):
                data = photo.read()
            else:
                data = bytes(photo)
        except Exception as exc:
            logger.warning("send_photo: не прочиталось фото (peer %s): %s", chat_id, exc)
            data = b""

        if not data:
            logger.warning("send_photo: пустые данные фото (peer %s)", chat_id)
            return await self._photo_fallback(chat_id, caption, reply_markup, fallback_text)

        attachment = self._upload_photo_attachment(data, int(chat_id))
        if attachment:
            return await self.send_message(int(chat_id), caption or "", reply_markup=reply_markup, attachment=attachment)
        return await self._photo_fallback(chat_id, caption, reply_markup, fallback_text)

    async def _photo_fallback(self, chat_id: int, caption: str | None, reply_markup: VkKeyboard | None, fallback_text: str | None) -> int | None:
        """Если фото не ушло — отправляем подпись/текст с клавиатурой (игра не молчит)."""
        text = fallback_text if fallback_text is not None else (caption or "")
        if not text.strip():
            logger.warning("send_photo: картинка не отправлена (peer %s), fallback пуст", chat_id)
            return None
        return await self.send_message(int(chat_id), text, reply_markup=reply_markup)

    def _upload_photo_attachment(self, data: bytes, peer_id: int) -> str | None:
        """Каскад загрузки картинки в сообщение; возвращает attachment (photo…/doc…) или None."""
        # 1) штатный путь vk_api (upload.php)
        attachment = self._upload_photo_messages(data, peer_id)
        if attachment:
            return attachment
        # 2) пережатие в понятный VK JPEG и повтор
        normalized = _normalize_photo(data)
        if normalized is not None and normalized != data:
            attachment = self._upload_photo_messages(normalized, peer_id)
            if attachment:
                return attachment
        # 3) гарантированный запасной путь — изображение-документ (показывается превью)
        attachment = self._upload_photo_document(data, peer_id)
        if attachment:
            logger.warning(
                "photo upload: photo-пути не сработали, картинка уйдёт документом-файлом "
                "(peer %s) — см. предупреждения выше",
                peer_id,
            )
            return attachment
        return None

    def _upload_photo_messages(self, data: bytes, peer_id: int) -> str | None:
        """Ручная загрузка фото в сообщение: VK менял протокол — старый upload.php принимает
        поле `photo`, новый v2/bulk_upload — только `file1` (а vk_api 11.x шлёт `file0` и
        молча роняет photo_messages, из-за чего фото уходило документом-файлом).
        Пробуем все имена полей и сохраняем через photos.saveMessagesPhoto."""
        try:
            server = self.vk.method("photos.getMessagesUploadServer", {"peer_id": int(peer_id)})
        except Exception as exc:
            logger.warning(
                "photos.getMessagesUploadServer failed (peer %s): %s — проверь, что в токене "
                "сообщества выданы права «фотографии»",
                peer_id, exc,
            )
            return None
        upload_url = str((server or {}).get("upload_url") or "")
        if not upload_url:
            logger.warning("photos.getMessagesUploadServer: пустой upload_url (peer %s)", peer_id)
            return None
        last_exc: Exception | None = None
        for field in ("photo", "file1", "file0"):
            try:
                files = {field: ("photo.jpg", io.BytesIO(data), "image/jpeg")}
                resp = self.vk.http.post(upload_url, files=files)
                payload = resp.json() if hasattr(resp, "json") else resp
                if not isinstance(payload, dict):
                    continue
                photo = payload.get("photo")
                if not photo:
                    # Новый протокол (bulk_upload) возвращает files/sha/secret без photo —
                    # сохранить такой ответ нельзя; пробуем следующее имя поля.
                    continue
                saved = self.vk.method(
                    "photos.saveMessagesPhoto",
                    {"server": payload.get("server"), "photo": photo, "hash": payload.get("hash")},
                )
                att = self._photo_attachment(saved, peer_id)
                if att:
                    logger.info(
                        "photo upload OK (peer %s, поле %s, upload_url=%s...)",
                        peer_id, field, upload_url[:44],
                    )
                    return att
            except Exception as exc:
                last_exc = exc
                logger.debug("photo upload (поле %s) failed (peer %s): %s", field, peer_id, exc)
        if last_exc is not None:
            logger.warning("photo upload failed (peer %s): %s", peer_id, last_exc)
        return None

    def _upload_photo_document(self, data: bytes, peer_id: int) -> str | None:
        """Картинка как документ с превью: docs.getMessagesUploadServer + docs.save."""
        try:
            server = self.vk.method("docs.getMessagesUploadServer", {"type": "doc", "peer_id": int(peer_id)})
            upload_url = server.get("upload_url")
            if not upload_url:
                return None
            files = {"file": ("photo.jpg", io.BytesIO(data), "image/jpeg")}
            resp = self.vk.http.post(upload_url, files=files)
            payload = resp.json()
            file_token = str(payload.get("file") or "")
            if not file_token:
                logger.warning("docs upload: пустой file (peer %s): %s", peer_id, payload)
                return None
            saved = self.vk.method("docs.save", {"file": file_token})
            doc = None
            if isinstance(saved, dict):
                doc = saved.get("doc") or {}
            elif isinstance(saved, list) and saved:
                doc = (saved[0] or {}).get("doc") or {}
            if not isinstance(doc, dict):
                return None
            owner_id = int(doc.get("owner_id") or 0)
            doc_id = int(doc.get("id") or 0)
            if not owner_id or not doc_id:
                return None
            att = f"doc{owner_id}_{doc_id}"
            access_key = str(doc.get("access_key") or "")
            if access_key:
                att += f"_{access_key}"
            return att
        except Exception as exc:
            logger.warning("docs upload failed (peer %s): %s", peer_id, exc)
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


    async def edit_message_text(self, text: str | None, chat_id: int, message_id: int, *, reply_markup: VkKeyboard | None = None, **unused: Any) -> bool:
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

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
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
        from app.bot import APP_VERSION

        _ok = "ok " + APP_VERSION  # версия видна прямо в access-логе Railway
        path = request.path
        try:
            data = await request.json()
        except Exception:
            logger.debug("callback: не-JSON POST на %s", path)
            return web.Response(text=_ok)
        if not isinstance(data, dict):
            return web.Response(text=_ok)
        typ = str(data.get("type") or "")
        if typ == "confirmation":
            code = (settings.confirmation_code or "").strip()
            if not code:
                logger.error(
                    "Callback подтверждение получено, но VK_CONFIRMATION_CODE не задан! "
                    "Код от VK впиши в .env и перезапусти бота."
                )
                return web.Response(text="ok")
            logger.info(
                "Callback: confirmation OK (группа %s, код совпадает=%s), отвечаю кодом.",
                data.get("group_id"),
                code == str(data.get("key") or ""),
            )
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
        return web.Response(text=_ok)

    app = web.Application()
    app.router.add_post("/callback", _on_callback)
    app.router.add_post("/", _on_callback)
    app.router.add_post("/{tail:.*}", _on_callback)

    async def _ping(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def _version(_: web.Request) -> web.Response:
        from app.bot import APP_VERSION

        return web.Response(text=f"vk_stalker {APP_VERSION}")

    app.router.add_get("/", _ping)
    app.router.add_get("/callback", _ping)
    app.router.add_get("/version", _version)

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
        logger.info(
            "VK_CONFIRMATION_CODE задан: %s (адрес в настройках VK: https://<домен>/callback)",
            "да" if (settings.confirmation_code or "").strip() else "НЕТ — подтверждение Callback API не пройдёт",
        )
        tasks = [asyncio.create_task(fn(messenger)) for fn in background]
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await runner.cleanup()
            for task in tasks:
                task.cancel()

    asyncio.run(_run())