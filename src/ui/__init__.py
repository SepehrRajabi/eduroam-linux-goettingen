"""User interfaces, and the choice between them."""

import logging
import os
import shutil
from enum import StrEnum

from ..messages import Messages
from .base import CredentialForm, UserInterface
from .dialogs import DialogUI, KDialogUI, YadUI, ZenityUI
from .silent import SilentUI
from .tk import TkinterUI, tkinter_available
from .tty import TtyUI

__all__ = ["CredentialForm", "GuiChoice", "UserInterface", "create_ui"]

logger = logging.getLogger(__name__)

#: Dialog tools in the order they are tried.
_DIALOG_TOOLS: tuple[type[DialogUI], ...] = (YadUI, ZenityUI, KDialogUI)


class GuiChoice(StrEnum):
    """Values accepted by the ``--gui`` option."""

    TTY = "tty"
    TKINTER = "tkinter"
    ZENITY = "zenity"
    KDIALOG = "kdialog"
    YAD = "yad"


def create_ui(
    preference: GuiChoice | None, *, silent: bool, title: str, messages: Messages
) -> UserInterface:
    """Choose the user interface.

    Without a display, or with ``tty`` requested, this is the terminal. Otherwise
    the requested interface wins if present, tkinter is next if an interface was
    requested, then the dialog tools. The terminal is the last resort.

    Args:
        preference: Interface requested by the user.
        silent: Do not interact with the user at all.
        title: Title of dialog windows.
        messages: Texts to show.
    """
    if silent:
        return SilentUI(title, messages)
    if preference is GuiChoice.TTY or os.environ.get("DISPLAY") is None:
        return TtyUI(title, messages)

    for tool in _DIALOG_TOOLS:
        if tool.program == preference and shutil.which(tool.program):
            logger.debug("Using %s", tool.program)
            return tool(title, messages)
    if preference and tkinter_available():
        logger.debug("Using tkinter")
        return TkinterUI(title, messages)
    for tool in _DIALOG_TOOLS:
        if shutil.which(tool.program):
            logger.debug("Using %s", tool.program)
            return tool(title, messages)
    return TtyUI(title, messages)
