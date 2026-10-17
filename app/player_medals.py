"""Игровые медали (значки в профиле). Не путать с титулом /medal в чатах."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.storage import Storage, utc_now

logger = logging.getLogger(__name__)

BETA_DEADLINE = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
BETA_MIN_RATING = 700
SUPPORT_MEDAL_RUB = 500
STARS_TO_RUB = 3.0  # ~50⭐ ≈ 149 ₽
BUGS_FOR_FINDER = 3
IDEAS_FOR_MEDAL = 5
ROTATING_MEDAL_DAYS = 3

MEDALS_META_PREFIX = "pmedals:"
FLAGS_META_PREFIX = "pmedal_flags:"
JOINED_META_PREFIX = "player:joined:"
ROTATING_META_KEY = "pmedals:rotating"
SEASON_PODIUM_META_KEY = "pmedals:season"
HOLDER_META_PREFIX = "pmedals:holder:"


@dataclass(frozen=True)
class MedalDef:
    key: str
    emoji: str
    title: str
    description: str
    permanent: bool = False


MEDAL_DEFS: tuple[MedalDef, ...] = (
    MedalDef(
        "beta",
        "⚙",
        "Бета-тестировщик",
        "Пришёл в проект до 30.08.2026 и набрал ≥700 рейтинга.",
        permanent=True,
    ),
    MedalDef("top_all", "🏆", "Топ-1 рейтинга", "Первое место в рейтинге за всё время. Снимается, если ушёл с вершины."),
    MedalDef("season_gold", "🥇", "Топ-1 сезона", "Первое место сезонного рейтинга. Держится до конца следующего сезона."),
    MedalDef("season_silver", "🥈", "Топ-2 сезона", "Второе место сезонного рейтинга. Держится до конца следующего сезона."),
    MedalDef("season_bronze", "🥉", "Топ-3 сезона", "Третье место сезонного рейтинга. Держится до конца следующего сезона."),
    MedalDef("creator", "👑", "Создатель", "Лидер группировки. Снимается, если группировка осталась без тебя."),
    MedalDef("mentor", "👥", "Наставник", "Активная помощь новичкам в чатах. Навсегда.", permanent=True),
    MedalDef("collector", "💎", "Собиратель", "Топ-1 по найденным артефактам. Обновляется раз в 3 дня."),
    MedalDef("richest", "💰", "Самый богатый человек в Зоне", "Топ-1 по деньгам. Обновляется раз в 3 дня."),
    MedalDef("main_support", "🏦", "Главная опора", "Топ-1 по сумме пожертвований проекту. Обновляется раз в 3 дня."),
    MedalDef(
        "support",
        "💵",
        "Опора",
        f"Пожертвовал проекту не менее {SUPPORT_MEDAL_RUB} ₽. Навсегда.",
        permanent=True,
    ),
    MedalDef("finder", "🔍", "Находчик", f"Нашёл не менее {BUGS_FOR_FINDER} багов. Навсегда.", permanent=True),
    MedalDef(
        "completionist",
        "💯",
        "Дальше некуда",
        "Открыл все достижения. Снимается, если добавят новые.",
    ),
    MedalDef(
        "idea",
        "💡",
        "Идея",
        f"Предложил не менее {IDEAS_FOR_MEDAL} идей, которые вошли в игру. Навсегда.",
        permanent=True,
    ),
    MedalDef(
        "developer",
        "🛠",
        "Без тебя этого бы не было",
        "Уникальная медаль главному разработчику. Навсегда.",
        permanent=True,
    ),
)
MEDAL_BY_KEY: dict[str, MedalDef] = {item.key: item for item in MEDAL_DEFS}

ADMIN_MEDAL_KEYS = frozenset({"mentor", "finder", "idea", "developer"})
BADGE_TOP_KINDS = frozenset(
    {
        "top",
        "топ",
        "топы",
        "who",
        "richest",
        "collector",
        "money",
        "arts",
        "арты",
        "богат",
        "богач",
        "собиратель",
        "donor",
        "донат",
        "wealth",
    }
)

# Короткие титулы для Telegram custom title (≤16, без эмодзи).
MEDAL_CHAT_TITLES: dict[str, str] = {
    "developer": "Разработчик",
    "mentor": "Наставник",
    "finder": "Находчик",
    "idea": "Идея",
    "creator": "Лидер ГП",
    "beta": "Бета",
    "top_all": "Топ-1",
    "season_gold": "Чемпион Зоны",
    "season_silver": "Серебро сезона",
    "season_bronze": "Бронза сезона",
    "collector": "Собиратель",
    "richest": "Богач Зоны",
    "main_support": "Главная опора",
    "support": "Опора",
    "completionist": "Дальше некуда",
}
MEDAL_CHAT_TITLE_PRIORITY: tuple[str, ...] = (
    "developer",
    "mentor",
    "finder",
    "idea",
    "creator",
    "beta",
    "top_all",
    "season_gold",
    "season_silver",
    "season_bronze",
    "completionist",
    "main_support",
    "support",
    "collector",
    "richest",
)


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def get_joined_at(storage: Storage, telegram_id: int) -> datetime | None:
    return _parse_dt(storage.get_meta(f"{JOINED_META_PREFIX}{int(telegram_id)}"))


def _medals_key(telegram_id: int) -> str:
    return f"{MEDALS_META_PREFIX}{int(telegram_id)}"


def _flags_key(telegram_id: int) -> str:
    return f"{FLAGS_META_PREFIX}{int(telegram_id)}"


def get_player_medal_keys(storage: Storage, telegram_id: int) -> list[str]:
    raw = storage.get_meta(_medals_key(telegram_id))
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    seen: list[str] = []
    for item in data:
        key = str(item)
        if key in MEDAL_BY_KEY and key not in seen:
            seen.append(key)
    return seen


def _save_player_medal_keys(storage: Storage, telegram_id: int, keys: list[str]) -> None:
    ordered = [key for key in MEDAL_BY_KEY if key in keys]
    if ordered:
        storage.set_meta(_medals_key(telegram_id), json.dumps(ordered, ensure_ascii=False))
    else:
        storage.delete_meta(_medals_key(telegram_id))


def grant_medal(storage: Storage, telegram_id: int, medal_key: str) -> bool:
    if medal_key not in MEDAL_BY_KEY:
        return False
    keys = get_player_medal_keys(storage, telegram_id)
    if medal_key in keys:
        return False
    keys.append(medal_key)
    _save_player_medal_keys(storage, telegram_id, keys)
    return True


def revoke_medal(storage: Storage, telegram_id: int, medal_key: str) -> bool:
    keys = get_player_medal_keys(storage, telegram_id)
    if medal_key not in keys:
        return False
    _save_player_medal_keys(storage, telegram_id, [key for key in keys if key != medal_key])
    return True


def get_medal_flags(storage: Storage, telegram_id: int) -> dict[str, int]:
    raw = storage.get_meta(_flags_key(telegram_id))
    if not raw:
        return {"mentor": 0, "bugs": 0, "ideas": 0, "developer": 0}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {
        "mentor": int(data.get("mentor") or 0),
        "bugs": int(data.get("bugs") or 0),
        "ideas": int(data.get("ideas") or 0),
        "developer": int(data.get("developer") or 0),
    }


def set_medal_flags(storage: Storage, telegram_id: int, flags: dict[str, int]) -> None:
    storage.set_meta(_flags_key(telegram_id), json.dumps(flags, ensure_ascii=False))


def stars_to_rub(stars: int) -> float:
    return max(0, int(stars)) * STARS_TO_RUB


def medals_nick_suffix(storage: Storage, telegram_id: int) -> str:
    """Эмодзи медалей сразу после ника: ' 🛠 👥' или пусто."""
    keys = get_player_medal_keys(storage, telegram_id)
    emojis = " ".join(MEDAL_BY_KEY[key].emoji for key in keys if key in MEDAL_BY_KEY)
    return f" {emojis}" if emojis else ""


def format_medals_profile_line(storage: Storage, telegram_id: int) -> str:
    suffix = medals_nick_suffix(storage, telegram_id)
    if not suffix:
        return ""
    return f"🎖{suffix}\n"


def format_medals_progress_lines(storage: Storage, telegram_id: int) -> list[str]:
    """Строки как в достижениях: ✅/🔒 + эмодзи + название + условие."""
    keys = set(get_player_medal_keys(storage, telegram_id))
    lines: list[str] = []
    for medal in MEDAL_DEFS:
        marker = "✅" if medal.key in keys else "🔒"
        lines.append(f"{marker} {medal.emoji} {medal.title} — {medal.description}")
    return lines


def format_medals_overview(storage: Storage, telegram_id: int) -> str:
    keys = set(get_player_medal_keys(storage, telegram_id))
    owned = " ".join(medal.emoji for medal in MEDAL_DEFS if medal.key in keys)
    lines = ["🏅 Медали", ""]
    if owned:
        lines.append(f"У тебя: {owned}")
        lines.append("")
    lines.extend(format_medals_progress_lines(storage, telegram_id))
    return "\n".join(lines)


def chat_title_for_player(storage: Storage, telegram_id: int) -> str | None:
    """Короткий титул в беседах по игровым медалям (без явного /medal)."""
    keys = set(get_player_medal_keys(storage, telegram_id))
    for medal_key in MEDAL_CHAT_TITLE_PRIORITY:
        if medal_key in keys:
            title = MEDAL_CHAT_TITLES.get(medal_key) or ""
            if title:
                return title[:16]
    return None


def _exclusive_holder(storage: Storage, medal_key: str, winner_id: int | None) -> None:
    """Выдать медаль победителю и снять у прошлого, без полного обхода базы."""
    meta_key = f"{HOLDER_META_PREFIX}{medal_key}"
    raw = storage.get_meta(meta_key)
    recorded: int | None = None
    if raw:
        try:
            recorded = int(raw)
        except (TypeError, ValueError):
            recorded = None
    new_id = int(winner_id) if winner_id else None
    if recorded is not None and recorded == new_id:
        if new_id is not None and medal_key not in get_player_medal_keys(storage, new_id):
            grant_medal(storage, new_id, medal_key)
        return
    if recorded is None:
        for tid in storage.list_player_ids():
            has = medal_key in get_player_medal_keys(storage, tid)
            if new_id is not None and tid == new_id:
                if not has:
                    grant_medal(storage, tid, medal_key)
            elif has:
                revoke_medal(storage, tid, medal_key)
    else:
        if recorded != new_id:
            revoke_medal(storage, recorded, medal_key)
        if new_id is not None:
            grant_medal(storage, new_id, medal_key)
    if new_id is not None:
        storage.set_meta(meta_key, str(new_id))
    else:
        storage.delete_meta(meta_key)


def _load_json_meta(storage: Storage, key: str) -> dict[str, Any]:
    raw = storage.get_meta(key)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def remember_season_podium(storage: Storage, season_id: int, top_rows: list[dict[str, Any]]) -> None:
    podium: dict[str, Any] = {"season_id": int(season_id)}
    for idx, row in enumerate(top_rows[:3], start=1):
        try:
            podium[str(idx)] = int(row.get("telegram_id") or 0)
        except (TypeError, ValueError):
            continue
    storage.set_meta(SEASON_PODIUM_META_KEY, json.dumps(podium, ensure_ascii=False))
    _apply_season_podium(storage, podium)


def _apply_season_podium(storage: Storage, podium: dict[str, Any]) -> None:
    mapping = {1: "season_gold", 2: "season_silver", 3: "season_bronze"}
    holders = {mapping[rank]: int(podium.get(str(rank)) or 0) for rank in (1, 2, 3)}
    for rank, medal_key in mapping.items():
        winner = holders.get(medal_key) or 0
        _exclusive_holder(storage, medal_key, winner if winner > 0 else None)


def add_admin_medal_progress(storage: Storage, telegram_id: int, kind: str, amount: int = 1) -> str:
    flags = get_medal_flags(storage, telegram_id)
    if kind == "mentor":
        flags["mentor"] = 1
        grant_medal(storage, telegram_id, "mentor")
        set_medal_flags(storage, telegram_id, flags)
        return "👥 Наставник — выдана навсегда."
    if kind == "developer":
        flags["developer"] = 1
        grant_medal(storage, telegram_id, "developer")
        set_medal_flags(storage, telegram_id, flags)
        return "🛠 Без тебя этого бы не было — выдана навсегда."
    if kind == "finder":
        flags["bugs"] = max(0, int(flags["bugs"]) + max(1, int(amount)))
        set_medal_flags(storage, telegram_id, flags)
        if flags["bugs"] >= BUGS_FOR_FINDER:
            grant_medal(storage, telegram_id, "finder")
            return f"🔍 Багов учтено: {flags['bugs']}. Медаль «Находчик» выдана."
        return f"🔍 Багов учтено: {flags['bugs']}/{BUGS_FOR_FINDER}."
    if kind == "idea":
        flags["ideas"] = max(0, int(flags["ideas"]) + max(1, int(amount)))
        set_medal_flags(storage, telegram_id, flags)
        if flags["ideas"] >= IDEAS_FOR_MEDAL:
            grant_medal(storage, telegram_id, "idea")
            return f"💡 Идей учтено: {flags['ideas']}. Медаль «Идея» выдана."
        return f"💡 Идей учтено: {flags['ideas']}/{IDEAS_FOR_MEDAL}."
    return "Неизвестный тип медали."


def _top_player_label(row: dict[str, Any] | None, telegram_id: int | None = None) -> str:
    if row is not None:
        nick = str(row.get("nickname") or "").strip() or "без ника"
        tid = int(row.get("telegram_id") or 0)
        return f"{nick} (id {tid})"
    if telegram_id:
        return f"id {int(telegram_id)}"
    return "никого"


def _format_top_block(title: str, rows: list[dict[str, Any]], value_fmt) -> str:
    lines = [title]
    if not rows:
        lines.append("пока пусто")
        return "\n".join(lines)
    for idx, row in enumerate(rows, start=1):
        lines.append(f"{idx}. {_top_player_label(row)} — {value_fmt(int(row.get('value') or 0))}")
    return "\n".join(lines)


def format_rotating_tops(storage: Storage, *, limit: int = 5) -> str:
    """Живые топы по деньгам, артам и донатам для админской /badge top."""

    def _load_rows(label: str, loader) -> tuple[list[dict[str, Any]], str | None]:
        try:
            rows = loader()
        except Exception:
            logger.exception("Failed to load %s top", label)
            return [], f"не удалось прочитать {label}"
        return list(rows or []), None

    money, money_err = _load_rows("деньги", lambda: storage.top_players_by_money(limit))
    arts, arts_err = _load_rows("артефакты", lambda: storage.top_players_by_stat("artifacts_found", limit))
    donors, donors_err = _load_rows("донаты", lambda: storage.top_stars_donors(limit))
    rotating = _load_json_meta(storage, ROTATING_META_KEY)
    last = _parse_dt(str(rotating.get("at") or ""))
    last_text = last.strftime("%d.%m.%Y %H:%M UTC") if last else "ещё не было"

    def holder_label(meta_key: str) -> str:
        try:
            tid = int(rotating.get(meta_key) or 0)
        except (TypeError, ValueError):
            tid = 0
        if tid <= 0:
            return "никого"
        player = storage.get_character(tid, refresh_energy=False)
        nick = str(getattr(player, "nickname", "") or "").strip() or "без ника"
        return f"{nick} (id {tid})"

    def block(title: str, rows: list[dict[str, Any]], value_fmt, error: str | None) -> str:
        if error:
            return f"{title}\n{error}"
        return _format_top_block(title, rows, value_fmt)

    blocks = [
        "Топы прямо сейчас (не рейтинг, а живые цифры).",
        f"Медали «Богач Зоны» / «Собиратель» / «Главная опора» обновляются раз в {ROTATING_MEDAL_DAYS} дня, "
        f"последний пересчёт: {last_text}. /badge sync выдаст их заново сразу.",
        "",
        block("💰 Деньги на руках", money, lambda n: f"{n} RU", money_err),
        "",
        block("💎 Ценных артефактов найдено", arts, lambda n: f"{n} шт.", arts_err),
        "",
        block(
            "🏦 Донаты проекту",
            donors,
            lambda n: f"{n}⭐ (~{int(stars_to_rub(n))} ₽)",
            donors_err,
        ),
        "",
        "Медали сейчас на:",
        f"• Богач Зоны — {holder_label('richest')}",
        f"• Собиратель — {holder_label('collector')}",
        f"• Главная опора — {holder_label('donor')}",
    ]
    return "\n".join(blocks)


def sync_player_medals(storage: Storage, telegram_id: int) -> None:
    """Пересчитать авто-медали одного игрока (не эксклюзивные топы)."""
    tid = int(telegram_id)
    player = storage.get_character(tid, refresh_energy=False)
    if player is None:
        return
    stats = storage.get_player_stats(tid)
    flags = get_medal_flags(storage, tid)

    joined = get_joined_at(storage, tid)
    is_beta_window = joined is None or joined <= BETA_DEADLINE
    if is_beta_window and int(stats.get("rating_points") or 0) >= BETA_MIN_RATING:
        grant_medal(storage, tid, "beta")

    leader_id = storage.get_faction_leader_id(str(player.faction or "")) if player.faction else None
    if leader_id == tid:
        grant_medal(storage, tid, "creator")
    else:
        revoke_medal(storage, tid, "creator")

    if flags.get("mentor"):
        grant_medal(storage, tid, "mentor")
    if flags.get("developer"):
        grant_medal(storage, tid, "developer")
    if int(flags.get("bugs") or 0) >= BUGS_FOR_FINDER:
        grant_medal(storage, tid, "finder")
    if int(flags.get("ideas") or 0) >= IDEAS_FOR_MEDAL:
        grant_medal(storage, tid, "idea")

    donated_rub = stars_to_rub(storage.total_stars_donated(tid))
    if donated_rub >= SUPPORT_MEDAL_RUB:
        grant_medal(storage, tid, "support")

    from app.game_logic import ACHIEVEMENT_RULES

    unlocked = storage.get_player_achievement_keys(tid)
    if len(ACHIEVEMENT_RULES) > 0 and len(unlocked) >= len(ACHIEVEMENT_RULES):
        grant_medal(storage, tid, "completionist")
    else:
        revoke_medal(storage, tid, "completionist")


def refresh_exclusive_and_rotating_medals(
    storage: Storage,
    *,
    force_rotating: bool = False,
    sync_all: bool = False,
) -> None:
    board = storage.get_rating_leaderboard(limit=1)
    top_id = None
    if board:
        try:
            top_id = int(board[0].get("telegram_id") or 0) or None
        except (TypeError, ValueError):
            top_id = None
    _exclusive_holder(storage, "top_all", top_id)

    podium = _load_json_meta(storage, SEASON_PODIUM_META_KEY)
    if podium:
        _apply_season_podium(storage, podium)

    rotating = _load_json_meta(storage, ROTATING_META_KEY)
    last = _parse_dt(str(rotating.get("at") or ""))
    now = utc_now()
    due = force_rotating or last is None or now - last >= timedelta(days=ROTATING_MEDAL_DAYS)
    if due:
        collector = storage.top_player_by_stat("artifacts_found")
        richest = storage.top_player_by_money()
        donor = storage.top_stars_donor()
        _exclusive_holder(storage, "collector", collector)
        _exclusive_holder(storage, "richest", richest)
        _exclusive_holder(storage, "main_support", donor)
        storage.set_meta(
            ROTATING_META_KEY,
            json.dumps(
                {
                    "at": now.isoformat(),
                    "collector": collector,
                    "richest": richest,
                    "donor": donor,
                },
                ensure_ascii=False,
            ),
        )

    if sync_all or force_rotating:
        for tid in storage.list_player_ids():
            sync_player_medals(storage, tid)
