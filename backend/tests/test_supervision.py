"""Technical supervision board: local-only snapshot, admin only, API timings recorded."""

from __future__ import annotations

from typing import Any

from app.soc import supervision


def test_supervision_board(client_as: Any) -> None:
    admin, supervisor = client_as("admin"), client_as("supervisor")
    supervisor.get("/api/v1/incidents")  # some traffic to time
    assert supervisor.get("/api/v1/system/supervision").status_code == 403
    board = admin.get("/api/v1/system/supervision").json()
    assert board["database"]["engine"] in ("sqlite", "postgresql") and "ais_positions" in board["database"]["rows"]
    assert {"coverage_30min", "gaps_24h", "rejected"} <= set(board["collection"])
    assert board["rules"]["enabled"] is True and "runs" in board["scans"]
    assert any(t["area"] == "incidents" and t["requests"] >= 1 for t in board["api"])
    assert board["disk"]["free_bytes"] is None or board["disk"]["free_bytes"] > 0


def test_area_grouping() -> None:
    assert supervision.area_of("/api/v1/maritime/vessel/123", "/api/v1") == "maritime"
    assert supervision.area_of("/health", "/api/v1") is None
