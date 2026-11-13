"""The order in which ``create_ui`` picks a user interface."""

from collections.abc import Iterable
from dataclasses import dataclass, field

import pytest

import src.ui
from src.messages import MESSAGES
from src.ui import GuiChoice, UserInterface, create_ui
from src.ui.dialogs import KDialogUI, YadUI, ZenityUI
from src.ui.silent import SilentUI
from src.ui.terminal import RichUI
from src.ui.tk import TkinterUI


@dataclass
class FakeSystem:
    """What ``create_ui`` sees of the system, changed by each test.

    Attributes:
        monkeypatch: Applies the changes, undone after the test.
        installed: Dialog programs found on ``PATH``.
        tkinter: Whether tkinter can be imported.
        terminal: Whether standard input and output are terminals.
    """

    monkeypatch: pytest.MonkeyPatch
    installed: set[str] = field(default_factory=set)
    tkinter: bool = False
    terminal: bool = False

    def __post_init__(self) -> None:
        """Start without displays, then route the probes to the attributes."""
        self.monkeypatch.delenv("DISPLAY", raising=False)
        self.monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        self.monkeypatch.setattr("shutil.which", self._which)
        self.monkeypatch.setattr(src.ui, "tkinter_available", lambda: self.tkinter)
        self.monkeypatch.setattr(src.ui, "stdio_is_terminal", lambda: self.terminal)

    def _which(self, program: str) -> str | None:
        """Pretend ``program`` is installed in ``/usr/bin`` if listed."""
        return f"/usr/bin/{program}" if program in self.installed else None

    def install(self, programs: Iterable[str]) -> None:
        """Put ``programs`` on the fake ``PATH``."""
        self.installed.update(programs)

    def x11(self) -> None:
        """Start an X11 session."""
        self.monkeypatch.setenv("DISPLAY", ":0")

    def wayland(self) -> None:
        """Start a Wayland session without XWayland."""
        self.monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")


@pytest.fixture
def system(monkeypatch: pytest.MonkeyPatch) -> FakeSystem:
    """Return a system without displays, dialog tools, tkinter or terminal."""
    return FakeSystem(monkeypatch)


def _create(
    preference: GuiChoice | None = None, *, silent: bool = False
) -> type[UserInterface]:
    """Return the class of the interface ``create_ui`` chooses."""
    ui: UserInterface = create_ui(
        preference, silent=silent, title="eduroam CAT", messages=MESSAGES
    )
    return type(ui)


ALL_TOOLS = ("yad", "zenity", "kdialog")


# 1. silent


@pytest.mark.parametrize("preference", [None, *GuiChoice])
def test_silent_wins(system: FakeSystem, preference: GuiChoice | None) -> None:
    """``--silent`` beats every other choice."""
    system.x11()
    system.install(ALL_TOOLS)
    system.tkinter = system.terminal = True
    assert _create(preference, silent=True) is SilentUI


# 2. --gui tty


def test_tty_request_in_graphical_session(system: FakeSystem) -> None:
    """``--gui tty`` uses Rich even with dialogs available and no terminal."""
    system.x11()
    system.install(ALL_TOOLS)
    system.tkinter = True
    assert _create(GuiChoice.TTY) is RichUI


# 3. --gui yad|zenity|kdialog


@pytest.mark.parametrize(
    ("preference", "expected"),
    [
        (GuiChoice.YAD, YadUI),
        (GuiChoice.ZENITY, ZenityUI),
        (GuiChoice.KDIALOG, KDialogUI),
    ],
)
def test_requested_tool_wins(
    system: FakeSystem, preference: GuiChoice, expected: type[UserInterface]
) -> None:
    """A requested installed tool beats the terminal and the default order."""
    system.x11()
    system.install(ALL_TOOLS)
    system.tkinter = system.terminal = True
    assert _create(preference) is expected


def test_requested_tool_on_wayland(system: FakeSystem) -> None:
    """``WAYLAND_DISPLAY`` alone is enough for a dialog tool."""
    system.wayland()
    system.install(["zenity"])
    system.terminal = True
    assert _create(GuiChoice.ZENITY) is ZenityUI


def test_requested_tool_without_display(system: FakeSystem) -> None:
    """An installed tool is not used without a graphical session."""
    system.install(ALL_TOOLS)
    system.terminal = True
    assert _create(GuiChoice.YAD) is RichUI


# 4. other --gui requests fall back to tkinter


