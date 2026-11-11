"""Operator CLI: ``python main.py <command>`` (run inside backend/ or the container).

Commands:
  scan [--scene ID]                           run the SAR pipeline once
  create-admin USER [--name N]                create an administrator directly (host access is the control;
                                              audited and notified to the SOC)
  create-user USER ROLE [--name N] [--recovery]
                                              create an account (prints a temporary password)
  reset-password USER [--recovery]            new temporary password, sessions revoked
  list-users                                  accounts, roles and state (no secrets)
  backup                                      archive data / evidence / keys into BACKUP_DIR
  factory-reset [--scope full|data] [--no-backup] [--recovery]
                                              wipe the instance and bring it back as new
  restore FILE [--recovery]                   replace the instance content by a backup
  verify-audit                                recompute the audit hash chain
  generate-master-key                         print a new MASTER_KEY value for a secret manager

Account commands require an existing administrator to authenticate (login +
password typed without echo, same lockout and audit as the web login).
``--recovery`` is the break-glass path when no administrator can log in any
more: it requires the master key instead (kept offline), is limited to a few
attempts, audited and notified. Threat model: anyone in the ``docker`` group /
root on the host can read the volumes directly — these controls stop casual
misuse and leave a tamper-evident trace, host access control remains essential.
"""

from __future__ import annotations

import argparse
import datetime
import getpass
import hmac
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app.config import Settings, get_settings
from app.core import audit, identity, maintenance, vault
from app.core.passwords import generate_password
from app.db import db, init_db

BREAK_GLASS_MAX_FAILURES = 3
BREAK_GLASS_WINDOW_MINUTES = 30

Prompt = Callable[[str], str]


EXIT_CANCELLED = 10  # operator declined the confirmation: not an error for `make`
CONFIRM_ATTEMPTS = 3


def _normalise_phrase(text: str) -> str:
    import unicodedata

    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(plain.upper().replace("«", " ").replace("»", " ").split())


def confirm_phrase(ask: Prompt) -> bool:
    """The full phrase must be typed (case, accents and extra spaces are tolerated)."""
    expected = _normalise_phrase(maintenance.CONFIRMATION_PHRASE)
    print(f"  Pour confirmer, tapez la phrase : {maintenance.CONFIRMATION_PHRASE}")
    for attempt in range(1, CONFIRM_ATTEMPTS + 1):
        typed = ask(f"  Confirmation ({attempt}/{CONFIRM_ATTEMPTS}) : ")
        if _normalise_phrase(typed) == expected:
            return True
        if not typed.strip():
            return False  # empty answer = explicit cancel
        print(f"  Phrase différente de « {maintenance.CONFIRMATION_PHRASE} ».")
    return False


class CliAuthError(Exception):
    """Operator could not be authenticated for a privileged CLI command."""


def authenticate_admin(cfg: Settings, ask: Prompt = input, secret: Prompt = getpass.getpass) -> Dict[str, Any]:
    """An existing, active administrator must prove their identity."""
    print("Authentification d'un administrateur existant requise.")
    username = ask("  Identifiant administrateur : ").strip()
    password = secret("  Mot de passe (non affiché) : ")
    try:
        user = identity.authenticate(username, password, ip="cli", action="auth.cli", settings=cfg)
    except identity.AuthError as exc:
        raise CliAuthError(str(exc)) from exc
    if user["role"] != "admin":
        audit.record("auth.cli", actor=user["username"], outcome="failure", ip="cli", details={"reason": "not_admin"}, settings=cfg)
        raise CliAuthError("Ce compte n'est pas administrateur")
    if user["must_change_password"]:
        raise CliAuthError("Mot de passe temporaire : connectez-vous d'abord à l'interface pour le changer")
    audit.record("auth.cli", actor=user["username"], ip="cli", settings=cfg)
    return user


def _backup_key_notice(cfg: Settings) -> str:
    """Whether the archive holds the master key (single host: keys/master.key) or not (Docker secret)."""
    if not cfg.master_key and not cfg.master_key_file and (Path(cfg.keys_dir) / vault.MASTER_KEY_FILE).is_file():
        return "contient la clé maître (keys/master.key) : à stocker hors ligne, aussi sensible que la plateforme"
    return "sans la clé maître (secrets/master_key) : conservez-la à part, hors de la machine, sinon l'archive est illisible"


