"""Entity graph over time: who is linked to whom, since when, and why.

Start from one or more entities (a vessel, an incident, a radar detection) and
expand their neighbourhood within a time window ``[start, end]`` (``end`` = the
reference instant of the situation, live or replay). Every link carries its
first / last time and the evidence that establishes it, so that the graph can
be replayed (links appear when they happened) and explained.

Entities (node ids):
  ``vessel:<mmsi>``            vessel of the AIS / VMS picture
  ``incident:<id>``            SOC incident
  ``detection:<alert>:<id>``   radar target sealed in an evidence dossier
  ``watch:<id>``               watchlist entry (Combined IUU list, manual)
  ``sanction:<id>``            OpenSanctions entity matched by a vessel

Links:
  vessel — incident     the vessel is the subject of the incident (or the second vessel of an encounter)
  incident — detection  radar observation of the incident
  vessel — vessel       AIS proximity: within ``PROXIMITY_KM`` in the same 10-minute slot, at least twice
  detection — vessel    AIS vessel within ``RADAR_NEIGHBOUR_KM`` of the echo at the radar pass
  vessel — watch        watchlist match (MMSI, IMO, call sign or name)
  vessel — sanction     OpenSanctions match (score)

Only what was known by ``end`` is used: incidents opened later, positions after
``end`` and dossiers sealed later never appear.
"""

from __future__ import annotations

import datetime
import json
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from app.config import Settings, get_settings
from app.core.geo import haversine_km
from app.db import read_db, rows
from app.sources import ais_store
from app.sources.mid import flag_name

PROXIMITY_KM = 1.0
PROXIMITY_MIN_SLOTS = 2          # 2 × 10 min together
RADAR_NEIGHBOUR_KM = 10.0
MAX_NODES = 150
MAX_FIXES = 200_000


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.astimezone(datetime.timezone.utc).isoformat()
    parsed = ais_store.parse_ts(value)
    return parsed.isoformat() if parsed else str(value)


class Graph:
    def __init__(self, start: datetime.datetime, end: datetime.datetime, cfg: Settings) -> None:
        self.start, self.end, self.cfg = start, end, cfg
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    @property
    def full(self) -> bool:
        return len(self.nodes) >= MAX_NODES

    def node(self, node_id: str, node_type: str, label: str, **props: Any) -> str:
        if node_id not in self.nodes:
            self.nodes[node_id] = {"id": node_id, "type": node_type, "label": label, **props}
        else:
            self.nodes[node_id].update({k: v for k, v in props.items() if v is not None})
        return node_id

    def edge(self, a: str, b: str, kind: str, label: str, first: Any, last: Any, evidence: str, **props: Any) -> None:
        key = (min(a, b), max(a, b), kind)
        first, last = _iso(first), _iso(last or first)
        current = self.edges.get(key)
        if current is None:
            self.edges[key] = {"id": "|".join(key), "source": a, "target": b, "type": kind, "label": label,
                               "first": first, "last": last, "evidence": [evidence], "count": 1, **props}
            return
        current["count"] += 1
        current["first"] = min(filter(None, [current["first"], first]), default=None)
        current["last"] = max(filter(None, [current["last"], last]), default=None)
        if evidence not in current["evidence"] and len(current["evidence"]) < 5:
            current["evidence"].append(evidence)


# --------------------------------------------------------------------------- entity loaders


def _vessel_node(graph: Graph, mmsi: str, conn: Any) -> str:
    row = conn.execute("SELECT * FROM ais_vessels WHERE mmsi = ?", (mmsi,)).fetchone()
    vessel = dict(row) if row else {"mmsi": mmsi}
    last = conn.execute("SELECT ts, lat, lon, source FROM ais_positions WHERE mmsi = ? AND ts <= ? ORDER BY ts DESC LIMIT 1",
                        (mmsi, graph.end.isoformat())).fetchone()
    try:
        intel = json.loads(vessel.get("intel") or "{}")
    except ValueError:
        intel = {}
    registry = intel.get("registry") or {}
    return graph.node(
        f"vessel:{mmsi}", "vessel", vessel.get("name") or (f"VMS {mmsi[4:]}" if mmsi.startswith("VMS-") else f"MMSI {mmsi}"),
        mmsi=mmsi, flag=vessel.get("flag") or flag_name(vessel.get("gfw_flag")), ship_type=vessel.get("ship_type"),
        fishing=vessel.get("ship_type") == 30 or bool(vessel.get("gfw_geartype")), imo=vessel.get("imo"),
        call_sign=vessel.get("call_sign"), first_seen=_iso(vessel.get("first_seen")),
        last_seen=_iso(last["ts"]) if last else None, lat=last["lat"] if last else None, lon=last["lon"] if last else None,
        feed=last["source"] if last else None, former_names=[f["name"] for f in registry.get("former_names") or []][:5],
        vessel_type=registry.get("vessel_type"),
    )


