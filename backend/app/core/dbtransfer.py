"""Portable table transfer: SQLite -> PostgreSQL migration, logical backup / restore (JSON lines).

The same format serves both engines, so a backup taken on SQLite restores on
PostgreSQL and vice versa. Rows keep their ids (incident references, audit
chain) and identity sequences are realigned afterwards.
"""

from __future__ import annotations

import datetime
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from app.config import Settings, get_settings
from app.db import IDENTITY_TABLES, TABLES, db, init_db, is_postgres, read_db

logger = logging.getLogger(__name__)

BATCH = 2000


def _columns(conn: Any, table: str, cfg: Settings) -> List[str]:
    if is_postgres(cfg):
        return [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ? AND table_schema = current_schema() "
            "ORDER BY ordinal_position", (table,))]
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def export_rows(settings: Optional[Settings] = None) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Every row of every table, in foreign-key order: ``(table, row)``."""
    cfg = settings or get_settings()
    with read_db(cfg) as conn:
        for table in TABLES:
            order = "ORDER BY id" if table in IDENTITY_TABLES else ""
            for record in conn.execute(f"SELECT * FROM {table} {order}"):
                yield table, dict(record)


def truncate_all(settings: Optional[Settings] = None) -> None:
    cfg = settings or get_settings()
    init_db(cfg)
    with db(cfg) as conn:
        if is_postgres(cfg):
            conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        else:
            for table in reversed(TABLES):
                conn.execute(f"DELETE FROM {table}")


def import_rows(records: Iterable[Tuple[str, Dict[str, Any]]], settings: Optional[Settings] = None) -> Dict[str, int]:
    """Insert ``(table, row)`` pairs (unknown tables / columns are ignored); returns counts."""
    cfg = settings or get_settings()
    init_db(cfg)
    counts: Dict[str, int] = {}
    batch: List[Tuple[Any, ...]] = []
    current: Optional[Tuple[str, Tuple[str, ...]]] = None
    with db(cfg) as conn:
        known = {table: set(_columns(conn, table, cfg)) for table in TABLES}

        def flush() -> None:
            if current and batch:
                table, columns = current
                conn.executemany(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))}) "
                    "ON CONFLICT DO NOTHING",
                    batch,
                )
                counts[table] = counts.get(table, 0) + len(batch)
            batch.clear()

        for table, record in records:
            if table not in known:
                continue
            columns = tuple(c for c in record if c in known[table])
            if current != (table, columns) or len(batch) >= BATCH:
                flush()
                current = (table, columns)
            batch.append(tuple(record[c] for c in columns))
        flush()
        if is_postgres(cfg):
            for table in IDENTITY_TABLES:
                conn.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
                )
    return counts


def sqlite_rows(path: Path) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Rows of a KRAKEN SQLite database file (read-only), in foreign-key order."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table in TABLES:
            if table in existing:
                for record in conn.execute(f"SELECT * FROM {table}"):
                    yield table, dict(record)
    finally:
        conn.close()


def migrate_sqlite_if_needed(settings: Optional[Settings] = None) -> Optional[Dict[str, int]]:
    """First start on PostgreSQL: copy the previous SQLite database, once, then set it aside.

    Runs only when PostgreSQL holds no user yet and ``DATA_DIR/kraken.db`` exists.
    """
    cfg = settings or get_settings()
    source = Path(cfg.data_dir) / "kraken.db"
    if not is_postgres(cfg) or not source.is_file():
        return None
    init_db(cfg)
    with read_db(cfg) as conn:
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return None
    logger.warning("Migrating SQLite database %s to PostgreSQL…", source)
    counts = import_rows(sqlite_rows(source), cfg)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    source.rename(source.with_name(f"kraken.db.migrated-{stamp}"))
    for suffix in ("-wal", "-shm"):
        leftover = source.with_name(source.name + suffix)
        if leftover.exists():
            leftover.unlink()
    from app.core import audit

    audit.record("system.migrate", actor="system", target="database:postgresql", details=counts, settings=cfg)
    logger.warning("SQLite -> PostgreSQL migration done: %s", counts)
    return counts


def write_jsonl(directory: Path, settings: Optional[Settings] = None) -> List[Path]:
    """One ``<table>.jsonl`` per table in ``directory`` (streamed: AIS history can be large)."""
    cfg = settings or get_settings()
    directory.mkdir(parents=True, exist_ok=True)
    paths = {table: directory / f"{table}.jsonl" for table in TABLES}
    handles = {table: path.open("w", encoding="utf-8") for table, path in paths.items()}
    try:
        for table, record in export_rows(cfg):
            handles[table].write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    finally:
        for handle in handles.values():
            handle.close()
    return list(paths.values())


def load_jsonl(directory: Path, settings: Optional[Settings] = None) -> Dict[str, int]:
    """Import ``<table>.jsonl`` files written by :func:`write_jsonl`."""
    def records() -> Iterator[Tuple[str, Dict[str, Any]]]:
        for table in TABLES:
            path = directory / f"{table}.jsonl"
            if path.is_file():
                with path.open(encoding="utf-8") as handle:
                    for line in handle:
                        if line.strip():
                            yield table, json.loads(line)

    return import_rows(records(), settings)
