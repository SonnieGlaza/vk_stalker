"""Смоук-тест VK-вертикали: персонаж → карточка PNG → вылазка.

Использование:
    python -m pytest tests/test_vk_vertical.py -q
"""

import tempfile
from pathlib import Path

import pytest

from app.storage import NicknameTakenError, Storage
from app.location_walk import get_walk_position, move_walk_position, render_walk_frame
from app.profile_card import build_character_card


@pytest.fixture()
def storage():
    db = Path(tempfile.gettempdir()) / "vk_stalker_test_vertical.db"
    if db.exists():
        db.unlink()
    st = Storage(str(db))
    st.init_db()
    yield st
    db.unlink()


def test_create_and_profile_png(storage: Storage) -> None:
    storage.create_character(999001, nickname="Стрелок", gender="male")
    player = storage.get_character(999001)
    assert player is not None
    assert player.nickname == "Стрелок"
    assert player.money >= 0

    png = build_character_card(player, storage=storage)
    assert isinstance(png, bytes)
    assert len(png) > 5000  # реальный PNG-буфер


def test_nickname_taken(storage: Storage) -> None:
    storage.create_character(999001, nickname="Охотник", gender="female")
    with pytest.raises(NicknameTakenError):
        storage.create_character(999002, nickname="Охотник", gender="male")


def test_walk_frame_and_move(storage: Storage) -> None:
    storage.create_character(999001, nickname="Ходок", gender="male")
    player = storage.get_character(999001)
    assert player is not None

    frame = render_walk_frame(storage, player)
    assert isinstance(frame, bytes)
    assert len(frame) > 5000

    x0, y0 = get_walk_position(storage, 999001)
    move_walk_position(storage, 999001, "up")
    x1, y1 = get_walk_position(storage, 999001)
    assert (x1, y1) != (x0, y0) or (x0, y0) == (0, 0)  # допускаем кламп на границе