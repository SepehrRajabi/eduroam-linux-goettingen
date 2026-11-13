"""iNet wireless daemon (iwd) configuration.

iwd keeps one file per known network in its state directory, normally
``/var/lib/iwd``, and watches that directory: files created, changed, deleted or
renamed into it take effect without restarting iwd (see iwd.network(5)). System
files are written to a temporary ``.<name>.<random>.tmp`` and renamed over the
target; iwd ignores the temporary file, as its name does not end in a security
type such as ``.8021x``, and reads the renamed one as a created network.
"""

import logging
import os
import shutil
import string
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Self

from ..config import EapMethod, Profile
from ..credentials import Credentials
from ..messages import Messages
from ..paths import write_private_file
from ..privileged import remove_system_file, write_system_file
from ..system import running_processes
from .base import Backend, BackendChoice, InstallRequest

logger = logging.getLogger(__name__)

#: Where iwd keeps its network files unless ``$STATE_DIRECTORY`` says otherwise.
IWD_DIRECTORY = Path("/var/lib/iwd")
#: Locations of the iwd daemon, which is not on ``PATH``.
IWD_PROGRAMS: tuple[Path, ...] = (
    Path("/usr/lib/iwd/iwd"),
    Path("/usr/libexec/iwd"),
)
#: Mount point of the proc file system, read to see whether iwd runs.
PROC = Path("/proc")

#: Characters iwd keeps in network filenames; others make it hex-encode the name.
_SAFE_SSID_CHARACTERS = frozenset(string.ascii_letters + string.digits + "-_ ")
#: Extension of the files describing 802.1X (WPA-Enterprise) networks.
_EAP_EXTENSION = "8021x"


def network_filename(ssid: str, extension: str = _EAP_EXTENSION) -> str:
    """Return the name iwd expects for the file of a network.

    As in iwd.network(5): an SSID made of ASCII letters, digits, spaces,
    underscores and minus signs is used verbatim. Any other SSID is written as
    an equal sign followed by the lower-case hex encoding of its UTF-8 bytes.

    Args:
        ssid: Network name.
        extension: Security type: ``8021x``, ``psk`` or ``open``.
    """
    if ssid and all(character in _SAFE_SSID_CHARACTERS for character in ssid):
        name = ssid
    else:
        name = "=" + ssid.encode("utf-8").hex()
    return f"{name}.{extension}"


def escape_value(value: str) -> str:
    r"""Escape a string value for an iwd settings file.

    iwd reads values to the end of the line, drops leading blanks and decodes
    ``\s``, ``\t``, ``\n``, ``\r`` and ``\\``; any other backslash sequence
    makes the value unreadable. This mirrors ``escape_value`` of ell, the library
    iwd uses: backslashes and line breaks are always escaped, spaces and tabs
    only before the first other character.
    """
    escaped = []
    leading = True
    for character in value:
        match character:
            case " " if leading:
                escaped.append("\\s")
            case "\t" if leading:
                escaped.append("\\t")
            case " " | "\t":
                escaped.append(character)
            case "\n":
                escaped.append("\\n")
            case "\r":
                escaped.append("\\r")
            case "\\":
                escaped.append("\\\\")
            case _:
                escaped.append(character)
        if character not in " \t":
            leading = False
    return "".join(escaped)


def _pwd_config(credentials: Credentials) -> str:
    """Return the configuration for EAP-PWD."""
    return f"""
[Security]
EAP-Method=PWD
EAP-Identity={escape_value(credentials.username)}
EAP-Password={escape_value(credentials.password)}

[Settings]
AutoConnect=true
"""


def _tunnelled_config(
    method: EapMethod, phase2_method: str, profile: Profile, credentials: Credentials
) -> str:
    """Return the configuration for a method with an inner, tunnelled exchange."""
    outer_identity = escape_value(profile.outer_identity(credentials.username))
    return f"""
[Security]
EAP-Method={method}
EAP-Identity={outer_identity}
EAP-{method}-CACert=embed:eduroam_ca_cert
EAP-{method}-ServerDomainMask={escape_value(profile.servers_cn)}
EAP-{method}-Phase2-Method={phase2_method}
EAP-{method}-Phase2-Identity={escape_value(credentials.username)}
EAP-{method}-Phase2-Password={escape_value(credentials.password)}

[Settings]
AutoConnect=true

[@pem@eduroam_ca_cert]
{profile.ca_certificate}
"""


