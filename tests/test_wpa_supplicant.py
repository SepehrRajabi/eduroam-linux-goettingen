"""The wpa_supplicant backends: encoding, discovery, merging and reloading."""

import dataclasses
import stat
from pathlib import Path

import pytest

from src.backends import wpa_supplicant
from src.backends.base import InstallRequest
from src.backends.wpa_supplicant import (
    BEGIN_MARKER,
    END_MARKER,
    CommandLine,
    WpaSupplicantBackend,
    WpaSupplicantTarget,
    config_line,
    control_directory,
    decode_string,
    encode_string,
    merge_config,
    network_block,
)
from src.certificates import save_ca
from src.cli import main
from src.config import EapMethod, Profile
from src.credentials import Credentials
from src.messages import MESSAGES
from src.paths import InstallerPaths
from src.system import process_command_lines, wireless_interfaces
from src.ui.silent import SilentUI

from .conftest import FakeSession

CREDENTIALS = ["--silent", "-u", "alice@uni-goettingen.de", "-p", "pa$$ 'word\""]
INJECTION = 'x"\n\tnetwork={\n\tssid="evil"\n}\n# '

EXISTING = """\
# my settings
ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev
update_config=0

network={
\tssid="home" # the flat, with a } in the comment
\tpsk="#not a comment}"
}

network={
\tssid="eduroam"
\tidentity="old@uni-goettingen.de"
}

network={
\tssid=6f6c64 # "old", written in hex
\tkey_mgmt=NONE
}

cred={
\tusername="network={"
}
"""


def _mode(path: Path) -> int:
    """Return the permission bits of ``path``."""
    return stat.S_IMODE(path.stat().st_mode)


def _fake_proc(root: Path, *processes: tuple[str, list[str]]) -> Path:
    """Create a fake ``/proc`` holding processes given as name and arguments."""
    root.mkdir(parents=True, exist_ok=True)
    for pid, (name, arguments) in enumerate(processes, start=100):
        directory = root / str(pid)
        directory.mkdir()
        (directory / "comm").write_text(f"{name}\n")
        (directory / "cmdline").write_bytes(
            b"".join(argument.encode() + b"\0" for argument in arguments)
        )
    (root / "self").mkdir()
    return root


def _fake_sys(
    root: Path, wireless: tuple[str, ...], wired: tuple[str, ...] = ()
) -> Path:
    """Create a fake ``/sys/class/net`` with wireless and wired interfaces."""
    root.mkdir(parents=True, exist_ok=True)
    for name in wireless:
        (root / name / "wireless").mkdir(parents=True)
    for name in wired:
        (root / name).mkdir(parents=True)
    return root


def _request(
    profile: Profile,
    home: Path,
    password: str = "secret",  # noqa: S107
) -> InstallRequest:
    """Return an installation request using the isolated home directory."""
    paths = InstallerPaths(home / ".config" / "cat_installer")
    paths.create()
    return InstallRequest(
        profile=profile,
        credentials=Credentials("alice@uni-goettingen.de", password),
        ca=save_ca(profile.ca_certificate, paths, MESSAGES),
        paths=paths,
        ui=SilentUI("test", MESSAGES),
        messages=MESSAGES,
    )


def _network_values(text: str) -> list[dict[str, str]]:
    """Return the settings of every network block, as wpa_supplicant reads them."""
    networks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        content = config_line(line)
        if content == "network={":
            current = {}
        elif content == "}" and current is not None:
            networks.append(current)
            current = None
        elif current is not None and content:
            key, value = content.split("=", 1)
            current[key] = value
    return networks


@pytest.fixture
def recorded_commands(
    privileged_session: FakeSession, monkeypatch: pytest.MonkeyPatch
) -> list[list[str]]:
    """Record privileged commands instead of running them; wpa_cli answers OK."""
    monkeypatch.setattr("shutil.which", lambda name, path=None: name)
    return privileged_session.commands


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("eduroam", '"eduroam"'),
        ("pa$$ 'w#rd\\", '"pa$$ \'w#rd\\"'),
        ("Grüße", '"Grüße"'),
        ("", '""'),
        ('a"b', "612262"),
        ("a\nb", "610a62"),
        ("tab\t", "74616209"),
    ],
)
def test_encode_string(value: str, expected: str) -> None:
    """Printable values are quoted, anything else is hex-encoded."""
    assert encode_string(value) == expected
    assert decode_string(encode_string(value)) == value.encode()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('"text"', b"text"),
        ('"a"b"', b'a"b'),
        ('P"a\\"b\\n\\x41\\101\\\\"', b'a"b\nAA\\'),
        ("656475", b"edu"),
        ("6564F", None),
        ("xyz", None),
        ('"open', None),
        ('"', None),
    ],
)
def test_decode_string(value: str, expected: bytes | None) -> None:
    """The three string syntaxes of wpa_supplicant are understood."""
    assert decode_string(value) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('  ssid="a#b"  # comment\n', 'ssid="a#b"'),
        ("\t# only a comment\n", ""),
        ("psk=abc#def\n", "psk=abc"),
        ('x="a"#"\n', 'x="a"'),
        ("}\r\n", "}"),
    ],
)
def test_config_line(line: str, expected: str) -> None:
    """Comments outside quotes and surrounding blanks are removed."""
    assert config_line(line) == expected


