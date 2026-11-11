"""Custom sources: any REST / GeoJSON / CSV API, XYZ / WMTS tiles or OGC WMS, declared from the dashboard.

A source is a declarative definition (URL template, parameters, headers, body,
authentication, pagination, response mapping, display). Definitions and their
secret live Fernet-encrypted in ``keys/custom_sources.enc`` (shared by the API
and the workers); the secret is write-only through the API and only ever sent
to the source itself — tiles and data are proxied by the backend, so the
browser never sees it.

Every outbound request goes through the same guard: http(s) only, no
credentials in the URL, public addresses only (``CUSTOM_SOURCE_ALLOWED_HOSTS``
for trusted internal hosts), each redirect re-checked, bounded size and time.
"""

from __future__ import annotations

import base64
import csv
import datetime
import hashlib
import io
import ipaddress
import json
import logging
import math
import os
import re
import socket
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import Settings, get_settings
from app.core import vault
from app.sources import health
from app.sources.http import USER_AGENT, atomic_write, cache_read, cache_read_stale, cache_write

logger = logging.getLogger(__name__)

STORE_NAME = "custom_sources.enc"
MAX_FEATURES = 5000
MAX_TILE_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3
PREVIEW_CHARS = 3000
# Guyane: tile used to test a tile source (map centre).
TEST_POINT = (5.6, -52.4)
# Replaced in tests by an httpx.MockTransport (no network).
_TRANSPORT: Optional[httpx.BaseTransport] = None
_lock = threading.Lock()
# Behind an outbound HTTP proxy the socket peer is the proxy itself: the peer check does not apply.
_PROXIED = any(os.environ.get(name) for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"))

ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,40}$"
FORBIDDEN_HEADERS = {"host", "content-length", "cookie", "transfer-encoding", "connection"}
PLACEHOLDERS = ("secret", "bbox", "west", "south", "east", "north", "date", "today", "start", "end",
                "start_iso", "end_iso", "lookback_days", "page", "offset", "limit")
LAT_NAMES = ("lat", "latitude", "y", "lat_dd", "latitude_dd")
LON_NAMES = ("lon", "lng", "long", "longitude", "x", "lon_dd", "longitude_dd")
GEOMETRY_TYPES = {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}