def _incident_node(graph: Graph, incident: Dict[str, Any], state: Optional[Dict[str, Any]]) -> str:
    return graph.node(
        f"incident:{incident['id']}", "incident", incident["ref"], incident_id=incident["id"], title=incident["title"],
        kind=incident["kind"], status=(state or incident)["status"], severity=(state or incident)["severity"],
        risk=incident.get("risk_score"), first_seen=_iso(incident["first_seen"]), last_seen=_iso(incident["last_seen"]),
        created_at=_iso(incident["created_at"]), lat=incident["lat"], lon=incident["lon"],
    )


def _states(graph: Graph) -> Dict[int, Dict[str, Any]]:
    """Incident status / severity as they stood at the end of the window (replay-exact)."""
    from app.soc import incidents

    return incidents.state_at(graph.end, graph.cfg)


# --------------------------------------------------------------------------- expansion


def _expand_vessel(graph: Graph, mmsi: str, conn: Any, states: Dict[int, Dict[str, Any]]) -> List[str]:
    found: List[str] = []
    me = _vessel_node(graph, mmsi, conn)
    low, high = graph.start.isoformat(), graph.end.isoformat()
    # Incidents about this vessel (observations in the window, incident known at the end).
    for r in rows(conn.execute(
        "SELECT i.*, MIN(o.seen_at) AS o_first, MAX(o.seen_at) AS o_last, COUNT(*) AS o_count FROM incident_observations o "
        "JOIN incidents i ON i.id = o.incident_id WHERE o.vessel_id = ? AND o.seen_at >= ? AND o.seen_at <= ? "
        "GROUP BY i.id", (f"AIS-{mmsi}"[:20], low, high)).fetchall()):
        if r["id"] not in states:
            continue
        node = _incident_node(graph, r, states[r["id"]])
        graph.edge(me, node, "subject", "objet de l'incident", r["o_first"], r["o_last"],
                   f"{r['o_count']} observation(s) de {r['ref']} ({r['title']})")
        found.append(node)
    # Second vessel of an encounter (recorded in the incident explanation).
    for r in rows(conn.execute(
        "SELECT i.*, e.at AS e_at FROM incident_events e JOIN incidents i ON i.id = e.incident_id "
        "WHERE e.kind = 'created' AND i.kind = 'encounter' AND e.payload LIKE ? AND e.at >= ? AND e.at <= ?",
        (f'%"{mmsi}"%', low, high)).fetchall()):
        if r["id"] not in states:
            continue
        node = _incident_node(graph, r, states[r["id"]])
        graph.edge(me, node, "subject", "impliqué dans le rendez-vous", r["e_at"], r["e_at"], f"{r['ref']} : {r['title']}")
        found.append(node)
    found += _proximity(graph, mmsi, conn)
    # Watchlist and sanctions (identity links: valid over the whole window).
    from app.soc.watchlist import Matcher, summary

    vessel = graph.nodes[me]
    hit = summary(Matcher(graph.cfg).match(mmsi=mmsi, imo=vessel.get("imo"), name=vessel["label"], call_sign=vessel.get("call_sign")))
    if hit:
        node = graph.node(f"watch:{hit['watchlist_id']}", "watch", hit.get("name") or f"Liste {hit['source']}",
                          source=hit["source"], reason=hit.get("reason"), flag=hit.get("flag"))
        graph.edge(me, node, "listed", "sur la liste de surveillance", vessel.get("first_seen"), graph.end,
                   f"correspondance sur {hit.get('matched_on')} — {hit.get('reason') or hit['source']}")
        found.append(node)
    row = conn.execute("SELECT intel FROM ais_vessels WHERE mmsi = ?", (mmsi,)).fetchone()
    try:
        intel = json.loads(row["intel"] or "{}") if row else {}
    except ValueError:
        intel = {}
    for match in intel.get("sanctions") or []:
        node = graph.node(f"sanction:{match.get('id')}", "sanction", match.get("caption") or "Entité sanctionnée",
                          datasets=match.get("datasets"), url=match.get("url"), score=match.get("score"))
        graph.edge(me, node, "sanctioned", "correspond à une entité sanctionnée", vessel.get("first_seen"), graph.end,
                   f"OpenSanctions, score {match.get('score')} — {', '.join((match.get('datasets') or [])[:3])}")
        found.append(node)
    return found


