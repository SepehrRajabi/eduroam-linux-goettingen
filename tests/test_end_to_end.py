"""Whole installer runs in silent mode, through the command line entry point."""

import stat
from pathlib import Path

import pytest

from src.cli import main

from .fake_dbus import FakeDbus

CREDENTIALS = ["--silent", "-u", "alice@uni-goettingen.de", "-p", "pa$$ 'word\""]


def _mode(path: Path) -> int:
    """Return the permission bits of ``path``."""
    return stat.S_IMODE(path.stat().st_mode)


def test_wpa_supplicant_file(isolated_home: Path) -> None:
    """``--wpa_conf`` writes a private wpa_supplicant file and the CA."""
    assert main([*CREDENTIALS, "--wpa_conf"]) == 0
    root = isolated_home / ".config" / "cat_installer"
    conf = root / "cat_installer.conf"
    assert _mode(conf) == 0o600
    assert _mode(root) == 0o700
    text = conf.read_text()
    assert text.startswith('network={\n\tssid="eduroam"\n')
    assert f'\tca_cert="{root / "ca.pem"}"\n' in text
    assert '\tidentity="alice@uni-goettingen.de"\n' in text
    assert '\tanonymous_identity="wifi@uni-goettingen.de"\n' in text
    assert "BEGIN CERTIFICATE" in (root / "ca.pem").read_text()


def test_iwd_file() -> None:
    """``--iwd_conf`` writes a private iwd file to the working directory."""
    assert main([*CREDENTIALS, "--iwd_conf"]) == 0
    conf = Path("eduroam.8021x")
    assert _mode(conf) == 0o600
    text = conf.read_text()
    assert "EAP-Method=PEAP\n" in text
    assert "EAP-PEAP-Phase2-Identity=alice@uni-goettingen.de\n" in text
    assert "[@pem@eduroam_ca_cert]\n-----BEGIN CERTIFICATE-----" in text


def test_backend_option_conflicts_with_aliases() -> None:
    """``--backend`` and ``--wpa_conf`` together are rejected."""
    with pytest.raises(SystemExit):
        main([*CREDENTIALS, "--wpa_conf", "--backend", "iwd"])


def test_network_manager(
    fake_dbus: FakeDbus, monkeypatch: pytest.MonkeyPatch, isolated_home: Path
) -> None:
    """NetworkManager gets one eduroam connection, replacing the old one."""
    monkeypatch.setattr(
        "src.backends.running_processes", lambda: frozenset({"NetworkManager"})
    )
    fake_dbus.add_wifi("eduroam", "old eduroam")
    fake_dbus.add_wifi("other", "other")
    assert main(CREDENTIALS) == 0
    connections = list(fake_dbus.connections.values())
    assert [c["connection"]["id"] for c in connections] == ["other", "eduroam"]
    security = connections[1]["802-1x"]
    assert security["identity"] == "alice@uni-goettingen.de"
    assert security["domain-match"] == "eduroam.gwdg.de"
    assert security["anonymous-identity"] == "wifi@uni-goettingen.de"
    ca = isolated_home / ".config" / "cat_installer" / "ca.pem"
    assert bytes(security["ca-cert"]) == f"file://{ca}\0".encode()
