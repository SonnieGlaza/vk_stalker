"""Еженедельные цели группировки."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.storage import Storage, utc_now

FACTION_GOAL_WEEKLY: tuple[tuple[str, str, int, int], ...] = (
    ("junk_deposit", "Сдать мусор на склад", "junk", 30),
    ("captures", "Захватить нейтральные точки", "enemy_bases_captured", 2),
    ("raids", "Завершить рейды", "raids_completed", 3),
)


def _week_key(when: datetime | None = None) -> str:
    dt = when or utc_now()
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def _meta_key(faction: str, week: str | None = None) -> str:
    return f"faction_goal:{week or _week_key()}:{faction}"


def _load_goal(storage: Storage, faction: str) -> dict[str, Any]:
    raw = storage.get_meta(_meta_key(faction))
    if not raw:
        return {"progress": {}, "reward_paid": False}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return {"progress": {}, "reward_paid": False}


def _save_goal(storage: Storage, faction: str, data: dict[str, Any]) -> None:
    storage.set_meta(_meta_key(faction), json.dumps(data, ensure_ascii=False))


def record_faction_goal_event(
    storage: Storage,
    faction: str | None,
    goal_key: str,
    *,
    amount: int = 1,
) -> None:
    if not faction or amount <= 0:
        return
    for key, _, _, _ in FACTION_GOAL_WEEKLY:
        if key != goal_key:
            continue
        data = _load_goal(storage, faction)
        progress = dict(data.get("progress") or {})
        progress[key] = int(progress.get(key, 0)) + int(amount)
        data["progress"] = progress
        _save_goal(storage, faction, data)
        return


def build_faction_goals_text(storage: Storage, faction: str) -> str:
    data = _load_goal(storage, faction)
    progress = dict(data.get("progress") or {})
    lines = [f"📅 Цели недели ({_week_key()})", ""]
    all_done = True
    for key, title, _stat, target in FACTION_GOAL_WEEKLY:
        current = int(progress.get(key, 0))
        done = current >= target
        mark = "✅" if done else "⬜"
        lines.append(f"{mark} {title}: {min(current, target)}/{target}")
        if not done:
            all_done = False
    if data.get("reward_paid"):
        lines.append("")
        lines.append("Награда за неделю уже выплачена в казну.")
    elif all_done:
        lines.append("")
        lines.append("Все цели выполнены — лидер может забрать бонус в меню группировки.")
    return "\n".join(lines)


def try_claim_faction_goal_reward(storage: Storage, faction: str, leader_id: int) -> tuple[bool, str]:
    if storage.get_faction_leader_id(faction) != int(leader_id):
        return False, "Бонус недели забирает только лидер."

    data = _load_goal(storage, faction)
    if data.get("reward_paid"):
        return False, "Награда за эту неделю уже выплачена."
    progress = dict(data.get("progress") or {})
    for key, _, _, target in FACTION_GOAL_WEEKLY:
        if int(progress.get(key, 0)) < target:
            return False, "Ещё не все цели недели выполнены."

    bonus = 15000
    storage.change_faction_treasury(faction, bonus)
    data["reward_paid"] = True
    _save_goal(storage, faction, data)
    return True, f"В казну «{faction}» зачислено {bonus} RU за цели недели."
