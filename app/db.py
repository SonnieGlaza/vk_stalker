from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable, Sequence


logger = logging.getLogger(__name__)


PLACEHOLDER_RE = re.compile(r"\?")
INSERT_OR_IGNORE_RE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+", re.IGNORECASE | re.DOTALL)
INSERT_OR_REPLACE_RE = re.compile(
    r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\s+([a-zA-Z_][\w]*)\s*\(([^)]+)\)\s*VALUES\s*\((.+)\)\s*$",
    re.IGNORECASE | re.DOTALL,
)
AUTOINCREMENT_RE = re.compile(
    r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
    re.IGNORECASE,
)
DML_HEAD_RE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)

# Primary keys for INSERT OR REPLACE → ON CONFLICT DO UPDATE translation.
TABLE_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "factions": ("name",),
    "locations": ("name",),
    "characters": ("telegram_id",),
    "topup_payments": ("payment_charge_id",),
    "faction_warehouse": ("faction", "item_key"),
    "auctions": ("id",),
    "raids": ("id",),
    "raid_members": ("raid_id", "telegram_id"),
    "war_lobbies": ("id",),
    "war_lobby_members": ("war_id", "telegram_id"),
    "map_events": ("location",),
    "player_stats": ("telegram_id",),
    "player_achievements": ("telegram_id", "achievement_key"),
    "alliances": ("faction_a", "faction_b"),
    "alliance_requests": ("requester_faction", "target_faction"),
    "pending_registrations": ("telegram_id",),
    "meta_kv": ("key",),
}


@dataclass(frozen=True)
class DbConfig:
    backend: str  # "sqlite" | "postgres"
    sqlite_path: str | None = None
    database_url: str | None = None


def normalize_database_url(raw: str) -> str:
    url = raw.strip()
    # Railway / Heroku отдают postgres:// который psycopg отвергает.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    return url


# ---------------------------------------------------------------------------
# Пул постоянных подключений Postgres
# ---------------------------------------------------------------------------
# Каждый psycopg.connect — это TCP+TLS+авторизация (~0.2-1 сек на Railway),
# а storage делает десятки подключений на команду. Держим живые соединения
# и переиспользуем их; после долгого простоя проверяем лёгким SELECT 1.
_PG_POOL: dict[str, list[tuple[Any, float]]] = {}
_PG_POOL_LOCK = threading.Lock()
_PG_POOL_MAX = 8
_PG_POOL_IDLE_PING_SEC = 60.0


def _pg_connect_raw(database_url: str) -> Any:
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(
        database_url,
        row_factory=dict_row,
        connect_timeout=8,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
    )


def _pg_pool_get(database_url: str) -> Any:
    with _PG_POOL_LOCK:
        stack = _PG_POOL.setdefault(database_url, [])
        now = time.monotonic()
        while stack:
            raw, last_used = stack.pop()
            if raw.closed:
                continue
            if now - last_used > _PG_POOL_IDLE_PING_SEC:
                # Долго лежал в пуле: хостинг (или прокси Railway) мог закрыть.
                try:
                    raw.cursor().execute("SELECT 1")
                except Exception:
                    try:
                        raw.close()
                    except Exception:
                        pass
                    continue
            return raw
        return _pg_connect_raw(database_url)


def _pg_pool_put(database_url: str, raw: Any) -> None:
    with _PG_POOL_LOCK:
        stack = _PG_POOL.setdefault(database_url, [])
        if len(stack) >= _PG_POOL_MAX or raw.closed:
            try:
                raw.close()
            except Exception:
                pass
            return
        stack.append((raw, time.monotonic()))


def is_dml(sql: str) -> bool:
    return bool(DML_HEAD_RE.match(sql or ""))


def rewrite_sql_for_postgres(sql: str) -> str:
    text = sql.strip()
    text = text.replace("MAX(0,", "GREATEST(0,")
    text = AUTOINCREMENT_RE.sub("BIGSERIAL PRIMARY KEY", text)

    if INSERT_OR_IGNORE_RE.match(text):
        text = INSERT_OR_IGNORE_RE.sub("INSERT INTO ", text, count=1)
        if "ON CONFLICT" not in text.upper():
            text = text.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    match = INSERT_OR_REPLACE_RE.match(text)
    if match:
        table = match.group(1)
        columns = [c.strip() for c in match.group(2).split(",")]
        values = match.group(3)
        pk = TABLE_PRIMARY_KEYS.get(table.lower())
        if not pk:
            raise ValueError(
                f"INSERT OR REPLACE into '{table}' has no TABLE_PRIMARY_KEYS mapping for Postgres"
            )
        updates = [f"{col} = EXCLUDED.{col}" for col in columns if col not in pk]
        conflict = ", ".join(pk)
        if updates:
            text = (
                f"INSERT INTO {table}({', '.join(columns)}) VALUES ({values}) "
                f"ON CONFLICT ({conflict}) DO UPDATE SET {', '.join(updates)}"
            )
        else:
            text = (
                f"INSERT INTO {table}({', '.join(columns)}) VALUES ({values}) "
                f"ON CONFLICT ({conflict}) DO NOTHING"
            )

    # Convert qmark placeholders to pyformat used by psycopg.
    text = PLACEHOLDER_RE.sub("%s", text)
    return text


