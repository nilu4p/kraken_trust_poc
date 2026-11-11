"""AIS collection alert: below 50 % of the last 30 min → #operations, once; recovery above 80 %."""

from __future__ import annotations

import datetime
from typing import List

import pytest

from app.config import get_settings
from app.soc import scheduler
from app.sources import feed_log

NOW = datetime.datetime.now(datetime.timezone.utc).replace(second=0, microsecond=0)


def ago(minutes: float) -> datetime.datetime:
    return NOW - datetime.timedelta(minutes=minutes)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    messages: List[str] = []
    monkeypatch.setattr("app.soc.notify.publish", lambda channel, text, **_kw: messages.append(f"{channel}|{text}") or True)
    monkeypatch.setattr(get_settings(), "aisstream_api_key", "k")
    return messages


def test_no_alert_without_feed_or_history(sent: List[str], monkeypatch: pytest.MonkeyPatch) -> None:
    assert scheduler.watch_ais_collection(now=NOW) is None  # nothing logged yet: collection just started
    feed_log.mark_span("aisstream", ago(10), NOW)
    assert scheduler.watch_ais_collection(now=NOW) is None  # history shorter than the 30-min window
    monkeypatch.setattr(get_settings(), "aisstream_api_key", "")
    assert scheduler.watch_ais_collection(now=NOW) is None and sent == []


def test_degraded_then_recovered(sent: List[str]) -> None:
    feed_log.mark_span("aisstream", ago(120), ago(25))  # collector stopped 25 min ago
    assert scheduler.watch_ais_collection(now=NOW) == "degraded"
    assert sent and sent[0].startswith("operations|Collecte AIS dégradée : 20 %")
    assert scheduler.watch_ais_collection(now=NOW) is None  # no repeat while degraded
    feed_log.mark_span("aisstream", ago(25), NOW)  # back
    assert scheduler.watch_ais_collection(now=NOW) == "recovered"
    assert "Collecte AIS rétablie" in sent[-1]


def test_hysteresis_avoids_flapping(sent: List[str]) -> None:
    feed_log.mark_span("aisstream", ago(120), ago(20))
    assert scheduler.watch_ais_collection(now=NOW) == "degraded"
    feed_log.mark_span("aisstream", ago(12), NOW)  # partially back: 22 of 30 min ≈ 73 % (< 80 %)
    assert scheduler.watch_ais_collection(now=NOW) is None
    assert len(sent) == 1
