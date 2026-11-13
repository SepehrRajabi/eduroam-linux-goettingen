"""The Rich terminal interface, driven through an in-memory console."""

import io
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from rich.console import Console

from src.cli import main
from src.errors import InstallerError
from src.messages import MESSAGES, Messages
from src.ui.terminal import RichUI

#: Messages of a German translation, as far as the terminal cares.
GERMAN = replace(MESSAGES, yes="Ja", no="Nein", file_not_found="Datei fehlt")


@dataclass
class Terminal:
    """A ``RichUI`` with scripted answers and captured output.

    Attributes:
        ui: The interface under test.
        output: Everything it printed, prompts included.
        hidden_prompts: How many answers were read without echo.
    """

    ui: RichUI
    output: io.StringIO
    hidden_prompts: int = 0

    @property
    def text(self) -> str:
        """Return the output so far."""
        return self.output.getvalue()


type TerminalFactory = Callable[..., Terminal]


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> TerminalFactory:
    """Return a factory of terminals answering with the given lines.

    Hidden answers are taken from the same lines through a fake ``getpass``, which
    counts how often it was used.
    """

    def create(*answers: str, messages: Messages = MESSAGES) -> Terminal:
        """Build a terminal that will answer ``answers`` in order."""
        output = io.StringIO()
        stream = io.StringIO("".join(f"{answer}\n" for answer in answers))
        console = Console(file=output, width=60, color_system=None, highlight=False)
        ui = RichUI("eduroam CAT", messages, console=console, stream=stream)
        result = Terminal(ui, output)

        def fake_getpass(prompt: str = "", stream: object = None) -> str:
            """Read a line like ``getpass`` does, without its line ending."""
            result.hidden_prompts += 1
            line = ui.stream.readline() if ui.stream else ""
            if not line:
                raise EOFError
            return line.rstrip("\n")

        monkeypatch.setattr("getpass.getpass", fake_getpass)
        return result

    return create


# ask


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("y", True), ("Y", True), ("yes", True), ("n", False), ("No", False)],
)
def test_ask_answers(terminal: TerminalFactory, answer: str, expected: bool) -> None:
    """Answers are matched on their first letter, ignoring case."""
    term = terminal(answer)
    assert term.ui.ask("Proceed?", "Continue?") is expected
    assert "Proceed?" in term.text
    assert "Continue? [Y/N]: " in term.text


@pytest.mark.parametrize("default", [True, False])
def test_ask_default(terminal: TerminalFactory, default: bool) -> None:
    """An empty answer takes the default, which the prompt shows."""
    term = terminal("")
    assert term.ui.ask("Proceed?", default=default) is default
    assert f"[Y/N] ({'Y' if default else 'N'}): " in term.text


def test_ask_repeats_until_answered(terminal: TerminalFactory) -> None:
    """Empty answers without a default, and unknown answers, ask again."""
    term = terminal("", "maybe", "  ", "n")
    assert term.ui.ask("Proceed?") is False
    assert term.text.count("[Y/N]: ") == 4


@pytest.mark.parametrize(
    ("answer", "expected"), [("j", True), ("JA", True), ("nein", False)]
)
def test_ask_translated(terminal: TerminalFactory, answer: str, expected: bool) -> None:
    """Translated yes and no words are accepted by their first letter."""
    term = terminal("y", answer, messages=GERMAN)
    assert term.ui.ask("Weiter?") is expected
    assert "[Ja/Nein]: " in term.text
    assert term.text.count("[Ja/Nein]: ") == 2  # "y" is not a German answer


def test_ask_translated_default(terminal: TerminalFactory) -> None:
    """The default is shown with the translated word."""
    term = terminal("")
    term.ui.messages = GERMAN
    assert term.ui.ask("Weiter?", default=False) is False
    assert "[Ja/Nein] (Nein): " in term.text


def test_question_is_not_markup(terminal: TerminalFactory) -> None:
    """Brackets and emoji codes in installer texts are shown literally."""
    term = terminal("y")
    term.ui.ask("[bold]keep[/bold] :smile:")
    assert "[bold]keep[/bold] :smile:" in term.text


def test_ask_to_continue(terminal: TerminalFactory) -> None:
    """Agreeing continues, also by default; "no" stops the installer."""
    term = terminal("y", "", "n")
    term.ui.ask_to_continue("Ready?")
    term.ui.ask_to_continue("Still ready?")
    with pytest.raises(InstallerError) as raised:
        term.ui.ask_to_continue("Really?")
    assert raised.value.message == ""
    assert "Continue? [Y/N] (Y): " in term.text


def test_confirm_exit(terminal: TerminalFactory) -> None:
    """Confirming the quit question stops the installer."""
    term = terminal("n", "y")
    term.ui.confirm_exit()
    with pytest.raises(InstallerError):
        term.ui.confirm_exit()


def test_end_of_input_stops(terminal: TerminalFactory) -> None:
    """Running out of input stops the installer instead of looping forever."""
    term = terminal("maybe")
    with pytest.raises(InstallerError):
        term.ui.ask("Proceed?")


# prompt_text


def test_prompt_text_repeats_until_not_empty(terminal: TerminalFactory) -> None:
    """Empty and blank values are asked again; the value is stripped."""
    term = terminal("", "   ", "  alice@uni-goettingen.de ")
    assert term.ui.prompt_text("enter your userid") == "alice@uni-goettingen.de"
    assert term.text.count("enter your userid: ") == 3
    assert term.hidden_prompts == 0


