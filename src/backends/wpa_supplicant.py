"""wpa_supplicant configuration: a file for the user, or the running service.

The configuration format is described in wpa_supplicant.conf(5). It is read line
by line: a line ``network={`` opens a block and a line ``}`` closes it, ``#``
starts a comment outside double quotes, and blanks around a line are ignored.
"""

import logging
import os
import re
import shutil
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Self

from ..certificates import CaStore
from ..config import PASSWORD_METHODS, EapMethod, Profile
from ..credentials import Credentials
from ..errors import InstallerError
from ..messages import Messages
from ..paths import InstallerPaths, write_private_file
from ..privileged import (
    read_system_file,
    run_privileged,
    write_system_bytes,
    write_system_file,
)
from ..system import process_command_lines, wireless_interfaces
from .base import Backend, BackendChoice, InstallRequest

logger = logging.getLogger(__name__)

#: Mount point of the proc file system, read to find the running wpa_supplicant.
PROC = Path("/proc")
#: The sysfs directory listing the network interfaces.
SYS_CLASS_NET = Path("/sys/class/net")
#: Where the per-interface configuration files of ``wpa_supplicant@.service`` are.
CONFIG_DIRECTORY = Path("/etc/wpa_supplicant")
#: Where the certificates referenced by the configuration are installed.
CERTIFICATE_DIRECTORY = CONFIG_DIRECTORY / "eduroam-gwdg"

#: First line of the part of a configuration file managed by the installer.
BEGIN_MARKER = "# BEGIN eduroam-gwdg-linux-goettingen"
#: Last line of the part of a configuration file managed by the installer.
END_MARKER = "# END eduroam-gwdg-linux-goettingen"
_REGION_NOTE = "# Written by the eduroam installer; replaced when it runs again.\n"
#: Start of a configuration file created by the installer.
NEW_FILE_HEADER = """\
# wpa_supplicant configuration, see wpa_supplicant.conf(5).
# Created by the eduroam installer.
ctrl_interface=DIR=/run/wpa_supplicant

"""

#: Options of wpa_supplicant which take an argument, from its ``getopt`` string.
_OPTIONS_WITH_ARGUMENT = frozenset("bcCDefgGiImoOpPz")
#: A line opening a block, such as ``network={`` or ``cred={``.
_BLOCK_START = re.compile(r"[^\s=]+=\{")
_HEX = re.compile(r"(?:[0-9a-fA-F]{2})*")
_PRINTF_ESCAPES = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t", "e": "\x1b"}


def encode_string(value: str) -> str:
    """Encode a value for a string setting of wpa_supplicant.

    Printable text without double quotes is written in double quotes, as usual.
    Anything else is written as unquoted hexadecimal UTF-8, which every string
    setting accepts, including ``password``. That form cannot contain a quote,
    a ``#`` or a line break, so no value can end its line early, start a comment
    or inject settings. Quotes inside quotes are not safe: wpa_supplicant pairs
    them when looking for comments, so ``"a"#"`` would be read as ``a``.
    """
    if value.isprintable() and '"' not in value:
        return f'"{value}"'
    return value.encode("utf-8").hex()


def decode_string(value: str) -> bytes | None:
    """Decode a string setting as wpa_supplicant does.

    Args:
        value: The setting after the equal sign: ``"text"``, ``P"escaped text"``
            or hexadecimal.

    Returns:
        The bytes meant, or ``None`` if wpa_supplicant would reject the value.
    """
    if value.startswith('"'):
        end = value.rfind('"')
        return value[1:end].encode("utf-8") if end == len(value) - 1 > 0 else None
    if value.startswith('P"'):
        end = value.rfind('"')
        return _printf_decode(value[2:end]) if end == len(value) - 1 > 1 else None
    if _HEX.fullmatch(value):
        return bytes.fromhex(value)
    return None


