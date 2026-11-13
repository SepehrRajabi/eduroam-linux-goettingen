"""The root side of a privileged session.

The installer starts this module once, through ``pkexec`` or ``sudo``, when it
first needs administrator rights. It then sends every privileged operation over
standard input, so the user authenticates only once. Each request and response is
one line of JSON; file content is base64 encoded, so secrets never appear on a
command line.

This file runs as a script with ``python -I``, so it must use the standard
library only and import nothing else from the installer.
"""

import base64
import contextlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, BinaryIO

#: Command line flag which makes a frozen executable act as this helper.
HELPER_FLAG = "--privileged-helper"

#: First line sent once the helper runs, i.e. once authentication succeeded.
READY = {"ready": True}


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    """Replace ``path`` with ``data`` in one step, creating parent directories.

    The content goes to a temporary file in the same directory, which gets its
    mode before any content is written and is then renamed over ``path``. Services
    watching the directory therefore never see a partly written file, and the
    temporary name ends in ``.tmp`` so that they ignore it.

    If ``path`` is a symbolic link, the file it points to is replaced, not the
    link. Missing directories are created readable by the owner only, which is
    what services such as iwd use for their own directories.

    Args:
        path: The file to write.
        data: Its new content.
        mode: Its permissions.
    """
    path = Path(os.path.realpath(path))
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        Path(temporary).replace(path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            Path(temporary).unlink()
        raise


def _absolute(request: dict[str, Any]) -> Path:
    """Return the path of a request, which must be absolute.

    Raises:
        ValueError: If it is relative.
    """
    path = Path(request["path"])
    if not path.is_absolute():
        msg = f"relative path: {path}"
        raise ValueError(msg)
    return path


def _write(request: dict[str, Any]) -> dict[str, Any]:
    """Replace a file: ``path``, ``mode`` and base64 ``data``."""
    data = base64.b64decode(request["data"], validate=True)
    atomic_write(_absolute(request), data, int(request["mode"]))
    return {"ok": True}


def _read(request: dict[str, Any]) -> dict[str, Any]:
    """Return the base64 content of ``path``, or ``None`` if it does not exist."""
    try:
        content = _absolute(request).read_bytes()
    except FileNotFoundError:
        return {"ok": True, "data": None}
    return {"ok": True, "data": base64.b64encode(content).decode()}


def _remove(request: dict[str, Any]) -> dict[str, Any]:
    """Delete ``path`` if it exists."""
    _absolute(request).unlink(missing_ok=True)
    return {"ok": True}


def _run(request: dict[str, Any]) -> dict[str, Any]:
    """Run ``argv``, feeding it the base64 ``stdin`` if given."""
    stdin = request.get("stdin")
    result = subprocess.run(
        [str(argument) for argument in request["argv"]],
        input=base64.b64decode(stdin, validate=True) if stdin is not None else None,
        stdout=subprocess.PIPE,
        check=False,
    )
    return {
        "ok": True,
        "returncode": result.returncode,
        "stdout": result.stdout.decode("utf-8", "replace"),
    }


#: Operations by the ``op`` of their requests.
_OPERATIONS = {"write": _write, "read": _read, "remove": _remove, "run": _run}


def handle(request: dict[str, Any]) -> dict[str, Any]:
    """Carry out one request and return the response.

    Requests have an ``op`` of ``write``, ``read``, ``remove`` or ``run``; see the
    functions implementing them. Binary values are base64 encoded. Every response
    has ``ok``, and ``error`` if that is false.
    """
    operation = _OPERATIONS.get(request.get("op", ""))
    if operation is None:
        return {"ok": False, "error": f"unknown operation: {request.get('op')}"}
    try:
        return operation(request)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}


def serve(stdin: BinaryIO, stdout: BinaryIO) -> int:
    """Answer requests until standard input closes.

    Returns:
        The exit status.
    """
    stdout.write(json.dumps(READY).encode() + b"\n")
    stdout.flush()
    for line in stdin:
        try:
            request = json.loads(line)
        except ValueError:
            response: dict[str, Any] = {"ok": False, "error": "malformed request"}
        else:
            response = handle(request)
        stdout.write(json.dumps(response).encode() + b"\n")
        stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve(sys.stdin.buffer, sys.stdout.buffer))