def _proximity(graph: Graph, mmsi: str, conn: Any) -> List[str]:
    """Vessels sharing the same place and 10-minute slot as ``mmsi`` at least twice in the window."""
    low, high = graph.start.isoformat(), graph.end.isoformat()
    mine = conn.execute("SELECT ts, lat, lon FROM ais_positions WHERE mmsi = ? AND ts >= ? AND ts <= ? ORDER BY ts",
                        (mmsi, low, high)).fetchall()
    if not mine:
        return []
    slots: Dict[str, Tuple[float, float]] = {}
    for p in mine:
        slots[_iso(p["ts"])[:15]] = (p["lat"], p["lon"])  # one fix per 10-minute slot
    margin = PROXIMITY_KM / 100.0 + 0.01
    lats, lons = [p["lat"] for p in mine], [p["lon"] for p in mine]
    others = conn.execute(
        "SELECT mmsi, ts, lat, lon FROM ais_positions WHERE ts >= ? AND ts <= ? AND mmsi != ? "
        "AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? LIMIT ?",
        (low, high, mmsi, min(lats) - margin, max(lats) + margin, min(lons) - margin, max(lons) + margin, MAX_FIXES),
    ).fetchall()
    together: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
    for p in others:
        slot = _iso(p["ts"])[:15]
        where = slots.get(slot)
        if where is None:
            continue
        distance = haversine_km(where[0], where[1], p["lat"], p["lon"])
        if distance <= PROXIMITY_KM and all(s != slot for s, _ in together[p["mmsi"]]):
            together[p["mmsi"]].append((slot, distance))
    found = []
    for other, hits in together.items():
        if len(hits) < PROXIMITY_MIN_SLOTS or graph.full:
            continue
        node = _vessel_node(graph, other, conn)
        hits.sort()
        closest = min(d for _, d in hits)
        graph.edge(f"vessel:{mmsi}", node, "proximity", "proximité AIS", hits[0][0] + "0:00+00:00", hits[-1][0] + "0:00+00:00",
                   f"à moins de {PROXIMITY_KM:g} km pendant ≥ {len(hits) * 10} min (au plus près : {closest * 1000:.0f} m)",
                   minutes=len(hits) * 10, closest_m=round(closest * 1000))
        found.append(node)
    return found


def _expand_incident(graph: Graph, incident_id: int, conn: Any, states: Dict[int, Dict[str, Any]]) -> List[str]:
    if incident_id not in states:
        return []
    incident = dict(conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone())
    me = _incident_node(graph, incident, states[incident_id])
    found = []
    for o in rows(conn.execute(
        "SELECT * FROM incident_observations WHERE incident_id = ? AND seen_at <= ? ORDER BY seen_at",
        (incident_id, graph.end.isoformat())).fetchall()):
        if o["vessel_id"].startswith("AIS-"):
            node = _vessel_node(graph, o["vessel_id"][4:], conn)
            graph.edge(node, me, "subject", "objet de l'incident", o["seen_at"], o["seen_at"], f"observation du {o['seen_at'][:16]}")
        else:
            node = graph.node(
                f"detection:{o['alert_id']}:{o['vessel_id']}", "detection", f"Écho {o['vessel_id']}",
                alert_id=o["alert_id"], target=o["vessel_id"], dossier=o["dossier"], scene_id=o["scene_id"],
                seen_at=_iso(o["seen_at"]), lat=o["lat"], lon=o["lon"], risk=o["risk_score"], quality=o["quality_score"],
            )
            graph.edge(me, node, "observation", "détection radar", o["seen_at"], o["seen_at"],
                       f"passage {o['scene_id'] or ''} · risque {o['risk_score']} · fiabilité {o['quality_score']}")
        found.append(node)
    created = conn.execute("SELECT payload FROM incident_events WHERE incident_id = ? AND kind = 'created'", (incident_id,)).fetchone()
    try:
        entities = (json.loads(created["payload"] or "{}").get("explanation") or {}).get("entities") or {} if created else {}
    except ValueError:
        entities = {}
    for mmsi in entities.get("vessels") or []:
        node = _vessel_node(graph, str(mmsi), conn)
        graph.edge(node, me, "subject", "impliqué dans le rendez-vous", incident["created_at"], incident["created_at"], incident["title"])
        found.append(node)
    return found