class CustomSourceError(Exception):
    """A step of a custom source call failed; ``stage`` tells which (url, network, http, parse, mapping)."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


# --------------------------------------------------------------------------- definition


class KeyValue(BaseModel):
    key: str = Field(..., min_length=1, max_length=128)
    value: str = Field("", max_length=2048)


class Auth(BaseModel):
    # header: <name>: <secret> · query: ?<name>=<secret> · bearer: Authorization: Bearer <secret>
    # basic: HTTP Basic <username>:<secret> · none: no secret, or {secret} placed by hand.
    type: Literal["none", "header", "query", "bearer", "basic"] = "none"
    name: str = Field("", max_length=128)
    username: str = Field("", max_length=256)


class Mapping(BaseModel):
    """Where the records are and which fields hold the position (dot paths, auto-detected when empty).

    ``records_path`` "." = the whole document is one record.
    """

    records_path: str = Field("", max_length=256)
    lat: str = Field("", max_length=256)
    lon: str = Field("", max_length=256)
    geometry: str = Field("", max_length=256)
    time: str = Field("", max_length=256)
    id: str = Field("", max_length=256)
    label: str = Field("", max_length=256)
    fields: List[str] = Field(default_factory=list, max_length=50)


class Pagination(BaseModel):
    # page: ?<param>=start, start+1… · offset: ?<param>=0, size, 2×size… · next: URL found at <next_path>
    type: Literal["none", "page", "offset", "next"] = "none"
    param: str = Field("page", max_length=64)
    start: int = Field(1, ge=0, le=1_000_000)
    size_param: str = Field("", max_length=64)
    size: int = Field(100, ge=1, le=10_000)
    next_path: str = Field("", max_length=256)
    max_pages: int = Field(1, ge=1, le=20)


class TileOptions(BaseModel):
    max_native_zoom: int = Field(18, ge=0, le=22)
    attribution: str = Field("", max_length=300)
    opacity: float = Field(0.8, ge=0.05, le=1.0)
    # screen: black becomes transparent (night lights, radiance) · multiply: white becomes transparent.
    blend: Literal["normal", "screen", "multiply", "lighten"] = "normal"


class WmsOptions(BaseModel):
    layers: str = Field("", max_length=500)
    styles: str = Field("", max_length=500)
    format: str = Field("image/png", max_length=64)
    version: Literal["1.1.1", "1.3.0"] = "1.3.0"
    transparent: bool = True
    # Send TIME=<date of the map imagery> (time-enabled WMS layers).
    time: bool = False


class Style(BaseModel):
    color: str = Field("#22d3ee", pattern=r"^#[0-9a-fA-F]{6}$")
    radius: int = Field(5, ge=2, le=20)


class SourceDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field("", max_length=41)
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field("", max_length=1000)
    category: str = Field("Source personnalisée", max_length=60)
    enabled: bool = True
    # features: points / lines / polygons drawn on the map · tiles: XYZ / WMTS raster · wms: OGC WMS GetMap.
    kind: Literal["features", "tiles", "wms"] = "features"
    url: str = Field(..., min_length=8, max_length=2048)
    method: Literal["GET", "POST"] = "GET"
    params: List[KeyValue] = Field(default_factory=list, max_length=30)
    headers: List[KeyValue] = Field(default_factory=list, max_length=30)
    body: str = Field("", max_length=20_000)
    auth: Auth = Field(default_factory=Auth)
    format: Literal["auto", "json", "geojson", "csv"] = "auto"
    csv_delimiter: str = Field("", max_length=1)
    mapping: Mapping = Field(default_factory=Mapping)
    pagination: Pagination = Field(default_factory=Pagination)
    tiles: TileOptions = Field(default_factory=TileOptions)
    wms: WmsOptions = Field(default_factory=WmsOptions)
    style: Style = Field(default_factory=Style)
    lookback_days: int = Field(7, ge=0, le=366)
    refresh_minutes: int = Field(15, ge=1, le=10_080)
    timeout_s: float = Field(20.0, ge=2.0, le=120.0)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if value and not re.match(ID_PATTERN, value):
            raise ValueError("Identifiant : minuscules, chiffres, - et _ (2 à 41 caractères)")
        return value

    @field_validator("headers")
    @classmethod
    def _headers(cls, value: List[KeyValue]) -> List[KeyValue]:
        for item in value:
            if item.key.strip().lower() in FORBIDDEN_HEADERS or not re.match(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$", item.key.strip()):
                raise ValueError(f"En-tête interdit ou invalide : {item.key}")
        return value


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:36] or "source"
    return slug if len(slug) > 1 else f"{slug}-1"


# --------------------------------------------------------------------------- encrypted store


def _store_path(cfg: Settings) -> Path:
    return cfg.keys_dir / STORE_NAME


def _read(cfg: Settings) -> Dict[str, Any]:
    path = _store_path(cfg)
    if not path.is_file():
        return {"sources": {}, "secrets": {}}
    try:
        data = json.loads(vault.decrypt(path.read_bytes(), cfg))
        return {"sources": data.get("sources") or {}, "secrets": data.get("secrets") or {}}
    except Exception as exc:
        logger.error("Custom source store unreadable (%s) — check MASTER_KEY", type(exc).__name__)
        return {"sources": {}, "secrets": {}}


def _write(cfg: Settings, data: Dict[str, Any]) -> None:
    vault.write_private(_store_path(cfg), vault.encrypt(json.dumps(data).encode(), cfg))


def _mask(secret: str) -> str:
    return "" if not secret else (f"••••{secret[-4:]}" if len(secret) > 8 else "••••")


def _public(definition: Dict[str, Any], secret: str) -> Dict[str, Any]:
    return {**definition, "has_secret": bool(secret), "secret_hint": _mask(secret)}


def list_sources(settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    """Every definition (secrets never included, only whether one is set)."""
    data = _read(settings or get_settings())
    return [_public(d, data["secrets"].get(sid, "")) for sid, d in sorted(data["sources"].items())]


def map_layers(settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    """What any operator needs to draw the enabled sources (no URL, parameters nor secret)."""
    keep = ("id", "name", "description", "category", "kind", "style", "tiles", "refresh_minutes")
    return [{k: d[k] for k in keep if k in d} for d in list_sources(settings) if d.get("enabled")]


def get_source(source_id: str, settings: Optional[Settings] = None) -> Tuple[Dict[str, Any], str]:
    """(definition, secret) or KeyError."""
    data = _read(settings or get_settings())
    if source_id not in data["sources"]:
        raise KeyError(source_id)
    return data["sources"][source_id], data["secrets"].get(source_id, "")


def save_source(
    definition: SourceDefinition,
    secret: Optional[str],
    *,
    source_id: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    """Create (``source_id`` None) or replace a source. ``secret``: None keeps it, "" removes it."""
    cfg = settings or get_settings()
    with _lock:
        data = _read(cfg)
        if source_id is None:
            new_id = definition.id or slugify(definition.name)
            base, n = new_id, 2
            while new_id in data["sources"]:
                new_id, n = f"{base[:37]}-{n}", n + 1
        else:
            if source_id not in data["sources"]:
                raise KeyError(source_id)
            new_id = source_id
        stored = definition.model_dump()
        stored["id"] = new_id
        stored["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        data["sources"][new_id] = stored
        if secret is not None:
            value = secret.strip()
            if len(value) > 4096 or any(ch in value for ch in "\r\n"):
                raise ValueError("Secret invalide")
            if value:
                data["secrets"][new_id] = value
            else:
                data["secrets"].pop(new_id, None)
        _write(cfg, data)
    return _public(stored, data["secrets"].get(new_id, ""))


def delete_source(source_id: str, settings: Optional[Settings] = None) -> None:
    cfg = settings or get_settings()
    with _lock:
        data = _read(cfg)
        if source_id not in data["sources"]:
            raise KeyError(source_id)
        data["sources"].pop(source_id)
        data["secrets"].pop(source_id, None)
        _write(cfg, data)


# --------------------------------------------------------------------------- outbound guard


def _resolve(host: str, port: int) -> List[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)]


def check_url(url: str, *, carries_secret: bool, settings: Optional[Settings] = None) -> None:
    """SSRF policy for custom sources (raises CustomSourceError(stage="url"))."""
    cfg = settings or get_settings()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise CustomSourceError("url", "L'URL doit commencer par https:// (ou http://) et contenir un nom d'hôte")
    if parsed.username or parsed.password:
        raise CustomSourceError("url", "Identifiants interdits dans l'URL : utilisez le champ secret et l'authentification")
    trusted = parsed.hostname in cfg.custom_source_allowed_hosts
    if parsed.scheme == "http" and carries_secret and not trusted:
        raise CustomSourceError("url", "Un secret ne peut pas être envoyé en clair : utilisez https://")
    if trusted:
        return
    try:
        addresses = _resolve(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except (socket.gaierror, UnicodeError) as exc:
        raise CustomSourceError("url", f"Nom d'hôte introuvable : {parsed.hostname}") from exc
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise CustomSourceError(
                "url",
                f"{parsed.hostname} pointe vers une adresse interne ({address}) : bloqué (SSRF). "
                "Un hôte interne de confiance s'ajoute dans CUSTOM_SOURCE_ALLOWED_HOSTS.",
            )


def _check_peer(response: httpx.Response, url: str, cfg: Settings) -> None:
    """The address actually connected to must be public too (DNS rebinding between the check and the call).

    The name is resolved twice — by :func:`check_url`, then by the HTTP client — and a hostile DNS
    server can answer a public address the first time and an internal one the second. The socket's
    peer is checked before anything of the answer is read or returned.
    """
    if urlparse(url).hostname in cfg.custom_source_allowed_hosts or _PROXIED:
        return
    stream = response.extensions.get("network_stream")
    try:
        peer = stream.get_extra_info("server_addr") if stream is not None else None
    except Exception:
        peer = None
    if peer and not ipaddress.ip_address(str(peer[0]).split("%")[0]).is_global:
        raise CustomSourceError("url", f"Connexion vers une adresse interne ({peer[0]}) : bloquée (SSRF)")


class Response:
    def __init__(self, status: int, content_type: str, body: bytes, elapsed_ms: int, url: str) -> None:
        self.status = status
        self.content_type = content_type
        self.body = body
        self.elapsed_ms = elapsed_ms
        self.url = url


def _send(
    method: str,
    url: str,
    *,
    params: Dict[str, str],
    headers: Dict[str, str],
    content: Optional[bytes],
    auth: Optional[Tuple[str, str]],
    secret: str,
    timeout: float,
    max_bytes: int,
    cfg: Settings,
) -> Response:
    started = time.monotonic()
    origin = urlparse(url).hostname
    for hop in range(MAX_REDIRECTS + 1):
        check_url(url, carries_secret=bool(secret) and urlparse(url).hostname == origin, settings=cfg)
        try:
            with httpx.Client(transport=_TRANSPORT, timeout=timeout, follow_redirects=False) as client:
                # Merged, not replaced: httpx drops the URL's own query string when given ``params``
                # (a "next" page cursor, or parameters written in the URL template, were lost).
                target = httpx.URL(url).copy_merge_params(params) if params else httpx.URL(url)
                request = client.build_request(method, target, headers={"User-Agent": USER_AGENT, **headers}, content=content)
                response = client.send(request, stream=True, auth=auth)
                try:
                    _check_peer(response, url, cfg)
                    if response.is_redirect and "location" in response.headers:
                        if hop == MAX_REDIRECTS:
                            raise CustomSourceError("http", "Trop de redirections")
                        url = urljoin(str(request.url), response.headers["location"])
                        params, content = {}, None
                        method = "GET" if response.status_code in (301, 302, 303) else method
                        if urlparse(url).hostname != origin:
                            # Never forward the secret to another host.
                            headers, auth = {}, None
                        continue
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise CustomSourceError("http", f"Réponse trop volumineuse (> {max_bytes // (1024 * 1024)} Mo)")
                        chunks.append(chunk)
                    return Response(response.status_code, response.headers.get("content-type", ""), b"".join(chunks),
                                    int((time.monotonic() - started) * 1000), str(request.url))
                finally:
                    response.close()
        except CustomSourceError:
            raise
        except httpx.TimeoutException as exc:
            raise CustomSourceError("network", f"Délai dépassé ({timeout:.0f} s)") from exc
        except httpx.HTTPError as exc:
            raise CustomSourceError("network", f"Connexion impossible : {type(exc).__name__}") from exc
    raise CustomSourceError("http", "Trop de redirections")


# --------------------------------------------------------------------------- request building


def _context(defn: Dict[str, Any], secret: str, cfg: Settings, extra: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    west, south, east, north = cfg.eez_bbox
    now = datetime.datetime.now(datetime.timezone.utc)
    start = now - datetime.timedelta(days=int(defn.get("lookback_days") or 0))
    ctx = {
        "secret": secret,
        "bbox": f"{west},{south},{east},{north}",
        "west": str(west), "south": str(south), "east": str(east), "north": str(north),
        "date": (now - datetime.timedelta(days=1)).date().isoformat(),
        "today": now.date().isoformat(),
        "start": start.date().isoformat(),
        "end": now.date().isoformat(),
        "start_iso": start.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "end_iso": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "lookback_days": str(defn.get("lookback_days") or 0),
        "page": "", "offset": "", "limit": "",
    }
    ctx.update({k: str(v) for k, v in (extra or {}).items()})
    return ctx


_TOKEN = re.compile(r"\{(" + "|".join(PLACEHOLDERS + ("z", "x", "y", "-y")) + r")\}")


def render(template: str, ctx: Dict[str, str]) -> str:
    """Replace known {placeholders} only: JSON braces and unknown tokens are left untouched."""
    return _TOKEN.sub(lambda m: ctx.get(m.group(1), m.group(0)), template or "")


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "••••") if secret else text


def _build(defn: Dict[str, Any], secret: str, ctx: Dict[str, str]) -> Dict[str, Any]:
    params = {kv["key"]: render(kv["value"], ctx) for kv in defn.get("params") or []}
    headers = {kv["key"].strip(): render(kv["value"], ctx) for kv in defn.get("headers") or []}
    auth_cfg = defn.get("auth") or {}
    kind, auth = auth_cfg.get("type", "none"), None
    if kind != "none" and not secret:
        raise CustomSourceError("url", "Authentification choisie mais aucun secret enregistré")
    if kind == "header":
        headers[auth_cfg.get("name") or "X-API-Key"] = secret
    elif kind == "query":
        params[auth_cfg.get("name") or "api_key"] = secret
    elif kind == "bearer":
        headers["Authorization"] = f"Bearer {secret}"
    elif kind == "basic":
        auth = (auth_cfg.get("username") or "", secret)
    content = None
    if defn.get("method") == "POST" and defn.get("body"):
        content = render(defn["body"], ctx).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    return {"url": render(defn["url"], ctx), "params": params, "headers": headers, "content": content, "auth": auth}


def _describe_request(method: str, req: Dict[str, Any], secret: str) -> Dict[str, Any]:
    query = "&".join(f"{k}={v}" for k, v in req["params"].items())
    url = req["url"] + (("&" if "?" in req["url"] else "?") + query if query else "")
    return {
        "method": method,
        "url": _redact(url, secret),
        "headers": {k: _redact(v, secret) for k, v in req["headers"].items()},
        "body": _redact(req["content"].decode("utf-8", "replace"), secret)[:2000] if req["content"] else "",
    }


# --------------------------------------------------------------------------- response parsing


def dot_get(obj: Any, path: str) -> Any:
    """``a.b.0.c`` in nested dicts / lists (None when absent)."""
    if not path:
        return obj
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and part.lstrip("-").isdigit():
            index = int(part)
            obj = obj[index] if -len(obj) <= index < len(obj) else None
        else:
            return None
        if obj is None:
            return None
    return obj


def flatten(record: Any, prefix: str = "", depth: int = 0, out: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Scalar leaves of a record as {dot.path: value} (3 levels, 80 fields at most)."""
    out = {} if out is None else out
    if isinstance(record, dict) and depth < 3:
        for key, value in record.items():
            if len(out) >= 80:
                break
            if isinstance(value, (dict, list)):
                flatten(value, f"{prefix}{key}.", depth + 1, out)
            else:
                out.setdefault(f"{prefix}{key}", value)
    elif isinstance(record, list) and depth < 3 and record and not isinstance(record[0], (dict, list)):
        out[prefix.rstrip(".")] = record[:6]
    elif isinstance(record, list) and depth < 3 and record:
        flatten(record[0], f"{prefix}0.", depth + 1, out)
    elif prefix:
        out[prefix.rstrip(".")] = record if not isinstance(record, (dict, list)) else "…"
    return out


