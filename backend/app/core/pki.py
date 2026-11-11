"""Local ECDSA P-256 PKI (Root CA + leaf sealer) for C2PA signing.

The root (10 years) is the trust anchor of every proof; the leaf (1 year) is
renewed by the root before it expires, so proofs signed by earlier leaves stay
verifiable. A replaced root is archived in ``keys/roots/`` (public certificate
only): proofs sealed under it remain verifiable.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.config import Settings, get_settings
from app.core import vault

logger = logging.getLogger(__name__)

ROOT_VALIDITY = datetime.timedelta(days=3650)
LEAF_VALIDITY = datetime.timedelta(days=365)
LEAF_RENEW_BEFORE = datetime.timedelta(days=30)
ARCHIVED_ROOTS = "roots"

REQUIRED_FILES = (
    "rootCA_cert.pem",
    "rootCA_key.pem",
    "cert.pem",
    "key.pem",
    "fullchain.pem",
)


def _name(common_name: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "FR"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Guyane"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Cayenne"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "KRAKEN"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )


def _passphrase(settings: Settings | None = None) -> bytes:
    return vault.derived_secret("pki-private-keys", settings)


def _write_key(path: Path, key: ec.EllipticCurvePrivateKey, settings: Settings | None = None) -> None:
    """Private keys are stored PKCS#8-encrypted (passphrase derived from the master key), 0600."""
    vault.write_private(
        path,
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(_passphrase(settings)),
        ),
    )


def signing_key_pem(settings: Settings | None = None) -> bytes:
    """Decrypted leaf private key, in memory only, for the C2PA signer."""
    cfg = settings or get_settings()
    key = serialization.load_pem_private_key((cfg.keys_dir / "key.pem").read_bytes(), password=_passphrase(cfg))
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def _encrypt_legacy_keys(cfg: Settings) -> None:
    """Re-encrypt plaintext private keys left by older versions."""
    for name in ("key.pem", "rootCA_key.pem"):
        path = cfg.keys_dir / name
        data = path.read_bytes()
        if b"ENCRYPTED" in data:
            continue
        key = serialization.load_pem_private_key(data, password=None)
        _write_key(path, key, cfg)  # type: ignore[arg-type]
        logger.warning("Plaintext private key %s encrypted at rest", name)


def _write_cert(path: Path, cert: x509.Certificate) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def pki_is_complete(keys_dir: Path) -> bool:
    """Return True when all expected PEM files exist and are non-empty."""
    return all((keys_dir / name).is_file() and (keys_dir / name).stat().st_size > 0 for name in REQUIRED_FILES)


def _issue_leaf(root_key: ec.EllipticCurvePrivateKey, root_cert: x509.Certificate) -> Tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    """New sealer key + certificate signed by the root."""
    now = datetime.datetime.now(datetime.timezone.utc)
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(_name("KRAKEN Sealer Node"))
        .issuer_name(root_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + LEAF_VALIDITY)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        # C2PA signers must use an allowed EKU (codeSigning is rejected) and
        # carry SKI/AKI so the chain can be built by c2pa-rs.
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]),
            critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()), critical=False)
        .sign(root_key, hashes.SHA256())
    )
    return leaf_key, leaf_cert


def generate_pki(keys_dir: Path | None = None, settings: Settings | None = None) -> Dict[str, Path]:
    """Generate Root CA + leaf certificate chain under ``keys_dir`` (private keys encrypted)."""
    settings = settings or get_settings()
    target = Path(keys_dir) if keys_dir is not None else settings.keys_dir
    target.mkdir(parents=True, exist_ok=True)
    key_cfg = settings.model_copy(update={"keys_dir": target})

    now = datetime.datetime.now(datetime.timezone.utc)
    validity_start = now - datetime.timedelta(days=1)
    _archive_root(target)

    root_key = ec.generate_private_key(ec.SECP256R1())
    root_subject = _name("KRAKEN Root CA")
    root_cert = (
        x509.CertificateBuilder()
        .subject_name(root_subject)
        .issuer_name(root_subject)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(validity_start)
        .not_valid_after(now + ROOT_VALIDITY)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()), critical=False)
        .sign(root_key, hashes.SHA256())
    )

    leaf_key, leaf_cert = _issue_leaf(root_key, root_cert)

    paths = {
        "rootCA_key": target / "rootCA_key.pem",
        "rootCA_cert": target / "rootCA_cert.pem",
        "key": target / "key.pem",
        "cert": target / "cert.pem",
        "fullchain": target / "fullchain.pem",
    }
    _write_key(paths["rootCA_key"], root_key, key_cfg)
    _write_cert(paths["rootCA_cert"], root_cert)
    _write_leaf(target, leaf_key, leaf_cert, root_cert, key_cfg)
    logger.info("PKI generated under %s", target)
    return paths


