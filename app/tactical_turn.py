"""Общая optimistic-concurrency для тактических ходов (turn_seq)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.storage import Storage


class _CasMetaCaptureStorage:
    """Прокси: перехватывает set_meta для CAS-сохранения тактической сессии."""

    def __init__(self, inner: Storage, meta_key: str, captured: dict[str, str]) -> None:
        self._inner = inner
        self._meta_key = meta_key
        self._captured = captured

    def set_meta(self, key: str, value: str) -> None:
        if key == self._meta_key:
            self._captured["json"] = value
            return
        self._inner.set_meta(key, value)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def patch_session_message_ids(
    storage: Storage,
    *,
    meta_key: str,
    message_ids: dict[str, int],
    from_dict: Callable[[dict[str, Any]], Any] | None = None,
    save_fn: Callable[..., None] | None = None,
    save_extra: tuple[Any, ...] = (),
) -> None:
    """Обновить только message_ids в JSON meta (не перезаписывать turn_seq и ход)."""
    del from_dict, save_fn, save_extra  # legacy args — merge на уровне JSON
    raw = storage.get_meta(meta_key)
    if not raw:
        return
    try:
        data = json.loads(raw)
    except Exception:
        return
    if not isinstance(data, dict):
        return
    merged = dict(data.get("message_ids") or {})
    for key, val in message_ids.items():
        merged[str(key)] = int(val)
    data["message_ids"] = merged
    storage.set_meta(meta_key, json.dumps(data, ensure_ascii=False))


def patch_session_json_field(
    storage: Storage,
    *,
    meta_key: str,
    field: str,
    value: Any,
) -> None:
    raw = storage.get_meta(meta_key)
    if not raw:
        return
    try:
        data = json.loads(raw)
    except Exception:
        return
    if not isinstance(data, dict):
        return
    data[field] = value
    storage.set_meta(meta_key, json.dumps(data, ensure_ascii=False))


def save_turn_if_seq_ok(
    storage: Storage,
    *,
    meta_key: str,
    session: Any,
    from_dict: Callable[[dict[str, Any]], Any],
    save_fn: Callable[[Storage, Any], None],
    expected_seq: int,
) -> bool:
    raw = storage.get_meta(meta_key)
    if not raw:
        return False
    try:
        fresh = from_dict(json.loads(raw))
    except Exception:
        return False
    if getattr(fresh, "finished", False) or int(getattr(fresh, "turn_seq", -1)) != int(expected_seq):
        return False

    captured: dict[str, str] = {}
    proxy = _CasMetaCaptureStorage(storage, meta_key, captured)
    save_fn(proxy, session)
    new_json = captured.get("json")
    if not new_json:
        return False
    return storage.cas_meta_value(meta_key, expected_value=raw, new_value=new_json)
