"""Writing binary system files."""

import stat
from pathlib import Path

from src import privileged
from src.messages import MESSAGES

from .conftest import FakeSession

CONTENT = b"\x00\x01binary\xff\n"


def test_direct_write(tmp_path: Path) -> None:
    """Writable targets are written without elevated rights, with their mode."""
    target = tmp_path / "var" / "lib" / "user.p12"
    privileged.write_system_bytes(target, CONTENT, MESSAGES)
    assert target.read_bytes() == CONTENT
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    privileged.write_system_bytes(target, b"new", MESSAGES, mode=0o644)
    assert target.read_bytes() == b"new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_elevated_write(tmp_path: Path, privileged_session: FakeSession) -> None:
    """Unwritable targets go through the privileged session unchanged."""
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    target = locked / "user.p12"
    privileged.write_system_bytes(target, CONTENT, MESSAGES)
    assert privileged_session.written() == {str(target): CONTENT}
    assert privileged_session.requests[0]["mode"] == 0o600
