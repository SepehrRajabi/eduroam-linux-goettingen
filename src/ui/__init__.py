"""User interfaces, and the choice between them."""

import logging
import os
import shutil
import sys
from enum import StrEnum

from ..messages import Messages
from .base import CredentialForm, UserInterface
from .dialogs import DialogUI, KDialogUI, YadUI, ZenityUI
from .silent import SilentUI
from .terminal import RichUI
from .tk import TkinterUI, tkinter_available

__all__ = ["CredentialForm", "GuiChoice", "UserInterface", "create_ui"]

logger = logging.getLogger(__name__)

#: Dialog tools in the order they are tried.
_DIALOG_TOOLS: tuple[type[DialogUI], ...] = (YadUI, ZenityUI, KDialogUI)


class GuiChoice(StrEnum):
    """Values accepted by the ``--gui`` option; ``tty`` is the Rich terminal."""

    TTY = "tty"
    TKINTER = "tkinter"
    ZENITY = "zenity"
    KDIALOG = "kdialog"
    YAD = "yad"


def stdio_is_terminal() -> bool:
    """Return whether both standard input and standard output are terminals."""
    return (
        sys.stdin is not None
        and sys.stdout is not None
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    )


def _graphical_session() -> bool:
    """Return whether an X11 or Wayland display is available."""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _tkinter_usable() -> bool:
    """Return whether tkinter is installed and has an X11 display to talk to.

    Tk has no native Wayland support, so it needs ``DISPLAY`` (X11 or XWayland).
    """
    return bool(os.environ.get("DISPLAY")) and tkinter_available()


def create_ui(
    preference: GuiChoice | None, *, silent: bool, title: str, messages: Messages
) -> UserInterface:
    """Choose the user interface.

    The first match wins:

    1. ``silent``: no interface at all.
    2. ``--gui tty``: the Rich terminal interface.
    3. ``--gui yad|zenity|kdialog``: that tool, in a graphical session where it
       is installed.
    4. Any other ``--gui`` request not met so far, ``tkinter`` included:
       tkinter, if it is installed and ``DISPLAY`` is set.
    5. Standard input and output are both terminals: the Rich terminal.
    6. A graphical session: the first installed of yad, zenity and kdialog, then
       tkinter if it is installed and ``DISPLAY`` is set.
    7. The Rich terminal, as the last resort.

    A graphical session is one with ``DISPLAY`` or ``WAYLAND_DISPLAY`` set.

    Args:
        preference: Interface requested by the user.
        silent: Do not interact with the user at all.
        title: Title of dialog windows.
        messages: Texts to show.
    """
    if silent:
        return SilentUI(title, messages)
    graphical = _graphical_session()
    chosen: type[UserInterface] | None = None
    if preference is GuiChoice.TTY:
        chosen = RichUI
    elif preference is not None:
        chosen = _requested_ui(preference, graphical=graphical)
        if chosen is None:
            logger.debug("%s is not available", preference)
    if chosen is None and stdio_is_terminal():
        chosen = RichUI
    if chosen is None and graphical:
        chosen = _first_graphical_ui()
    if chosen is None:
        logger.debug("Nothing else is available")
        chosen = RichUI
    logger.debug("Using %s", chosen.__name__)
    return chosen(title, messages)


def _requested_ui(
    preference: GuiChoice, *, graphical: bool
) -> type[UserInterface] | None:
    """Return the interface for a ``--gui`` request other than ``tty``.

    This is the requested dialog tool if it is installed in a graphical session,
    and otherwise tkinter, if usable.
    """
    if graphical:
        for tool in _DIALOG_TOOLS:
            if tool.program == preference and shutil.which(tool.program):
                return tool
    return TkinterUI if _tkinter_usable() else None


def _first_graphical_ui() -> type[UserInterface] | None:
    """Return the first installed dialog tool, or tkinter if usable."""
    for tool in _DIALOG_TOOLS:
        if shutil.which(tool.program):
            return tool
    return TkinterUI if _tkinter_usable() else None