def test_prompt_text_initial(terminal: TerminalFactory) -> None:
    """A suggested value is shown and taken on an empty answer."""
    term = terminal("", "bob@uni-goettingen.de")
    assert term.ui.prompt_text("user", initial="alice@x") == "alice@x"
    assert "user (alice@x): " in term.text
    assert term.ui.prompt_text("user", initial="alice@x") == "bob@uni-goettingen.de"


def test_prompt_text_hidden(terminal: TerminalFactory) -> None:
    """Hidden values are read without echo and never show a suggestion."""
    term = terminal("", "s3cret")
    assert term.ui.prompt_text("password", hidden=True, initial="old") == "s3cret"
    assert term.hidden_prompts == 2
    assert "s3cret" not in term.text
    assert "old" not in term.text
    assert term.text.count("password: ") == 2


# select_p12_file


def test_p12_suggestion(terminal: TerminalFactory) -> None:
    """The only certificate in the working directory is offered and accepted."""
    Path("mine.p12").write_bytes(b"")
    Path("notes.txt").write_text("")
    term = terminal("")
    assert term.ui.select_p12_file() == Path("mine.p12")
    assert "(mine.p12): " in term.text


def test_p12_no_suggestion_with_several(terminal: TerminalFactory) -> None:
    """Two certificates mean no suggestion; the typed one is used."""
    Path("a.p12").write_bytes(b"")
    Path("b.PFX").write_bytes(b"")
    term = terminal("b.PFX")
    assert term.ui.select_p12_file() == Path("b.PFX")
    assert "(a.p12)" not in term.text


def test_p12_file_not_found_loop(terminal: TerminalFactory) -> None:
    """Missing files and empty answers are refused until a file exists."""
    Path("only.pfx").write_bytes(b"")
    Path("other.p12").mkdir()  # a directory is not a certificate
    Path("real.p12").write_bytes(b"")
    term = terminal("missing.p12", "", "other.p12", "'real.p12'", messages=GERMAN)
    assert term.ui.select_p12_file() == Path("real.p12")
    assert term.text.count("Datei fehlt") == 3


def test_p12_suggestion_dropped_after_mistake(terminal: TerminalFactory) -> None:
    """After a wrong answer the suggestion is no longer offered."""
    Path("mine.p12").write_bytes(b"")
    term = terminal("nope.p12", "", "mine.p12")
    assert term.ui.select_p12_file() == Path("mine.p12")
    assert term.text.count("(mine.p12)") == 1
    assert term.text.count("file not found") == 2


def test_p12_home_directory(terminal: TerminalFactory, isolated_home: Path) -> None:
    """A leading ``~`` means the home directory."""
    (isolated_home / "cert.p12").write_bytes(b"")
    term = terminal("~/cert.p12")
    assert term.ui.select_p12_file() == isolated_home / "cert.p12"


# show_info and alert


def test_show_info_panel(terminal: TerminalFactory) -> None:
    """Information is framed and titled with the window title."""
    term = terminal()
    term.ui.show_info("Installation successful")
    assert "eduroam CAT" in term.text
    assert "Installation successful" in term.text
    assert "╭" in term.text


def test_alert_stands_out(terminal: TerminalFactory) -> None:
    """Warnings use a heavier frame than information, visible without colour."""
    term = terminal()
    term.ui.alert("passwords do not match")
    assert "passwords do not match" in term.text
    assert "┏" in term.text
    assert "╭" not in term.text


def test_no_colour_on_plain_files() -> None:
    """Writing to a file that is not a terminal produces no escape sequences."""
    output = io.StringIO()
    ui = RichUI("eduroam CAT", MESSAGES, console=Console(file=output))
    ui.alert("careful")
    ui.show_info("done")
    assert "\x1b[" not in output.getvalue()


def test_has_no_credentials_form() -> None:
    """The terminal asks for username and passwords one by one."""
    assert RichUI.has_credentials_form is False
    assert RichUI.silent is False


# the whole installer


def test_interactive_wpa_conf(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    isolated_home: Path,
) -> None:
    """A terminal user installs a wpa_supplicant file by answering prompts."""
    answers = io.StringIO("\nalice@uni-goettingen.de\n")
    passwords = iter(["secret", "different", "secret", "secret"])
    monkeypatch.setattr("sys.stdin", answers)
    monkeypatch.setattr("getpass.getpass", lambda *args, **kwargs: next(passwords))

    assert main(["--gui", "tty", "--wpa_conf"]) == 0

    out = capsys.readouterr().out
    assert "This installer has been prepared for University Goettingen" in out
    assert "passwords do not match" in out
    assert "Installation successful" in out
    conf = isolated_home / ".config" / "cat_installer" / "cat_installer.conf"
    text = conf.read_text()
    assert '\tidentity="alice@uni-goettingen.de"\n' in text
    assert '\tpassword="secret"\n' in text


def test_interactive_decline(
    monkeypatch: pytest.MonkeyPatch, isolated_home: Path
) -> None:
    """Declining the membership question ends the installer without changes."""
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    assert main(["--gui", "tty", "--wpa_conf"]) == 1
    assert not (isolated_home / ".config" / "cat_installer").exists()