def _find_records(doc: Any, depth: int = 0, path: str = "") -> Tuple[Optional[List[Any]], str]:
    """Largest list of objects in the document (breadth-first, 4 levels)."""
    if isinstance(doc, list):
        return doc, path
    if not isinstance(doc, dict) or depth > 4:
        return None, path
    best: Tuple[Optional[List[Any]], str] = (None, path)
    for key, value in doc.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            if best[0] is None or len(value) > len(best[0]):
                best = (value, f"{path}{key}")
    if best[0] is not None:
        return best
    for key, value in doc.items():
        # An empty list is a valid answer (last page, nothing in the area today).
        if isinstance(value, list) and not value:
            return value, f"{path}{key}"
    for key, value in doc.items():
        if isinstance(value, dict):
            found = _find_records(value, depth + 1, f"{path}{key}.")
            if found[0] is not None:
                return found
    return None, path


def parse_body(defn: Dict[str, Any], resp: Response) -> Tuple[List[Any], str, Any]:
    """(records, records_path_used, decoded document) — raises CustomSourceError(stage="parse")."""
    fmt = defn.get("format") or "auto"
    text = resp.body.decode("utf-8-sig", "replace")
    is_csv = fmt == "csv" or (fmt == "auto" and ("csv" in resp.content_type or "text/plain" in resp.content_type)
                              and not text.lstrip().startswith(("{", "[")))
    if is_csv:
        sample = text[:4096]
        delimiter = defn.get("csv_delimiter") or ""
        if not delimiter:
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except csv.Error:
                delimiter = ","
        rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
        if not rows:
            raise CustomSourceError("parse", "CSV vide ou sans ligne d'en-tête")
        return rows, "", rows
    try:
        doc = json.loads(text)
    except ValueError as exc:
        hint = " (la réponse ressemble à du HTML/XML : mauvaise URL ou erreur du service ?)" if text.lstrip().startswith("<") else ""
        raise CustomSourceError("parse", f"Réponse non JSON{hint}") from exc
    mapping = defn.get("mapping") or {}
    if mapping.get("records_path") == "." and isinstance(doc, dict):
        return [doc], ".", doc
    if mapping.get("records_path"):
        records = dot_get(doc, mapping["records_path"])
        if not isinstance(records, list):
            raise CustomSourceError("mapping", f"Chemin des enregistrements « {mapping['records_path']} » : aucune liste trouvée")
        return records, mapping["records_path"], doc
    if isinstance(doc, dict) and doc.get("type") == "FeatureCollection":
        return doc.get("features") or [], "features", doc
    if isinstance(doc, dict) and doc.get("type") == "Feature":
        return [doc], "", doc
    records, path = _find_records(doc)
    if records is None and isinstance(doc, dict):
        # A single object carrying its own position (point forecast, station status…).
        fields = flatten(doc)
        if (mapping.get("lat") and mapping.get("lon")) or (_guess(fields, LAT_NAMES) and _guess(fields, LON_NAMES)):
            return [doc], ".", doc
    if records is None:
        raise CustomSourceError("mapping", "Aucune liste d'enregistrements trouvée : indiquez le chemin (ex. data.items)")
    return records, path, doc


