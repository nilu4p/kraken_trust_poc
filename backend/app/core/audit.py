"""Tamper-evident audit trail (hash chain).

Each entry stores ``hash = SHA-256(prev_hash || canonical JSON of the entry)``.
Modifying, deleting or re-ordering any past entry breaks every following hash,
which :func:`verify_chain` detects. Secrets are never written to the log.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from app.config import Settings
from app.db import db, lock, read_db, rows

logger = logging.getLogger(__name__)

GENESIS = "0" * 64
SENSITIVE_KEYS = {"password", "new_password", "current_password", "token", "secret", "secret_key", "api_key", "value"}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _scrub(details: Dict[str, Any]) -> Dict[str, Any]:
    return {k: ("[redacted]" if k.lower() in SENSITIVE_KEYS else v) for k, v in details.items()}


def _entry_hash(prev_hash: str, entry: Dict[str, Any]) -> str:
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


def record(
    action: str,
    *,
    actor: str = "system",
    target: str = "",
    outcome: str = "success",
    ip: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    settings: Optional[Settings] = None,
) -> None:
    """Append an audit entry. Never raises: auditing must not break operations."""
    try:
        entry = {
            "at": "",
            "actor": actor,
            "action": action,
            "target": target,
            "outcome": outcome,
            "ip": ip,
            "details": json.dumps(_scrub(details or {}), sort_keys=True, default=str),
        }
        with db(settings) as conn:
            # Timestamp taken under the write lock: entries are chronological in id order.
            lock(conn, "audit")  # the hash chain must never fork between processes
            entry["at"] = _now()
            last = conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
            prev_hash = last["hash"] if last else GENESIS
            digest = _entry_hash(prev_hash, entry)
            conn.execute(
                "INSERT INTO audit_log (at, actor, action, target, outcome, ip, details, prev_hash, hash) "
                "VALUES (:at, :actor, :action, :target, :outcome, :ip, :details, :prev_hash, :hash)",
                {**entry, "prev_hash": prev_hash, "hash": digest},
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Audit write failed for %s: %s", action, exc)


VERIFY_BATCH = 5000


def verify_chain(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Recompute the whole chain; report the first broken entry if any.

    Read in batches of :data:`VERIFY_BATCH` entries from one consistent snapshot: memory stays
    bounded however long the trail grows (it is verified every hour).
    """
    prev_hash = GENESIS
    count, last_id = 0, None
    with read_db(settings) as conn:
        total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        while True:
            if last_id is None:
                batch = rows(conn.execute("SELECT * FROM audit_log ORDER BY id LIMIT ?", (VERIFY_BATCH,)).fetchall())
            else:
                batch = rows(conn.execute("SELECT * FROM audit_log WHERE id > ? ORDER BY id LIMIT ?",
                                          (last_id, VERIFY_BATCH)).fetchall())
            if not batch:
                break
            for entry in batch:
                payload = {k: entry[k] for k in ("at", "actor", "action", "target", "outcome", "ip", "details")}
                if entry["prev_hash"] != prev_hash or _entry_hash(prev_hash, payload) != entry["hash"]:
                    return {"valid": False, "entries": total, "broken_at": entry["id"]}
                prev_hash = entry["hash"]
            count += len(batch)
            last_id = batch[-1]["id"]
    return {"valid": True, "entries": count, "head": prev_hash}


def search(
    *,
    target: Optional[str] = None,
    actor: Optional[str] = None,
    action_prefix: Optional[str] = None,
    limit: int = 200,
    before_id: Optional[int] = None,
    settings: Optional[Settings] = None,
) -> List[Dict[str, Any]]:
    clauses, params = [], []
    if target:
        clauses.append("target = ?")
        params.append(target)
    if actor:  # part of the account name, case-insensitive
        clauses.append("LOWER(actor) LIKE ?")
        params.append("%" + actor.lower().replace("%", "").replace("_", "") + "%")
    if action_prefix:
        clauses.append("action LIKE ?")
        params.append(action_prefix.replace("%", "") + "%")
    if before_id:
        clauses.append("id < ?")
        params.append(before_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with read_db(settings) as conn:
        result = rows(
            conn.execute(f"SELECT * FROM audit_log {where} ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
        )
    for entry in result:
        try:
            entry["details"] = json.loads(entry["details"])
        except ValueError:
            pass
    return result
