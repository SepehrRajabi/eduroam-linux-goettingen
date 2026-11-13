"""Command line interface."""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from .backends import BackendChoice
from .errors import ExitCode, InstallerError
from .installer import InstallOptions, run_installer
from .privileged_helper import HELPER_FLAG, serve
from .ui import GuiChoice


def build_parser() -> argparse.ArgumentParser:
    """Return the parser for the command line options."""
    parser = argparse.ArgumentParser(description="eduroam linux installer.")
    parser.add_argument("--debug", "-d", action="store_true", help="set debug flag")
    parser.add_argument(
        "--username",
        "-u",
        default="",
        help="set username; the --password and --silent options should be used "
        "together with --username",
    )
    parser.add_argument(
        "--password",
        "-p",
        default="",
        help="set the password; the --username and --silent options should be used "
        "together with --password",
    )
    parser.add_argument("--silent", "-s", action="store_true", help="set silent flag")
    parser.add_argument(
        "--pfxfile",
        dest="pfx_file",
        type=Path,
        help="set path to user certificate file",
    )
    parser.add_argument(
        "--wpa_conf",
        action="store_true",
        help="generate wpa_supplicant config file without configuring the system; "
        f"same as --backend {BackendChoice.WPA_SUPPLICANT_FILE}",
    )
    parser.add_argument(
        "--iwd_conf",
        action="store_true",
        help="generate iwd config file without configuring the system; "
        f"same as --backend {BackendChoice.IWD_FILE}",
    )
    parser.add_argument(
        "--backend",
        choices=[choice.value for choice in BackendChoice],
        help="configure this network service instead of detecting the running one",
    )
    parser.add_argument(
        "--gui",
        choices=[choice.value for choice in GuiChoice],
        help="use this GUI system if present, falling back to standard choice if not",
    )
    return parser


def _backend_choice(args: argparse.Namespace) -> BackendChoice | None:
    """Return the backend requested by ``--backend`` or its older aliases."""
    if args.backend:
        return BackendChoice(args.backend)
    if args.iwd_conf:
        return BackendChoice.IWD_FILE
    if args.wpa_conf:
        return BackendChoice.WPA_SUPPLICANT_FILE
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Run the installer and return the process exit code.

    Args:
        argv: Command line arguments; defaults to those of the process.
    """
    arguments = sys.argv[1:] if argv is None else list(argv)
    if arguments == [HELPER_FLAG]:
        # A frozen executable runs itself as the privileged helper.
        return serve(sys.stdin.buffer, sys.stdout.buffer)
    parser = build_parser()
    args = parser.parse_args(arguments)
    if args.backend and (args.wpa_conf or args.iwd_conf):
        parser.error("--backend cannot be combined with --wpa_conf or --iwd_conf")
    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format="DEBUG:%(message)s")
        print("Running debug mode")
    options = InstallOptions(
        username=args.username,
        password=args.password,
        pfx_file=args.pfx_file,
        silent=args.silent,
        gui=GuiChoice(args.gui) if args.gui else None,
        backend=_backend_choice(args),
    )
    try:
        run_installer(options)
    except InstallerError as error:
        if error.message:
            print(error.message)
        return error.exit_code
    return ExitCode.SUCCESS