def _recent_break_glass_failures(cfg: Settings) -> int:
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=BREAK_GLASS_WINDOW_MINUTES)).isoformat()
    with db(cfg) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action = 'auth.break_glass' AND outcome = 'failure' AND at >= ?", (since,)
        ).fetchone()[0]


def authenticate_break_glass(cfg: Settings, secret: Prompt = getpass.getpass) -> Dict[str, Any]:
    """Recovery when no administrator can log in: prove possession of the master key."""
    if _recent_break_glass_failures(cfg) >= BREAK_GLASS_MAX_FAILURES:
        raise CliAuthError(f"Mode secours bloqué : trop d'échecs, réessayez dans {BREAK_GLASS_WINDOW_MINUTES} min")
    print("MODE SECOURS — preuve de possession de la clé maître requise (contenu de secrets/master_key, MASTER_KEY "
          "ou keys/master.key selon le déploiement).")
    provided = secret("  Clé maître (non affichée) : ").strip().encode()
    expected = vault.master_key_bytes(cfg)
    if not provided or not hmac.compare_digest(provided, expected):
        audit.record("auth.break_glass", actor="cli-recovery", outcome="failure", ip="cli", settings=cfg)
        raise CliAuthError("Clé maître incorrecte")
    audit.record("auth.break_glass", actor="cli-recovery", ip="cli", settings=cfg)
    from app.soc import notify

    # Blocking: the CLI process exits right after, a background send would be lost.
    notify.notify_security(
        "MODE SECOURS utilisé (clé maître) en ligne de commande", level="danger",
        details={"Opération": " ".join(sys.argv[1:3]) or "—", "Hôte": "cli"}, settings=cfg, blocking=True,
    )
    return {"id": 0, "username": "cli-recovery", "role": "admin"}


def operator(cfg: Settings, recovery: bool, ask: Prompt = input, secret: Prompt = getpass.getpass) -> Dict[str, Any]:
    return authenticate_break_glass(cfg, secret) if recovery else authenticate_admin(cfg, ask, secret)


def create_admin(cfg: Settings, username: str, display_name: str = "") -> int:
    """Create an administrator without prior authentication (operator decision).

    Whoever can run this has shell + Docker access to the host. Detective
    controls instead of a gate: audit entry (actor ``cli:host``) and a SOC
    notification on every creation.
    """
    password = generate_password()
    try:
        identity.create_user(username, password, "admin", display_name=display_name, actor="cli:host")
    except ValueError as exc:
        print(f"Création impossible : {exc}", file=sys.stderr)
        if "déjà utilisé" in str(exc):
            print(f"  → pour reprendre la main sur ce compte : make reset-password NAME={username}", file=sys.stderr)
        return 1
    from app.soc import notify

    notify.notify_security(
        f"Nouvel administrateur créé en ligne de commande : {username}",
        details={"Compte": username, "Créé par": "cli:host (accès machine)"}, settings=cfg, blocking=True,
    )
    print(
        f"\n  Compte créé : {username} (Administrateur)\n"
        f"  Mot de passe temporaire : {password}\n"
        "  (affiché une seule fois — à changer à la première connexion)\n"
    )
    return 0


