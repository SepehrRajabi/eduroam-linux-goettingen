"""The iwd backends: network filenames, value escaping and the state directory."""

import dataclasses
import stat
from pathlib import Path

import pytest

from src.backends import iwd
from src.backends.base import InstallRequest
from src.backends.iwd import (
    IwdBackend,
    escape_value,
    network_filename,
    render_config,
    write_iwd_configs,
)
from src.certificates import save_ca
from src.cli import main
from src.config import EapMethod, Profile
from src.credentials import Credentials
from src.messages import MESSAGES
from src.paths import InstallerPaths
from src.ui.silent import SilentUI

CREDENTIALS = ["--silent", "-u", "alice@uni-goettingen.de", "-p", "pa$$ 'word\""]


def _mode(path: Path) -> int:
    """Return the permission bits of ``path``."""
    return stat.S_IMODE(path.stat().st_mode)


def _unescape(value: str) -> str:
    """Decode an iwd value the way ell's ``unescape_value`` does."""
    escapes = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}
    result = []
    characters = iter(value)
    for character in characters:
        result.append(escapes[next(characters)] if character == "\\" else character)
    return "".join(result)


def _settings(text: str) -> dict[str, list[str]]:
    """Parse the ``[Security]`` group of an iwd file into raw values by key."""
    values: dict[str, list[str]] = {}
    group = ""
    for line in text.splitlines():
        stripped = line.lstrip(" \t")
        if stripped.startswith("["):
            group = stripped
        elif group == "[Security]" and "=" in stripped:
            key, value = stripped.split("=", 1)
            values.setdefault(key, []).append(value.lstrip(" \t"))
    return values


def _fake_proc(root: Path, *names: str) -> Path:
    """Create a fake ``/proc`` with one process per name."""
    for pid, name in enumerate(names, start=100):
        directory = root / str(pid)
        directory.mkdir(parents=True)
        (directory / "comm").write_text(f"{name}\n")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _request(profile: Profile, home: Path) -> InstallRequest:
    """Return an installation request using the isolated home directory."""
    paths = InstallerPaths(home / ".config" / "cat_installer")
    paths.create()
    return InstallRequest(
        profile=profile,
        credentials=Credentials("alice@uni-goettingen.de", "secret"),
        ca=save_ca(profile.ca_certificate, paths, MESSAGES),
        paths=paths,
        ui=SilentUI("test", MESSAGES),
        messages=MESSAGES,
    )


@pytest.mark.parametrize(
    ("ssid", "expected"),
    [
        ("eduroam", "eduroam.8021x"),
        ("My net_2-go", "My net_2-go.8021x"),
        ("eduroam.de", "=656475726f616d2e6465.8021x"),
        ("Café", "=436166c3a9.8021x"),
        ("a/b", "=612f62.8021x"),
        ("../x", "=2e2e2f78.8021x"),
    ],
)
def test_network_filename(ssid: str, expected: str) -> None:
    """Safe SSIDs are used verbatim, others hex-encoded after ``=``."""
    assert network_filename(ssid) == expected


def test_network_filename_extension() -> None:
    """Other security types only change the extension."""
    assert network_filename("eduroam", "psk") == "eduroam.psk"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("plain", "plain"),
        ("pa$$ 'word\"", "pa$$ 'word\""),
        ("  lead", "\\s\\slead"),
        ("\tlead", "\\tlead"),
        ("a b\tc", "a b\tc"),
        ("back\\slash", "back\\\\slash"),
        ("line\nbreak\r", "line\\nbreak\\r"),
        ("\n ", "\\n "),
    ],
)
def test_escape_value(value: str, expected: str) -> None:
    """Values are escaped like ell does it, and decode back to the original."""
    assert escape_value(value) == expected
    assert _unescape(escape_value(value)) == value


@pytest.mark.parametrize("method", [EapMethod.PEAP, EapMethod.TTLS, EapMethod.PWD])
def test_render_config_cannot_be_injected(profile: Profile, method: EapMethod) -> None:
    """A hostile password stays one value and adds no setting or group."""
    password = " pw\\s\n[Settings]\nAutoConnect=false\r\nEAP-Method=MD5"
    credentials = Credentials("alice@uni-goettingen.de", password)
    text = render_config(dataclasses.replace(profile, eap_outer=method), credentials)
    settings = _settings(text)
    assert settings["EAP-Method"] == [str(method)]
    key = "EAP-Password" if method is EapMethod.PWD else f"EAP-{method}-Phase2-Password"
    assert [_unescape(value) for value in settings[key]] == [password]
    lines = text.splitlines()
    assert lines.count("[Settings]") == 1
    assert "AutoConnect=false" not in lines


