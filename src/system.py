"""Detection of the host system and its network interfaces."""

import logging
import os
import platform
import re
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_IGNORED_INTERFACE = re.compile(
    r"\b(lo|wl\w+|docker\d+|virbr\d+|tun\d+|br-\w+|veth\w+)\b"
)
_WIRED_INTERFACE = re.compile(r"\b(eth|en[opsx]|eno|enp|ens)\d", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SystemInfo:
    """Description of the host, logged to help debugging."""

    system: str
    release: str
    version: str
    desktop: str


def detect_desktop_environment() -> str:
    """Return the desktop in use: ``kde``, ``gnome``, ``xfce`` or ``generic``."""
    if os.environ.get("KDE_FULL_SESSION") == "true":
        return "kde"
    if os.environ.get("GNOME_DESKTOP_SESSION_ID"):
        return "gnome"
    try:
        result = subprocess.run(
            ["xprop", "-root", "_DT_SAVE_MODE"],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
    except OSError:
        return "generic"
    return "xfce" if ' = "xfce4"' in result.stdout else "generic"


def get_system_info() -> SystemInfo:
    """Return a description of the operating system and desktop."""
    system, release, version = platform.system_alias(
        platform.system(), platform.release(), platform.version()
    )
    return SystemInfo(system, release, version, detect_desktop_environment())


def has_wired_interface() -> bool:
    """Return whether the machine has a physical Ethernet interface."""
    try:
        result = subprocess.run(
            ["ip", "-o", "link", "show"],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
    except OSError:
        logger.debug("cannot run ip, assuming there is no wired interface")
        return False
    return any(
        _WIRED_INTERFACE.search(line) and not _IGNORED_INTERFACE.search(line)
        for line in result.stdout.splitlines()
    )
