"""Aggregate API v1 routers."""

from fastapi import APIRouter

from app.api.endpoints import alerts, audit_log, authorized, auth, command, context, custom_sources, evidence, graph, imagery, incidents, maritime, rules, scan, sensors, system, timeline, users, watchlist, zones
from app.config import get_settings

api_router = APIRouter(prefix=get_settings().api_prefix)
for module in (auth, users, alerts, context, scan, command, sensors, incidents, evidence, audit_log, system, watchlist, maritime, authorized, custom_sources, timeline, imagery, rules, graph, zones):
    api_router.include_router(module.router)
