"""SAR imaging artefacts that look like ships: azimuth ambiguities and sidelobes.

Two classic false-alarm sources of CFAR ship detection on Sentinel-1 GRD, both
caused by a *real* bright target elsewhere in the scene:

* **Azimuth ambiguities** (ghosts). Aliasing of the Doppler spectrum replicates a
  strong scatterer along the azimuth (flight) direction at
  ``Δx = λ · R · PRF / (2 · V)`` — about 4 to 6 km for the IW sub-swaths
  (λ = 5.55 cm, slant range R ≈ 800-950 km, PRF ≈ 1450-1720 Hz, V ≈ 7.5 km/s) —
  20 to 30 dB weaker than the parent, at the same range (same image column in
  a GRD, whose lines are azimuth times). The ghost and its parent usually fall
  in different 1024-pixel tiles: this check runs on the whole scene.
* **Sidelobes**. The impulse response of a very bright target (large metal
  hull) spreads along the image rows and columns: a cross of weaker echoes
  within a few hundred metres, aligned with the parent.

A weak target is flagged when a much brighter target sits exactly where its
parent would be. The thresholds are deliberately conservative (an ambiguity
must be ≥ 13 dB weaker): a genuine small vessel next to a large one is kept.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

# Azimuth offset of the first-order ambiguity in IW mode (metres), all sub-swaths.
AMBIGUITY_MIN_M = 3500.0
AMBIGUITY_MAX_M = 7500.0
AMBIGUITY_RANGE_TOLERANCE_PX = 6      # same range bin (± a few pixels of ground-range resampling)
AMBIGUITY_POWER_RATIO = 20.0          # parent ≥ 13 dB brighter (ambiguities are 20-30 dB below)
SIDELOBE_MAX_M = 400.0
SIDELOBE_ALIGN_PX = 2
SIDELOBE_POWER_RATIO = 30.0           # parent ≥ 15 dB brighter


def _power(target: Dict[str, Any]) -> float:
    return float(target.get("peak") or 0.0) ** 2  # GRD values are amplitudes: power = amplitude²


def find_parent(target: Dict[str, Any], others: List[Dict[str, Any]], pixel_m: float) -> Optional[Dict[str, Any]]:
    """The bright target that explains ``target`` as an artefact, with the reason, or None."""
    power = _power(target)
    if power <= 0:
        return None
    for other in others:
        if other is target:
            continue
        ratio = _power(other) / power
        d_row = abs(int(other["abs_row"]) - int(target["abs_row"]))
        d_col = abs(int(other["abs_col"]) - int(target["abs_col"]))
        if (ratio >= AMBIGUITY_POWER_RATIO and d_col <= AMBIGUITY_RANGE_TOLERANCE_PX
                and AMBIGUITY_MIN_M <= d_row * pixel_m <= AMBIGUITY_MAX_M):
            return {"reason": "azimuth_ambiguity", "parent": other, "ratio_db": round(10 * math.log10(ratio), 1),
                    "offset_m": round(d_row * pixel_m)}
        aligned = d_row <= SIDELOBE_ALIGN_PX or d_col <= SIDELOBE_ALIGN_PX
        if ratio >= SIDELOBE_POWER_RATIO and aligned and max(d_row, d_col) * pixel_m <= SIDELOBE_MAX_M:
            return {"reason": "sidelobe", "parent": other, "ratio_db": round(10 * math.log10(ratio), 1),
                    "offset_m": round(max(d_row, d_col) * pixel_m)}
    return None


def reject_artifacts(targets: List[Dict[str, Any]], pixel_m: float) -> Dict[str, int]:
    """Remove ambiguities / sidelobes from ``targets`` in place; returns counts per reason.

    Targets need ``abs_row``, ``abs_col`` (scene pixel coordinates) and ``peak``
    (amplitude). The brightest targets are examined first and are never removed
    by a weaker one.
    """
    ordered = sorted(targets, key=_power, reverse=True)
    rejected: Dict[str, int] = {}
    kept: List[Dict[str, Any]] = []
    for target in ordered:
        match = find_parent(target, kept, pixel_m)
        if match:
            rejected[match["reason"]] = rejected.get(match["reason"], 0) + 1
            continue
        kept.append(target)
    survivors = {id(t) for t in kept}
    targets[:] = [t for t in targets if id(t) in survivors]
    return rejected
