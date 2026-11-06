"""Plain terminal interface."""

import getpass
from pathlib import Path

from .base import P12_SUFFIXES, UserInterface


class TtyUI(UserInterface):
    """Talks to the user on standard input and output."""

    def ask(self, question: str, prompt: str = "", default: bool | None = None) -> bool:
        """Repeat the question until it is answered with yes or no."""
        yes = self.messages.yes[:1].upper()
        no = self.messages.no[:1].upper()
        print(f"\n-------\n{question}\n")
        text = f"{prompt} ({self.messages.yes}/{self.messages.no}) "
        if default is not None:
            text += f"[{yes if default else no}]"
        while True:
            answer = input(text)
            if not answer and default is not None:
                return default
            first_letter = answer[:1].upper()
            if first_letter == yes:
                return True
            if first_letter == no:
                return False

    def show_info(self, text: str) -> None:
        """Print the text."""
        print(text)

    def alert(self, text: str) -> None:
        """Print the text."""
        print(text)

    def prompt_text(
        self, prompt: str, *, hidden: bool = False, initial: str = ""
    ) -> str:
        """Read a line, ignoring ``initial`` as a terminal cannot pre-fill it."""
        read = getpass.getpass if hidden else input
        while True:
            value = read(f"{prompt}: ").strip()
            if value:
                return value

    def select_p12_file(self) -> Path | None:
        """Read a path, offering a default if the current directory has one match."""
        candidates = [
            path
            for path in Path().iterdir()
            if path.is_file() and path.name.endswith(P12_SUFFIXES)
        ]
        suggestion = candidates[0] if len(candidates) == 1 else None
        hint = f"[{suggestion}]" if suggestion else ""
        while True:
            value = input(f"{self.messages.p12_title}{hint}: ").strip()
            if suggestion and not value:
                return suggestion
            hint = ""
            suggestion = None
            path = Path(value)
            if value and path.is_file():
                return path
            print(self.messages.file_not_found)
