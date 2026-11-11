"""xView3 opinion: calibrated crops, service call, sealing — and no effect whatsoever on decisions."""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

import numpy as np
import pytest

from app.config import get_settings
from app.pipeline import detection, quality, risk
from app.sources import xview3


class _FakeService(BaseHTTPRequestHandler):
    received: List[Dict[str, Any]] = []
    status = 200

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).received.extend(body["chips"])
        payload = {"results": [{"id": c["id"], "objectness": 0.81, "vessel": 0.9, "fishing": 0.42, "length_m": 37.0,
                                "offset_px": [0, 0]} for c in body["chips"]]}
        data = json.dumps(payload if type(self).status == 200 else {"error": "loading"}).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture()
def service():
    _FakeService.received, _FakeService.status = [], 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeService)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield get_settings().model_copy(update={"xview3_url": f"http://127.0.0.1:{server.server_port}"})
    server.shutdown()


def _tile(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    tile = rng.rayleigh(40.0, (400, 400)).astype(np.float32)
    tile[200:208, 150:153] = 2000.0
    return tile


def test_calibration_aligns_the_sea_level() -> None:
    tile = _tile()
    vh, vv = xview3.to_db(tile, "vh"), xview3.to_db(tile * 3, "vv")
    assert abs(float(np.nanmedian(vh)) - xview3.SEA_DB["vh"]) < 0.01
    assert abs(float(np.nanmedian(vv)) - xview3.SEA_DB["vv"]) < 0.01
    assert vh[204, 151] - float(np.nanmedian(vh)) > 25  # the ship keeps its contrast
    assert np.isnan(xview3.to_db(np.zeros((4, 4)), "vh")).all()  # no-data pixels stay unknown


def test_crop_near_the_edge_keeps_the_target_position() -> None:
    piece = xview3.crop(np.arange(300 * 300).reshape(300, 300), 10, 290, 256)
    assert piece["data"].shape == (256, 256)
    assert piece["data"][piece["row"], piece["col"]] == 10 * 300 + 290


def test_opinion_is_attached_and_crops_are_dropped(service) -> None:
    targets = [{"row": 204, "col": 151, "snr": 30.0}, {"row": 50, "col": 60, "snr": 8.0}]
    xview3.attach_crops(targets, _tile(), _tile(1), service)
    assert xview3.score(targets, service) == 2
    assert all("_xview3_chip" not in t for t in targets)
    assert targets[0]["xview3"] == {"objectness": 0.81, "vessel": 0.9, "fishing": 0.42, "length_m": 37.0}
    sent = _FakeService.received[0]
    assert sent["shape"] == [256, 256] and (sent["row"], sent["col"]) == (128, 128)
    vh = np.frombuffer(base64.b64decode(sent["vh"]), dtype=np.float16).reshape(256, 256)
    assert abs(float(np.median(vh)) - xview3.SEA_DB["vh"]) < 0.5
    assert "navire 90 %" in xview3.summary(targets[0]["xview3"])


def test_no_copol_band_means_no_opinion(service) -> None:
    targets = [{"row": 204, "col": 151, "snr": 30.0}]
    xview3.attach_crops(targets, _tile(), None, service)
    assert xview3.score(targets, service) == 0 and "xview3" not in targets[0]


def test_service_down_never_breaks_the_scan(service, monkeypatch) -> None:
    _FakeService.status = 503
    targets = [{"row": 204, "col": 151, "snr": 30.0}]
    xview3.attach_crops(targets, _tile(), _tile(1), service)
    with pytest.raises(xview3.Xview3Unavailable):
        xview3.score(targets, service)
    assert "_xview3_chip" not in targets[0]

    stats: Dict[str, Any] = {}
    monkeypatch.setattr("app.pipeline.scan_state.add_stats", lambda **kw: stats.update(kw))
    xview3.attach_crops(targets, _tile(), _tile(1), service)
    detection._xview3_opinion(targets, service, {})  # must not raise
    assert stats["xview3_scored"] == 0 and "chargement" in stats["xview3_error"]
    assert "xview3" not in targets[0] and "_xview3_chip" not in targets[0]


def test_unreachable_service() -> None:
    cfg = get_settings().model_copy(update={"xview3_url": "http://127.0.0.1:9"})
    targets = [{"row": 204, "col": 151, "snr": 30.0}]
    xview3.attach_crops(targets, _tile(), _tile(1), cfg)
    with pytest.raises(xview3.Xview3Unavailable):
        xview3.score(targets, cfg)
    assert xview3.status(cfg)["ready"] is False
    assert xview3.status(get_settings().model_copy(update={"xview3_url": ""})) == {"enabled": False}


@pytest.mark.parametrize("opinion", [
    {"objectness": 0.01, "vessel": 0.02, "fishing": 0.0, "length_m": 5.0},
    {"objectness": 0.99, "vessel": 0.99, "fishing": 0.99, "length_m": 200.0},
])
def test_information_only_no_effect_on_quality_or_risk(opinion) -> None:
    det = {"id": "SAR-001", "lat": 5.5, "lon": -52.5, "length_m": 40.0, "pixels": 20, "snr": 12.0,
           "confidence": 0.8, "elongation": 3.0, "dual_pol": True}
    base_q = quality.assess(dict(det))
    base_r = risk.score_vessel(dict(det), zone="eez", events=[], ais_source="aisstream.io (historique)")
    with_q = quality.assess({**det, "xview3": opinion})
    with_r = risk.score_vessel({**det, "xview3": opinion}, zone="eez", events=[], ais_source="aisstream.io (historique)")
    assert with_q == base_q and with_r == base_r


def test_agreement_with_operators_uses_objectness() -> None:
    from app.pipeline.ai_detector import evaluation

    def feature(objectness: float, resolution: str) -> Dict[str, Any]:
        return {"properties": {"xview3": {"objectness": objectness}, "incident_status": "closed",
                               "incident_resolution": resolution}}

    result = evaluation([feature(0.9, "confirmed_iuu"), feature(0.1, "false_positive"), feature(0.8, "false_positive"),
                         {"properties": {"incident_status": "closed", "incident_resolution": "confirmed_iuu"}}],
                        xview3.objectness_of)
    assert (result["tp"], result["tn"], result["fp"], result["judged"]) == (1, 1, 1, 3)
