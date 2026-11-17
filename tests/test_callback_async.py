from __future__ import annotations

import asyncio
from types import SimpleNamespace

from requests import Session

import app.vk_platform as platform


def _run(coro):
    return asyncio.run(coro)


def test_vk_requests_timeout_is_capped(monkeypatch):
    seen = {}

    def fake_request(self, method, url, **kwargs):
        seen.update(kwargs)
        return "response"

    monkeypatch.setattr(Session, "request", fake_request)
    session = platform._TimeoutSession()

    assert session.request("POST", "https://vk.example", timeout=(10, 20)) == "response"
    assert seen["timeout"] == (5.0, 5.0)

    seen.clear()
    session.request("GET", "https://vk.example")
    assert seen["timeout"] == 5.0


def test_photo_upload_uses_one_httpx_attempt_with_five_second_deadline(monkeypatch):
    calls = []
    api_calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"server": 7, "photo": "serialized", "hash": "hash"}

    class FakeClient:
        def __init__(self, *, timeout):
            calls.append(("timeout", timeout))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, files):
            calls.append(("post", url, files))
            return FakeResponse()

    monkeypatch.setattr(platform.httpx, "AsyncClient", FakeClient)

    def vk_method(name, params):
        api_calls.append((name, params))
        if name == "photos.getMessagesUploadServer":
            return {"upload_url": "https://upload.example"}
        if name == "photos.saveMessagesPhoto":
            return [{"owner_id": -123, "id": 456}]
        raise AssertionError(name)

    messenger = object.__new__(platform.VkMessenger)
    messenger.vk = SimpleNamespace(method=vk_method)
    messenger.group_id = 123

    attachment = _run(messenger._upload_photo_attachment(b"image-bytes", 42))

    assert attachment == "photo-123_456"
    assert [call[0] for call in api_calls] == ["photos.getMessagesUploadServer", "photos.saveMessagesPhoto"]
    assert [call[0] for call in calls].count("post") == 1
    assert calls[0][1].as_dict()["read"] == 5.0
    assert list(calls[1][2]) == ["photo"]


def test_send_photo_retries_photo_then_document(monkeypatch):
    """Кадр всегда с картинкой: повтор загрузки фото, затем документ (без текста)."""
    calls = []
    sent = []
    messenger = object.__new__(platform.VkMessenger)

    async def photo_upload(data, peer_id):
        calls.append(("photo", data, peer_id))
        return None

    async def document_upload(data, peer_id):
        calls.append(("document", data, peer_id))
        return "doc-1_2"

    async def send_message(chat_id, text, *, reply_markup=None, attachment=None, **kwargs):
        sent.append((chat_id, text, attachment))
        return 9

    monkeypatch.setattr(messenger, "_upload_photo_attachment", photo_upload)
    monkeypatch.setattr(messenger, "_upload_photo_document", document_upload)
    monkeypatch.setattr(messenger, "send_message", send_message)

    result = _run(messenger.send_photo(42, b"image-bytes", caption="карта"))

    assert result == 9
    assert [item[0] for item in calls] == ["photo"] * platform.PHOTO_UPLOAD_ATTEMPTS + ["document"]
    assert sent == [(42, "карта", "doc-1_2")]


def test_send_photo_never_sends_text_without_image(monkeypatch):
    """Если картинка не ушла совсем — текстового сообщения вместо кадра нет."""
    sent = []
    messenger = object.__new__(platform.VkMessenger)

    async def fail_upload(data, peer_id):
        return None

    async def send_message(chat_id, text, *, reply_markup=None, attachment=None, **kwargs):
        sent.append((chat_id, text, attachment))
        return 9

    monkeypatch.setattr(messenger, "_upload_photo_attachment", fail_upload)
    monkeypatch.setattr(messenger, "_upload_photo_document", fail_upload)
    monkeypatch.setattr(messenger, "send_message", send_message)

    assert _run(messenger.send_photo(42, b"image-bytes", caption="карта")) is None
    assert sent == []


def test_photo_upload_fields_for_bulk_protocol():
    """Под новый v2/bulk_upload первым идёт поле file1, под upload.php — photo."""
    assert platform._photo_upload_fields("https://pu.vk.com/v2/bulk_upload?x=1")[0] == "file1"
    assert platform._photo_upload_fields("https://pu.vk.com/upload.php?x=1")[0] == "photo"
