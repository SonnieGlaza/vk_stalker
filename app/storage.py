from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.db import DbConfig, DbConnection, connect as db_connect, integrity_error_types

logger = logging.getLogger(__name__)

ENERGY_REGEN_PER_MINUTE = 2
BASE_LOCATION_NPC_POWER = 100
REGULAR_LOCATION_NPC_POWER = 60


class NicknameTakenError(ValueError):
    """Прозвище уже занято другим персонажем."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_player_uid(telegram_id: int) -> str:
    return f"STK-{telegram_id}"


@dataclass
class Character:
    telegram_id: int
    player_uid: str
    avatar_style: str
    nickname: str
    gender: str
    faction: str | None
    faction_rank: str | None
    money: int
    energy: int
    max_energy: int
    health: int
    gear_power: int
    location: str
    inventory: dict[str, int]
    equipment: dict[str, Any]
    truck_owned: bool
    truck_durability: int
    niva_owned: bool
    niva_durability: int
    bicycle_owned: bool
    sleeping_bag_owned: bool
    diesel: int
    gasoline: int
    travel_destination: str | None
    travel_arrives_at: datetime | None
    travel_transport: str | None
    active_contract_json: str | None
    energy_updated_at: datetime
    radiation: int
    hunger: int
    thirst: int
    needs_updated_at: datetime
    survival_damage_at: datetime


SURVIVAL_HOURLY_GAIN = 1
SURVIVAL_DAMAGE_PER_TICK = 10
SURVIVAL_DAMAGE_TICK_MINUTES = 30
SURVIVAL_NEED_MAX = 100  # голод / жажда / радиация
SURVIVAL_CRITICAL_NEED = 100  # голод/жажда/радиация ≥ порога → урон HP


def _survival_immune(telegram_id: int) -> bool:
    """Персонаж с иммунитетом (телеграм-владелец) не умирает от выживания."""
    try:
        from app.game_logic import owner_survival_immune

        return owner_survival_immune(telegram_id)
    except Exception:
        return False

# Telegram user id > 2^31-1 → Postgres INTEGER overflow. Everywhere BIGINT.
TELEGRAM_ID_COLUMNS: tuple[tuple[str, str], ...] = (
    ("characters", "telegram_id"),
    ("factions", "leader_id"),
    ("alliance_requests", "proposed_by"),
    ("topup_payments", "telegram_id"),
    ("auctions", "seller_id"),
    ("auctions", "buyer_id"),
    ("raids", "leader_id"),
    ("raid_members", "telegram_id"),
    ("war_lobbies", "leader_id"),
    ("war_lobby_members", "telegram_id"),
    ("player_stats", "telegram_id"),
    ("player_achievements", "telegram_id"),
    ("pending_registrations", "telegram_id"),
)


class Storage:
    def __init__(
        self,
        db_path: str,
        snapshot_path: str | None = None,
        database_url: str | None = None,
    ) -> None:
        self.db_path = db_path
        self.database_url = (database_url or "").strip() or None
        if self.database_url:
            self.backend = "postgres"
            # Snapshot рядом с volume /data, а не "рядом с URL".
            default_snapshot = Path("/data/stalker_game.backup.json")
        else:
            self.backend = "sqlite"
            default_snapshot = Path(db_path).with_suffix(".backup.json")
            db_parent = Path(db_path).parent
            try:
                db_parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

        requested = Path(snapshot_path) if snapshot_path else default_snapshot
        self.snapshot_path = self._resolve_writable_snapshot_path(requested)
        self._snapshot_last_at: float = 0.0
        self._snapshot_min_interval_sec: float = 15.0
        self._meta_schema_ready = False

    @staticmethod
    def _resolve_writable_snapshot_path(preferred: Path) -> Path:
        fallbacks = [
            preferred,
            Path("/tmp/stalker_game.backup.json"),
            Path("stalker_game.backup.json"),
        ]
        seen: set[Path] = set()
        for candidate in fallbacks:
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                candidate.parent.mkdir(parents=True, exist_ok=True)
                probe = candidate.parent / ".stalker_write_probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
                return candidate
            except OSError:
                continue
        return preferred

    def _db_config(self) -> DbConfig:
        if self.backend == "postgres":
            return DbConfig(backend="postgres", database_url=self.database_url)
        return DbConfig(backend="sqlite", sqlite_path=self.db_path)

    def _connect(self) -> DbConnection:
        return db_connect(self._db_config())

    @staticmethod
    def _stmt_rowcount(cursor: Any) -> int:
        return max(0, int(getattr(cursor, "rowcount", 0) or 0))

    def _ensure_meta_kv_schema(self, conn: DbConnection) -> None:
        if self._meta_schema_ready:
            return
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta_kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._meta_schema_ready = True

    def _sync_serial_sequences(self, conn: DbConnection) -> None:
        if conn.backend != "postgres":
            return
        for table in ("auctions", "raids", "war_lobbies"):
            conn.execute(
                f"""
                SELECT setval(
                    pg_get_serial_sequence('{table}', 'id'),
                    COALESCE((SELECT MAX(id) FROM {table}), 1),
                    true
                )
                """  # noqa: S608
            )

    def _table_columns(self, conn: DbConnection, table_name: str) -> set[str]:
        if conn.backend == "postgres":
            rows = conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = ?
                """,
                (table_name,),
            ).fetchall()
            return {str(row["column_name"]) for row in rows}
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows}

    def _insert_returning_id(self, conn: DbConnection, sql: str, params: tuple[Any, ...]) -> int:
        if conn.backend == "postgres":
            statement = sql.rstrip().rstrip(";") + " RETURNING id"
            row = conn.execute(statement, params).fetchone()
            if row is None:
                raise RuntimeError("INSERT RETURNING id returned no row")
            return int(row["id"])
        cursor = conn.execute(sql, params)
        return int(cursor.lastrowid)

    def _write_snapshot(self) -> None:
        try:
            with self._connect() as conn:
                characters = [dict(row) for row in conn.execute("SELECT * FROM characters").fetchall()]
                factions = [dict(row) for row in conn.execute("SELECT * FROM factions").fetchall()]
                locations = [dict(row) for row in conn.execute("SELECT * FROM locations").fetchall()]
                alliances = [dict(row) for row in conn.execute("SELECT * FROM alliances").fetchall()]
                alliance_requests = [dict(row) for row in conn.execute("SELECT * FROM alliance_requests").fetchall()]
                topup_payments = [dict(row) for row in conn.execute("SELECT * FROM topup_payments").fetchall()]
                faction_warehouse = [dict(row) for row in conn.execute("SELECT * FROM faction_warehouse").fetchall()]
                auctions = [dict(row) for row in conn.execute("SELECT * FROM auctions").fetchall()]
                raids = [dict(row) for row in conn.execute("SELECT * FROM raids").fetchall()]
                raid_members = [dict(row) for row in conn.execute("SELECT * FROM raid_members").fetchall()]
                war_lobbies = [dict(row) for row in conn.execute("SELECT * FROM war_lobbies").fetchall()]
                war_lobby_members = [
                    dict(row) for row in conn.execute("SELECT * FROM war_lobby_members").fetchall()
                ]
                map_events = [dict(row) for row in conn.execute("SELECT * FROM map_events").fetchall()]
                player_stats = [dict(row) for row in conn.execute("SELECT * FROM player_stats").fetchall()]
                player_achievements = [
                    dict(row) for row in conn.execute("SELECT * FROM player_achievements").fetchall()
                ]
                meta_kv = [dict(row) for row in conn.execute("SELECT * FROM meta_kv").fetchall()]
                pending_registrations: list[dict[str, Any]] = []
                conn.savepoint("sp_pending_snap")
                try:
                    self._ensure_pending_registrations_schema(conn)
                    pending_registrations = [
                        dict(row) for row in conn.execute("SELECT * FROM pending_registrations").fetchall()
                    ]
                    conn.release_savepoint("sp_pending_snap")
                except Exception:
                    conn.rollback_to_savepoint("sp_pending_snap")
                    pending_registrations = []
            payload = {
                "version": 3,
                "characters": characters,
                "factions": factions,
                "locations": locations,
                "alliances": alliances,
                "alliance_requests": alliance_requests,
                "topup_payments": topup_payments,
                "faction_warehouse": faction_warehouse,
                "auctions": auctions,
                "raids": raids,
                "raid_members": raid_members,
                "war_lobbies": war_lobbies,
                "war_lobby_members": war_lobby_members,
                "map_events": map_events,
                "player_stats": player_stats,
                "player_achievements": player_achievements,
                "meta_kv": meta_kv,
                "pending_registrations": pending_registrations,
            }
            self.snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        except Exception:
            # Не ломаем игру, если backup временно недоступен или данные не сериализуются.
            logger.exception("Failed to write player snapshot to %s", self.snapshot_path)
            return

    def _restore_from_snapshot_if_needed(self, conn: DbConnection) -> None:
        count_row = conn.execute("SELECT COUNT(*) AS cnt FROM characters").fetchone()
        existing_count = int(count_row["cnt"]) if count_row else 0
        if existing_count > 0:
            return
        if not self.snapshot_path.exists():
            return
        try:
            payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        characters = payload.get("characters") or []
        factions = payload.get("factions") or []
        locations = payload.get("locations") or []
        alliances = payload.get("alliances") or []
        alliance_requests = payload.get("alliance_requests") or []
        topup_payments = payload.get("topup_payments") or []
        faction_warehouse = payload.get("faction_warehouse") or []
        auctions = payload.get("auctions") or []
        raids = payload.get("raids") or []
        raid_members = payload.get("raid_members") or []
        war_lobbies = payload.get("war_lobbies") or []
        war_lobby_members = payload.get("war_lobby_members") or []
        map_events = payload.get("map_events") or []
        player_stats = payload.get("player_stats") or []
        player_achievements = payload.get("player_achievements") or []
        meta_kv = payload.get("meta_kv") or []
        pending_registrations = payload.get("pending_registrations") or []
        if not characters:
            return

        self._ensure_raids_schema(conn)
        for row in factions:
            conn.execute(
                """
                INSERT OR REPLACE INTO factions(name, treasury, leader_id)
                VALUES(?, ?, ?)
                """,
                (
                    row.get("name"),
                    int(row.get("treasury", 0)),
                    row.get("leader_id"),
                ),
            )
        for row in locations:
            conn.execute(
                """
                INSERT OR REPLACE INTO locations(name, point_type, controlled_by, npc_power, defense_bonus)
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    row.get("name"),
                    row.get("point_type"),
                    row.get("controlled_by"),
                    int(row.get("npc_power", REGULAR_LOCATION_NPC_POWER)),
                    int(row.get("defense_bonus", 0)),
                ),
            )
        for row in alliances:
            conn.execute(
                """
                INSERT OR IGNORE INTO alliances(faction_a, faction_b, created_at)
                VALUES(?, ?, ?)
                """,
                (
                    row.get("faction_a"),
                    row.get("faction_b"),
                    row.get("created_at") or utc_now().isoformat(),
                ),
            )
        for row in alliance_requests:
            conn.execute(
                """
                INSERT OR IGNORE INTO alliance_requests(requester_faction, target_faction, proposed_by, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (
                    row.get("requester_faction"),
                    row.get("target_faction"),
                    row.get("proposed_by"),
                    row.get("created_at") or utc_now().isoformat(),
                ),
            )
        for row in characters:
            now_iso = utc_now().isoformat()
            conn.execute(
                """
                INSERT OR REPLACE INTO characters(
                    telegram_id, player_uid, avatar_style, nickname, gender, faction, faction_rank, money,
                    energy, max_energy, energy_updated_at, health, gear_power, location,
                    inventory_json, equipment_json, truck_owned, truck_durability, sleeping_bag_owned, fuel,
                    radiation, hunger, thirst, needs_updated_at, survival_damage_at,
                    niva_owned, niva_durability, bicycle_owned, diesel, gasoline, stash_json,
                    active_contract_json, travel_destination, travel_arrives_at, travel_transport
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(row.get("telegram_id")),
                    row.get("player_uid") or build_player_uid(int(row.get("telegram_id"))),
                    row.get("avatar_style") or "classic",
                    row.get("nickname") or "Сталкер",
                    row.get("gender") or "Мужской",
                    row.get("faction"),
                    row.get("faction_rank"),
                    int(row.get("money", 3000)),
                    int(row.get("energy", 100)),
                    int(row.get("max_energy", 100)),
                    row.get("energy_updated_at") or now_iso,
                    int(row.get("health", 100)),
                    int(row.get("gear_power", 2)),
                    row.get("location") or "База новичков",
                    row.get("inventory_json") or "{}",
                    row.get("equipment_json")
                    or '{"weapon":"Нож","armor":"Куртка новичка","weapon_durability":100,"armor_durability":100}',
                    int(row.get("truck_owned", 0)),
                    int(row.get("truck_durability", 100 if int(row.get("truck_owned", 0)) else 0)),
                    int(row.get("sleeping_bag_owned", 0)),
                    int(row.get("fuel", row.get("diesel", 0)) or 0),
                    int(row.get("radiation", 0)),
                    int(row.get("hunger", 0)),
                    int(row.get("thirst", 0)),
                    row.get("needs_updated_at") or now_iso,
                    row.get("survival_damage_at") or now_iso,
                    int(row.get("niva_owned", 0)),
                    int(row.get("niva_durability", 100 if int(row.get("niva_owned", 0)) else 0)),
                    int(row.get("bicycle_owned", 0)),
                    int(row.get("diesel", row.get("fuel", 0)) or 0),
                    int(row.get("gasoline", 0)),
                    row.get("stash_json") or "{}",
                    row.get("active_contract_json"),
                    row.get("travel_destination"),
                    row.get("travel_arrives_at"),
                    row.get("travel_transport"),
                ),
            )
        for row in topup_payments:
            conn.execute(
                """
                INSERT OR REPLACE INTO topup_payments(
                    payment_charge_id, telegram_id, stars_amount, ru_amount, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row.get("payment_charge_id"),
                    int(row.get("telegram_id")),
                    int(row.get("stars_amount", 0)),
                    int(row.get("ru_amount", 0)),
                    row.get("created_at") or utc_now().isoformat(),
                ),
            )
        for row in faction_warehouse:
            conn.execute(
                """
                INSERT OR REPLACE INTO faction_warehouse(faction, item_key, amount)
                VALUES(?, ?, ?)
                """,
                (
                    row.get("faction"),
                    row.get("item_key"),
                    int(row.get("amount", 0)),
                ),
            )
        for row in auctions:
            conn.execute(
                """
                INSERT OR REPLACE INTO auctions(
                    id, seller_id, faction, item_key, amount, price, status, buyer_id, created_at, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(row.get("id")),
                    int(row.get("seller_id")),
                    row.get("faction"),
                    row.get("item_key"),
                    int(row.get("amount", 1)),
                    int(row.get("price", 0)),
                    row.get("status") or "open",
                    row.get("buyer_id"),
                    row.get("created_at") or utc_now().isoformat(),
                    row.get("closed_at"),
                ),
            )
        for row in raids:
            conn.execute(
                """
                INSERT OR REPLACE INTO raids(
                    id, faction, location, leader_id, status, created_at, started_at, finished_at, result_text,
                    raid_kind, target_faction
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(row.get("id")),
                    row.get("faction"),
                    row.get("location"),
                    int(row.get("leader_id")),
                    row.get("status") or "open",
                    row.get("created_at") or utc_now().isoformat(),
                    row.get("started_at"),
                    row.get("finished_at"),
                    row.get("result_text"),
                    row.get("raid_kind") or "lair",
                    row.get("target_faction"),
                ),
            )
        for row in raid_members:
            conn.execute(
                """
                INSERT OR REPLACE INTO raid_members(raid_id, telegram_id, joined_at)
                VALUES(?, ?, ?)
                """,
                (
                    int(row.get("raid_id")),
                    int(row.get("telegram_id")),
                    row.get("joined_at") or utc_now().isoformat(),
                ),
            )
        for row in war_lobbies:
            conn.execute(
                """
                INSERT OR REPLACE INTO war_lobbies(
                    id, host_faction, location, leader_id, status, created_at, started_at, finished_at, result_text
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(row.get("id")),
                    row.get("host_faction"),
                    row.get("location"),
                    int(row.get("leader_id")),
                    row.get("status") or "open",
                    row.get("created_at") or utc_now().isoformat(),
                    row.get("started_at"),
                    row.get("finished_at"),
                    row.get("result_text"),
                ),
            )
        for row in war_lobby_members:
            conn.execute(
                """
                INSERT OR REPLACE INTO war_lobby_members(war_id, telegram_id, joined_at)
                VALUES(?, ?, ?)
                """,
                (
                    int(row.get("war_id")),
                    int(row.get("telegram_id")),
                    row.get("joined_at") or utc_now().isoformat(),
                ),
            )
        for row in map_events:
            conn.execute(
                """
                INSERT OR REPLACE INTO map_events(
                    location, event_type, modifier, description, expires_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("location"),
                    row.get("event_type"),
                    int(row.get("modifier", 0)),
                    row.get("description") or "",
                    row.get("expires_at") or utc_now().isoformat(),
                    row.get("updated_at") or utc_now().isoformat(),
                ),
            )
        for row in player_stats:
            conn.execute(
                """
                INSERT OR REPLACE INTO player_stats(
                    telegram_id, quests_completed, quests_failed, raids_completed, raids_failed,
                    wars_won, enemy_bases_captured, smuggling_success, trades_done, money_earned, artifacts_found,
                    deaths, rating_points, achievements_unlocked, season_rating, radio_helps
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(row.get("telegram_id")),
                    int(row.get("quests_completed", 0)),
                    int(row.get("quests_failed", 0)),
                    int(row.get("raids_completed", 0)),
                    int(row.get("raids_failed", 0)),
                    int(row.get("wars_won", 0)),
                    int(row.get("enemy_bases_captured", 0)),
                    int(row.get("smuggling_success", 0)),
                    int(row.get("trades_done", 0)),
                    int(row.get("money_earned", 0)),
                    int(row.get("artifacts_found", 0)),
                    int(row.get("deaths", 0)),
                    int(row.get("rating_points", 0)),
                    int(row.get("achievements_unlocked", 0)),
                    int(row.get("season_rating", 0)),
                    int(row.get("radio_helps", 0)),
                ),
            )
        for row in player_achievements:
            conn.execute(
                """
                INSERT OR REPLACE INTO player_achievements(
                    telegram_id, achievement_key, unlocked_at
                ) VALUES (?, ?, ?)
                """,
                (
                    int(row.get("telegram_id")),
                    row.get("achievement_key"),
                    row.get("unlocked_at") or utc_now().isoformat(),
                ),
            )
        for row in meta_kv:
            key = str(row.get("key") or "").strip()
            if not key:
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO meta_kv(key, value) VALUES (?, ?)
                """,
                (key, str(row.get("value") or "")),
            )
        self._ensure_pending_registrations_schema(conn)
        for row in pending_registrations:
            nick = str(row.get("nickname") or "").strip()
            if not nick:
                continue
            gender = str(row.get("gender") or "").strip() or None
            step = str(
                row.get("registration_step") or row.get("step") or ""
            ).strip() or ("faction" if gender else "gender")
            now_iso = utc_now().isoformat()
            conn.execute(
                """
                INSERT INTO pending_registrations(telegram_id, nickname, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET nickname = EXCLUDED.nickname
                """,
                (
                    int(row.get("telegram_id")),
                    nick,
                    row.get("created_at") or now_iso,
                ),
            )
            columns = self._table_columns(conn, "pending_registrations")
            tid = int(row.get("telegram_id"))
            if "gender" in columns:
                conn.execute(
                    "UPDATE pending_registrations SET gender = ? WHERE telegram_id = ?",
                    (gender, tid),
                )
            if "registration_step" in columns:
                conn.execute(
                    "UPDATE pending_registrations SET registration_step = ? WHERE telegram_id = ?",
                    (step, tid),
                )
            if "updated_at" in columns:
                conn.execute(
                    "UPDATE pending_registrations SET updated_at = ? WHERE telegram_id = ?",
                    (row.get("updated_at") or now_iso, tid),
                )
        # Backfill survival columns / UIDs after restore, then fix SERIAL counters.
        self._ensure_characters_schema(conn)
        self._sync_serial_sequences(conn)

    def save_snapshot(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._snapshot_last_at) < self._snapshot_min_interval_sec:
            return
        self._snapshot_last_at = now
        self._write_snapshot()

    def restore_from_snapshot(self) -> None:
        with self._connect() as conn:
            self._restore_from_snapshot_if_needed(conn)

    # Backward-compatible alias for older bot startup code.
    def restore_from_snapshot_if_empty(self) -> None:
        self.restore_from_snapshot()

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS characters (
                    telegram_id BIGINT PRIMARY KEY,
                    player_uid TEXT UNIQUE,
                    avatar_style TEXT NOT NULL DEFAULT 'classic',
                    nickname TEXT NOT NULL,
                    gender TEXT NOT NULL,
                    faction TEXT,
                    faction_rank TEXT,
                    money INTEGER NOT NULL DEFAULT 3000,
                    energy INTEGER NOT NULL DEFAULT 100,
                    max_energy INTEGER NOT NULL DEFAULT 100,
                    energy_updated_at TEXT NOT NULL,
                    health INTEGER NOT NULL DEFAULT 100,
                    gear_power INTEGER NOT NULL DEFAULT 2,
                    location TEXT NOT NULL DEFAULT 'База новичков',
                    inventory_json TEXT NOT NULL DEFAULT '{}',
                    equipment_json TEXT NOT NULL DEFAULT '{"weapon":"ПМ","armor":"Куртка новичка","weapon_durability":100,"armor_durability":100}',
                    truck_owned INTEGER NOT NULL DEFAULT 0,
                    truck_durability INTEGER NOT NULL DEFAULT 0,
                    niva_durability INTEGER NOT NULL DEFAULT 0,
                    sleeping_bag_owned INTEGER NOT NULL DEFAULT 0,
                    fuel INTEGER NOT NULL DEFAULT 0,
                    radiation INTEGER NOT NULL DEFAULT 0,
                    hunger INTEGER NOT NULL DEFAULT 0,
                    thirst INTEGER NOT NULL DEFAULT 0,
                    needs_updated_at TEXT,
                    survival_damage_at TEXT
                )
                """
            )
            self._ensure_characters_schema(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS factions (
                    name TEXT PRIMARY KEY,
                    treasury INTEGER NOT NULL DEFAULT 20000,
                    leader_id BIGINT
                )
                """
            )
            columns = self._table_columns(conn, "factions")
            if "leader_id" not in columns:
                conn.execute("ALTER TABLE factions ADD COLUMN leader_id BIGINT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS locations (
                    name TEXT PRIMARY KEY,
                    point_type TEXT NOT NULL,
                    controlled_by TEXT,
                    npc_power INTEGER NOT NULL DEFAULT 60,
                    defense_bonus INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            location_columns = self._table_columns(conn, "locations")
            if "defense_bonus" not in location_columns:
                conn.execute(
                    "ALTER TABLE locations ADD COLUMN defense_bonus INTEGER NOT NULL DEFAULT 0"
                )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alliance_requests (
                    requester_faction TEXT NOT NULL,
                    target_faction TEXT NOT NULL,
                    proposed_by BIGINT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(requester_faction, target_faction)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alliances (
                    faction_a TEXT NOT NULL,
                    faction_b TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(faction_a, faction_b)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS topup_payments (
                    payment_charge_id TEXT PRIMARY KEY,
                    telegram_id BIGINT NOT NULL,
                    stars_amount INTEGER NOT NULL,
                    ru_amount INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS faction_warehouse (
                    faction TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(faction, item_key)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auctions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    seller_id BIGINT NOT NULL,
                    faction TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    price INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    buyer_id BIGINT,
                    created_at TEXT NOT NULL,
                    closed_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS raids (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    faction TEXT NOT NULL,
                    location TEXT NOT NULL,
                    leader_id BIGINT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    result_text TEXT,
                    raid_kind TEXT NOT NULL DEFAULT 'lair',
                    target_faction TEXT
                )
                """
            )
            self._ensure_raids_schema(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS raid_members (
                    raid_id INTEGER NOT NULL,
                    telegram_id BIGINT NOT NULL,
                    joined_at TEXT NOT NULL,
                    PRIMARY KEY(raid_id, telegram_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS war_lobbies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host_faction TEXT NOT NULL,
                    location TEXT NOT NULL,
                    leader_id BIGINT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    result_text TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS war_lobby_members (
                    war_id INTEGER NOT NULL,
                    telegram_id BIGINT NOT NULL,
                    joined_at TEXT NOT NULL,
                    PRIMARY KEY(war_id, telegram_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS map_events (
                    location TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    modifier INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS player_stats (
                    telegram_id BIGINT PRIMARY KEY,
                    quests_completed INTEGER NOT NULL DEFAULT 0,
                    quests_failed INTEGER NOT NULL DEFAULT 0,
                    raids_completed INTEGER NOT NULL DEFAULT 0,
                    raids_failed INTEGER NOT NULL DEFAULT 0,
                    wars_won INTEGER NOT NULL DEFAULT 0,
                    enemy_bases_captured INTEGER NOT NULL DEFAULT 0,
                    smuggling_success INTEGER NOT NULL DEFAULT 0,
                    trades_done INTEGER NOT NULL DEFAULT 0,
                    money_earned INTEGER NOT NULL DEFAULT 0,
                    artifacts_found INTEGER NOT NULL DEFAULT 0,
                    deaths INTEGER NOT NULL DEFAULT 0,
                    rating_points INTEGER NOT NULL DEFAULT 0,
                    achievements_unlocked INTEGER NOT NULL DEFAULT 0,
                    season_rating INTEGER NOT NULL DEFAULT 0,
                    radio_helps INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS player_achievements (
                    telegram_id BIGINT NOT NULL,
                    achievement_key TEXT NOT NULL,
                    unlocked_at TEXT NOT NULL,
                    PRIMARY KEY(telegram_id, achievement_key)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta_kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            # Черновик регистрации: ник/пол живут в БД, а не только в MemoryStorage FSM.
            self._ensure_pending_registrations_schema(conn)
            self._ensure_referrals_schema(conn)
            self._ensure_bigint_telegram_ids(conn)
            self._ensure_unique_nicknames(conn)
            conn.executemany(
                "INSERT OR IGNORE INTO factions(name, treasury, leader_id) VALUES(?, ?, NULL)",
                [
                    ("Долг", 20000),
                    ("Свобода", 20000),
                    ("Нейтралы", 20000),
                    ("Бандиты", 20000),
                    ("Монолит", 20000),
                ],
            )
            conn.executemany(
                "INSERT OR IGNORE INTO locations(name, point_type, controlled_by, npc_power) VALUES(?, ?, ?, ?)",
                [
                    ("Росток", "база", "Долг", BASE_LOCATION_NPC_POWER),
                    ("Кордон", "база", "Нейтралы", BASE_LOCATION_NPC_POWER),
                    ("Армейские склады", "база", "Свобода", BASE_LOCATION_NPC_POWER),
                    ("Янтарь", "точка ресурсов", None, REGULAR_LOCATION_NPC_POWER),
                    ("Свалка", "база", "Бандиты", BASE_LOCATION_NPC_POWER),
                    ("Болото", "точка ресурсов", None, REGULAR_LOCATION_NPC_POWER),
                    ("НИИ Агропром", "точка интереса", None, REGULAR_LOCATION_NPC_POWER),
                    ("Темная долина", "точка интереса", "Долг", REGULAR_LOCATION_NPC_POWER),
                    ("Рыжий лес", "точка интереса", None, REGULAR_LOCATION_NPC_POWER),
                    ("Радар", "точка интереса", None, REGULAR_LOCATION_NPC_POWER),
                    ("Припять", "точка интереса", None, REGULAR_LOCATION_NPC_POWER),
                    ("ЧАЭС", "база", "Монолит", BASE_LOCATION_NPC_POWER),
                    ("Тунель", "точка ресурсов", None, REGULAR_LOCATION_NPC_POWER),
                ],
            )
            # Для существующих БД фиксируем базовые владельцы и типы ключевых точек.
            conn.execute(
                "UPDATE locations SET point_type = 'база', controlled_by = 'Нейтралы' WHERE name = 'Кордон'"
            )
            conn.execute(
                "UPDATE locations SET point_type = 'база', controlled_by = 'Бандиты' WHERE name = 'Свалка'"
            )
            conn.execute(
                "UPDATE locations SET point_type = 'база', controlled_by = 'Монолит' WHERE name = 'ЧАЭС'"
            )
            conn.execute(
                "UPDATE locations SET point_type = 'точка ресурсов' WHERE name = 'Тунель'"
            )
            conn.execute(
                "UPDATE locations SET controlled_by = 'Долг' WHERE name = 'Темная долина'"
            )
            self._restore_from_snapshot_if_needed(conn)
            self._ensure_characters_schema(conn)
            self._ensure_player_stats_schema(conn)
            self._sync_serial_sequences(conn)
            self._enforce_location_power_baseline(conn)
            self._ensure_player_stats_rows(conn)

    def create_character(self, telegram_id: int, nickname: str, gender: str) -> None:
        from app.game_logic import STARTING_EQUIPMENT_JSON, STARTING_INVENTORY, STARTING_MONEY_RU

        player_uid = build_player_uid(telegram_id)
        now_iso = utc_now().isoformat()
        default_equipment = STARTING_EQUIPMENT_JSON
        starter_inventory = json.dumps(STARTING_INVENTORY, ensure_ascii=False)
        nick = (nickname or "").strip()
        gen = (gender or "").strip()
        if not nick:
            raise ValueError("nickname is empty")
        if not gen:
            raise ValueError("gender is empty")
        if self.is_nickname_taken(nick, exclude_telegram_id=telegram_id):
            raise NicknameTakenError(nick)

        with self._connect() as schema_conn:
            self._ensure_pending_registrations_schema(schema_conn)
            # Отдельная транзакция: падение CREATE INDEX на Postgres
            # не должно abort'ить INSERT персонажа.
            self._ensure_characters_schema(schema_conn)
            self._ensure_bigint_telegram_ids(schema_conn)
            self._ensure_unique_nicknames(schema_conn)

        with self._connect() as conn:
            # Убираем коллизии player_uid у других аккаунтов — иначе INSERT падает
            # по UNIQUE, а ON CONFLICT(telegram_id) это не ловит.
            conn.execute(
                """
                UPDATE characters
                SET player_uid = NULL
                WHERE player_uid = ? AND telegram_id <> ?
                """,
                (player_uid, telegram_id),
            )
            existing = conn.execute(
                "SELECT telegram_id FROM characters WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
            if existing is not None:
                conn.execute(
                    """
                    UPDATE characters
                    SET nickname = ?, gender = ?, player_uid = COALESCE(NULLIF(TRIM(player_uid), ''), ?)
                    WHERE telegram_id = ?
                    """,
                    (nick, gen, player_uid, telegram_id),
                )
            else:
                if conn.backend == "postgres":
                    conn.savepoint("sp_create_character")
                try:
                    conn.execute(
                        """
                        INSERT INTO characters(
                            telegram_id, player_uid, avatar_style, nickname, gender,
                            money, energy, max_energy, energy_updated_at, health, gear_power, location,
                            inventory_json, equipment_json, truck_owned, truck_durability, sleeping_bag_owned, fuel,
                            radiation, hunger, thirst, needs_updated_at, survival_damage_at
                        ) VALUES(
                            ?, ?, 'classic', ?, ?,
                            ?, 100, 100, ?, 100, 2, 'База новичков',
                            ?, ?, 0, 0, 0, 0,
                            0, 0, 0, ?, ?
                        )
                        """,
                        (
                            telegram_id,
                            player_uid,
                            nick,
                            gen,
                            int(STARTING_MONEY_RU),
                            now_iso,
                            starter_inventory,
                            default_equipment,
                            now_iso,
                            now_iso,
                        ),
                    )
                    if conn.backend == "postgres":
                        conn.release_savepoint("sp_create_character")
                except integrity_error_types():
                    if conn.backend == "postgres":
                        conn.rollback_to_savepoint("sp_create_character")
                    # Конфликт мог быть и по нику (unique index), и по telegram_id.
                    if self.is_nickname_taken(nick, exclude_telegram_id=telegram_id):
                        raise NicknameTakenError(nick)
                    conn.execute(
                        """
                        UPDATE characters
                        SET nickname = ?, gender = ?, player_uid = ?
                        WHERE telegram_id = ?
                        """,
                        (nick, gen, player_uid, telegram_id),
                    )
            self._ensure_player_stats_row(conn, telegram_id)
            saved = conn.execute(
                "SELECT 1 AS ok FROM characters WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
            if saved is None:
                raise RuntimeError(f"create_character failed for telegram_id={telegram_id}")
            conn.execute(
                "DELETE FROM pending_registrations WHERE telegram_id = ?",
                (telegram_id,),
            )
        if not self.get_meta(f"player:joined:{int(telegram_id)}"):
            self.set_meta(f"player:joined:{int(telegram_id)}", now_iso)
        self.save_snapshot()
        self.sync_gear_power(telegram_id)

    def _purge_player_meta_keys(self, telegram_id: int) -> None:
        tid = int(telegram_id)
        exact_keys = [
            f"quest_mission:{tid}",
            f"smuggle_mission:{tid}",
            f"artifact_hunt:{tid}",
            f"smuggle:active:{tid}",
            f"duel:grid:player:{tid}",
            f"rgrid:player:{tid}",
            f"cwar:player:{tid}",
            f"arena:player:{tid}",
            f"ncap:player:{tid}",
            f"coop:player:{tid}",
            f"pending_referrer:{tid}",
            f"last_arrival_transport:{tid}",
            f"bound_transport:{tid}",
            f"arrival_notice:{tid}",
            f"travel_eta_msg:{tid}",
            f"respawn:debt:{tid}",
            f"death:last_cause:{tid}",
            f"death:last_killer:{tid}",
            f"artifact_find_log:{tid}",
            f"death_log:{tid}",
            f"death_notice_sent:{tid}",
            f"duel:pending_in:{tid}",
            f"duel:pending_out:{tid}",
            f"login:streak:{tid}",
            f"login:hint_shown:{tid}",
            f"notify:prefs:{tid}",
            f"tutorial:seen:{tid}",
            f"trader:weapon_tier:{tid}",
            f"vendor:barkeep_tier:{tid}",
            f"vendor:medic_tier:{tid}",
            f"vendor:tech_tier:{tid}",
            f"vendor_rep:barkeep:{tid}",
            f"vendor_rep:medic:{tid}",
            f"vendor_rep:tech:{tid}",
            f"pmedals:{tid}",
            f"pmedal_flags:{tid}",
            f"player:joined:{tid}",
            f"chat_medal:{tid}",
        ]
        like_prefixes = (
            f"contracts:daily_done:{tid}:",
            f"contracts:weekly_done:{tid}:",
            f"daily:claimed:{tid}:",
            f"clan_quest:claim:%:{tid}",
        )
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta_kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            for key in exact_keys:
                conn.execute("DELETE FROM meta_kv WHERE key = ?", (key,))
            for prefix in like_prefixes:
                conn.execute("DELETE FROM meta_kv WHERE key LIKE ?", (f"{prefix}%",))
            # Снять id из реестров активных сессий.
            for registry_key in (
                "quest_mission:active_ids",
                "smuggle_mission:active_ids",
                "artifact_hunt:active_ids",
                "duel:grid:active_ids",
            ):
                row = conn.execute("SELECT value FROM meta_kv WHERE key = ?", (registry_key,)).fetchone()
                if row is None:
                    continue
                try:
                    parsed = json.loads(str(row["value"]))
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, list):
                    continue
                ids = [int(x) for x in parsed if int(x) != tid]
                if ids:
                    conn.execute(
                        "INSERT INTO meta_kv(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (registry_key, json.dumps(ids, ensure_ascii=False)),
                    )
                else:
                    conn.execute("DELETE FROM meta_kv WHERE key = ?", (registry_key,))
            row = conn.execute(
                "SELECT value FROM meta_kv WHERE key = ?",
                ("chat_medals:index",),
            ).fetchone()
            if row is not None:
                try:
                    indexed = json.loads(str(row["value"]))
                except json.JSONDecodeError:
                    indexed = None
                if isinstance(indexed, list):
                    cleaned = [int(x) for x in indexed if int(x) != tid]
                    if cleaned:
                        conn.execute(
                            "INSERT INTO meta_kv(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                            ("chat_medals:index", json.dumps(cleaned, ensure_ascii=False)),
                        )
                    else:
                        conn.execute("DELETE FROM meta_kv WHERE key = ?", ("chat_medals:index",))
        self.save_snapshot()

    def delete_character_account(self, telegram_id: int) -> dict[str, Any] | None:
        """Полностью удалить аккаунт игрока. None — если персонажа не было."""
        tid = int(telegram_id)
        player = self.get_character(tid, refresh_energy=False)
        if player is None:
            return None
        snapshot = {
            "telegram_id": tid,
            "nickname": player.nickname,
            "faction": player.faction,
        }
        now_iso = utc_now().isoformat()
        with self._connect() as conn:
            self._ensure_referrals_schema(conn)
            conn.execute(
                """
                UPDATE auctions
                SET status = 'cancelled', closed_at = ?
                WHERE seller_id = ? AND status = 'open'
                """,
                (now_iso, tid),
            )
            conn.execute("DELETE FROM raid_members WHERE telegram_id = ?", (tid,))
            conn.execute("DELETE FROM war_lobby_members WHERE telegram_id = ?", (tid,))
            conn.execute("UPDATE factions SET leader_id = NULL WHERE leader_id = ?", (tid,))
            conn.execute("DELETE FROM player_achievements WHERE telegram_id = ?", (tid,))
            conn.execute("DELETE FROM player_stats WHERE telegram_id = ?", (tid,))
            conn.execute("DELETE FROM referrals WHERE invitee_id = ?", (tid,))
            conn.execute("DELETE FROM referrals WHERE referrer_id = ?", (tid,))
            conn.execute("DELETE FROM topup_payments WHERE telegram_id = ?", (tid,))
            conn.execute("DELETE FROM pending_registrations WHERE telegram_id = ?", (tid,))
            conn.execute("DELETE FROM characters WHERE telegram_id = ?", (tid,))
        self._purge_player_meta_keys(tid)
        self.save_snapshot()
        return snapshot

    def is_nickname_taken(self, nickname: str, exclude_telegram_id: int | None = None) -> bool:
        normalized = (nickname or "").strip().casefold()
        if not normalized:
            return False
        with self._connect() as conn:
            if exclude_telegram_id is None:
                rows = conn.execute("SELECT nickname FROM characters").fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT nickname
                    FROM characters
                    WHERE telegram_id <> ?
                    """,
                    (exclude_telegram_id,),
                ).fetchall()
        for row in rows:
            candidate = str(row["nickname"] or "").strip().casefold()
            if candidate == normalized:
                return True
        return False

    def _ensure_unique_nicknames(self, conn: DbConnection) -> None:
        """Чинит старые дубликаты ников и ставит уникальный индекс без учёта регистра."""
        try:
            conn.savepoint("sp_nick_dedupe")
            rows = conn.execute(
                """
                SELECT telegram_id, nickname
                FROM characters
                ORDER BY telegram_id
                """
            ).fetchall()
            seen: dict[str, int] = {}
            for row in rows:
                tid = int(row["telegram_id"])
                nick = str(row["nickname"] or "").strip()
                key = nick.casefold()
                if not key:
                    continue
                if key not in seen:
                    seen[key] = tid
                    continue
                # Дубликат: переименовываем более поздний аккаунт.
                suffix = f"#{tid}"
                base = nick[: max(1, 24 - len(suffix))]
                new_nick = f"{base}{suffix}"
                guard = 0
                while new_nick.casefold() in seen:
                    guard += 1
                    new_nick = f"id{tid}"[:24] if guard > 3 else f"n{tid}_{guard}"[:24]
                    if guard > 10:
                        break
                conn.execute(
                    "UPDATE characters SET nickname = ? WHERE telegram_id = ?",
                    (new_nick, tid),
                )
                seen[new_nick.casefold()] = tid
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_characters_nickname_ci ON characters (lower(nickname))"
            )
            conn.release_savepoint("sp_nick_dedupe")
        except Exception:
            try:
                conn.rollback_to_savepoint("sp_nick_dedupe")
            except Exception:
                pass

    def _ensure_pending_registrations_schema(self, conn: DbConnection) -> None:
        # Базовая таблица совместима со старым деплоем (только nick + created_at).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_registrations (
                telegram_id BIGINT PRIMARY KEY,
                nickname TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        columns = self._table_columns(conn, "pending_registrations")
        alters = [
            ("gender", "ALTER TABLE pending_registrations ADD COLUMN gender TEXT"),
            ("registration_step", "ALTER TABLE pending_registrations ADD COLUMN registration_step TEXT"),
            ("updated_at", "ALTER TABLE pending_registrations ADD COLUMN updated_at TEXT"),
            ("referrer_id", "ALTER TABLE pending_registrations ADD COLUMN referrer_id BIGINT"),
        ]
        # Старое имя колонки step → registration_step (step может конфликтовать в SQL).
        if "step" in columns and "registration_step" not in columns:
            try:
                conn.savepoint("sp_pending_rename")
                conn.execute(
                    "ALTER TABLE pending_registrations ADD COLUMN registration_step TEXT"
                )
                conn.execute(
                    """
                    UPDATE pending_registrations
                    SET registration_step = step
                    WHERE registration_step IS NULL AND step IS NOT NULL
                    """
                )
                conn.release_savepoint("sp_pending_rename")
                columns = self._table_columns(conn, "pending_registrations")
            except Exception:
                conn.rollback_to_savepoint("sp_pending_rename")

        for col_name, ddl in alters:
            if col_name in columns:
                continue
            try:
                conn.savepoint(f"sp_add_{col_name}")
                conn.execute(ddl)
                conn.release_savepoint(f"sp_add_{col_name}")
                columns.add(col_name)
            except Exception:
                conn.rollback_to_savepoint(f"sp_add_{col_name}")

        now_iso = utc_now().isoformat()
        try:
            conn.savepoint("sp_pending_backfill")
            if "registration_step" in columns or "registration_step" in self._table_columns(conn, "pending_registrations"):
                conn.execute(
                    """
                    UPDATE pending_registrations
                    SET registration_step = 'gender'
                    WHERE registration_step IS NULL OR TRIM(registration_step) = ''
                    """
                )
            if "updated_at" in columns or "updated_at" in self._table_columns(conn, "pending_registrations"):
                conn.execute(
                    """
                    UPDATE pending_registrations
                    SET updated_at = COALESCE(NULLIF(TRIM(created_at), ''), ?)
                    WHERE updated_at IS NULL OR TRIM(updated_at) = ''
                    """,
                    (now_iso,),
                )
            conn.release_savepoint("sp_pending_backfill")
        except Exception:
            conn.rollback_to_savepoint("sp_pending_backfill")

    def _ensure_referrals_schema(self, conn: DbConnection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS referrals (
                invitee_id BIGINT PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

    def _ensure_raids_schema(self, conn: DbConnection) -> None:
        """Рейды на логова + рейды на склад/гараж вражеской группировки (raid_kind, target_faction)."""
        column_names = self._table_columns(conn, "raids")
        add_columns = [
            ("raid_kind", "ALTER TABLE raids ADD COLUMN raid_kind TEXT NOT NULL DEFAULT 'lair'"),
            ("target_faction", "ALTER TABLE raids ADD COLUMN target_faction TEXT"),
        ]
        for col_name, ddl in add_columns:
            if col_name in column_names:
                continue
            sp = f"sp_raid_add_{col_name}"
            try:
                conn.savepoint(sp)
                conn.execute(ddl)
                conn.release_savepoint(sp)
                column_names.add(col_name)
            except Exception:
                try:
                    conn.rollback_to_savepoint(sp)
                except Exception:
                    pass

    def has_referral_claim(self, invitee_id: int) -> bool:
        with self._connect() as conn:
            self._ensure_referrals_schema(conn)
            row = conn.execute(
                "SELECT 1 AS ok FROM referrals WHERE invitee_id = ?",
                (invitee_id,),
            ).fetchone()
        return row is not None

    def record_referral(self, invitee_id: int, referrer_id: int) -> bool:
        if invitee_id == referrer_id:
            return False
        with self._connect() as conn:
            self._ensure_referrals_schema(conn)
            existing = conn.execute(
                "SELECT 1 AS ok FROM referrals WHERE invitee_id = ?",
                (invitee_id,),
            ).fetchone()
            if existing is not None:
                return False
            conn.execute(
                """
                INSERT INTO referrals(invitee_id, referrer_id, created_at)
                VALUES (?, ?, ?)
                """,
                (invitee_id, referrer_id, utc_now().isoformat()),
            )
        self.save_snapshot()
        return True

    def set_pending_referrer(self, telegram_id: int, referrer_id: int | None) -> None:
        key = f"pending_referrer:{int(telegram_id)}"
        if referrer_id is None or int(referrer_id) <= 0 or int(referrer_id) == int(telegram_id):
            with self._connect() as conn:
                conn.execute("DELETE FROM meta_kv WHERE key = ?", (key,))
            return
        self.set_meta(key, str(int(referrer_id)))

    def get_pending_referrer(self, telegram_id: int) -> int | None:
        pending = self.get_pending_registration(telegram_id)
        if pending and pending.get("referrer_id"):
            try:
                value = int(pending["referrer_id"])
                if value > 0 and value != int(telegram_id):
                    return value
            except (TypeError, ValueError):
                pass
        raw = self.get_meta(f"pending_referrer:{int(telegram_id)}")
        if not raw:
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        if value <= 0 or value == int(telegram_id):
            return None
        return value

    def clear_pending_referrer(self, telegram_id: int) -> None:
        key = f"pending_referrer:{int(telegram_id)}"
        with self._connect() as conn:
            conn.execute("DELETE FROM meta_kv WHERE key = ?", (key,))

    def save_pending_registration(
        self,
        telegram_id: int,
        nickname: str,
        gender: str | None = None,
        step: str = "gender",
        referrer_id: int | None = None,
    ) -> None:
        nick = (nickname or "").strip()
        if not nick:
            raise ValueError("nickname is empty")
        gen = (gender or "").strip() or None
        step_value = (step or "gender").strip() or "gender"
        now_iso = utc_now().isoformat()

        # Схему коммитим отдельно — иначе падение ALTER abort'ит INSERT на Postgres.
        with self._connect() as conn:
            self._ensure_pending_registrations_schema(conn)

        last_error: Exception | None = None
        with self._connect() as conn:
            columns = self._table_columns(conn, "pending_registrations")
            has_gender = "gender" in columns
            has_step = "registration_step" in columns
            has_updated = "updated_at" in columns
            try:
                if has_gender and has_step and has_updated:
                    conn.execute(
                        """
                        INSERT INTO pending_registrations(
                            telegram_id, nickname, gender, registration_step, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(telegram_id) DO UPDATE SET
                            nickname = EXCLUDED.nickname,
                            gender = EXCLUDED.gender,
                            registration_step = EXCLUDED.registration_step,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (telegram_id, nick, gen, step_value, now_iso, now_iso),
                    )
                elif has_gender and has_step:
                    conn.execute(
                        """
                        INSERT INTO pending_registrations(
                            telegram_id, nickname, gender, registration_step, created_at
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(telegram_id) DO UPDATE SET
                            nickname = EXCLUDED.nickname,
                            gender = EXCLUDED.gender,
                            registration_step = EXCLUDED.registration_step
                        """,
                        (telegram_id, nick, gen, step_value, now_iso),
                    )
                else:
                    # Минимальная совместимость со старой таблицей.
                    conn.execute(
                        """
                        INSERT INTO pending_registrations(telegram_id, nickname, created_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(telegram_id) DO UPDATE SET
                            nickname = EXCLUDED.nickname
                        """,
                        (telegram_id, nick, now_iso),
                    )
                    if has_gender:
                        conn.execute(
                            "UPDATE pending_registrations SET gender = ? WHERE telegram_id = ?",
                            (gen, telegram_id),
                        )
                    if has_step:
                        conn.execute(
                            "UPDATE pending_registrations SET registration_step = ? WHERE telegram_id = ?",
                            (step_value, telegram_id),
                        )
                    if has_updated:
                        conn.execute(
                            "UPDATE pending_registrations SET updated_at = ? WHERE telegram_id = ?",
                            (now_iso, telegram_id),
                        )
            except Exception as exc:
                last_error = exc

        if referrer_id is not None and int(referrer_id) > 0 and int(referrer_id) != int(telegram_id):
            try:
                with self._connect() as conn:
                    self._ensure_pending_registrations_schema(conn)
                    columns = self._table_columns(conn, "pending_registrations")
                    if "referrer_id" in columns:
                        conn.execute(
                            "UPDATE pending_registrations SET referrer_id = ? WHERE telegram_id = ?",
                            (int(referrer_id), telegram_id),
                        )
            except Exception:
                logger.exception(
                    "Failed to persist pending registration referrer_id=%s for telegram_id=%s",
                    int(referrer_id),
                    int(telegram_id),
                )

        if last_error is not None:
            # Последний шанс: delete + insert минимальных полей в новой транзакции.
            try:
                with self._connect() as conn:
                    self._ensure_pending_registrations_schema(conn)
                    conn.execute(
                        "DELETE FROM pending_registrations WHERE telegram_id = ?",
                        (telegram_id,),
                    )
                    conn.execute(
                        """
                        INSERT INTO pending_registrations(telegram_id, nickname, created_at)
                        VALUES (?, ?, ?)
                        """,
                        (telegram_id, nick, now_iso),
                    )
                    columns = self._table_columns(conn, "pending_registrations")
                    if "gender" in columns and gen is not None:
                        conn.execute(
                            "UPDATE pending_registrations SET gender = ? WHERE telegram_id = ?",
                            (gen, telegram_id),
                        )
                    if "registration_step" in columns:
                        conn.execute(
                            "UPDATE pending_registrations SET registration_step = ? WHERE telegram_id = ?",
                            (step_value, telegram_id),
                        )
                    if "updated_at" in columns:
                        conn.execute(
                            "UPDATE pending_registrations SET updated_at = ? WHERE telegram_id = ?",
                            (now_iso, telegram_id),
                        )
            except Exception as exc:
                raise RuntimeError(f"save_pending_registration failed: {exc}") from exc

        try:
            self.save_snapshot()
        except Exception:
            # Snapshot не должен ломать регистрацию.
            pass

    def get_pending_registration(self, telegram_id: int) -> dict[str, str] | None:
        with self._connect() as conn:
            self._ensure_pending_registrations_schema(conn)
            columns = self._table_columns(conn, "pending_registrations")
            select_cols = ["nickname"]
            if "gender" in columns:
                select_cols.append("gender")
            if "registration_step" in columns:
                select_cols.append("registration_step")
            elif "step" in columns:
                select_cols.append("step")
            if "referrer_id" in columns:
                select_cols.append("referrer_id")
            row = conn.execute(
                f"SELECT {', '.join(select_cols)} FROM pending_registrations WHERE telegram_id = ?",  # noqa: S608
                (telegram_id,),
            ).fetchone()
        if row is None:
            return None
        nick = str(row["nickname"] or "").strip()
        if not nick:
            return None
        gender = str(self._row_get(row, "gender") or "").strip()
        step = str(
            self._row_get(row, "registration_step")
            or self._row_get(row, "step")
            or ""
        ).strip() or ("faction" if gender else "gender")
        result = {"nickname": nick, "step": step}
        if gender:
            result["gender"] = gender
        raw_ref = self._row_get(row, "referrer_id")
        if raw_ref is not None and str(raw_ref).strip() != "":
            try:
                result["referrer_id"] = str(int(raw_ref))
            except (TypeError, ValueError):
                pass
        return result

    def clear_pending_registration(self, telegram_id: int) -> None:
        with self._connect() as conn:
            self._ensure_pending_registrations_schema(conn)
            conn.execute(
                "DELETE FROM pending_registrations WHERE telegram_id = ?",
                (telegram_id,),
            )
        try:
            self.save_snapshot()
        except Exception:
            pass

    def get_character(self, telegram_id: int, refresh_energy: bool = True) -> Character | None:
        if refresh_energy:
            self.recover_energy(telegram_id)
            self.refresh_survival(telegram_id)
        self.resolve_travel_if_due(telegram_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM characters WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_character(row)

    def character_exists(self, telegram_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM characters WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
        return row is not None

    def set_faction(self, telegram_id: int, faction: str) -> None:
        from app.faction_ranks import default_rank_key
        from app.game_logic import faction_home_base

        tid = int(telegram_id)
        rank_key = default_rank_key(faction)
        home = faction_home_base(faction)
        with self._connect() as conn:
            # Если уходил с поста командира — снимаем лидерство со старой ГП.
            conn.execute("UPDATE factions SET leader_id = NULL WHERE leader_id = ?", (tid,))
            conn.execute(
                """
                UPDATE characters
                SET faction = ?, faction_rank = ?, location = ?
                WHERE telegram_id = ?
                """,
                (faction, rank_key, home, tid),
            )
        self.save_snapshot()

    def set_last_arrival_transport(self, telegram_id: int, transport: str | None) -> None:
        key = f"last_arrival_transport:{int(telegram_id)}"
        if transport:
            self.set_meta(key, str(transport))
        else:
            self.delete_meta(key)

    def get_last_arrival_transport(self, telegram_id: int) -> str | None:
        return self.get_meta(f"last_arrival_transport:{int(telegram_id)}")

    def consume_last_arrival_transport(self, telegram_id: int) -> str | None:
        key = f"last_arrival_transport:{int(telegram_id)}"
        value = self.get_meta(key)
        if value is not None:
            self.delete_meta(key)
        return value

    def set_bound_transport(self, telegram_id: int, transport: str | None) -> None:
        key = f"bound_transport:{int(telegram_id)}"
        if transport:
            self.set_meta(key, str(transport))
        else:
            self.delete_meta(key)

    def get_bound_transport(self, telegram_id: int) -> str | None:
        return self.get_meta(f"bound_transport:{int(telegram_id)}")

    def clear_bound_transport(self, telegram_id: int) -> None:
        self.delete_meta(f"bound_transport:{int(telegram_id)}")

    @staticmethod
    def _arrival_notice_key(telegram_id: int) -> str:
        return f"arrival_notice:{int(telegram_id)}"

    def _set_pending_arrival_notice(self, telegram_id: int, destination: str) -> None:
        """Пережить рестарт бота: пишем в meta_kv, а не только в память процесса."""
        self.set_meta(self._arrival_notice_key(telegram_id), str(destination))

    def set_faction_rank(self, telegram_id: int, rank_key: str | None) -> bool:
        character = self.get_character(telegram_id, refresh_energy=False)
        if character is None or character.faction is None:
            return False
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET faction_rank = ? WHERE telegram_id = ?",
                (rank_key, telegram_id),
            )
        self.save_snapshot()
        return True

    def list_faction_members(self, faction: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT telegram_id, nickname, faction, faction_rank, location, health
                FROM characters
                WHERE faction = ?
                ORDER BY nickname
                """,
                (faction,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_location(self, telegram_id: int, location: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET location = ? WHERE telegram_id = ?",
                (location, telegram_id),
            )
        self.save_snapshot()

    def pop_arrival_notice(self, telegram_id: int) -> str | None:
        key = self._arrival_notice_key(telegram_id)
        value = self.get_meta(key)
        if value is not None:
            self.delete_meta(key)
        return value

    def resolve_travel_if_due(self, telegram_id: int) -> str | None:
        """Завершить переход, если время прибытия наступило. Возвращает локацию или None."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT travel_destination, travel_arrives_at, travel_transport
                FROM characters
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            ).fetchone()
            if row is None:
                return None
            destination = row["travel_destination"]
            raw_arrives = row["travel_arrives_at"]
            transport = row["travel_transport"]
            if destination is None or str(destination).strip() == "":
                return None
            if raw_arrives is None or str(raw_arrives).strip() == "":
                return None
            try:
                arrives_at = datetime.fromisoformat(str(raw_arrives))
            except ValueError:
                return None
            if arrives_at.tzinfo is None:
                arrives_at = arrives_at.replace(tzinfo=timezone.utc)
            if arrives_at > utc_now():
                return None
            dest = str(destination)
            conn.execute(
                """
                UPDATE characters
                SET location = ?,
                    travel_destination = NULL,
                    travel_arrives_at = NULL,
                    travel_transport = NULL
                WHERE telegram_id = ?
                """,
                (dest, telegram_id),
            )
        self.save_snapshot()
        self._set_pending_arrival_notice(telegram_id, dest)
        if transport:
            self.set_last_arrival_transport(telegram_id, str(transport))
        return dest

    def pop_due_travels(self) -> list[tuple[int, str]]:
        """Завершить просроченные переходы и вернуть (telegram_id, destination)."""
        completed: list[tuple[int, str]] = []
        now = utc_now()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT telegram_id, travel_destination, travel_arrives_at, travel_transport
                FROM characters
                WHERE travel_destination IS NOT NULL
                  AND TRIM(travel_destination) != ''
                  AND travel_arrives_at IS NOT NULL
                  AND TRIM(travel_arrives_at) != ''
                """
            ).fetchall()
            for row in rows:
                destination = str(row["travel_destination"])
                raw_arrives = row["travel_arrives_at"]
                transport = row["travel_transport"]
                try:
                    arrives_at = datetime.fromisoformat(str(raw_arrives))
                except ValueError:
                    continue
                if arrives_at.tzinfo is None:
                    arrives_at = arrives_at.replace(tzinfo=timezone.utc)
                if arrives_at > now:
                    continue
                telegram_id = int(row["telegram_id"])
                conn.execute(
                    """
                    UPDATE characters
                    SET location = ?,
                        travel_destination = NULL,
                        travel_arrives_at = NULL,
                        travel_transport = NULL
                    WHERE telegram_id = ?
                    """,
                    (destination, telegram_id),
                )
                completed.append((telegram_id, destination))
                if transport:
                    # set_meta открывает своё соединение — после цикла ок
                    self._pending_arrival_transports = getattr(self, "_pending_arrival_transports", {})
                    self._pending_arrival_transports[telegram_id] = str(transport)
        if completed:
            self.save_snapshot()
            # Пишем уведомления о прибытии в meta_kv (переживает рестарт бота),
            # а не только в память процесса.
            for tid, destination in completed:
                self._set_pending_arrival_notice(tid, destination)
            pending_t = getattr(self, "_pending_arrival_transports", {})
            for tid, mode in list(pending_t.items()):
                self.set_last_arrival_transport(tid, mode)
                pending_t.pop(tid, None)
        return completed

    def start_travel(
        self,
        telegram_id: int,
        destination: str,
        arrives_at: datetime,
        transport_mode: str,
    ) -> None:
        arrives_iso = arrives_at.isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE characters
                SET travel_destination = ?,
                    travel_arrives_at = ?,
                    travel_transport = ?
                WHERE telegram_id = ?
                """,
                (destination, arrives_iso, transport_mode, telegram_id),
            )
        self.save_snapshot()

    def set_travel_arrives_at(self, telegram_id: int, arrives_at: datetime) -> bool:
        """Перезаписать ETA активного перехода."""
        arrives_iso = arrives_at.isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT travel_destination
                FROM characters
                WHERE telegram_id = ?
                  AND travel_destination IS NOT NULL
                  AND TRIM(travel_destination) != ''
                """,
                (telegram_id,),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                """
                UPDATE characters
                SET travel_arrives_at = ?
                WHERE telegram_id = ?
                """,
                (arrives_iso, telegram_id),
            )
        self.save_snapshot()
        return True

    def list_active_travels(self) -> list[tuple[int, str, datetime, str]]:
        """Активные переходы: (telegram_id, destination, arrives_at, transport)."""
        now = utc_now()
        active: list[tuple[int, str, datetime, str]] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT telegram_id, travel_destination, travel_arrives_at, travel_transport
                FROM characters
                WHERE travel_destination IS NOT NULL
                  AND TRIM(travel_destination) != ''
                  AND travel_arrives_at IS NOT NULL
                  AND TRIM(travel_arrives_at) != ''
                """
            ).fetchall()
            for row in rows:
                destination = str(row["travel_destination"] or "").strip()
                if not destination:
                    continue
                try:
                    arrives_at = datetime.fromisoformat(str(row["travel_arrives_at"]))
                except ValueError:
                    continue
                if arrives_at.tzinfo is None:
                    arrives_at = arrives_at.replace(tzinfo=timezone.utc)
                if arrives_at <= now:
                    continue
                transport = str(row["travel_transport"] or "foot")
                active.append((int(row["telegram_id"]), destination, arrives_at, transport))
        return active

    def clear_travel(self, telegram_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE characters
                SET travel_destination = NULL,
                    travel_arrives_at = NULL,
                    travel_transport = NULL
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            )
        self.save_snapshot()

    def set_niva_owned(self, telegram_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET niva_owned = 1, niva_durability = 100 WHERE telegram_id = ?",
                (telegram_id,),
            )
        self.save_snapshot()

    def clear_niva_owned(self, telegram_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET niva_owned = 0, niva_durability = 0 WHERE telegram_id = ?",
                (telegram_id,),
            )
        self.save_snapshot()

    def apply_niva_wear(self, telegram_id: int, wear_percent: int) -> int | None:
        character = self.get_character(telegram_id, refresh_energy=False)
        if character is None or not character.niva_owned:
            return None
        wear = max(0, int(wear_percent))
        new_durability = max(0, min(100, int(character.niva_durability) - wear))
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET niva_durability = ? WHERE telegram_id = ?",
                (new_durability, telegram_id),
            )
        self.save_snapshot()
        return new_durability

    def set_niva_durability(self, telegram_id: int, durability: int) -> bool:
        character = self.get_character(telegram_id, refresh_energy=False)
        if character is None:
            return False
        value = max(0, min(100, int(durability)))
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET niva_durability = ? WHERE telegram_id = ?",
                (value, telegram_id),
            )
        self.save_snapshot()
        return True

    def set_bicycle_owned(self, telegram_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET bicycle_owned = 1 WHERE telegram_id = ?",
                (telegram_id,),
            )
        self.save_snapshot()

    def clear_bicycle_owned(self, telegram_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET bicycle_owned = 0 WHERE telegram_id = ?",
                (telegram_id,),
            )
        self.save_snapshot()

    def get_active_contract(self, telegram_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT active_contract_json FROM characters WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
        if row is None:
            return None
        raw = row["active_contract_json"]
        if raw is None or str(raw).strip() == "":
            return None
        try:
            data = json.loads(str(raw))
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def set_active_contract(self, telegram_id: int, contract: dict[str, Any] | None) -> None:
        payload = json.dumps(contract, ensure_ascii=False) if contract else None
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET active_contract_json = ? WHERE telegram_id = ?",
                (payload, telegram_id),
            )
        self.save_snapshot()

    def spend_energy(self, telegram_id: int, amount: int) -> bool:
        character = self.get_character(telegram_id, refresh_energy=True)
        if character is None or character.energy < amount:
            return False
        new_energy = character.energy - amount
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE characters
                SET energy = ?, energy_updated_at = ?
                WHERE telegram_id = ?
                """,
                (new_energy, utc_now().isoformat(), telegram_id),
            )
        self.save_snapshot()
        return True

    def restore_energy(self, telegram_id: int, amount: int) -> None:
        character = self.get_character(telegram_id, refresh_energy=True)
        if character is None:
            return
        new_energy = min(character.max_energy, character.energy + amount)
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET energy = ?, energy_updated_at = ? WHERE telegram_id = ?",
                (new_energy, utc_now().isoformat(), telegram_id),
            )
        self.save_snapshot()

    def refresh_survival(self, telegram_id: int) -> None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT radiation, hunger, thirst, health, needs_updated_at, survival_damage_at, equipment_json
                FROM characters
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            ).fetchone()
            if row is None:
                return
            now = utc_now()

            def _as_dt(value: Any) -> datetime:
                if isinstance(value, datetime):
                    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
                if value is None or str(value).strip() == "":
                    return now
                try:
                    parsed = datetime.fromisoformat(str(value))
                except ValueError:
                    return now
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

            def _as_int(value: Any, default: int = 0) -> int:
                try:
                    if value is None:
                        return default
                    return int(value)
                except (TypeError, ValueError):
                    return default

            needs_updated_at = _as_dt(row["needs_updated_at"])
            survival_damage_at = _as_dt(row["survival_damage_at"])
            radiation = _as_int(row["radiation"], 0)
            hunger = _as_int(row["hunger"], 0)
            thirst = _as_int(row["thirst"], 0)
            health = _as_int(row["health"], 100)
            was_alive = health > 0
            orig_hunger = hunger
            orig_thirst = thirst
            changed = False

            hours_passed = int((now - needs_updated_at).total_seconds() // 3600)
            if hours_passed > 0:
                hunger = min(SURVIVAL_NEED_MAX, hunger + hours_passed * SURVIVAL_HOURLY_GAIN)
                thirst = min(SURVIVAL_NEED_MAX, thirst + hours_passed * SURVIVAL_HOURLY_GAIN)
                needs_updated_at = now
                changed = True

            was_damaging = (
                orig_hunger >= SURVIVAL_CRITICAL_NEED
                or orig_thirst >= SURVIVAL_CRITICAL_NEED
                or radiation >= SURVIVAL_CRITICAL_NEED
            )
            in_damage = (
                hunger >= SURVIVAL_CRITICAL_NEED
                or thirst >= SURVIVAL_CRITICAL_NEED
                or radiation >= SURVIVAL_CRITICAL_NEED
            )
            if in_damage:
                if not was_damaging:
                    survival_damage_at = now
                    changed = True
                ticks = int(
                    (now - survival_damage_at).total_seconds() // (SURVIVAL_DAMAGE_TICK_MINUTES * 60)
                )
                if ticks > 0:
                    if _survival_immune(telegram_id):
                        # Владелец не умирает от голода/жажды/радиации — без урона HP.
                        survival_damage_at = now
                        changed = True
                    else:
                        # Радиация бьёт сильнее голодного тика, если фон критический.
                        per_tick = SURVIVAL_DAMAGE_PER_TICK
                        if radiation >= SURVIVAL_CRITICAL_NEED:
                            per_tick = SURVIVAL_DAMAGE_PER_TICK + 5
                        health = max(0, health - ticks * per_tick)
                        survival_damage_at = now
                        changed = True

            if not changed:
                return

            try:
                from app.game_logic import effective_max_health
                from types import SimpleNamespace

                equipment = json.loads(self._row_get(row, "equipment_json", "{}") or "{}")
                if not isinstance(equipment, dict):
                    equipment = {}
                max_health = int(effective_max_health(SimpleNamespace(equipment=equipment)))
            except Exception:
                max_health = 100
            final_health = max(0, min(max_health, health))
            conn.execute(
                """
                UPDATE characters
                SET radiation = ?, hunger = ?, thirst = ?, health = ?, needs_updated_at = ?, survival_damage_at = ?
                WHERE telegram_id = ?
                """,
                (
                    max(0, min(SURVIVAL_NEED_MAX, radiation)),
                    max(0, min(SURVIVAL_NEED_MAX, hunger)),
                    max(0, min(SURVIVAL_NEED_MAX, thirst)),
                    final_health,
                    needs_updated_at.isoformat(),
                    survival_damage_at.isoformat(),
                    telegram_id,
                ),
            )
        self.save_snapshot()
        if was_alive and final_health <= 0:
            self.add_player_stat(telegram_id, "deaths", 1)
            # Причина для текста смерти (голод / жажда / радиация).
            try:
                from app.game_logic import remember_death_cause

                scores = {
                    "hunger": hunger,
                    "thirst": thirst,
                    "radiation": radiation,
                }
                cause = max(scores, key=lambda k: scores[k])
                if scores[cause] >= SURVIVAL_CRITICAL_NEED:
                    remember_death_cause(self, telegram_id, cause)
            except Exception:
                logger.exception("Failed to record survival death cause for %s", telegram_id)

    def adjust_survival(
        self,
        telegram_id: int,
        radiation_delta: int = 0,
        hunger_delta: int = 0,
        thirst_delta: int = 0,
        health_delta: int = 0,
    ) -> bool:
        self.refresh_survival(telegram_id)
        character = self.get_character(telegram_id, refresh_energy=False)
        if character is None:
            return False
        new_radiation = max(0, min(SURVIVAL_NEED_MAX, character.radiation + radiation_delta))
        new_hunger = max(0, min(SURVIVAL_NEED_MAX, character.hunger + hunger_delta))
        new_thirst = max(0, min(SURVIVAL_NEED_MAX, character.thirst + thirst_delta))
        try:
            from app.game_logic import effective_max_health

            max_hp = int(effective_max_health(character))
        except Exception:
            max_hp = 100
        new_health = max(0, min(max_hp, character.health + health_delta))
        if _survival_immune(telegram_id):
            # Владелец не умирает от голода/жажды/радиации.
            new_health = max(1, new_health)
        died = character.health > 0 and new_health <= 0
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE characters
                SET radiation = ?, hunger = ?, thirst = ?, health = ?, needs_updated_at = ?, survival_damage_at = ?
                WHERE telegram_id = ?
                """,
                (
                    new_radiation,
                    new_hunger,
                    new_thirst,
                    new_health,
                    utc_now().isoformat(),
                    utc_now().isoformat(),
                    telegram_id,
                ),
            )
        self.save_snapshot()
        if died:
            self.add_player_stat(telegram_id, "deaths", 1)
            try:
                from app.game_logic import remember_death_cause

                scores = {
                    "hunger": new_hunger,
                    "thirst": new_thirst,
                    "radiation": new_radiation,
                }
                cause = max(scores, key=lambda k: scores[k])
                if scores[cause] >= SURVIVAL_CRITICAL_NEED:
                    remember_death_cause(self, telegram_id, cause)
            except Exception:
                logger.exception("Failed to record adjust_survival death cause for %s", telegram_id)
        return True

    def set_survival_needs(
        self,
        telegram_id: int,
        *,
        radiation: int | None = None,
        hunger: int | None = None,
        thirst: int | None = None,
    ) -> bool:
        """Абсолютная установка шкал выживания (в пределах 0…SURVIVAL_NEED_MAX)."""
        character = self.get_character(telegram_id, refresh_energy=False)
        if character is None:
            return False
        new_radiation = (
            max(0, min(SURVIVAL_NEED_MAX, int(radiation)))
            if radiation is not None
            else character.radiation
        )
        new_hunger = (
            max(0, min(SURVIVAL_NEED_MAX, int(hunger))) if hunger is not None else character.hunger
        )
        new_thirst = (
            max(0, min(SURVIVAL_NEED_MAX, int(thirst))) if thirst is not None else character.thirst
        )
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE characters
                SET radiation = ?, hunger = ?, thirst = ?, needs_updated_at = ?, survival_damage_at = ?
                WHERE telegram_id = ?
                """,
                (
                    new_radiation,
                    new_hunger,
                    new_thirst,
                    utc_now().isoformat(),
                    utc_now().isoformat(),
                    telegram_id,
                ),
            )
        self.save_snapshot()
        return True

    def recover_energy(self, telegram_id: int) -> None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT energy, max_energy, energy_updated_at, sleeping_bag_owned, equipment_json, radiation, faction, health
                FROM characters
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            ).fetchone()
            if row is None:
                return

            now = utc_now()

            def _as_int(value: Any, default: int = 0) -> int:
                try:
                    if value is None:
                        return default
                    return int(value)
                except (TypeError, ValueError):
                    return default

            def _as_dt(value: Any) -> datetime:
                if isinstance(value, datetime):
                    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
                if value is None or str(value).strip() == "":
                    return now
                try:
                    parsed = datetime.fromisoformat(str(value))
                except ValueError:
                    return now
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

            energy = _as_int(row["energy"], 0)
            max_energy = _as_int(row["max_energy"], 100)
            radiation = _as_int(self._row_get(row, "radiation"), 0)
            faction = str(self._row_get(row, "faction") or "").strip() or None
            health = _as_int(self._row_get(row, "health"), 0)
            last_update = _as_dt(row["energy_updated_at"])
            minutes_passed = int((now - last_update).total_seconds() // 60)
            if minutes_passed <= 0:
                return

            regen_multiplier = 2.0 if _as_int(self._row_get(row, "sleeping_bag_owned", 0), 0) == 1 else 1.0
            has_zone_artifact = False
            try:
                equipment = json.loads(self._row_get(row, "equipment_json", "{}") or "{}")
                if isinstance(equipment, dict):
                    from app.game_logic import ARTIFACT_ENERGY_REGEN_NAMES

                    art_names = [
                        str(equipment.get("artifact") or "").strip(),
                        str(equipment.get("artifact_2") or "").strip(),
                        str(equipment.get("artifact_3") or "").strip(),
                    ]
                    has_zone_artifact = any(n in ARTIFACT_ENERGY_REGEN_NAMES for n in art_names)
            except (TypeError, json.JSONDecodeError, ImportError):
                has_zone_artifact = False
            if has_zone_artifact:
                regen_multiplier *= 1.05
            if faction:
                try:
                    from app.faction_buildings import bar_energy_regen_bonus_multiplier

                    regen_multiplier *= 1.0 + bar_energy_regen_bonus_multiplier(self, faction)
                except ImportError:
                    pass
            gained = int(minutes_passed * ENERGY_REGEN_PER_MINUTE * regen_multiplier)
            try:
                from types import SimpleNamespace

                from app.artifact_features import scaled_artifact_energy_regen_bonus

                equipment_for_energy = json.loads(self._row_get(row, "equipment_json", "{}") or "{}")
                if isinstance(equipment_for_energy, dict):
                    proxy = SimpleNamespace(equipment=equipment_for_energy)
                    gained += minutes_passed * scaled_artifact_energy_regen_bonus(proxy)  # type: ignore[arg-type]
            except (TypeError, json.JSONDecodeError, ImportError):
                pass
            new_energy = min(max_energy, energy + gained)
            new_radiation = radiation
            try:
                equipment = json.loads(self._row_get(row, "equipment_json", "{}") or "{}")
                if isinstance(equipment, dict):
                    from types import SimpleNamespace

                    from app.artifact_features import apply_passive_artifact_radiation

                    proxy = SimpleNamespace(equipment=equipment)
                    new_radiation = apply_passive_artifact_radiation(
                        radiation, minutes_passed, proxy  # type: ignore[arg-type]
                    )
            except (TypeError, json.JSONDecodeError, ImportError):
                new_radiation = radiation

            # Пассивный хил от медпункта группировки (≈6 HP/час) + артефактов. Никогда не оживляет мёртвый персонаж — штрафы смерти не обходятся.
            new_health = health
            heal_total = 0.0
            if faction and health > 0:
                try:
                    from app.faction_buildings import passive_heal_per_minute

                    heal_total += minutes_passed * passive_heal_per_minute(self, faction)
                except ImportError:
                    pass
            if health > 0:
                try:
                    from types import SimpleNamespace

                    from app.artifact_features import scaled_artifact_hp_regen_per_minute

                    equipment_for_hp = json.loads(self._row_get(row, "equipment_json", "{}") or "{}")
                    if isinstance(equipment_for_hp, dict):
                        proxy = SimpleNamespace(equipment=equipment_for_hp)
                        heal_total += minutes_passed * scaled_artifact_hp_regen_per_minute(proxy)  # type: ignore[arg-type]
                except (TypeError, json.JSONDecodeError, ImportError):
                    pass
            heal = int(heal_total)
            if heal > 0:
                try:
                    from types import SimpleNamespace

                    from app.game_logic import effective_max_health

                    equipment = json.loads(self._row_get(row, "equipment_json", "{}") or "{}")
                    max_hp = int(effective_max_health(SimpleNamespace(equipment=equipment)))
                except Exception:
                    max_hp = 100
                new_health = max(0, min(max_hp, health + heal))

            if (
                new_energy == energy
                and new_radiation == radiation
                and new_health == health
                and minutes_passed > 0
            ):
                # Даже без прироста двигаем таймер, чтобы не пересчитывать огромный gap.
                conn.execute(
                    "UPDATE characters SET energy_updated_at = ? WHERE telegram_id = ?",
                    (now.isoformat(), telegram_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE characters
                    SET energy = ?, radiation = ?, health = ?, energy_updated_at = ?
                    WHERE telegram_id = ?
                    """,
                    (new_energy, new_radiation, new_health, now.isoformat(), telegram_id),
                )
        self.save_snapshot()

    def change_money(self, telegram_id: int, delta: int, *, skip_debt_collect: bool = False) -> bool:
        with self._connect() as conn:
            if delta >= 0:
                cur = conn.execute(
                    "UPDATE characters SET money = money + ? WHERE telegram_id = ?",
                    (delta, telegram_id),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE characters
                    SET money = money + ?
                    WHERE telegram_id = ? AND money + ? >= 0
                    """,
                    (delta, telegram_id, delta),
                )
            ok = int(cur.rowcount or 0) > 0
        if ok:
            self.save_snapshot()
            if delta > 0 and not skip_debt_collect:
                from app.game_logic import collect_respawn_debt

                collect_respawn_debt(self, telegram_id)
        return ok

    def drain_money(self, telegram_id: int, max_amount: int) -> int:
        """Атомарно списать до max_amount RU (весь баланс, если не хватает)."""
        amount = max(0, int(max_amount))
        if amount <= 0:
            return 0
        with self._connect() as conn:
            row = conn.execute(
                "SELECT money FROM characters WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
            if row is None:
                return 0
            balance = int(row["money"])
            taken = min(amount, balance)
            if taken <= 0:
                return 0
            conn.execute(
                "UPDATE characters SET money = money - ? WHERE telegram_id = ? AND money >= ?",
                (taken, telegram_id, taken),
            )
            if int(conn.total_changes or 0) == 0:
                return 0
        self.save_snapshot()
        return taken

    def change_gear_power(self, telegram_id: int, delta: int) -> None:
        if delta == 0:
            return
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE characters
                SET gear_power = MAX(1, gear_power + ?)
                WHERE telegram_id = ?
                """,
                (int(delta), telegram_id),
            )
        self.save_snapshot()

    def set_gear_power(self, telegram_id: int, power: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET gear_power = ? WHERE telegram_id = ?",
                (max(1, int(power)), telegram_id),
            )
        self.save_snapshot()

    def sync_gear_power(self, telegram_id: int) -> int | None:
        """Пересчитать gear_power из экипировки и сохранить в БД."""
        character = self.get_character(telegram_id, refresh_energy=False)
        if character is None:
            return None
        from app.game_logic import compute_total_gear_power

        power = max(1, int(compute_total_gear_power(character)))
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET gear_power = ? WHERE telegram_id = ?",
                (power, telegram_id),
            )
        self.save_snapshot()
        return power

    def backfill_all_gear_power(self) -> int:
        """Разовая синхронизация gear_power для всех персонажей."""
        from app.game_logic import compute_total_gear_power

        updated = 0
        with self._connect() as conn:
            rows = conn.execute("SELECT telegram_id FROM characters").fetchall()
        for row in rows:
            tid = int(row["telegram_id"])
            character = self.get_character(tid, refresh_energy=False)
            if character is None:
                continue
            power = max(1, int(compute_total_gear_power(character)))
            if character.gear_power == power:
                continue
            with self._connect() as conn:
                conn.execute(
                    "UPDATE characters SET gear_power = ? WHERE telegram_id = ?",
                    (power, tid),
                )
            updated += 1
        if updated:
            self.save_snapshot()
        return updated

    def persist_character_state(self, telegram_id: int) -> bool:
        """Принудительно перезаписать все поля персонажа в БД из текущего состояния."""
        character = self.get_character(telegram_id, refresh_energy=False)
        if character is None:
            return False
        from app.game_logic import compute_total_gear_power

        gear_power = max(1, int(compute_total_gear_power(character)))
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE characters SET
                    player_uid = ?,
                    avatar_style = ?,
                    nickname = ?,
                    gender = ?,
                    faction = ?,
                    faction_rank = ?,
                    money = ?,
                    energy = ?,
                    max_energy = ?,
                    energy_updated_at = ?,
                    health = ?,
                    gear_power = ?,
                    location = ?,
                    inventory_json = ?,
                    equipment_json = ?,
                    truck_owned = ?,
                    truck_durability = ?,
                    sleeping_bag_owned = ?,
                    fuel = ?,
                    radiation = ?,
                    hunger = ?,
                    thirst = ?,
                    needs_updated_at = ?,
                    survival_damage_at = ?
                WHERE telegram_id = ?
                """,
                (
                    character.player_uid,
                    character.avatar_style,
                    character.nickname,
                    character.gender,
                    character.faction,
                    character.faction_rank,
                    character.money,
                    character.energy,
                    character.max_energy,
                    character.energy_updated_at.isoformat(),
                    character.health,
                    gear_power,
                    character.location,
                    json.dumps(character.inventory, ensure_ascii=False),
                    json.dumps(character.equipment, ensure_ascii=False),
                    1 if character.truck_owned else 0,
                    character.truck_durability,
                    1 if character.sleeping_bag_owned else 0,
                    character.diesel,
                    character.radiation,
                    character.hunger,
                    character.thirst,
                    character.needs_updated_at.isoformat(),
                    character.survival_damage_at.isoformat(),
                    telegram_id,
                ),
            )
        self.save_snapshot()
        return True

    def get_db_status(self) -> dict[str, Any]:
        with self._connect() as conn:
            characters = int(conn.execute("SELECT COUNT(*) AS cnt FROM characters").fetchone()["cnt"])
            pending = 0
            try:
                pending = int(
                    conn.execute("SELECT COUNT(*) AS cnt FROM pending_registrations").fetchone()["cnt"]
                )
            except Exception:
                pending = 0
            sample_type = None
            if conn.backend == "postgres":
                row = conn.execute(
                    """
                    SELECT data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'characters'
                      AND column_name = 'telegram_id'
                    """
                ).fetchone()
                sample_type = str(row["data_type"]) if row else None
        return {
            "backend": self.backend,
            "characters": characters,
            "pending_registrations": pending,
            "telegram_id_type": sample_type,
            "snapshot_path": str(self.snapshot_path),
            "db_path": self.db_path if self.backend == "sqlite" else "(DATABASE_URL)",
        }

    def add_item(self, telegram_id: int, item_key: str, amount: int = 1) -> None:
        if amount <= 0:
            return
        character = self.get_character(telegram_id, refresh_energy=False)
        if character is None:
            return
        inventory = dict(character.inventory)
        inventory[item_key] = inventory.get(item_key, 0) + amount
        self._set_inventory(telegram_id, inventory)
        self.save_snapshot()

    def remove_item(self, telegram_id: int, item_key: str, amount: int = 1) -> bool:
        character = self.get_character(telegram_id, refresh_energy=False)
        if character is None:
            return False
        inventory = dict(character.inventory)
        owned = inventory.get(item_key, 0)
        if owned < amount:
            return False
        new_amount = owned - amount
        if new_amount <= 0:
            inventory.pop(item_key, None)
        else:
            inventory[item_key] = new_amount
        self._set_inventory(telegram_id, inventory)
        self.save_snapshot()
        return True

    def set_equipment_item(self, telegram_id: int, slot: str, value: Any) -> None:
        character = self.get_character(telegram_id, refresh_energy=False)
        if character is None:
            return
        equipment = dict(character.equipment)
        equipment[slot] = value
        self._set_equipment(telegram_id, equipment)
        self.sync_gear_power(telegram_id)

    def update_equipment_fields(self, telegram_id: int, updates: dict[str, Any]) -> bool:
        character = self.get_character(telegram_id, refresh_energy=False)
        if character is None:
            return False
        equipment = dict(character.equipment)
        equipment.update(updates)
        self._set_equipment(telegram_id, equipment)
        self.sync_gear_power(telegram_id)
        return True

    def add_player_stat(self, telegram_id: int, stat_key: str, delta: int = 1) -> bool:
        allowed_columns = {
            "quests_completed": "quests_completed",
            "quests_failed": "quests_failed",
            "raids_completed": "raids_completed",
            "raids_failed": "raids_failed",
            "wars_won": "wars_won",
            "enemy_bases_captured": "enemy_bases_captured",
            "smuggling_success": "smuggling_success",
            "trades_done": "trades_done",
            "money_earned": "money_earned",
            "artifacts_found": "artifacts_found",
            "deaths": "deaths",
            "rating_points": "rating_points",
            "achievements_unlocked": "achievements_unlocked",
            "season_rating": "season_rating",
            "radio_helps": "radio_helps",
        }
        column = allowed_columns.get(stat_key)
        if column is None or delta == 0:
            return False
        with self._connect() as conn:
            self._ensure_player_stats_row(conn, telegram_id)
            conn.execute(
                f"UPDATE player_stats SET {column} = MAX(0, {column} + ?) WHERE telegram_id = ?",  # noqa: S608
                (delta, telegram_id),
            )
        self.save_snapshot()
        return True

    def get_player_stats(self, telegram_id: int) -> dict[str, int]:
        with self._connect() as conn:
            self._ensure_player_stats_row(conn, telegram_id)
            row = conn.execute(
                """
                SELECT quests_completed, quests_failed, raids_completed, raids_failed, wars_won,
                       enemy_bases_captured, smuggling_success, trades_done, money_earned, artifacts_found, deaths,
                       rating_points, achievements_unlocked, season_rating, radio_helps
                FROM player_stats
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            ).fetchone()
        if row is None:
            return {
                "quests_completed": 0,
                "quests_failed": 0,
                "raids_completed": 0,
                "raids_failed": 0,
                "wars_won": 0,
                "enemy_bases_captured": 0,
                "smuggling_success": 0,
                "trades_done": 0,
                "money_earned": 0,
                "artifacts_found": 0,
                "deaths": 0,
                "rating_points": 0,
                "achievements_unlocked": 0,
                "season_rating": 0,
                "radio_helps": 0,
            }
        return {
            "quests_completed": int(row["quests_completed"]),
            "quests_failed": int(row["quests_failed"]),
            "raids_completed": int(row["raids_completed"]),
            "raids_failed": int(row["raids_failed"]),
            "wars_won": int(row["wars_won"]),
            "enemy_bases_captured": int(row["enemy_bases_captured"] or 0),
            "smuggling_success": int(row["smuggling_success"]),
            "trades_done": int(row["trades_done"]),
            "money_earned": int(row["money_earned"]),
            "artifacts_found": int(row["artifacts_found"]),
            "deaths": int(row["deaths"]),
            "rating_points": int(row["rating_points"]),
            "achievements_unlocked": int(row["achievements_unlocked"]),
            "season_rating": int(row["season_rating"] or 0),
            "radio_helps": int(row["radio_helps"] or 0),
        }

    def unlock_player_achievement(self, telegram_id: int, achievement_key: str) -> bool:
        if not achievement_key.strip():
            return False
        with self._connect() as conn:
            self._ensure_player_stats_row(conn, telegram_id)
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO player_achievements(telegram_id, achievement_key, unlocked_at)
                VALUES (?, ?, ?)
                """,
                (telegram_id, achievement_key, utc_now().isoformat()),
            )
            inserted = cursor.rowcount > 0
        if inserted:
            self.save_snapshot()
        return inserted

    def get_player_achievement_keys(self, telegram_id: int) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT achievement_key
                FROM player_achievements
                WHERE telegram_id = ?
                ORDER BY unlocked_at
                """,
                (telegram_id,),
            ).fetchall()
        return {str(row["achievement_key"]) for row in rows}

    def list_player_achievements(self, telegram_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT achievement_key, unlocked_at
                FROM player_achievements
                WHERE telegram_id = ?
                ORDER BY unlocked_at
                """,
                (telegram_id,),
            ).fetchall()
        return [{"achievement_key": row["achievement_key"], "unlocked_at": row["unlocked_at"]} for row in rows]

    def get_rating_leaderboard(self, limit: int = 10) -> list[dict[str, Any]]:
        safe_limit = max(1, min(100, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    c.telegram_id,
                    c.nickname,
                    c.faction,
                    c.gear_power,
                    c.money,
                    COALESCE(ps.rating_points, 0) AS rating_points,
                    COALESCE(ps.quests_completed, 0) AS quests_completed,
                    COALESCE(ps.raids_completed, 0) AS raids_completed,
                    COALESCE(ps.wars_won, 0) AS wars_won,
                    COALESCE(ps.achievements_unlocked, 0) AS achievements_unlocked
                FROM characters c
                LEFT JOIN player_stats ps ON ps.telegram_id = c.telegram_id
                ORDER BY rating_points DESC, c.money DESC, c.gear_power DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_season_rating_leaderboard(self, limit: int = 10) -> list[dict[str, Any]]:
        safe_limit = max(1, min(100, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    c.telegram_id,
                    c.nickname,
                    c.faction,
                    COALESCE(ps.season_rating, 0) AS season_rating
                FROM characters c
                LEFT JOIN player_stats ps ON ps.telegram_id = c.telegram_id
                WHERE COALESCE(ps.season_rating, 0) > 0
                ORDER BY season_rating DESC, c.money DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def reset_all_season_ratings(self) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE player_stats SET season_rating = 0")
        self.save_snapshot()

    def set_avatar_style(self, telegram_id: int, style: str) -> None:
        if style not in {"classic", "realistic"}:
            return
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET avatar_style = ? WHERE telegram_id = ?",
                (style, telegram_id),
            )
        self.save_snapshot()

    def set_truck_owned(self, telegram_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET truck_owned = 1, truck_durability = 100 WHERE telegram_id = ?",
                (telegram_id,),
            )
        self.save_snapshot()

    def set_sleeping_bag_owned(self, telegram_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET sleeping_bag_owned = 1 WHERE telegram_id = ?",
                (telegram_id,),
            )
        self.save_snapshot()

    def clear_sleeping_bag_owned(self, telegram_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET sleeping_bag_owned = 0 WHERE telegram_id = ?",
                (telegram_id,),
            )
        self.save_snapshot()

    def clear_truck_owned(self, telegram_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET truck_owned = 0, truck_durability = 0 WHERE telegram_id = ?",
                (telegram_id,),
            )
        self.save_snapshot()

    def apply_truck_wear(self, telegram_id: int, wear_percent: int) -> int | None:
        character = self.get_character(telegram_id, refresh_energy=False)
        if character is None or not character.truck_owned:
            return None
        wear = max(0, int(wear_percent))
        new_durability = max(0, min(100, int(character.truck_durability) - wear))
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET truck_durability = ? WHERE telegram_id = ?",
                (new_durability, telegram_id),
            )
        self.save_snapshot()
        return new_durability

    def set_truck_durability(self, telegram_id: int, durability: int) -> bool:
        character = self.get_character(telegram_id, refresh_energy=False)
        if character is None:
            return False
        value = max(0, min(100, int(durability)))
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET truck_durability = ? WHERE telegram_id = ?",
                (value, telegram_id),
            )
        self.save_snapshot()
        return True

    def change_diesel(self, telegram_id: int, delta: int) -> bool:
        character = self.get_character(telegram_id, refresh_energy=False)
        if character is None:
            return False
        new_diesel = character.diesel + delta
        if new_diesel < 0:
            return False
        with self._connect() as conn:
            if "fuel" in self._table_columns(conn, "characters"):
                conn.execute(
                    "UPDATE characters SET diesel = ?, fuel = ? WHERE telegram_id = ?",
                    (new_diesel, new_diesel, telegram_id),
                )
            else:
                conn.execute(
                    "UPDATE characters SET diesel = ? WHERE telegram_id = ?",
                    (new_diesel, telegram_id),
                )
        self.save_snapshot()
        return True

    def change_gasoline(self, telegram_id: int, delta: int) -> bool:
        character = self.get_character(telegram_id, refresh_energy=False)
        if character is None:
            return False
        new_gasoline = character.gasoline + delta
        if new_gasoline < 0:
            return False
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET gasoline = ? WHERE telegram_id = ?",
                (new_gasoline, telegram_id),
            )
        self.save_snapshot()
        return True

    def change_fuel(self, telegram_id: int, delta: int) -> bool:
        """Обратная совместимость: fuel = дизель."""
        return self.change_diesel(telegram_id, delta)

    def change_health(self, telegram_id: int, delta: int, *, max_health: int | None = None) -> bool:
        cap = 100 if max_health is None else max(1, int(max_health))
        if max_health is None:
            character = self.get_character(telegram_id, refresh_energy=False)
            if character is None:
                return False
            try:
                from app.game_logic import effective_max_health

                cap = max(cap, int(effective_max_health(character)))
            except Exception:
                cap = 100
        with self._connect() as conn:
            row = conn.execute(
                "SELECT health FROM characters WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
            if row is None:
                return False
            old_health = int(row["health"])
            new_health = max(0, min(cap, old_health + int(delta)))
            conn.execute(
                "UPDATE characters SET health = ? WHERE telegram_id = ?",
                (new_health, telegram_id),
            )
        self.save_snapshot()
        if old_health > 0 and new_health <= 0:
            self.add_player_stat(telegram_id, "deaths", 1)
        return True

    def get_faction_power(self, faction: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(gear_power), 0) AS total_power
                FROM characters
                WHERE faction = ?
                """,
                (faction,),
            ).fetchone()
        return int(row["total_power"]) if row else 0

    def get_faction_active_members_count(self, faction: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total_members
                FROM characters
                WHERE faction = ? AND health > 0
                """,
                (faction,),
            ).fetchone()
        return int(row["total_members"]) if row else 0

    def change_faction_treasury(self, faction: str, delta: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE factions SET treasury = treasury + ? WHERE name = ?",
                (delta, faction),
            )
        self.save_snapshot()

    def withdraw_faction_treasury(self, faction: str, amount: int) -> bool:
        if amount <= 0:
            return False
        with self._connect() as conn:
            row = conn.execute("SELECT treasury FROM factions WHERE name = ?", (faction,)).fetchone()
            if row is None:
                return False
            treasury = int(row["treasury"] or 0)
            if treasury < amount:
                return False
            conn.execute(
                "UPDATE factions SET treasury = treasury - ? WHERE name = ?",
                (amount, faction),
            )
        self.save_snapshot()
        return True

    def get_meta(self, key: str) -> str | None:
        with self._connect() as conn:
            self._ensure_meta_kv_schema(conn)
            row = conn.execute("SELECT value FROM meta_kv WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            self._ensure_meta_kv_schema(conn)
            conn.execute(
                """
                INSERT INTO meta_kv(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
        self.save_snapshot()

    def set_meta_if_absent(self, key: str, value: str) -> bool:
        """Записать meta только если ключа ещё нет. True — ключ создан, False — уже был."""
        with self._connect() as conn:
            self._ensure_meta_kv_schema(conn)
            cur = conn.execute(
                "INSERT OR IGNORE INTO meta_kv(key, value) VALUES (?, ?)",
                (key, value),
            )
            inserted = self._stmt_rowcount(cur) > 0
        if inserted:
            self.save_snapshot()
        return inserted

    def cas_meta_value(self, key: str, *, expected_value: str, new_value: str) -> bool:
        """Compare-and-swap meta: обновить только если текущее value совпадает."""
        with self._connect() as conn:
            self._ensure_meta_kv_schema(conn)
            cur = conn.execute(
                "UPDATE meta_kv SET value = ? WHERE key = ? AND value = ?",
                (new_value, key, expected_value),
            )
            ok = self._stmt_rowcount(cur) > 0
        if ok:
            self.save_snapshot()
        return ok

    def update_json_meta(
        self,
        key: str,
        mutator: Callable[[dict[str, Any]], bool],
    ) -> bool:
        """Атомарно прочитать JSON meta, изменить через mutator, записать если mutator вернул True."""
        with self._connect() as conn:
            self._ensure_meta_kv_schema(conn)
            row = conn.execute("SELECT value FROM meta_kv WHERE key = ?", (key,)).fetchone()
            if row is None:
                return False
            old_value = str(row["value"])
            try:
                parsed = json.loads(old_value)
            except json.JSONDecodeError:
                return False
            if not isinstance(parsed, dict):
                return False
            data = dict(parsed)
            if not mutator(data):
                return False
            new_value = json.dumps(data, ensure_ascii=False)
            cur = conn.execute(
                "UPDATE meta_kv SET value = ? WHERE key = ? AND value = ?",
                (new_value, key, old_value),
            )
            if self._stmt_rowcount(cur) <= 0:
                return False
        self.save_snapshot()
        return True

    def delete_meta(self, key: str) -> None:
        with self._connect() as conn:
            self._ensure_meta_kv_schema(conn)
            conn.execute("DELETE FROM meta_kv WHERE key = ?", (key,))
        self.save_snapshot()

    def list_player_ids(self) -> list[int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT telegram_id FROM characters").fetchall()
        return [int(row["telegram_id"]) for row in rows]

    def total_stars_donated(self, telegram_id: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(stars_amount), 0) AS total
                FROM topup_payments
                WHERE telegram_id = ?
                """,
                (int(telegram_id),),
            ).fetchone()
        return int(row["total"] if row is not None else 0)

    def top_stars_donors(self, limit: int = 5) -> list[dict[str, Any]]:
        safe_limit = max(1, min(20, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.telegram_id,
                       MAX(COALESCE(c.nickname, '')) AS nickname,
                       SUM(p.stars_amount) AS value
                FROM topup_payments p
                LEFT JOIN characters c ON c.telegram_id = p.telegram_id
                GROUP BY p.telegram_id
                HAVING SUM(p.stars_amount) > 0
                ORDER BY value DESC, p.telegram_id ASC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            {
                "telegram_id": int(row["telegram_id"]),
                "nickname": str(row["nickname"] or ""),
                "value": int(row["value"] or 0),
            }
            for row in rows
        ]

    def top_stars_donor(self) -> int | None:
        rows = self.top_stars_donors(limit=1)
        return int(rows[0]["telegram_id"]) if rows else None

    def top_players_by_money(self, limit: int = 5) -> list[dict[str, Any]]:
        safe_limit = max(1, min(20, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT telegram_id, nickname, money AS value
                FROM characters
                ORDER BY money DESC, telegram_id ASC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [
            {
                "telegram_id": int(row["telegram_id"]),
                "nickname": str(row["nickname"] or ""),
                "value": int(row["value"] or 0),
            }
            for row in rows
        ]

    def top_player_by_money(self) -> int | None:
        rows = self.top_players_by_money(limit=1)
        return int(rows[0]["telegram_id"]) if rows else None

    def top_players_by_stat(self, stat_key: str, limit: int = 5) -> list[dict[str, Any]]:
        allowed = {
            "artifacts_found",
            "rating_points",
            "quests_completed",
            "money_earned",
        }
        if stat_key not in allowed:
            return []
        safe_limit = max(1, min(20, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT ps.telegram_id,
                       COALESCE(c.nickname, '') AS nickname,
                       ps.{stat_key} AS value
                FROM player_stats ps
                LEFT JOIN characters c ON c.telegram_id = ps.telegram_id
                WHERE ps.{stat_key} > 0
                ORDER BY ps.{stat_key} DESC, ps.telegram_id ASC
                LIMIT ?
                """,  # noqa: S608
                (safe_limit,),
            ).fetchall()
        return [
            {
                "telegram_id": int(row["telegram_id"]),
                "nickname": str(row["nickname"] or ""),
                "value": int(row["value"] or 0),
            }
            for row in rows
        ]

    def top_player_by_stat(self, stat_key: str) -> int | None:
        rows = self.top_players_by_stat(stat_key, limit=1)
        return int(rows[0]["telegram_id"]) if rows else None

    def list_players(self, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(1, min(500, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT telegram_id, nickname, faction, faction_rank, location, health
                FROM characters
                ORDER BY nickname
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def find_telegram_id_by_nickname(self, nickname: str) -> int | None:
        normalized = (nickname or "").strip().casefold()
        if not normalized:
            return None
        # SQLite lower() не делает casefold для кириллицы — сравниваем в Python.
        with self._connect() as conn:
            rows = conn.execute("SELECT telegram_id, nickname FROM characters").fetchall()
        for row in rows:
            if str(row["nickname"] or "").strip().casefold() == normalized:
                return int(row["telegram_id"])
        return None

    def list_faction_member_ids(self, faction: str) -> list[int]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT telegram_id
                FROM characters
                WHERE faction = ?
                ORDER BY nickname
                """,
                (faction,),
            ).fetchall()
        return [int(row["telegram_id"]) for row in rows]

    def list_characters_at_location(self, location: str) -> list[dict[str, Any]]:
        loc = (location or "").strip()
        if not loc:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT telegram_id, nickname, faction, health
                FROM characters
                WHERE location = ? AND health > 0
                ORDER BY nickname
                """,
                (loc,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_factions(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT name, treasury, leader_id FROM factions ORDER BY name").fetchall()
        return [
            {
                "name": row["name"],
                "treasury": row["treasury"],
                "leader_id": row["leader_id"],
            }
            for row in rows
        ]

    def set_faction_leader(self, faction: str, leader_id: int) -> bool:
        with self._connect() as conn:
            faction_row = conn.execute("SELECT 1 FROM factions WHERE name = ?", (faction,)).fetchone()
            if faction_row is None:
                return False
            character_row = conn.execute(
                "SELECT faction FROM characters WHERE telegram_id = ?",
                (leader_id,),
            ).fetchone()
            if character_row is None or str(character_row["faction"] or "") != faction:
                return False
            conn.execute(
                "UPDATE factions SET leader_id = ? WHERE name = ?",
                (leader_id, faction),
            )
        self.save_snapshot()
        return True

    def get_faction_leader_id(self, faction: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute("SELECT leader_id FROM factions WHERE name = ?", (faction,)).fetchone()
        if row is None or row["leader_id"] is None:
            return None
        return int(row["leader_id"])

    def are_factions_allied(self, faction_a: str, faction_b: str) -> bool:
        if faction_a == faction_b:
            return True
        left, right = sorted((faction_a, faction_b))
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM alliances
                WHERE faction_a = ? AND faction_b = ?
                """,
                (left, right),
            ).fetchone()
        return row is not None

    def set_faction_alliance(self, faction_a: str, faction_b: str, allied: bool) -> bool:
        if faction_a == faction_b:
            return False
        left, right = sorted((faction_a, faction_b))
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM factions WHERE name IN (?, ?)",
                (left, right),
            ).fetchall()
            if len(exists) < 2:
                return False
            if allied:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO alliances(faction_a, faction_b, created_at)
                    VALUES(?, ?, ?)
                    """,
                    (left, right, utc_now().isoformat()),
                )
                conn.execute(
                    """
                    DELETE FROM alliance_requests
                    WHERE (requester_faction = ? AND target_faction = ?)
                       OR (requester_faction = ? AND target_faction = ?)
                    """,
                    (left, right, right, left),
                )
            else:
                conn.execute(
                    """
                    DELETE FROM alliances
                    WHERE faction_a = ? AND faction_b = ?
                    """,
                    (left, right),
                )
        self.save_snapshot()
        return True

    def create_alliance_request(self, requester_faction: str, target_faction: str, proposed_by: int) -> bool:
        if requester_faction == target_faction:
            return False
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT 1 FROM factions WHERE name IN (?, ?)",
                (requester_faction, target_faction),
            ).fetchall()
            if len(rows) < 2:
                return False
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO alliance_requests(requester_faction, target_faction, proposed_by, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (requester_faction, target_faction, proposed_by, utc_now().isoformat()),
            )
            created = int(cursor.rowcount or 0) > 0
        if created:
            self.save_snapshot()
        return created

    def list_incoming_alliance_requests(self, faction: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT requester_faction, target_faction, proposed_by, created_at
                FROM alliance_requests
                WHERE target_faction = ?
                ORDER BY created_at DESC
                """,
                (faction,),
            ).fetchall()
        return [dict(row) for row in rows]

    def remove_alliance_request(self, requester_faction: str, target_faction: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM alliance_requests
                WHERE requester_faction = ? AND target_faction = ?
                """,
                (requester_faction, target_faction),
            )
        self.save_snapshot()

    def list_faction_alliances(self, faction: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT faction_a, faction_b
                FROM alliances
                WHERE faction_a = ? OR faction_b = ?
                ORDER BY faction_a, faction_b
                """,
                (faction, faction),
            ).fetchall()
        allies: list[str] = []
        for row in rows:
            left = str(row["faction_a"])
            right = str(row["faction_b"])
            allies.append(right if left == faction else left)
        return allies

    def get_locations(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT name, point_type, controlled_by, npc_power, defense_bonus
                FROM locations
                ORDER BY name
                """
            ).fetchall()
        return [
            {
                "name": row["name"],
                "point_type": row["point_type"],
                "controlled_by": row["controlled_by"],
                "npc_power": row["npc_power"],
                "defense_bonus": int(row["defense_bonus"] or 0),
            }
            for row in rows
        ]

    def get_location(self, location_name: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT name, point_type, controlled_by, npc_power, defense_bonus
                FROM locations
                WHERE name = ?
                """,
                (location_name,),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["defense_bonus"] = int(data.get("defense_bonus") or 0)
        return data

    def set_location_control(self, location_name: str, faction: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE locations SET controlled_by = ? WHERE name = ?",
                (faction, location_name),
            )
        self.save_snapshot()

    def increment_location_defense_bonus(self, location_name: str, delta: int = 1) -> int | None:
        """Увеличить пассивную защиту базы. Возвращает новый бонус или None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT defense_bonus FROM locations WHERE name = ?",
                (location_name,),
            ).fetchone()
            if row is None:
                return None
            new_bonus = max(0, int(row["defense_bonus"] or 0) + int(delta))
            conn.execute(
                "UPDATE locations SET defense_bonus = ? WHERE name = ?",
                (new_bonus, location_name),
            )
        self.save_snapshot()
        return new_bonus

    def set_location_npc_power(self, location_name: str, npc_power: int) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT point_type FROM locations WHERE name = ?",
                (location_name,),
            ).fetchone()
            min_power = (
                BASE_LOCATION_NPC_POWER
                if row is not None and row["point_type"] == "база"
                else REGULAR_LOCATION_NPC_POWER
            )
            safe_power = max(min_power, npc_power)
            conn.execute(
                "UPDATE locations SET npc_power = ? WHERE name = ?",
                (safe_power, location_name),
            )
        self.save_snapshot()

    def run_periodic_sync(self) -> None:
        self.save_snapshot()

    def get_characters_by_ids(self, telegram_ids: list[int]) -> list[Character]:
        if not telegram_ids:
            return []
        placeholders = ",".join("?" for _ in telegram_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM characters WHERE telegram_id IN ({placeholders})",  # noqa: S608
                tuple(telegram_ids),
            ).fetchall()
        return [self._row_to_character(row) for row in rows]

    def get_open_raid_for_faction(self, faction: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, faction, location, leader_id, status, created_at, started_at, finished_at, result_text,
                       raid_kind, target_faction
                FROM raids
                WHERE faction = ? AND status = 'open'
                ORDER BY id DESC
                LIMIT 1
                """,
                (faction,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_in_progress_raid_for_faction(self, faction: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, faction, location, leader_id, status, created_at, started_at, finished_at, result_text,
                       raid_kind, target_faction
                FROM raids
                WHERE faction = ? AND status = 'in_progress'
                ORDER BY id DESC
                LIMIT 1
                """,
                (faction,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def create_raid(
        self,
        faction: str,
        location: str,
        leader_id: int,
        raid_kind: str = "lair",
        target_faction: str | None = None,
    ) -> int:
        now_iso = utc_now().isoformat()
        with self._connect() as conn:
            raid_id = self._insert_returning_id(
                conn,
                """
                INSERT INTO raids(faction, location, leader_id, status, created_at, raid_kind, target_faction)
                VALUES (?, ?, ?, 'open', ?, ?, ?)
                """,
                (faction, location, leader_id, now_iso, raid_kind, target_faction),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO raid_members(raid_id, telegram_id, joined_at)
                VALUES (?, ?, ?)
                """,
                (raid_id, leader_id, now_iso),
            )
        self.save_snapshot()
        return raid_id

    def add_raid_member(self, raid_id: int, telegram_id: int) -> bool:
        with self._connect() as conn:
            raid_row = conn.execute(
                "SELECT status, faction FROM raids WHERE id = ?",
                (raid_id,),
            ).fetchone()
            if raid_row is None or raid_row["status"] != "open":
                return False
            member_row = conn.execute(
                "SELECT faction FROM characters WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
            if member_row is None or not str(member_row["faction"] or ""):
                return False
            conn.execute(
                """
                INSERT OR IGNORE INTO raid_members(raid_id, telegram_id, joined_at)
                VALUES (?, ?, ?)
                """,
                (raid_id, telegram_id, utc_now().isoformat()),
            )
        self.save_snapshot()
        return True

    def get_raid(self, raid_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, faction, location, leader_id, status, created_at, started_at, finished_at, result_text,
                       raid_kind, target_faction
                FROM raids
                WHERE id = ?
                """,
                (raid_id,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_open_war_lobby_for_faction(self, faction: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, host_faction, location, leader_id, status, created_at, started_at, finished_at, result_text
                FROM war_lobbies
                WHERE host_faction = ? AND status = 'open'
                ORDER BY id DESC
                LIMIT 1
                """,
                (faction,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def create_war_lobby(self, host_faction: str, location: str, leader_id: int) -> int:
        now_iso = utc_now().isoformat()
        with self._connect() as conn:
            war_id = self._insert_returning_id(
                conn,
                """
                INSERT INTO war_lobbies(host_faction, location, leader_id, status, created_at)
                VALUES (?, ?, ?, 'open', ?)
                """,
                (host_faction, location, leader_id, now_iso),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO war_lobby_members(war_id, telegram_id, joined_at)
                VALUES (?, ?, ?)
                """,
                (war_id, leader_id, now_iso),
            )
        self.save_snapshot()
        return war_id

    def add_war_lobby_member(self, war_id: int, telegram_id: int) -> bool:
        with self._connect() as conn:
            lobby = conn.execute(
                "SELECT status FROM war_lobbies WHERE id = ?",
                (war_id,),
            ).fetchone()
            if lobby is None or str(lobby["status"]) != "open":
                return False
            if conn.execute("SELECT 1 FROM characters WHERE telegram_id = ?", (telegram_id,)).fetchone() is None:
                return False
            conn.execute(
                """
                INSERT OR IGNORE INTO war_lobby_members(war_id, telegram_id, joined_at)
                VALUES (?, ?, ?)
                """,
                (war_id, telegram_id, utc_now().isoformat()),
            )
        self.save_snapshot()
        return True

    def get_war_lobby_member_ids(self, war_id: int) -> list[int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT telegram_id FROM war_lobby_members WHERE war_id = ? ORDER BY joined_at",
                (war_id,),
            ).fetchall()
        return [int(row["telegram_id"]) for row in rows]

    def cancel_war_lobby(self, war_id: int, leader_id: int) -> dict[str, Any] | None:
        """Распустить открытое военное лобби. Только создатель (leader_id)."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, host_faction, location, leader_id, status
                FROM war_lobbies
                WHERE id = ? AND status = 'open'
                """,
                (war_id,),
            ).fetchone()
            if row is None:
                return None
            if int(row["leader_id"]) != int(leader_id):
                return None
            now_iso = utc_now().isoformat()
            cursor = conn.execute(
                """
                UPDATE war_lobbies
                SET status = 'cancelled', finished_at = ?, result_text = ?
                WHERE id = ? AND leader_id = ? AND status = 'open'
                """,
                (
                    now_iso,
                    f"Лобби распущено создателем (telegram_id={leader_id}).",
                    war_id,
                    leader_id,
                ),
            )
            if int(cursor.rowcount or 0) <= 0:
                return None
            result = dict(row)
        self.save_snapshot()
        return result

    def start_war_lobby_assault(self, war_id: int) -> bool:
        """Перевести открытое лобби в тактический штурм (in_progress)."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE war_lobbies
                SET status = 'in_progress', started_at = ?
                WHERE id = ? AND status = 'open'
                """,
                (utc_now().isoformat(), war_id),
            )
            ok = int(cursor.rowcount or 0) > 0
        if ok:
            self.save_snapshot()
        return ok

    def finish_war_lobby(self, war_id: int, status: str, result_text: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE war_lobbies
                SET status = ?, started_at = COALESCE(started_at, ?), finished_at = ?, result_text = ?
                WHERE id = ?
                """,
                (status, utc_now().isoformat(), utc_now().isoformat(), result_text, war_id),
            )
        self.save_snapshot()

    def get_raid_member_ids(self, raid_id: int) -> list[int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT telegram_id FROM raid_members WHERE raid_id = ? ORDER BY joined_at",
                (raid_id,),
            ).fetchall()
        return [int(row["telegram_id"]) for row in rows]

    def finish_raid(self, raid_id: int, status: str, result_text: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE raids
                SET status = ?, started_at = COALESCE(started_at, ?), finished_at = ?, result_text = ?
                WHERE id = ?
                """,
                (status, utc_now().isoformat(), utc_now().isoformat(), result_text, raid_id),
            )
        self.save_snapshot()

    def start_raid_assault(self, raid_id: int) -> bool:
        """Перевести открытый рейд в тактический режим (in_progress)."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE raids
                SET status = 'in_progress', started_at = ?
                WHERE id = ? AND status = 'open'
                """,
                (utc_now().isoformat(), raid_id),
            )
            ok = int(cursor.rowcount or 0) > 0
        if ok:
            self.save_snapshot()
        return ok

    def list_open_raids_led_by(self, leader_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, faction, location, leader_id, status, created_at, raid_kind, target_faction
                FROM raids
                WHERE leader_id = ? AND status = 'open'
                ORDER BY id DESC
                """,
                (leader_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def cancel_raid(self, raid_id: int, leader_id: int) -> dict[str, Any] | None:
        """Отменить открытый рейд. Только лидер-создатель. Возвращает данные рейда или None."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, faction, location, leader_id, status
                FROM raids
                WHERE id = ? AND status = 'open'
                """,
                (raid_id,),
            ).fetchone()
            if row is None:
                return None
            if int(row["leader_id"]) != int(leader_id):
                return None
            now_iso = utc_now().isoformat()
            cursor = conn.execute(
                """
                UPDATE raids
                SET status = 'cancelled', finished_at = ?, result_text = ?
                WHERE id = ? AND leader_id = ? AND status = 'open'
                """,
                (
                    now_iso,
                    f"Рейд отменён создателем (telegram_id={leader_id}).",
                    raid_id,
                    leader_id,
                ),
            )
            if int(cursor.rowcount or 0) <= 0:
                return None
            result = dict(row)
        self.save_snapshot()
        return result

    def cancel_all_open_raids_led_by(self, leader_id: int) -> list[dict[str, Any]]:
        open_raids = self.list_open_raids_led_by(leader_id)
        cancelled: list[dict[str, Any]] = []
        for raid in open_raids:
            done = self.cancel_raid(int(raid["id"]), leader_id)
            if done is not None:
                cancelled.append(done)
        return cancelled

    def cancel_all_open_war_lobbies_led_by(self, leader_id: int) -> int:
        tid = int(leader_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id FROM war_lobbies
                WHERE leader_id = ? AND status = 'open'
                """,
                (tid,),
            ).fetchall()
        cancelled = 0
        for row in rows:
            if self.cancel_war_lobby(int(row["id"]), tid) is not None:
                cancelled += 1
        return cancelled

    def get_faction_warehouse(self, faction: str) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT item_key, amount FROM faction_warehouse WHERE faction = ? ORDER BY item_key",
                (faction,),
            ).fetchall()
        return {str(row["item_key"]): int(row["amount"]) for row in rows}

    def change_faction_warehouse_item(self, faction: str, item_key: str, delta: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT amount FROM faction_warehouse
                WHERE faction = ? AND item_key = ?
                """,
                (faction, item_key),
            ).fetchone()
            current = int(row["amount"]) if row else 0
            new_amount = current + delta
            if new_amount < 0:
                return False
            conn.execute(
                """
                INSERT INTO faction_warehouse(faction, item_key, amount)
                VALUES (?, ?, ?)
                ON CONFLICT(faction, item_key) DO UPDATE SET amount = excluded.amount
                """,
                (faction, item_key, new_amount),
            )
        self.save_snapshot()
        return True

    def create_auction(
        self,
        seller_id: int,
        faction: str,
        item_key: str,
        amount: int,
        price: int,
    ) -> int:
        with self._connect() as conn:
            auction_id = self._insert_returning_id(
                conn,
                """
                INSERT INTO auctions(seller_id, faction, item_key, amount, price, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'open', ?)
                """,
                (seller_id, faction, item_key, amount, price, utc_now().isoformat()),
            )
        self.save_snapshot()
        return auction_id

    def list_open_auctions(self, faction: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if faction is None:
                rows = conn.execute(
                    """
                    SELECT id, seller_id, faction, item_key, amount, price, status, buyer_id, created_at, closed_at
                    FROM auctions
                    WHERE status = 'open'
                    ORDER BY id DESC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, seller_id, faction, item_key, amount, price, status, buyer_id, created_at, closed_at
                    FROM auctions
                    WHERE status = 'open' AND faction = ?
                    ORDER BY id DESC
                    """,
                    (faction,),
                ).fetchall()
        return [dict(row) for row in rows]

    def list_open_equipment_market_lots(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, seller_id, faction, item_key, amount, price, status, buyer_id, created_at, closed_at
                FROM auctions
                WHERE status = 'open'
                ORDER BY id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_open_auction(self, auction_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, seller_id, faction, item_key, amount, price, status, buyer_id, created_at, closed_at
                FROM auctions
                WHERE id = ? AND status = 'open'
                """,
                (auction_id,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def close_auction(
        self,
        auction_id: int,
        buyer_id: int | None = None,
        status: str = "sold",
    ) -> bool:
        if status not in {"sold", "cancelled"}:
            return False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM auctions WHERE id = ?",
                (auction_id,),
            ).fetchone()
            if row is None or row["status"] != "open":
                return False
            conn.execute(
                """
                UPDATE auctions
                SET status = ?, buyer_id = ?, closed_at = ?
                WHERE id = ?
                """,
                (status, buyer_id, utc_now().isoformat(), auction_id),
            )
        self.save_snapshot()
        return True

    def complete_auction_sale(
        self,
        lot_id: int,
        *,
        buyer_id: int,
        seller_id: int,
        price: int,
        seller_income: int,
        item_key: str,
        amount: int,
    ) -> bool:
        """Атомарно: списать деньги у покупателя, закрыть лот, выплатить продавцу, выдать предмет."""
        try:
            with self._connect() as conn:
                lot = conn.execute(
                    "SELECT status FROM auctions WHERE id = ?",
                    (lot_id,),
                ).fetchone()
                if lot is None or lot["status"] != "open":
                    return False
                buyer_row = conn.execute(
                    "SELECT money, inventory_json FROM characters WHERE telegram_id = ?",
                    (buyer_id,),
                ).fetchone()
                if buyer_row is None or int(buyer_row["money"]) < price:
                    return False
                cur = conn.execute(
                    "UPDATE characters SET money = money - ? WHERE telegram_id = ? AND money >= ?",
                    (price, buyer_id, price),
                )
                if self._stmt_rowcount(cur) <= 0:
                    raise RuntimeError("auction_buyer_deduct_failed")
                cur = conn.execute(
                    """
                    UPDATE auctions
                    SET status = ?, buyer_id = ?, closed_at = ?
                    WHERE id = ? AND status = 'open'
                    """,
                    ("sold", buyer_id, utc_now().isoformat(), lot_id),
                )
                if self._stmt_rowcount(cur) <= 0:
                    raise RuntimeError("auction_close_failed")
                conn.execute(
                    "UPDATE characters SET money = money + ? WHERE telegram_id = ?",
                    (seller_income, seller_id),
                )
                try:
                    inventory = json.loads(buyer_row["inventory_json"] or "{}")
                except json.JSONDecodeError:
                    inventory = {}
                inventory[item_key] = int(inventory.get(item_key, 0)) + amount
                conn.execute(
                    "UPDATE characters SET inventory_json = ? WHERE telegram_id = ?",
                    (json.dumps(inventory, ensure_ascii=False), buyer_id),
                )
        except RuntimeError:
            return False
        self.save_snapshot()
        return True

    def cancel_auction_and_refund(
        self,
        auction_id: int,
        *,
        seller_id: int,
        item_key: str,
        amount: int,
    ) -> bool:
        """Атомарно отменить открытый лот и вернуть предмет продавцу."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT status, seller_id, item_key, amount
                    FROM auctions WHERE id = ?
                    """,
                    (auction_id,),
                ).fetchone()
                if row is None or row["status"] != "open":
                    return False
                if int(row["seller_id"]) != int(seller_id):
                    return False
                if str(row["item_key"]) != str(item_key) or int(row["amount"]) != int(amount):
                    return False
                cur = conn.execute(
                    """
                    UPDATE auctions
                    SET status = ?, buyer_id = ?, closed_at = ?
                    WHERE id = ? AND status = 'open'
                    """,
                    ("cancelled", None, utc_now().isoformat(), auction_id),
                )
                if self._stmt_rowcount(cur) <= 0:
                    raise RuntimeError("auction_cancel_failed")
                seller_row = conn.execute(
                    "SELECT inventory_json FROM characters WHERE telegram_id = ?",
                    (seller_id,),
                ).fetchone()
                if seller_row is None:
                    raise RuntimeError("auction_seller_missing")
                try:
                    inventory = json.loads(seller_row["inventory_json"] or "{}")
                except json.JSONDecodeError:
                    inventory = {}
                inventory[item_key] = int(inventory.get(item_key, 0)) + amount
                conn.execute(
                    "UPDATE characters SET inventory_json = ? WHERE telegram_id = ?",
                    (json.dumps(inventory, ensure_ascii=False), seller_id),
                )
        except RuntimeError:
            return False
        self.save_snapshot()
        return True

    def upsert_map_event(
        self,
        location: str,
        event_type: str,
        modifier: int,
        description: str,
        expires_at: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO map_events(location, event_type, modifier, description, expires_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(location) DO UPDATE SET
                    event_type = excluded.event_type,
                    modifier = excluded.modifier,
                    description = excluded.description,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (location, event_type, modifier, description, expires_at, utc_now().isoformat()),
            )
        self.save_snapshot()

    def delete_expired_map_events(self) -> None:
        now_iso = utc_now().isoformat()
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM map_events WHERE expires_at <= ?",
                (now_iso,),
            )
        self.save_snapshot()

    def get_map_events(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT location, event_type, modifier, description, expires_at, updated_at
                FROM map_events
                ORDER BY location
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def apply_topup_payment(
        self,
        telegram_id: int,
        payment_charge_id: str,
        stars_amount: int,
        ru_amount: int,
        *,
        to_faction: str | None = None,
    ) -> tuple[bool, bool]:
        if stars_amount <= 0 or ru_amount <= 0 or not payment_charge_id.strip():
            return False, False
        faction = str(to_faction or "").strip() or None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT money FROM characters WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
            if row is None:
                return False, False
            if faction:
                fac = conn.execute("SELECT name FROM factions WHERE name = ?", (faction,)).fetchone()
                if fac is None:
                    return False, False
            new_money = int(row["money"]) + ru_amount
            conn.savepoint("sp_topup")
            try:
                conn.execute(
                    """
                    INSERT INTO topup_payments(
                        payment_charge_id, telegram_id, stars_amount, ru_amount, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        payment_charge_id,
                        telegram_id,
                        stars_amount,
                        ru_amount,
                        utc_now().isoformat(),
                    ),
                )
                conn.release_savepoint("sp_topup")
            except integrity_error_types():
                conn.rollback_to_savepoint("sp_topup")
                return False, True

            if faction:
                conn.execute(
                    "UPDATE factions SET treasury = treasury + ? WHERE name = ?",
                    (ru_amount, faction),
                )
            else:
                conn.execute(
                    "UPDATE characters SET money = ? WHERE telegram_id = ?",
                    (new_money, telegram_id),
                )
        self.save_snapshot()
        return True, False

    def _set_inventory(self, telegram_id: int, inventory: dict[str, int]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET inventory_json = ? WHERE telegram_id = ?",
                (json.dumps(inventory, ensure_ascii=False), telegram_id),
            )

    def get_personal_stash(self, telegram_id: int) -> dict[str, int]:
        with self._connect() as conn:
            self._ensure_characters_schema(conn)
            row = conn.execute(
                "SELECT stash_json FROM characters WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
        if row is None:
            return {}
        raw = Storage._row_get(row, "stash_json", "{}")
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            return {}
        result: dict[str, int] = {}
        for key, value in data.items():
            amount = int(value or 0)
            if amount > 0:
                result[str(key)] = amount
        return result

    def set_personal_stash(self, telegram_id: int, stash: dict[str, int]) -> None:
        cleaned = {str(k): int(v) for k, v in stash.items() if int(v) > 0}
        with self._connect() as conn:
            self._ensure_characters_schema(conn)
            conn.execute(
                "UPDATE characters SET stash_json = ? WHERE telegram_id = ?",
                (json.dumps(cleaned, ensure_ascii=False), telegram_id),
            )
        self.save_snapshot()

    def _set_equipment(self, telegram_id: int, equipment: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE characters SET equipment_json = ? WHERE telegram_id = ?",
                (json.dumps(equipment, ensure_ascii=False), telegram_id),
            )

    def _ensure_player_stats_rows(self, conn: DbConnection) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO player_stats(telegram_id)
            SELECT telegram_id FROM characters
            """
        )

    def _ensure_player_stats_schema(self, conn: DbConnection) -> None:
        column_names = self._table_columns(conn, "player_stats")
        add_columns = [
            (
                "artifacts_found",
                "ALTER TABLE player_stats ADD COLUMN artifacts_found INTEGER NOT NULL DEFAULT 0",
            ),
            (
                "deaths",
                "ALTER TABLE player_stats ADD COLUMN deaths INTEGER NOT NULL DEFAULT 0",
            ),
            (
                "enemy_bases_captured",
                "ALTER TABLE player_stats ADD COLUMN enemy_bases_captured INTEGER NOT NULL DEFAULT 0",
            ),
            (
                "season_rating",
                "ALTER TABLE player_stats ADD COLUMN season_rating INTEGER NOT NULL DEFAULT 0",
            ),
            (
                "radio_helps",
                "ALTER TABLE player_stats ADD COLUMN radio_helps INTEGER NOT NULL DEFAULT 0",
            ),
        ]
        for col_name, ddl in add_columns:
            if col_name in column_names:
                continue
            sp = f"sp_stats_add_{col_name}"
            try:
                conn.savepoint(sp)
                conn.execute(ddl)
                conn.release_savepoint(sp)
                column_names.add(col_name)
            except Exception:
                try:
                    conn.rollback_to_savepoint(sp)
                except Exception:
                    pass

        # Для старых аккаунтов с уже купленным грузовиком проставляем стартовую прочность.
        if "truck_durability" in self._table_columns(conn, "characters"):
            conn.execute(
                """
                UPDATE characters
                SET truck_durability = 100
                WHERE truck_owned = 1 AND COALESCE(truck_durability, 0) <= 0
                """
            )
        # Аналогично для Нивы — старым владельцам проставляем стартовую прочность.
        if "niva_durability" in self._table_columns(conn, "characters"):
            conn.execute(
                """
                UPDATE characters
                SET niva_durability = 100
                WHERE niva_owned = 1 AND COALESCE(niva_durability, 0) <= 0
                """
            )

    def _ensure_player_stats_row(self, conn: DbConnection, telegram_id: int) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO player_stats(telegram_id) VALUES (?)",
            (telegram_id,),
        )

    def _enforce_location_power_baseline(self, conn: DbConnection) -> None:
        conn.execute(
            """
            UPDATE locations
            SET npc_power = CASE
                WHEN point_type = 'база' AND npc_power < ? THEN ?
                WHEN point_type <> 'база' AND npc_power < ? THEN ?
                ELSE npc_power
            END
            """,
            (
                BASE_LOCATION_NPC_POWER,
                BASE_LOCATION_NPC_POWER,
                REGULAR_LOCATION_NPC_POWER,
                REGULAR_LOCATION_NPC_POWER,
            ),
        )

    def _ensure_bigint_telegram_ids(self, conn: DbConnection) -> None:
        """Upgrade telegram id columns to BIGINT on Postgres (INTEGER overflows)."""
        if conn.backend != "postgres":
            return
        for table, column in TELEGRAM_ID_COLUMNS:
            sp = f"sp_bigint_{table}_{column}"
            try:
                conn.savepoint(sp)
                row = conn.execute(
                    """
                    SELECT data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = ?
                      AND column_name = ?
                    """,
                    (table, column),
                ).fetchone()
                if row is None:
                    conn.release_savepoint(sp)
                    continue
                data_type = str(row["data_type"] or "").lower()
                if data_type in {"bigint", "int8"}:
                    conn.release_savepoint(sp)
                    continue
                if data_type in {"integer", "int", "int4", "smallint", "int2"}:
                    conn.execute(
                        f"ALTER TABLE {table} ALTER COLUMN {column} TYPE BIGINT"  # noqa: S608
                    )
                conn.release_savepoint(sp)
            except Exception:
                try:
                    conn.rollback_to_savepoint(sp)
                except Exception:
                    pass

    def _ensure_characters_schema(self, conn: DbConnection) -> None:
        column_names = self._table_columns(conn, "characters")
        add_columns = [
            ("player_uid", "ALTER TABLE characters ADD COLUMN player_uid TEXT"),
            ("avatar_style", "ALTER TABLE characters ADD COLUMN avatar_style TEXT"),
            ("radiation", "ALTER TABLE characters ADD COLUMN radiation INTEGER NOT NULL DEFAULT 0"),
            ("hunger", "ALTER TABLE characters ADD COLUMN hunger INTEGER NOT NULL DEFAULT 0"),
            ("thirst", "ALTER TABLE characters ADD COLUMN thirst INTEGER NOT NULL DEFAULT 0"),
            ("needs_updated_at", "ALTER TABLE characters ADD COLUMN needs_updated_at TEXT"),
            ("survival_damage_at", "ALTER TABLE characters ADD COLUMN survival_damage_at TEXT"),
            (
                "sleeping_bag_owned",
                "ALTER TABLE characters ADD COLUMN sleeping_bag_owned INTEGER NOT NULL DEFAULT 0",
            ),
            (
                "truck_durability",
                "ALTER TABLE characters ADD COLUMN truck_durability INTEGER NOT NULL DEFAULT 0",
            ),
            ("faction_rank", "ALTER TABLE characters ADD COLUMN faction_rank TEXT"),
            ("niva_owned", "ALTER TABLE characters ADD COLUMN niva_owned INTEGER NOT NULL DEFAULT 0"),
            (
                "niva_durability",
                "ALTER TABLE characters ADD COLUMN niva_durability INTEGER NOT NULL DEFAULT 0",
            ),
            ("bicycle_owned", "ALTER TABLE characters ADD COLUMN bicycle_owned INTEGER NOT NULL DEFAULT 0"),
            ("travel_destination", "ALTER TABLE characters ADD COLUMN travel_destination TEXT"),
            ("travel_arrives_at", "ALTER TABLE characters ADD COLUMN travel_arrives_at TEXT"),
            ("travel_transport", "ALTER TABLE characters ADD COLUMN travel_transport TEXT"),
            ("active_contract_json", "ALTER TABLE characters ADD COLUMN active_contract_json TEXT"),
            ("gasoline", "ALTER TABLE characters ADD COLUMN gasoline INTEGER NOT NULL DEFAULT 0"),
            ("diesel", "ALTER TABLE characters ADD COLUMN diesel INTEGER NOT NULL DEFAULT 0"),
            ("stash_json", "ALTER TABLE characters ADD COLUMN stash_json TEXT NOT NULL DEFAULT '{}'"),
        ]
        for col_name, ddl in add_columns:
            if col_name in column_names:
                continue
            sp = f"sp_char_add_{col_name}"
            try:
                conn.savepoint(sp)
                conn.execute(ddl)
                conn.release_savepoint(sp)
                column_names.add(col_name)
            except Exception:
                try:
                    conn.rollback_to_savepoint(sp)
                except Exception:
                    pass

        column_names = self._table_columns(conn, "characters")
        if "fuel" in column_names and "diesel" in column_names:
            conn.execute(
                """
                UPDATE characters
                SET diesel = CASE WHEN diesel > 0 THEN diesel ELSE COALESCE(fuel, 0) END
                WHERE diesel = 0 AND COALESCE(fuel, 0) > 0
                """
            )

        # Backfill ID-address for old rows created before this column existed.
        rows = conn.execute(
            "SELECT telegram_id FROM characters WHERE player_uid IS NULL OR TRIM(player_uid) = ''"
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE characters SET player_uid = ? WHERE telegram_id = ?",
                (build_player_uid(int(row["telegram_id"])), int(row["telegram_id"])),
            )
        # Снимаем дубли player_uid перед уникальным индексом, иначе CREATE INDEX
        # валит всю транзакцию create_character на Postgres.
        dup_rows = conn.execute(
            """
            SELECT player_uid
            FROM characters
            WHERE player_uid IS NOT NULL AND TRIM(player_uid) <> ''
            GROUP BY player_uid
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        for dup in dup_rows:
            uid = dup["player_uid"]
            owners = conn.execute(
                """
                SELECT telegram_id
                FROM characters
                WHERE player_uid = ?
                ORDER BY telegram_id
                """,
                (uid,),
            ).fetchall()
            for owner in owners[1:]:
                tid = int(owner["telegram_id"])
                conn.execute(
                    "UPDATE characters SET player_uid = ? WHERE telegram_id = ?",
                    (build_player_uid(tid), tid),
                )
        try:
            conn.savepoint("sp_uid_index")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_characters_player_uid ON characters(player_uid)"
            )
            conn.release_savepoint("sp_uid_index")
        except Exception:
            try:
                conn.rollback_to_savepoint("sp_uid_index")
            except Exception:
                pass
            # Не блокируем создание персонажа из-за индекса.
        conn.execute(
            """
            UPDATE characters
            SET avatar_style = 'classic'
            WHERE avatar_style IS NULL OR TRIM(avatar_style) = ''
            """
        )
        now_iso = utc_now().isoformat()
        conn.execute(
            """
            UPDATE characters
            SET needs_updated_at = ?
            WHERE needs_updated_at IS NULL OR TRIM(needs_updated_at) = ''
            """,
            (now_iso,),
        )
        conn.execute(
            """
            UPDATE characters
            SET survival_damage_at = ?
            WHERE survival_damage_at IS NULL OR TRIM(survival_damage_at) = ''
            """,
            (now_iso,),
        )
        # Старым бойцам без звания — стартовый ранг группировки.
        try:
            from app.faction_ranks import DEFAULT_RANK_KEY, FACTION_RANKS

            for faction_name in FACTION_RANKS:
                conn.execute(
                    """
                    UPDATE characters
                    SET faction_rank = ?
                    WHERE faction = ?
                      AND (faction_rank IS NULL OR TRIM(faction_rank) = '')
                    """,
                    (DEFAULT_RANK_KEY, faction_name),
                )
        except Exception:
            logger.exception("Failed to backfill default faction ranks")
        rows = conn.execute(
            "SELECT telegram_id, equipment_json FROM characters"
        ).fetchall()
        for row in rows:
            try:
                equipment = json.loads(row["equipment_json"] or "{}")
            except json.JSONDecodeError:
                equipment = {}
            if not isinstance(equipment, dict):
                equipment = {}
            changed = False
            if "weapon" not in equipment:
                equipment["weapon"] = "Нож"
                changed = True
            if "armor" not in equipment:
                equipment["armor"] = "Куртка новичка"
                changed = True
            if "weapon_durability" not in equipment:
                equipment["weapon_durability"] = 100
                changed = True
            if "armor_durability" not in equipment:
                equipment["armor_durability"] = 100
                changed = True
            if "armor_upgrade_level" not in equipment:
                equipment["armor_upgrade_level"] = 0
                changed = True
            if changed:
                conn.execute(
                    "UPDATE characters SET equipment_json = ? WHERE telegram_id = ?",
                    (json.dumps(equipment, ensure_ascii=False), int(row["telegram_id"])),
                )

    @staticmethod
    def _row_get(row: Any, key: str, default: Any = None) -> Any:
        try:
            keys = row.keys() if hasattr(row, "keys") else None
            if keys is not None and key not in keys:
                return default
            value = row[key]
            return default if value is None else value
        except (KeyError, IndexError, TypeError):
            return default

    @staticmethod
    def _row_to_character(row: Any) -> Character:
        inventory_raw = Storage._row_get(row, "inventory_json", "{}")
        equipment_raw = Storage._row_get(row, "equipment_json", "{}")
        try:
            inventory = json.loads(inventory_raw or "{}")
        except (TypeError, json.JSONDecodeError):
            inventory = {}
        if not isinstance(inventory, dict):
            inventory = {}
        try:
            equipment = json.loads(equipment_raw or "{}")
        except (TypeError, json.JSONDecodeError):
            equipment = {"weapon": "Нож", "armor": "Куртка новичка"}
        if not isinstance(equipment, dict):
            equipment = {"weapon": "Нож", "armor": "Куртка новичка"}
        if "weapon" not in equipment:
            equipment["weapon"] = "Нож"
        if "armor" not in equipment:
            equipment["armor"] = "Куртка новичка"
        try:
            weapon_durability = int(equipment.get("weapon_durability", 100))
        except (TypeError, ValueError):
            weapon_durability = 100
        try:
            armor_durability = int(equipment.get("armor_durability", 100))
        except (TypeError, ValueError):
            armor_durability = 100
        equipment["weapon_durability"] = max(0, min(100, weapon_durability))
        equipment["armor_durability"] = max(0, min(100, armor_durability))
        try:
            armor_upgrade_level = int(equipment.get("armor_upgrade_level", 0))
        except (TypeError, ValueError):
            armor_upgrade_level = 0
        equipment["armor_upgrade_level"] = max(0, armor_upgrade_level)

        def _as_int(value: Any, default: int = 0) -> int:
            try:
                if value is None:
                    return default
                return int(value)
            except (TypeError, ValueError):
                return default

        def _as_dt(value: Any) -> datetime:
            if isinstance(value, datetime):
                return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            if value is None or str(value).strip() == "":
                return utc_now()
            try:
                return datetime.fromisoformat(str(value))
            except ValueError:
                return utc_now()

        telegram_id = _as_int(Storage._row_get(row, "telegram_id", 0))
        return Character(
            telegram_id=telegram_id,
            player_uid=(
                Storage._row_get(row, "player_uid")
                or build_player_uid(telegram_id)
            ),
            avatar_style=(Storage._row_get(row, "avatar_style") or "classic"),
            nickname=str(Storage._row_get(row, "nickname") or "Сталкер"),
            gender=str(Storage._row_get(row, "gender") or "Мужской"),
            faction=Storage._row_get(row, "faction"),
            faction_rank=Storage._row_get(row, "faction_rank"),
            money=_as_int(Storage._row_get(row, "money"), 3000),
            energy=_as_int(Storage._row_get(row, "energy"), 100),
            max_energy=_as_int(Storage._row_get(row, "max_energy"), 100),
            health=_as_int(Storage._row_get(row, "health"), 100),
            gear_power=_as_int(Storage._row_get(row, "gear_power"), 2),
            location=Storage._row_get(row, "location") or "База новичков",
            inventory=inventory,
            equipment=equipment,
            truck_owned=bool(Storage._row_get(row, "truck_owned", 0)),
            truck_durability=max(0, min(100, _as_int(Storage._row_get(row, "truck_durability"), 0))),
            niva_owned=bool(Storage._row_get(row, "niva_owned", 0)),
            niva_durability=max(0, min(100, _as_int(Storage._row_get(row, "niva_durability"), 0))),
            bicycle_owned=bool(Storage._row_get(row, "bicycle_owned", 0)),
            sleeping_bag_owned=bool(Storage._row_get(row, "sleeping_bag_owned", 0)),
            diesel=_as_int(
                Storage._row_get(row, "diesel"),
                _as_int(Storage._row_get(row, "fuel"), 0),
            ),
            gasoline=_as_int(Storage._row_get(row, "gasoline"), 0),
            travel_destination=Storage._row_get(row, "travel_destination"),
            travel_arrives_at=(
                _as_dt(Storage._row_get(row, "travel_arrives_at"))
                if Storage._row_get(row, "travel_arrives_at")
                else None
            ),
            travel_transport=Storage._row_get(row, "travel_transport"),
            active_contract_json=Storage._row_get(row, "active_contract_json"),
            energy_updated_at=_as_dt(Storage._row_get(row, "energy_updated_at")),
            radiation=max(0, min(SURVIVAL_NEED_MAX, _as_int(Storage._row_get(row, "radiation"), 0))),
            hunger=max(0, min(SURVIVAL_NEED_MAX, _as_int(Storage._row_get(row, "hunger"), 0))),
            thirst=max(0, min(SURVIVAL_NEED_MAX, _as_int(Storage._row_get(row, "thirst"), 0))),
            needs_updated_at=_as_dt(Storage._row_get(row, "needs_updated_at")),
            survival_damage_at=_as_dt(Storage._row_get(row, "survival_damage_at")),
        )
