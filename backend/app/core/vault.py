"""Secrets at rest: Fernet (AES-128-CBC + HMAC-SHA256) under a master key.

Master key resolution order:
1. ``MASTER_KEY`` environment variable (injected by a secret manager),
2. ``MASTER_KEY_FILE`` (Docker secret ``secrets/master_key``, the default deployment):
   kept out of the ``keys/`` volume, hence out of backups — the encrypted secrets
   are never stored next to the key that opens them,
3. ``keys/master.key`` (0600), generated on first boot — single-host development only.

Used for API keys, the webhook URL and the PKI private-key passphrase. The
master key itself is never logged nor returned by the API.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

MASTER_KEY_FILE = "master.key"
_lock = threading.Lock()
_cache: dict = {}


class VaultError(RuntimeError):
    """Raised when a secret cannot be decrypted (wrong / rotated master key)."""


def write_private(path: Path, data: bytes) -> None:
    """Atomically write ``data`` to ``path`` with 0600 permissions from creation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)


def _load_master_key(cfg: Settings) -> bytes:
    if cfg.master_key:
        key = cfg.master_key.strip().encode()
        Fernet(key)  # validates format (32 bytes urlsafe base64)
        return key
    if cfg.master_key_file:
        # Never fall back to generating another key: secrets encrypted with the configured one would be lost.
        try:
            key = Path(cfg.master_key_file).read_bytes().strip()
            Fernet(key)
            return key
        except Exception as exc:
            raise VaultError(f"MASTER_KEY_FILE illisible ou invalide ({cfg.master_key_file})") from exc
    path = Path(cfg.keys_dir) / MASTER_KEY_FILE
    if path.is_file():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    write_private(path, key)
    logger.warning(
        "Generated a new master key in %s — back it up and prefer MASTER_KEY from a secret manager",
        path,
    )
    return key


def _fernet(cfg: Optional[Settings] = None) -> Fernet:
    cfg = cfg or get_settings()
    cache_key = (cfg.master_key, cfg.master_key_file, str(cfg.keys_dir))
    with _lock:
        if cache_key not in _cache:
            _cache[cache_key] = Fernet(_load_master_key(cfg))
        return _cache[cache_key]


def encrypt(plaintext: bytes, settings: Optional[Settings] = None) -> bytes:
    return _fernet(settings).encrypt(plaintext)


def decrypt(token: bytes, settings: Optional[Settings] = None) -> bytes:
    try:
        return _fernet(settings).decrypt(token)
    except InvalidToken as exc:
        raise VaultError("Déchiffrement impossible : clé maître incorrecte ou donnée altérée") from exc


def derived_secret(purpose: str, settings: Optional[Settings] = None) -> bytes:
    """Deterministic sub-key for a given purpose (e.g. PKI passphrase), never stored."""
    cfg = settings or get_settings()
    master = base64.urlsafe_b64decode(_load_master_key(cfg))
    return base64.urlsafe_b64encode(hmac.new(master, purpose.encode(), hashlib.sha256).digest())


def master_key_bytes(settings: Optional[Settings] = None) -> bytes:
    """Current master key for break-glass verification; never generates one (b"" if absent)."""
    cfg = settings or get_settings()
    if cfg.master_key:
        return cfg.master_key.strip().encode()
    if cfg.master_key_file:
        path = Path(cfg.master_key_file)
        return path.read_bytes().strip() if path.is_file() else b""
    path = Path(cfg.keys_dir) / MASTER_KEY_FILE
    return path.read_bytes().strip() if path.is_file() else b""
