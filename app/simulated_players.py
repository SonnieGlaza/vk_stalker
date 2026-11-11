"""Симулированные игроки — «живые» боты в рейтинге и списке игроков.

Боты — обычные записи в ``characters`` с отрицательным telegram_id
(``-1000101…``). Поэтому они видны везде: в «👥 Игроки», в профилях,
в общем и сезонном рейтинге, на локациях своей группировки.

Отрицательный id — предохранитель: награды сезона выдаются только
положительным id (``_grant_season_rating_rewards``), поэтому боты никогда
не получат предметы и не попадут в рассылки.

Боты исключены из:
* ``Storage.list_player_ids()`` — циклы уведомлений, выживания и рассылок;
* вызовов на дуэль — иначе бой завис бы: бот не ходит.

Ежедневно каждый бот получает 15–40 рейтинга (как будто играет), немного
денег и активности. Присутствие «в сети» имитируется детерминированно:
у каждого бота свои получасовые окна, без записи в БД.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone

from app.storage import Storage

logger = logging.getLogger(__name__)

# Сколько рейтинга бот получает за сутки.
SIM_DAILY_RATING_MIN = 15
SIM_DAILY_RATING_MAX = 40

# Мета-ключи.
SIM_DAILY_KEY_PREFIX = "sim_daily:"
SIM_READY_META_KEY = "sim_players_ready"

# Боты-статисты: id, ник, группировка и стартовые показатели.
SIM_PLAYERS: tuple[dict[str, object], ...] = (
    {
        "id": -1000101,
        "nickname": "Клык",
        "faction": "Долг",
        "rating": 1480,
        "season": 260,
        "money": 18400,
        "quests": 63,
        "raids": 11,
        "achievements": 7,
    },
    {
        "id": -1000102,
        "nickname": "Пепел",
        "faction": "Свобода",
        "rating": 1265,
        "season": 210,
        "money": 12300,
        "quests": 51,
        "raids": 8,
        "achievements": 6,
    },
    {
        "id": -1000103,
        "nickname": "Ржавый",
        "faction": "Бандиты",
        "rating": 1120,
        "season": 180,
        "money": 9600,
        "quests": 44,
        "raids": 6,
        "achievements": 5,
    },
    {
        "id": -1000104,
        "nickname": "Тихий",
        "faction": "Нейтралы",
        "rating": 940,
        "season": 150,
        "money": 7400,
        "quests": 37,
        "raids": 4,
        "achievements": 4,
    },
    {
        "id": -1000105,
        "nickname": "Вектор",
        "faction": "Монолит",
        "rating": 1690,
        "season": 310,
        "money": 22100,
        "quests": 72,
        "raids": 14,
        "achievements": 9,
    },
)

_SIM_IDS: frozenset[int] = frozenset(int(player["id"]) for player in SIM_PLAYERS)


def simulated_ids() -> frozenset[int]:
    """Все id симулированных игроков."""
    return _SIM_IDS


def is_simulated_player(telegram_id: int | str | None) -> bool:
    """Это бот-статист, а не живой игрок?"""
    try:
        return int(telegram_id or 0) in _SIM_IDS
    except (TypeError, ValueError):
        return False


def simulated_online_now(telegram_id: int, *, now: datetime | None = None) -> bool:
    """Бот «в сети» в своём получасовом окне (детерминированно, без БД)."""
    current = now or datetime.now(timezone.utc)
    block = int(current.timestamp() // 1800)
    rng = random.Random(int(telegram_id) * 100003 + block)
    return rng.random() < 0.45


def _align_stats(storage: Storage, telegram_id: int, player: dict[str, object]) -> None:
    """Догнать показатели бота до целевых (только вверх, идемпотентно)."""
    stats = storage.get_player_stats(telegram_id)
    targets = (
        ("rating_points", int(player.get("rating") or 0)),
        ("season_rating", int(player.get("season") or 0)),
        ("quests_completed", int(player.get("quests") or 0)),
        ("raids_completed", int(player.get("raids") or 0)),
        ("achievements_unlocked", int(player.get("achievements") or 0)),
    )
    for stat_key, target in targets:
        current = int(stats.get(stat_key) or 0)
        if target > current:
            storage.add_player_stat(telegram_id, stat_key, target - current)


def _create_character(storage: Storage, player: dict[str, object]) -> bool:
    telegram_id = int(player["id"])
    nickname = str(player.get("nickname") or "Сталкер")
    try:
        storage.create_character(telegram_id, nickname, "unknown")
    except ValueError as exc:
        # Ник занят живым игроком — не ломаем регистрацию, просто пропускаем.
        logger.warning(
            "Симулированный игрок %r (id=%s) не создан: %s", nickname, telegram_id, exc
        )
        return False
    return True


def ensure_simulated_players(storage: Storage) -> int:
    """Создать недостающих ботов и выровнять их показатели.

    Безопасно вызывать на каждом тике: ничего не дублируется, показатели
    только догоняются до целевых.
    """
    created = 0
    for player in SIM_PLAYERS:
        telegram_id = int(player["id"])
        character = storage.get_character(telegram_id, refresh_energy=False)
        if character is None:
            if not _create_character(storage, player):
                continue
            character = storage.get_character(telegram_id, refresh_energy=False)
            if character is None:
                continue
            created += 1

        faction = str(player.get("faction") or "")
        if faction and str(character.faction or "") != faction:
            storage.set_faction(telegram_id, faction)

        target_money = int(player.get("money") or 0)
        if int(character.money) < target_money:
            storage.change_money(
                telegram_id, target_money - int(character.money), skip_debt_collect=True
            )

        _align_stats(storage, telegram_id, player)
    if created:
        logger.info("Созданы симулированные игроки: %s", created)
    return created


def process_simulated_daily_activity(storage: Storage) -> list[tuple[str, int]]:
    """Раз в сутки начислить ботам 15–40 рейтинга и немного активности.

    Возвращает список (ник, сколько начислено) — для журнала.
    """
    ensure_simulated_players(storage)
    today = datetime.now(timezone.utc).date().isoformat()
    granted: list[tuple[str, int]] = []
    for player in SIM_PLAYERS:
        telegram_id = int(player["id"])
        key = f"{SIM_DAILY_KEY_PREFIX}{telegram_id}"
        if storage.get_meta(key) == today:
            continue
        if storage.get_character(telegram_id, refresh_energy=False) is None:
            continue
        gain = random.randint(SIM_DAILY_RATING_MIN, SIM_DAILY_RATING_MAX)
        storage.add_player_stat(telegram_id, "rating_points", gain)
        storage.add_player_stat(telegram_id, "season_rating", gain)
        storage.change_money(
            telegram_id, random.randint(80, 400), skip_debt_collect=True
        )
        storage.add_player_stat(telegram_id, "quests_completed", random.randint(0, 2))
        if random.random() < 0.35:
            storage.add_player_stat(telegram_id, "raids_completed", 1)
        if random.random() < 0.2:
            storage.add_player_stat(telegram_id, "achievements_unlocked", 1)
        storage.set_meta(key, today)
        granted.append((str(player.get("nickname") or "Сталкер"), gain))
    if granted:
        logger.info("Симулированные игроки: рейтинг за сутки %s", granted)
    return granted
