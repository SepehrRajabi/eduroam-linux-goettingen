"""The interface every way of talking to the user implements."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from ..errors import InstallerError
from ..messages import Messages

#: Filename extensions recognised as personal certificates.
P12_SUFFIXES = (".p12", ".pfx", ".P12", ".PFX")


@dataclass(frozen=True, slots=True)
class CredentialForm:
    """The fields of a submitted credentials form."""

    username: str
    password: str
    confirmation: str


@dataclass
class UserInterface(ABC):
    """Everything the installer needs from a user interface.

    Attributes:
        title: Title of dialog windows.
        messages: Texts to show.
        silent: Whether the user is not being asked anything at all.
        has_credentials_form: Whether :meth:`credentials_form` is available.
    """

    title: str
    messages: Messages

    silent: ClassVar[bool] = False
    has_credentials_form: ClassVar[bool] = False

    @abstractmethod
    def ask(self, question: str, prompt: str = "", default: bool | None = None) -> bool:
        """Ask a yes or no question and return whether the answer was yes."""

    @abstractmethod
    def show_info(self, text: str) -> None:
        """Show a piece of information."""

    @abstractmethod
    def alert(self, text: str) -> None:
        """Show a warning."""

    @abstractmethod
    def prompt_text(
        self, prompt: str, *, hidden: bool = False, initial: str = ""
    ) -> str:
        """Ask for a value until a non-empty one is given.

        Args:
            prompt: Description of the value.
            hidden: Do not echo the value, as it is a password.
            initial: Suggested value, where the interface can offer one.
        """

    @abstractmethod
    def select_p12_file(self) -> Path | None:
        """Let the user pick a personal certificate; ``None`` if they did not."""

    def credentials_form(self, username: str) -> CredentialForm | None:
        """Ask for username and password in one step.

        Only available if ``has_credentials_form`` is set.

        Args:
            username: Suggested username.

        Returns:
            The submitted form, or ``None`` if it was incomplete and the user has
            already been told. The caller shows the form again.
        """
        raise NotImplementedError

    def ask_to_continue(self, question: str) -> None:
        """Stop the installer unless the user agrees to continue past ``question``."""
        if not self.ask(question, self.messages.cont, default=True):
            raise InstallerError

    def confirm_exit(self) -> None:
        """Stop the installer if the user confirms they want to quit."""
        if self.ask(self.messages.quit):
            raise InstallerError
