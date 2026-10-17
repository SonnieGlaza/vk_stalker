from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from app.storage import Storage

TABLES = (
    "factions",
    "locations",
    "characters",
    "alliances",
    "alliance_requests",
    "topup_payments",
    "faction_warehouse",
    "auctions",
    "raids",
    "raid_members",
    "war_lobbies",
    "war_lobby_members",
    "map_events",
    "player_stats",
    "player_achievements",
    "pending_registrations",
)

SQLITE_CANDIDATES = (
    "/data/stalker_game.db",
    "/app/stalker_game.db",
    "/workspace/stalker_game.db",
    "stalker_game.db",
)

SNAPSHOT_CANDIDATES = (
    "/data/stalker_game.backup.json",
    "/app/stalker_game.backup.json",
    "/workspace/stalker_game.backup.json",
    "stalker_game.backup.json",
)


def _find_first(paths: tuple[str, ...]) -> Path | None:
    for raw in paths:
        path = Path(raw)
        if path.exists() and path.is_file() and path.stat().st_size > 0:
            return path
    return None


def load_legacy_payload() -> tuple[dict[str, list[dict[str, Any]]], str]:
    """Load old data from SQLite file or JSON snapshot. Returns (payload, source_label)."""
    sqlite_path = _find_first(SQLITE_CANDIDATES)
    if sqlite_path is not None:
        payload: dict[str, list[dict[str, Any]]] = {name: [] for name in TABLES}
        with sqlite3.connect(sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            existing = {
                str(r["name"])
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            for table in TABLES:
                if table not in existing:
                    continue
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
                payload[table] = [dict(row) for row in rows]
        return payload, f"sqlite:{sqlite_path}"

    snapshot_path = _find_first(SNAPSHOT_CANDIDATES)
    if snapshot_path is not None:
        raw = json.loads(snapshot_path.read_text(encoding="utf-8"))
        payload = {table: [dict(row) for row in (raw.get(table) or [])] for table in TABLES}
        return payload, f"snapshot:{snapshot_path}"

    raise FileNotFoundError(
        "Старая база не найдена. Ожидал /data/stalker_game.db или /data/stalker_game.backup.json"
    )


def load_current_payload(storage: Storage) -> tuple[dict[str, list[dict[str, Any]]], str]:
    payload: dict[str, list[dict[str, Any]]] = {}
    with storage._connect() as conn:
        for table in TABLES:
            try:
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
            except Exception:
                rows = []
            payload[table] = [dict(row) for row in rows]
    label = "postgres" if storage.backend == "postgres" else f"sqlite:{storage.db_path}"
    return payload, f"current:{label}"


def build_players_export_files(
    characters: list[dict[str, Any]],
    stats_rows: list[dict[str, Any]],
    out_dir: Path,
) -> tuple[dict[str, Path], int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stats_by_id = {int(r.get("telegram_id", 0)): r for r in stats_rows}
    fieldnames = [
        "telegram_id",
        "player_uid",
        "nickname",
        "gender",
        "faction",
        "money",
        "health",
        "energy",
        "location",
        "gear_power",
        "quests_completed",
        "raids_completed",
        "wars_won",
        "money_earned",
        "rating_points",
    ]
    rows: list[dict[str, Any]] = []
    for ch in sorted(characters, key=lambda r: str(r.get("nickname") or "").casefold()):
        tid = int(ch.get("telegram_id") or 0)
        st = stats_by_id.get(tid, {})
        rows.append(
            {
                "telegram_id": tid,
                "player_uid": ch.get("player_uid") or "",
                "nickname": ch.get("nickname") or "",
                "gender": ch.get("gender") or "",
                "faction": ch.get("faction") or "",
                "money": int(ch.get("money") or 0),
                "health": int(ch.get("health") or 0),
                "energy": int(ch.get("energy") or 0),
                "location": ch.get("location") or "",
                "gear_power": int(ch.get("gear_power") or 0),
                "quests_completed": int(st.get("quests_completed") or 0),
                "raids_completed": int(st.get("raids_completed") or 0),
                "wars_won": int(st.get("wars_won") or 0),
                "money_earned": int(st.get("money_earned") or 0),
                "rating_points": int(st.get("rating_points") or 0),
            }
        )

    csv_path = out_dir / "old_players.csv"
    json_path = out_dir / "old_players.json"
    txt_path = out_dir / "old_players.txt"

    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"Игроки: {len(rows)}", ""]
    for row in rows:
        lines.append(
            f"{row['nickname']} | id={row['telegram_id']} | {row['faction'] or 'без гп'} | "
            f"{row['money']} RU | {row['location']} | rating={row['rating_points']}"
        )
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"csv": csv_path, "json": json_path, "txt": txt_path}, len(rows)


def clear_postgres_tables(storage: Storage) -> None:
    with storage._connect() as conn:
        for idx, table in enumerate(reversed(TABLES)):
            sp = f"sp_clear_{idx}"
            try:
                conn.savepoint(sp)
                conn.execute(f"DELETE FROM {table}")  # noqa: S608
                conn.release_savepoint(sp)
            except Exception:
                try:
                    conn.rollback_to_savepoint(sp)
                except Exception:
                    pass
                continue


def migrate_payload_to_storage(storage: Storage, payload: dict[str, list[dict[str, Any]]]) -> int:
    export_dir = Path("/tmp/stalker_export")
    export_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = export_dir / "migration.backup.json"
    body = {"version": 2, **payload}
    snapshot_path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    storage.snapshot_path = snapshot_path

    clear_postgres_tables(storage)
    with storage._connect() as conn:
        storage._restore_from_snapshot_if_needed(conn)
        storage._ensure_characters_schema(conn)
        storage._ensure_pending_registrations_schema(conn)
        storage._ensure_player_stats_rows(conn)
        storage._enforce_location_power_baseline(conn)
        storage._sync_serial_sequences(conn)

    with storage._connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM characters").fetchone()
        return int(row["cnt"] if isinstance(row, dict) else row[0])
