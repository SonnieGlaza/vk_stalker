#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from app.db import normalize_database_url
from app.export_players import (
    build_players_export_files,
    load_legacy_payload,
    migrate_payload_to_storage,
)
from app.storage import Storage


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Export old players and migrate SQLite/snapshot → Postgres")
    parser.add_argument("--export-dir", default=os.getenv("EXPORT_DIR", "/tmp/stalker_export"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--migrate", action="store_true", help="Import into Postgres (replaces target tables)")
    parser.add_argument("--export-only", action="store_true", help="Only export files")
    args = parser.parse_args()

    try:
        payload, source = load_legacy_payload()
    except FileNotFoundError as exc:
        print(str(exc))
        return 1

    print(f"Source: {source}")
    export_dir = Path(args.export_dir)
    files, count = build_players_export_files(
        payload.get("characters") or [],
        payload.get("player_stats") or [],
        export_dir,
    )
    full_dump = export_dir / "full_source_dump.json"
    full_dump.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported players: {count}")
    print(f"  TXT : {files['txt']}")
    print(f"  CSV : {files['csv']}")
    print(f"  JSON: {files['json']}")
    print(f"  FULL: {full_dump}")

    if args.export_only or not args.migrate:
        print("Миграция пропущена. Для переноса в Postgres запусти с --migrate")
        return 0

    database_url = normalize_database_url(args.database_url) if args.database_url else ""
    if not database_url:
        print("DATABASE_URL не задан")
        return 1

    storage = Storage(
        db_path=database_url,
        snapshot_path=str(export_dir / "migration.backup.json"),
        database_url=database_url,
    )
    storage.init_db()
    pg_count = migrate_payload_to_storage(storage, payload)
    print(f"Migration done. Characters in Postgres: {pg_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
