"""Choosing the backend from the command line or the running services."""

import pytest

from src.backends import BackendChoice, select_backend
from src.backends.iwd import IwdFileBackend
from src.backends.network_manager import NetworkManagerBackend
from src.backends.wpa_supplicant import WpaSupplicantFileBackend
from src.errors import ExitCode, InstallerError
from src.messages import MESSAGES
from src.ui.silent import SilentUI

from .fake_dbus import FakeDbus

UI = SilentUI("test", MESSAGES)


class DecliningUI(SilentUI):
    """Answers no to every question."""

    def ask(self, question: str, prompt: str = "", default: bool | None = None) -> bool:
        """Decline."""
        return False


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        (BackendChoice.IWD_FILE, IwdFileBackend),
        (BackendChoice.WPA_SUPPLICANT_FILE, WpaSupplicantFileBackend),
    ],
)
def test_file_backends_are_always_available(
    choice: BackendChoice, expected: type
) -> None:
    """File output needs no running service."""
    assert isinstance(select_backend(choice, UI, MESSAGES, frozenset()), expected)


def test_requested_backend_must_be_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicitly requested but unusable backend stops the installer."""
    monkeypatch.setattr("src.backends.network_manager.dbus", None)
    with pytest.raises(InstallerError) as raised:
        select_backend(BackendChoice.NETWORK_MANAGER, UI, MESSAGES, frozenset())
    assert raised.value.exit_code is ExitCode.BACKEND_UNAVAILABLE


def test_network_manager_detected(fake_dbus: FakeDbus) -> None:
    """NetworkManager wins over the wpa_supplicant it runs underneath."""
    processes = frozenset({"NetworkManager", "wpa_supplicant", "iwd"})
    backend = select_backend(None, UI, MESSAGES, processes)
    assert isinstance(backend, NetworkManagerBackend)


def test_network_manager_tried_when_nothing_detected(fake_dbus: FakeDbus) -> None:
    """With nothing visible in /proc, NetworkManager is tried as before."""
    backend = select_backend(None, UI, MESSAGES, frozenset())
    assert isinstance(backend, NetworkManagerBackend)


def test_unusable_service_falls_back_to_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the running manager cannot be used, a wpa_supplicant file is offered."""
    monkeypatch.setattr("src.backends.network_manager.dbus", None)
    processes = frozenset({"NetworkManager", "wpa_supplicant"})
    backend = select_backend(None, UI, MESSAGES, processes)
    assert isinstance(backend, WpaSupplicantFileBackend)


def test_declined_fallback_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    """Declining the wpa_supplicant file stops the installer."""
    monkeypatch.setattr("src.backends.network_manager.dbus", None)
    with pytest.raises(InstallerError) as raised:
        select_backend(None, DecliningUI("test", MESSAGES), MESSAGES, frozenset())
    assert raised.value.exit_code is ExitCode.ABORTED
