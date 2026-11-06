"""The installation sequence."""

import logging
from dataclasses import dataclass
from pathlib import Path

from .backends.iwd import write_iwd_configs
from .backends.network_manager import NetworkManagerBackend
from .backends.wpa_supplicant import write_wpa_conf
from .certificates import save_ca
from .config import GOETTINGEN, Profile
from .credentials import Credentials, collect_credentials
from .messages import MESSAGES, Messages
from .paths import InstallerPaths
from .system import get_system_info, has_wired_interface
from .ui import GuiChoice, UserInterface, create_ui

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class InstallOptions:
    """Choices made on the command line.

    Attributes:
        username: Username, if already known.
        password: Password, if already known.
        pfx_file: Personal certificate to import.
        silent: Do not interact with the user.
        gui: Preferred user interface.
        wpa_conf: Only generate a wpa_supplicant configuration file.
        iwd_conf: Only generate an iwd configuration file.
    """

    username: str = ""
    password: str = ""
    pfx_file: Path | None = None
    silent: bool = False
    gui: GuiChoice | None = None
    wpa_conf: bool = False
    iwd_conf: bool = False


def _confirm_profile(ui: UserInterface, profile: Profile) -> None:
    """Introduce the installer and let the user back out."""
    ui.show_info(profile.welcome_text)
    ui.ask_to_continue(profile.confirmation_text)
    if profile.terms_of_use:
        ui.ask_to_continue(profile.terms_of_use)


def _wants_wired(ui: UserInterface, profile: Profile, messages: Messages) -> bool:
    """Return whether the user wants 802.1X on the wired interface."""
    if not profile.offer_wired or not has_wired_interface():
        return False
    return ui.ask(messages.wired)


def _prepare_directory(
    ui: UserInterface, paths: InstallerPaths, messages: Messages
) -> None:
    """Create the installer directory, warning if it already exists."""
    if paths.root.exists():
        ui.ask_to_continue(messages.cat_dir_exists.format(paths.root))
    paths.create()


def run_installer(
    options: InstallOptions,
    profile: Profile = GOETTINGEN,
    messages: Messages = MESSAGES,
) -> None:
    """Run the installation from the first question to the final message.

    Args:
        options: Choices made on the command line.
        profile: What to configure.
        messages: Texts to show.

    Raises:
        InstallerError: If the installation stops early.
    """
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("%s", get_system_info())
    paths = InstallerPaths.default()
    ui = create_ui(
        options.gui, silent=options.silent, title=profile.title, messages=messages
    )
    _confirm_profile(ui, profile)

    file_output_requested = options.wpa_conf or options.iwd_conf
    network_manager = (
        None if file_output_requested else NetworkManagerBackend.connect(messages)
    )
    wired = network_manager is not None and _wants_wired(ui, profile, messages)
    _prepare_directory(ui, paths, messages)
    if network_manager is None and not file_output_requested:
        ui.ask_to_continue(messages.save_wpa_conf)

    credentials = Credentials(options.username, options.password, options.pfx_file)
    collect_credentials(credentials, ui, profile, paths)

    ca = save_ca(
        profile.ca_certificate,
        paths,
        messages,
        prefer_directory=(
            network_manager is not None and network_manager.version.needs_ca_directory
        ),
    )
    if network_manager is not None:
        network_manager.install(profile, credentials, ca, paths, wired=wired)
    elif options.iwd_conf:
        write_iwd_configs(profile, credentials)
    else:
        write_wpa_conf(profile, credentials, ca, paths)
    ui.show_info(messages.installation_finished)
