"""The interface every way of configuring the network implements."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Self

from ..certificates import CaStore
from ..config import Profile
from ..credentials import Credentials
from ..messages import Messages
from ..paths import InstallerPaths
from ..system import running_processes
from ..ui import UserInterface


class BackendChoice(StrEnum):
    """Values accepted by the ``--backend`` option."""

    NETWORK_MANAGER = "networkmanager"
    CONNMAN = "connman"
    IWD = "iwd"
    WPA_SUPPLICANT = "wpa_supplicant"
    IWD_FILE = "iwd-file"
    WPA_SUPPLICANT_FILE = "wpa_supplicant-file"


@dataclass(frozen=True, slots=True, kw_only=True)
class InstallRequest:
    """Everything a backend needs to configure the connections.

    Attributes:
        profile: Connections to configure.
        credentials: What to authenticate with.
        ca: Installed CA certificates.
        paths: The installer directory and its files.
        ui: The user interface, for backends which need to ask something.
        messages: Texts to show.
        wired: Also configure 802.1X on wired interfaces.
    """

    profile: Profile
    credentials: Credentials
    ca: CaStore
    paths: InstallerPaths
    ui: UserInterface
    messages: Messages
    wired: bool = False


class Backend(ABC):
    """A way of configuring the eduroam connections.

    Attributes:
        choice: How ``--backend`` names this backend.
        label: Human-readable name, used in messages.
        service: Process name of the network service configured, as it appears in
            ``/proc/<pid>/comm``; ``None`` for backends which only write files.
        supports_wired: Whether 802.1X on wired interfaces can be configured.
        needs_root: Whether installing writes files only root may write.
    """

    __slots__ = ()

    choice: ClassVar[BackendChoice]
    label: ClassVar[str]
    service: ClassVar[str | None] = None
    supports_wired: ClassVar[bool] = False
    needs_root: ClassVar[bool] = False

    @classmethod
    def is_running(cls, processes: frozenset[str] | None = None) -> bool:
        """Return whether the network service of this backend is running.

        Args:
            processes: Names of the running processes; read from ``/proc`` if
                omitted.
        """
        if cls.service is None:
            return False
        if processes is None:
            processes = running_processes()
        return cls.service in processes

    @classmethod
    @abstractmethod
    def connect(cls, messages: Messages) -> Self | None:
        """Prepare to configure the network service.

        Args:
            messages: Texts to show.

        Returns:
            The backend, or ``None`` if it cannot be used here. The reason has
            then been printed.
        """

    @property
    def prefers_ca_directory(self) -> bool:
        """Return whether CA certificates should be installed as a hashed directory."""
        return False

    @abstractmethod
    def install(self, request: InstallRequest) -> str | None:
        """Configure the connections described by ``request``.

        Returns:
            Instructions for the user to follow afterwards, if any.

        Raises:
            InstallerError: If the installation fails.
        """
