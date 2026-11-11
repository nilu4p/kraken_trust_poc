"""Persistence: embedded SQLite or PostgreSQL (+ TimescaleDB), same application code.

- **SQLite** (``DATABASE_URL`` empty): one short-lived connection per unit of work,
  WAL journaling, foreign keys on. Development, tests, single-host deployments.
- **PostgreSQL** (``DATABASE_URL=postgresql://…``): connection pool per process,
  UTC session time zone, advisory locks for read-modify-write sequences, AIS
  positions in a TimescaleDB hypertable (compressed, chunked by day) when the
  extension is installed.

Application SQL uses the portable subset (``?`` placeholders, ``ON CONFLICT``,
``RETURNING``); this module adapts placeholders and ``LIKE`` for PostgreSQL and
returns rows that behave like ``sqlite3.Row`` (index and column-name access).
"""

from __future__ import annotations

import atexit
import datetime
import logging
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_init_lock = threading.Lock()
_initialised: set = set()

MIGRATIONS: List[str] = [
    # v1 — identity, sessions, audit
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        display_name TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL CHECK (role IN ('viewer','analyst','supervisor','admin')),
        password_hash TEXT NOT NULL,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        disabled INTEGER NOT NULL DEFAULT 0,
        failed_logins INTEGER NOT NULL DEFAULT 0,
        locked_until TEXT,
        last_login_at TEXT,
        created_at TEXT NOT NULL,
        password_changed_at TEXT NOT NULL
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        csrf_token TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        ip TEXT,
        user_agent TEXT
    );
    CREATE INDEX idx_sessions_user ON sessions(user_id);
    CREATE TABLE audit_log (
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        target TEXT NOT NULL DEFAULT '',
        outcome TEXT NOT NULL DEFAULT 'success',
        ip TEXT,
        details TEXT NOT NULL DEFAULT '{}',
        prev_hash TEXT NOT NULL,
        hash TEXT NOT NULL
    );
    CREATE INDEX idx_audit_target ON audit_log(target);
    CREATE TABLE app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL
    );
    """,
    # v2 — SOC incident management
    """
    CREATE TABLE incidents (
        id INTEGER PRIMARY KEY,
        ref TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        status TEXT NOT NULL,
        severity TEXT NOT NULL CHECK (severity IN ('P1','P2','P3','P4')),
        risk_score INTEGER,
        quality_score REAL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        zone TEXT,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        triage_due TEXT NOT NULL,
        resolve_due TEXT NOT NULL,
        acknowledged_at TEXT,
        escalated_at TEXT,
        closed_at TEXT,
        resolution TEXT,
        resolution_note TEXT,
        escalation_target TEXT,
        escalation_ref TEXT
    );
    CREATE INDEX idx_incidents_status ON incidents(status);
    CREATE TABLE incident_observations (
        id INTEGER PRIMARY KEY,
        incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        alert_id TEXT NOT NULL,
        vessel_id TEXT NOT NULL,
        dossier TEXT NOT NULL,
        scene_id TEXT,
        seen_at TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        risk_score INTEGER,
        quality_score REAL,
        UNIQUE (alert_id, vessel_id)
    );
    CREATE INDEX idx_obs_incident ON incident_observations(incident_id);
    CREATE TABLE incident_events (
        id INTEGER PRIMARY KEY,
        incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        at TEXT NOT NULL,
        actor TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX idx_events_incident ON incident_events(incident_id);
    CREATE TABLE incident_actions (
        incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        label TEXT NOT NULL,
        position INTEGER NOT NULL,
        done INTEGER NOT NULL DEFAULT 0,
        done_by TEXT,
        done_at TEXT,
        PRIMARY KEY (incident_id, key)
    );
    CREATE TABLE processed_scenes (
        scene_id TEXT PRIMARY KEY,
        processed_at TEXT NOT NULL,
        outcome TEXT NOT NULL
    );
    """,
    # v3 — vessel watchlist (Combined IUU list, manual entries)
    """
    CREATE TABLE watchlist (
        id INTEGER PRIMARY KEY,
        source TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        name_key TEXT NOT NULL DEFAULT '',
        mmsi TEXT,
        imo TEXT,
        call_sign TEXT,
        flag TEXT,
        reason TEXT NOT NULL DEFAULT '',
        added_at TEXT NOT NULL,
        added_by TEXT NOT NULL
    );
    CREATE INDEX idx_watchlist_mmsi ON watchlist(mmsi);
    CREATE INDEX idx_watchlist_imo ON watchlist(imo);
    CREATE INDEX idx_watchlist_name ON watchlist(name_key);
    ALTER TABLE incidents ADD COLUMN kind TEXT NOT NULL DEFAULT 'dark_vessel';
    """,
    # v4 — continuous AIS picture (positions history, vessel identity) for 24/7 rules
    """
    CREATE TABLE ais_vessels (
        mmsi TEXT PRIMARY KEY,
        name TEXT NOT NULL DEFAULT '',
        imo TEXT,
        call_sign TEXT,
        ship_type INTEGER,
        flag TEXT,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        last_lat REAL,
        last_lon REAL,
        last_sog REAL,
        last_zone TEXT,
        gfw_geartype TEXT,
        gfw_flag TEXT,
        gfw_checked_at TEXT
    );
    CREATE INDEX idx_ais_vessels_last ON ais_vessels(last_seen);
    CREATE TABLE ais_positions (
        mmsi TEXT NOT NULL,
        ts TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        sog REAL,
        cog REAL,
        heading REAL,
        nav_status INTEGER,
        PRIMARY KEY (mmsi, ts)
    );
    CREATE INDEX idx_ais_positions_ts ON ais_positions(ts);
    CREATE TABLE rule_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    # v5 — watchlist aliases (RFMO names) and listing details (gear, RFMO listings…)
    """
    ALTER TABLE watchlist ADD COLUMN alias_keys TEXT NOT NULL DEFAULT '';
    ALTER TABLE watchlist ADD COLUMN details TEXT NOT NULL DEFAULT '{}';
    CREATE INDEX idx_watchlist_callsign ON watchlist(call_sign);
    """,
    # v6 — multi-source fusion: feed of each position (aisstream, aishub, vms), fused vessel intelligence
    """
    ALTER TABLE ais_positions ADD COLUMN source TEXT NOT NULL DEFAULT 'aisstream';
    ALTER TABLE ais_vessels ADD COLUMN last_source TEXT NOT NULL DEFAULT 'aisstream';
    ALTER TABLE ais_vessels ADD COLUMN intel TEXT NOT NULL DEFAULT '{}';
    ALTER TABLE ais_vessels ADD COLUMN intel_checked_at TEXT;
    CREATE INDEX idx_ais_positions_source ON ais_positions(source, ts);
    """,
    # v7 — vessels authorised to fish in the EEZ (licences): excluded from the foreign-fishing
    # rule and from the risk context, matched on hard identifiers only (MMSI / IMO / call sign).
    """
    CREATE TABLE authorized_vessels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL DEFAULT '',
        mmsi TEXT,
        imo TEXT,
        call_sign TEXT,
        flag TEXT,
        licence TEXT NOT NULL DEFAULT '',
        valid_until TEXT,
        note TEXT NOT NULL DEFAULT '',
        added_at TEXT NOT NULL,
        added_by TEXT NOT NULL
    );
    CREATE INDEX idx_authorized_mmsi ON authorized_vessels(mmsi);
    CREATE INDEX idx_authorized_imo ON authorized_vessels(imo);
    """,
    # v8 — collection log: minutes during which each feed was up (and how many messages it
    # received), so that "no vessel" and "no data" are never confused, live or in replay.
    """
    CREATE TABLE feed_minutes (
        feed TEXT NOT NULL,
        minute TEXT NOT NULL,
        messages INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (feed, minute)
    );
    CREATE INDEX idx_feed_minutes_minute ON feed_minutes(minute);
    """,
    # v9 — one timestamp format: aisstream fixes were stored as "2026-10-20 10:00:00.1 +0000 UTC",
    # which sorts before same-day ISO bounds ("…T…") in every time-window query.
    """
    UPDATE OR IGNORE ais_positions SET ts = substr(ts, 1, 10) || 'T' || substr(ts, 12, 8) || '+00:00'
        WHERE ts LIKE '____-__-__ __:__:__%';
    DELETE FROM ais_positions WHERE ts LIKE '____-__-__ __:__:__%';
    UPDATE ais_vessels SET last_seen = substr(last_seen, 1, 10) || 'T' || substr(last_seen, 12, 8) || '+00:00'
        WHERE last_seen LIKE '____-__-__ __:__:__%';
    UPDATE ais_vessels SET first_seen = substr(first_seen, 1, 10) || 'T' || substr(first_seen, 12, 8) || '+00:00'
        WHERE first_seen LIKE '____-__-__ __:__:__%';
    """,
    # v10 — custom detection rules built from the dashboard (declarative conditions, no code).
    """
    CREATE TABLE custom_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        definition TEXT NOT NULL,
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL
    );
    """,
    # v11 — indexes for the chronological incident list, the entity graph and the timeline.
    """
    CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_at);
    CREATE INDEX IF NOT EXISTS idx_obs_vessel ON incident_observations(vessel_id, seen_at);
    CREATE INDEX IF NOT EXISTS idx_obs_seen ON incident_observations(seen_at);
    CREATE INDEX IF NOT EXISTS idx_events_kind ON incident_events(kind, at);
    """,
    # v12 — cache of the activity timeline for periods that can no longer change.
    """
    CREATE TABLE timeline_cache (
        bucket TEXT NOT NULL,
        key TEXT NOT NULL,
        data TEXT NOT NULL,
        computed_at TEXT NOT NULL,
        PRIMARY KEY (bucket, key)
    );
    """,
    # v13 — declared vessel length (AIS static dimensions A+B, or VesselAPI registry): a radar
    # echo far larger than the AIS vessel that "explains" it stays suspect.
    """
    ALTER TABLE ais_vessels ADD COLUMN length_m REAL;
    """,
    # v14 — surveillance zones drawn on the map (polygons), usable in custom rules.
    """
    CREATE TABLE surveillance_zones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        color TEXT NOT NULL DEFAULT '#22d3ee',
        geometry TEXT NOT NULL,
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL
    );
    """,
    # v15 — decision support and reporting: intervention means (ETA / interception) and the
    # register of signed reports issued to the chain of command (incident reports, SITREP).
    """
    CREATE TABLE intervention_assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        base_name TEXT NOT NULL DEFAULT '',
        base_lat REAL NOT NULL,
        base_lon REAL NOT NULL,
        speed_kn REAL NOT NULL,
        endurance_nm REAL NOT NULL,
        readiness_min INTEGER NOT NULL DEFAULT 60,
        can_board INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'available',
        notes TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL
    );
    CREATE TABLE reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ref TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL,
        incident_id INTEGER REFERENCES incidents(id),
        period_start TEXT,
        period_end TEXT,
        title TEXT NOT NULL,
        classification TEXT NOT NULL,
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL,
        content TEXT NOT NULL,
        sections TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        signature TEXT NOT NULL DEFAULT '',
        cert_fingerprint TEXT NOT NULL DEFAULT '',
        certificate TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_reports_incident ON reports(incident_id);
    CREATE INDEX idx_reports_created ON reports(created_at);
    """,
    # v16 — multi-sensor detection (Sentinel-2 / Landsat optical and thermal, VIIRS night lights)
    # and intelligence reports (HUMINT, OSINT…) feeding the all-source fusion.
    """
    CREATE TABLE sensor_detections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT NOT NULL UNIQUE,
        sensor TEXT NOT NULL,
        platform TEXT NOT NULL DEFAULT '',
        scene_id TEXT NOT NULL,
        acquired_at TEXT NOT NULL,
        time_uncertainty_min INTEGER NOT NULL DEFAULT 0,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        length_m REAL,
        confidence REAL NOT NULL,
        attributes TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_sensor_det_time ON sensor_detections(acquired_at);
    CREATE TABLE sensor_scenes (
        sensor TEXT NOT NULL,
        scene_id TEXT NOT NULL,
        processed_at TEXT NOT NULL,
        outcome TEXT NOT NULL,
        detections INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (sensor, scene_id)
    );
    CREATE TABLE intel_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        discipline TEXT NOT NULL,
        source_name TEXT NOT NULL DEFAULT '',
        reliability TEXT NOT NULL,
        credibility INTEGER NOT NULL,
        observed_at TEXT NOT NULL,
        lat REAL,
        lon REAL,
        radius_km REAL NOT NULL DEFAULT 1,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        vessel_name TEXT NOT NULL DEFAULT '',
        mmsi TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        incident_id INTEGER REFERENCES incidents(id),
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL
    );
    CREATE INDEX idx_intel_observed ON intel_reports(observed_at);
    """,
    # v17 — intelligence reports withdrawn (the corroboration relies on sensors only).
    """
    DROP TABLE IF EXISTS intel_reports;
    """,
]


