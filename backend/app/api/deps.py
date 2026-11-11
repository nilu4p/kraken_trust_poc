"""Request authentication, CSRF and role-based access control dependencies."""

from __future__ import annotations

import hmac
from typing import Any, Callable, Dict

from fastapi import Depends, HTTPException, Request

from app.config import get_settings
from app.core.identity import has_role, resolve_session
from app.core.ratelimit import client_ip

SESSION_COOKIE = "kraken_session"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Endpoints reachable while a password change is pending.
PASSWORD_CHANGE_ALLOWED = ("/auth/me", "/auth/password", "/auth/logout")


def session_cookie_name() -> str:
    """Session cookie name. Over HTTPS (COOKIE_SECURE=true) it carries the ``__Host-`` prefix: the
    browser then only accepts it Secure, on Path=/ and host-only, so a neighbouring subdomain (shared
    dynamic-DNS zones) can neither plant nor overwrite a session cookie (session fixation)."""
    return ("__Host-" if get_settings().cookie_secure else "") + SESSION_COOKIE


def request_ip(request: Request) -> str:
    peer = request.client.host if request.client else None
    return client_ip(peer, request.headers.get("x-forwarded-for"))


def current_user(request: Request) -> Dict[str, Any]:
    """Authenticated user for this request (401 otherwise); enforces CSRF on writes."""
    resolved = resolve_session(request.cookies.get(session_cookie_name()))
    if resolved is None:
        raise HTTPException(status_code=401, detail="Authentification requise")
    user, csrf = resolved
    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not hmac.compare_digest(sent, csrf):
            raise HTTPException(status_code=403, detail="Jeton CSRF manquant ou invalide")
    if user["must_change_password"] and not request.url.path.endswith(PASSWORD_CHANGE_ALLOWED):
        raise HTTPException(status_code=403, detail="Changement de mot de passe requis")
    request.state.user = user
    request.state.csrf = csrf
    return user


def require_role(minimum: str) -> Callable[..., Dict[str, Any]]:
    """Dependency factory: the user must hold ``minimum`` role or above."""

    def dependency(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
        if not has_role(user, minimum):
            raise HTTPException(status_code=403, detail="Droits insuffisants pour cette action")
        return user

    return dependency


viewer = require_role("viewer")
analyst = require_role("analyst")
supervisor = require_role("supervisor")
admin = require_role("admin")
