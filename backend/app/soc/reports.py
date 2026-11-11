"""Reports to the chain of command: incident report (compte rendu) and periodic SITREP.

A report is drafted from the platform's records (incident file, detections,
evidence, decision support, KPIs, sensor coverage), completed by the operator
(assessment, proposals, addressees), then **issued**: its text is frozen,
hashed (SHA-256) and signed with the instance PKI (ECDSA, the certificate that
seals the evidence), stored in the reports register, written to the audit
trail and — for an incident report — to the incident's timeline.

What is signed is the exact text a recipient receives (message layout:
marking, date-time group, originator, addressees, subject, numbered
paragraphs). The structured ``sections`` are kept beside it for display only.
Verification recomputes the hash, checks the signature with the stored
certificate and that this certificate chains to a root of this instance.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import logging
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.config import Settings, get_settings
from app.core import audit
from app.db import db, read_db, row, rows

logger = logging.getLogger(__name__)

KINDS = {"incident": "Compte rendu d'incident", "sitrep": "Rapport de situation (SITREP)"}
PREFIXES = {"incident": "CR", "sitrep": "SITREP"}
CLASSIFICATIONS = ("NON PROTÉGÉ", "DIFFUSION RESTREINTE")
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
MAX_PERIOD_DAYS = 31
WIDTH = 96

Section = Dict[str, Any]


class ReportError(ValueError):
    pass


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse(value: Any) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.timezone.utc)


def dtg(value: Any) -> str:
    """Date-time group, NATO form: ``221430Z OCT 26``."""
    moment = value if isinstance(value, datetime.datetime) else _parse(value)
    if moment is None:
        return "—"
    moment = moment.astimezone(datetime.timezone.utc)
    return f"{moment:%d%H%M}Z {MONTHS[moment.month - 1]} {moment:%y}"


def _utc(value: Any) -> str:
    moment = _parse(value)
    return f"{moment:%d/%m/%Y %H:%M}Z" if moment else "—"


def _duration(minutes: Optional[float]) -> str:
    if minutes is None:
        return "—"
    minutes = int(round(minutes))
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 48 * 60:
        return f"{minutes // 60} h {minutes % 60:02d}"
    return f"{minutes // 1440} j {(minutes % 1440) // 60} h"


def _position(lat: Optional[float], lon: Optional[float]) -> str:
    from app.soc.notify import position_text

    return position_text(lat, lon)


# --------------------------------------------------------------------------- text layout


def render_text(header: Dict[str, Any], sections: Sequence[Section]) -> str:
    """Canonical message text (what is hashed and signed)."""
    rule = "-" * WIDTH
    lines = [header["classification"], rule, f"{header['title'].upper()}"]
    for label, value in (
        ("RÉF.", header["ref"]),
        ("GDH", header["dtg"]),
        ("ORIGINE", header["originator"]),
        ("RÉDACTEUR", header["author"]),
        ("DESTINATAIRES", header["addressees"] or "—"),
        ("OBJET", header["subject"]),
    ):
        lines.extend(textwrap.wrap(f"{label:<14}: {value}", WIDTH, subsequent_indent=" " * 16) or [label])
    if header.get("period"):
        lines.append(f"{'PÉRIODE':<14}: {header['period']}")
    lines.append(rule)
    for number, section in enumerate(sections, start=1):
        lines.append("")
        lines.append(f"{number}. {section['title'].upper()}")
        for label, value in section.get("rows") or []:
            lines.extend(textwrap.wrap(f"   {label} : {value}", WIDTH, subsequent_indent="      ") or [""])
        for paragraph in section.get("paragraphs") or []:
            lines.extend(textwrap.wrap(paragraph, WIDTH, initial_indent="   ", subsequent_indent="   ") or [""])
        table = section.get("table")
        if table and table.get("rows"):
            columns = table["columns"]
            for record in table["rows"]:
                text = " | ".join(f"{c}: {v}" for c, v in zip(columns, record) if v not in (None, ""))
                lines.extend(textwrap.wrap(f"   - {text}", WIDTH, subsequent_indent="     "))
        elif table:
            lines.append(f"   {table.get('empty') or 'Néant.'}")
    lines.extend(["", rule, header["classification"]])
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- signature


def _sign(content: bytes, cfg: Settings) -> Tuple[str, str, str]:
    """(signature b64, certificate PEM, certificate SHA-256) — empty strings when no PKI is available."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        from app.core import pki

        pki.ensure_pki(cfg)
        key = serialization.load_pem_private_key(pki.signing_key_pem(cfg), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            return "", "", ""
        signature = key.sign(content, ec.ECDSA(hashes.SHA256()))
        certificate = (Path(cfg.keys_dir) / "cert.pem").read_text(encoding="ascii")
        return base64.b64encode(signature).decode(), certificate, pki.leaf_fingerprint_sha256(cfg)
    except Exception:  # an unsigned report is still issued (hash + audit), and says so
        logger.exception("Report signature unavailable")
        return "", "", ""


def verify(report_id: int, user: Dict[str, Any], settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Recompute the hash, check the signature and that its certificate chains to this instance's PKI."""
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    from app.core import pki

    cfg = settings or get_settings()
    report = get_report(report_id, cfg)
    if report is None:
        raise LookupError("Rapport introuvable")
    content = report["content"].encode("utf-8")
    hash_ok = hashlib.sha256(content).hexdigest() == report["sha256"]
    signature_ok: Optional[bool] = None
    chain_ok: Optional[bool] = None
    if report["signature"] and report["certificate"]:
        try:
            cert = x509.load_pem_x509_certificate(report["certificate"].encode("ascii"))
            cert.public_key().verify(base64.b64decode(report["signature"]), content, ec.ECDSA(hashes.SHA256()))
            signature_ok = True
        except (InvalidSignature, ValueError):
            signature_ok = False
            cert = None
        chain_ok = False
        if cert is not None:
            for root in pki.trusted_roots(cfg):
                try:
                    root.public_key().verify(cert.signature, cert.tbs_certificate_bytes,  # type: ignore[call-arg]
                                             ec.ECDSA(cert.signature_hash_algorithm))  # type: ignore[arg-type]
                    chain_ok = True
                    break
                except Exception:
                    continue
    valid = hash_ok and signature_ok is not False and chain_ok is not False
    audit.record("report.verify", actor=user["username"], target=f"report:{report['ref']}",
                 outcome="success" if valid else "failure",
                 details={"hash": hash_ok, "signature": signature_ok, "chain": chain_ok}, settings=cfg)
    return {"ref": report["ref"], "valid": valid, "hash_ok": hash_ok, "signature_ok": signature_ok, "chain_ok": chain_ok,
            "signed": bool(report["signature"]), "sha256": report["sha256"], "cert_fingerprint": report["cert_fingerprint"]}


# --------------------------------------------------------------------------- incident report


def _alert_properties(dossier: str, vessel_id: str, cfg: Settings) -> Dict[str, Any]:
    """Sealed detection facts of one target (risk reasons, reliability flags, size, scene)."""
    if not dossier:
        return {}
    try:
        from app.pipeline.aggregation import _read_geojson
        from app.soc.evidence import dossier_path

        data = _read_geojson(dossier_path(dossier, cfg) / "kraken_alerts.geojson")
    except Exception:
        return {}
    for feature in data.get("features") or []:
        props = feature.get("properties") or {}
        if props.get("vessel_id") == vessel_id:
            return props
    return {}


def _dossier_integrity(dossier: str, cfg: Settings) -> str:
    try:
        from app.pipeline.aggregation import cached_proof_status
        from app.soc.evidence import dossier_path

        status = cached_proof_status(dossier_path(dossier, cfg), cfg)
    except Exception:
        return "dossier introuvable"
    return PROOF_LABELS.get(status, status)


PROOF_LABELS = {
    "verified": "intègre — manifeste C2PA signé vérifié",
    "fallback": "intègre — scellement de secours vérifié",
    "unverifiable": "non vérifiable",
    "tampered": "ALTÉRÉ",
}
EVENT_TEXT = {
    "created": "Ouverture de l'incident",
    "observation": "Nouvelle observation rattachée",
    "severity": "Sévérité relevée",
    "assign": "Affectation",
    "comment": "Note d'analyse",
    "escalation": "Saisine d'autorité",
    "report": "Rapport émis",
    "intel": "Renseignement rattaché",  # historical events (intelligence reports withdrawn)
    "sla_breach": "Échéance dépassée",
    "evidence_export": "Export du dossier de preuves",
}


def _event_line(event: Dict[str, Any], statuses: Dict[str, str]) -> Optional[str]:
    payload = event.get("payload") or {}
    kind = event["kind"]
    if kind == "action":
        return None  # playbook ticks: listed in their own section
    if kind == "status":
        text = f"{statuses.get(payload.get('from'), payload.get('from'))} → {statuses.get(payload.get('to'), payload.get('to'))}"
        if payload.get("cancelled"):
            text += " (annulation)"
        if payload.get("resolution"):
            from app.soc.incidents import RESOLUTIONS

            text += f" — {RESOLUTIONS.get(payload['resolution'], payload['resolution'])}"
        if payload.get("note"):
            text += f" — {payload['note']}"
        return text
    if kind == "escalation":
        return f"{EVENT_TEXT[kind]} : {payload.get('label') or payload.get('target')}" + (
            f" (réf. {payload['reference']})" if payload.get("reference") else "") + (
            f" — {payload['note']}" if payload.get("note") else "")
    if kind == "comment":
        return f"{EVENT_TEXT[kind]} : {payload.get('text', '')}"
    if kind == "assign":
        return f"{EVENT_TEXT[kind]} : {payload.get('to') or 'personne'}"
    if kind == "severity":
        return f"{EVENT_TEXT[kind]} : {payload.get('from')} → {payload.get('to')}"
    if kind == "observation":
        return f"{EVENT_TEXT[kind]} ({payload.get('vessel_id')}, à {payload.get('distance_km')} km, risque {payload.get('risk')})"
    if kind == "report":
        return f"{EVENT_TEXT[kind]} : {payload.get('ref')}"
    if kind == "intel":
        return f"{EVENT_TEXT[kind]} ({payload.get('discipline', '').upper()} {payload.get('grading')}) : {payload.get('title')}"
    if kind == "sla_breach":
        return f"{EVENT_TEXT[kind]} ({'triage' if payload.get('stage') == 'triage' else 'résolution'})"
    if kind == "evidence_export":
        return f"{EVENT_TEXT[kind]} : {payload.get('dossier')}"
    return EVENT_TEXT.get(kind, kind)


def _kind_label(kind: str) -> str:
    from app.soc.rule_catalog import BY_ID

    if kind.startswith("custom:"):
        return "Règle sur mesure"
    return (BY_ID.get(kind) or {}).get("label") or kind


def incident_sections(incident: Dict[str, Any], assessment: str, proposals: str, cfg: Settings,
                      now: datetime.datetime) -> Tuple[List[Section], Dict[str, Any]]:
    from app.soc import decision
    from app.soc.incidents import STATUSES

    observations = sorted(incident["observations"], key=lambda o: o["seen_at"])
    latest = observations[-1] if observations else {}
    props = _alert_properties(latest.get("dossier") or "", str(latest.get("vessel_id") or ""), cfg)
    zone = decision.ZONE_LABELS.get(incident.get("zone"), "—") if incident.get("zone") else "—"
    sections: List[Section] = [{
        "title": "Situation",
        "rows": [
            ["Incident", f"{incident['ref']} — {incident['title']}"],
            ["Nature", _kind_label(incident["kind"])],
            ["Sévérité", incident["severity"]],
            ["Statut", incident["status_label"] + (f" — {incident['resolution_label']}" if incident.get("resolution_label") else "")],
            ["Position (dernière détection)", _position(incident["lat"], incident["lon"])],
            ["Zone", zone],
            ["Première / dernière détection", f"{_utc(incident['first_seen'])} / {_utc(incident['last_seen'])}"],
            ["Ouvert le", _utc(incident["created_at"])],
            ["Affecté à", incident.get("assignee_name") or incident.get("assignee") or "non affecté"],
        ] + ([["Autorité saisie", f"{incident['escalation_label']}" + (f" (réf. {incident['escalation_ref']})" if incident.get('escalation_ref') else "")]]
             if incident.get("escalation_label") else []),
    }]

    detection_rows = [
        ["Risque", f"{incident.get('risk_score') if incident.get('risk_score') is not None else '—'}/100"],
        ["Fiabilité de détection", f"{round(incident['quality_score'] * 100)} %" if incident.get("quality_score") is not None else "—"],
        ["Observations", str(len(observations))],
    ]
    if props.get("length_m"):
        detection_rows.append(["Longueur estimée", f"~{round(props['length_m'])} m"])
    if props.get("stac_source"):
        detection_rows.append(["Scène radar", str(props["stac_source"])])
    paragraphs = [f"Facteur de risque : {r}" for r in props.get("risk_reasons") or []]
    paragraphs += [f"Signal de fiabilité : {f}" for f in props.get("quality_flags") or []]
    created = next((e for e in reversed(incident["events"]) if e["kind"] == "created"), None)
    why = ((created or {}).get("payload") or {}).get("explanation") or {}
    if why:
        paragraphs.append(f"Règle déclenchée : {why.get('label') or why.get('rule')}"
                          + (f" — {why['summary']}" if why.get("summary") else ""))
        paragraphs += [f"Condition vérifiée : {c.get('condition')} (observé : {c.get('observed')})"
                       for c in why.get("checks") or []]
    sections.append({"title": "Détection et évaluation", "rows": detection_rows, "paragraphs": paragraphs})

    analysis: Dict[str, Any] = {}
    try:
        analysis = decision.assess(incident, now, cfg)
    except Exception:
        logger.exception("Decision support unavailable for %s", incident["ref"])
    target = analysis.get("target") or {}
    identity_rows = []
    if target.get("mmsi"):
        identity_rows = [["MMSI", target["mmsi"]], ["Nom AIS", target.get("name") or "—"], ["Pavillon", target.get("flag") or "—"]]
    else:
        identity_rows = [["Identité", "non identifié (aucune émission AIS rattachée)"]]
    sections.append({"title": "Identification", "rows": identity_rows})

    if analysis:
        means = [m for m in analysis["means"] if "eta_minutes" in m][:5]
        sections.append({
            "title": "Situation actuelle et aide à la décision",
            "rows": [
                ["Position estimée maintenant", _position(target.get("lat_now"), target.get("lon_now"))
                 + f" — incertitude {target.get('uncertainty_nm')} NM"],
                ["Dernière position connue", f"{_utc(target.get('time'))} (il y a {_duration(target.get('age_minutes'))})"],
                ["Cinématique", target.get("kinematics_label", "—") + (
                    f" — {target['sog_kn']} nd au {target['cog_deg']}°" if target.get("sog_kn") is not None else "")],
                ["Zone actuelle", analysis["zone_now_label"]],
                ["Projection", analysis["projection"].get("note") or "—"],
                ["Option recommandée", next((o["title"] for o in analysis["options"] if o["recommended"]), "—")],
                ["Motif", analysis["recommendation"]],
            ],
            "table": {
                "columns": ["Moyen", "Sur zone dans", "Distance", "Zone d'interception", "Réalisable", "Réserves"],
                "rows": [[m["name"], m["eta_label"], f"{m['distance_nm']} NM", m["intercept_zone_label"],
                          "oui" if m["feasible"] else "non", "; ".join(m["issues"])] for m in means],
                "empty": "Aucun moyen d'intervention renseigné.",
            },
            "paragraphs": [f"Hypothèse : {c}" for c in analysis["caveats"]],
        })

    fused = (analysis or {}).get("corroboration") or {}
    contact = fused.get("contact")
    if fused:
        rows_ = [["Synthèse", fused.get("summary") or "—"]]
        table_rows = []
        if contact:
            rows_.append(["Contact corroboré", f"{contact['id']} — {contact['int_count']} discipline(s), confiance "
                                              f"{round(contact['confidence'] * 100)} %"])
            table_rows = [[o["at"][:16].replace("T", " ") + "Z", o["int"], o["source"], o.get("detail") or "",
                           f"{round(o['p'] * 100)} %"] for o in contact["observations"][-15:]]
        sections.append({"title": "Corroboration multi-capteurs", "rows": rows_, "table": {
            "columns": ["Date", "Discipline", "Source", "Détail", "Poids"], "rows": table_rows,
            "empty": "Aucune autre observation (optique, thermique, lumières nocturnes, radar GFW) sur la zone.",
        }})

    chronology = []
    for event in reversed(incident["events"]):  # events are newest first
        line = _event_line(event, STATUSES)
        if line:
            chronology.append([_utc(event["at"]), event["actor"], line])
    sections.append({"title": "Chronologie des actions", "table": {"columns": ["Date", "Acteur", "Fait"], "rows": chronology}})
    done = [a for a in incident["actions"] if a["done"]]
    todo = [a for a in incident["actions"] if not a["done"]]
    sections.append({"title": "Vérifications du playbook", "rows": [
        ["Réalisées", "; ".join(a["label"] for a in done) or "aucune"],
        ["Restant à faire", "; ".join(a["label"] for a in todo) or "aucune"],
    ]})

    evidence_rows = []
    for o in observations:
        if not o.get("dossier"):
            continue
        p = _alert_properties(o["dossier"], str(o["vessel_id"]), cfg)
        evidence_rows.append([o["dossier"], o["vessel_id"], _utc(o["seen_at"]), _dossier_integrity(o["dossier"], cfg),
                              p.get("sha256") or "—"])
    sections.append({"title": "Preuves scellées", "table": {
        "columns": ["Dossier", "Cible", "Observée le", "Intégrité", "SHA-256 vignette"], "rows": evidence_rows,
        "empty": "Pas de dossier radar (incident ouvert sur des données AIS, conservées dans l'historique de la plateforme).",
    }})
    if analysis:
        sections.append({"title": "Cadre juridique", "paragraphs": analysis["legal"]})
    sections.append({"title": "Appréciation", "paragraphs": [assessment.strip() or "Néant."]})
    sections.append({"title": "Propositions et demandes", "paragraphs": [proposals.strip() or "Néant."]})
    return sections, analysis


# --------------------------------------------------------------------------- SITREP


def sitrep_sections(start: datetime.datetime, end: datetime.datetime, assessment: str, proposals: str,
                    cfg: Settings) -> List[Section]:
    from app.soc import assets as assets_svc
    from app.soc.incidents import RESOLUTIONS, STATUSES, kpis

    s, e = start.isoformat(), end.isoformat()
    with read_db(cfg) as conn:
        created = rows(conn.execute(
            "SELECT * FROM incidents WHERE created_at >= ? AND created_at < ? ORDER BY created_at", (s, e)).fetchall())
        notable = rows(conn.execute(
            "SELECT * FROM incidents WHERE severity IN ('P1', 'P2') AND created_at < ? "
            "AND (status != 'closed' OR closed_at >= ?) ORDER BY severity, created_at", (e, s)).fetchall())
        events = rows(conn.execute(
            "SELECT e.*, i.ref FROM incident_events e JOIN incidents i ON i.id = e.incident_id "
            "WHERE e.at >= ? AND e.at < ? AND e.kind IN ('status', 'escalation') ORDER BY e.at", (s, e)).fetchall())
        closed = rows(conn.execute(
            "SELECT resolution, COUNT(*) AS n FROM incidents WHERE closed_at >= ? AND closed_at < ? GROUP BY resolution",
            (s, e)).fetchall())
        scenes = rows(conn.execute(
            "SELECT outcome, COUNT(*) AS n FROM processed_scenes WHERE processed_at >= ? AND processed_at < ? GROUP BY outcome",
            (s, e)).fetchall())
        detections = conn.execute(
            "SELECT COUNT(*) FROM incident_observations WHERE seen_at >= ? AND seen_at < ?", (s, e)).fetchone()[0]
        ais_vessels = conn.execute(
            "SELECT COUNT(DISTINCT mmsi) FROM ais_positions WHERE ts >= ? AND ts < ?", (s, e)).fetchone()[0]
    queue = kpis(cfg)
    by_sev = {p: sum(1 for i in created if i["severity"] == p) for p in ("P1", "P2", "P3", "P4")}
    hours = (end - start).total_seconds() / 3600
    open_total = queue["open"]
    summary = (
        f"Sur la période ({_duration(hours * 60)}), {len(created)} incident(s) ouvert(s) "
        f"(P1 {by_sev['P1']}, P2 {by_sev['P2']}, P3 {by_sev['P3']}, P4 {by_sev['P4']}) et "
        f"{sum(c['n'] for c in closed)} clos. {open_total} incident(s) restent ouverts, dont "
        f"{queue['open_by_severity'].get('P1', 0)} P1 et {queue['sla_breached']} hors délai."
    )
    sections: List[Section] = [{"title": "Situation générale", "paragraphs": [summary]}]
    sections.append({"title": "Incidents", "rows": [
        ["Ouverts sur la période", f"{len(created)} (P1 {by_sev['P1']} · P2 {by_sev['P2']} · P3 {by_sev['P3']} · P4 {by_sev['P4']})"],
        ["Clos sur la période", ", ".join(f"{RESOLUTIONS.get(c['resolution'], c['resolution'])} {c['n']}" for c in closed) or "0"],
        ["Ouverts en fin de période", f"{open_total} (P1 {queue['open_by_severity'].get('P1', 0)} · P2 {queue['open_by_severity'].get('P2', 0)}"
                                       f" · P3 {queue['open_by_severity'].get('P3', 0)} · P4 {queue['open_by_severity'].get('P4', 0)})"],
        ["Hors délai (SLA)", str(queue["sla_breached"])],
        ["Non affectés", str(queue["unassigned"])],
        ["Délai moyen de prise en compte (30 j)", _duration(queue["mtta_minutes"])],
        ["Délai moyen de clôture (30 j)", _duration(queue["mttr_minutes"])],
    ]})
    sections.append({"title": "Incidents marquants (P1 / P2)", "table": {
        "columns": ["Réf.", "Sév.", "Objet", "Position", "Statut", "Autorité saisie"],
        "rows": [[i["ref"], i["severity"], i["title"], _position(i["lat"], i["lon"]).split("  (")[0],
                  STATUSES.get(i["status"], i["status"]), i.get("escalation_ref") or (i.get("escalation_target") or "")]
                 for i in notable[:30]],
        "empty": "Néant.",
    }})
    actions = []
    for ev in events:
        payload = json.loads(ev["payload"] or "{}")
        if ev["kind"] == "escalation":
            text = f"Saisine : {payload.get('label') or payload.get('target')}" + (f" (réf. {payload['reference']})" if payload.get("reference") else "")
        elif payload.get("to") in ("response", "closed") or payload.get("cancelled") or payload.get("from") == "closed":
            text = f"{STATUSES.get(payload.get('from'), payload.get('from'))} → {STATUSES.get(payload.get('to'), payload.get('to'))}"
            if payload.get("resolution"):
                text += f" — {RESOLUTIONS.get(payload['resolution'], payload['resolution'])}"
        else:
            continue
        actions.append([_utc(ev["at"]), ev["ref"], text])
    sections.append({"title": "Action de l'État en mer", "table": {
        "columns": ["Date", "Incident", "Fait"], "rows": actions, "empty": "Aucune saisine, intervention ni clôture sur la période."}})

    from app.sources import feed_log, health

    coverage = None
    gaps: List[Dict[str, str]] = []
    try:
        coverage = feed_log.coverage(start, end, settings=cfg)
        gaps = feed_log.gaps(start, end, min_minutes=30, settings=cfg)
    except Exception:
        logger.exception("AIS coverage unavailable for SITREP")
    down = [f"{name} ({entry['status']})" for name, entry in sorted(health.snapshot().items()) if entry["status"] != "ok"]
    sensor_rows = [
        ["Scènes radar traitées", ", ".join(f"{c['outcome']} {c['n']}" for c in scenes) or "aucune"],
        ["Détections rattachées à un incident", str(detections)],
        ["Navires AIS entendus", str(ais_vessels)],
        ["Couverture de la collecte AIS", f"{round(coverage * 100)} %" if coverage is not None else "non mesurée"],
        ["Interruptions de collecte ≥ 30 min", "; ".join(f"{_utc(g['start'])} → {_utc(g['end'])}" for g in gaps[:10]) or "aucune"],
        ["Sources dégradées ou en panne", ", ".join(down) or "aucune"],
    ]
    sections.append({"title": "Capteurs et couverture", "rows": sensor_rows})
    fleet = assets_svc.list_assets(cfg)
    sections.append({"title": "Moyens d'intervention", "table": {
        "columns": ["Moyen", "Type", "Base", "Disponibilité"],
        "rows": [[a["name"], a["kind_label"], a["base_name"], a["status_label"]] for a in fleet],
        "empty": "Aucun moyen renseigné.",
    }})
    sections.append({"title": "Appréciation", "paragraphs": [assessment.strip() or "Néant."]})
    sections.append({"title": "Propositions et demandes", "paragraphs": [proposals.strip() or "Néant."]})
    return sections


# --------------------------------------------------------------------------- issue / read


def _classification(value: Optional[str], cfg: Settings) -> str:
    marking = (value or cfg.report_classification or CLASSIFICATIONS[0]).strip().upper()
    allowed = {*CLASSIFICATIONS, cfg.report_classification.strip().upper()}
    if marking not in allowed:
        raise ReportError("Mention de protection inconnue")
    return marking


def _issue(kind: str, *, title: str, subject: str, sections: List[Section], user: Dict[str, Any], addressees: str,
           classification: str, cfg: Settings, now: datetime.datetime, incident: Optional[Dict[str, Any]] = None,
           period: Optional[Tuple[datetime.datetime, datetime.datetime]] = None) -> Dict[str, Any]:
    author = f"{user.get('display_name') or user['username']} ({user['username']})"
    with db(cfg) as conn:
        report_id = conn.execute(
            "INSERT INTO reports (ref, kind, incident_id, period_start, period_end, title, classification, created_at, "
            "created_by, content, sections, sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', '[]', '') RETURNING id",
            (f"pending-{now.timestamp()}-{user['username']}"[:120], kind, incident["id"] if incident else None,
             period[0].isoformat() if period else None, period[1].isoformat() if period else None, title,
             classification, now.isoformat(), user["username"]),
        ).fetchone()[0]
        ref = f"{PREFIXES[kind]}-{now.year}-{report_id:04d}"
        header = {
            "classification": classification, "title": KINDS[kind], "ref": ref, "dtg": dtg(now),
            "originator": cfg.report_originator, "author": author, "addressees": addressees.strip(),
            "subject": subject,
            "period": f"du {_utc(period[0])} au {_utc(period[1])}" if period else None,
        }
        content = render_text(header, sections)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        signature, certificate, fingerprint = _sign(content.encode("utf-8"), cfg)
        conn.execute(
            "UPDATE reports SET ref = ?, content = ?, sections = ?, sha256 = ?, signature = ?, certificate = ?, "
            "cert_fingerprint = ? WHERE id = ?",
            (ref, content, json.dumps({"header": header, "sections": sections}, ensure_ascii=False, default=str),
             digest, signature, certificate, fingerprint, report_id),
        )
        if incident:
            conn.execute(
                "INSERT INTO incident_events (incident_id, at, actor, kind, payload) VALUES (?, ?, ?, 'report', ?)",
                (incident["id"], now.isoformat(), user["username"],
                 json.dumps({"ref": ref, "report_id": report_id, "sha256": digest, "addressees": addressees.strip()[:300]},
                            ensure_ascii=False)),
            )
            conn.execute("UPDATE incidents SET updated_at = ? WHERE id = ?", (now.isoformat(), incident["id"]))
    audit.record("report.issue", actor=user["username"], target=f"report:{ref}",
                 details={"kind": kind, "sha256": digest, "signed": bool(signature),
                          **({"incident": incident["ref"]} if incident else {}),
                          **({"period": [period[0].isoformat(), period[1].isoformat()]} if period else {})},
                 settings=cfg)
    return get_report(report_id, cfg)  # type: ignore[return-value]


def draft_incident(incident_id: int, user: Dict[str, Any], *, assessment: str = "", proposals: str = "",
                   addressees: str = "", classification: Optional[str] = None, issue: bool = False,
                   settings: Optional[Settings] = None, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Preview (``issue=False``: nothing stored) or issue the report of one incident."""
    from app.soc.incidents import get_incident

    cfg = settings or get_settings()
    moment = now or _now()
    incident = get_incident(incident_id, cfg)
    if incident is None:
        raise LookupError("Incident introuvable")
    marking = _classification(classification, cfg)
    sections, _analysis = incident_sections(incident, assessment, proposals, cfg, moment)
    subject = f"{incident['ref']} — {incident['title']} ({incident['severity']})"
    if issue:
        return _issue("incident", title=f"{KINDS['incident']} {incident['ref']}", subject=subject, sections=sections,
                      user=user, addressees=addressees, classification=marking, cfg=cfg, now=moment, incident=incident)
    header = {"classification": marking, "title": KINDS["incident"], "ref": "(projet — non émis)", "dtg": dtg(moment),
              "originator": cfg.report_originator, "author": f"{user.get('display_name') or user['username']} ({user['username']})",
              "addressees": addressees.strip(), "subject": subject, "period": None}
    return {"draft": True, "kind": "incident", "header": header, "sections": sections, "content": render_text(header, sections)}


def draft_sitrep(user: Dict[str, Any], *, start: Optional[datetime.datetime] = None, end: Optional[datetime.datetime] = None,
                 assessment: str = "", proposals: str = "", addressees: str = "", classification: Optional[str] = None,
                 issue: bool = False, settings: Optional[Settings] = None) -> Dict[str, Any]:
    cfg = settings or get_settings()
    moment = _now()
    end = min(end or moment, moment)
    start = start or end - datetime.timedelta(hours=24)
    if start.tzinfo is None or end.tzinfo is None:
        raise ReportError("Dates avec fuseau horaire attendues")
    if start >= end:
        raise ReportError("Le début de la période doit précéder sa fin")
    if end - start > datetime.timedelta(days=MAX_PERIOD_DAYS):
        raise ReportError(f"Période limitée à {MAX_PERIOD_DAYS} jours")
    marking = _classification(classification, cfg)
    sections = sitrep_sections(start, end, assessment, proposals, cfg)
    subject = f"Situation de la ZEE de Guyane du {_utc(start)} au {_utc(end)}"
    if issue:
        return _issue("sitrep", title=f"SITREP {dtg(end)}", subject=subject, sections=sections, user=user,
                      addressees=addressees, classification=marking, cfg=cfg, now=moment, period=(start, end))
    header = {"classification": marking, "title": KINDS["sitrep"], "ref": "(projet — non émis)", "dtg": dtg(moment),
              "originator": cfg.report_originator, "author": f"{user.get('display_name') or user['username']} ({user['username']})",
              "addressees": addressees.strip(), "subject": subject, "period": f"du {_utc(start)} au {_utc(end)}"}
    return {"draft": True, "kind": "sitrep", "header": header, "sections": sections, "content": render_text(header, sections)}


def _decorate(report: Dict[str, Any], full: bool) -> Dict[str, Any]:
    report["kind_label"] = KINDS.get(report["kind"], report["kind"])
    report["signed"] = bool(report.get("signature"))
    if full:
        structured = json.loads(report.pop("sections") or "{}") or {}
        report["header"] = structured.get("header")
        report["sections"] = structured.get("sections") or []
    else:
        for heavy in ("content", "sections", "signature", "certificate"):
            report.pop(heavy, None)
    return report


def list_reports(*, incident_id: Optional[int] = None, kind: Optional[str] = None, limit: int = 200,
                 settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    clauses, params = [], []
    if incident_id is not None:
        clauses.append("r.incident_id = ?")
        params.append(incident_id)
    if kind:
        clauses.append("r.kind = ?")
        params.append(kind)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with read_db(settings) as conn:
        result = rows(conn.execute(
            f"SELECT r.*, i.ref AS incident_ref FROM reports r LEFT JOIN incidents i ON i.id = r.incident_id {where} "
            f"ORDER BY r.created_at DESC, r.id DESC LIMIT ?", (*params, limit)).fetchall())
    return [_decorate(r, full=False) for r in result]


def get_report(report_id: int, settings: Optional[Settings] = None) -> Optional[Dict[str, Any]]:
    with read_db(settings) as conn:
        report = row(conn.execute(
            "SELECT r.*, i.ref AS incident_ref FROM reports r LEFT JOIN incidents i ON i.id = r.incident_id WHERE r.id = ?",
            (report_id,)).fetchone())
    return _decorate(report, full=True) if report else None


def export_text(report_id: int, user: Dict[str, Any], settings: Optional[Settings] = None) -> Tuple[str, str]:
    """(file name, text): the signed message followed by its seal block (hash, signature, certificate)."""
    cfg = settings or get_settings()
    report = get_report(report_id, cfg)
    if report is None:
        raise LookupError("Rapport introuvable")
    seal = [
        "=" * WIDTH,
        "SCELLEMENT KRAKEN-Trust — le texte signé est exactement tout ce qui précède la ligne de « = » ci-dessus",
        f"SHA-256          : {report['sha256']}",
        f"Signature ECDSA  : {report['signature'] or 'non signée (PKI indisponible à l’émission)'}",
        f"Certificat (SHA-256) : {report['cert_fingerprint'] or '—'}",
    ]
    if report["certificate"]:
        seal += ["", report["certificate"].strip()]
    audit.record("report.export", actor=user["username"], target=f"report:{report['ref']}", settings=cfg)
    return f"{report['ref']}.txt", report["content"] + "\n".join(seal) + "\n"
