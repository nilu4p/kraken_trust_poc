"""SOC-style maritime incident management: detect → triage → qualify → escalate → respond → close.

Every dark-vessel detection becomes (or joins) an incident. Nearby detections
within the correlation window are grouped into the same incident as new
observations instead of flooding the queue. Every change is written to the
incident timeline and to the tamper-evident audit trail.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config import Settings, get_settings
from app.core import audit
from app.core.geo import haversine_km
from app.core.identity import ROLE_RANK
from app.db import db, lock, read_db, row, rows

logger = logging.getLogger(__name__)

STATUSES = {
    "new": "Nouveau",
    "triage": "En triage",
    "qualified": "Qualifié",
    "escalated": "Escaladé",
    "response": "Intervention en cours",
    "closed": "Clos",
}
OPEN_STATUSES = ("new", "triage", "qualified", "escalated", "response")

RESOLUTIONS = {
    "confirmed_iuu": "Pêche INN confirmée",
    "legitimate": "Activité légitime",
    "false_positive": "Fausse alarme (pas de navire)",
    "duplicate": "Doublon",
    "no_action": "Sans suite / non interceptable",
}
# Closing straight from triage is allowed only for these (no escalation needed).
TRIAGE_CLOSE_RESOLUTIONS = {"legitimate", "false_positive", "duplicate"}

# (from, to) -> minimum role
TRANSITIONS: Dict[Tuple[str, str], str] = {
    ("new", "triage"): "analyst",
    ("triage", "qualified"): "analyst",
    ("qualified", "escalated"): "supervisor",
    ("qualified", "response"): "supervisor",
    ("escalated", "response"): "supervisor",
    ("closed", "triage"): "supervisor",
    # Cancellations (motive required): an escalation withdrawn, an intervention called off.
    ("escalated", "qualified"): "supervisor",
    ("response", "escalated"): "supervisor",
    ("response", "qualified"): "supervisor",
}
# (from, to) -> what is cancelled; the motive is mandatory and kept in the history.
CANCELLATIONS: Dict[Tuple[str, str], str] = {
    ("escalated", "qualified"): "escalation",
    ("response", "escalated"): "response",
    ("response", "qualified"): "response",
}
CANCELLATION_LABELS = {"escalation": "Escalade annulée", "response": "Intervention annulée"}
SORTS = {
    "recent": "i.created_at DESC, i.id DESC",
    "oldest": "i.created_at ASC, i.id ASC",
    "priority": "CASE WHEN i.status = 'closed' THEN 1 ELSE 0 END, i.severity, i.created_at DESC",
}

ESCALATION_TARGETS = {
    "cross_ag": "CROSS Antilles-Guyane (coordination opérationnelle)",
    "aem_prefecture": "Délégué du gouvernement pour l'Action de l'État en Mer (Préfet de Guyane)",
    "marine_nationale": "Marine nationale — Commandement de la zone maritime",
    "dm_guyane": "Direction de la Mer de Guyane (affaires maritimes, contrôle des pêches)",
    "gendarmerie_maritime": "Gendarmerie maritime",
    "douanes": "Douanes — garde-côtes",
    "parquet": "Parquet de Cayenne (procédure judiciaire)",
}

PLAYBOOK: List[Tuple[str, str]] = [
    ("check_detection", "Vérifier la vignette SAR et la fiabilité de détection"),
    ("check_ais", "Contrôler l'historique AIS et les coupures (GFW)"),
    ("check_optical", "Rechercher une imagerie optique (Pléiades / SPOT / Sentinel-2)"),
    ("check_night", "Vérifier les lumières nocturnes VIIRS"),
    ("identify", "Identifier le type de navire, le pavillon, l'activité"),
    ("escalate", "Transmettre à l'autorité compétente (AEM)"),
    ("follow_up", "Suivre l'intervention (moyen nautique / aérien)"),
    ("export_evidence", "Exporter le dossier de preuves scellé pour la procédure"),
]


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(value: datetime.datetime) -> str:
    return value.isoformat()


def _parse(value: Optional[str]) -> Optional[datetime.datetime]:
    return datetime.datetime.fromisoformat(value) if value else None


def severity_for(risk_score: Optional[int]) -> str:
    score = risk_score or 0
    if score >= 75:
        return "P1"
    if score >= 55:
        return "P2"
    if score >= 35:
        return "P3"
    return "P4"


def _event(conn: Any, incident_id: int, actor: str, kind: str, payload: Optional[Dict[str, Any]] = None) -> None:
    conn.execute(
        "INSERT INTO incident_events (incident_id, at, actor, kind, payload) VALUES (?, ?, ?, ?, ?)",
        (incident_id, _iso(_now()), actor, kind, json.dumps(payload or {}, ensure_ascii=False, default=str)),
    )


def _sla(incident: Dict[str, Any]) -> Dict[str, Any]:
    now = _now()
    triage_due = _parse(incident["triage_due"])
    resolve_due = _parse(incident["resolve_due"])
    acknowledged = incident.get("acknowledged_at")
    closed = incident["status"] == "closed"
    triage_breached = bool(triage_due and not acknowledged and now > triage_due) or bool(
        acknowledged and triage_due and _parse(acknowledged) > triage_due
    )
    resolve_breached = bool(resolve_due and not closed and now > resolve_due) or bool(
        closed and incident.get("closed_at") and resolve_due and _parse(incident["closed_at"]) > resolve_due
    )
    next_due = None
    if not closed:
        next_due = incident["triage_due"] if not acknowledged else incident["resolve_due"]
    return {
        "triage_breached": triage_breached,
        "resolve_breached": resolve_breached,
        "breached": (triage_breached and not acknowledged) or (resolve_breached and not closed),
        "next_due": next_due,
    }


def _decorate(incident: Dict[str, Any]) -> Dict[str, Any]:
    incident["status_label"] = STATUSES.get(incident["status"], incident["status"])
    incident["resolution_label"] = RESOLUTIONS.get(incident.get("resolution") or "", None)
    incident["escalation_label"] = ESCALATION_TARGETS.get(incident.get("escalation_target") or "", None)
    incident["sla"] = _sla(incident)
    return incident


# --------------------------------------------------------------------------- creation / correlation


def ingest_detection(
    vessel: Dict[str, Any],
    *,
    alert_id: str,
    dossier: str,
    scene_id: Optional[str],
    seen_at: str,
    kind: str = "dark_vessel",
    title: Optional[str] = None,
    same_vessel_only: bool = False,
    explanation: Optional[Dict[str, Any]] = None,
    settings: Optional[Settings] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Create an incident for a dark vessel, or attach it to a correlated open one.

    ``explanation`` (which rule fired, the conditions checked with the values
    observed, the parameters in force) is kept in the incident's history, so
    that "why this alert?" can be answered later exactly as it was decided.

    Returns ``(incident, created)``.
    """
    cfg = settings or get_settings()
    risk = (vessel.get("risk") or {}).get("score")
    quality = (vessel.get("quality") or {}).get("score")
    severity = severity_for(risk)
    now = _now()
    window_start = _iso(now - datetime.timedelta(hours=cfg.incident_correlation_hours))
    vessel_id = str(vessel.get("id", "SAR"))

    with db(cfg) as conn:
        lock(conn, "incidents")  # correlation is a read-then-write: one detection at a time
        existing = conn.execute(
            "SELECT incident_id FROM incident_observations WHERE alert_id = ? AND vessel_id = ?", (alert_id, vessel_id)
        ).fetchone()
        if existing:
            incident = row(conn.execute("SELECT * FROM incidents WHERE id = ?", (existing[0],)).fetchone())
            return _decorate(incident), False
        if scene_id:
            # Same scene analysed again (manual re-scan): the same echo is not a new observation.
            for previous in conn.execute(
                "SELECT incident_id, lat, lon FROM incident_observations WHERE scene_id = ?", (scene_id,)
            ).fetchall():
                if haversine_km(vessel["lat"], vessel["lon"], previous["lat"], previous["lon"]) <= cfg.persistence_radius_km:
                    incident = row(conn.execute("SELECT * FROM incidents WHERE id = ?", (previous["incident_id"],)).fetchone())
                    return _decorate(incident), False

        query = (
            f"SELECT * FROM incidents i WHERE status IN ({','.join('?' * len(OPEN_STATUSES))}) AND last_seen >= ? AND kind = ?"
        )
        params: Tuple[Any, ...] = (*OPEN_STATUSES, window_start, kind)
        if same_vessel_only:
            # AIS-identified vessels: correlate on the vessel itself, not on proximity.
            query += " AND EXISTS (SELECT 1 FROM incident_observations o WHERE o.incident_id = i.id AND o.vessel_id = ?)"
            params += (vessel_id,)
        candidates = rows(conn.execute(query, params).fetchall())
        match = None
        best = None
        for candidate in candidates:
            distance = haversine_km(vessel["lat"], vessel["lon"], candidate["lat"], candidate["lon"])
            if (same_vessel_only or distance <= cfg.incident_correlation_km) and (best is None or distance < best):
                match, best = candidate, distance

        if match:
            incident_id = match["id"]
            upgrade = severity < match["severity"]  # "P1" < "P2"
            conn.execute(
                "UPDATE incidents SET last_seen = ?, updated_at = ?, lat = ?, lon = ?, "
                "risk_score = CASE WHEN COALESCE(risk_score, 0) >= ? THEN COALESCE(risk_score, 0) ELSE ? END, "
                "severity = ? WHERE id = ?",
                (seen_at, _iso(now), vessel["lat"], vessel["lon"], risk or 0, risk or 0, severity if upgrade else match["severity"], incident_id),
            )
            _event(
                conn,
                incident_id,
                "system",
                "observation",
                {"alert_id": alert_id, "vessel_id": vessel_id, "distance_km": round(best or 0, 2), "risk": risk,
                 **({"explanation": explanation} if explanation else {})},
            )
            if upgrade:
                _event(conn, incident_id, "system", "severity", {"from": match["severity"], "to": severity})
            upgraded_from = match["severity"] if upgrade else None
            created = False
        else:
            triage_due = now + datetime.timedelta(minutes=cfg.sla_triage_minutes.get(severity, 1440))
            resolve_due = now + datetime.timedelta(minutes=cfg.sla_resolve_minutes.get(severity, 20160))
            zone_label = {"territorial": "mer territoriale", "contiguous": "zone contiguë", "eez": "ZEE"}.get(
                vessel.get("zone") or "", "ZEE"
            )
            length = vessel.get("length_m")
            if (vessel.get("pixels") or 99) < 6:
                length = None  # no measurable size on a tiny blob
            # Without any AIS picture at the pass, "without AIS" is not established.
            subject = "Écho radar non identifié" if vessel.get("ais_source") in ("unavailable", "mock") else "Navire sans AIS"
            if vessel.get("size_mismatch"):
                subject = "Écho incohérent avec le navire AIS déclaré"
            title = title or (f"{subject} en {zone_label}" + (f" (~{round(length)} m)" if length else ""))
            incident_id = conn.execute(
                "INSERT INTO incidents (ref, title, status, severity, risk_score, quality_score, lat, lon, zone, "
                "first_seen, last_seen, created_at, updated_at, triage_due, resolve_due, kind) "
                "VALUES (?, ?, 'new', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                (
                    f"pending-{alert_id}-{vessel_id}"[:120], title, severity, risk, quality, vessel["lat"], vessel["lon"],
                    vessel.get("zone"), seen_at, seen_at, _iso(now), _iso(now), _iso(triage_due), _iso(resolve_due), kind,
                ),
            ).fetchone()[0]
            conn.execute(
                "UPDATE incidents SET ref = ? WHERE id = ?", (f"KRK-{now.year}-{incident_id:04d}", incident_id)
            )
            conn.executemany(
                "INSERT INTO incident_actions (incident_id, key, label, position) VALUES (?, ?, ?, ?)",
                [(incident_id, key, label, index) for index, (key, label) in enumerate(PLAYBOOK)],
            )
            _event(conn, incident_id, "system", "created", {"alert_id": alert_id, "vessel_id": vessel_id, "risk": risk,
                                                             **({"explanation": explanation} if explanation else {})})
            created = True
            upgraded_from = None

        conn.execute(
            "INSERT INTO incident_observations (incident_id, alert_id, vessel_id, dossier, scene_id, seen_at, lat, lon, "
            "risk_score, quality_score) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (incident_id, alert_id, vessel_id, dossier, scene_id, seen_at, vessel["lat"], vessel["lon"], risk, quality),
        )
        incident = row(conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone())

    if created:
        audit.record("incident.create", target=f"incident:{incident['ref']}", details={"severity": severity, "risk": risk}, settings=cfg)
    from app.soc import timeline_cache

    timeline_cache.invalidate([seen_at], cfg)  # a scene analysed late adds a detection to a past period
    incident = _decorate(incident)
    incident["upgraded_from"] = upgraded_from
    return incident, created


