from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from app.db import normalize_database_url


@dataclass(frozen=True)
class Settings:
    vk_token: str
    group_id: int
    db_path: str
    snapshot_path: str
    admin_ids: tuple[int, ...]
    database_url: str | None = None
    market_url: str = ""
    use_callback: bool = False
    webhook_port: int = 8080
    confirmation_code: str = ""
    callback_secret: str = ""


def _is_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _resolve_default_db_path() -> str:
    canonical_path = Path("/data/stalker_game.db")
    legacy_candidates = (
        Path("stalker_game.db"),
        Path("/workspace/stalker_game.db"),
        Path("/app/stalker_game.db"),
    )

    def has_character_data(path: Path) -> bool:
        if not path.exists():
            return False
        try:
            with sqlite3.connect(path) as conn:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'characters'
                    """
                ).fetchone()
                if row is None or int(row[0]) == 0:
                    return False
                count_row = conn.execute("SELECT COUNT(*) FROM characters").fetchone()
                return bool(count_row and int(count_row[0]) > 0)
        except sqlite3.Error:
            return False

    canonical_has_data = has_character_data(canonical_path)
    if canonical_has_data:
        return str(canonical_path)

    seen: set[Path] = set()
    for legacy_path in legacy_candidates:
        if legacy_path in seen:
            continue
        seen.add(legacy_path)
        legacy_has_data = has_character_data(legacy_path)
        if not legacy_has_data:
            continue
        if _is_writable_dir(canonical_path.parent):
            try:
                shutil.copy2(legacy_path, canonical_path)
                return str(canonical_path)
            except OSError:
                return str(legacy_path)
        return str(legacy_path)

    if _is_writable_dir(canonical_path.parent):
        return str(canonical_path)
    return str(legacy_candidates[0])


def _resolve_default_snapshot_path(db_path: str) -> str:
    if db_path.startswith("postgresql") or db_path.startswith("postgres://"):
        candidates = (
            Path("/data/stalker_game.backup.json"),
            Path("/tmp/stalker_game.backup.json"),
            Path("stalker_game.backup.json"),
        )
    else:
        candidates = (
            Path(db_path).with_suffix(".backup.json"),
            Path("/data/stalker_game.backup.json"),
            Path("/tmp/stalker_game.backup.json"),
            Path("stalker_game.backup.json"),
        )

    # Prefer existing snapshot with data.
    for candidate in candidates:
        if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0:
            return str(candidate)

    for candidate in candidates:
        if _is_writable_dir(candidate.parent):
            return str(candidate)
    return str(candidates[-1])


def _parse_admin_ids(raw_value: str) -> tuple[int, ...]:
    if not raw_value.strip():
        return ()
    admin_ids: list[int] = []
    for token in raw_value.split(","):
        value = token.strip()
        if not value:
            continue
        try:
            admin_ids.append(int(value))
        except ValueError as exc:
            raise ValueError(f"ADMIN_IDS содержит некорректный ID: {value}") from exc
    return tuple(admin_ids)


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("VK_TOKEN", "").strip()
    if not token:
        raise ValueError("VK_TOKEN is not set. Put community token in .env or environment variables.")
    group_raw = os.getenv("VK_GROUP_ID", "").strip()
    if not group_raw:
        raise ValueError("VK_GROUP_ID is not set. Put community ID in .env or environment variables.")
    try:
        group_id = int(group_raw)
    except ValueError:
        raise ValueError(f"VK_GROUP_ID должен быть числом: {group_raw}") from None

    database_url_raw = os.getenv("DATABASE_URL", "").strip()
    database_url = normalize_database_url(database_url_raw) if database_url_raw else None

    # Prefer Railway/Postgres DATABASE_URL when present.
    if database_url:
        db_path = database_url
    else:
        db_path = os.getenv("DB_PATH", "").strip() or _resolve_default_db_path()

    snapshot_path = os.getenv("SNAPSHOT_PATH", "").strip() or _resolve_default_snapshot_path(db_path)
    admin_ids = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))
    market_url = os.getenv("VK_MARKET_URL", "").strip()
    use_callback = os.getenv("VK_USE_CALLBACK", "").strip().lower() in ("1", "true", "yes")
    try:
        webhook_port = int(os.getenv("VK_WEBHOOK_PORT", "8080").strip() or "8080")
    except ValueError:
        webhook_port = 8080
    confirmation_code = os.getenv("VK_CONFIRMATION_CODE", "").strip()
    callback_secret = os.getenv("VK_CALLBACK_SECRET", "").strip()
    return Settings(
        vk_token=token,
        group_id=group_id,
        db_path=db_path,
        snapshot_path=snapshot_path,
        admin_ids=admin_ids,
        database_url=database_url,
        market_url=market_url,
        use_callback=use_callback,
        webhook_port=webhook_port,
        confirmation_code=confirmation_code,
        callback_secret=callback_secret,
    )
