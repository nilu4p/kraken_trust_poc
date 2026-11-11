"""VMS (Vessel Monitoring System) position import — NAF messages or CSV exports.

VMS data is restricted to fisheries control administrations (CNSP Étel,
DM Guyane). No public API exists: the authority exports positions of the
licensed vessels (NAF "North Atlantic Format" messages or a CSV extract) and
an operator imports them here. They are fused with the AIS history
(``source = 'vms'``) so that:

- a SAR echo explained by a VMS position is not raised as a dark vessel;
- licensed vessels are not reported by the foreign-fishing rule.

NAF example::

    //SR//AD/FRA//FR/VEN//TM/POS//RC/YYV1234//NA/DON PEDRO//FS/VEN//
    LT/+5.123//LG/-052.456//SP/85//CO/270//DA/20261020//TI/1030//ER//

(SP = speed in tenths of knots, CO = course in degrees, DA/TI = UTC date/time.)
"""

from __future__ import annotations

import csv
import datetime
import io
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

MAX_ROWS = 200_000


# CSV header aliases (lower-case, accents stripped)
COLUMNS = {
    "mmsi": ("mmsi",),
    "ircs": ("ircs", "rc", "call_sign", "callsign", "indicatif", "indicatif_radio"),
    "cfr": ("cfr", "ir", "internal_reference", "immatriculation"),
    "name": ("name", "na", "vessel_name", "nom", "nom_navire", "navire"),
    "flag": ("flag", "fs", "flag_state", "pavillon"),
    "lat": ("lat", "latitude", "lt"),
    "lon": ("lon", "lng", "long", "longitude", "lg"),
    "speed": ("speed", "sog", "vitesse", "sp"),
    "course": ("course", "cog", "cap", "route", "co"),
    "ts": ("ts", "timestamp", "datetime", "date_time", "date_heure", "horodatage", "time_utc", "position_date"),
    "date": ("date", "da"),
    "time": ("time", "heure", "ti"),
}


class VmsFormatError(ValueError):
    pass