def render_config(profile: Profile, credentials: Credentials) -> str:
    """Return the iwd 802.1X configuration for the EAP method of ``profile``.

    Raises:
        ValueError: If iwd support for the EAP method is not implemented.
    """
    match profile.eap_outer:
        case EapMethod.PWD:
            return _pwd_config(credentials)
        case EapMethod.PEAP:
            return _tunnelled_config(EapMethod.PEAP, "MSCHAPV2", profile, credentials)
        case EapMethod.TTLS:
            return _tunnelled_config(
                EapMethod.TTLS, "Tunneled-PAP", profile, credentials
            )
        case _:
            msg = f"Invalid connection type: {profile.eap_outer}"
            raise ValueError(msg)


def write_config(ssid: str, config: str, directory: Path | None = None) -> Path:
    """Write the iwd configuration of one SSID.

    If that is not permitted, for example because a previous run created the file
    as root, write it through ``pkexec``. The configuration goes over standard
    input so that the password does not appear on any command line.

    Args:
        ssid: Network name, which determines the filename.
        config: File content.
        directory: Where to write; defaults to the current directory.

    Returns:
        The file written.
    """
    path = (directory or Path.cwd()) / network_filename(ssid)
    logger.debug("writing: %s", path)
    try:
        write_private_file(path, config)
    except PermissionError:
        subprocess.run(
            ["pkexec", "install", "-m", "600", "/dev/stdin", path],
            input=config,
            encoding="utf-8",
            check=True,
        )
    return path


def write_iwd_configs(profile: Profile, credentials: Credentials) -> None:
    """Write the iwd configuration for all SSIDs of ``profile``."""
    config = render_config(profile, credentials)
    for ssid in profile.ssids:
        write_config(ssid, config)


def _is_installed() -> bool:
    """Return whether iwd seems to be installed, even if it is not running."""
    search_path = os.pathsep.join(
        (os.environ.get("PATH", os.defpath), "/usr/sbin", "/sbin")
    )
    if shutil.which("iwctl", path=search_path):
        return True
    return IWD_DIRECTORY.is_dir() or any(path.exists() for path in IWD_PROGRAMS)


@dataclass(slots=True)
class IwdFileBackend(Backend):
    """Writes iwd configuration files to the current directory for the user."""

    choice: ClassVar[BackendChoice] = BackendChoice.IWD_FILE
    label: ClassVar[str] = "iwd configuration file"

    @classmethod
    def connect(cls, messages: Messages) -> Self:
        """Return the backend, which is always usable."""
        return cls()

    def install(self, request: InstallRequest) -> None:
        """Write the files."""
        write_iwd_configs(request.profile, request.credentials)


@dataclass(slots=True)
class IwdBackend(Backend):
    """Installs the configuration into the state directory of iwd, as root.

    iwd notices new files in that directory by itself, so nothing needs to be
    restarted while it runs.

    Attributes:
        directory: The iwd state directory holding the network files.
        running: Whether iwd was seen running; if not, the user is told to start
            it.
    """

    choice: ClassVar[BackendChoice] = BackendChoice.IWD
    label: ClassVar[str] = "iwd"
    service: ClassVar[str | None] = "iwd"
    needs_root: ClassVar[bool] = True

    directory: Path = IWD_DIRECTORY
    running: bool = True

    @classmethod
    def connect(cls, messages: Messages) -> Self | None:
        """Check that iwd is installed, explaining on stdout if it is not.

        Returns:
            The backend, or ``None`` if iwd is neither running nor installed.
        """
        running = cls.is_running(running_processes(PROC))
        if not running and not _is_installed():
            print(messages.iwd_not_installed)
            return None
        logger.debug("iwd running: %s", running)
        return cls(directory=IWD_DIRECTORY, running=running)

    def install(self, request: InstallRequest) -> str | None:
        """Write one network file per SSID and delete those of removed SSIDs.

        Returns:
            How to start iwd, if it was not running.
        """
        messages = request.messages
        config = render_config(request.profile, request.credentials)
        for ssid in request.profile.ssids:
            write_system_file(
                self.directory / network_filename(ssid), config, messages, mode=0o600
            )
        for ssid in request.profile.del_ssids:
            remove_system_file(self.directory / network_filename(ssid), messages)
        return None if self.running else messages.iwd_start
