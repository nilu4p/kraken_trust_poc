"""The knowledge base (docs/content) must follow the code: settings, incident kinds, channels.

Skipped where the docs folder is absent (backend container image).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.soc.notify import CHANNELS
from app.soc.runtime import EDITABLE

DOCS = Path(__file__).resolve().parents[2] / "docs" / "content"
pytestmark = pytest.mark.skipif(not DOCS.is_dir(), reason="documentation not present")

APP = Path(__file__).resolve().parents[1] / "app"


def _read(page: str) -> str:
    return (DOCS / page).read_text(encoding="utf-8")


def test_every_runtime_setting_is_documented() -> None:
    page = _read("administration/parametres.md")
    missing = [key for key in EDITABLE if f"`{key}`" not in page]
    assert not missing, f"Run `make docs-params` (missing: {missing})"


def test_every_incident_kind_is_documented() -> None:
    kinds = set()
    for path in APP.rglob("*.py"):
        kinds |= set(re.findall(r'kind="([a-z_]+)"', path.read_text(encoding="utf-8")))
    page = _read("incidents/types.md")
    assert kinds and not [k for k in kinds if f"`{k}`" not in page]


def test_every_notification_channel_is_documented() -> None:
    page = _read("notifications.md")
    assert not [c for c in CHANNELS if f"#{c}" not in page]


def test_every_api_route_is_documented() -> None:
    from app.api.endpoints import (alerts, audit_log, auth, authorized, command, context, custom_sources, evidence, graph, imagery, incidents,
                                   maritime, rules, scan, sensors, system, timeline, users, watchlist, zones)

    page = re.sub(r"\{[^}]*\}", "{}", _read("api.md"))
    missing = []
    for module in (auth, users, alerts, context, scan, command, sensors, incidents, evidence, audit_log, system, watchlist, maritime, authorized,
                   custom_sources, timeline, imagery, rules, graph, zones):
        for route in module.router.routes:
            path = re.sub(r"\{[^}]*\}", "{}", route.path)
            if not re.search(re.escape(f"`{path}") + r"[`?]", page):
                missing.append(path)
    assert not missing, f"Routes absentes de docs/content/api.md : {missing}"
