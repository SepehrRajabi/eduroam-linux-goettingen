"""Interfaces driving the zenity, kdialog and yad command line tools."""

import subprocess
from abc import abstractmethod
from pathlib import Path
from typing import ClassVar

from .base import CredentialForm, UserInterface

#: Arguments of an external dialog program.
type Command = list[str]

#: Separates form fields in tool output. Unlikely to appear inside a password.
_FIELD_SEPARATOR = "\n\n\n\n\n"


class DialogUI(UserInterface):
    """Common behaviour of external dialog tools.

    Subclasses provide the command line for each kind of dialog.
    """

    program: ClassVar[str]

    @abstractmethod
    def _question_command(self, text: str) -> Command:
        """Return the command for a yes/no question; exit status 0 means yes."""

    @abstractmethod
    def _info_command(self, text: str) -> Command:
        """Return the command showing information."""

    @abstractmethod
    def _warning_command(self, text: str) -> Command:
        """Return the command showing a warning."""

    @abstractmethod
    def _entry_command(self, prompt: str, *, hidden: bool, initial: str) -> Command:
        """Return the command reading a value."""

    @abstractmethod
    def _file_command(self) -> Command:
        """Return the command selecting a personal certificate."""

    def _clean_output(self, output: str) -> str:
        """Return tool output without its line ending."""
        return output.strip()

    def _read_until_answered(self, command: Command) -> str:
        """Run a dialog until it returns a value, offering to quit on cancel."""
        while True:
            result = subprocess.run(
                command, capture_output=True, encoding="utf-8", check=False
            )
            output = self._clean_output(result.stdout)
            if result.returncode == 1:
                self.confirm_exit()
            if output:
                return output

    def ask(self, question: str, prompt: str = "", default: bool | None = None) -> bool:
        """Show a question dialog; the tools have no way to mark a default."""
        command = self._question_command(f"{question}\n\n{prompt}")
        return (
            subprocess.run(command, stderr=subprocess.DEVNULL, check=False).returncode
            == 0
        )

    def show_info(self, text: str) -> None:
        """Show an information dialog."""
        subprocess.run(self._info_command(text), stderr=subprocess.DEVNULL, check=False)

    def alert(self, text: str) -> None:
        """Show a warning dialog."""
        subprocess.run(
            self._warning_command(text), stderr=subprocess.DEVNULL, check=False
        )

    def prompt_text(
        self, prompt: str, *, hidden: bool = False, initial: str = ""
    ) -> str:
        """Show an entry dialog until a value is given."""
        return self._read_until_answered(
            self._entry_command(prompt, hidden=hidden, initial=initial)
        )

    def select_p12_file(self) -> Path | None:
        """Show a file selection dialog."""
        result = subprocess.run(
            self._file_command(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            check=False,
        )
        selected = result.stdout.strip()
        return Path(selected) if selected else None

    def _parse_form(self, output: str) -> CredentialForm | None:
        """Split the output of a form, telling the user if a field is missing."""
        fields = output.split(_FIELD_SEPARATOR)
        if len(fields) != 3:
            self.alert(self.messages.empty_field)
            return None
        return CredentialForm(*fields)


class ZenityUI(DialogUI):
    """Dialogs using zenity."""

    program: ClassVar[str] = "zenity"
    has_credentials_form: ClassVar[bool] = True

    def _question_command(self, text: str) -> Command:
        """Return the command for a yes/no question."""
        return [
            "zenity",
            f"--title={self.title}",
            "--width=500",
            "--question",
            f"--text={text}",
        ]

    def _info_command(self, text: str) -> Command:
        """Return the command showing information."""
        return ["zenity", "--info", "--width=500", f"--text={text}"]

    def _warning_command(self, text: str) -> Command:
        """Return the command showing a warning."""
        return ["zenity", "--warning", f"--text={text}"]

    def _entry_command(self, prompt: str, *, hidden: bool, initial: str) -> Command:
        """Return the command reading a value."""
        command = ["zenity", "--entry", "--width=500", f"--text={prompt}"]
        if hidden:
            command.append("--hide-text")
        if initial:
            command.append(f"--entry-text={initial}")
        return command

    def _file_command(self) -> Command:
        """Return the command selecting a personal certificate."""
        return [
            "zenity",
            "--file-selection",
            f"--file-filter={self.messages.p12_filter} | *.p12 *.P12 *.pfx *.PFX",
            f"--file-filter={self.messages.all_filter} | *",
            f"--title={self.messages.p12_title}",
        ]

    def credentials_form(self, username: str) -> CredentialForm | None:
        """Show one form; zenity cannot pre-fill its fields."""
        return self._parse_form(
            self._read_until_answered(
                [
                    "zenity",
                    "--forms",
                    "--width=500",
                    f"--title={self.title}",
                    f"--text={self.messages.credentials_prompt}",
                    f"--add-entry={self.messages.username_prompt}",
                    f"--add-password={self.messages.enter_password}",
                    f"--add-password={self.messages.repeat_password}",
                    "--separator",
                    _FIELD_SEPARATOR,
                ]
            )
        )


class KDialogUI(DialogUI):
    """Dialogs using kdialog, which has no multi-field forms."""

    program: ClassVar[str] = "kdialog"

    def _question_command(self, text: str) -> Command:
        """Return the command for a yes/no question."""
        return ["kdialog", "--yesno", text, f"--title={self.title}"]

    def _info_command(self, text: str) -> Command:
        """Return the command showing information."""
        return ["kdialog", "--msgbox", text, f"--title={self.title}"]

    def _warning_command(self, text: str) -> Command:
        """Return the command showing a warning."""
        return ["kdialog", "--sorry", text, f"--title={self.title}"]

    def _entry_command(self, prompt: str, *, hidden: bool, initial: str) -> Command:
        """Return the command reading a value."""
        kind = "--password" if hidden else "--inputbox"
        return ["kdialog", kind, prompt, f"--title={self.title}"]

    def _file_command(self) -> Command:
        """Return the command selecting a personal certificate."""
        return [
            "kdialog",
            "--getopenfilename",
            ".",
            f"--title={self.messages.p12_title}",
        ]


class YadUI(DialogUI):
    """Dialogs using yad."""

    program: ClassVar[str] = "yad"
    has_credentials_form: ClassVar[bool] = True

    def _clean_output(self, output: str) -> str:
        """Drop the separator yad puts after the last field, and the line ending."""
        trailing = len(_FIELD_SEPARATOR) + 1 if _FIELD_SEPARATOR in output else 2
        return output[:-trailing].strip()

    def _question_command(self, text: str) -> Command:
        """Return the command for a yes/no question."""
        return [
            "yad",
            "--image=dialog-question",
            "--button=yad-yes:0",
            "--button=yad-no:1",
            "--width=500",
            "--wrap",
            f"--text={text}",
            f"--title={self.title}",
        ]

    def _info_command(self, text: str) -> Command:
        """Return the command showing information."""
        return ["yad", f"--button={self.messages.ok}", "--width=500", f"--text={text}"]

    def _warning_command(self, text: str) -> Command:
        """Return the command showing a warning."""
        return ["yad", f"--text={text}"]

    def _entry_command(self, prompt: str, *, hidden: bool, initial: str) -> Command:
        """Return the command reading a value."""
        field = "--field=:H" if hidden else "--field="
        return ["yad", "--form", field, f"--text={prompt}", initial]

    def _file_command(self) -> Command:
        """Return the command selecting a personal certificate."""
        return [
            "yad",
            "--file",
            "--file-filter=*.p12 *.P12 *.pfx *.PFX",
            "--file-filter=*",
            f"--title={self.messages.p12_title}",
        ]

    def credentials_form(self, username: str) -> CredentialForm | None:
        """Show one form with the username pre-filled."""
        return self._parse_form(
            self._read_until_answered(
                [
                    "yad",
                    "--form",
                    f"--title={self.title}",
                    f"--text={self.messages.credentials_prompt}",
                    f"--field={self.messages.username_prompt}",
                    username,
                    f"--field={self.messages.enter_password}:H",
                    f"--field={self.messages.repeat_password}:H",
                    "--separator",
                    _FIELD_SEPARATOR,
                ]
            )
        )
