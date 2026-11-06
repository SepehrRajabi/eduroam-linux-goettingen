"""Collecting and validating the credentials of the user."""

import base64
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from .certificates import InvalidP12Error, read_p12_identity
from .config import PASSWORD_METHODS, EapMethod, Profile
from .errors import InstallerError
from .messages import Messages
from .paths import InstallerPaths
from .ui.base import UserInterface

logger = logging.getLogger(__name__)

#: Checks a username and returns an error message, or ``None`` if it is valid.
type UsernameValidator = Callable[[str], str | None]


@dataclass(slots=True)
class Credentials:
    """What the user authenticates with.

    Attributes:
        username: Inner identity.
        password: Network password, or the import password of a certificate.
        pfx_file: Personal certificate to import, given on the command line.
    """

    username: str = ""
    password: str = ""
    pfx_file: Path | None = None


def check_username(username: str, profile: Profile, messages: Messages) -> str | None:
    """Validate the form of a username against the realm rules of a profile.

    Returns:
        The error to show to the user, or ``None`` if the username is acceptable.
    """
    user, at_sign, realm = username.partition("@")
    if not at_sign:
        accepted = username and not profile.verify_user_realm_input
        return None if accepted else messages.wrong_username_format
    if not user or not realm:
        return messages.wrong_username_format
    if profile.verify_user_realm_input:
        return _check_expected_realm(realm, profile, messages)
    if "@" in realm or realm.startswith("."):
        return messages.wrong_username_format
    return None


def _check_expected_realm(
    realm: str, profile: Profile, messages: Messages
) -> str | None:
    """Compare the realm of a username with the one ``profile`` expects."""
    expected = profile.user_realm
    if profile.hint_user_input:
        if f"@{realm}".endswith(f"@{expected}"):
            return None
        return messages.wrong_realm.format(expected)
    if realm.endswith(expected):
        return None
    return messages.wrong_realm_suffix.format(expected)


def collect_credentials(
    credentials: Credentials,
    ui: UserInterface,
    profile: Profile,
    paths: InstallerPaths,
) -> None:
    """Fill in ``credentials`` as the EAP method of ``profile`` requires."""
    if profile.eap_outer in PASSWORD_METHODS:
        if not ui.silent:
            _ask_username_password(credentials, ui, profile)
    elif profile.eap_outer is EapMethod.TLS:
        _collect_certificate(credentials, ui, profile, paths)


def _ask_username_password(
    credentials: Credentials, ui: UserInterface, profile: Profile
) -> None:
    """Ask for a username and password, using a single form if available."""
    messages = ui.messages
    validate: UsernameValidator = partial(
        check_username, profile=profile, messages=messages
    )
    if ui.has_credentials_form:
        _fill_credentials_form(credentials, ui, validate)
        return

    if credentials.username:
        suggestion = credentials.username
    elif profile.hint_user_input:
        suggestion = f"@{profile.user_realm}"
    else:
        suggestion = ""
    while True:
        credentials.username = ui.prompt_text(
            messages.username_prompt, initial=suggestion
        )
        error = validate(credentials.username)
        if error is None:
            break
        ui.alert(error)
    while True:
        password = ui.prompt_text(messages.enter_password, hidden=True)
        if password == ui.prompt_text(messages.repeat_password, hidden=True):
            break
        ui.alert(messages.passwords_differ)
    credentials.password = password


def _fill_credentials_form(
    credentials: Credentials, ui: UserInterface, validate: UsernameValidator
) -> None:
    """Show the credentials form until it is filled in correctly."""
    while True:
        form = ui.credentials_form(credentials.username)
        if form is None:
            continue
        credentials.username = form.username
        error = validate(form.username)
        if error is not None:
            ui.alert(error)
        elif form.password != form.confirmation:
            ui.alert(ui.messages.passwords_differ)
        else:
            credentials.password = form.password
            return


def _collect_certificate(
    credentials: Credentials,
    ui: UserInterface,
    profile: Profile,
    paths: InstallerPaths,
) -> None:
    """Install the personal certificate and unlock it."""
    if profile.eap_inner == "SILVERBULLET":
        paths.user_p12.write_bytes(base64.b64decode(profile.sb_user_file))
    else:
        source = credentials.pfx_file if ui.silent else ui.select_p12_file()
        _copy_certificate(source, paths.user_p12, ui.messages)

    if ui.silent:
        _unlock_certificate_silently(credentials, profile, paths)
    else:
        _ask_certificate_password(credentials, ui, profile, paths)


def _copy_certificate(
    source: Path | None, destination: Path, messages: Messages
) -> None:
    """Copy the personal certificate, stopping the installer if that fails."""
    if source is None:
        raise InstallerError(messages.user_cert_missing)
    try:
        shutil.copyfile(source, destination)
    except OSError as error:
        raise InstallerError(messages.user_cert_missing) from error


def _unlock_certificate_silently(
    credentials: Credentials, profile: Profile, paths: InstallerPaths
) -> None:
    """Check the given password; a given username wins over the certificate."""
    try:
        identity = read_p12_identity(paths.user_p12, credentials.password)
    except InvalidP12Error as error:
        raise InstallerError from error
    if not credentials.username and not profile.use_other_tls_id:
        credentials.username = identity or ""


def _ask_certificate_password(
    credentials: Credentials,
    ui: UserInterface,
    profile: Profile,
    paths: InstallerPaths,
) -> None:
    """Ask for the import password until the certificate unlocks."""
    messages = ui.messages
    while True:
        if not credentials.password:
            credentials.password = ui.prompt_text(
                messages.enter_import_password, hidden=True
            )
        try:
            identity = read_p12_identity(paths.user_p12, credentials.password)
        except InvalidP12Error:
            ui.alert(messages.incorrect_password)
            credentials.password = ""
        else:
            break
    if identity and not profile.use_other_tls_id:
        credentials.username = identity
    if not credentials.username:
        credentials.username = ui.prompt_text(messages.username_prompt)