def test_network_block_unchanged(profile: Profile) -> None:
    """The file backend writes the same block as before the refactoring."""
    block = network_block(
        "eduroam",
        profile,
        Credentials("alice@uni-goettingen.de", "pa$$ 'w#rd"),
        ca_cert=Path("/home/a/.config/cat_installer/ca.pem"),
        private_key=Path("/unused"),
    )
    assert block == (
        "network={\n"
        '\tssid="eduroam"\n'
        "\tkey_mgmt=WPA-EAP\n"
        "\tpairwise=CCMP\n"
        "\tgroup=CCMP\n"
        "\teap=PEAP\n"
        '\tca_cert="/home/a/.config/cat_installer/ca.pem"\n'
        '\tidentity="alice@uni-goettingen.de"\n'
        '\taltsubject_match="DNS:eduroam.gwdg.de"\n'
        '\tphase2="auth=MSCHAPV2"\n'
        '\tpassword="pa$$ \'w#rd"\n'
        '\tanonymous_identity="wifi@uni-goettingen.de"\n'
        "}\n"
    )


@pytest.mark.parametrize("field", ["password", "identity"])
def test_network_block_cannot_be_injected(profile: Profile, field: str) -> None:
    """A value with quotes and line breaks cannot add a network or a setting."""
    credentials = Credentials(
        INJECTION if field == "identity" else "alice",
        INJECTION if field == "password" else "pw",
    )
    block = network_block(
        "eduroam", profile, credentials, ca_cert=Path("/ca"), private_key=Path("/k")
    )
    networks = _network_values(block)
    assert len(networks) == 1
    assert decode_string(networks[0][field]) == INJECTION.encode()
    assert decode_string(networks[0]["ssid"]) == b"eduroam"


def test_file_backend_escapes(isolated_home: Path) -> None:
    """``--wpa_conf`` hex-encodes a password containing a double quote."""
    assert main([*CREDENTIALS, "--wpa_conf"]) == 0
    conf = isolated_home / ".config" / "cat_installer" / "cat_installer.conf"
    networks = _network_values(conf.read_text())
    assert decode_string(networks[0]["password"]) == b"pa$$ 'word\""


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (
            ["wpa_supplicant", "-B", "-c", "/etc/a.conf", "-i", "wlan0"],
            [("wlan0", "/etc/a.conf", None)],
        ),
        (
            ["/sbin/wpa_supplicant", "-Bc/etc/a.conf", "-iwlp2s0", "-Dnl80211,wext"],
            [("wlp2s0", "/etc/a.conf", None)],
        ),
        (
            [
                *("wpa_supplicant", "-s", "-c/etc/a", "-iwlan0", "-N", "-iwlan1"),
                *("-c", "/etc/b", "-C", "DIR=/run/w GROUP=0"),
            ],
            [("wlan0", "/etc/a", None), ("wlan1", "/etc/b", "DIR=/run/w GROUP=0")],
        ),
        (["wpa_supplicant", "-u", "-s", "-O", "/run/wpa_supplicant"], []),
        (["wpa_supplicant", "-c"], []),
    ],
)
def test_parse_command_line(
    arguments: list[str], expected: list[tuple[str, str, str | None]]
) -> None:
    """Grouped, attached and separate options and ``-N`` are understood."""
    parsed = CommandLine.parse(arguments)
    assert [(i.name, i.config, i.control) for i in parsed.interfaces] == expected
    assert parsed.dbus == ("-u" in arguments)


def test_process_command_lines(tmp_path: Path) -> None:
    """Command lines are read for the named processes only."""
    proc = _fake_proc(
        tmp_path / "proc",
        ("bash", ["bash"]),
        ("wpa_supplicant", ["wpa_supplicant", "-c", "/etc/x y.conf", "-iwlan0"]),
    )
    assert process_command_lines("wpa_supplicant", proc) == [
        ["wpa_supplicant", "-c", "/etc/x y.conf", "-iwlan0"]
    ]