def factory_reset(cfg: Settings, args: Any, ask: Prompt, secret: Prompt) -> int:
    actor = operator(cfg, args.recovery, ask, secret)
    counts = maintenance.inventory(cfg)
    kept = "rien (installation neuve : PKI, clé maître et clés API régénérées / effacées)" if args.scope == "full" else (
        "PKI, clé maître et clés API"
    )
    print(
        f"\n  ⚠  RÉINITIALISATION ({args.scope}) — opération irréversible\n"
        f"  Sera effacé : {counts.get('users', '?')} compte(s), {counts.get('incidents', '?')} incident(s), "
        f"{counts.get('evidence_dossiers', 0)} dossier(s) de preuves, {counts.get('watchlist', '?')} entrée(s) de surveillance, "
        f"{counts.get('audit_log', '?')} entrée(s) d'audit, paramètres et caches.\n"
        f"  Conservé : {kept}.\n"
        f"  Sauvegarde préalable : {'NON (--no-backup)' if args.no_backup else 'oui, dans ' + str(cfg.backup_dir)}\n"
    )
    if not confirm_phrase(ask):
        audit.record("system.factory_reset", actor=f"cli:{actor['username']}", outcome="failure",
                     details={"reason": "not_confirmed", "scope": args.scope}, settings=cfg)
        print("\n  Réinitialisation annulée : aucune modification.\n")
        return EXIT_CANCELLED
    backup = None if args.no_backup else maintenance.create_backup(cfg, label=f"before-reset-{args.scope}")
    previous_head = audit.verify_chain(cfg).get("head")
    failures = maintenance.wipe(args.scope, cfg)
    password = maintenance.reinitialise(
        args.scope, actor=f"cli:{actor['username']}", backup=backup, previous_audit_head=previous_head, settings=cfg
    )
    from app.soc import notify

    notify.notify_security(
        f"Plateforme RÉINITIALISÉE (portée : {args.scope})", level="danger",
        details={"Par": f"cli:{actor['username']}", "Sauvegarde": backup[0].name if backup else "aucune",
                 "Éléments effacés": ", ".join(f"{k} {v}" for k, v in counts.items())},
        settings=cfg, blocking=True,
    )
    print("\n  ✔ Plateforme réinitialisée.")
    if backup:
        print(f"  Sauvegarde : {backup[0]}\n  SHA-256    : {backup[1]}  ({_backup_key_notice(cfg)})")
    if failures:
        print(f"  ⚠ {len(failures)} élément(s) non supprimé(s) (droits) : {failures[:3]}…\n"
              "    → sudo chown -R $(id -u):$(id -g) keys preuves cache data   puis relancer.")
    if password:
        print(f"\n  Nouvel administrateur : {cfg.bootstrap_admin_user}\n  Mot de passe temporaire : {password}\n"
              "  (affiché une seule fois — à changer à la première connexion)\n")
    else:
        print("\n  Administrateur recréé avec BOOTSTRAP_ADMIN_PASSWORD (changement exigé à la connexion).\n")
    return 0


def restore_backup(cfg: Settings, args: Any, ask: Prompt, secret: Prompt) -> int:
    actor = operator(cfg, args.recovery, ask, secret)
    path = Path(args.file)
    if not path.is_absolute():
        path = Path(cfg.backup_dir) / path.name
    print(f"\n  ⚠  RESTAURATION de {path.name} : le contenu actuel (comptes, incidents, preuves, clés) sera remplacé.")
    if not confirm_phrase(ask):
        print("\n  Restauration annulée : aucune modification.\n")
        return EXIT_CANCELLED
    safety = maintenance.create_backup(cfg, label="before-restore")
    try:
        failures = maintenance.restore(path, cfg)
    except (ValueError, FileNotFoundError) as exc:
        print(f"\n  Restauration refusée : {exc}\n", file=sys.stderr)
        return 1
    from app.db import init_db

    init_db(cfg)  # applies migrations if the backup predates the current schema
    audit.record("system.restore", actor=f"cli:{actor['username']}", target=path.name,
                 details={"sha256": maintenance._sha256(path), "safety_backup": safety[0].name}, settings=cfg)
    from app.soc import notify

    notify.notify_security(
        f"Plateforme RESTAURÉE depuis {path.name}", level="danger",
        details={"Par": f"cli:{actor['username']}", "Sauvegarde de l'état précédent": safety[0].name},
        settings=cfg, blocking=True,
    )
    print(f"\n  ✔ Restauration terminée. Sauvegarde de sécurité de l'état précédent : {safety[0].name}")
    if failures:
        print(f"  ⚠ {len(failures)} élément(s) non supprimé(s) (droits) : {failures[:3]}…")
    print("  Les comptes et mots de passe sont ceux de la sauvegarde.\n")
    return 0