def _printf_decode(text: str) -> bytes:
    """Undo the escaping of a ``P"..."`` string, like ``printf_decode`` does."""
    decoded = bytearray()
    raw = text.encode("utf-8")
    position = 0
    while position < len(raw):
        byte = raw[position]
        position += 1
        if byte != ord("\\") or position >= len(raw):
            decoded.append(byte)
            continue
        escape = chr(raw[position])
        position += 1
        if escape in _PRINTF_ESCAPES:
            decoded += _PRINTF_ESCAPES[escape].encode()
        elif escape == "x":
            digits = re.match(rb"[0-9a-fA-F]{1,2}", raw[position:])
            if digits:
                decoded.append(int(digits.group(), 16))
                position += len(digits.group())
        elif escape in "01234567":
            digits = re.match(rb"[0-7]{0,2}", raw[position:])
            octal = escape.encode() + (digits.group() if digits else b"")
            decoded.append(int(octal, 8) & 0xFF)
            position += len(octal) - 1
    return bytes(decoded)


def config_line(line: str) -> str:
    """Return what wpa_supplicant reads from a line of its configuration file.

    Blanks around the line and comments are removed; a ``#`` between double
    quotes is not a comment. Comment and empty lines give an empty string.
    """
    content = line.lstrip(" \t\r")
    if not content or content[0] in "#\n":
        return ""
    end = content.find("#")
    start = 0
    while end != -1:
        opening = content.find('"', start)
        if opening == -1 or opening > end:
            break
        closing = content.find('"', opening + 1)
        if closing == -1:
            break
        start = closing + 1
        if start > end:
            end = content.find("#", start)
    if end != -1:
        content = content[:end]
    return content.rstrip("\n \t\r")


@dataclass(frozen=True, slots=True)
class _Block:
    """A block of a configuration file, such as ``network={ ... }``.

    Attributes:
        start: Index of the opening line.
        end: Index of the closing ``}`` line.
        kind: What comes before ``={``, such as ``network``.
        ssid: The SSID of a network block, if it has a readable one.
    """

    start: int
    end: int
    kind: str
    ssid: bytes | None


def _blocks(lines: Sequence[str], skipped: Collection[int]) -> Iterator[_Block]:
    """Yield the complete blocks among ``lines``, ignoring those in ``skipped``.

    A block missing its closing line is not yielded, so it is left alone.
    """
    opening: tuple[int, str] | None = None
    ssid: bytes | None = None
    for index, line in enumerate(lines):
        if index in skipped:
            continue
        content = config_line(line)
        if opening is None:
            if _BLOCK_START.fullmatch(content):
                opening = (index, content[:-2])
                ssid = None
        elif content == "}":
            yield _Block(opening[0], index, opening[1], ssid)
            opening = None
        elif content.startswith("ssid="):
            ssid = decode_string(content.removeprefix("ssid="))


def _managed_lines(lines: Sequence[str]) -> tuple[set[int], int | None]:
    """Find the regions written by the installer.

    A marker without its partner is dropped alone, so that a damaged region never
    takes the rest of the file with it.

    Returns:
        The indices of the lines to drop, and where the first complete region
        started, if there is one.
    """
    dropped: set[int] = set()
    first: int | None = None
    begin: int | None = None
    for index, line in enumerate(lines):
        marker = line.strip()
        if marker == BEGIN_MARKER:
            if begin is not None:
                dropped.add(begin)
            begin = index
        elif marker == END_MARKER:
            if begin is None:
                dropped.add(index)
                continue
            dropped.update(range(begin, index + 1))
            first = begin if first is None else first
            begin = None
    if begin is not None:
        dropped.add(begin)
    return dropped, first


