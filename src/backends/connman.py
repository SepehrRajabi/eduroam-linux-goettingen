"""System configuration through ConnMan provisioning files.

ConnMan provisions networks from ``*.config`` files in its storage directory,
``/var/lib/connman`` (see connman-service.config(5) and ``doc/config-format.txt``
in the ConnMan sources). The files are GLib key files with one ``[service_*]``
section per network.

connmand watches that directory with inotify (``IN_CREATE``, ``IN_MODIFY``,
``IN_MOVED_TO`` and the removals), so no restart is needed. System files are
written to a temporary ``.<name>.<random>.tmp`` file and renamed into place;
ConnMan ignores names not ending in ``.config``, and loads a ``*.config`` file
renamed into the directory just like a created or modified one. On every such
load it removes the services provisioned from the file and provisions them again,
which may briefly interrupt a connection to them. Services of a section or file
which disappeared are removed. Networks the user saved by hand are not touched;
ConnMan offers no file based way to forget them.

connmand runs as root and may start before home directories are mounted, so the
CA certificate and the personal certificate are copied next to the file.
"""

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Self

from cryptography.hazmat.primitives.serialization import Encoding, pkcs12

from ..config import PASSWORD_METHODS, EapMethod, Profile
from ..credentials import Credentials
from ..errors import InstallerError
from ..messages import Messages
from ..privileged import write_system_bytes, write_system_file
from .base import Backend, BackendChoice, InstallRequest

logger = logging.getLogger(__name__)

#: Where ConnMan looks for provisioning files.
CONNMAN_DIRECTORY = Path("/var/lib/connman")

#: Name of the provisioning file and of the directory beside it holding the
#: certificates. The documentation allows only letters and digits in file names.
_NAME = "eduroamgwdg"

#: Outer EAP methods ConnMan can provision.
SUPPORTED_METHODS = frozenset({EapMethod.PEAP, EapMethod.TTLS, EapMethod.TLS})

_UNSAFE_GROUP_CHARACTERS = re.compile(r"[^A-Za-z0-9]+")


def escape_value(value: str) -> str:
    """Escape a string for a GLib key file, as ``g_key_file_set_string`` does.

    Backslashes, newlines and carriage returns are escaped everywhere, so a value
    can never end its line and start a new key or section. Leading spaces and tabs
    are escaped because the parser would drop them. Other whitespace, including
    trailing spaces, is kept as is by the parser.

    Raises:
        ValueError: If ``value`` contains a NUL character, which key files cannot
            hold.
    """
    if "\0" in value:
        raise ValueError("NUL character in key file value")
    escaped: list[str] = []
    leading = True
    for char in value:
        match char:
            case " " if leading:
                escaped.append(r"\s")
            case "\t" if leading:
                escaped.append(r"\t")
            case "\n":
                escaped.append(r"\n")
            case "\r":
                escaped.append(r"\r")
            case "\\":
                escaped.append(r"\\")
                leading = False
            case _:
                escaped.append(char)
                leading = False
    return "".join(escaped)


@dataclass(frozen=True, slots=True)
class ConnManFiles:
    """The files installed in the ConnMan storage directory.

    Attributes:
        directory: The ConnMan storage directory.
    """

    directory: Path

    @property
    def config(self) -> Path:
        """Return the provisioning file."""
        return self.directory / f"{_NAME}.config"

    @property
    def certificates(self) -> Path:
        """Return the directory holding the certificates.

        ConnMan ignores it: it only treats subdirectories holding a ``settings``
        file as saved services.
        """
        return self.directory / _NAME

    @property
    def ca(self) -> Path:
        """Return the CA certificates, PEM encoded."""
        return self.certificates / "ca.pem"

    @property
    def client_cert(self) -> Path:
        """Return the personal certificate, PEM encoded, without its key."""
        return self.certificates / "user.pem"

    @property
    def private_key(self) -> Path:
        """Return the personal certificate and key, as the PKCS#12 file imported."""
        return self.certificates / "user.p12"


def _group_names(ssids: tuple[str, ...]) -> Iterator[str]:
    """Return a distinct ``service_*`` section name for each SSID."""
    used: set[str] = set()
    for ssid in ssids:
        readable = _UNSAFE_GROUP_CHARACTERS.sub("_", ssid).strip("_") or "ssid"
        base = name = f"service_{readable}"
        counter = 2
        while name in used:
            name = f"{base}_{counter}"
            counter += 1
        used.add(name)
        yield name


