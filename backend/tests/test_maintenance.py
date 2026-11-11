"""Factory reset, backup and restore (CLI only, authenticated, confirmed, backed up)."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from typing import Any, Iterator

import pytest

import main as cli
from app.config import get_settings
from app.core import audit, credentials, identity, maintenance, vault
from app.soc import watchlist
from tests.conftest import TEST_PASSWORD

PHRASE = maintenance.CONFIRMATION_PHRASE


def answers(*values: str) -> Any:
    queue: Iterator[str] = iter(values)
    return lambda _prompt="": next(queue)


def seed(make_user: Any) -> None:
    make_user("chief", "admin")
    make_user("ana", "analyst")
    watchlist.add_entry({"name": "Sea Ghost", "reason": "INN"}, actor="t")
    credentials.update({"gfw_token": "TOKEN-ABCDEFGHIJ"})
    dossier = get_settings().preuves_dir / "alerte_20261019_213439_deadbeef"
    dossier.mkdir(parents=True)
    (dossier / "manifest.json").write_text("{}")


def run(argv: list, ask: Any, secret: Any) -> int:
    return cli.main(argv, ask=ask, secret=secret)


def test_reset_refused_without_auth_or_phrase(make_user: Any) -> None:
    seed(make_user)
    assert run(["factory-reset"], answers("chief"), answers("wrong")) == 3
    assert run(["factory-reset"], answers("ana", ), answers(TEST_PASSWORD)) == 3  # not an admin
    assert run(["factory-reset"], answers("chief", "oui", "non", "toujours pas"), answers(TEST_PASSWORD)) == cli.EXIT_CANCELLED
    assert run(["factory-reset"], answers("chief", ""), answers(TEST_PASSWORD)) == cli.EXIT_CANCELLED  # empty = cancel
    assert len(identity.list_users()) == 2  # nothing touched
    assert any(e["outcome"] == "failure" for e in audit.search(action_prefix="system.factory_reset"))


def test_full_reset_backs_up_and_starts_fresh(make_user: Any, capsys: pytest.CaptureFixture) -> None:
    seed(make_user)
    old_key = vault.master_key_bytes()
    assert run(["factory-reset", "--scope", "full"], answers("chief", PHRASE), answers(TEST_PASSWORD)) == 0
    out = capsys.readouterr().out
    assert "Plateforme réinitialisée" in out and "Mot de passe temporaire" in out
    cfg = get_settings()
    assert [u["username"] for u in identity.list_users()] == [cfg.bootstrap_admin_user]
    assert not any(cfg.preuves_dir.iterdir())
    assert vault.master_key_bytes() != old_key  # new master key, old secrets unreadable
    assert credentials.describe(cfg)["gfw_token"]["origin"] != "dashboard"
    first = audit.search(limit=1000)[-1]
    assert first["action"] == "user.create" or first["action"] == "system.factory_reset"
    reset_entry = next(e for e in audit.search(action_prefix="system.factory_reset") if e["outcome"] == "success")
    assert reset_entry["details"]["previous_audit_head"] and reset_entry["details"]["backup_sha256"]
    backups = list(Path(cfg.backup_dir).glob("kraken-before-reset-full-*.tar.gz"))
    assert len(backups) == 1 and oct(backups[0].stat().st_mode & 0o777) == "0o600"


def test_data_reset_keeps_keys_and_credentials(make_user: Any) -> None:
    seed(make_user)
    old_key = vault.master_key_bytes()
    assert run(["factory-reset", "--scope", "data", "--no-backup"], answers("chief", PHRASE), answers(TEST_PASSWORD)) == 0
    assert vault.master_key_bytes() == old_key
    credentials.apply_stored_credentials(get_settings())
    assert get_settings().gfw_token == "TOKEN-ABCDEFGHIJ"
    assert watchlist.list_entries()["entries"] == []
    assert not list(Path(get_settings().backup_dir).glob("*.tar.gz"))


def test_backup_restore_roundtrip(make_user: Any, capsys: pytest.CaptureFixture) -> None:
    seed(make_user)
    assert run(["backup"], answers("chief"), answers(TEST_PASSWORD)) == 0
    backup = next(Path(get_settings().backup_dir).glob("kraken-backup-*.tar.gz"))
    assert run(["factory-reset", "--no-backup"], answers("chief", PHRASE), answers(TEST_PASSWORD)) == 0
    new_admin_password = capsys.readouterr().out.split("Mot de passe temporaire : ")[1].split()[0]
    identity.change_password(
        next(u for u in identity.list_users() if u["username"] == "admin") | {"id": 1}, new_admin_password, "Restau-Ration-42!"
    )
    assert run(["restore", backup.name], answers("admin", PHRASE), answers("Restau-Ration-42!")) == 0
    assert sorted(u["username"] for u in identity.list_users()) == ["ana", "chief"]
    assert (get_settings().preuves_dir / "alerte_20261019_213439_deadbeef" / "manifest.json").is_file()
    credentials.apply_stored_credentials(get_settings())
    assert get_settings().gfw_token == "TOKEN-ABCDEFGHIJ"  # master key restored with the secrets
    assert any(e["action"] == "system.restore" for e in audit.search(limit=50))


@pytest.mark.parametrize("evil", ["../../etc/passwd", "/abs/path", "keys/../../x", "other/file"])
def test_restore_rejects_malicious_archives(evil: str) -> None:
    path = Path(get_settings().backup_dir)
    path.mkdir(parents=True, exist_ok=True)
    archive_path = path / "evil.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name, data in (("KRAKEN_BACKUP.json", b"{}"), (evil, b"pwned")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError):
        maintenance.restore(archive_path)


def test_restore_rejects_symlinks_and_foreign_archives() -> None:
    path = Path(get_settings().backup_dir)
    path.mkdir(parents=True, exist_ok=True)
    link = path / "link.tar.gz"
    with tarfile.open(link, "w:gz") as archive:
        info = tarfile.TarInfo("KRAKEN_BACKUP.json")
        info.size = 2
        archive.addfile(info, io.BytesIO(b"{}"))
        sym = tarfile.TarInfo("keys/master.key")
        sym.type = tarfile.SYMTYPE
        sym.linkname = "/etc/shadow"
        archive.addfile(sym)
    with pytest.raises(ValueError):
        maintenance.restore(link)
    foreign = path / "foreign.tar.gz"
    with tarfile.open(foreign, "w:gz") as archive:
        info = tarfile.TarInfo("data/x")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="pas une sauvegarde"):
        maintenance.restore(foreign)


def test_confirmation_tolerates_case_accents_and_spaces(make_user: Any) -> None:
    seed(make_user)
    typed = "  réinitialiser   kraken "
    assert run(["factory-reset", "--no-backup"], answers("chief", "oops", typed), answers(TEST_PASSWORD)) == 0
    assert [u["username"] for u in identity.list_users()] == [get_settings().bootstrap_admin_user]
