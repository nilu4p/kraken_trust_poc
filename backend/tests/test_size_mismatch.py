"""An echo far larger than the AIS vessel that would explain it stays suspect."""

from __future__ import annotations

from app.config import get_settings
from app.pipeline.detection import correlate_with_ais, size_mismatch
from app.pipeline.risk import score_vessel
from app.sources import ais_store


def _echo(length: float, pixels: int = 20) -> dict:
    return {"id": "SAR-001", "lat": 5.5, "lon": -52.5, "length_m": length, "pixels": pixels, "confidence": 0.9}


AIS = {"mmsi": "710000001", "name": "PIROGUE", "lat": 5.501, "lon": -52.5, "time_utc": None, "length_m": 15}


def test_thresholds() -> None:
    assert size_mismatch(_echo(120), AIS)["declared_m"] == 15
    assert size_mismatch(_echo(40), AIS) is None             # 2.7× but only 25 m longer: measurement error
    assert size_mismatch(_echo(120, pixels=4), AIS) is None  # tiny blob: size not measurable
    assert size_mismatch(_echo(120), {**AIS, "length_m": None}) is None  # declared length unknown


def test_correlation_keeps_oversized_echo_as_suspect() -> None:
    dark, _ = correlate_with_ais([_echo(120)], get_settings(), ais_tracks=[AIS])
    [suspect] = dark
    assert suspect["size_mismatch"]["mmsi"] == "710000001" and suspect["size_mismatch"]["distance_km"] < 1
    normal, _ = correlate_with_ais([_echo(18)], get_settings(), ais_tracks=[AIS])
    assert normal == []  # consistent size: explained by the AIS as before
    base = score_vessel(_echo(120), zone="eez", events=[], ais_source="aisstream.io (historique)")
    flagged = score_vessel(suspect, zone="eez", events=[], ais_source="aisstream.io (historique)")
    assert flagged["score"] > base["score"] and any("déclaré 15 m" in r for r in flagged["reasons"])


def test_declared_length_from_ais_dimensions_reaches_the_history() -> None:
    def eez(_lon: float, _lat: float) -> str:
        return "eez"

    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    ais_store.store_static("710000001", name="PIROGUE", ship_type=30, length_m=ais_store.dimension_length(10, 5))
    ais_store.store_positions([{"mmsi": "710000001", "ts": now.isoformat(), "lat": 5.5, "lon": -52.5}], zone_of=eez)
    [fix] = ais_store.positions_at(now.isoformat(), 1.0)
    assert fix["length_m"] == 15
    assert ais_store.dimension_length(511, 0) is None and ais_store.dimension_length(0, 0) is None