def merge_config(existing: str, region: str, ssids: Collection[str]) -> str:
    """Put the installer's region into an existing configuration file.

    Everything outside the region is kept, except network blocks for ``ssids``,
    which would compete with the new ones. A previous region is replaced where it
    was; otherwise the region is appended.

    Args:
        existing: Current file content.
        region: The new region, markers included, ending with a newline.
        ssids: Networks whose blocks outside the region are removed.
    """
    lines = existing.splitlines(keepends=True)
    dropped, insert_at = _managed_lines(lines)
    removed = {ssid.encode("utf-8") for ssid in ssids}
    for block in _blocks(lines, set(dropped)):
        if block.kind == "network" and block.ssid in removed:
            logger.debug("removing lines %d-%d", block.start + 1, block.end + 1)
            dropped.update(range(block.start, block.end + 1))

    merged: list[str] = []
    for index, line in enumerate(lines):
        if index == insert_at:
            merged.append(region)
        if index not in dropped:
            merged.append(line)
    if insert_at is None:
        text = "".join(merged)
        if text and not text.endswith("\n"):
            merged.append("\n")
        if text.strip() and not text.endswith("\n\n"):
            merged.append("\n")
        merged.append(region)
    return "".join(merged)


def control_directory(text: str) -> Path | None:
    """Return the control socket directory set by ``ctrl_interface``, if any.

    Args:
        text: Configuration file content, or the value of ``-C``.
    """
    for line in text.splitlines():
        content = config_line(line)
        if content.startswith("ctrl_interface="):
            value = content.removeprefix("ctrl_interface=")
            break
    else:
        return None
    return _control_path(value)


def _control_path(value: str) -> Path | None:
    """Return the directory of a ``ctrl_interface`` value, if it is a path."""
    if value.startswith("DIR="):
        value = value.removeprefix("DIR=").split(" GROUP=")[0]
    path = Path(value.strip())
    return path if path.is_absolute() else None


def network_block(
    ssid: str,
    profile: Profile,
    credentials: Credentials,
    *,
    ca_cert: Path,
    private_key: Path,
) -> str:
    """Return the ``network`` block for one SSID.

    Args:
        ssid: Network name.
        profile: The settings of the connection.
        credentials: What to authenticate with.
        ca_cert: The CA certificate file.
        private_key: The personal certificate, for EAP-TLS.
    """
    lines = [
        f"ssid={encode_string(ssid)}",
        "key_mgmt=WPA-EAP",
        "pairwise=CCMP",
        "group=CCMP",
        f"eap={profile.eap_outer}",
        f"ca_cert={encode_string(str(ca_cert))}",
        f"identity={encode_string(credentials.username)}",
        f"altsubject_match={encode_string(';'.join(profile.servers))}",
    ]
    if profile.eap_outer in PASSWORD_METHODS:
        outer_identity = profile.outer_identity(credentials.username)
        lines += [
            f"phase2={encode_string(f'auth={profile.eap_inner}')}",
            f"password={encode_string(credentials.password)}",
            f"anonymous_identity={encode_string(outer_identity)}",
        ]
    elif profile.eap_outer is EapMethod.TLS:
        lines += [
            f"private_key_passwd={encode_string(credentials.password)}",
            f"private_key={encode_string(str(private_key))}",
        ]
    body = "\n".join(f"\t{line}" for line in lines)
    return f"network={{\n{body}\n}}\n"


def write_wpa_conf(
    profile: Profile,
    credentials: Credentials,
    ca: CaStore,
    paths: InstallerPaths,
) -> None:
    """Write the wpa_supplicant configuration for all SSIDs of ``profile``."""
    logger.debug("writing: %s", paths.wpa_conf)
    blocks = (
        network_block(
            ssid, profile, credentials, ca_cert=ca.path, private_key=paths.user_p12
        )
        for ssid in profile.ssids
    )
    write_private_file(paths.wpa_conf, "".join(blocks))


@dataclass(slots=True)
class WpaSupplicantFileBackend(Backend):
    """Writes a wpa_supplicant configuration file to the installer directory."""

    choice: ClassVar[BackendChoice] = BackendChoice.WPA_SUPPLICANT_FILE
    label: ClassVar[str] = "wpa_supplicant configuration file"

    @classmethod
    def connect(cls, messages: Messages) -> Self:
        """Return the backend, which is always usable."""
        return cls()

    def install(self, request: InstallRequest) -> None:
        """Write the file."""
        write_wpa_conf(request.profile, request.credentials, request.ca, request.paths)


