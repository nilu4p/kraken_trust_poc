"""Users, roles, sessions and login hardening.

- Roles (cumulative): viewer < analyst < supervisor < admin.
- Sessions: opaque 256-bit random tokens in an HttpOnly SameSite=Strict
  cookie; only their SHA-256 is stored. Absolute + idle expiry.
- CSRF: per-session token, required in ``X-CSRF-Token`` on state changes.
- Login: generic error message, constant-time path for unknown users,
  account lockout after repeated failures, every attempt audited.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import secrets
from typing import Any, Dict, List, Optional, Tuple

from app.config import Settings, get_settings
from app.core import audit
from app.core.passwords import DUMMY_HASH, generate_password, hash_password, policy_violations, verify_password
from app.db import db, read_db, row, rows

logger = logging.getLogger(__name__)

ROLES = ("viewer", "analyst", "supervisor", "admin")
ROLE_RANK = {role: index for index, role in enumerate(ROLES)}
ROLE_LABELS = {
    "viewer": "Observateur",
    "analyst": "Analyste",
    "supervisor": "Superviseur",
    "admin": "Administrateur",
}
PUBLIC_USER_FIELDS = (
    "id", "username", "display_name", "role", "must_change_password", "disabled",
    "last_login_at", "created_at", "password_changed_at", "locked_until",
)


class AuthError(Exception):
    """Authentication / authorisation failure with a user-safe message."""

    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.status = status


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(value: datetime.datetime) -> str:
    return value.isoformat()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def public_user(user: Dict[str, Any]) -> Dict[str, Any]:
    data = {k: user.get(k) for k in PUBLIC_USER_FIELDS}
    data["must_change_password"] = bool(data["must_change_password"])
    data["disabled"] = bool(data["disabled"])
    data["role_label"] = ROLE_LABELS.get(user.get("role", ""), user.get("role"))
    return data


def has_role(user: Dict[str, Any], minimum: str) -> bool:
    return ROLE_RANK.get(user.get("role", ""), -1) >= ROLE_RANK[minimum]


# --------------------------------------------------------------------------- users


def count_users(settings: Optional[Settings] = None) -> int:
    with read_db(settings) as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def get_user(user_id: int, settings: Optional[Settings] = None) -> Optional[Dict[str, Any]]:
    with read_db(settings) as conn:
        return row(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def list_users(settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    with read_db(settings) as conn:
        return [public_user(u) for u in rows(conn.execute("SELECT * FROM users ORDER BY username").fetchall())]


def create_user(
    username: str,
    password: str,
    role: str,
    *,
    display_name: str = "",
    must_change_password: bool = True,
    actor: str = "system",
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    cfg = settings or get_settings()
    username = username.strip()
    if role not in ROLES:
        raise ValueError("Rôle inconnu")
    if not username or len(username) > 64 or not all(c.isalnum() or c in "._-" for c in username):
        raise ValueError("Identifiant invalide (lettres, chiffres, . _ -)")
    problems = policy_violations(password, username, cfg.password_min_length)
    if problems:
        raise ValueError("Mot de passe refusé : " + ", ".join(problems))
    now = _iso(_now())
    with db(cfg) as conn:
        if conn.execute("SELECT 1 FROM users WHERE lower(username) = lower(?)", (username,)).fetchone():
            raise ValueError("Identifiant déjà utilisé")
        user = row(conn.execute(
            "INSERT INTO users (username, display_name, role, password_hash, must_change_password, created_at, "
            "password_changed_at) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING *",
            (username, display_name.strip()[:120], role, hash_password(password), int(must_change_password), now, now),
        ).fetchone())
    audit.record("user.create", actor=actor, target=f"user:{username}", details={"role": role}, settings=cfg)
    return public_user(user)


def update_user(
    user_id: int,
    *,
    role: Optional[str] = None,
    display_name: Optional[str] = None,
    disabled: Optional[bool] = None,
    actor: Dict[str, Any],
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    cfg = settings or get_settings()
    target = get_user(user_id, cfg)
    if target is None:
        raise LookupError("Utilisateur introuvable")
    if role is not None and role not in ROLES:
        raise ValueError("Rôle inconnu")
    if target["id"] == actor["id"] and (disabled or (role and role != "admin")):
        raise ValueError("Impossible de vous désactiver ou de retirer votre propre rôle administrateur")
    changes: Dict[str, Any] = {}
    if role is not None:
        changes["role"] = role
    if display_name is not None:
        changes["display_name"] = display_name.strip()[:120]
    if disabled is not None:
        changes["disabled"] = int(disabled)
    if changes:
        with db(cfg) as conn:
            assignments = ", ".join(f"{k} = ?" for k in changes)
            conn.execute(f"UPDATE users SET {assignments} WHERE id = ?", (*changes.values(), user_id))
            if disabled or "role" in changes:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        audit.record(
            "user.update", actor=actor["username"], target=f"user:{target['username']}", details=changes, settings=cfg
        )
    return public_user(get_user(user_id, cfg))


def reset_password(user_id: int, *, actor: Dict[str, Any], settings: Optional[Settings] = None) -> str:
    """Admin reset: random temporary password, must be changed at next login."""
    cfg = settings or get_settings()
    target = get_user(user_id, cfg)
    if target is None:
        raise LookupError("Utilisateur introuvable")
    temporary = generate_password()
    with db(cfg) as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1, failed_logins = 0, locked_until = NULL, "
            "password_changed_at = ? WHERE id = ?",
            (hash_password(temporary), _iso(_now()), user_id),
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    audit.record("user.password_reset", actor=actor["username"], target=f"user:{target['username']}", settings=cfg)
    return temporary


def change_password(
    user: Dict[str, Any],
    current: str,
    new: str,
    *,
    keep_session: Optional[str] = None,
    ip: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> None:
    cfg = settings or get_settings()
    fresh = get_user(user["id"], cfg)
    if fresh is None or not verify_password(current, fresh["password_hash"]):
        audit.record("auth.password_change", actor=user["username"], outcome="failure", ip=ip, settings=cfg)
        raise AuthError("Mot de passe actuel incorrect", 400)
    if verify_password(new, fresh["password_hash"]):
        raise ValueError("Le nouveau mot de passe doit être différent de l'ancien")
    problems = policy_violations(new, user["username"], cfg.password_min_length)
    if problems:
        raise ValueError("Mot de passe refusé : " + ", ".join(problems))
    with db(cfg) as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0, password_changed_at = ? WHERE id = ?",
            (hash_password(new), _iso(_now()), user["id"]),
        )
        # Revoke every other session of this user.
        conn.execute(
            "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
            (user["id"], _token_hash(keep_session) if keep_session else ""),
        )
    audit.record("auth.password_change", actor=user["username"], ip=ip, settings=cfg)


def bootstrap_admin(settings: Optional[Settings] = None) -> Optional[str]:
    """Create the first administrator when the user table is empty.

    Returns the generated password (to be shown once) or None.
    """
    cfg = settings or get_settings()
    if count_users(cfg) > 0:
        return None
    configured = cfg.bootstrap_admin_password
    if configured and policy_violations(configured, cfg.bootstrap_admin_user, cfg.password_min_length):
        # A weak BOOTSTRAP_ADMIN_PASSWORD must not crash the start-up: generate one instead.
        logger.error(
            "BOOTSTRAP_ADMIN_PASSWORD refused by the password policy (%s): a random password is generated instead",
            ", ".join(policy_violations(configured, cfg.bootstrap_admin_user, cfg.password_min_length)),
        )
        configured = ""
    password = configured or generate_password()
    create_user(
        cfg.bootstrap_admin_user,
        password,
        "admin",
        display_name="Administrateur",
        must_change_password=True,
        settings=cfg,
    )
    if not configured:
        # Printed once, on purpose, so the operator can log in; it must be changed at first login.
        logger.warning(
            "\n%s\n  KRAKEN first boot — administrator account created\n  user: %s\n  temporary password: %s\n"
            "  (change required at first login)\n%s",
            "=" * 64, cfg.bootstrap_admin_user, password, "=" * 64,
        )
        return password
    return None


# --------------------------------------------------------------------------- sessions


def authenticate(
    username: str,
    password: str,
    *,
    ip: Optional[str],
    action: str = "auth.login",
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    """Check credentials with lockout, constant-time path and auditing; return the user row.

    Shared by the web login and the operator CLI, so both enforce the same rules.
    """
    cfg = settings or get_settings()
    generic = AuthError("Identifiant ou mot de passe incorrect")
    now = _now()
    with db(cfg) as conn:
        user = row(conn.execute("SELECT * FROM users WHERE lower(username) = lower(?)", (username.strip(),)).fetchone())
    if user is None:
        verify_password(password, DUMMY_HASH)  # equalise timing
        audit.record(action, actor=username[:64], outcome="failure", ip=ip, details={"reason": "unknown_user"}, settings=cfg)
        raise generic
    if user["locked_until"] and datetime.datetime.fromisoformat(user["locked_until"]) > now:
        verify_password(password, DUMMY_HASH)
        audit.record(action, actor=user["username"], outcome="failure", ip=ip, details={"reason": "locked"}, settings=cfg)
        raise AuthError("Compte temporairement verrouillé après plusieurs échecs. Réessayez plus tard.", 423)
    if not verify_password(password, user["password_hash"]) or user["disabled"]:
        locked_until = None
        with db(cfg) as conn:
            # Incremented in the database, not from the value read above: parallel attempts
            # (several API processes, concurrent requests) must all count towards the lockout.
            failures = conn.execute(
                "UPDATE users SET failed_logins = failed_logins + 1 WHERE id = ? RETURNING failed_logins", (user["id"],)
            ).fetchone()[0]
            if failures >= cfg.login_max_attempts:
                locked_until = _iso(now + datetime.timedelta(minutes=cfg.login_lockout_minutes))
                conn.execute("UPDATE users SET failed_logins = 0, locked_until = ? WHERE id = ?", (locked_until, user["id"]))
        if locked_until:
            from app.soc import notify

            notify.notify_security(
                f"Compte verrouillé après {cfg.login_max_attempts} échecs : {user['username']}",
                details={"Compte": user["username"], "Rôle": user["role"], "Origine": ip or "—",
                         "Canal": "ligne de commande" if action == "auth.cli" else "interface web",
                         "Déverrouillage": f"{cfg.login_lockout_minutes} min ou make reset-password"},
                key=f"lock:{user['username']}", cooldown=cfg.login_lockout_minutes * 60, settings=cfg,
            )
        audit.record(
            action,
            actor=user["username"],
            outcome="failure",
            ip=ip,
            details={"reason": "disabled" if user["disabled"] else "bad_password", "locked": bool(locked_until)},
            settings=cfg,
        )
        raise generic
    return user


def login(
    username: str,
    password: str,
    *,
    ip: Optional[str],
    user_agent: Optional[str],
    settings: Optional[Settings] = None,
) -> Tuple[str, Dict[str, Any], str]:
    """Return ``(session_token, user, csrf_token)`` or raise AuthError (generic message)."""
    cfg = settings or get_settings()
    now = _now()
    user = authenticate(username, password, ip=ip, settings=cfg)

    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    with db(cfg) as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, last_seen_at, expires_at, ip, user_agent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _token_hash(token),
                user["id"],
                csrf,
                _iso(now),
                _iso(now),
                _iso(now + datetime.timedelta(minutes=cfg.session_ttl_minutes)),
                ip,
                (user_agent or "")[:256],
            ),
        )
        conn.execute(
            "UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = ? WHERE id = ?",
            (_iso(now), user["id"]),
        )
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_iso(now),))
    audit.record("auth.login", actor=user["username"], ip=ip, settings=cfg)
    return token, public_user(user), csrf


def resolve_session(token: Optional[str], settings: Optional[Settings] = None) -> Optional[Tuple[Dict[str, Any], str]]:
    """``(user, csrf_token)`` for a valid session, else None. Slides the idle timeout."""
    if not token or len(token) > 128:
        return None
    cfg = settings or get_settings()
    now = _now()
    # Read first without the write lock: authenticating a request must not queue behind
    # writers. The write lock is taken only to drop a dead session or slide the idle timer.
    with read_db(cfg) as conn:
        session = row(conn.execute("SELECT * FROM sessions WHERE token_hash = ?", (_token_hash(token),)).fetchone())
        user = row(conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()) if session else None
    if session is None:
        return None
    idle_limit = datetime.datetime.fromisoformat(session["last_seen_at"]) + datetime.timedelta(minutes=cfg.session_idle_minutes)
    if datetime.datetime.fromisoformat(session["expires_at"]) < now or idle_limit < now or user is None or user["disabled"]:
        with db(cfg) as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (session["token_hash"],))
        return None
    if (now - datetime.datetime.fromisoformat(session["last_seen_at"])).total_seconds() > 30:
        with db(cfg) as conn:
            conn.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (_iso(now), session["token_hash"]))
    return user, session["csrf_token"]


def logout(token: Optional[str], settings: Optional[Settings] = None) -> None:
    if token:
        with db(settings) as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def list_sessions(user_id: int, settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    with read_db(settings) as conn:
        return rows(
            conn.execute(
                "SELECT created_at, last_seen_at, expires_at, ip, user_agent FROM sessions WHERE user_id = ? "
                "ORDER BY last_seen_at DESC",
                (user_id,),
            ).fetchall()
        )
