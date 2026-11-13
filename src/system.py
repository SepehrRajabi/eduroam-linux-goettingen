"""Detection of the host system and its network interfaces."""

import logging
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

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


def running_processes(proc: Path = Path("/proc")) -> frozenset[str]:
    """Return the names of the running processes.

    Names come from ``/proc/<pid>/comm``, which the kernel truncates to 15
    characters. Processes hidden by ``hidepid`` are missing, so an empty result
    means nothing could be detected rather than that nothing runs.

    Args:
        proc: Mount point of the proc file system.
    """
    names = set()
    for comm in proc.glob("[0-9]*/comm"):
        try:
            names.add(comm.read_text(encoding="utf-8").strip())
        except OSError:
            continue  # the process exited while we were looking
    return frozenset(names)


def process_command_lines(name: str, proc: Path = Path("/proc")) -> list[list[str]]:
    """Return the command lines of the running processes called ``name``.

    Processes are matched by ``/proc/<pid>/comm``; their arguments come from the
    NUL-separated ``/proc/<pid>/cmdline``. Processes which exit while being read,
    or whose command line is hidden, are left out.

    Args:
        name: Process name as it appears in ``comm``.
        proc: Mount point of the proc file system.

    Returns:
        One list of arguments per process, the program itself first, ordered by
        process ID.
    """
    command_lines = []
    pids = sorted(
        (entry for entry in proc.glob("[0-9]*") if entry.name.isdigit()),
        key=lambda entry: int(entry.name),
    )
    for pid in pids:
        try:
            if (pid / "comm").read_text(encoding="utf-8").strip() != name:
                continue
            raw = (pid / "cmdline").read_bytes()
        except OSError:
            continue
        arguments = raw.decode("utf-8", errors="surrogateescape").split("\0")
        if arguments and not arguments[-1]:
            arguments.pop()  # the command line ends with a NUL
        if arguments:
            command_lines.append(arguments)
    return command_lines


def wireless_interfaces(sys_class_net: Path = Path("/sys/class/net")) -> list[str]:
    """Return the names of the wireless network interfaces, sorted.

    An interface is wireless if sysfs shows its ``wireless`` directory (wireless
    extensions) or its ``phy80211`` link (any cfg80211 driver).

    Args:
        sys_class_net: The sysfs directory listing the network interfaces.
    """
    try:
        interfaces = list(sys_class_net.iterdir())
    except OSError:
        return []
    return sorted(
        interface.name
        for interface in interfaces
        if (interface / "wireless").exists() or (interface / "phy80211").exists()
    )
