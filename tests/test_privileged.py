"""Writing system files, directly or through the privileged session."""

import stat
import sys
from pathlib import Path

import pytest

from src import privileged
from src.errors import ExitCode, InstallerError
from src.messages import MESSAGES

from .conftest import FakeSession


def _locked_directory(tmp_path: Path) -> Path:
    """Return a directory the current user cannot write."""
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    return locked


def test_direct_write_read_remove(tmp_path: Path) -> None:
    """Writable targets are handled without elevated rights."""
    target = tmp_path / "etc" / "service" / "conf"
    privileged.write_system_file(target, "secret", MESSAGES)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert privileged.read_system_file(target, MESSAGES) == "secret"
    privileged.write_system_file(target, "public", MESSAGES, mode=0o644)
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert privileged.read_system_file(target, MESSAGES) == "public"
    privileged.remove_system_file(target, MESSAGES)
    assert privileged.read_system_file(target, MESSAGES) is None
    assert list(target.parent.iterdir()) == []


def test_elevated_operations_share_one_session(
    tmp_path: Path, privileged_session: FakeSession
) -> None:
    """Unwritable targets go through the session, content over the pipe."""
    locked = _locked_directory(tmp_path)
    target = locked / "conf"
    privileged.write_system_file(target, "pa$$word", MESSAGES)
    privileged.write_system_bytes(locked / "user.p12", b"\0\xff", MESSAGES)
    privileged.run_privileged(["wpa_cli", "reconfigure"], target, MESSAGES)
    assert [request["op"] for request in privileged_session.requests] == [
        "write",
        "write",
        "run",
    ]
    assert privileged_session.written() == {
        str(target): b"pa$$word",
        str(locked / "user.p12"): b"\0\xff",
    }
    assert privileged_session.commands == [["wpa_cli", "reconfigure"]]


def test_failed_command(privileged_session: FakeSession, tmp_path: Path) -> None:
    """A command failing as root stops the installer."""
    privileged_session.command_status = 1
    with pytest.raises(InstallerError) as raised:
        privileged.run_privileged(["false"], tmp_path, MESSAGES)
    assert raised.value.exit_code is ExitCode.PRIVILEGES_UNAVAILABLE


def test_no_elevator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without pkexec or sudo the installer stops with a clear error."""
    monkeypatch.setattr("src.privileged.shutil.which", lambda name: None)
    with pytest.raises(InstallerError) as raised:
        privileged._elevator(tmp_path, MESSAGES)
    assert raised.value.exit_code is ExitCode.PRIVILEGES_UNAVAILABLE


def test_prefers_pkexec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pkexec is used if installed, sudo otherwise."""
    installed = {"pkexec", "sudo"}
    monkeypatch.setattr(
        "src.privileged.shutil.which", lambda name: name if name in installed else None
    )
    assert privileged._elevator(tmp_path, MESSAGES) == "pkexec"
    installed.remove("pkexec")
    assert privileged._elevator(tmp_path, MESSAGES) == "sudo"


def test_helper_command_is_isolated() -> None:
    """The helper script runs in isolated mode, or via a frozen executable."""
    command = privileged._helper_command()
    assert command[:2] == [sys.executable, "-I"]
    assert Path(command[2]).name == "privileged_helper.py"


def test_write_follows_symlink(tmp_path: Path) -> None:
    """A symlinked configuration keeps its link; the target is replaced."""
    real = tmp_path / "real.conf"
    real.write_text("old")
    link = tmp_path / "link.conf"
    link.symlink_to(real)
    privileged.write_system_file(link, "new", MESSAGES)
    assert link.is_symlink()
    assert real.read_text() == "new"


def test_new_directories_are_private(tmp_path: Path) -> None:
    """Directories created for a file are readable by their owner only."""
    target = tmp_path / "lib" / "iwd" / "eduroam.8021x"
    privileged.write_system_file(target, "x", MESSAGES)
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
