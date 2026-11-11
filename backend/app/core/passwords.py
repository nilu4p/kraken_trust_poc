"""Password hashing (scrypt, stdlib) and password policy."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import List

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
_MAXMEM = 64 * 1024 * 1024

COMMON_PASSWORDS = {
    "password", "motdepasse", "azerty", "azertyuiop", "qwerty", "123456789012",
    "admin", "administrator", "kraken", "changeme", "welcome", "letmein",
}


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def hash_password(password: str) -> str:
    """scrypt$N$r$p$salt$hash — memory-hard, per-password random salt."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=_MAXMEM, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verification; False on any malformed hash."""
    try:
        algo, n, r, p, salt_b64, hash_b64 = encoded.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_MAXMEM,
            dklen=len(expected),
        )
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False


# Verified against when the username does not exist, so response time does not
# reveal whether an account exists.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def policy_violations(password: str, username: str = "", min_length: int = 12) -> List[str]:
    """Human-readable reasons why a password is rejected (empty list = accepted)."""
    problems: List[str] = []
    if len(password) < min_length:
        problems.append(f"au moins {min_length} caractères")
    if len(password) > 256:
        problems.append("256 caractères maximum")
    classes = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
    )
    if classes < 3:
        problems.append("au moins 3 types parmi minuscules, majuscules, chiffres, symboles")
    lowered = password.lower()
    if username and username.lower() in lowered:
        problems.append("ne doit pas contenir l'identifiant")
    if lowered in COMMON_PASSWORDS or any(word in lowered for word in ("kraken", "password", "motdepasse")):
        problems.append("mot de passe trop courant")
    return problems


def generate_password(length: int = 20) -> str:
    """Random password that satisfies the policy."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_!@#%"
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if not policy_violations(candidate, min_length=length):
            return candidate
