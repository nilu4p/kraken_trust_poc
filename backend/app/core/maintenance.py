"""Factory reset, backup and restore of a KRAKEN-Trust instance.

Destructive by design, so only reachable from the operator CLI on the host
(never from the web API), after authentication + typed confirmation, and
always preceded by a backup unless explicitly declined.

Scopes:
- ``data``: incidents, users, sessions, audit, settings, watchlist, evidence,
  caches. PKI, master key and API credentials are kept.
- ``full``: everything above **plus** keys (PKI, encrypted API credentials,
  and ``keys/master.key`` in single-host mode): the instance comes back as
  freshly installed. A master key given by MASTER_KEY / MASTER_KEY_FILE is
  outside the instance folders and never touched.
"""

from __future__ import annotations

import datetime
import hashlib
import io
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from app.config import Settings, get_settings

SCOPES = {
    "data": ("data", "preuves", "cache"),
    "full": ("data", "preuves", "cache", "keys"),
}
# Folders included in a backup (the cache is rebuilt automatically).
BACKUP_FOLDERS = ("data", "preuves", "keys")
# PostgreSQL deployments: tables as JSON lines under database/ (portable to SQLite too).
DATABASE_FOLDER = "database"
CONFIRMATION_PHRASE = "REINITIALISER KRAKEN"
MAX_RESTORE_BYTES = 5 * 1024 * 1024 * 1024


def folders(cfg: Settings) -> Dict[str, Path]:
    return {
        "data": Path(cfg.data_dir),
        "preuves": Path(cfg.preuves_dir),
        "cache": Path(cfg.cache_dir),
        "keys": Path(cfg.keys_dir),
    }


def inventory(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """What a reset would destroy (counts only, no secrets)."""
    from app.db import db

    cfg = settings or get_settings()
    counts: Dict[str, Any] = {}
    try:
        with db(cfg) as conn:
            for table in ("users", "incidents", "audit_log", "watchlist"):
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except Exception:
        counts["database"] = "illisible ou absente"
    preuves = Path(cfg.preuves_dir)
    counts["evidence_dossiers"] = sum(1 for p in preuves.glob("alerte_*") if p.is_dir()) if preuves.is_dir() else 0
    return counts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _add_sqlite_snapshot(archive: tarfile.TarFile, database: Path, arcname: str) -> None:
    """Add a transactionally consistent copy of a live SQLite database (online backup API).

    Copying ``.db`` / ``-wal`` / ``-shm`` while the backend writes can produce a torn,
    unrestorable archive; the backup API copies a coherent snapshot instead.
    """
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / database.name
        source = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=30)
        target = sqlite3.connect(snapshot)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        os.chmod(snapshot, 0o600)
        archive.add(snapshot, arcname=arcname, recursive=False)


