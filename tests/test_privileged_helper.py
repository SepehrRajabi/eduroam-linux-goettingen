"""The real helper process, started through ``env`` instead of pkexec."""

import base64
import json
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from src import privileged
from src.errors import ExitCode, InstallerError
from src.messages import MESSAGES
from src.privileged_helper import HELPER_FLAG, handle

#: The real session start, captured before the test-wide ban replaces it.
START_SESSION = privileged._start_session

PROJECT_ROOT = Path(__file__).parent.parent


@pytest.fixture
def real_session(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[privileged.PrivilegedSession]:
    """Start the real helper as the current user."""
    monkeypatch.setattr(privileged, "_elevator", lambda target, messages: "env")
    session = START_SESSION(Path("/"), MESSAGES)
    yield session
    session.close()


def test_session_over_pipes(
    real_session: privileged.PrivilegedSession, tmp_path: Path
) -> None:
    """Writes, reads, removals and commands work through the real helper."""
    target = tmp_path / "dir" / "conf"
    data = b"line one\n\0binary\xff"
    write = {
        "op": "write",
        "path": str(target),
        "mode": 0o640,
        "data": base64.b64encode(data).decode(),
    }
    assert real_session.request(write) == {"ok": True}
    assert target.read_bytes() == data
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert [path.name for path in target.parent.iterdir()] == ["conf"]

    read = real_session.request({"op": "read", "path": str(target)})
    assert base64.b64decode(read["data"]) == data
    run = real_session.request({"op": "run", "argv": ["echo", "hello"]})
    assert run == {"ok": True, "returncode": 0, "stdout": "hello\n"}
    assert real_session.request({"op": "remove", "path": str(target)}) == {"ok": True}
    assert not target.exists()
    missing = real_session.request({"op": "read", "path": str(target)})
    assert missing == {"ok": True, "data": None}


def test_refused_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the elevating program fails, the installer stops."""
    monkeypatch.setattr(privileged, "_elevator", lambda target, messages: "false")
    with pytest.raises(InstallerError) as raised:
        START_SESSION(Path("/etc/x"), MESSAGES)
    assert raised.value.exit_code is ExitCode.PRIVILEGES_UNAVAILABLE


@pytest.mark.parametrize(
    "request_",
    [
        {"op": "write", "path": "relative", "mode": 0o600, "data": ""},
        {"op": "write", "path": "/x", "mode": 0o600, "data": "not base64!"},
        {"op": "read"},
        {"op": "format", "path": "/"},
    ],
)
def test_bad_requests(request_: dict[str, object]) -> None:
    """Invalid requests get an error response instead of crashing the helper."""
    assert handle(request_)["ok"] is False


def test_frozen_executable_flag() -> None:
    """``main.py --privileged-helper`` serves requests, for frozen executables."""
    result = subprocess.run(
        [sys.executable, "main.py", HELPER_FLAG],
        input=b"",
        capture_output=True,
        check=True,
        cwd=PROJECT_ROOT,
    )
    assert json.loads(result.stdout) == {"ready": True}