def test_wireless_interfaces(tmp_path: Path) -> None:
    """Interfaces with ``wireless`` or ``phy80211`` in sysfs are wireless."""
    net = _fake_sys(tmp_path / "net", ("wlp2s0",), ("eth0", "lo"))
    (net / "wlx00").mkdir()
    (net / "wlx00" / "phy80211").symlink_to(tmp_path)
    assert wireless_interfaces(net) == ["wlp2s0", "wlx00"]
    assert wireless_interfaces(tmp_path / "missing") == []


def _discover(tmp_path: Path, proc: Path, net: Path) -> WpaSupplicantBackend | None:
    """Run discovery against fake system directories."""
    return WpaSupplicantBackend.discover(
        MESSAGES,
        proc=proc,
        sys_class_net=net,
        config_directory=tmp_path / "etc",
        certificate_directory=tmp_path / "certs",
    )


def test_discover_running(tmp_path: Path) -> None:
    """The files of a running wpa_supplicant are used, but not for wired ports."""
    proc = _fake_proc(
        tmp_path / "proc",
        ("wpa_supplicant", ["wpa_supplicant", "-c/etc/w.conf", "-iwlan0"]),
        (
            "wpa_supplicant",
            [
                *("wpa_supplicant", "-Dwired", "-c/etc/wired.conf", "-ieth0"),
                *("-N", "-iwlan1", "-c/etc/w.conf", "-C/run/x"),
            ],
        ),
    )
    net = _fake_sys(tmp_path / "net", ("wlan0", "wlan1"), ("eth0",))
    backend = _discover(tmp_path, proc, net)
    assert backend is not None
    assert backend.targets == [
        WpaSupplicantTarget(
            Path("/etc/w.conf"), ("wlan0", "wlan1"), Path("/run/x"), running=True
        )
    ]


def test_discover_dbus_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A wpa_supplicant run only over D-Bus has no file to change."""
    proc = _fake_proc(
        tmp_path / "proc",
        ("wpa_supplicant", ["wpa_supplicant", "-u", "-s", "-O/run/wpa_supplicant"]),
    )
    net = _fake_sys(tmp_path / "net", ("wlan0",))
    assert _discover(tmp_path, proc, net) is None
    assert MESSAGES.wpa_supplicant_no_config in capsys.readouterr().out


def test_discover_stopped(tmp_path: Path) -> None:
    """Without wpa_supplicant, the first wireless interface gets a file."""
    net = _fake_sys(tmp_path / "net", ("wlx001122", "wlp3s0"), ("eth0",))
    backend = _discover(tmp_path, _fake_proc(tmp_path / "proc"), net)
    assert backend is not None
    assert backend.targets == [
        WpaSupplicantTarget(
            tmp_path / "etc" / "wpa_supplicant-wlp3s0.conf", ("wlp3s0",)
        )
    ]


def test_discover_stopped_prefers_existing_files(tmp_path: Path) -> None:
    """Interfaces which already have a file are the ones configured."""
    net = _fake_sys(tmp_path / "net", ("wlan0", "wlan1", "wlan2"))
    etc = tmp_path / "etc"
    etc.mkdir()
    for name in ("wlan1", "wlan2"):
        (etc / f"wpa_supplicant-{name}.conf").touch()
    backend = _discover(tmp_path, _fake_proc(tmp_path / "proc"), net)
    assert backend is not None
    assert [target.interfaces for target in backend.targets] == [
        ("wlan1",),
        ("wlan2",),
    ]


def test_discover_without_wireless(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without a wireless interface there is nothing to configure."""
    net = _fake_sys(tmp_path / "net", (), ("eth0",))
    assert _discover(tmp_path, _fake_proc(tmp_path / "proc"), net) is None
    assert MESSAGES.wpa_supplicant_no_interface in capsys.readouterr().out


def test_merge_keeps_other_settings() -> None:
    """Other networks and settings survive; old blocks for our SSIDs go."""
    region = f'{BEGIN_MARKER}\nnetwork={{\n\tssid="eduroam"\n}}\n{END_MARKER}\n'
    merged = merge_config(EXISTING, region, ["eduroam", "old"])
    assert merged.startswith(EXISTING.split("network={", maxsplit=1)[0])
    assert merged.endswith(f"}}\n\n{region}")
    ssids = [decode_string(n["ssid"]) for n in _network_values(merged)]
    assert ssids == [b"home", b"eduroam"]
    assert '\tpsk="#not a comment}"\n' in merged
    assert 'cred={\n\tusername="network={"\n}\n' in merged
    assert "old@uni-goettingen.de" not in merged