def _float(value: Any) -> Optional[float]:
    try:
        number = float(str(value).replace(",", ".")) if value not in (None, "") else None
    except ValueError:
        return None
    return number if number is not None and math.isfinite(number) else None


def _guess(fields: Dict[str, Any], names: Tuple[str, ...]) -> str:
    for name in names:
        for path in fields:
            if path.split(".")[-1].lower() == name and _float(fields[path]) is not None:
                return path
    return ""


_WKT_POINT = re.compile(r"^\s*POINT\s*\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s*\)\s*$", re.I)


def _geometry(record: Any, mapping: Dict[str, Any], lat_path: str, lon_path: str) -> Optional[Dict[str, Any]]:
    if isinstance(record, dict) and record.get("type") == "Feature":
        geom = record.get("geometry")
        return geom if isinstance(geom, dict) and geom.get("type") in GEOMETRY_TYPES else None
    if mapping.get("geometry"):
        geom = dot_get(record, mapping["geometry"])
        if isinstance(geom, str):
            match = _WKT_POINT.match(geom)
            return {"type": "Point", "coordinates": [float(match.group(1)), float(match.group(2))]} if match else None
        if isinstance(geom, dict) and geom.get("type") in GEOMETRY_TYPES:
            return geom
        return None
    lat, lon = _float(dot_get(record, lat_path)), _float(dot_get(record, lon_path))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {"type": "Point", "coordinates": [lon, lat]}


