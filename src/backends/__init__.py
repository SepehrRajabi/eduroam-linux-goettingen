"""Backends configuring the network, and the choice between them."""

import logging

from ..errors import ExitCode, InstallerError
from ..messages import Messages
from ..system import running_processes
from ..ui import UserInterface
from .base import Backend, BackendChoice, InstallRequest
from .connman import ConnManBackend
from .iwd import IwdBackend, IwdFileBackend
from .network_manager import NetworkManagerBackend
from .wpa_supplicant import WpaSupplicantBackend, WpaSupplicantFileBackend

__all__ = ["Backend", "BackendChoice", "InstallRequest", "select_backend"]

logger = logging.getLogger(__name__)

#: Every backend, by the name ``--backend`` uses for it.
BACKENDS: dict[BackendChoice, type[Backend]] = {
    backend.choice: backend
    for backend in (
        NetworkManagerBackend,
        ConnManBackend,
        IwdBackend,
        WpaSupplicantBackend,
        IwdFileBackend,
        WpaSupplicantFileBackend,
    )
}

#: Network services in the order they are detected. A network manager comes
#: before the lower-level services it controls, since NetworkManager and ConnMan
#: both run iwd or wpa_supplicant underneath.
AUTO_DETECT_ORDER: tuple[type[Backend], ...] = (
    NetworkManagerBackend,
    ConnManBackend,
    IwdBackend,
    WpaSupplicantBackend,
)


def select_backend(
    choice: BackendChoice | None,
    ui: UserInterface,
    messages: Messages,
    processes: frozenset[str] | None = None,
) -> Backend:
    """Choose how to configure the network.

    A backend named on the command line must be usable. Otherwise the first
    running network service is used. If none is detected, NetworkManager is tried
    anyway, since ``/proc`` may hide other users' processes. If the chosen service
    cannot be used, the user may accept a wpa_supplicant file instead. The
    services below it are not tried, as the manager running them would overwrite
    their configuration.

    Args:
        choice: Backend requested by the user.
        ui: Where to ask about the fallback.
        messages: Texts to show.
        processes: Names of the running processes; read from ``/proc`` if omitted.

    Raises:
        InstallerError: If the requested backend cannot be used, or the user
            declines the fallback.
    """
    if choice is not None:
        backend = BACKENDS[choice].connect(messages)
        if backend is None:
            raise InstallerError(
                messages.backend_unavailable.format(BACKENDS[choice].label),
                ExitCode.BACKEND_UNAVAILABLE,
            )
        return backend

    if processes is None:
        processes = running_processes()
    running = [
        backend for backend in AUTO_DETECT_ORDER if backend.is_running(processes)
    ]
    candidate = running[0] if running else NetworkManagerBackend
    logger.debug("detected: %s, trying %s", running, candidate.label)
    backend = candidate.connect(messages)
    if backend is not None:
        return backend
    ui.ask_to_continue(messages.save_wpa_conf)
    return WpaSupplicantFileBackend()
