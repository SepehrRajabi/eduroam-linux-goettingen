"""Exit codes and the exception used to stop the installer."""

from enum import IntEnum


class ExitCode(IntEnum):
    """Process exit codes returned by the installer."""

    SUCCESS = 0
    ABORTED = 1
    CERTIFICATE_ERROR = 2
    DBUS_ERROR = 3


class InstallerError(Exception):
    """Stop the installer, optionally telling the user why.

    Attributes:
        message: Text printed before exiting; empty if the user chose to leave.
        exit_code: Process exit code to finish with.
    """

    def __init__(
        self, message: str = "", exit_code: ExitCode = ExitCode.ABORTED
    ) -> None:
        """Store the message and exit code."""
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
