"""On-demand optical / thermal check around a point (used by the incident decision support)."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import analyst
from app.core import audit
from app.soc import sensors

router = APIRouter(tags=["sensors"])


class OpticalScan(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    radius_km: float = Field(8, gt=0.5, le=25)
    days: int = Field(10, ge=1, le=30)


@router.post("/sensors/optical/scan")
def optical_scan(body: OpticalScan, user: Dict[str, Any] = Depends(analyst)) -> Dict[str, Any]:
    """Latest Sentinel-2 and Landsat scenes around a point; detections stored for the corroboration."""
    from app.sources import multispectral

    result = multispectral.scan_area(body.lat, body.lon, body.radius_km, body.days, max_scenes=2)
    stored = sensors.save(result["detections"])
    audit.record("sensor.optical_scan", actor=user["username"], target=f"point:{body.lat:.4f},{body.lon:.4f}",
                 details={"radius_km": body.radius_km, "days": body.days, "scenes": len(result["scenes"]),
                          "detections": len(result["detections"])})
    return {**result, "stored": stored}