def normalize(defn: Dict[str, Any], records: List[Any]) -> Dict[str, Any]:
    """Records → GeoJSON FeatureCollection, plus the detected mapping and warnings."""
    mapping = defn.get("mapping") or {}
    first = records[0] if records else {}
    first_props = first.get("properties", {}) if isinstance(first, dict) and first.get("type") == "Feature" else first
    fields = flatten(first_props)
    lat_path = mapping.get("lat") or _guess(fields, LAT_NAMES)
    lon_path = mapping.get("lon") or _guess(fields, LON_NAMES)
    features, skipped = [], 0
    for record in records[:MAX_FEATURES]:
        geom = _geometry(record, mapping, lat_path, lon_path)
        if geom is None:
            skipped += 1
            continue
        is_feature = isinstance(record, dict) and record.get("type") == "Feature"
        props_src = (record.get("properties") or {}) if is_feature else record
        flat = flatten(props_src)
        keep = mapping.get("fields") or list(flat)[:30]
        props = {path: flat.get(path, dot_get(props_src, path)) for path in keep}
        props = {k: v for k, v in props.items() if v is None or isinstance(v, (str, int, float, bool))}
        for key, path in (("_id", mapping.get("id")), ("_label", mapping.get("label")), ("_time", mapping.get("time"))):
            if path:
                value = dot_get(props_src, path)
                props[key] = value if isinstance(value, (str, int, float, bool)) or value is None else str(value)
        features.append({"type": "Feature", "geometry": geom, "properties": props})
    warnings = []
    if len(records) > MAX_FEATURES:
        warnings.append(f"{len(records)} enregistrements : seuls les {MAX_FEATURES} premiers sont affichés")
    if records and not features:
        warnings.append("Aucune position exploitable : indiquez les champs latitude / longitude ou géométrie")
    elif skipped:
        warnings.append(f"{skipped} enregistrement(s) sans position valide ignoré(s)")
    return {
        "type": "FeatureCollection",
        "features": features,
        "mapping_used": {"lat": lat_path, "lon": lon_path, "geometry": mapping.get("geometry", "")},
        "detected_fields": [{"path": p, "sample": _sample(v)} for p, v in fields.items()],
        "records": len(records),
        "warnings": warnings,
    }


