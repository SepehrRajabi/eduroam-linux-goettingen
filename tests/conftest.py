"""Shared fixtures: isolation from the real home directory, and a fake D-Bus."""

import base64
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from src import privileged
from src.backends import network_manager
from src.config import GOETTINGEN, Profile
from src.messages import Messages

from .fake_dbus import FakeDbus


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``HOME`` and ``XDG_CONFIG_HOME`` into a temporary directory.

    Every test gets this, so that nothing ever writes to the real
    ``~/.config/cat_installer``. The working directory is a fresh one too.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return home


def _refuse_elevation(target: Path, messages: Messages) -> None:
    """Fail the test instead of asking for administrator rights."""
    msg = f"test tried to gain administrator rights for {target}"
    raise AssertionError(msg)


@pytest.fixture(autouse=True)
def no_real_elevation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make sure no test runs pkexec or sudo, and none shares a session."""
    monkeypatch.setattr(privileged, "_session", None)
    monkeypatch.setattr(privileged, "_start_session", _refuse_elevation)


@dataclass
class FakeSession:
    """A privileged session that records requests instead of carrying them out.

    Files live in :attr:`files`; commands answer with ``command_output``.

    Attributes:
        requests: Every request received, in order.
        files: Content of the files written as root, by path.
        commands: The ``argv`` of every ``run`` request.
        command_output: Standard output reported for commands.
        command_status: Exit status reported for commands.
    """

    requests: list[dict[str, Any]] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)
    commands: list[list[str]] = field(default_factory=list)
    command_output: str = "OK\n"
    command_status: int = 0

    def request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Record ``request`` and answer it."""
        self.requests.append(request)
        match request["op"]:
            case "run":
                self.commands.append(list(request["argv"]))
                return {
                    "ok": True,
                    "returncode": self.command_status,
                    "stdout": self.command_output,
                }
            case "write":
                self.files[request["path"]] = base64.b64decode(request["data"])
            case "read":
                data = self.files.get(request["path"])
                encoded = None if data is None else base64.b64encode(data).decode()
                return {"ok": True, "data": encoded}
            case "remove":
                self.files.pop(request["path"], None)
        return {"ok": True}

    def written(self) -> dict[str, bytes]:
        """Return the files written as root, by path."""
        return self.files

    def close(self) -> None:
        """Do nothing, as there is no process."""


@pytest.fixture
def privileged_session(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    """Install an already authenticated, in-process privileged session."""
    session = FakeSession()
    monkeypatch.setattr(privileged, "_session", session)
    return session


@pytest.fixture
def profile() -> Profile:
    """Return the Göttingen profile."""
    return GOETTINGEN


@pytest.fixture
def fake_dbus(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeDbus]:
    """Replace dbus-python with an in-memory NetworkManager."""
    fake = FakeDbus()
    monkeypatch.setattr(network_manager, "dbus", fake)
    yield fake