# --------------------------------------------------------------------------- PostgreSQL schema
# Same logical schema as the SQLite migrations above (v1-v7 consolidated). Timestamps stay
# ISO-8601 TEXT like in SQLite, except ``ais_positions.ts`` (TIMESTAMPTZ: TimescaleDB
# partitions on it); rows are converted back to ISO strings so callers see no difference.
PG_MIGRATIONS: List[str] = [
    """
    CREATE TABLE users (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        username TEXT NOT NULL,
        display_name TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL CHECK (role IN ('viewer','analyst','supervisor','admin')),
        password_hash TEXT NOT NULL,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        disabled INTEGER NOT NULL DEFAULT 0,
        failed_logins INTEGER NOT NULL DEFAULT 0,
        locked_until TEXT,
        last_login_at TEXT,
        created_at TEXT NOT NULL,
        password_changed_at TEXT NOT NULL
    );
    CREATE UNIQUE INDEX idx_users_username ON users (lower(username));
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        csrf_token TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        ip TEXT,
        user_agent TEXT
    );
    CREATE INDEX idx_sessions_user ON sessions(user_id);
    CREATE TABLE audit_log (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        at TEXT NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        target TEXT NOT NULL DEFAULT '',
        outcome TEXT NOT NULL DEFAULT 'success',
        ip TEXT,
        details TEXT NOT NULL DEFAULT '{}',
        prev_hash TEXT NOT NULL,
        hash TEXT NOT NULL
    );
    CREATE INDEX idx_audit_target ON audit_log(target);
    CREATE TABLE app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL
    );
    CREATE TABLE incidents (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        ref TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        status TEXT NOT NULL,
        severity TEXT NOT NULL CHECK (severity IN ('P1','P2','P3','P4')),
        risk_score INTEGER,
        quality_score DOUBLE PRECISION,
        lat DOUBLE PRECISION NOT NULL,
        lon DOUBLE PRECISION NOT NULL,
        zone TEXT,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        assignee_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
        triage_due TEXT NOT NULL,
        resolve_due TEXT NOT NULL,
        acknowledged_at TEXT,
        escalated_at TEXT,
        closed_at TEXT,
        resolution TEXT,
        resolution_note TEXT,
        escalation_target TEXT,
        escalation_ref TEXT,
        kind TEXT NOT NULL DEFAULT 'dark_vessel'
    );
    CREATE INDEX idx_incidents_status ON incidents(status);
    CREATE TABLE incident_observations (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        alert_id TEXT NOT NULL,
        vessel_id TEXT NOT NULL,
        dossier TEXT NOT NULL,
        scene_id TEXT,
        seen_at TEXT NOT NULL,
        lat DOUBLE PRECISION NOT NULL,
        lon DOUBLE PRECISION NOT NULL,
        risk_score INTEGER,
        quality_score DOUBLE PRECISION,
        UNIQUE (alert_id, vessel_id)
    );
    CREATE INDEX idx_obs_incident ON incident_observations(incident_id);
    CREATE INDEX idx_obs_scene ON incident_observations(scene_id);
    CREATE TABLE incident_events (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        at TEXT NOT NULL,
        actor TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX idx_events_incident ON incident_events(incident_id);
    CREATE TABLE incident_actions (
        incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        label TEXT NOT NULL,
        position INTEGER NOT NULL,
        done INTEGER NOT NULL DEFAULT 0,
        done_by TEXT,
        done_at TEXT,
        PRIMARY KEY (incident_id, key)
    );
    CREATE TABLE processed_scenes (
        scene_id TEXT PRIMARY KEY,
        processed_at TEXT NOT NULL,
        outcome TEXT NOT NULL
    );
    CREATE TABLE watchlist (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        source TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        name_key TEXT NOT NULL DEFAULT '',
        mmsi TEXT,
        imo TEXT,
        call_sign TEXT,
        flag TEXT,
        reason TEXT NOT NULL DEFAULT '',
        added_at TEXT NOT NULL,
        added_by TEXT NOT NULL,
        alias_keys TEXT NOT NULL DEFAULT '',
        details TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX idx_watchlist_mmsi ON watchlist(mmsi);
    CREATE INDEX idx_watchlist_imo ON watchlist(imo);
    CREATE INDEX idx_watchlist_name ON watchlist(name_key);
    CREATE INDEX idx_watchlist_callsign ON watchlist(call_sign);
    CREATE TABLE ais_vessels (
        mmsi TEXT PRIMARY KEY,
        name TEXT NOT NULL DEFAULT '',
        imo TEXT,
        call_sign TEXT,
        ship_type INTEGER,
        flag TEXT,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        last_lat DOUBLE PRECISION,
        last_lon DOUBLE PRECISION,
        last_sog DOUBLE PRECISION,
        last_zone TEXT,
        gfw_geartype TEXT,
        gfw_flag TEXT,
        gfw_checked_at TEXT,
        last_source TEXT NOT NULL DEFAULT 'aisstream',
        intel TEXT NOT NULL DEFAULT '{}',
        intel_checked_at TEXT
    );
    CREATE INDEX idx_ais_vessels_last ON ais_vessels(last_seen);
    CREATE TABLE ais_positions (
        mmsi TEXT NOT NULL,
        ts TIMESTAMPTZ NOT NULL,
        lat DOUBLE PRECISION NOT NULL,
        lon DOUBLE PRECISION NOT NULL,
        sog DOUBLE PRECISION,
        cog DOUBLE PRECISION,
        heading DOUBLE PRECISION,
        nav_status INTEGER,
        source TEXT NOT NULL DEFAULT 'aisstream',
        PRIMARY KEY (mmsi, ts)
    );
    CREATE INDEX idx_ais_positions_ts ON ais_positions(ts);
    CREATE INDEX idx_ais_positions_source ON ais_positions(source, ts);
    CREATE TABLE rule_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE authorized_vessels (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        name TEXT NOT NULL DEFAULT '',
        mmsi TEXT,
        imo TEXT,
        call_sign TEXT,
        flag TEXT,
        licence TEXT NOT NULL DEFAULT '',
        valid_until TEXT,
        note TEXT NOT NULL DEFAULT '',
        added_at TEXT NOT NULL,
        added_by TEXT NOT NULL
    );
    CREATE INDEX idx_authorized_mmsi ON authorized_vessels(mmsi);
    CREATE INDEX idx_authorized_imo ON authorized_vessels(imo);
    """,
    # v8 — collection log: minutes during which each feed was up (and how many messages it
    # received), so that "no vessel" and "no data" are never confused, live or in replay.
    """
    CREATE TABLE feed_minutes (
        feed TEXT NOT NULL,
        minute TEXT NOT NULL,
        messages INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (feed, minute)
    );
    CREATE INDEX idx_feed_minutes_minute ON feed_minutes(minute);
    """,
    # v3 — ISO timestamps for the vessel rows (ais_positions.ts is TIMESTAMPTZ already).
    """
    UPDATE ais_vessels SET last_seen = substr(last_seen, 1, 10) || 'T' || substr(last_seen, 12, 8) || '+00:00'
        WHERE last_seen LIKE '____-__-__ __:__:__%';
    UPDATE ais_vessels SET first_seen = substr(first_seen, 1, 10) || 'T' || substr(first_seen, 12, 8) || '+00:00'
        WHERE first_seen LIKE '____-__-__ __:__:__%';
    """,
    # v4 — custom detection rules built from the dashboard (declarative conditions, no code).
    """
    CREATE TABLE custom_rules (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        definition TEXT NOT NULL,
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL
    );
    """,
    # v5 — indexes for the chronological incident list, the entity graph and the timeline.
    """
    CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_at);
    CREATE INDEX IF NOT EXISTS idx_obs_vessel ON incident_observations(vessel_id, seen_at);
    CREATE INDEX IF NOT EXISTS idx_obs_seen ON incident_observations(seen_at);
    CREATE INDEX IF NOT EXISTS idx_events_kind ON incident_events(kind, at);
    """,
    # v6 — cache of the activity timeline for periods that can no longer change.
    """
    CREATE TABLE timeline_cache (
        bucket TEXT NOT NULL,
        key TEXT NOT NULL,
        data TEXT NOT NULL,
        computed_at TEXT NOT NULL,
        PRIMARY KEY (bucket, key)
    );
    """,
    # v7 — declared vessel length (AIS static dimensions A+B, or VesselAPI registry): a radar
    # echo far larger than the AIS vessel that "explains" it stays suspect.
    """
    ALTER TABLE ais_vessels ADD COLUMN length_m REAL;
    """,
    # v8 — surveillance zones drawn on the map (polygons), usable in custom rules.
    """
    CREATE TABLE surveillance_zones (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        color TEXT NOT NULL DEFAULT '#22d3ee',
        geometry TEXT NOT NULL,
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL
    );
    """,
    # v9 — decision support and reporting (intervention means, signed reports register).
    """
    CREATE TABLE intervention_assets (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        base_name TEXT NOT NULL DEFAULT '',
        base_lat REAL NOT NULL,
        base_lon REAL NOT NULL,
        speed_kn REAL NOT NULL,
        endurance_nm REAL NOT NULL,
        readiness_min INTEGER NOT NULL DEFAULT 60,
        can_board INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'available',
        notes TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL
    );
    CREATE TABLE reports (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        ref TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL,
        incident_id BIGINT REFERENCES incidents(id),
        period_start TEXT,
        period_end TEXT,
        title TEXT NOT NULL,
        classification TEXT NOT NULL,
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL,
        content TEXT NOT NULL,
        sections TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        signature TEXT NOT NULL DEFAULT '',
        cert_fingerprint TEXT NOT NULL DEFAULT '',
        certificate TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_reports_incident ON reports(incident_id);
    CREATE INDEX idx_reports_created ON reports(created_at);
    """,
    # v10 — multi-sensor detection and intelligence reports (all-source fusion).
    """
    CREATE TABLE sensor_detections (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        key TEXT NOT NULL UNIQUE,
        sensor TEXT NOT NULL,
        platform TEXT NOT NULL DEFAULT '',
        scene_id TEXT NOT NULL,
        acquired_at TEXT NOT NULL,
        time_uncertainty_min INTEGER NOT NULL DEFAULT 0,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        length_m REAL,
        confidence REAL NOT NULL,
        attributes TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_sensor_det_time ON sensor_detections(acquired_at);
    CREATE TABLE sensor_scenes (
        sensor TEXT NOT NULL,
        scene_id TEXT NOT NULL,
        processed_at TEXT NOT NULL,
        outcome TEXT NOT NULL,
        detections INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (sensor, scene_id)
    );
    CREATE TABLE intel_reports (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        discipline TEXT NOT NULL,
        source_name TEXT NOT NULL DEFAULT '',
        reliability TEXT NOT NULL,
        credibility INTEGER NOT NULL,
        observed_at TEXT NOT NULL,
        lat REAL,
        lon REAL,
        radius_km REAL NOT NULL DEFAULT 1,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        vessel_name TEXT NOT NULL DEFAULT '',
        mmsi TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        incident_id BIGINT REFERENCES incidents(id),
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL
    );
    CREATE INDEX idx_intel_observed ON intel_reports(observed_at);
    """,
    # v11 — intelligence reports withdrawn (the corroboration relies on sensors only).
    """
    DROP TABLE IF EXISTS intel_reports;
    """,
]