def _eap_settings(
    profile: Profile, credentials: Credentials, files: ConnManFiles
) -> list[tuple[str, str]]:
    """Return the EAP settings shared by every network of ``profile``."""
    settings = [
        ("EAP", profile.eap_outer.lower()),
        ("CACertFile", str(files.ca)),
    ]
    if profile.servers_cn:
        settings.append(("DomainMatch", profile.servers_cn))
    elif profile.servers:
        settings.append(("AltSubjectMatch", ";".join(profile.servers)))
    settings.append(("Identity", credentials.username))
    if profile.eap_outer in PASSWORD_METHODS:
        settings += [
            ("AnonymousIdentity", profile.outer_identity(credentials.username)),
            ("Phase2", profile.eap_inner),
            ("Passphrase", credentials.password),
        ]
    elif profile.eap_outer is EapMethod.TLS:
        settings += [
            ("ClientCertFile", str(files.client_cert)),
            ("PrivateKeyFile", str(files.private_key)),
            ("PrivateKeyPassphrase", credentials.password),
        ]
    return settings


def render_config(
    profile: Profile, credentials: Credentials, files: ConnManFiles
) -> str:
    """Return the provisioning file for every SSID of ``profile``.

    SSIDs are written in hexadecimal, so that any SSID works.

    Raises:
        ValueError: If a value contains a NUL character.
    """
    sections = [
        [
            "[global]",
            "Name = eduroam",
            f"Description = {escape_value(profile.profile_name)}",
        ]
    ]
    eap = [
        f"{key} = {escape_value(value)}"
        for key, value in _eap_settings(profile, credentials, files)
    ]
    for group, ssid in zip(_group_names(profile.ssids), profile.ssids, strict=True):
        sections.append(
            [
                f"[{group}]",
                "Type = wifi",
                f"SSID = {ssid.encode('utf-8').hex()}",
                *eap,
            ]
        )
    return "\n\n".join("\n".join(lines) for lines in sections) + "\n"


def client_certificate_pem(p12: bytes, password: str, messages: Messages) -> str:
    """Return the personal certificate of a PKCS#12 file, PEM encoded.

    ConnMan passes ``ClientCertFile`` to wpa_supplicant as ``client_cert``, which
    must be PEM or DER. The key stays in the PKCS#12 file.

    Raises:
        InstallerError: If the file cannot be decrypted or holds no certificate.
    """
    try:
        _, certificate, _ = pkcs12.load_key_and_certificates(
            p12, password.encode("utf-8") or None
        )
    except ValueError as error:
        raise InstallerError(messages.incorrect_password) from error
    if certificate is None:
        raise InstallerError(messages.user_cert_missing)
    return certificate.public_bytes(Encoding.PEM).decode("ascii")


def _default_directory() -> Path:
    """Return the ConnMan storage directory, looked up when a backend is made."""
    return CONNMAN_DIRECTORY


@dataclass(slots=True)
class ConnManBackend(Backend):
    """Installs a provisioning file for ConnMan, which needs root.

    Attributes:
        directory: The ConnMan storage directory.
    """

    choice: ClassVar[BackendChoice] = BackendChoice.CONNMAN
    label: ClassVar[str] = "ConnMan"
    service: ClassVar[str | None] = "connmand"
    needs_root: ClassVar[bool] = True

    directory: Path = field(default_factory=_default_directory)

    @classmethod
    def connect(cls, messages: Messages) -> Self | None:
        """Return the backend, or ``None`` if ConnMan's directory is missing.

        connmand creates the directory when it first starts, so it is missing
        only if ConnMan is not installed. The reason is printed.
        """
        backend = cls()
        if not backend.directory.is_dir():
            print(messages.connman_missing.format(backend.directory))
            return None
        return backend

    def install(self, request: InstallRequest) -> str | None:
        """Install the certificates, then the provisioning file.

        The provisioning file comes last, so the files it names exist when
        ConnMan loads it.

        Returns:
            A note on the networks of ``profile.del_ssids``, which cannot be
            removed through a file, if there are any.

        Raises:
            InstallerError: If ConnMan cannot use the EAP method of the profile,
                or a file cannot be written.
        """
        profile, messages = request.profile, request.messages
        if profile.eap_outer not in SUPPORTED_METHODS:
            raise InstallerError(
                messages.connman_unsupported_method.format(profile.eap_outer)
            )
        files = ConnManFiles(self.directory)
        try:
            config = render_config(profile, request.credentials, files)
        except ValueError as error:
            raise InstallerError(messages.connman_invalid_value) from error

        ca = (
            profile.ca_certificate
            if request.ca.is_directory
            else request.ca.path.read_text(encoding="utf-8")
        )
        write_system_file(files.ca, ca, messages, mode=0o644)
        if profile.eap_outer is EapMethod.TLS:
            p12 = request.paths.user_p12.read_bytes()
            certificate = client_certificate_pem(
                p12, request.credentials.password, messages
            )
            write_system_file(files.client_cert, certificate, messages)
            write_system_bytes(files.private_key, p12, messages)
        write_system_file(files.config, config, messages)
        logger.debug("ConnMan provisioning file written: %s", files.config)

        if profile.del_ssids:
            return messages.connman_del_ssids.format(", ".join(profile.del_ssids))
        return None
