"""Outbound SOC notifications (Slack / Mattermost compatible webhooks), routed by channel.

Channels
--------
``alertes``       new priority incidents, severity upgrades
``escalades``     hand-over to a State-at-sea authority, intervention, closure
``sla``           triage / resolution deadlines missed
``surveillance``  watchlisted (IUU) vessels detected in the EEZ
``operations``    scan reports and failures, data-source outages and recoveries
``securite``      admin creation, break-glass, factory reset / restore, lockouts,
                  audit-chain integrity

Routing: if ``ALERT_WEBHOOK_URL`` contains ``{channel}`` (or ``__CHANNEL__``, handy in
docker-compose defaults where braces are not allowed) it is substituted
(e.g. the PoC sink ``http://notif-sink:8090/hooks/{channel}``); otherwise the
single URL is used and the payload carries ``"channel": "kraken-<name>"``
(honoured by Mattermost when channel override is allowed).

Payload: ``text`` fallback + one Slack / Mattermost ``attachment`` (colour
bar, title link, fields, footer) + ``actions`` link buttons (rendered by the
PoC sink, ignored elsewhere). Evidence images are never attached: they stay
behind authentication.

SSRF guard: https only, public addresses only, no redirects. Hosts listed in
``WEBHOOK_ALLOWED_HOSTS`` (the local sink) are trusted explicitly.
Anti-flood: identical keys are throttled (``cooldown`` seconds).
"""

from __future__ import annotations

import datetime
import ipaddress
import logging
import socket
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from app.config import Settings, get_settings
from app.core import shared
from app.sources import health

logger = logging.getLogger(__name__)

CHANNELS: Dict[str, str] = {
    "alertes": "Nouveaux incidents prioritaires et sévérités relevées",
    "escalades": "Saisines des autorités AEM, interventions et clôtures",
    "sla": "Échéances de triage / résolution dépassées",
    "surveillance": "Navires de la liste de surveillance (INN) dans la ZEE",
    "operations": "Scans Sentinel-1, échecs, santé des sources de données",
    "securite": "Événements de sécurité de la plateforme",
}
SEVERITY_EMOJI = {"P1": "🔴", "P2": "🟠", "P3": "🟡", "P4": "⚪"}
SEVERITY_COLORS = {"P1": "#ff3b3b", "P2": "#ff8c1a", "P3": "#f5c542", "P4": "#94a3b8"}
LEVEL_COLORS = {"info": "#38bdf8", "success": "#3dff8a", "warning": "#f5c542", "danger": "#ff3b3b"}
ZONE_LABELS = {"territorial": "Mer territoriale (12 NM)", "contiguous": "Zone contiguë (24 NM)", "eez": "ZEE (200 NM)"}
STATUS_LABELS = {
    "new": "Nouveau", "triage": "En triage", "qualified": "Qualifié", "escalated": "Escaladé",
    "response": "Intervention en cours", "closed": "Clos",
}



class WebhookRejected(ValueError):
    """The webhook URL fails the SSRF policy."""


# --------------------------------------------------------------------------- transport


def validate_webhook_url(url: str, settings: Optional[Settings] = None) -> str:
    cfg = settings or get_settings()
    candidate = channel_url(url.strip(), "alertes")
    parsed = urlparse(candidate)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise WebhookRejected("URL de webhook invalide")
    trusted = cfg.allow_insecure_webhooks or parsed.hostname in cfg.webhook_allowed_hosts
    if parsed.scheme != "https" and not trusted:
        raise WebhookRejected("Le webhook doit utiliser HTTPS")
    if parsed.username or parsed.password:
        raise WebhookRejected("Identifiants interdits dans l'URL")
    if trusted:
        return url.strip()
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise WebhookRejected("Nom d'hôte du webhook introuvable") from exc
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise WebhookRejected("Le webhook pointe vers une adresse interne (SSRF bloqué)")
    return url.strip()


def channel_url(base: str, channel: str) -> str:
    return base.replace("{channel}", channel).replace("__CHANNEL__", channel)


def _post(url: str, payload: Dict[str, Any], cfg: Settings) -> bool:
    try:
        validate_webhook_url(url, cfg)
        response = httpx.post(url, json=payload, timeout=cfg.http_timeout, follow_redirects=False)
        ok = response.status_code < 400
        health.record("webhook", ok, "" if ok else f"HTTP {response.status_code}")
        return ok
    except Exception as exc:
        health.record("webhook", False, type(exc).__name__)
        logger.warning("Webhook notification failed: %s", type(exc).__name__)
        return False


