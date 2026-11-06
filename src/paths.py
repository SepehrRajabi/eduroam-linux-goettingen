"""Locations of the files written by the installer."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Self


@dataclass(frozen=True, slots=True)
class InstallerPaths:
    """The ``cat_installer`` directory and the files inside it."""

    root: Path

    @classmethod
    def default(cls) -> Self:
        """Return the paths under ``$XDG_CONFIG_HOME``, or ``~/.config`` if unset."""
        config_home = os.environ.get("XDG_CONFIG_HOME")
        base = Path(config_home) if config_home else Path.home() / ".config"
        return cls(base / "cat_installer")

    @property
    def ca_directory(self) -> Path:
        """Return the directory holding one file per CA certificate."""
        return self.root / "ca"

    @property
    def ca_file(self) -> Path:
        """Return the file holding all CA certificates."""
        return self.root / "ca.pem"

    @property
    def user_p12(self) -> Path:
        """Return the installed copy of the personal certificate."""
        return self.root / "user.p12"

    @property
    def wpa_conf(self) -> Path:
        """Return the generated wpa_supplicant configuration file."""
        return self.root / "cat_installer.conf"

    def create(self) -> None:
        """Create the directory tree, readable by the current user only."""
        # With parents=True, the mode applies to the last directory alone.
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.ca_directory.mkdir(mode=0o700, exist_ok=True)


def write_private_file(path: Path, content: str) -> None:
    """Write ``content`` to a file only its owner can read.

    Permissions are set before any content is written, so a secret is never
    readable by others, even when overwriting a file that was world-readable.
    """
    path.touch(mode=0o600)
    path.chmod(0o600)
    path.write_text(content, encoding="utf-8")
