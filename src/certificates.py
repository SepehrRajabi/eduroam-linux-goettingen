"""Installing the CA certificates and reading personal certificates."""

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509 import Certificate
from cryptography.x509.oid import NameOID

from .errors import ExitCode, InstallerError
from .messages import Messages
from .paths import InstallerPaths

logger = logging.getLogger(__name__)

_PEM_CERTIFICATE = re.compile(
    r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.DOTALL
)


class InvalidP12Error(Exception):
    """A personal certificate file could not be decrypted."""


@dataclass(frozen=True, slots=True)
class CaStore:
    """Where the CA certificates were installed.

    Attributes:
        path: The certificate file, or the directory of hashed certificates.
        is_directory: Whether ``path`` is a directory.
    """

    path: Path
    is_directory: bool = False


def save_ca(
    ca_certificate: str,
    paths: InstallerPaths,
    messages: Messages,
    *,
    prefer_directory: bool = False,
) -> CaStore:
    """Install the CA certificates.

    A single bundle file is used, unless ``prefer_directory`` is set and there are
    several certificates. That is the workaround for a NetworkManager bug which
    needs one hashed file per certificate. The bundle is also the fallback if
    hashing fails.

    Args:
        ca_certificate: PEM encoded CA certificates.
        paths: Installation directories.
        messages: Text for the error raised on failure.
        prefer_directory: Install one hashed file per certificate if possible.

    Raises:
        InstallerError: If ``ca_certificate`` holds no certificate.
    """
    certificates = _PEM_CERTIFICATE.findall(ca_certificate)
    if not certificates:
        raise InstallerError(messages.cert_error, ExitCode.CERTIFICATE_ERROR)
    if (
        prefer_directory
        and len(certificates) > 1
        and _save_hashed_directory(certificates, paths.ca_directory)
    ):
        return CaStore(paths.ca_directory, is_directory=True)
    logger.debug("saving cert")
    paths.ca_file.write_text(f"{ca_certificate}\n", encoding="utf-8")
    return CaStore(paths.ca_file)


def _save_hashed_directory(certificates: list[str], directory: Path) -> bool:
    """Write one file per certificate and hash them with ``openssl rehash``.

    Returns:
        Whether the hash links exist, which is what makes the directory usable.
    """
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for old_file in directory.iterdir():
        old_file.unlink()
    for index, certificate in enumerate(certificates):
        (directory / f"ca-{index}.pem").write_text(f"{certificate}\n", encoding="utf-8")
    try:
        subprocess.run(
            ["openssl", "rehash", directory], capture_output=True, check=False
        )
    except OSError:
        return False
    return any(entry.is_symlink() for entry in directory.iterdir())


def read_p12_identity(path: Path, password: str) -> str | None:
    """Decrypt a personal certificate file and return the identity it names.

    Returns:
        The common name of the certificate, else its email address, else ``None``.

    Raises:
        InvalidP12Error: If the password is wrong or the file is malformed.
    """
    try:
        _, certificate, _ = pkcs12.load_key_and_certificates(
            path.read_bytes(), password.encode("utf-8") or None
        )
    except ValueError as error:
        logger.debug("Incorrect password (%s).", error)
        raise InvalidP12Error from error
    return None if certificate is None else _identity_of(certificate)


def _identity_of(certificate: Certificate) -> str | None:
    """Return the common name or email address in a certificate subject."""
    for oid in (NameOID.COMMON_NAME, NameOID.EMAIL_ADDRESS):
        attributes = certificate.subject.get_attributes_for_oid(oid)
        if attributes:
            return str(attributes[0].value)
    return None
