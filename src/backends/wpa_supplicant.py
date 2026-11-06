"""wpa_supplicant configuration file generation."""

import logging

from ..certificates import CaStore
from ..config import PASSWORD_METHODS, EapMethod, Profile
from ..credentials import Credentials
from ..paths import InstallerPaths, write_private_file

logger = logging.getLogger(__name__)


def network_block(
    ssid: str,
    profile: Profile,
    credentials: Credentials,
    ca: CaStore,
    paths: InstallerPaths,
) -> str:
    """Return the ``network`` block for one SSID."""
    lines = [
        f'ssid="{ssid}"',
        "key_mgmt=WPA-EAP",
        "pairwise=CCMP",
        "group=CCMP",
        f"eap={profile.eap_outer}",
        f'ca_cert="{ca.path}"',
        f'identity="{credentials.username}"',
        f'altsubject_match="{";".join(profile.servers)}"',
    ]
    if profile.eap_outer in PASSWORD_METHODS:
        lines += [
            f'phase2="auth={profile.eap_inner}"',
            f'password="{credentials.password}"',
            f'anonymous_identity="{profile.outer_identity(credentials.username)}"',
        ]
    elif profile.eap_outer is EapMethod.TLS:
        lines += [
            f'private_key_passwd="{credentials.password}"',
            f'private_key="{paths.user_p12}"',
        ]
    body = "\n".join(f"\t{line}" for line in lines)
    return f"network={{\n{body}\n}}\n"


def write_wpa_conf(
    profile: Profile,
    credentials: Credentials,
    ca: CaStore,
    paths: InstallerPaths,
) -> None:
    """Write the wpa_supplicant configuration for all SSIDs of ``profile``."""
    logger.debug("writing: %s", paths.wpa_conf)
    blocks = (
        network_block(ssid, profile, credentials, ca, paths) for ssid in profile.ssids
    )
    write_private_file(paths.wpa_conf, "".join(blocks))