def main(argv: Optional[list] = None, ask: Prompt = input, secret: Prompt = getpass.getpass) -> int:
    parser = argparse.ArgumentParser(prog="kraken")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--scene", default=None)
    create = sub.add_parser("create-user")
    create.add_argument("username")
    create.add_argument("role", choices=identity.ROLES)
    create.add_argument("--name", default="", help="display name")
    create.add_argument("--recovery", action="store_true", help="break-glass: authenticate with the master key")
    reset = sub.add_parser("reset-password")
    reset.add_argument("username")
    reset.add_argument("--recovery", action="store_true", help="break-glass: authenticate with the master key")
    create_admin_cmd = sub.add_parser("create-admin")
    create_admin_cmd.add_argument("username")
    create_admin_cmd.add_argument("--name", default="", help="display name")
    sub.add_parser("list-users")
    sub.add_parser("backup")
    reset_all = sub.add_parser("factory-reset")
    reset_all.add_argument("--scope", choices=("full", "data"), default="full")
    reset_all.add_argument("--no-backup", action="store_true")
    reset_all.add_argument("--recovery", action="store_true", help="break-glass: authenticate with the master key")
    restore = sub.add_parser("restore")
    restore.add_argument("file")
    restore.add_argument("--recovery", action="store_true", help="break-glass: authenticate with the master key")
    sub.add_parser("verify-audit")
    sub.add_parser("generate-master-key")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    if args.command == "generate-master-key":
        from cryptography.fernet import Fernet

        print(Fernet.generate_key().decode())
        return 0

    cfg = get_settings()
    init_db(cfg)
    try:
        if args.command == "scan":
            from app.pipeline.orchestrator import run_kraken_pipeline

            run_kraken_pipeline(cfg, args.scene, "cli")
        elif args.command == "create-admin":
            return create_admin(cfg, args.username, args.name)
        elif args.command == "create-user":
            if args.recovery and args.role != "admin":
                print("Le mode secours ne sert qu'à recréer un administrateur.", file=sys.stderr)
                return 1
            actor = operator(cfg, args.recovery, ask, secret)
            password = generate_password()
            try:
                identity.create_user(
                    args.username, password, args.role, display_name=args.name, actor=f"cli:{actor['username']}"
                )
            except ValueError as exc:
                print(f"Création impossible : {exc}", file=sys.stderr)
                if "déjà utilisé" in str(exc):
                    print(f"  → pour reprendre la main sur ce compte : make reset-password NAME={args.username}", file=sys.stderr)
                return 1
            print(
                f"\n  Compte créé : {args.username} ({identity.ROLE_LABELS[args.role]}) par {actor['username']}\n"
                f"  Mot de passe temporaire : {password}\n"
                "  (affiché une seule fois — à changer à la première connexion)\n"
            )
        elif args.command == "reset-password":
            actor = operator(cfg, args.recovery, ask, secret)
            user = next((u for u in identity.list_users() if u["username"].lower() == args.username.lower()), None)
            if user is None:
                print("Utilisateur introuvable", file=sys.stderr)
                return 1
            temporary = identity.reset_password(user["id"], actor={**actor, "username": f"cli:{actor['username']}"})
            print(
                f"\n  Mot de passe temporaire de {user['username']} : {temporary}\n"
                "  (sessions fermées, verrouillage levé, changement exigé à la connexion)\n"
            )
        elif args.command == "list-users":
            operator(cfg, False, ask, secret)
            for u in identity.list_users():
                state = "désactivé" if u["disabled"] else ("MDP temporaire" if u["must_change_password"] else "actif")
                print(f"  {u['username']:<24} {u['role_label']:<15} {state:<15} dernière connexion : {u['last_login_at'] or 'jamais'}")
        elif args.command == "backup":
            actor = operator(cfg, False, ask, secret)
            path, digest = maintenance.create_backup(cfg)
            audit.record("system.backup", actor=f"cli:{actor['username']}", target=path.name, details={"sha256": digest}, settings=cfg)
            print(f"\n  Sauvegarde : {path}\n  SHA-256 : {digest}\n  ({_backup_key_notice(cfg)})\n")
        elif args.command == "factory-reset":
            return factory_reset(cfg, args, ask, secret)
        elif args.command == "restore":
            return restore_backup(cfg, args, ask, secret)
        elif args.command == "verify-audit":
            result = audit.verify_chain()
            print(result)
            return 0 if result["valid"] else 2
    except CliAuthError as exc:
        print(f"\n  Refusé : {exc}\n", file=sys.stderr)
        return 3
    except (KeyboardInterrupt, EOFError):
        print("\n  Annulé : aucune modification.")
        return EXIT_CANCELLED
    return 0


if __name__ == "__main__":
    sys.exit(main())