@dataclass(slots=True)
class _Interface:
    """The options of one interface on a wpa_supplicant command line.

    Attributes:
        name: Interface name, from ``-i``.
        config: Configuration file, from ``-c``.
        control: Control interface, from ``-C``.
    """

    name: str | None = None
    config: str | None = None
    control: str | None = None


@dataclass(frozen=True, slots=True)
class CommandLine:
    """What a wpa_supplicant command line says about its configuration.

    Attributes:
        interfaces: The interfaces given, separated on the command line by ``-N``.
        dbus: Whether ``-u`` enables the D-Bus interface, through which another
            program can add interfaces and networks.
    """

    interfaces: tuple[_Interface, ...]
    dbus: bool

    @classmethod
    def parse(cls, arguments: Sequence[str]) -> Self:
        """Parse a command line as wpa_supplicant's ``getopt`` call does.

        Options may be grouped and their arguments attached, as in ``-Bc/etc/x``
        or ``-iwlan0``, or separate, as in ``-c /etc/x``.

        Args:
            arguments: The command line, the program itself first.
        """
        interfaces = [_Interface()]
        dbus = False
        remaining = iter(arguments[1:])
        for argument in remaining:
            if argument == "--":
                break
            if not argument.startswith("-"):
                continue
            for position, option in enumerate(argument[1:], start=2):
                if option in _OPTIONS_WITH_ARGUMENT:
                    value = argument[position:] or next(remaining, None)
                    if value is not None:
                        _set_option(interfaces[-1], option, value)
                    break
                if option == "N":
                    interfaces.append(_Interface())
                elif option == "u":
                    dbus = True
        return cls(
            tuple(i for i in interfaces if i.name or i.config or i.control), dbus
        )


def _set_option(interface: _Interface, option: str, value: str) -> None:
    """Record an interface option of the command line."""
    match option:
        case "i":
            interface.name = value
        case "c":
            interface.config = value
        case "C":
            interface.control = value


@dataclass(frozen=True, slots=True)
class WpaSupplicantTarget:
    """A configuration file of wpa_supplicant to install the networks into.

    Attributes:
        config: The configuration file.
        interfaces: The wireless interfaces using it.
        control: Control socket directory given on the command line with ``-C``;
            otherwise ``ctrl_interface`` of the file applies.
        running: Whether wpa_supplicant runs with this file.
    """

    config: Path
    interfaces: tuple[str, ...] = ()
    control: Path | None = None
    running: bool = False


def _is_wired(interface: str | None, sys_class_net: Path, wireless: list[str]) -> bool:
    """Return whether ``interface`` exists and is known not to be wireless."""
    if interface is None:
        return False
    return interface not in wireless and (sys_class_net / interface).exists()


def _running_targets(
    command_lines: Sequence[CommandLine], sys_class_net: Path, wireless: list[str]
) -> list[WpaSupplicantTarget]:
    """Return the configuration files of the running wpa_supplicant processes.

    Interfaces known not to be wireless, such as an Ethernet port doing 802.1X,
    are left out. So are relative configuration paths, which depend on the
    directory wpa_supplicant was started from. Interfaces sharing a file are
    grouped into one target.
    """
    targets: dict[Path, WpaSupplicantTarget] = {}
    for command_line in command_lines:
        for interface in command_line.interfaces:
            if interface.config is None:
                continue
            config = Path(interface.config)
            if not config.is_absolute():
                logger.debug("ignoring relative configuration path %s", config)
                continue
            if _is_wired(interface.name, sys_class_net, wireless):
                logger.debug("ignoring wired interface %s", interface.name)
                continue
            previous = targets.get(config, WpaSupplicantTarget(config, running=True))
            names = previous.interfaces
            if interface.name and interface.name not in names:
                names = (*names, interface.name)
            control = previous.control
            if control is None and interface.control is not None:
                control = _control_path(interface.control)
            targets[config] = WpaSupplicantTarget(config, names, control, running=True)
    return list(targets.values())