# Tables in foreign-key order (copy / dump / restore / wipe).
TABLES = (
    "users", "sessions", "audit_log", "app_settings", "incidents", "incident_observations", "incident_events",
    "incident_actions", "processed_scenes", "watchlist", "ais_vessels", "ais_positions", "rule_state",
    "authorized_vessels", "feed_minutes", "custom_rules", "timeline_cache", "surveillance_zones",
    "intervention_assets", "reports", "sensor_detections", "sensor_scenes",
)
IDENTITY_TABLES = ("users", "audit_log", "incidents", "incident_observations", "incident_events", "watchlist",
                   "authorized_vessels", "custom_rules", "surveillance_zones",
                   "intervention_assets", "reports", "sensor_detections")


def is_postgres(settings: Optional[Settings] = None) -> bool:
    return (settings or get_settings()).database_url.startswith(("postgres://", "postgresql://"))


def database_dsn(cfg: Settings) -> str:
    """DATABASE_URL with the password from DATABASE_PASSWORD_FILE (docker secret) when given."""
    url = cfg.database_url
    path = cfg.database_password_file
    if path and os.path.isfile(path):
        from urllib.parse import quote, urlsplit, urlunsplit

        password = Path(path).read_text(encoding="utf-8").strip()
        parts = urlsplit(url)
        user = parts.username or ""
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
        url = urlunsplit((parts.scheme, f"{quote(user, safe='')}:{quote(password, safe='')}@{host}{port}", parts.path, parts.query, parts.fragment))
    return url