class DbCursor:
    def __init__(self, cursor: Any, backend: str) -> None:
        self._cursor = cursor
        self._backend = backend
        self.lastrowid = getattr(cursor, "lastrowid", None)
        self.rowcount = getattr(cursor, "rowcount", -1)

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> "DbCursor":
        statement = rewrite_sql_for_postgres(sql) if self._backend == "postgres" else sql
        _t0 = time.monotonic()
        if params is None:
            self._cursor.execute(statement)
        else:
            self._cursor.execute(statement, tuple(params))
        _dt = time.monotonic() - _t0
        if _dt >= 2.0:
            logger.warning("МЕДЛЕННЫЙ SQL (%.2f с): %s", _dt, statement[:120])
        self.lastrowid = getattr(self._cursor, "lastrowid", None)
        self.rowcount = getattr(self._cursor, "rowcount", -1)
        return self

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence[Any]]) -> "DbCursor":
        statement = rewrite_sql_for_postgres(sql) if self._backend == "postgres" else sql
        _t0 = time.monotonic()
        self._cursor.executemany(statement, list(seq_of_params))
        _dt = time.monotonic() - _t0
        if _dt >= 2.0:
            logger.warning("МЕДЛЕННЫЙ SQL (%.2f с, executemany): %s", _dt, statement[:120])
        self.rowcount = getattr(self._cursor, "rowcount", -1)
        return self

    def fetchone(self) -> Any:
        return self._cursor.fetchone()

    def fetchall(self) -> list[Any]:
        return list(self._cursor.fetchall())

    def __iter__(self):
        return iter(self._cursor)


class DbConnection:
    def __init__(self, raw_conn: Any, backend: str, pool_key: str | None = None) -> None:
        self._conn = raw_conn
        self.backend = backend
        self._pool_key = pool_key
        self._dead = False
        self.total_changes = 0

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> DbCursor:
        if self.backend == "postgres":
            cursor = self._conn.cursor()
            wrapped = DbCursor(cursor, self.backend)
            wrapped.execute(sql, params)
            # Match SQLite total_changes: only count DML, never SELECT.
            if is_dml(sql) and wrapped.rowcount and wrapped.rowcount > 0:
                self.total_changes += int(wrapped.rowcount)
            return wrapped

        before = int(getattr(self._conn, "total_changes", 0))
        cursor = self._conn.execute(sql, params or ())
        self.total_changes = int(getattr(self._conn, "total_changes", before)) - before + self.total_changes
        # Keep absolute-style counter similar to sqlite for callers using `> 0` after one statement.
        self.total_changes = int(getattr(self._conn, "total_changes", 0))
        return DbCursor(cursor, self.backend)

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence[Any]]) -> DbCursor:
        if self.backend == "postgres":
            cursor = self._conn.cursor()
            wrapped = DbCursor(cursor, self.backend)
            wrapped.executemany(sql, seq_of_params)
            if is_dml(sql) and wrapped.rowcount and wrapped.rowcount > 0:
                self.total_changes += int(wrapped.rowcount)
            return wrapped
        cursor = self._conn.executemany(sql, list(seq_of_params))
        self.total_changes = int(getattr(self._conn, "total_changes", 0))
        return DbCursor(cursor, self.backend)

    def savepoint(self, name: str) -> None:
        # Работает и в SQLite, и в Postgres — нужно, чтобы ошибка ALTER
        # не abort'ила всю транзакцию ensure-схемы.
        self._conn.execute(f"SAVEPOINT {name}")

    def release_savepoint(self, name: str) -> None:
        self._conn.execute(f"RELEASE SAVEPOINT {name}")

    def rollback_to_savepoint(self, name: str) -> None:
        self._conn.execute(f"ROLLBACK TO SAVEPOINT {name}")

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._dead = True
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> "DbConnection":
        self.total_changes = 0
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        dead = self._dead
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        except Exception:
            # Если даже commit/rollback упал (соединение умерло) — в пул не возвращаем.
            dead = True
        finally:
            if self._pool_key and not dead and not self._conn.closed:
                _pg_pool_put(self._pool_key, self._conn)
            else:
                try:
                    self._conn.close()
                except Exception:
                    pass
        return None


def connect(config: DbConfig) -> DbConnection:
    if config.backend == "postgres":
        if not config.database_url:
            raise ValueError("DATABASE_URL is empty")
        raw = _pg_pool_get(config.database_url)
        return DbConnection(raw, "postgres", pool_key=config.database_url)

    if not config.sqlite_path:
        raise ValueError("DB_PATH is empty")
    raw_sqlite = sqlite3.connect(config.sqlite_path)
    raw_sqlite.row_factory = sqlite3.Row
    raw_sqlite.execute("PRAGMA journal_mode=WAL")
    raw_sqlite.execute("PRAGMA synchronous=NORMAL")
    return DbConnection(raw_sqlite, "sqlite")


def integrity_error_types() -> tuple[type[BaseException], ...]:
    types: list[type[BaseException]] = [sqlite3.IntegrityError]
    try:
        import psycopg

        types.append(psycopg.IntegrityError)
    except Exception:
        pass
    return tuple(types)


def row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return dict(row)