def _stopped_targets(
    wireless: list[str], config_directory: Path
) -> list[WpaSupplicantTarget]:
    """Return the files ``wpa_supplicant@<interface>.service`` would read.

    The files of every wireless interface which already has one are updated, as
    the user set those interfaces up. If none has a file, only the first wireless
    interface in alphabetical order gets one; that is usually the built-in card
    (``wlp…``) rather than a USB adapter (``wlx…``).
    """
    files = {
        interface: config_directory / f"wpa_supplicant-{interface}.conf"
        for interface in wireless
    }
    chosen = [interface for interface, path in files.items() if path.exists()]
    if not chosen:
        chosen = wireless[:1]
    return [WpaSupplicantTarget(files[name], (name,)) for name in chosen]


def _wpa_cli() -> str | None:
    """Return the path of ``wpa_cli``, which is often in ``sbin``."""
    search_path = os.pathsep.join(
        (os.environ.get("PATH", os.defpath), "/usr/sbin", "/sbin")
    )
    return shutil.which("wpa_cli", path=search_path)


@dataclass(slots=True)
class WpaSupplicantBackend(Backend):
    """Installs the networks into the configuration of a standalone wpa_supplicant.

    This is for systems where wpa_supplicant is not run by a network manager,
    but for example by ``wpa_supplicant@.service`` with systemd-networkd, by
    ifupdown or by dhcpcd. Only the region between :data:`BEGIN_MARKER` and
    :data:`END_MARKER` is written, together with the removal of other network
    blocks for the same SSIDs; a timestamped backup of the previous file is kept
    next to it. Files are replaced atomically, so wpa_supplicant never reads a
    partly written one; the new file belongs to root with mode 600.

    wpa_supplicant runs as root and may start before home directories are
    mounted, so the certificates are copied to :attr:`certificate_directory`.

    Attributes:
        targets: The configuration files to update.
        certificate_directory: Where the certificates are installed.
    """

    choice: ClassVar[BackendChoice] = BackendChoice.WPA_SUPPLICANT
    label: ClassVar[str] = "wpa_supplicant"
    service: ClassVar[str | None] = "wpa_supplicant"
    needs_root: ClassVar[bool] = True

    targets: list[WpaSupplicantTarget] = field(default_factory=list)
    certificate_directory: Path = CERTIFICATE_DIRECTORY

    @classmethod
    def connect(cls, messages: Messages) -> Self | None:
        """Find the configuration files, explaining on stdout if there are none.

        Returns:
            The backend, or ``None`` if wpa_supplicant cannot be configured.
        """
        return cls.discover(
            messages,
            proc=PROC,
            sys_class_net=SYS_CLASS_NET,
            config_directory=CONFIG_DIRECTORY,
            certificate_directory=CERTIFICATE_DIRECTORY,
        )

    @classmethod
    def discover(
        cls,
        messages: Messages,
        *,
        proc: Path,
        sys_class_net: Path,
        config_directory: Path,
        certificate_directory: Path,
    ) -> Self | None:
        """Find the configuration files of wpa_supplicant.

        If wpa_supplicant runs, the files named by ``-c`` on its command lines are
        used, for every wireless interface. If it only runs for another program
        over D-Bus, it has no file to change, and it cannot be configured. If it
        does not run, the per-interface files of ``wpa_supplicant@.service`` are
        used (see :func:`_stopped_targets`).

        Args:
            messages: Texts to show.
            proc: Mount point of the proc file system.
            sys_class_net: The sysfs directory listing the network interfaces.
            config_directory: Where the per-interface files are.
            certificate_directory: Where the certificates are installed.

        Returns:
            The backend, or ``None`` if wpa_supplicant cannot be configured. The
            reason has then been printed.
        """
        wireless = wireless_interfaces(sys_class_net)
        command_lines = [
            CommandLine.parse(arguments)
            for arguments in process_command_lines("wpa_supplicant", proc)
        ]
        logger.debug("wpa_supplicant processes: %s", command_lines)
        targets = _running_targets(command_lines, sys_class_net, wireless)
        if not targets and any(
            all(interface.config is None for interface in command_line.interfaces)
            for command_line in command_lines
        ):
            print(messages.wpa_supplicant_no_config)
            return None
        if not targets:
            targets = _stopped_targets(wireless, config_directory)
        if not targets:
            print(messages.wpa_supplicant_no_interface)
            return None
        logger.debug("wpa_supplicant targets: %s", targets)
        return cls(targets, certificate_directory)

    def install(self, request: InstallRequest) -> str | None:
        """Install the certificates, update the files and reload wpa_supplicant.

        Returns:
            What the user still needs to do: start or restart wpa_supplicant,
            if it was not running or could not be reloaded.
        """
        ca_cert, private_key = self._install_certificates(request)
        profile = request.profile
        blocks = "".join(
            network_block(
                ssid,
                profile,
                request.credentials,
                ca_cert=ca_cert,
                private_key=private_key,
            )
            for ssid in profile.ssids
        )
        region = f"{BEGIN_MARKER}\n{_REGION_NOTE}{blocks}{END_MARKER}\n"
        instructions: list[str] = []
        for target in self.targets:
            text = _update_config(
                target.config,
                region,
                (*profile.ssids, *profile.del_ssids),
                request.messages,
            )
            instructions += _reload(target, text, request.messages)
        return "\n\n".join(instructions) or None

    def _install_certificates(self, request: InstallRequest) -> tuple[Path, Path]:
        """Copy the CA and, for EAP-TLS, the personal certificate to the system.

        Returns:
            The installed CA file and personal certificate.
        """
        messages = request.messages
        ca_cert = self.certificate_directory / "ca.pem"
        if request.ca.is_directory:
            ca_text = f"{request.profile.ca_certificate}\n"
        else:
            ca_text = request.ca.path.read_text(encoding="utf-8")
        write_system_file(ca_cert, ca_text, messages, mode=0o644)
        private_key = self.certificate_directory / "user.p12"
        if request.profile.eap_outer is EapMethod.TLS:
            write_system_bytes(
                private_key, request.paths.user_p12.read_bytes(), messages, mode=0o600
            )
        return ca_cert, private_key