def test_merge_replaces_region_in_place() -> None:
    """A second run replaces the region where it was, keeping what follows."""
    first = f'{BEGIN_MARKER}\nnetwork={{\n\tssid="eduroam"\n}}\n{END_MARKER}\n'
    second = f"{BEGIN_MARKER}\n# new\n{END_MARKER}\n"
    text = f'a=1\n{first}network={{\n\tssid="after"\n}}\n'
    assert merge_config(text, second, ["eduroam"]) == (
        f'a=1\n{second}network={{\n\tssid="after"\n}}\n'
    )


def test_merge_survives_damaged_markers() -> None:
    """A marker without its partner never removes the rest of the file."""
    text = f'{END_MARKER}\na=1\n{BEGIN_MARKER}\nnetwork={{\n\tssid="x"\n}}\n'
    region = f"{BEGIN_MARKER}\n{END_MARKER}\n"
    assert merge_config(text, region, ["eduroam"]) == (
        f'a=1\nnetwork={{\n\tssid="x"\n}}\n\n{region}'
    )


def test_merge_leaves_unterminated_block() -> None:
    """A block missing its closing brace is left for the user to fix."""
    text = 'network={\n\tssid="eduroam"\n'
    region = f"{BEGIN_MARKER}\n{END_MARKER}\n"
    assert merge_config(text, region, ["eduroam"]) == f"{text}\n{region}"


def test_control_directory() -> None:
    """Both forms of ``ctrl_interface`` give the socket directory."""
    assert control_directory("ctrl_interface=DIR=/run/w GROUP=netdev\n") == Path(
        "/run/w"
    )
    assert control_directory("ctrl_interface=/var/run/w\n") == Path("/var/run/w")
    assert control_directory("# ctrl_interface=/run/w\n") is None
    assert control_directory("ctrl_interface=udp\n") is None


def test_install_merges_and_backs_up(
    tmp_path: Path,
    isolated_home: Path,
    profile: Profile,
    recorded_commands: list[list[str]],
) -> None:
    """The file is merged, backed up, and wpa_supplicant reloads it."""
    conf = tmp_path / "etc" / "wpa_supplicant.conf"
    conf.parent.mkdir()
    conf.write_text(EXISTING)
    conf.chmod(0o640)
    target = WpaSupplicantTarget(conf, ("wlan0",), running=True)
    backend = WpaSupplicantBackend([target], tmp_path / "certs")
    request = _request(
        dataclasses.replace(profile, del_ssids=("old",)), isolated_home, INJECTION
    )

    assert backend.install(request) is None
    text = conf.read_text()
    assert _mode(conf) == 0o600
    assert text.count(BEGIN_MARKER) == 1
    networks = _network_values(text)
    assert [decode_string(n["ssid"]) for n in networks] == [b"home", b"eduroam"]
    assert decode_string(networks[1]["password"]) == INJECTION.encode()
    assert networks[1]["ca_cert"] == f'"{tmp_path / "certs" / "ca.pem"}"'
    assert str(isolated_home) not in text
    (backup,) = conf.parent.glob("wpa_supplicant.conf.*.bak")
    assert backup.read_text() == EXISTING
    assert _mode(backup) == 0o600
    assert recorded_commands == [
        ["wpa_cli", "-p", "/run/wpa_supplicant", "-i", "wlan0", "reconfigure"]
    ]

    # Running again with a new password replaces the region in place.
    backup.unlink()
    assert backend.install(_request(profile, isolated_home, "new")) is None
    rerun = conf.read_text()
    assert rerun.count(BEGIN_MARKER) == rerun.count(END_MARKER) == 1
    assert rerun.split(BEGIN_MARKER)[0] == text.split(BEGIN_MARKER)[0]
    assert '\tpassword="new"\n' in rerun
    (backup,) = conf.parent.glob("wpa_supplicant.conf.*.bak")
    assert backup.read_text() == text

    # An identical run changes nothing and leaves no backup.
    backup.unlink()
    backend.install(_request(profile, isolated_home, "new"))
    assert conf.read_text() == rerun
    assert not list(conf.parent.glob("*.bak"))
    assert all("new" not in " ".join(command) for command in recorded_commands)