def _write_leaf(target: Path, leaf_key: ec.EllipticCurvePrivateKey, leaf_cert: x509.Certificate,
                root_cert: x509.Certificate, settings: Settings) -> None:
    _write_key(target / "key.pem", leaf_key, settings)
    _write_cert(target / "cert.pem", leaf_cert)
    (target / "fullchain.pem").write_bytes(
        leaf_cert.public_bytes(serialization.Encoding.PEM) + b"\n" + root_cert.public_bytes(serialization.Encoding.PEM)
    )


def _fingerprint(cert: x509.Certificate) -> str:
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def _archive_root(target: Path) -> None:
    """Keep the public certificate of a root about to be replaced (old proofs stay verifiable)."""
    current = target / "rootCA_cert.pem"
    try:
        cert = x509.load_pem_x509_certificate(current.read_bytes())
    except Exception:
        return
    archive = target / ARCHIVED_ROOTS
    archive.mkdir(parents=True, exist_ok=True)
    _write_cert(archive / f"{_fingerprint(cert)[:16]}.pem", cert)


def _expires_soon(path: Path, margin: datetime.timedelta) -> bool:
    try:
        cert = x509.load_pem_x509_certificate(path.read_bytes())
    except Exception:
        return True
    return cert.not_valid_after_utc - datetime.datetime.now(datetime.timezone.utc) < margin


def renew_leaf(settings: Settings | None = None) -> None:
    """Issue a new sealer certificate from the existing root (same trust anchor)."""
    cfg = settings or get_settings()
    root_key = serialization.load_pem_private_key((cfg.keys_dir / "rootCA_key.pem").read_bytes(), password=_passphrase(cfg))
    root_cert = x509.load_pem_x509_certificate((cfg.keys_dir / "rootCA_cert.pem").read_bytes())
    leaf_key, leaf_cert = _issue_leaf(root_key, root_cert)  # type: ignore[arg-type]
    _write_leaf(cfg.keys_dir, leaf_key, leaf_cert, root_cert, cfg)
    logger.warning("Sealer certificate renewed (valid until %s)", leaf_cert.not_valid_after_utc.date())


def trusted_roots(settings: Settings | None = None) -> List[x509.Certificate]:
    """Current root + archived roots: the only anchors a KRAKEN proof may chain to."""
    cfg = settings or get_settings()
    paths = [cfg.keys_dir / "rootCA_cert.pem", *sorted((cfg.keys_dir / ARCHIVED_ROOTS).glob("*.pem"))]
    roots = []
    for path in paths:
        try:
            roots.append(x509.load_pem_x509_certificate(path.read_bytes()))
        except Exception:
            continue
    return roots


def trusted_roots_pem(settings: Settings | None = None) -> str:
    return "".join(c.public_bytes(serialization.Encoding.PEM).decode() for c in trusted_roots(settings))


def leaf_is_c2pa_compatible(keys_dir: Path) -> bool:
    """True if the leaf certificate has a C2PA-allowed EKU and key identifiers."""
    try:
        cert = x509.load_pem_x509_certificate((keys_dir / "cert.pem").read_bytes())
        ekus = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
        cert.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier)
    except Exception:
        return False
    return ExtendedKeyUsageOID.CODE_SIGNING not in ekus


def ensure_pki(settings: Settings | None = None) -> Path:
    """Create the PKI on first boot, or regenerate a pre-C2PA-compatible one."""
    cfg = settings or get_settings()
    cfg.keys_dir.mkdir(parents=True, exist_ok=True)
    if not pki_is_complete(cfg.keys_dir):
        logger.warning("Incomplete PKI in %s — generating a local Root CA + leaf", cfg.keys_dir)
        generate_pki(cfg.keys_dir, cfg)
    elif not leaf_is_c2pa_compatible(cfg.keys_dir):
        logger.warning("Leaf certificate rejected by C2PA (EKU/SKI) — regenerating the local PKI")
        generate_pki(cfg.keys_dir, cfg)
    elif _expires_soon(cfg.keys_dir / "rootCA_cert.pem", LEAF_VALIDITY):
        # The root must outlive the leaf it signs: new root, old one archived (proofs stay verifiable).
        logger.warning("Root CA expires within a year — generating a new root (previous one archived)")
        generate_pki(cfg.keys_dir, cfg)
    else:
        _encrypt_legacy_keys(cfg)
        if _expires_soon(cfg.keys_dir / "cert.pem", LEAF_RENEW_BEFORE):
            renew_leaf(cfg)
        logger.info("PKI present in %s (private keys encrypted at rest)", cfg.keys_dir)
    return cfg.keys_dir


def leaf_fingerprint_sha256(settings: Settings | None = None) -> str:
    """SHA-256 fingerprint of the leaf (sealer) certificate (hex)."""
    return _file_fingerprint((settings or get_settings()).keys_dir / "cert.pem")


def root_fingerprint_sha256(settings: Settings | None = None) -> str:
    """SHA-256 fingerprint of the current root certificate (hex): the trust anchor to publish."""
    return _file_fingerprint((settings or get_settings()).keys_dir / "rootCA_cert.pem")


def _file_fingerprint(path: Path) -> str:
    if not path.is_file():
        return ""
    return _fingerprint(x509.load_pem_x509_certificate(path.read_bytes()))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    generate_pki()