# --------------------------------------------------------------------------- queries


def list_incidents(
    *,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    assignee_id: Optional[int] = None,
    open_only: bool = False,
    search: Optional[str] = None,
    sort: str = "recent",
    limit: int = 500,
    settings: Optional[Settings] = None,
) -> List[Dict[str, Any]]:
    """Incidents in chronological order of opening (newest first by default; ``oldest`` / ``priority``)."""
    order = SORTS.get(sort, SORTS["recent"])
    clauses, params = [], []
    if status:
        clauses.append("i.status = ?")
        params.append(status)
    if open_only:
        clauses.append(f"i.status IN ({','.join('?' * len(OPEN_STATUSES))})")
        params.extend(OPEN_STATUSES)
    if severity:
        clauses.append("i.severity = ?")
        params.append(severity)
    if assignee_id is not None:
        clauses.append("i.assignee_id = ?")
        params.append(assignee_id)
    if search:
        clauses.append("(i.ref LIKE ? OR i.title LIKE ?)")
        params.extend([f"%{search[:64]}%"] * 2)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with read_db(settings) as conn:
        result = rows(
            conn.execute(
                f"""SELECT i.*, u.username AS assignee, u.display_name AS assignee_name,
                    (SELECT COUNT(*) FROM incident_observations o WHERE o.incident_id = i.id) AS observations,
                    (SELECT COUNT(*) FROM incident_actions a WHERE a.incident_id = i.id AND a.done = 1) AS actions_done,
                    (SELECT COUNT(*) FROM incident_actions a WHERE a.incident_id = i.id) AS actions_total
                FROM incidents i LEFT JOIN users u ON u.id = i.assignee_id {where}
                ORDER BY {order}
                LIMIT ?""",
                (*params, limit),
            ).fetchall()
        )
    return [_decorate(i) for i in result]


