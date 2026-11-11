"""Карта локации: зоны для тактических вылазок на текущей локации.

Блок «карта локации»: на каждой из 12 локаций есть 2 зоны —
аномальный участок (ловля артефактов) и зона обыска (поиск схрона).
Внутриигровые мини-игры переиспользуются как есть: art.hunt (аномалии +
артефакт) и stash hunt (схрон). Встроенные шансы засады в обеих мини-играх
покрывают «риск нападения», дополнительно их здесь не увеличиваем.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import random

from app.game_logic import (
    ActionResult,
    _dead_block_text,
    _is_dead,
    faction_home_base,
    is_traveling,
)
from app.storage import Storage

SEARCH_ZONE_COOLDOWN_HOURS = 2

# Блокпост на подконтрольных чужих локациях.
BLOCKPOST_META_PREFIX = "blockpost:"
BLOCKPOST_GUARDS = 2


def _blockpost_meta_key(telegram_id: int, location: str) -> str:
    return f"{BLOCKPOST_META_PREFIX}{int(telegram_id)}:{location}"


def blockpost_passed(storage: Storage, telegram_id: int, location: str) -> bool:
    """Игрок уже прорвал блокпост на этой локации."""
    return str(storage.get_meta(_blockpost_meta_key(telegram_id, location)) or "") == "1"


def mark_blockpost_passed(storage: Storage, telegram_id: int, location: str) -> None:
    storage.set_meta(_blockpost_meta_key(telegram_id, location), "1")


def location_requires_blockpost(storage: Storage, player) -> bool:
    """Нужен ли блокпост: чужая подконтрольная локация (не база, не своя)."""
    if player is None or not player.faction:
        return False
    loc = storage.get_location(player.location)
    if loc is None:
        return False
    owner = str(loc.get("controlled_by") or "")
    if not owner or owner == player.faction:
        return False
    if player.location == faction_home_base(player.faction):
        return False
    return not blockpost_passed(storage, player.telegram_id, player.location)


def fight_blockpost(storage: Storage, telegram_id: int, location: str) -> ActionResult:
    """Прорыв блокпоста: 2 лёгких бойца ГП, урон и шанс по силе игрока."""
    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    owner = str((storage.get_location(location) or {}).get("controlled_by") or "")
    if not owner or owner == player.faction or location == faction_home_base(player.faction):
        return ActionResult(False, "Здесь блокпоста нет.")
    if blockpost_passed(storage, telegram_id, location):
        return ActionResult(False, "Блокпост уже прорван — проходи.")

    power = max(1, int(player.gear_power))
    # 2 лёгких бойца: урон ~14-22 суммарно, шанс по силе.
    guard_power = 10 + power // 4
    chance = min(95, 35 + power * 3)
    damage = max(20, random.randint(14, 22))
    if random.randint(1, 100) <= chance:
        mark_blockpost_passed(storage, telegram_id, location)
        return ActionResult(
            True,
            f"🚧 Блокпост «{owner}» прорван! Два лёгких бойца отброшены "
            f"(−{max(0, damage // 2)} HP). Проходи на локацию.",
        )
    storage.change_health(telegram_id, -damage)
    updated = storage.get_character(telegram_id, refresh_energy=False)
    if updated is not None and updated.health <= 0:
        return ActionResult(False, f"Блокпост «{owner}» расстрелял тебя: −{damage} HP. Ты погиб.")
    return ActionResult(
        False,
        f"🚧 Блокпост «{owner}» отбил атаку: −{damage} HP. Попробуй ещё раз.",
    )

# Пополнение снаряжения на своей базе: бесплатно раз в RESUPPLY_COOLDOWN_HOURS.
RESUPPLY_COOLDOWN_HOURS = 2
RESUPPLY_ITEMS: tuple[tuple[str, int], ...] = (
    ("medkit", 2),
    ("stew", 1),
    ("beard_tea", 1),
    ("vodka", 1),
)

RESUPPLY_CD_PREFIX = "resupply_cd:"

# Для каждой локации ровно 2 зоны: аномальный участок + зона обыска.
# Зоны обыска тематически названы под лор S.T.A.L.K.E.R.
LOCATION_ZONES: dict[str, list[dict]] = {
    "Кордон": [
        {"id": "anomaly", "kind": "anomaly", "label": "Аномальный участок"},
        {"id": "search_1", "kind": "search", "label": "Заброшенный гараж"},
    ],
    "Свалка": [
        {"id": "anomaly", "kind": "anomaly", "label": "Аномальный участок"},
        {"id": "search_1", "kind": "search", "label": "Сортировочная"},
    ],
    "Росток": [
        {"id": "anomaly", "kind": "anomaly", "label": "Аномальный участок"},
        {"id": "search_1", "kind": "search", "label": "Тоннели под баром"},
    ],
    "Армейские склады": [
        {"id": "anomaly", "kind": "anomaly", "label": "Аномальный участок"},
        {"id": "search_1", "kind": "search", "label": "Секретные ангары"},
    ],
    "НИИ Агропром": [
        {"id": "anomaly", "kind": "anomaly", "label": "Аномальный участок"},
        {"id": "search_1", "kind": "search", "label": "Тоннели Агропрома"},
        {"id": "lab_limit1", "kind": "lab", "label": "Лаборатория «Предел-1»"},
    ],
    "Янтарь": [
        {"id": "anomaly", "kind": "anomaly", "label": "Аномальный участок"},
        {"id": "search_1", "kind": "search", "label": "Лаборатория X-16"},
        {"id": "lab_limit2", "kind": "lab", "label": "Лаборатория «Предел-2»"},
    ],
    "Болото": [
        {"id": "anomaly", "kind": "anomaly", "label": "Аномальный участок"},
        {"id": "search_1", "kind": "search", "label": "Затопленный бункер"},
    ],
    "Темная долина": [
        {"id": "anomaly", "kind": "anomaly", "label": "Аномальный участок"},
        {"id": "search_1", "kind": "search", "label": "обыск"},
    ],
    "Рыжий лес": [
        {"id": "anomaly", "kind": "anomaly", "label": "Аномальный участок"},
        {"id": "search_1", "kind": "search", "label": "Заброшенная лесопилка"},
    ],
    "Радар": [
        {"id": "anomaly", "kind": "anomaly", "label": "Аномальный участок"},
        {"id": "search_1", "kind": "search", "label": "Ангары Радара"},
        {"id": "lab_limit3", "kind": "lab", "label": "Лаборатория «Предел-3»"},
    ],
    "Припять": [
        {"id": "anomaly", "kind": "anomaly", "label": "Аномальный участок"},
        {"id": "search_1", "kind": "search", "label": "Заброшенный универмаг"},
    ],
    "ЧАЭС": [
        {"id": "anomaly", "kind": "anomaly", "label": "Аномальный участок"},
        {"id": "search_1", "kind": "search", "label": "Машинный зал"},
    ],
    "Тунель": [
        {"id": "bazaar", "kind": "bazaar", "label": "Барахолка"},
        {"id": "trade", "kind": "trade", "label": "Торговец"},
    ],
}


def location_zones_for(location: str) -> list[dict]:
    """Зоны локации; точки обыска разворачиваются в отдельные зоны (персональный КД)."""
    zones = list(LOCATION_ZONES.get(location, []))
    if location == "Рыжий лес" and not any(str(z.get("id")) == "forester" for z in zones):
        zones.append({"id": "forester", "kind": "forester", "label": "Лесник"})
    try:
        from app.location_walk import LOCATION_WALK_RECTS

        if location in LOCATION_WALK_RECTS:
            zones = [z for z in zones if str(z.get("id") or "") != "lair_1"]
            zones.append({"id": "lair_1", "kind": "lair", "label": "Логово мутантов"})
    except Exception:
        pass
    search_base = next(
        (z for z in zones if str(z.get("id") or "") == "search_1" and str(z.get("kind") or "") == "search"),
        None,
    )
    if search_base is None:
        return zones
    try:
        from app.location_walk import LOCATION_WALK_RECTS

        rects = LOCATION_WALK_RECTS.get(location) or {}
        count = len(rects.get("search_1") or ())
    except Exception:
        count = 1
    if count <= 1:
        return zones
    out = [z for z in zones if str(z.get("id") or "") != "search_1"]
    for i in range(1, count + 1):
        z = dict(search_base)
        z["id"] = f"search_{i}"
        out.append(z)
    return out


def _zone_cooldown_key(telegram_id: int, location: str, zone_id: str) -> str:
    return f"zonecd:{location}:{zone_id}:{int(telegram_id)}"


def zone_ready_at(
    storage: Storage,
    telegram_id: int,
    location: str,
    zone_id: str,
) -> datetime | None:
    """Когда зона обыска снова доступна; None — записей нет или кулдаун истёк.

    Соглашение по времени такое же, как в остальном проекте
    (см. arena_grid/coop_mission/faction_change): храним ISO-строку
    _utc_now().isoformat(), при чтении наивный datetime трактуем как UTC.
    """
    raw = storage.get_meta(_zone_cooldown_key(telegram_id, location, zone_id))
    if not raw:
        return None
    try:
        ready_at = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if ready_at.tzinfo is None:
        ready_at = ready_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return ready_at if ready_at > now else None


def zone_cooldown_remaining_text(
    storage: Storage,
    telegram_id: int,
    location: str,
    zone_id: str,
) -> str | None:
    """Человекочитаемый остаток кулдауна («1ч 20м»); None — зона готова."""
    ready_at = zone_ready_at(storage, telegram_id, location, zone_id)
    if ready_at is None:
        return None
    total_sec = max(0, int((ready_at - datetime.now(timezone.utc)).total_seconds()))
    hours, rem = divmod(total_sec, 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours}ч {minutes}м"
    if hours:
        return f"{hours}ч"
    return f"{minutes}м"


# Отдельный кулдаун поиска артефактов (аномальный участок): 20 минут на метку-локацию,
# хранится отдельно от кулдаунов зон обыска / логова / пополнения.
ARTIFACT_HUNT_CD_MINUTES = 20
ARTIFACT_HUNT_CD_PREFIX = "huntcd:"


def _artifact_hunt_cd_key(telegram_id: int, location: str) -> str:
    return f"{ARTIFACT_HUNT_CD_PREFIX}{int(telegram_id)}:{location}"


def artifact_hunt_cd_remaining_text(storage: Storage, telegram_id: int, location: str) -> str | None:
    """Остаток кулдауна поиска артефактов («20м»); None — можно искать."""
    raw = storage.get_meta(_artifact_hunt_cd_key(telegram_id, location))
    if not raw:
        return None
    try:
        ready_at = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if ready_at.tzinfo is None:
        ready_at = ready_at.replace(tzinfo=timezone.utc)
    total_sec = max(0, int((ready_at - datetime.now(timezone.utc)).total_seconds()))
    if total_sec <= 0:
        return None
    hours, rem = divmod(total_sec, 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours}ч {minutes}м"
    if hours:
        return f"{hours}ч"
    return f"{minutes}м"


def mark_artifact_hunt_cooled_down(storage: Storage, telegram_id: int, location: str) -> None:
    """Поставить кулдаун поиска артефактов на локацию (20 минут)."""
    ready_at = datetime.now(timezone.utc) + timedelta(minutes=ARTIFACT_HUNT_CD_MINUTES)
    storage.set_meta(_artifact_hunt_cd_key(telegram_id, location), ready_at.isoformat())


def build_location_zones_caption(
    storage: Storage,
    telegram_id: int,
    location: str,
    zones_status: list[tuple[dict, str | None]],
) -> list[str]:
    """Строки блока «Зоны:» для подписи «📍 Локация».

    Аномалия — поиск артефактов (с отдельным КД поиска), логово мутантов —
    зачистка (с КД входа в логово), остальное — обыск схрона. Зона с названием
    «обыск» (Тёмная долина) показывается просто как «обыск».
    """
    lines: list[str] = ["Зоны:"]
    search_counts: dict[str, int] = {}
    search_order: list[str] = []
    for zone, remaining in zones_status:
        kind = str(zone.get("kind") or "")
        label = str(zone.get("label") or zone.get("id") or "")
        if kind == "search":
            search_counts[label] = search_counts.get(label, 0) + 1
            if label not in search_order:
                search_order.append(label)
        elif kind == "anomaly":
            suffix = f" (КД {remaining})" if remaining else ""
            lines.append(f"• ☢ {label} — поиск артефактов{suffix}")
        elif kind == "lair":
            from app.lair_hunt import lair_cooldown_remaining_text

            lair_left = lair_cooldown_remaining_text(storage, telegram_id)
            suffix = f" (КД {lair_left})" if lair_left else ""
            lines.append(f"• 🔍 {label} — зачистка{suffix}.")
        else:
            suffix = f" (КД {remaining})" if remaining else ""
            lines.append(f"• 🔍 {label} — обыск схрона{suffix}")
    for label in search_order:
        count = search_counts[label]
        if label == "обыск":
            lines.append(f"• 🔍 обыск{(' (' + str(count) + ')') if count > 1 else ''}")
        else:
            line = f"• 🔍 {label} — обыск схрона"
            if count > 1:
                line += f" ({count})"
            lines.append(line)
    return lines


def start_search_zone(
    storage: Storage,
    telegram_id: int,
    location: str,
    zone_id: str,
) -> ActionResult:
    """Начать обыск зоны локации (переиспользует мини-игру схрона).

    Проверки «мёртв / в пути / занят / уже есть сессия» делает сама
    start_stash_hunt(source="zone"): кулдаун ставится только когда новая
    вылазка реально началась (payload["stash_started"] == True) — повторный
    вход в уже идущую сессию не штрафуется новым кулдауном.
    """
    zones = location_zones_for(location)
    zone = next((z for z in zones if str(z.get("id") or "") == zone_id), None)
    if zone is None or str(zone.get("kind") or "") != "search":
        return ActionResult(False, "Такой зоны здесь нет.")

    remaining = zone_cooldown_remaining_text(storage, telegram_id, location, zone_id)
    if remaining is not None:
        return ActionResult(False, f"Зона обыска ещё восстанавливается: {remaining}.")

    from app.stash_hunt import start_stash_hunt

    result = start_stash_hunt(storage, telegram_id, source="zone")
    if result.ok and (result.payload or {}).get("stash_started"):
        ready_at = datetime.now(timezone.utc) + timedelta(hours=SEARCH_ZONE_COOLDOWN_HOURS)
        storage.set_meta(
            _zone_cooldown_key(telegram_id, location, zone_id),
            ready_at.isoformat(),
        )
    return result


def _resupply_cd_key(telegram_id: int) -> str:
    return f"{RESUPPLY_CD_PREFIX}{int(telegram_id)}"


def resupply_ready_at(storage: Storage, telegram_id: int) -> datetime | None:
    """Когда снова доступно пополнение; None — готово сейчас."""
    raw = storage.get_meta(_resupply_cd_key(telegram_id))
    if not raw:
        return None
    try:
        ready_at = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if ready_at.tzinfo is None:
        ready_at = ready_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return ready_at if ready_at > now else None


def resupply_cooldown_text(storage: Storage, telegram_id: int) -> str | None:
    """Человекочитаемый остаток кулдауна пополнения; None — готово."""
    ready_at = resupply_ready_at(storage, telegram_id)
    if ready_at is None:
        return None
    total_sec = max(0, int((ready_at - datetime.now(timezone.utc)).total_seconds()))
    hours, rem = divmod(total_sec, 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours}ч {minutes}м"
    if hours:
        return f"{hours}ч"
    return f"{minutes}м"


def resupply_equipment(storage: Storage, telegram_id: int, location: str) -> ActionResult:
    """Пополнить снаряжение на своей базе: бесплатный набор раз в 2 часа.

    Доступно только на собственной базе группировки — оттуда же берут
    старт обычных вылазок и рейдов.
    """
    from app.player_busy import player_busy_reason

    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return ActionResult(False, "Сначала создай персонажа через /start.")
    if _is_dead(player):
        return ActionResult(False, _dead_block_text())
    if is_traveling(player):
        return ActionResult(False, "В пути пополнение недоступно.")
    if location != faction_home_base(player.faction):
        return ActionResult(False, "Пополнять снаряжение можно только на своей базе.")
    busy = player_busy_reason(storage, telegram_id, skip="vendor", auto_recover=False)
    if busy:
        return ActionResult(False, busy)
    remaining = resupply_cooldown_text(storage, telegram_id)
    if remaining is not None:
        return ActionResult(False, f"🎒 Снаряжение ещё собирают: следующее пополнение через {remaining}.")
    given: list[str] = []
    for key, amount in RESUPPLY_ITEMS:
        storage.add_item(telegram_id, key, amount)
        from app.game_logic import ITEM_LABELS

        given.append(f"{ITEM_LABELS.get(key, key)} x{amount}")
    ready_at = datetime.now(timezone.utc) + timedelta(hours=RESUPPLY_COOLDOWN_HOURS)
    storage.set_meta(_resupply_cd_key(telegram_id), ready_at.isoformat())
    return ActionResult(
        True,
        f"🎒 Снаряжение пополнено: {', '.join(given)}. "
        f"Следующее пополнение через {RESUPPLY_COOLDOWN_HOURS} ч.",
    )