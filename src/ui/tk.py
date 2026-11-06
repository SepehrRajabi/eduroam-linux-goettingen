"""Graphical interface built on tkinter."""

from pathlib import Path
from typing import ClassVar

from .base import CredentialForm, UserInterface


def tkinter_available() -> bool:
    """Return whether tkinter can be imported."""
    try:
        import tkinter  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


class TkinterUI(UserInterface):
    """Standard dialogs from tkinter; imports it only when in use."""

    has_credentials_form: ClassVar[bool] = True

    def ask(self, question: str, prompt: str = "", default: bool | None = None) -> bool:
        """Show a yes/no message box."""
        from tkinter import messagebox  # noqa: PLC0415

        return messagebox.askyesno(self.title, f"{question}\n\n{prompt}")

    def show_info(self, text: str) -> None:
        """Show an information message box."""
        from tkinter import messagebox  # noqa: PLC0415

        messagebox.showinfo(self.title, text)

    def alert(self, text: str) -> None:
        """Show a warning message box."""
        from tkinter import messagebox  # noqa: PLC0415

        messagebox.showwarning(self.title, text)

    def prompt_text(
        self, prompt: str, *, hidden: bool = False, initial: str = ""
    ) -> str:
        """Show an entry dialog until a value is given."""
        from tkinter import simpledialog  # noqa: PLC0415

        while True:
            value = simpledialog.askstring(
                self.title, prompt, initialvalue=initial, show="*" if hidden else ""
            )
            if value:
                return value

    def select_p12_file(self) -> Path | None:
        """Show a file selection dialog."""
        from tkinter import filedialog  # noqa: PLC0415

        patterns = ("*.p12", "*.P12", "*.pfx", "*.PFX")
        selected = filedialog.askopenfilename(
            title=self.messages.p12_title,
            filetypes=((self.messages.certificate_filter, patterns),),
        )
        return Path(selected) if selected else None

    def credentials_form(self, username: str) -> CredentialForm | None:
        """Show one window asking for username, password and its confirmation."""
        import tkinter as tk  # noqa: PLC0415

        submitted: CredentialForm | None = None
        root = tk.Tk()
        root.title(self.title)

        def add_row(row: int, label: str, entry: tk.Entry) -> tk.Entry:
            """Place a labelled entry in the given grid row."""
            tk.Label(root, text=label).grid(row=row, column=0, sticky=tk.W)
            entry.grid(row=row, column=1)
            return entry

        tk.Label(root, text=self.messages.credentials_prompt).grid(
            row=0, column=0, columnspan=2, sticky=tk.W
        )
        username_entry = add_row(
            1,
            self.messages.username_prompt,
            tk.Entry(root, textvariable=tk.StringVar(root, value=username)),
        )
        password_entry = add_row(
            2, self.messages.enter_password, tk.Entry(root, show="*")
        )
        confirmation_entry = add_row(
            3, self.messages.repeat_password, tk.Entry(root, show="*")
        )

        def submit() -> None:
            """Keep the entered values and close the window."""
            nonlocal submitted
            submitted = CredentialForm(
                username_entry.get(), password_entry.get(), confirmation_entry.get()
            )
            root.destroy()

        tk.Button(root, text=self.messages.ok, command=submit).grid(
            row=4, column=0, columnspan=2
        )
        root.mainloop()

        if submitted is None:  # the window was closed
            self.confirm_exit()
        elif not all((submitted.username, submitted.password)):
            self.alert(self.messages.empty_field)
            return None
        return submitted