# --------------------------------------------------------------------------- rows & SQL adaptation


class Row(tuple):
    """Tuple with column-name access, ``keys()`` and ``dict(row)`` support (like sqlite3.Row)."""

    def __new__(cls, names: Sequence[str], values: Sequence[Any]) -> "Row":
        obj = super().__new__(cls, values)
        obj._names = tuple(names)
        return obj

    def __getitem__(self, key: Any) -> Any:  # type: ignore[override]
        if isinstance(key, str):
            return tuple.__getitem__(self, self._names.index(key))
        return tuple.__getitem__(self, key)

    def keys(self) -> List[str]:
        return list(self._names)


def _plain(value: Any) -> Any:
    """TIMESTAMPTZ values come back as the ISO strings the application stores elsewhere."""
    if isinstance(value, datetime.datetime):
        return value.astimezone(datetime.timezone.utc).isoformat()
    return value


def _row_factory(cursor: Any) -> Any:
    names = [column.name for column in (cursor.description or [])]

    def make(values: Sequence[Any]) -> Row:
        return Row(names, [_plain(v) for v in values])

    return make


_PLACEHOLDER = re.compile(r"'[^']*'|\?")
_LIKE = re.compile(r"\bLIKE\b")


@lru_cache(maxsize=512)
def to_postgres(sql: str) -> str:
    """``?`` placeholders -> ``%s`` (outside string literals), ``%`` escaped, LIKE -> ILIKE."""
    sql = sql.replace("%", "%%")
    sql = _PLACEHOLDER.sub(lambda m: "%s" if m.group(0) == "?" else m.group(0), sql)
    return _LIKE.sub("ILIKE", sql)