def _update_config(
    config: Path, region: str, ssids: Collection[str], messages: Messages
) -> str:
    """Merge ``region`` into a configuration file, backing up the old content.

    Returns:
        The new content of the file.
    """
    existing = read_system_file(config, messages)
    if existing is None:
        text = f"{NEW_FILE_HEADER}{region}"
    else:
        text = merge_config(existing, region, ssids)
        if text == existing:
            return text
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        backup = config.with_name(f"{config.name}.{stamp}.bak")
        write_system_file(backup, existing, messages, mode=0o600)
    write_system_file(config, text, messages, mode=0o600)
    return text


def _reload(target: WpaSupplicantTarget, text: str, messages: Messages) -> list[str]:
    """Make a running wpa_supplicant read its file again, as far as possible.

    ``wpa_cli reconfigure`` needs a control interface, which ``-C`` or
    ``ctrl_interface`` in the file enables.

    Args:
        target: The file and its interfaces.
        text: The new content of the file.
        messages: Texts to show.

    Returns:
        Instructions for the user, if wpa_supplicant must be started or restarted
        by hand.
    """
    if not target.running:
        return [
            messages.wpa_supplicant_start.format(name) for name in target.interfaces
        ]
    control = target.control or control_directory(text)
    wpa_cli = _wpa_cli()
    if control is None or wpa_cli is None or not target.interfaces:
        return [messages.wpa_supplicant_restart.format(target.config)]
    for interface in target.interfaces:
        command = [wpa_cli, "-p", str(control), "-i", interface, "reconfigure"]
        try:
            output = run_privileged(command, target.config, messages)
        except InstallerError:
            output = "FAIL"
        if "FAIL" in output:
            logger.debug("wpa_cli reconfigure failed on %s", interface)
            return [messages.wpa_supplicant_restart.format(target.config)]
    return []