def _sample(value: Any) -> Any:
    if isinstance(value, str) and len(value) > 80:
        return value[:80] + "…"
    return value


# --------------------------------------------------------------------------- features


def _check_status(resp: Response, secret: str) -> None:
    if resp.status >= 400:
        excerpt = _redact(resp.body[:300].decode("utf-8", "replace"), secret).strip()
        meaning = {401: "authentification refusée (secret ?)", 403: "accès refusé (droits, quota ?)", 404: "ressource introuvable (URL ?)",
                   429: "quota dépassé", 400: "requête refusée (paramètres ?)"}.get(resp.status, "erreur du service")
        raise CustomSourceError("http", f"HTTP {resp.status} — {meaning}{f' : {excerpt}' if excerpt else ''}")


def fetch_features(defn: Dict[str, Any], secret: str, settings: Optional[Settings] = None,
                   trace: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Call the source (with pagination) and return a normalised FeatureCollection."""
    cfg = settings or get_settings()
    page_cfg = defn.get("pagination") or {}
    ptype = page_cfg.get("type", "none")
    pages = int(page_cfg.get("max_pages") or 1) if ptype != "none" else 1
    size = int(page_cfg.get("size") or 100)
    records: List[Any] = []
    next_url: Optional[str] = None
    path_used, doc = "", None
    for index in range(pages):
        extra = {"page": int(page_cfg.get("start") or 0) + index, "offset": index * size, "limit": size}
        req = _build(defn, secret, _context(defn, secret, cfg, extra))
        if ptype in ("page", "offset") and page_cfg.get("param"):
            req["params"][page_cfg["param"]] = str(extra["page" if ptype == "page" else "offset"])
            if page_cfg.get("size_param"):
                req["params"][page_cfg["size_param"]] = str(size)
        source_host = urlparse(req["url"]).hostname
        carries_secret = True
        if next_url:
            same_host = urlparse(next_url).hostname == source_host
            # The next page's URL comes from the response: on the source's own host it carries every
            # parameter but the secret; anywhere else nothing secret may follow it (no query secret,
            # no header holding it, no HTTP Basic).
            req["url"] = next_url
            req["params"] = {k: v for k, v in req["params"].items() if v == secret} if same_host and secret else {}
            if not same_host:
                req["headers"] = {k: v for k, v in req["headers"].items() if not secret or secret not in v}
                req["auth"] = None
                carries_secret = False
        if trace is not None and index == 0:
            trace["request"] = _describe_request(defn.get("method", "GET"), req, secret)
        resp = _send(defn.get("method", "GET"), req["url"], params=req["params"], headers=req["headers"], content=req["content"],
                     auth=req["auth"], secret=secret if carries_secret else "", timeout=float(defn.get("timeout_s") or cfg.http_timeout),
                     max_bytes=cfg.custom_source_max_mb * 1024 * 1024, cfg=cfg)
        if trace is not None and index == 0:
            trace["http"] = {"status": resp.status, "content_type": resp.content_type, "bytes": len(resp.body),
                             "elapsed_ms": resp.elapsed_ms}
            trace["preview"] = _redact(resp.body[:PREVIEW_CHARS].decode("utf-8", "replace"), secret)
        _check_status(resp, secret)
        page_records, path_used, doc = parse_body(defn, resp)
        records.extend(page_records)
        if not page_records or len(records) >= MAX_FEATURES:
            break
        if ptype == "next":
            candidate = dot_get(doc, page_cfg.get("next_path") or "") if page_cfg.get("next_path") else None
            if not isinstance(candidate, str) or not candidate:
                break
            next_url = urljoin(resp.url, candidate)
        if trace is not None:
            trace["pages"] = index + 1
    collection = normalize(defn, records)
    collection["records_path"] = path_used
    return collection


def cached_features(source_id: str, settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Map data of an enabled source, cached ``refresh_minutes``; last good copy when the source fails."""
    cfg = settings or get_settings()
    defn, secret = get_source(source_id, cfg)
    if defn.get("kind") != "features":
        raise CustomSourceError("url", "Cette source est une couche d'images (tuiles), pas des objets")
    key = hashlib.sha256(json.dumps(defn, sort_keys=True).encode() + secret.encode()).hexdigest()
    namespace = f"custom_{source_id}"
    cached = cache_read(namespace, key, float(defn.get("refresh_minutes") or 15) * 60, cfg)
    if cached is not None:
        return cached
    try:
        collection = fetch_features(defn, secret, cfg)
    except CustomSourceError as exc:
        health.record(f"custom:{source_id}", False, f"{exc.stage}: {exc}")
        stale = cache_read_stale(namespace, key, cfg)
        if stale is not None:
            return {**stale, "stale": True, "error": str(exc)}
        raise
    health.record(f"custom:{source_id}", True)
    result = {k: collection[k] for k in ("type", "features", "warnings", "records")}
    result.update({"source": source_id, "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "stale": False})
    cache_write(namespace, key, result, cfg)
    return result


# --------------------------------------------------------------------------- tiles / WMS

_EARTH = 20037508.342789244


def tile_bbox_3857(z: int, x: int, y: int) -> Tuple[float, float, float, float]:
    size = 2 * _EARTH / (2 ** z)
    return (-_EARTH + x * size, _EARTH - (y + 1) * size, -_EARTH + (x + 1) * size, _EARTH - y * size)


def tile_for(lat: float, lon: float, z: int) -> Tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def _tile_request(defn: Dict[str, Any], secret: str, z: int, x: int, y: int, date: str, cfg: Settings) -> Dict[str, Any]:
    ctx = _context(defn, secret, cfg, {"z": z, "x": x, "y": y, "-y": 2 ** z - 1 - y})
    if date:
        ctx["date"] = date
    req = _build(defn, secret, ctx)
    req["url"] = req["url"].replace("{s}", "a")
    if defn.get("kind") == "wms":
        wms = defn.get("wms") or {}
        minx, miny, maxx, maxy = tile_bbox_3857(z, x, y)
        version = wms.get("version", "1.3.0")
        req["params"].update({
            "SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": version,
            "LAYERS": wms.get("layers", ""), "STYLES": wms.get("styles", ""),
            "FORMAT": wms.get("format") or "image/png", "TRANSPARENT": "TRUE" if wms.get("transparent", True) else "FALSE",
            "WIDTH": "256", "HEIGHT": "256", "CRS" if version == "1.3.0" else "SRS": "EPSG:3857",
            "BBOX": f"{minx},{miny},{maxx},{maxy}",
        })
        if wms.get("time") and date:
            req["params"]["TIME"] = date
    return req


def _looks_like_image(resp: Response) -> bool:
    return resp.content_type.startswith("image/") or resp.body[:8].startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF"))


def fetch_tile(defn: Dict[str, Any], secret: str, z: int, x: int, y: int, date: str = "",
               settings: Optional[Settings] = None, trace: Optional[Dict[str, Any]] = None) -> Tuple[bytes, str]:
    cfg = settings or get_settings()
    if defn.get("kind") not in ("tiles", "wms"):
        raise CustomSourceError("url", "Cette source fournit des objets, pas des tuiles")
    if not (0 <= z <= 22 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise CustomSourceError("url", "Tuile hors limites")
    if date and not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise CustomSourceError("url", "Date invalide (AAAA-MM-JJ)")
    req = _tile_request(defn, secret, z, x, y, date, cfg)
    if trace is not None:
        trace["request"] = _describe_request(defn.get("method", "GET"), req, secret)
    resp = _send(defn.get("method", "GET"), req["url"], params=req["params"], headers=req["headers"], content=req["content"],
                 auth=req["auth"], secret=secret, timeout=float(defn.get("timeout_s") or cfg.http_timeout),
                 max_bytes=MAX_TILE_BYTES, cfg=cfg)
    if trace is not None:
        trace["http"] = {"status": resp.status, "content_type": resp.content_type, "bytes": len(resp.body), "elapsed_ms": resp.elapsed_ms}
    _check_status(resp, secret)
    if not _looks_like_image(resp):
        excerpt = _redact(resp.body[:300].decode("utf-8", "replace"), secret).strip()
        raise CustomSourceError("parse", f"Le service n'a pas renvoyé une image ({resp.content_type or 'type inconnu'}) : {excerpt}")
    content_type = resp.content_type.split(";")[0] if resp.content_type.startswith("image/") else "image/png"
    return resp.body, content_type


def cached_tile(source_id: str, z: int, x: int, y: int, date: str = "", settings: Optional[Settings] = None) -> Tuple[bytes, str]:
    cfg = settings or get_settings()
    defn, secret = get_source(source_id, cfg)
    if not defn.get("enabled"):
        raise KeyError(source_id)
    digest = hashlib.sha256(f"{defn.get('updated_at')}|{z}/{x}/{y}|{date}".encode()).hexdigest()[:32]
    path = cfg.cache_dir / "custom_tiles" / source_id / digest
    ttl = float(defn.get("refresh_minutes") or 15) * 60
    try:
        if path.is_file() and time.time() - path.stat().st_mtime < ttl:
            content_type, _, body = path.read_bytes().partition(b"\n")
            return body, content_type.decode()
    except OSError:
        pass
    try:
        body, content_type = fetch_tile(defn, secret, z, x, y, date, cfg)
    except CustomSourceError as exc:
        health.record(f"custom:{source_id}", False, f"{exc.stage}: {exc}")
        raise
    health.record(f"custom:{source_id}", True)
    try:
        atomic_write(path, content_type.encode() + b"\n" + body)
    except OSError as exc:
        logger.warning("Tile cache write failed: %s", exc)
    return body, content_type


# --------------------------------------------------------------------------- live test


def test_source(defn: Dict[str, Any], secret: str, settings: Optional[Settings] = None, date: str = "") -> Dict[str, Any]:
    """Run the source once and explain the outcome step by step (nothing is cached nor saved)."""
    cfg = settings or get_settings()
    trace: Dict[str, Any] = {"kind": defn.get("kind"), "warnings": []}
    try:
        if defn.get("kind") in ("tiles", "wms"):
            z = min(int((defn.get("tiles") or {}).get("max_native_zoom", 6)), 6)
            x, y = tile_for(*TEST_POINT, z)
            body, content_type = fetch_tile(defn, secret, z, x, y, date, cfg, trace=trace)
            trace["tile"] = {"z": z, "x": x, "y": y, "bytes": len(body), "content_type": content_type}
            if len(body) <= 400_000:
                trace["tile"]["preview"] = f"data:{content_type};base64,{base64.b64encode(body).decode()}"
            if len(body) < 1200:
                trace["warnings"].append("Tuile très légère : probablement vide (date, couche ou zoom sans données ?)")
            message = f"Tuile {z}/{x}/{y} reçue ({len(body) // 1024} Ko, {content_type}) sur la Guyane"
        else:
            collection = fetch_features(defn, secret, cfg, trace=trace)
            west, south, east, north = cfg.eez_bbox
            inside = sum(
                1 for f in collection["features"]
                if f["geometry"]["type"] == "Point"
                and west <= f["geometry"]["coordinates"][0] <= east and south <= f["geometry"]["coordinates"][1] <= north
            )
            trace.update({
                "records": collection["records"], "records_path": collection["records_path"],
                "features": len(collection["features"]), "inside_zone": inside,
                "mapping_used": collection["mapping_used"], "detected_fields": collection["detected_fields"],
                "sample": collection["features"][:5],
            })
            trace["warnings"].extend(collection["warnings"])
            if collection["features"] and not inside and all(f["geometry"]["type"] == "Point" for f in collection["features"]):
                trace["warnings"].append("Aucun point dans l'emprise de la ZEE de Guyane : filtre géographique ({bbox}) à ajouter ?")
            message = (f"{collection['records']} enregistrement(s), {len(collection['features'])} positionné(s)"
                       f"{f', dont {inside} dans la zone' if collection['features'] else ''}")
        return {"ok": True, "stage": "done", "message": message, **trace}
    except CustomSourceError as exc:
        return {"ok": False, "stage": exc.stage, "message": str(exc), **trace}