def test_render_config_unchanged_for_plain_values(profile: Profile) -> None:
    """Ordinary credentials are written as before."""
    text = render_config(profile, Credentials("alice@uni-goettingen.de", "pa$$ wd"))
    assert "\nEAP-PEAP-Phase2-Password=pa$$ wd\n" in text
    assert "\nEAP-Identity=wifi@uni-goettingen.de\n" in text
    assert "\nEAP-PEAP-ServerDomainMask=eduroam.gwdg.de\n" in text


def test_file_backend_uses_encoded_names(profile: Profile) -> None:
    """The file output follows the iwd naming rules too."""
    special = dataclasses.replace(profile, ssids=("eduroam", "uni.wlan"))
    write_iwd_configs(special, Credentials("alice", "pw"))
    assert Path("eduroam.8021x").exists()
    assert Path("=756e692e776c616e.8021x").exists()


def test_install_and_remove(
    tmp_path: Path, isolated_home: Path, profile: Profile
) -> None:
    """Every SSID gets a private file; files of removed SSIDs are deleted."""
    directory = tmp_path / "var" / "lib" / "iwd"
    directory.mkdir(parents=True)
    stale = directory / "=6f6c642e6e6574.8021x"
    stale.write_text("old")
    unrelated = directory / "home.psk"
    unrelated.write_text("keep")
    request = _request(
        dataclasses.replace(
            profile, ssids=("eduroam", "x.y"), del_ssids=("old.net", "gone")
        ),
        isolated_home,
    )
    assert IwdBackend(directory=directory).install(request) is None
    for name in ("eduroam.8021x", "=782e79.8021x"):
        assert _mode(directory / name) == 0o600
        assert "EAP-PEAP-Phase2-Password=secret\n" in (directory / name).read_text()
    assert not stale.exists()
    assert unrelated.read_text() == "keep"


def test_install_tells_to_start_iwd(
    tmp_path: Path, isolated_home: Path, profile: Profile
) -> None:
    """If iwd is installed but stopped, the user is told how to start it."""
    backend = IwdBackend(directory=tmp_path / "iwd", running=False)
    assert backend.install(_request(profile, isolated_home)) == MESSAGES.iwd_start


def test_connect_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A running iwd is used with its default state directory."""
    monkeypatch.setattr(iwd, "PROC", _fake_proc(tmp_path / "proc", "bash", "iwd"))
    backend = IwdBackend.connect(MESSAGES)
    assert backend == IwdBackend(directory=Path("/var/lib/iwd"), running=True)


def test_connect_installed_but_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An installed iwd can be configured before it is started."""
    program = tmp_path / "usr" / "libexec" / "iwd"
    program.parent.mkdir(parents=True)
    program.touch()
    monkeypatch.setattr(iwd, "PROC", _fake_proc(tmp_path / "proc", "bash"))
    monkeypatch.setattr(iwd, "IWD_DIRECTORY", tmp_path / "missing")
    monkeypatch.setattr(iwd, "IWD_PROGRAMS", (program,))
    monkeypatch.setattr("src.backends.iwd.shutil.which", lambda name, path=None: None)
    backend = IwdBackend.connect(MESSAGES)
    assert backend is not None
    assert not backend.running


def test_connect_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without any trace of iwd, the backend refuses and says why."""
    monkeypatch.setattr(iwd, "PROC", _fake_proc(tmp_path / "proc", "bash"))
    monkeypatch.setattr(iwd, "IWD_DIRECTORY", tmp_path / "missing")
    monkeypatch.setattr(iwd, "IWD_PROGRAMS", ())
    monkeypatch.setattr("src.backends.iwd.shutil.which", lambda name, path=None: None)
    assert IwdBackend.connect(MESSAGES) is None
    assert MESSAGES.iwd_not_installed in capsys.readouterr().out


def test_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--backend iwd`` writes into the iwd state directory."""
    directory = tmp_path / "var" / "lib" / "iwd"
    monkeypatch.setattr(iwd, "PROC", _fake_proc(tmp_path / "proc", "iwd"))
    monkeypatch.setattr(iwd, "IWD_DIRECTORY", directory)
    assert main([*CREDENTIALS, "--backend", "iwd"]) == 0
    conf = directory / "eduroam.8021x"
    assert _mode(conf) == 0o600
    text = conf.read_text()
    assert "EAP-PEAP-Phase2-Identity=alice@uni-goettingen.de\n" in text
    assert "EAP-PEAP-Phase2-Password=pa$$ 'word\"\n" in text
    assert MESSAGES.iwd_start not in capsys.readouterr().out


def test_end_to_end_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--backend iwd`` without iwd fails with the backend error code."""
    monkeypatch.setattr(iwd, "PROC", _fake_proc(tmp_path / "proc"))
    monkeypatch.setattr(iwd, "IWD_DIRECTORY", tmp_path / "missing")
    monkeypatch.setattr(iwd, "IWD_PROGRAMS", ())
    monkeypatch.setattr("src.backends.iwd.shutil.which", lambda name, path=None: None)
    assert main([*CREDENTIALS, "--backend", "iwd"]) == 4