def _throttled(key: Optional[str], cooldown: float) -> bool:
    """Anti-repeat shared by every process (API, workers): first sender in the window wins."""
    if not key or cooldown <= 0:
        return False
    try:
        return not shared.first_in(f"notify:{key}", cooldown)
    except Exception:  # Redis unavailable: better a duplicate than a lost alert
        return False


def publish(
    channel: str,
    text: str,
    *,
    attachment: Optional[Dict[str, Any]] = None,
    key: Optional[str] = None,
    cooldown: float = 0,
    settings: Optional[Settings] = None,
    blocking: bool = False,
) -> bool:
    """Send one message to a channel. Returns False if not configured or throttled."""
    cfg = settings or get_settings()
    if not cfg.alert_webhook_url or channel not in CHANNELS:
        return False
    if _throttled(key, cooldown):
        return False
    payload: Dict[str, Any] = {
        "channel": f"kraken-{channel}",
        "username": "KRAKEN-Trust",
        "icon_emoji": ":satellite:",
        "text": text,
    }
    if attachment:
        payload["attachments"] = [attachment]
    url = channel_url(cfg.alert_webhook_url, channel)
    if blocking:
        return _post(url, payload, cfg)
    if cfg.background_mode == "workers" and _enqueue(url, payload):
        return True
    _spawn(_post, url, payload, cfg)
    return True


def _enqueue(url: str, payload: Dict[str, Any]) -> bool:
    """Durable delivery through the ``notify`` queue (retries with backoff); False if the broker is down."""
    try:
        from app.tasks import notify as notify_task

        notify_task.apply_async(args=[url, payload], queue="notify")
        return True
    except Exception as exc:
        logger.warning("Notification queue unavailable (%s): direct delivery", type(exc).__name__)
        return False


def _spawn(target: Any, *args: Any) -> None:
    """Fire-and-forget delivery (isolated so tests can run it inline)."""
    threading.Thread(target=target, args=args, daemon=True).start()


def send(text: str, *, attachment: Optional[Dict[str, Any]] = None, settings: Optional[Settings] = None, blocking: bool = False) -> bool:
    """Backwards-compatible plain notification (operations channel)."""
    return publish("operations", text, attachment=attachment, settings=settings, blocking=blocking)


# --------------------------------------------------------------------------- formatting


def _dms(value: float, positive: str, negative: str) -> str:
    hemisphere = positive if value >= 0 else negative
    value = abs(value)
    degrees = int(value)
    minutes = (value - degrees) * 60
    return f"{degrees}°{minutes:06.3f}'{hemisphere}"


def position_text(lat: Optional[float], lon: Optional[float]) -> str:
    if lat is None or lon is None:
        return "—"
    return f"{_dms(lat, 'N', 'S')} {_dms(lon, 'E', 'W')}  ({lat:.4f}, {lon:.4f})"


