"""Non-interactive operation for ``--silent``."""

from pathlib import Path
from typing import ClassVar

from .base import UserInterface


class SilentUI(UserInterface):
    """Accepts every question and shows nothing."""

    silent: ClassVar[bool] = True

    def ask(self, question: str, prompt: str = "", default: bool | None = None) -> bool:
        """Answer yes."""
        return True

    def show_info(self, text: str) -> None:
        """Do nothing."""

    def alert(self, text: str) -> None:
        """Do nothing."""

    def prompt_text(
        self, prompt: str, *, hidden: bool = False, initial: str = ""
    ) -> str:
        """Refuse, as credentials come from the command line in silent mode."""
        raise NotImplementedError

    def select_p12_file(self) -> Path | None:
        """Refuse, as the file comes from the command line in silent mode."""
        raise NotImplementedError