class _Result:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def fetchone(self) -> Optional[Row]:
        return self._cursor.fetchone() if self._cursor.description else None

    def fetchall(self) -> List[Row]:
        return self._cursor.fetchall() if self._cursor.description else []

    def __iter__(self) -> Iterator[Row]:
        return iter(self.fetchall())

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount


class PgConnection:
    """sqlite3-like facade over a psycopg connection (execute / executemany / rows)."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Sequence[Any] = ()) -> _Result:
        cursor = self._conn.cursor(row_factory=_row_factory)
        if isinstance(params, dict):
            cursor.execute(re.sub(r":(\w+)", r"%(\1)s", sql.replace("%", "%%")), params)
        else:
            cursor.execute(to_postgres(sql), tuple(params) if params else None)
        return _Result(cursor)

    def executemany(self, sql: str, seq: Sequence[Sequence[Any]]) -> None:
        with self._conn.cursor() as cursor:
            cursor.executemany(to_postgres(sql), [tuple(p) for p in seq])

    @property
    def raw(self) -> Any:
        return self._conn


# --------------------------------------------------------------------------- PostgreSQL pool

_pools: Dict[Any, Any] = {}
_pools_lock = threading.Lock()
_pg_initialised: set = set()


def _configure(conn: Any) -> None:
    conn.execute("SET TIME ZONE 'UTC'")
    conn.commit()


def _pool(cfg: Settings) -> Any:
    from psycopg_pool import ConnectionPool

    key = (os.getpid(), cfg.database_url)  # one pool per process (Celery prefork safe)
    with _pools_lock:
        pool = _pools.get(key)
        if pool is None:
            pool = ConnectionPool(
                database_dsn(cfg), min_size=1, max_size=max(2, cfg.database_pool_size),
                configure=_configure, open=True, timeout=30, name=f"kraken-{os.getpid()}",
            )
            _pools[key] = pool
    return pool


def close_pools() -> None:
    with _pools_lock:
        for pool in _pools.values():
            pool.close()
        _pools.clear()
    _pg_initialised.clear()


atexit.register(close_pools)


def timescale_enabled(conn: Any) -> bool:
    return bool(conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'").fetchone())


def _init_postgres(cfg: Settings) -> None:
    pool = _pool(cfg)
    with pool.connection() as conn:
        conn.execute("SELECT pg_advisory_lock(7262001)")  # one process migrates at a time
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            current = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
            for index, script in enumerate(PG_MIGRATIONS[current:], start=current + 1):
                with conn.transaction():
                    conn.execute(script)
                    if index == 1:
                        _enable_timescale(conn)
                    conn.execute("INSERT INTO schema_version (version) VALUES (%s)", (index,))
                logger.info("PostgreSQL schema migrated to v%s", index)
            conn.commit()
        finally:
            conn.execute("SELECT pg_advisory_unlock(7262001)")
            conn.commit()


def _enable_timescale(conn: Any) -> None:
    """Hypertable + compression for AIS positions when TimescaleDB is installed (optional)."""
    try:
        with conn.transaction():
            conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    except Exception as exc:
        logger.warning("TimescaleDB not available (%s): AIS positions stay in a plain table", type(exc).__name__)
        return
    with conn.transaction():
        conn.execute("SELECT create_hypertable('ais_positions', 'ts', chunk_time_interval => INTERVAL '1 day')")
        conn.execute("ALTER TABLE ais_positions SET (timescaledb.compress, timescaledb.compress_segmentby = 'mmsi')")
        conn.execute("SELECT add_compression_policy('ais_positions', INTERVAL '2 days')")
    logger.info("TimescaleDB enabled: ais_positions is a compressed hypertable")


# --------------------------------------------------------------------------- SQLite


def _db_path(cfg: Settings) -> Path:
    return Path(cfg.data_dir) / "kraken.db"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def init_db(settings: Optional[Settings] = None) -> Any:
    """Create / migrate the schema of the configured database."""
    cfg = settings or get_settings()
    if is_postgres(cfg):
        with _init_lock:
            _init_postgres(cfg)
            _pg_initialised.add(cfg.database_url)
        return cfg.database_url
    path = _db_path(cfg)
    with _init_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch(mode=0o600)
        conn = _connect(path)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            for index, script in enumerate(MIGRATIONS[version:], start=version + 1):
                conn.executescript(f"BEGIN; {script} PRAGMA user_version = {index}; COMMIT;")
        finally:
            conn.close()
        _initialised.add(str(path))
    return path


@contextmanager
def _pg_transaction(cfg: Settings, read_only: bool) -> Iterator[PgConnection]:
    if cfg.database_url not in _pg_initialised:
        init_db(cfg)
    with _pool(cfg).connection() as conn:
        with conn.transaction():
            if read_only:
                conn.execute("SET TRANSACTION READ ONLY")
            yield PgConnection(conn)


@contextmanager
def db(settings: Optional[Settings] = None) -> Iterator[Any]:
    """Write transaction: commits on success, rolls back on error."""
    cfg = settings or get_settings()
    if is_postgres(cfg):
        with _pg_transaction(cfg, read_only=False) as conn:
            yield conn
        return
    path = _db_path(cfg)
    if str(path) not in _initialised:
        init_db(cfg)
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


@contextmanager
def read_db(settings: Optional[Settings] = None) -> Iterator[Any]:
    """Read-only snapshot: never takes a write lock, so reads never queue behind writers.

    Any accidental write fails loudly (``query_only`` / ``READ ONLY`` transaction).
    """
    cfg = settings or get_settings()
    if is_postgres(cfg):
        with _pg_transaction(cfg, read_only=True) as conn:
            yield conn
        return
    path = _db_path(cfg)
    if str(path) not in _initialised:
        init_db(cfg)
    conn = _connect(path)
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def lock(conn: Any, name: str) -> None:
    """Serialise a read-modify-write sequence across processes for the rest of the transaction.

    SQLite write transactions (BEGIN IMMEDIATE) are already exclusive; PostgreSQL takes a
    transaction-scoped advisory lock (e.g. the audit hash chain must never fork).
    """
    if isinstance(conn, PgConnection):
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (name,))


def row(r: Any) -> Optional[Dict[str, Any]]:
    return dict(r) if r is not None else None


def rows(rs: Sequence[Any]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rs]
