"""User-facing strings, kept together so they can be translated."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Messages:
    """Every string shown to the user.

    The defaults are English. A translation is a new instance with the relevant
    fields replaced. ``yes`` and ``no`` are also what the terminal prompt accepts,
    matched on their first letter.
    """

    quit: str = "Really quit?"
    credentials_prompt: str = "Please enter your credentials:"
    username_prompt: str = "enter your userid"
    enter_password: str = "enter password"
    enter_import_password: str = "enter your import password"
    incorrect_password: str = "incorrect password"
    repeat_password: str = "repeat your password"
    passwords_differ: str = "passwords do not match"
    empty_field: str = "one of the fields was empty"
    installation_finished: str = "Installation successful"
    cat_dir_exists: str = "Directory {} exists; some of its files may be overwritten"
    cont: str = "Continue?"
    nm_not_supported: str = "This NetworkManager version is not supported"
    cert_error: str = "Certificate file not found, looks like a CAT error"
    dbus_error: str = "DBus connection problem, a sudo might help"
    dbus_missing: str = "WARNING: Cannot import the dbus module - please install it!"
    dbus_broken: str = "DBus not properly installed"
    dbus_unreachable: str = "Can't connect to DBus"
    yes: str = "Y"
    no: str = "N"
    ok: str = "OK"
    p12_filter: str = "personal certificate file (p12 or pfx)"
    all_filter: str = "All files"
    p12_title: str = "personal certificate file (p12 or pfx)"
    certificate_filter: str = "Certificate file"
    file_not_found: str = "file not found"
    save_wpa_conf: str = (
        "DBus module not found - please install dbus-python! NetworkManager "
        "configuration failed, but we can generate a wpa_supplicant configuration "
        "file if you wish. Be warned that your connection password will be saved "
        "in this file as clear text."
    )
    wrong_username_format: str = (
        "Error: Your username must be of the form 'xxx@institutionID' "
        "e.g. 'john@example.net'!"
    )
    wrong_realm: str = (
        "Error: your username must be in the form of 'xxx@{}'. "
        "Please enter the username in the correct format."
    )
    wrong_realm_suffix: str = (
        "Error: your username must be in the form of 'xxx@institutionID' and end "
        "with '{}'. Please enter the username in the correct format."
    )
    user_cert_missing: str = "personal certificate file not found"
    wired: str = (
        "Configure eduroam also on the wired Ethernet?\n\n Please be aware that "
        "doing so will introduce a connection delay lasting up to 30 sec. when you "
        "connect to an open wired network."
    )


MESSAGES = Messages()
