"""Terminal interface built on Rich.

It stays line based on purpose: no full-screen mode, no alternate screen and no
live display, because ``pkexec`` or ``sudo`` may later ask for the administrator
password in the same terminal. Rich drops colours and styles by itself when the
output is not a terminal, ``NO_COLOR`` is set or ``TERM`` is ``dumb``.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from ..errors import InstallerError
from .base import P12_SUFFIXES, UserInterface

#: Style of the choices and defaults offered in prompts.
_CHOICE_STYLE = "bold magenta"
_DEFAULT_STYLE = "bold cyan"


def _default_console() -> Console:
    """Return a console on standard output that leaves plain text unhighlighted."""
    return Console(highlight=False)


def _unquote(value: str) -> str:
    """Remove one pair of matching quotes, as terminals add when a file is dropped."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


@dataclass
class RichUI(UserInterface):
    """Talks to the user in the terminal, one line at a time.

    Every text from the installer is shown literally: it is wrapped in
    :class:`~rich.text.Text`, so brackets and colons are never read as Rich
    markup or emoji codes.

    Attributes:
        console: Where output goes, and where prompts are shown.
        stream: Where visible answers are read from; standard input if ``None``.
            Hidden answers always go through :func:`getpass.getpass`.
    """

    console: Console = field(default_factory=_default_console)
    stream: TextIO | None = None

    def _read(self, prompt: Text, *, hidden: bool = False) -> str:
        """Show ``prompt`` and return the stripped answer.

        Args:
            prompt: Text in front of the cursor.
            hidden: Do not echo what is typed.

        Raises:
            InstallerError: If the input has ended, so no answer can come.
        """
        try:
            answer = self.console.input(prompt, password=hidden, stream=self.stream)
        except EOFError as error:
            self.console.print()
            raise InstallerError from error
        if self.stream is not None and not hidden and not answer:
            # Reading from ``stream`` keeps the line ending, so "" means its end.
            self.console.print()
            raise InstallerError
        return answer.strip()

    def ask(self, question: str, prompt: str = "", default: bool | None = None) -> bool:
        """Repeat the question until it is answered with yes or no.

        Answers are matched on the first letter of ``messages.yes`` and
        ``messages.no``, ignoring case, so translations work too.
        """
        yes, no = self.messages.yes, self.messages.no
        self.console.print()
        self.console.print(Rule(style="dim"))
        self.console.print(Text(question, style="bold"))
        self.console.print()
        line = Text.assemble(
            f"{prompt} " if prompt else "", (f"[{yes}/{no}]", _CHOICE_STYLE)
        )
        if default is not None:
            line.append(f" ({yes if default else no})", _DEFAULT_STYLE)
        line.append(": ")
        while True:
            first_letter = self._read(line)[:1].casefold()
            if not first_letter and default is not None:
                return default
            if first_letter and first_letter == yes[:1].casefold():
                return True
            if first_letter and first_letter == no[:1].casefold():
                return False

    def show_info(self, text: str) -> None:
        """Show the text in a panel titled with the window title."""
        self.console.print()
        self.console.print(
            Panel(
                Text(text),
                title=Text(self.title, style="bold"),
                title_align="left",
                border_style="blue",
                box=box.ROUNDED,
                padding=(1, 2),
            )
        )

    def alert(self, text: str) -> None:
        """Show the text in a heavy yellow frame, distinct even without colours."""
        self.console.print()
        self.console.print(
            Panel(
                Text(text, style="bold yellow"),
                title=Text(f"! {self.title}", style="bold yellow"),
                title_align="left",
                border_style="yellow",
                box=box.HEAVY,
                padding=(0, 2),
            )
        )

    def prompt_text(
        self, prompt: str, *, hidden: bool = False, initial: str = ""
    ) -> str:
        """Read a line until it is not empty.

        A non-empty ``initial`` is offered as the default of a visible value; it
        is never shown for a hidden one.
        """
        offered = "" if hidden else initial
        line = Text(prompt, style="bold")
        if offered:
            line.append(f" ({offered})", _DEFAULT_STYLE)
        line.append(": ")
        while True:
            value = self._read(line, hidden=hidden)
            if value:
                return value
            if offered:
                return offered

    def select_p12_file(self) -> Path | None:
        """Read a path, offering a default if the current directory has one match."""
        candidates = [
            path
            for path in Path().iterdir()
            if path.is_file() and path.name.endswith(P12_SUFFIXES)
        ]
        suggestion = candidates[0] if len(candidates) == 1 else None
        while True:
            line = Text(self.messages.p12_title, style="bold")
            if suggestion:
                line.append(f" ({suggestion})", _DEFAULT_STYLE)
            line.append(": ")
            value = _unquote(self._read(line))
            if suggestion and not value:
                return suggestion
            suggestion = None
            path = Path(value).expanduser()
            if value and path.is_file():
                return path
            self.console.print(Text(self.messages.file_not_found, style="bold red"))