def _key(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _float(value: Any) -> Optional[float]:
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def _timestamp(ts: Any = None, date: Any = None, time: Any = None) -> Optional[str]:
    from app.pipeline.detection import _parse_time

    if ts:
        parsed = _parse_time(str(ts).replace("/", "-"))
        if parsed:
            return parsed.isoformat()
    d = re.sub(r"\D", "", str(date or ""))
    t = re.sub(r"\D", "", str(time or "")).ljust(4, "0")
    if len(d) == 8:
        try:
            value = datetime.datetime.strptime(d + t[:4], "%Y%m%d%H%M").replace(tzinfo=datetime.timezone.utc)
            return value.isoformat()
        except ValueError:
            return None
    return None


def _identity(mmsi: Any, ircs: Any, cfr: Any, name: Any) -> Optional[str]:
    text = re.sub(r"\D", "", str(mmsi or ""))
    if len(text) == 9:
        return text
    for value in (ircs, cfr, name):
        token = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
        if token:
            return f"VMS-{token}"[:20]
    return None


def _flag(value: Any) -> Optional[str]:
    from app.sources.mid import flag_name

    return flag_name(str(value or "").strip().upper()) or None


def _position(fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    lat, lon = _float(fields.get("lat")), _float(fields.get("lon"))
    vessel = _identity(fields.get("mmsi"), fields.get("ircs"), fields.get("cfr"), fields.get("name"))
    ts = _timestamp(fields.get("ts"), fields.get("date"), fields.get("time"))
    if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180 or not vessel or not ts:
        return None
    speed, course = _float(fields.get("speed")), _float(fields.get("course"))
    return {
        "mmsi": vessel,
        "name": str(fields.get("name") or "").strip()[:60],
        "call_sign": str(fields.get("ircs") or "").strip()[:20],
        "flag": _flag(fields.get("flag")),
        "lat": lat,
        "lon": lon,
        "sog": speed,
        "cog": course if course is not None and 0 <= course < 360 else None,
        "ts": ts,
        "source": "vms",
    }


def parse_naf(text: str) -> List[Dict[str, Any]]:
    """Parse NAF ``//SR// ... //ER//`` messages (one or many, any line layout)."""
    out = []
    for message in re.findall(r"//SR//(.*?)//ER//", text, flags=re.S):
        tags: Dict[str, str] = {}
        for part in re.sub(r"\s+", "", message).split("//"):
            if "/" in part:
                code, _, value = part.partition("/")
                tags[code.upper()] = value
        if tags.get("TM", "POS").upper() not in ("POS", "ENT", "EXI", "MAN"):
            continue
        speed = _float(tags.get("SP"))
        fields = {
            "mmsi": tags.get("MM"), "ircs": tags.get("RC"), "cfr": tags.get("IR"), "name": tags.get("NA"),
            "flag": tags.get("FS"), "lat": tags.get("LT"), "lon": tags.get("LG"),
            "speed": speed / 10 if speed is not None else None, "course": tags.get("CO"),
            "date": tags.get("DA"), "time": tags.get("TI"),
        }
        position = _position(fields)
        if position:
            out.append(position)
    return out


def parse_csv(text: str) -> List[Dict[str, Any]]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    header = next(reader, None)
    if not header:
        raise VmsFormatError("Fichier vide")
    keys = [_key(h) for h in header]
    index: Dict[str, int] = {}
    for field, aliases in COLUMNS.items():
        for i, k in enumerate(keys):
            if k in aliases and field not in index:
                index[field] = i
    if "lat" not in index or "lon" not in index or not ({"ts", "date"} & set(index)):
        raise VmsFormatError(
            "Colonnes requises introuvables : latitude, longitude et date/heure (ts ou date + time) ; "
            f"en-têtes lus : {', '.join(header[:12])}"
        )
    if not ({"mmsi", "ircs", "cfr", "name"} & set(index)):
        raise VmsFormatError("Il faut au moins un identifiant navire : mmsi, ircs (indicatif), cfr ou nom")
    out = []
    for n, row in enumerate(reader):
        if n >= MAX_ROWS:
            break
        fields = {f: row[i] for f, i in index.items() if i < len(row)}
        position = _position(fields)
        if position:
            out.append(position)
    return out


def parse(raw: bytes) -> Tuple[str, List[Dict[str, Any]]]:
    """Detect the format and return (format, positions)."""
    text = raw.decode("utf-8-sig", errors="replace")
    if "//SR//" in text:
        return "naf", parse_naf(text)
    return "csv", parse_csv(text)


def import_positions(raw: bytes, zone_of: Any = None) -> Dict[str, Any]:
    """Parse then fuse into the vessel history; returns an import report."""
    from app.sources import ais_store

    fmt, positions = parse(raw)
    if not positions:
        raise VmsFormatError("Aucune position VMS exploitable dans le fichier")
    vessels: Dict[str, Dict[str, Any]] = {}
    for p in positions:
        vessels.setdefault(p["mmsi"], p)
    for mmsi, p in vessels.items():
        # VMS = licensed fishing vessels: AIS ship type 30 by definition.
        ais_store.store_static(mmsi, name=p["name"], call_sign=p["call_sign"], ship_type=30)
    stored = ais_store.store_positions(positions, zone_of, source="vms")
    times = sorted(p["ts"] for p in positions)
    return {"format": fmt, "positions": stored, "vessels": len(vessels), "from": times[0], "to": times[-1]}


def licensed_mmsis(days: int = 7) -> set:
    """Vessels reported by VMS recently (= licensed / monitored fishing vessels)."""
    from app.db import read_db

    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
    with read_db() as conn:
        return {r[0] for r in conn.execute("SELECT DISTINCT mmsi FROM ais_positions WHERE source = 'vms' AND ts >= ?", (since,))}