def get_incident(incident_id: int, settings: Optional[Settings] = None) -> Optional[Dict[str, Any]]:
    with read_db(settings) as conn:
        incident = row(
            conn.execute(
                "SELECT i.*, u.username AS assignee, u.display_name AS assignee_name FROM incidents i "
                "LEFT JOIN users u ON u.id = i.assignee_id WHERE i.id = ?",
                (incident_id,),
            ).fetchone()
        )
        if incident is None:
            return None
        incident["observations"] = rows(
            conn.execute(
                "SELECT * FROM incident_observations WHERE incident_id = ? ORDER BY seen_at DESC", (incident_id,)
            ).fetchall()
        )
        incident["actions"] = rows(
            conn.execute("SELECT * FROM incident_actions WHERE incident_id = ? ORDER BY position", (incident_id,)).fetchall()
        )
        events = rows(
            conn.execute("SELECT * FROM incident_events WHERE incident_id = ? ORDER BY id DESC", (incident_id,)).fetchall()
        )
    for event in events:
        event["payload"] = json.loads(event["payload"] or "{}")
    incident["events"] = events
    for action in incident["actions"]:
        action["done"] = bool(action["done"])
    return _decorate(incident)


def _require(user: Dict[str, Any], minimum: str) -> None:
    if ROLE_RANK.get(user["role"], -1) < ROLE_RANK[minimum]:
        raise PermissionError("Droits insuffisants pour cette action")


