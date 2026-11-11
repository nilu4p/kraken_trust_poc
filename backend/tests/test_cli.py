"""Operator CLI: account commands require an authenticated admin or the master key (break-glass)."""

from __future__ import annotations

from typing import Any, Iterator, List

import pytest

import main as cli
from app.config import get_settings
from app.core import audit, identity, vault
from tests.conftest import TEST_PASSWORD


def answers(*values: str) -> Any:
    queue: Iterator[str] = iter(values)
    return lambda _prompt="": next(queue)


def run(argv: List[str], ask: Any = None, secret: Any = None) -> int:
    return cli.main(argv, ask=ask or answers(), secret=secret or answers())


def test_create_admin_requires_existing_admin(make_user: Any, capsys: pytest.CaptureFixture) -> None:
    make_user("chief", "admin")
    make_user("ana", "analyst")
    # No / wrong credentials → refused, nothing created.
    assert run(["create-user", "bob", "admin"], ask=answers("chief"), secret=answers("wrong")) == 3
    assert run(["create-user", "bob", "admin"], ask=answers("ana"), secret=answers(TEST_PASSWORD)) == 3
    assert "bob" not in [u["username"] for u in identity.list_users()]
    # Valid admin → created, password printed once.
    assert run(["create-user", "bob", "admin"], ask=answers("chief"), secret=answers(TEST_PASSWORD)) == 0
    out = capsys.readouterr().out
    assert "Compte créé : bob" in out and "par chief" in out
    created = next(u for u in identity.list_users() if u["username"] == "bob")
    assert created["role"] == "admin" and created["must_change_password"]
    assert any(e["actor"] == "cli:chief" for e in audit.search(action_prefix="user.create"))


def test_cli_failures_count_towards_lockout(make_user: Any) -> None:
    make_user("chief", "admin")
    for _ in range(get_settings().login_max_attempts):
        run(["list-users"], ask=answers("chief"), secret=answers("bad"))
    # Even the right password is now refused: same lockout as the web login.
    assert run(["list-users"], ask=answers("chief"), secret=answers(TEST_PASSWORD)) == 3


def test_temporary_password_admin_cannot_use_cli() -> None:
    password = identity.bootstrap_admin()
    assert run(["create-user", "bob", "admin"], ask=answers("admin"), secret=answers(password)) == 3


def test_break_glass_requires_master_key_and_is_rate_limited(capsys: pytest.CaptureFixture) -> None:
    vault.encrypt(b"init")  # ensures keys/master.key exists
    key = vault.master_key_bytes().decode()
    assert run(["create-user", "rescue", "analyst", "--recovery"], secret=answers(key)) == 1  # admin only
    assert run(["create-user", "rescue", "admin", "--recovery"], secret=answers("not-the-key")) == 3
    assert run(["create-user", "rescue", "admin", "--recovery"], secret=answers(key)) == 0
    assert "par cli-recovery" in capsys.readouterr().out
    actions = [(e["action"], e["outcome"]) for e in audit.search(action_prefix="auth.break_glass")]
    assert ("auth.break_glass", "success") in actions and ("auth.break_glass", "failure") in actions
    for _ in range(cli.BREAK_GLASS_MAX_FAILURES):
        run(["reset-password", "rescue", "--recovery"], secret=answers("bad"))
    # Locked even with the right key until the window expires.
    assert run(["reset-password", "rescue", "--recovery"], secret=answers(key)) == 3


def test_reset_password_requires_admin(make_user: Any, capsys: pytest.CaptureFixture) -> None:
    make_user("chief", "admin")
    make_user("ana", "analyst")
    assert run(["reset-password", "ana"], ask=answers("ana"), secret=answers(TEST_PASSWORD)) == 3
    assert run(["reset-password", "ana"], ask=answers("chief"), secret=answers(TEST_PASSWORD)) == 0
    assert "Mot de passe temporaire de ana" in capsys.readouterr().out


def test_create_admin_needs_no_validation_but_is_traced(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    sent: List[str] = []
    monkeypatch.setattr("app.soc.notify.publish", lambda channel, text, **_kw: sent.append(f"{channel}|{text}") or True)
    assert run(["create-admin", "alice"]) == 0  # no prompt answered: no authentication requested
    out = capsys.readouterr().out
    assert "Compte créé : alice (Administrateur)" in out and "Mot de passe temporaire" in out
    alice = next(u for u in identity.list_users() if u["username"] == "alice")
    assert alice["role"] == "admin" and alice["must_change_password"]
    assert any(e["actor"] == "cli:host" and e["target"] == "user:alice" for e in audit.search(action_prefix="user.create"))
    assert sent and sent[0].startswith("securite|") and "alice" in sent[0]
    assert run(["create-admin", "alice"]) == 1  # duplicate refused
