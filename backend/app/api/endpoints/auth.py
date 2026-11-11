"""Login / logout / current user / password change."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import current_user, request_ip, session_cookie_name
from app.config import get_settings
from app.core import audit, identity
from app.core.ratelimit import login_limiter

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class PasswordBody(BaseModel):
    current_password: str = Field(..., max_length=256)
    new_password: str = Field(..., max_length=256)


def _set_cookie(response: Response, token: str) -> None:
    cfg = get_settings()
    response.set_cookie(
        session_cookie_name(),
        token,
        max_age=cfg.session_ttl_minutes * 60,
        httponly=True,
        secure=cfg.cookie_secure,  # enable (COOKIE_SECURE=true) whenever served over TLS
        samesite="strict",
        path="/",
    )


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response) -> Dict[str, Any]:
    ip = request_ip(request)
    if not login_limiter.hit(f"ip:{ip}") or not login_limiter.hit(f"user:{body.username.lower()}"):
        raise HTTPException(status_code=429, detail="Trop de tentatives. Patientez une minute.")
    try:
        token, user, csrf = identity.login(
            body.username, body.password, ip=ip, user_agent=request.headers.get("user-agent")
        )
    except identity.AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    _set_cookie(response, token)
    return {"user": user, "csrf_token": csrf}


@router.post("/logout")
def logout(request: Request, response: Response, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, str]:
    identity.logout(request.cookies.get(session_cookie_name()))
    cfg = get_settings()
    # Same attributes as when set: a __Host- cookie is only removed by a Secure deletion.
    response.delete_cookie(session_cookie_name(), path="/", secure=cfg.cookie_secure, httponly=True, samesite="strict")
    audit.record("auth.logout", actor=user["username"], ip=request_ip(request))
    return {"status": "ok"}


@router.get("/me")
def me(request: Request, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    return {"user": identity.public_user(user), "csrf_token": request.state.csrf}


@router.get("/check", status_code=204)
def check(role: str = Query("viewer", pattern=r"^(viewer|analyst|supervisor|admin)$"),
          user: Dict[str, Any] = Depends(current_user)) -> Response:
    """Session probe for the reverse proxy (nginx auth_request protecting the knowledge base).

    204 = valid session, 401 = not logged in, 403 = password change pending or role below ``role``
    (the notification sink, which shows the security channel, asks for ``supervisor``).
    """
    if not identity.has_role(user, role):
        raise HTTPException(status_code=403, detail="Droits insuffisants pour cette action")
    return Response(status_code=204)


@router.post("/password")
def change_password(body: PasswordBody, request: Request, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    # A stolen session must not become an oracle to guess the current password.
    if not login_limiter.hit(f"password:{user['id']}"):
        raise HTTPException(status_code=429, detail="Trop de tentatives. Patientez une minute.")
    try:
        identity.change_password(
            user,
            body.current_password,
            body.new_password,
            keep_session=request.cookies.get(session_cookie_name()),
            ip=request_ip(request),
        )
    except identity.AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"user": identity.public_user(identity.get_user(user["id"]))}


@router.get("/sessions")
def sessions(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    return {"sessions": identity.list_sessions(user["id"])}
