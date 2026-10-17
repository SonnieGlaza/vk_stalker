"""Админ-утилиты: сводка по игроку, алерты тиков."""

from __future__ import annotations

from app.storage import Storage


def build_admin_player_summary(storage: Storage, target: str) -> str:
    raw = (target or "").strip()
    if not raw:
        return "Укажи telegram_id или прозвище."

    if raw.isdigit():
        telegram_id = int(raw)
    else:
        found = storage.find_telegram_id_by_nickname(raw)
        if found is None:
            return f"Игрок «{raw}» не найден."
        telegram_id = found

    player = storage.get_character(telegram_id, refresh_energy=False)
    if player is None:
        return "Персонаж не найден."

    from app.player_busy import player_busy_reason

    busy = player_busy_reason(storage, telegram_id) or "свободен"
    stats = storage.get_player_stats(telegram_id)

    lines = [
        f"👤 {player.nickname} (id {telegram_id})",
        f"ГП: {player.faction or '—'} | звание: {player.faction_rank or '—'}",
        f"Локация: {player.location}",
        f"HP: {player.health} | энергия: {player.energy}/{player.max_energy}",
        f"RU: {player.money} | сила: {player.gear_power}",
        f"Статус: {busy}",
    ]
    if player.active_contract_json:
        lines.append(f"Контракт: {player.active_contract_json[:120]}…")
    if player.travel_destination:
        lines.append(f"Переход → {player.travel_destination}")
    lines.extend(
        [
            "",
            "📊 Статистика:",
            f"Контракты: ✅{stats['quests_completed']} / ❌{stats['quests_failed']}",
            f"Рейды: ✅{stats['raids_completed']} / ❌{stats['raids_failed']}",
            f"Смерти: {stats['deaths']} | артефакты: {stats['artifacts_found']}",
            f"Рейтинг: {stats['rating_points']} | сезон: {stats['season_rating']}",
        ]
    )
    return "\n".join(lines)