def _relative(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        due = datetime.datetime.fromisoformat(iso)
    except ValueError:
        return iso
    minutes = (due - datetime.datetime.now(datetime.timezone.utc)).total_seconds() / 60
    span = f"{abs(minutes):.0f} min" if abs(minutes) < 120 else f"{abs(minutes) / 60:.1f} h"
    stamp = due.astimezone(datetime.timezone.utc).strftime("%d/%m %H:%MZ")
    return f"{stamp} ({'dans ' + span if minutes >= 0 else 'dépassée de ' + span})"


def _links(cfg: Settings, incident: Optional[Dict[str, Any]] = None, dossier: Optional[str] = None) -> List[Dict[str, str]]:
    base = cfg.public_url.rstrip("/")
    actions = []
    if incident and incident.get("id") is not None:
        actions.append({"type": "button", "text": "Ouvrir l'incident", "url": f"{base}/#/incidents/{incident['id']}"})
    actions.append({"type": "button", "text": "Situation tactique", "url": f"{base}/#/situation"})
    if dossier:
        actions.append({"type": "button", "text": "Dossier de preuves", "url": f"{base}/#/evidence/{dossier}"})
    return actions


def _field(title: str, value: Any, short: bool = True) -> Optional[Dict[str, Any]]:
    if value is None or value == "" or value == []:
        return None
    return {"title": title, "value": str(value), "short": short}


def incident_attachment(
    incident: Dict[str, Any],
    headline: str,
    settings: Optional[Settings] = None,
    *,
    vessel: Optional[Dict[str, Any]] = None,
    dossier: Optional[str] = None,
    extra: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Detailed incident card. ``vessel`` = detection context (risk reasons, quality…)."""
    cfg = settings or get_settings()
    vessel = vessel or {}
    risk = vessel.get("risk") or {}
    quality = vessel.get("quality") or {}
    severity = incident.get("severity", "")
    sla = incident.get("sla") or {}
    dual = vessel.get("dual_pol")
    fields = [
        _field("Sévérité", f"{SEVERITY_EMOJI.get(severity, '')} {severity}"),
        _field("Risque", f"{incident.get('risk_score')}/100" if incident.get("risk_score") is not None else None),
        _field("Fiabilité détection", f"{round(quality['score'] * 100)} % ({quality.get('level')})" if quality.get("score") is not None else None),
        _field("Statut", incident.get("status_label") or STATUS_LABELS.get(incident.get("status", ""), incident.get("status"))),
        _field("Zone", ZONE_LABELS.get(incident.get("zone") or "", incident.get("zone"))),
        _field("Assigné", incident.get("assignee_name") or incident.get("assignee") or "non assigné"),
        _field("Position", position_text(incident.get("lat"), incident.get("lon")), short=False),
        _field("Taille estimée", f"~{round(vessel['length_m'])} m" if vessel.get("length_m") else None),
        _field("Polarisation", None if dual is None else ("double ✓" if dual else "double ✗")),
        _field("Source AIS", vessel.get("ais_source")),
        _field("Observations", incident.get("observations") if isinstance(incident.get("observations"), int) else None),
        _field("Première détection", (incident.get("first_seen") or "")[:16].replace("T", " ") or None),
        _field("Échéance", _relative(sla.get("next_due") or incident.get("triage_due"))),
    ]
    if risk.get("reasons"):
        fields.append(_field("Pourquoi ce risque", "\n".join(f"• {r}" for r in risk["reasons"][:5]), short=False))
    if quality.get("flags"):
        fields.append(_field("Signaux de fiabilité", "\n".join(f"• {f}" for f in quality["flags"][:4]), short=False))
    fields.extend(extra or [])
    return {
        "fallback": f"{headline} — {incident.get('ref')} [{severity}] {incident.get('title')}",
        "color": SEVERITY_COLORS.get(severity, "#94a3b8"),
        "pretext": f"{SEVERITY_EMOJI.get(severity, '')} *{headline}*",
        "title": f"{incident.get('ref')} — {incident.get('title')}",
        "title_link": f"{cfg.public_url.rstrip('/')}/#/incidents/{incident.get('id')}",
        "fields": [f for f in fields if f],
        "actions": _links(cfg, incident, dossier),
        "footer": "KRAKEN-Trust · SOC maritime ZEE Guyane",
        "ts": int(time.time()),
    }


def event_attachment(
    title: str,
    level: str,
    fields: List[Optional[Dict[str, Any]]],
    settings: Optional[Settings] = None,
    *,
    text: str = "",
) -> Dict[str, Any]:
    cfg = settings or get_settings()
    return {
        "fallback": title,
        "color": LEVEL_COLORS.get(level, "#94a3b8"),
        "title": title,
        "text": text,
        "fields": [f for f in fields if f],
        "actions": _links(cfg),
        "footer": "KRAKEN-Trust · SOC maritime ZEE Guyane",
        "ts": int(time.time()),
    }


# --------------------------------------------------------------------------- SOC events


def incident_message(incident: Dict[str, Any], headline: str) -> str:
    emoji = SEVERITY_EMOJI.get(incident.get("severity", ""), "")
    return (
        f"{emoji} *{headline}* — {incident.get('ref')} [{incident.get('severity')}]\n"
        f"{incident.get('title')} · risque {incident.get('risk_score')}/100 · "
        f"{incident.get('lat', 0):.4f}, {incident.get('lon', 0):.4f}"
    )


def notify_incident(
    incident: Dict[str, Any],
    headline: str,
    settings: Optional[Settings] = None,
    *,
    channel: str = "alertes",
    vessel: Optional[Dict[str, Any]] = None,
    dossier: Optional[str] = None,
    extra: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    return publish(
        channel,
        incident_message(incident, headline),
        attachment=incident_attachment(incident, headline, settings, vessel=vessel, dossier=dossier, extra=extra),
        settings=settings,
    )


def notify_workflow(incident: Dict[str, Any], kind: str, actor: str, details: Dict[str, Any], settings: Optional[Settings] = None) -> bool:
    """Lifecycle steps worth a message in #escalades (escalation, intervention, closure, reopening)."""
    extra = [_field("Par", actor)]
    if kind == "escalation":
        headline = f"Escalade → {details.get('label')}"
        extra += [_field("Référence de transmission", details.get("reference")), _field("Éléments transmis", details.get("note"), short=False)]
    elif kind == "response":
        headline = "Intervention engagée"
        extra.append(_field("Note", details.get("note"), short=False))
    elif kind == "closed":
        headline = f"Incident clos — {details.get('resolution_label') or details.get('resolution')}"
        extra.append(_field("Conclusion", details.get("note"), short=False))
    elif kind == "reopened":
        headline = "Incident rouvert"
        extra.append(_field("Motif", details.get("note"), short=False))
    else:
        return False
    return notify_incident(incident, headline, settings, channel="escalades", extra=[e for e in extra if e])


def notify_scan(report: Dict[str, Any], settings: Optional[Settings] = None) -> bool:
    """End-of-scan report in #operations (success or failure)."""
    failed = report.get("status") == "error"
    created = report.get("new_incidents") or []
    title = (
        f"Scan Sentinel-1 en échec : {report.get('message')}"
        if failed
        else f"Scan terminé — {report.get('dark_vessels', 0)} navire(s) sans AIS, {len(created)} nouvel(s) incident(s)"
    )
    fields = [
        _field("Scène", report.get("scene_id"), short=False),
        _field("Passage satellite", (report.get("scene_datetime") or "")[:16].replace("T", " ") or None),
        _field("Lancé par", report.get("actor")),
        _field("Cibles radar", report.get("radar_targets")),
        _field("Écartées (fiabilité)", report.get("discarded")),
        _field("Navires sans AIS", report.get("dark_vessels")),
        _field("Corrélées à un incident ouvert", report.get("correlated")),
        _field("Nouveaux incidents", ", ".join(f"{i['ref']} [{i['severity']}]" for i in created), short=False),
        _field("Navires listés INN à l'AIS", report.get("watchlisted")),
        _field("Source AIS", report.get("ais_source")),
    ]
    level = "danger" if failed else ("warning" if created else "success")
    return publish("operations", title, attachment=event_attachment(title, level, fields, settings), settings=settings)


def notify_source_health(source: str, status: str, error: str = "", settings: Optional[Settings] = None) -> bool:
    down = status == "down"
    title = f"Source {'indisponible' if down else 'rétablie'} : {source}"
    return publish(
        "operations",
        title,
        attachment=event_attachment(title, "danger" if down else "success", [_field("Dernière erreur", error)], settings),
        key=f"source:{source}:{status}",
        cooldown=3600,
        settings=settings,
    )


FEED_NAMES = {"aisstream": "aisstream.io", "aishub": "AISHub", "vesselapi": "VesselAPI"}


def notify_collection(degraded: bool, coverage: float, per_feed: Dict[str, Optional[float]], gaps: List[Dict[str, str]],
                      minutes: Optional[int], settings: Optional[Settings] = None) -> bool:
    """AIS collection below 50 % of the last 30 min (or back to normal) — #operations."""
    pct = lambda share: f"{round(share * 100)} %"  # noqa: E731
    if degraded:
        title = f"Collecte AIS dégradée : {pct(coverage)} des 30 dernières minutes écoutées"
        fields = [
            _field("Par flux", " · ".join(f"{FEED_NAMES.get(f, f)} {pct(c)}" for f, c in per_feed.items() if c is not None) or "—"),
            _field("Interruptions (6 h)", ", ".join(f"{g['start'][11:16]}–{g['end'][11:16]} Z" for g in gaps[-5:]) or "—"),
            _field("Conséquences", "une mer vide sur la carte ne prouve pas l'absence de navires ; les échos radar de cette "
                                   "période sont « non identifiés » (risque minoré) ; la règle « coupure AIS » ne peut pas conclure"),
            _field("À vérifier", "conteneur collector (make workers), clé aisstream.io (Administration › Clés), "
                                 "connexion Internet, couverture des récepteurs"),
        ]
        level = "danger"
    else:
        title = "Collecte AIS rétablie" + (f" après {minutes} min" if minutes else "") + f" ({pct(coverage)} des 30 dernières minutes)"
        fields = []
        level = "success"
    return publish(
        "operations",
        title,
        attachment=event_attachment(title, level, fields, settings),
        key=f"ais-collection:{'down' if degraded else 'up'}",
        cooldown=600,
        settings=settings,
    )


def notify_security(title: str, *, level: str = "warning", details: Optional[Dict[str, Any]] = None,
                    key: Optional[str] = None, cooldown: float = 0, settings: Optional[Settings] = None,
                    blocking: bool = False) -> bool:
    fields = [_field(k, v, short=len(str(v)) < 40) for k, v in (details or {}).items()]
    return publish(
        "securite",
        f"🛡 {title}",
        attachment=event_attachment(f"🛡 {title}", level, fields, settings),
        key=key,
        cooldown=cooldown,
        settings=settings,
        blocking=blocking,
    )
