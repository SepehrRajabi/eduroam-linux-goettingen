"""Command line interface."""

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from .errors import ExitCode, InstallerError
from .installer import InstallOptions, run_installer
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
        help="generate wpa_supplicant config file without configuring the system",
    )
    parser.add_argument(
        "--iwd_conf",
        action="store_true",
        help="generate iwd config file without configuring the system",
    )
    parser.add_argument(
        "--gui",
        choices=[choice.value for choice in GuiChoice],
        help="use this GUI system if present, falling back to standard choice if not",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the installer and return the process exit code.

    Args:
        argv: Command line arguments; defaults to those of the process.
    """
    args = build_parser().parse_args(argv)
    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format="DEBUG:%(message)s")
        print("Running debug mode")
    options = InstallOptions(
        username=args.username,
        password=args.password,
        pfx_file=args.pfx_file,
        silent=args.silent,
        gui=GuiChoice(args.gui) if args.gui else None,
        wpa_conf=args.wpa_conf,
        iwd_conf=args.iwd_conf,
    )
    try:
        run_installer(options)
    except InstallerError as error:
        if error.message:
            print(error.message)
        return error.exit_code
    return ExitCode.SUCCESS