def _expand_detection(graph: Graph, node_id: str, conn: Any, states: Dict[int, Dict[str, Any]]) -> List[str]:
    _, alert_id, target = node_id.split(":", 2)
    o = conn.execute("SELECT * FROM incident_observations WHERE alert_id = ? AND vessel_id = ?", (alert_id, target)).fetchone()
    if o is None or o["seen_at"] > graph.end.isoformat():
        return []
    found = []
    graph.node(node_id, "detection", f"Écho {target}", alert_id=alert_id, target=target, dossier=o["dossier"],
               scene_id=o["scene_id"], seen_at=_iso(o["seen_at"]), lat=o["lat"], lon=o["lon"], risk=o["risk_score"],
               quality=o["quality_score"])
    if o["incident_id"] in states:
        incident = dict(conn.execute("SELECT * FROM incidents WHERE id = ?", (o["incident_id"],)).fetchone())
        node = _incident_node(graph, incident, states[o["incident_id"]])
        graph.edge(node, node_id, "observation", "détection radar", o["seen_at"], o["seen_at"],
                   f"passage {o['scene_id'] or ''} · risque {o['risk_score']} · fiabilité {o['quality_score']}")
        found.append(node)
    # AIS vessels around the echo at the pass: escort, mother ship, transshipment partner…
    for ship in ais_store.positions_at(o["seen_at"], 1.0, graph.cfg):
        distance = haversine_km(o["lat"], o["lon"], ship["lat"], ship["lon"])
        if distance > RADAR_NEIGHBOUR_KM or graph.full:
            continue
        node = _vessel_node(graph, ship["mmsi"], conn)
        graph.edge(node_id, node, "radar_neighbour", "navire AIS proche au passage radar", o["seen_at"], o["seen_at"],
                   f"à {distance:.1f} km de l'écho (position estimée à l'instant du passage, écart {ship['fix_age_h']} h)",
                   distance_km=round(distance, 2))
        found.append(node)
    return found


# --------------------------------------------------------------------------- public API


def build(focus: Iterable[str], end: Optional[datetime.datetime] = None, days: float = 7, depth: int = 1,
          settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Neighbourhood of the focus entities within ``[end - days, end]`` (depth 1 or 2)."""
    cfg = settings or get_settings()
    end = end or datetime.datetime.now(datetime.timezone.utc)
    graph = Graph(end - datetime.timedelta(days=days), end, cfg)
    states = _states(graph)
    frontier: List[str] = [f for f in dict.fromkeys(focus) if ":" in f][:10]
    expanded: Set[str] = set()
    with read_db(cfg) as conn:
        for level in range(max(1, min(depth, 3))):
            next_frontier: List[str] = []
            for node_id in frontier:
                if node_id in expanded or graph.full:
                    continue
                expanded.add(node_id)
                kind, _, key = node_id.partition(":")
                if kind == "vessel":
                    next_frontier += _expand_vessel(graph, key, conn, states)
                elif kind == "incident" and key.isdigit():
                    next_frontier += _expand_incident(graph, int(key), conn, states)
                elif kind == "detection":
                    next_frontier += _expand_detection(graph, node_id, conn, states)
            frontier = next_frontier
    for node_id in focus:
        if node_id in graph.nodes:
            graph.nodes[node_id]["focus"] = True
    return {
        "nodes": list(graph.nodes.values()),
        "edges": [e for e in graph.edges.values() if e["source"] in graph.nodes and e["target"] in graph.nodes],
        "window": {"start": graph.start.isoformat(), "end": graph.end.isoformat(), "days": days},
        "focus": list(focus),
        "truncated": graph.full,
        "limits": {"nodes": MAX_NODES, "proximity_km": PROXIMITY_KM, "radar_neighbour_km": RADAR_NEIGHBOUR_KM},
    }


def search(query: str, settings: Optional[Settings] = None, limit: int = 12) -> List[Dict[str, Any]]:
    """Entities whose name / MMSI / IMO / call sign / incident reference matches ``query``."""
    text = query.strip()
    if len(text) < 2:
        return []
    like = f"%{text.upper()}%"
    with read_db(settings) as conn:
        vessels = rows(conn.execute(
            "SELECT mmsi, name, flag, last_seen FROM ais_vessels WHERE UPPER(name) LIKE ? OR mmsi LIKE ? OR imo LIKE ? "
            "OR UPPER(COALESCE(call_sign, '')) LIKE ? ORDER BY last_seen DESC LIMIT ?",
            (like, like, like, like, limit)).fetchall())
        found_incidents = rows(conn.execute(
            "SELECT id, ref, title, severity FROM incidents WHERE UPPER(ref) LIKE ? OR UPPER(title) LIKE ? "
            "ORDER BY created_at DESC LIMIT ?", (like, like, limit)).fetchall())
    return [
        *[{"id": f"vessel:{v['mmsi']}", "type": "vessel", "label": v["name"] or v["mmsi"],
           "detail": f"MMSI {v['mmsi']} · {v['flag'] or 'pavillon inconnu'}"} for v in vessels],
        *[{"id": f"incident:{i['id']}", "type": "incident", "label": i["ref"], "detail": f"{i['severity']} · {i['title']}"}
          for i in found_incidents],
    ][:limit]
