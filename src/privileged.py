"""Reading and writing system files, with administrator rights where needed.

Each function first tries without elevated rights, which succeeds when running as
root or when the target is writable, as in tests. Otherwise it hands the operation
to a privileged session: :mod:`.privileged_helper` started once through ``pkexec``,
or ``sudo`` if ``pkexec`` is missing. The user therefore authenticates at most once
per run. Both programs can ask for the password in the terminal, so the user
interface must not occupy the whole screen. File content always travels over a
pipe, never on a command line.
"""

import atexit
import base64
import json
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from .errors import ExitCode, InstallerError
from .messages import Messages
from .privileged_helper import HELPER_FLAG, READY, atomic_write

logger = logging.getLogger(__name__)

#: Programs which run a command as root, in order of preference.
_ELEVATORS = ("pkexec", "sudo")


@dataclass(slots=True)
class PrivilegedSession:
    """A running helper process with administrator rights.

    Attributes:
        process: The helper.
        requests: Its standard input, taking one JSON request per line.
        responses: Its standard output, giving one JSON response per line.
    """

    process: subprocess.Popen[bytes]
    requests: IO[bytes]
    responses: IO[bytes]

    def request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send one request and return the response.

        Raises:
            OSError: If the helper has gone away.
        """
        self.requests.write(json.dumps(request).encode() + b"\n")
        self.requests.flush()
        line = self.responses.readline()
        if not line:
            msg = "privileged helper exited"
            raise OSError(msg)
        response: dict[str, Any] = json.loads(line)
        return response

    def close(self) -> None:
        """Let the helper finish by closing its input."""
        self.requests.close()
        self.process.wait()


#: The session of this run, started on first use.
_session: PrivilegedSession | None = None


def _elevator(target: Path, messages: Messages) -> str:
    """Return the program used to gain administrator rights.

    Raises:
        InstallerError: If none is installed.
    """
    for program in _ELEVATORS:
        if shutil.which(program):
            return program
    raise InstallerError(
        messages.privileges_unavailable.format(target),
        ExitCode.PRIVILEGES_UNAVAILABLE,
    )


def _helper_command() -> list[str]:
    """Return the command which runs the helper, without the elevating program.

    An executable built by PyInstaller runs itself with :data:`HELPER_FLAG`;
    otherwise the interpreter runs the helper script in isolated mode, so that no
    environment variable or user directory can change what runs as root.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, HELPER_FLAG]
    helper = Path(__file__).with_name("privileged_helper.py")
    return [sys.executable, "-I", str(helper)]


def _start_session(target: Path, messages: Messages) -> PrivilegedSession:
    """Start the helper, which asks the user to authenticate.

    Raises:
        InstallerError: If rights are unavailable or refused.
    """
    command = [_elevator(target, messages), *_helper_command()]
    logger.debug("starting privileged session: %s", command)
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    if process.stdin is None or process.stdout is None:  # pragma: no cover
        msg = "pipes were not created"
        raise OSError(msg)
    session = PrivilegedSession(process, process.stdin, process.stdout)
    try:
        ready = json.loads(process.stdout.readline()) == READY
    except ValueError:
        ready = False
    if not ready:
        session.close()
        raise InstallerError(
            messages.privileges_refused.format(target),
            ExitCode.PRIVILEGES_UNAVAILABLE,
        )
    atexit.register(session.close)
    return session


def _privileged_request(
    request: dict[str, Any], target: Path, messages: Messages
) -> dict[str, Any]:
    """Carry out ``request`` as root, starting the session if needed.

    Raises:
        InstallerError: If rights are unavailable or refused, or the request fails.
    """
    global _session  # noqa: PLW0603 - one session per process, started lazily
    if _session is None:
        _session = _start_session(target, messages)
    try:
        response = _session.request(request)
    except (OSError, ValueError) as error:
        logger.debug("privileged session failed: %s", error)
        _session = None
        response = {"ok": False}
    if not response.get("ok"):
        logger.debug("privileged %s failed: %s", request["op"], response.get("error"))
        raise InstallerError(
            messages.privileges_refused.format(target),
            ExitCode.PRIVILEGES_UNAVAILABLE,
        )
    return response


def run_privileged(
    command: list[str], target: Path, messages: Messages, *, stdin: str | None = None
) -> str:
    """Run ``command`` as root and return its standard output.

    Args:
        command: The command, without the elevating program.
        target: The file affected, named in error messages.
        messages: Texts of the errors.
        stdin: Text sent to the command's standard input.

    Raises:
        InstallerError: If rights are unavailable or refused, or the command fails.
    """
    request: dict[str, Any] = {"op": "run", "argv": command}
    if stdin is not None:
        request["stdin"] = base64.b64encode(stdin.encode()).decode()
    response = _privileged_request(request, target, messages)
    if response["returncode"] != 0:
        raise InstallerError(
            messages.privileges_refused.format(target),
            ExitCode.PRIVILEGES_UNAVAILABLE,
        )
    output: str = response["stdout"]
    return output


def write_system_bytes(
    path: Path, content: bytes, messages: Messages, *, mode: int = 0o600
) -> None:
    """Replace ``path`` with ``content`` atomically, creating parent directories.

    The mode is set before any content is written, so a secret is never readable
    by others, and services watching the directory never see a partial file.

    Args:
        path: Absolute path of the file.
        content: Bytes to write.
        messages: Texts of the errors.
        mode: Permissions of the file.

    Raises:
        InstallerError: If administrator rights are needed but not obtained.
    """
    logger.debug("writing: %s", path)
    try:
        atomic_write(path, content, mode)
    except PermissionError:
        request = {
            "op": "write",
            "path": str(path),
            "mode": mode,
            "data": base64.b64encode(content).decode(),
        }
        _privileged_request(request, path, messages)


def write_system_file(
    path: Path, content: str, messages: Messages, *, mode: int = 0o600
) -> None:
    """Replace ``path`` with the UTF-8 text ``content``; see :func:`write_system_bytes`.

    Raises:
        InstallerError: If administrator rights are needed but not obtained.
    """
    write_system_bytes(path, content.encode("utf-8"), messages, mode=mode)


def read_system_file(path: Path, messages: Messages) -> str | None:
    """Return the content of ``path``, or ``None`` if it does not exist.

    Raises:
        InstallerError: If administrator rights are needed but not obtained.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except PermissionError:
        response = _privileged_request(
            {"op": "read", "path": str(path)}, path, messages
        )
    data = response["data"]
    return None if data is None else base64.b64decode(data).decode("utf-8")


def remove_system_file(path: Path, messages: Messages) -> None:
    """Delete ``path`` if it exists.

    Raises:
        InstallerError: If administrator rights are needed but not obtained.
    """
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        _privileged_request({"op": "remove", "path": str(path)}, path, messages)
