"""Vessels authorised to fish in the EEZ (licences, agreements) — the counterpart of the watchlist.

In French Guiana, foreign vessels fish legally under licence (e.g. the
Venezuelan snapper fleet). Without this list every one of them would be raised
as "foreign fishing" and their GFW events would inflate the risk of nearby
radar targets.

Matching uses **hard identifiers only** (MMSI, IMO, radio call sign) and
ignores expired licences: a name match could whitelist an IUU vessel sailing
under a borrowed name. The marine-protected-area rule still applies to
authorised vessels (a licence does not allow fishing in a reserve).
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from app.config import Settings
from app.core import audit
from app.db import db, read_db, rows
from app.soc.watchlist import MAX_IMPORT_BYTES, _digits, _sheets, call_sign_key, detect_format, name_key

HEADERS = {
    "name": ("name", "nom", "navire", "vessel", "vesselname", "nomnavire", "shipname"),
    "mmsi": ("mmsi",),
    "imo": ("imo", "imonumber", "numeroimo", "omi"),
    "call_sign": ("callsign", "ircs", "indicatif", "indicatifradio", "radiocallsign"),
    "flag": ("flag", "pavillon", "flagstate"),
    "licence": ("licence", "license", "licencenumber", "numerolicence", "autorisation", "reference"),
    "valid_until": ("validuntil", "finvalidite", "expiration", "expiry", "datefin", "until", "jusquau"),
    "note": ("note", "notes", "commentaire", "remarque", "observations"),
}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _date(value: Any) -> Optional[str]:
    """ISO date (YYYY-MM-DD) from common spellings (2026-12-31, 31/12/2026), or None."""
    text = str(value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _clean(entry: Dict[str, Any]) -> Dict[str, Any]:
    mmsi, imo = _digits(entry.get("mmsi"), 9), _digits(entry.get("imo"), 7)
    call_sign = call_sign_key(entry.get("call_sign"))
    if not (mmsi or imo or call_sign):
        raise ValueError("Un navire autorisé doit avoir un identifiant certain : MMSI (9 chiffres), IMO (7 chiffres) ou indicatif radio")
    if entry.get("valid_until") and not _date(entry.get("valid_until")):
        raise ValueError("Date de fin de validité illisible (AAAA-MM-JJ ou JJ/MM/AAAA)")
    return {
        "name": str(entry.get("name") or "").strip()[:200],
        "mmsi": mmsi,
        "imo": imo,
        "call_sign": call_sign or None,
        "flag": str(entry.get("flag") or "").strip()[:60] or None,
        "licence": str(entry.get("licence") or "").strip()[:120],
        "valid_until": _date(entry.get("valid_until")),
        "note": str(entry.get("note") or "").strip()[:300],
    }


def _insert(conn: Any, entry: Dict[str, Any], actor: str) -> int:
    created = conn.execute(
        "INSERT INTO authorized_vessels (name, mmsi, imo, call_sign, flag, licence, valid_until, note, added_at, added_by) "
        "VALUES (:name, :mmsi, :imo, :call_sign, :flag, :licence, :valid_until, :note, :added_at, :added_by) RETURNING id",
        {**entry, "added_at": _now(), "added_by": actor},
    ).fetchone()
    return int(created[0])


def add_entry(entry: Dict[str, Any], *, actor: str, settings: Optional[Settings] = None) -> Dict[str, Any]:
    clean = _clean(entry)
    with db(settings) as conn:
        entry_id = _insert(conn, clean, actor)
        created = dict(conn.execute("SELECT * FROM authorized_vessels WHERE id = ?", (entry_id,)).fetchone())
    audit.record("authorized.add", actor=actor, target=f"authorized:{entry_id}",
                 details={k: clean[k] for k in ("mmsi", "imo", "call_sign", "licence", "valid_until")}, settings=settings)
    return created


def remove_entry(entry_id: int, *, actor: str, settings: Optional[Settings] = None) -> None:
    with db(settings) as conn:
        if not conn.execute("DELETE FROM authorized_vessels WHERE id = ?", (entry_id,)).rowcount:
            raise LookupError("Entrée introuvable")
    audit.record("authorized.remove", actor=actor, target=f"authorized:{entry_id}", settings=settings)


def import_file(data: bytes, *, actor: str, replace: bool = True, settings: Optional[Settings] = None) -> Dict[str, Any]:
    """CSV / XLS / XLSX with a header row; ``replace`` swaps the whole list atomically."""
    if not data:
        raise ValueError("Fichier vide")
    if len(data) > MAX_IMPORT_BYTES:
        raise ValueError("Fichier limité à 10 Mo")
    table = next((t for t in _sheets(data, detect_format(data)) if t), [])
    if len(table) < 2:
        raise ValueError("Aucune ligne de données")
    keys = [name_key(h).lower() for h in table[0]]
    columns = {field: next((i for i, k in enumerate(keys) if k in aliases), None) for field, aliases in HEADERS.items()}
    if all(columns[f] is None for f in ("mmsi", "imo", "call_sign")):
        raise ValueError("Colonne d'identifiant introuvable : mmsi, imo ou indicatif (call sign) requis ; "
                         f"en-têtes lus : {', '.join(table[0][:10])}")
    entries, rejected = [], []
    for number, raw in enumerate(table[1:], start=2):
        values = {f: (raw[i] if i is not None and i < len(raw) else "") for f, i in columns.items()}
        if not any(str(v).strip() for v in values.values()):
            continue
        try:
            entries.append(_clean(values))
        except ValueError as exc:
            rejected.append(f"ligne {number} : {exc}")
    with db(settings) as conn:
        if replace:
            conn.execute("DELETE FROM authorized_vessels")
        for entry in entries:
            _insert(conn, entry, actor)
    audit.record("authorized.import", actor=actor, target="authorized",
                 details={"entries": len(entries), "rejected": len(rejected), "replace": replace}, settings=settings)
    return {"imported": len(entries), "rejected": rejected[:20], "rejected_count": len(rejected), "replace": replace}


def list_entries(search: str = "", settings: Optional[Settings] = None) -> Dict[str, Any]:
    today = datetime.date.today().isoformat()
    with read_db(settings) as conn:
        if search:
            like = f"%{search[:64]}%"
            result = rows(conn.execute(
                "SELECT * FROM authorized_vessels WHERE name LIKE ? OR mmsi LIKE ? OR imo LIKE ? OR call_sign LIKE ? "
                "OR licence LIKE ? ORDER BY name LIMIT 1000", (like,) * 5).fetchall())
        else:
            result = rows(conn.execute("SELECT * FROM authorized_vessels ORDER BY name LIMIT 1000").fetchall())
    for entry in result:
        entry["expired"] = bool(entry.get("valid_until") and entry["valid_until"] < today)
    return {"entries": result, "active": sum(1 for e in result if not e["expired"]), "total": len(result)}


class AuthorizedMatcher:
    """Valid licences indexed by MMSI, IMO and call sign (expired licences are ignored)."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        today = datetime.date.today().isoformat()
        with read_db(settings) as conn:
            entries = rows(conn.execute(
                "SELECT * FROM authorized_vessels WHERE valid_until IS NULL OR valid_until >= ?", (today,)).fetchall())
        self.by_mmsi = {e["mmsi"]: e for e in entries if e["mmsi"]}
        self.by_imo = {e["imo"]: e for e in entries if e["imo"]}
        self.by_call_sign = {e["call_sign"]: e for e in entries if e["call_sign"]}

    def match(self, *, mmsi: Any = None, imo: Any = None, call_sign: Any = None) -> Optional[Dict[str, Any]]:
        for index, key in ((self.by_mmsi, _digits(mmsi, 9)), (self.by_imo, _digits(imo, 7)),
                           (self.by_call_sign, call_sign_key(call_sign))):
            if key and key in index:
                return index[key]
        return None

    def mmsis(self) -> List[str]:
        return list(self.by_mmsi)


def licence_summary(entry: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not entry:
        return None
    return {"licence": entry.get("licence"), "valid_until": entry.get("valid_until"), "name": entry.get("name"),
            "flag": entry.get("flag")}