def create_backup(settings: Optional[Settings] = None, label: str = "backup") -> Tuple[Path, str]:
    """tar.gz of data / preuves / keys in BACKUP_DIR (0600). Returns (path, sha256).

    The archive contains the encrypted secrets. In the Docker deployment the
    master key is a separate secret (MASTER_KEY_FILE), deliberately absent from
    the archive: keep it apart. In single-host mode (``keys/master.key``) it is
    included and the archive is as sensitive as the instance itself.
    """
    cfg = settings or get_settings()
    backup_dir = Path(cfg.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = backup_dir / f"kraken-{label}-{stamp}.tar.gz"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as archive:
        from app.db import is_postgres

        postgres = is_postgres(cfg)
        manifest = {"created_at": stamp, "folders": list(BACKUP_FOLDERS), "format": 2,
                    "database": "postgresql" if postgres else "sqlite"}
        data = json.dumps(manifest).encode()
        info = tarfile.TarInfo("KRAKEN_BACKUP.json")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
        for name in BACKUP_FOLDERS:
            folder = folders(cfg)[name]
            if not folder.is_dir():
                continue
            for item in sorted(folder.rglob("*")):
                if not item.is_file() or item.is_symlink():
                    continue
                if postgres and ".db" in item.name:
                    continue  # the live database is PostgreSQL: dumped below
                if item.suffix == ".db":
                    _add_sqlite_snapshot(archive, item, f"{name}/{item.relative_to(folder).as_posix()}")
                elif item.name.endswith((".db-wal", ".db-shm", ".db-journal")):
                    continue  # folded into the consistent snapshot above
                else:
                    archive.add(item, arcname=f"{name}/{item.relative_to(folder).as_posix()}", recursive=False)
        if postgres:
            from app.core.dbtransfer import write_jsonl

            with tempfile.TemporaryDirectory() as tmp:
                for table_file in write_jsonl(Path(tmp), cfg):
                    archive.add(table_file, arcname=f"{DATABASE_FOLDER}/{table_file.name}", recursive=False)
    return path, _sha256(path)


def _empty(folder: Path) -> List[str]:
    """Delete the *content* of a folder (mount points stay). Returns undeletable paths."""
    failures: List[str] = []
    if not folder.is_dir():
        return failures
    for item in folder.iterdir():
        try:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
        except OSError:
            failures.append(str(item))
    return failures


def wipe(scope: str, settings: Optional[Settings] = None) -> List[str]:
    if scope not in SCOPES:
        raise ValueError(f"Portée inconnue : {scope}")
    cfg = settings or get_settings()
    failures: List[str] = []
    for name in SCOPES[scope]:
        failures += _empty(folders(cfg)[name])
    from app.db import is_postgres

    if is_postgres(cfg) and "data" in SCOPES[scope]:
        from app.core.dbtransfer import truncate_all

        truncate_all(cfg)
    _reset_process_state(cfg)
    return failures


def _reset_process_state(cfg: Settings) -> None:
    """Forget cached DB / vault handles so the next use starts from the new state."""
    from app.core import vault
    from app import db as db_module

    db_module._initialised.clear()
    db_module._pg_initialised.clear()
    vault._cache.clear()


def reinitialise(
    scope: str,
    *,
    actor: str,
    backup: Optional[Tuple[Path, str]],
    previous_audit_head: Optional[str],
    settings: Optional[Settings] = None,
) -> Optional[str]:
    """Recreate a working instance after a wipe; returns the new admin password if generated."""
    from app.core import audit
    from app.core.identity import bootstrap_admin
    from app.core.pki import ensure_pki
    from app.db import init_db

    cfg = settings or get_settings()
    for folder in folders(cfg).values():
        folder.mkdir(parents=True, exist_ok=True)
    init_db(cfg)
    ensure_pki(cfg)
    # First entry of the new audit chain anchors the reset to the previous one.
    audit.record(
        "system.factory_reset",
        actor=actor,
        target=f"scope:{scope}",
        details={
            "backup": backup[0].name if backup else None,
            "backup_sha256": backup[1] if backup else None,
            "previous_audit_head": previous_audit_head,
        },
        settings=cfg,
    )
    return bootstrap_admin(cfg)


def _safe_member(member: tarfile.TarInfo) -> bool:
    path = PurePosixPath(member.name)
    if member.name == "KRAKEN_BACKUP.json":
        return member.isfile()
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] not in (*BACKUP_FOLDERS, DATABASE_FOLDER):
        return False
    return member.isfile() or member.isdir()  # no links, devices or fifos


def restore(archive_path: Path, settings: Optional[Settings] = None) -> List[str]:
    """Replace data / preuves / keys by a backup. Validates every member first."""
    cfg = settings or get_settings()
    archive_path = Path(archive_path)
    if not archive_path.is_file():
        raise FileNotFoundError(f"Sauvegarde introuvable : {archive_path}")
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        names = {m.name for m in members}
        if "KRAKEN_BACKUP.json" not in names:
            raise ValueError("Ce fichier n'est pas une sauvegarde KRAKEN")
        unsafe = [m.name for m in members if not _safe_member(m)]
        if unsafe:
            raise ValueError(f"Archive refusée, entrées dangereuses : {unsafe[:3]}")
        if sum(m.size for m in members) > MAX_RESTORE_BYTES:
            raise ValueError("Archive trop volumineuse")
        failures = wipe("full", cfg)
        dump_dir = Path(tempfile.mkdtemp(prefix="kraken-restore-"))
        for member in members:
            if member.name == "KRAKEN_BACKUP.json" or not member.isfile():
                continue
            top, _, rest = member.name.partition("/")
            if top == DATABASE_FOLDER:
                source = archive.extractfile(member)
                if source is not None and "/" not in rest:
                    with (dump_dir / rest).open("wb") as handle:
                        shutil.copyfileobj(source, handle)
                continue
            target_root = folders(cfg)[top].resolve()
            target = (target_root / rest).resolve()
            if target_root not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                continue
            mode = 0o600 if top in ("keys", "data") else 0o644
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
            with os.fdopen(fd, "wb") as handle:
                shutil.copyfileobj(source, handle)
    _reset_process_state(cfg)
    try:
        _restore_database(dump_dir, cfg)
    finally:
        shutil.rmtree(dump_dir, ignore_errors=True)
    return failures


def _restore_database(dump_dir: Path, cfg: Settings) -> None:
    """Load the tables of the archive into the configured engine, whatever engine took the backup."""
    from app.core.dbtransfer import import_rows, load_jsonl, sqlite_rows, truncate_all
    from app.db import is_postgres

    sqlite_file = Path(cfg.data_dir) / "kraken.db"
    has_dump = any(dump_dir.glob("*.jsonl"))
    if is_postgres(cfg):
        truncate_all(cfg)
        if has_dump:
            load_jsonl(dump_dir, cfg)
        elif sqlite_file.is_file():  # backup taken on SQLite
            import_rows(sqlite_rows(sqlite_file), cfg)
            sqlite_file.unlink()
    elif has_dump:  # backup taken on PostgreSQL, restored on SQLite
        load_jsonl(dump_dir, cfg)