def _load_open(conn: Any, incident_id: int) -> Dict[str, Any]:
    incident = row(conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone())
    if incident is None:
        raise LookupError("Incident introuvable")
    return incident


# --------------------------------------------------------------------------- workflow


def transition(
    incident_id: int,
    to_status: str,
    user: Dict[str, Any],
    *,
    note: str = "",
    resolution: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    """Move an incident through the workflow, enforcing allowed transitions and roles."""
    cfg = settings or get_settings()
    if to_status not in STATUSES:
        raise ValueError("Statut inconnu")
    now = _iso(_now())
    with db(cfg) as conn:
        incident = _load_open(conn, incident_id)
        current = incident["status"]
        if to_status == "closed":
            if current == "closed":
                raise ValueError("Incident déjà clos")
            if resolution not in RESOLUTIONS:
                raise ValueError("Une résolution est requise pour clore")
            if current in ("new", "triage") and resolution in TRIAGE_CLOSE_RESOLUTIONS:
                _require(user, "analyst")
            else:
                _require(user, "supervisor")
        else:
            minimum = TRANSITIONS.get((current, to_status))
            if minimum is None:
                raise ValueError(f"Transition interdite : {STATUSES[current]} → {STATUSES[to_status]}")
            _require(user, minimum)

        cancelled = CANCELLATIONS.get((current, to_status))
        if cancelled and len(note.strip()) < 5:
            raise ValueError(f"{CANCELLATION_LABELS[cancelled]} : indiquez le motif (5 caractères au moins)")
        if (current, to_status) == ("response", "escalated") and not incident["escalation_target"]:
            raise ValueError("Aucune escalade à laquelle revenir : annulez vers « Qualifié »")

        updates: Dict[str, Any] = {"status": to_status, "updated_at": now}
        withdrawn = None
        if to_status == "qualified" and cancelled:
            # Back to "qualified": the escalation (if any) is withdrawn too; it stays in the history.
            withdrawn = {"target": incident["escalation_target"], "reference": incident["escalation_ref"],
                         "escalated_at": incident["escalated_at"]} if incident["escalation_target"] else None
            updates.update(escalated_at=None, escalation_target=None, escalation_ref=None)
        if to_status == "triage" and not incident["acknowledged_at"]:
            updates["acknowledged_at"] = now
            if incident["assignee_id"] is None:
                updates["assignee_id"] = user["id"]
        if to_status == "closed":
            updates.update(closed_at=now, resolution=resolution, resolution_note=note.strip()[:2000])
            if not incident["acknowledged_at"]:
                updates["acknowledged_at"] = now
        if current == "closed" and to_status == "triage":
            updates.update(closed_at=None, resolution=None)
        assignments = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE incidents SET {assignments} WHERE id = ?", (*updates.values(), incident_id))
        if cancelled:
            # The playbook must not claim a transmission / follow-up that was called off.
            undone = ["follow_up"] + (["escalate"] if withdrawn else [])
            conn.execute(
                f"UPDATE incident_actions SET done = 0, done_by = NULL, done_at = NULL WHERE incident_id = ? "
                f"AND key IN ({','.join('?' * len(undone))})",
                (incident_id, *undone),
            )
        payload: Dict[str, Any] = {"from": current, "to": to_status, "note": note.strip()[:2000], "resolution": resolution}
        if cancelled:
            payload.update(cancelled=cancelled, withdrawn_escalation=withdrawn)
        _event(conn, incident_id, user["username"], "status", payload)
    audit.record(
        "incident.cancel" if cancelled else "incident.status",
        actor=user["username"],
        target=f"incident:{incident['ref']}",
        details={"from": current, "to": to_status, "resolution": resolution,
                 **({"cancelled": cancelled, "motive": note.strip()[:200]} if cancelled else {})},
        settings=cfg,
    )
    return get_incident(incident_id, cfg)  # type: ignore[return-value]


def assign(incident_id: int, assignee_id: Optional[int], user: Dict[str, Any], settings: Optional[Settings] = None) -> Dict[str, Any]:
    cfg = settings or get_settings()
    # Analysts may take an incident for themselves; assigning others needs a supervisor.
    if assignee_id != user["id"]:
        _require(user, "supervisor")
    else:
        _require(user, "analyst")
    with db(cfg) as conn:
        incident = _load_open(conn, incident_id)
        assignee = None
        if assignee_id is not None:
            assignee = row(conn.execute("SELECT * FROM users WHERE id = ? AND disabled = 0", (assignee_id,)).fetchone())
            if assignee is None:
                raise ValueError("Utilisateur inconnu ou désactivé")
            if ROLE_RANK[assignee["role"]] < ROLE_RANK["analyst"]:
                raise ValueError("Un observateur ne peut pas traiter d'incident")
        conn.execute(
            "UPDATE incidents SET assignee_id = ?, updated_at = ? WHERE id = ?", (assignee_id, _iso(_now()), incident_id)
        )
        _event(conn, incident_id, user["username"], "assign", {"to": assignee["username"] if assignee else None})
    audit.record(
        "incident.assign",
        actor=user["username"],
        target=f"incident:{incident['ref']}",
        details={"to": assignee["username"] if assignee else None},
        settings=cfg,
    )
    return get_incident(incident_id, cfg)  # type: ignore[return-value]


def comment(incident_id: int, text: str, user: Dict[str, Any], settings: Optional[Settings] = None) -> Dict[str, Any]:
    cfg = settings or get_settings()
    _require(user, "analyst")
    text = text.strip()
    if not text:
        raise ValueError("Commentaire vide")
    with db(cfg) as conn:
        incident = _load_open(conn, incident_id)
        conn.execute("UPDATE incidents SET updated_at = ? WHERE id = ?", (_iso(_now()), incident_id))
        _event(conn, incident_id, user["username"], "comment", {"text": text[:4000]})
    audit.record("incident.comment", actor=user["username"], target=f"incident:{incident['ref']}", settings=cfg)
    return get_incident(incident_id, cfg)  # type: ignore[return-value]


def escalate(
    incident_id: int,
    target: str,
    reference: str,
    note: str,
    user: Dict[str, Any],
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    """Record the hand-over to a State-at-sea authority and move to ``escalated``."""
    cfg = settings or get_settings()
    _require(user, "supervisor")
    if target not in ESCALATION_TARGETS:
        raise ValueError("Autorité inconnue")
    now = _iso(_now())
    with db(cfg) as conn:
        incident = _load_open(conn, incident_id)
        if incident["status"] not in ("qualified", "escalated", "response"):
            raise ValueError("Seul un incident qualifié peut être escaladé")
        conn.execute(
            "UPDATE incidents SET status = CASE WHEN status = 'qualified' THEN 'escalated' ELSE status END, "
            "escalated_at = COALESCE(escalated_at, ?), escalation_target = ?, escalation_ref = ?, updated_at = ? WHERE id = ?",
            (now, target, reference.strip()[:120], now, incident_id),
        )
        conn.execute(
            "UPDATE incident_actions SET done = 1, done_by = ?, done_at = ? WHERE incident_id = ? AND key = 'escalate'",
            (user["username"], now, incident_id),
        )
        _event(
            conn,
            incident_id,
            user["username"],
            "escalation",
            {"target": target, "label": ESCALATION_TARGETS[target], "reference": reference.strip()[:120], "note": note.strip()[:2000]},
        )
    audit.record(
        "incident.escalate",
        actor=user["username"],
        target=f"incident:{incident['ref']}",
        details={"authority": target, "reference": reference.strip()[:120]},
        settings=cfg,
    )
    return get_incident(incident_id, cfg)  # type: ignore[return-value]


def set_action(incident_id: int, key: str, done: bool, user: Dict[str, Any], settings: Optional[Settings] = None) -> Dict[str, Any]:
    cfg = settings or get_settings()
    _require(user, "analyst")
    with db(cfg) as conn:
        incident = _load_open(conn, incident_id)
        updated = conn.execute(
            "UPDATE incident_actions SET done = ?, done_by = ?, done_at = ? WHERE incident_id = ? AND key = ?",
            (int(done), user["username"] if done else None, _iso(_now()) if done else None, incident_id, key),
        ).rowcount
        if not updated:
            raise LookupError("Action inconnue")
        _event(conn, incident_id, user["username"], "action", {"key": key, "done": done})
    audit.record(
        "incident.action", actor=user["username"], target=f"incident:{incident['ref']}", details={"key": key, "done": done}, settings=cfg
    )
    return get_incident(incident_id, cfg)  # type: ignore[return-value]


def record_event(incident_id: int, actor: str, kind: str, payload: Dict[str, Any], settings: Optional[Settings] = None) -> None:
    with db(settings) as conn:
        _event(conn, incident_id, actor, kind, payload)


# --------------------------------------------------------------------------- analytics & cross-references


def kpis(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Queue health: open counts, SLA breaches, MTTA / MTTR (30 days), detections (7 days)."""
    incidents = list_incidents(limit=10_000, settings=settings)
    now = _now()
    open_items = [i for i in incidents if i["status"] != "closed"]
    month_ago = now - datetime.timedelta(days=30)
    recent = [i for i in incidents if _parse(i["created_at"]) >= month_ago]

    def _avg_minutes(pairs: Iterable[Tuple[Optional[str], Optional[str]]]) -> Optional[float]:
        values = [(_parse(b) - _parse(a)).total_seconds() / 60 for a, b in pairs if a and b]
        return round(sum(values) / len(values), 1) if values else None

    with read_db(settings) as conn:
        detections_7d = conn.execute(
            "SELECT COUNT(*) FROM incident_observations WHERE seen_at >= ?", (_iso(now - datetime.timedelta(days=7)),)
        ).fetchone()[0]
    return {
        "open": len(open_items),
        "by_status": {s: sum(1 for i in incidents if i["status"] == s) for s in STATUSES},
        "open_by_severity": {p: sum(1 for i in open_items if i["severity"] == p) for p in ("P1", "P2", "P3", "P4")},
        "sla_breached": sum(1 for i in open_items if i["sla"]["breached"]),
        "unassigned": sum(1 for i in open_items if not i["assignee_id"]),
        "mtta_minutes": _avg_minutes((i["created_at"], i["acknowledged_at"]) for i in recent),
        "mttr_minutes": _avg_minutes((i["created_at"], i["closed_at"]) for i in recent if i["status"] == "closed"),
        "closed_30d_by_resolution": {
            r: sum(1 for i in recent if i.get("resolution") == r) for r in RESOLUTIONS
        },
        "detections_7d": detections_7d,
    }


_STATE_SOURCE: Dict[Any, Any] = {}
_INDEX_CACHE: Dict[Any, Any] = {}


def _signature(conn: Any, cfg: Settings) -> Tuple[Any, ...]:
    """Cheap version stamp of the incident tables: any insert, transition or edit changes it."""
    events = conn.execute("SELECT COUNT(*), MAX(id), MAX(at) FROM incident_events").fetchone()
    items = conn.execute("SELECT COUNT(*), MAX(id), MAX(created_at), MAX(updated_at) FROM incidents").fetchone()
    observations = conn.execute("SELECT COUNT(*), MAX(id) FROM incident_observations").fetchone()
    return (str(cfg.data_dir), cfg.database_url, *events, *items, *observations)


def _state_source(settings: Optional[Settings] = None) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, str], Dict[int, List[Any]]]:
    """All incidents and their status history, parsed once per version of the tables (replays reuse it)."""
    cfg = settings or get_settings()
    with read_db(cfg) as conn:
        signature = _signature(conn, cfg)
        if signature in _STATE_SOURCE:
            return _STATE_SOURCE[signature]
        current = {r["id"]: dict(r) for r in conn.execute(
            "SELECT id, ref, status, severity, resolution, created_at FROM incidents").fetchall()}
        events = conn.execute(
            "SELECT incident_id, at, kind, payload FROM incident_events "
            "WHERE kind IN ('status', 'escalation', 'severity') ORDER BY at, id"
        ).fetchall()
    first_severity: Dict[int, str] = {}
    timeline: Dict[int, List[Tuple[str, str, Dict[str, Any]]]] = {}
    for e in events:
        if e["incident_id"] not in current:
            continue
        try:
            payload = json.loads(e["payload"] or "{}")
        except ValueError:
            payload = {}
        if e["kind"] == "severity" and e["incident_id"] not in first_severity and payload.get("from"):
            first_severity[e["incident_id"]] = payload["from"]
        timeline.setdefault(e["incident_id"], []).append((e["at"], e["kind"], payload))
    _STATE_SOURCE.clear()
    _STATE_SOURCE[signature] = (current, first_severity, timeline)
    return current, first_severity, timeline


def state_at(at: datetime.datetime, settings: Optional[Settings] = None) -> Dict[int, Dict[str, Any]]:
    """Status / severity / resolution of every incident as it stood at ``at`` (replayed from its event log).

    Incidents created after ``at`` are absent: a replay shows what the chain of
    command saw at that instant, not today's verdicts.
    """
    stamp = _iso(at)
    current_all, first_severity, timeline = _state_source(settings)
    current = {k: v for k, v in current_all.items() if v["created_at"] <= stamp}
    result: Dict[int, Dict[str, Any]] = {}
    for incident_id, row in current.items():
        state = {"ref": row["ref"], "status": "new", "resolution": None,
                 "severity": first_severity.get(incident_id, row["severity"])}
        for when, kind, payload in timeline.get(incident_id, []):
            if when > stamp:
                break
            if kind == "status" and payload.get("to") in STATUSES:
                state["status"] = payload["to"]
                state["resolution"] = payload.get("resolution") if payload["to"] == "closed" else None
            elif kind == "escalation" and state["status"] == "qualified":
                state["status"] = "escalated"
            elif kind == "severity" and payload.get("to"):
                state["severity"] = payload["to"]
        result[incident_id] = state
    return result


def detection_index(settings: Optional[Settings] = None, at: Optional[datetime.datetime] = None) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """(alert_id, vessel_id) -> incident summary, to annotate the map feed (as it stood at ``at`` when given)."""
    cfg = settings or get_settings()
    with read_db(cfg) as conn:
        signature = _signature(conn, cfg)
        result = _INDEX_CACHE.get(signature)
        if result is None:
            result = [dict(r) for r in conn.execute(
                "SELECT o.alert_id, o.vessel_id, i.id, i.ref, i.status, i.severity, i.resolution, i.assignee_id "
                "FROM incident_observations o JOIN incidents i ON i.id = o.incident_id"
            ).fetchall()]
            _INDEX_CACHE.clear()
            _INDEX_CACHE[signature] = result
    states = state_at(at, settings) if at is not None else None
    index = {}
    for r in result:
        state = {"status": r["status"], "severity": r["severity"], "resolution": r["resolution"]}
        if states is not None:
            if r["id"] not in states:
                continue  # incident not opened yet at that instant
            state = states[r["id"]]
        index[(r["alert_id"], r["vessel_id"])] = {
            "incident_id": r["id"],
            "incident_ref": r["ref"],
            "incident_status": state["status"],
            "incident_severity": state["severity"],
            "incident_resolution": state["resolution"],
        }
    return index


def detection_history(settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    """Past observations with the operator verdict, for the detection-quality engine."""
    with read_db(settings) as conn:
        result = conn.execute(
            "SELECT o.lat, o.lon, o.scene_id, i.status, i.resolution, i.closed_at FROM incident_observations o "
            "JOIN incidents i ON i.id = o.incident_id"
        ).fetchall()
    history = []
    for r in result:
        review = None
        if r["resolution"] == "false_positive":
            review = "false_positive"
        elif r["resolution"] in ("confirmed_iuu", "legitimate") or r["status"] in ("qualified", "escalated", "response"):
            review = "confirmed"
        history.append({"lat": r["lat"], "lon": r["lon"], "scene_id": r["scene_id"], "review": review,
                        "reviewed_at": r["closed_at"]})
    return history


def incidents_for_dossier(dossier: str, settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    with read_db(settings) as conn:
        return rows(
            conn.execute(
                "SELECT DISTINCT i.id, i.ref, i.status, i.severity FROM incident_observations o "
                "JOIN incidents i ON i.id = o.incident_id WHERE o.dossier = ?",
                (dossier,),
            ).fetchall()
        )