def test_install_new_file(
    tmp_path: Path, isolated_home: Path, profile: Profile
) -> None:
    """A missing file is created with a control interface and no backup."""
    conf = tmp_path / "etc" / "wpa_supplicant-wlan0.conf"
    backend = WpaSupplicantBackend(
        [WpaSupplicantTarget(conf, ("wlan0",))], tmp_path / "certs"
    )
    instructions = backend.install(_request(profile, isolated_home))
    assert instructions == MESSAGES.wpa_supplicant_start.format("wlan0")
    text = conf.read_text()
    assert config_line(text.splitlines()[2]) == "ctrl_interface=DIR=/run/wpa_supplicant"
    assert BEGIN_MARKER in text
    assert _mode(conf) == 0o600
    assert list(conf.parent.iterdir()) == [conf]


def test_install_tls_certificates(
    tmp_path: Path, isolated_home: Path, profile: Profile
) -> None:
    """EAP-TLS gets the CA and the personal certificate in a system directory."""
    certs = tmp_path / "etc" / "wpa_supplicant" / "eduroam-gwdg"
    conf = tmp_path / "etc" / "wpa_supplicant" / "wpa_supplicant-wlan0.conf"
    request = _request(
        dataclasses.replace(profile, eap_outer=EapMethod.TLS), isolated_home
    )
    request.paths.user_p12.write_bytes(b"\x30\x82\x00\xffp12")
    backend = WpaSupplicantBackend([WpaSupplicantTarget(conf, ("wlan0",))], certs)
    backend.install(request)
    assert (certs / "user.p12").read_bytes() == b"\x30\x82\x00\xffp12"
    assert _mode(certs / "user.p12") == 0o600
    assert _mode(certs / "ca.pem") == 0o644
    assert (certs / "ca.pem").read_text() == request.ca.path.read_text()
    (network,) = _network_values(conf.read_text())
    assert network["private_key"] == f'"{certs / "user.p12"}"'
    assert network["private_key_passwd"] == '"secret"'


def test_reload_failure_asks_for_restart(
    tmp_path: Path,
    isolated_home: Path,
    profile: Profile,
    recorded_commands: list[list[str]],
    privileged_session: FakeSession,
) -> None:
    """If wpa_cli fails, the user is asked to restart wpa_supplicant."""
    privileged_session.command_output = "FAIL\n"
    conf = tmp_path / "w.conf"
    conf.write_text("ctrl_interface=/run/w\n")
    backend = WpaSupplicantBackend(
        [WpaSupplicantTarget(conf, ("wlan0",), running=True)], tmp_path / "certs"
    )
    instructions = backend.install(_request(profile, isolated_home))
    assert instructions == MESSAGES.wpa_supplicant_restart.format(conf)


def test_reload_without_control_interface(
    tmp_path: Path,
    isolated_home: Path,
    profile: Profile,
    recorded_commands: list[list[str]],
) -> None:
    """Without a control interface wpa_cli is not tried."""
    conf = tmp_path / "w.conf"
    conf.write_text("ap_scan=1\n")
    backend = WpaSupplicantBackend(
        [WpaSupplicantTarget(conf, ("wlan0",), running=True)], tmp_path / "certs"
    )
    instructions = backend.install(_request(profile, isolated_home))
    assert instructions == MESSAGES.wpa_supplicant_restart.format(conf)
    assert recorded_commands == []


def test_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorded_commands: list[list[str]]
) -> None:
    """``--backend wpa_supplicant`` updates the file of the running service."""
    etc = tmp_path / "etc" / "wpa_supplicant"
    conf = etc / "wpa_supplicant-wlan0.conf"
    proc = _fake_proc(
        tmp_path / "proc",
        ("wpa_supplicant", ["wpa_supplicant", f"-c{conf}", "-iwlan0"]),
    )
    monkeypatch.setattr(wpa_supplicant, "PROC", proc)
    monkeypatch.setattr(
        wpa_supplicant, "SYS_CLASS_NET", _fake_sys(tmp_path / "net", ("wlan0",))
    )
    monkeypatch.setattr(wpa_supplicant, "CONFIG_DIRECTORY", etc)
    monkeypatch.setattr(wpa_supplicant, "CERTIFICATE_DIRECTORY", etc / "eduroam-gwdg")
    assert main([*CREDENTIALS, "--backend", "wpa_supplicant"]) == 0
    (network,) = _network_values(conf.read_text())
    assert decode_string(network["password"]) == b"pa$$ 'word\""
    assert network["ca_cert"] == f'"{etc / "eduroam-gwdg" / "ca.pem"}"'
    assert recorded_commands[-1][-1] == "reconfigure"
