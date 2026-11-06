"""iNet wireless daemon (iwd) configuration file generation."""

import logging
import subprocess
from pathlib import Path

from ..config import EapMethod, Profile
from ..credentials import Credentials
from ..paths import write_private_file

logger = logging.getLogger(__name__)


def _pwd_config(credentials: Credentials) -> str:
    """Return the configuration for EAP-PWD."""
    return f"""
[Security]
EAP-Method=PWD
EAP-Identity={credentials.username}
EAP-Password={credentials.password}

[Settings]
AutoConnect=true
"""


def _tunnelled_config(
    method: EapMethod, phase2_method: str, profile: Profile, credentials: Credentials
) -> str:
    """Return the configuration for a method with an inner, tunnelled exchange."""
    return f"""
[Security]
EAP-Method={method}
EAP-Identity={profile.outer_identity(credentials.username)}
EAP-{method}-CACert=embed:eduroam_ca_cert
EAP-{method}-ServerDomainMask={profile.servers_cn}
EAP-{method}-Phase2-Method={phase2_method}
EAP-{method}-Phase2-Identity={credentials.username}
EAP-{method}-Phase2-Password={credentials.password}

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
    path = (directory or Path.cwd()) / f"{ssid}.8021x"
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