def test_tkinter_request(system: FakeSystem) -> None:
    """``--gui tkinter`` selects tkinter, before the terminal and dialog tools."""
    system.x11()
    system.install(ALL_TOOLS)
    system.tkinter = system.terminal = True
    assert _create(GuiChoice.TKINTER) is TkinterUI


def test_missing_tool_falls_back_to_tkinter(system: FakeSystem) -> None:
    """A requested tool that is not installed gives way to tkinter."""
    system.x11()
    system.install(["yad"])
    system.tkinter = system.terminal = True
    assert _create(GuiChoice.ZENITY) is TkinterUI


def test_tkinter_request_needs_x11(system: FakeSystem) -> None:
    """On Wayland without XWayland, ``--gui tkinter`` falls through to the terminal."""
    system.wayland()
    system.tkinter = system.terminal = True
    assert _create(GuiChoice.TKINTER) is RichUI


def test_tkinter_request_needs_tkinter(system: FakeSystem) -> None:
    """Without tkinter, ``--gui tkinter`` falls through to the default order."""
    system.x11()
    system.install(["kdialog"])
    assert _create(GuiChoice.TKINTER) is KDialogUI


# 5. terminal


def test_terminal_beats_dialogs(system: FakeSystem) -> None:
    """Started from a terminal, the installer stays there unless told otherwise."""
    system.x11()
    system.install(ALL_TOOLS)
    system.tkinter = system.terminal = True
    assert _create() is RichUI


def test_terminal_after_unmet_request(system: FakeSystem) -> None:
    """A request for a missing tool, without tkinter, ends in the terminal."""
    system.x11()
    system.install(["yad", "zenity"])
    system.terminal = True
    assert _create(GuiChoice.KDIALOG) is RichUI


def test_terminal_check_reads_both_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """Standard input and output must both be terminals."""

    class Stream:
        """A stream that is a terminal or not."""

        def __init__(self, tty: bool) -> None:
            """Remember whether to claim to be a terminal."""
            self.tty = tty

        def isatty(self) -> bool:
            """Return the configured answer."""
            return self.tty

    for stdin, stdout, expected in [
        (True, True, True),
        (True, False, False),
        (False, True, False),
    ]:
        monkeypatch.setattr("sys.stdin", Stream(stdin))
        monkeypatch.setattr("sys.stdout", Stream(stdout))
        assert src.ui.stdio_is_terminal() is expected


# 6. graphical session without a terminal


@pytest.mark.parametrize(
    ("installed", "expected"),
    [
        (ALL_TOOLS, YadUI),
        (("zenity", "kdialog"), ZenityUI),
        (("kdialog",), KDialogUI),
    ],
)
def test_dialog_tool_order(
    system: FakeSystem, installed: tuple[str, ...], expected: type[UserInterface]
) -> None:
    """Without a terminal, the first installed of yad, zenity and kdialog wins."""
    system.x11()
    system.install(installed)
    system.tkinter = True
    assert _create() is expected


def test_wayland_dialog_without_terminal(system: FakeSystem) -> None:
    """A Wayland session uses the dialog tools too."""
    system.wayland()
    system.install(["kdialog"])
    assert _create() is KDialogUI


def test_tkinter_after_dialog_tools(system: FakeSystem) -> None:
    """Without dialog tools, tkinter is next."""
    system.x11()
    system.tkinter = True
    assert _create() is TkinterUI


def test_unavailable_request_uses_default_order(system: FakeSystem) -> None:
    """A request that cannot be met falls through to the dialog tools."""
    system.x11()
    system.install(["zenity"])
    assert _create(GuiChoice.YAD) is ZenityUI


# 7. last resort


def test_wayland_only_without_tools(system: FakeSystem) -> None:
    """Tkinter is not tried without ``DISPLAY``; the terminal is the last resort."""
    system.wayland()
    system.tkinter = True
    assert _create() is RichUI


@pytest.mark.parametrize("preference", [None, *GuiChoice])
def test_nothing_available(system: FakeSystem, preference: GuiChoice | None) -> None:
    """With nothing else to use, every request ends in the terminal."""
    system.install(ALL_TOOLS)
    system.tkinter = True
    assert _create(preference) is RichUI


def test_empty_display_is_no_session(system: FakeSystem) -> None:
    """An empty ``DISPLAY`` does not count as a graphical session."""
    system.monkeypatch.setenv("DISPLAY", "")
    system.install(ALL_TOOLS)
    system.tkinter = True
    assert _create() is RichUI
