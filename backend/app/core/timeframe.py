"""Single reference instant for every layer, query and computation (live or replay).

The whole situation is computed *as of* one instant ``T`` so that the sources
stay coherent with each other: AIS at T, radar targets seen before T, incident
status at T, GFW events before T, ocean and weather of T. ``T = None`` means
live ("now"). A replay instant is limited to the history the platform keeps
(``ais_retention_days``, 90 days by default) and can never be in the future.
"""

from __future__ import annotations

import datetime
from typing import Any, Optional

from fastapi import HTTPException, Query

from app.config import get_settings

LIVE_TOLERANCE = datetime.timedelta(minutes=2)


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def parse_instant(value: Any) -> Optional[datetime.datetime]:
    """ISO 8601 → aware UTC datetime (naive = UTC); None for empty."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        when = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            when = datetime.datetime.fromisoformat(text)
        except ValueError:
            return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return when.astimezone(datetime.timezone.utc)


def resolve(at: Optional[str]) -> Optional[datetime.datetime]:
    """Validated replay instant, or None for live. Raises 422 on a malformed / out-of-range value."""
    if at in (None, ""):
        return None
    when = parse_instant(at)
    if when is None:
        raise HTTPException(status_code=422, detail="Instant invalide (format ISO 8601 attendu)")
    now = utcnow()
    if when >= now - LIVE_TOLERANCE:
        return None  # "now" or a few seconds ago: the live picture
    oldest = now - datetime.timedelta(days=get_settings().ais_retention_days)
    if when < oldest:
        raise HTTPException(
            status_code=422, detail=f"Hors de l'historique conservé ({get_settings().ais_retention_days} jours)"
        )
    return when


def instant(at: Optional[datetime.datetime]) -> datetime.datetime:
    """The reference instant itself (now when live)."""
    return at or utcnow()


def iso(value: datetime.datetime) -> str:
    return value.astimezone(datetime.timezone.utc).isoformat()


def describe(at: Optional[datetime.datetime]) -> dict:
    """Stamp added to every time-aware response: which instant the data describes."""
    return {"mode": "live" if at is None else "replay", "at": iso(instant(at))}


AT_QUERY = Query(
    None,
    max_length=40,
    description="Reference instant (ISO 8601 UTC). Empty = live. Replays are limited to the kept history.",
)
