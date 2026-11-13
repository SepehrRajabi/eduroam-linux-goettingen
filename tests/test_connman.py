"""The ConnMan backend: provisioning file, certificates and key file escaping."""

import dataclasses
import datetime
import stat
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    pkcs12,
)
from cryptography.x509.oid import NameOID

from src.backends import BackendChoice, InstallRequest, connman, select_backend
from src.backends.connman import (
    ConnManBackend,
    ConnManFiles,
    escape_value,
    render_config,
)
from src.certificates import save_ca
from src.cli import main
from src.config import GOETTINGEN, EapMethod, Profile
from src.credentials import Credentials
from src.errors import ExitCode, InstallerError
from src.messages import MESSAGES
from src.paths import InstallerPaths
from src.ui.silent import SilentUI

USERNAME = "alice@uni-goettingen.de"
PASSWORD = "pa$$ 'word\""
FILES = ConnManFiles(Path("/var/lib/connman"))

TTLS = dataclasses.replace(GOETTINGEN, eap_outer=EapMethod.TTLS, eap_inner="PAP")
TLS = dataclasses.replace(
    GOETTINGEN, eap_outer=EapMethod.TLS, eap_inner="", anonymous_identity=""
)


@pytest.fixture
def connman_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the ConnMan storage directory into a temporary one."""
    directory = tmp_path / "var" / "lib" / "connman"
    directory.mkdir(parents=True)
    monkeypatch.setattr(connman, "CONNMAN_DIRECTORY", directory)
    return directory


def _mode(path: Path) -> int:
    """Return the permission bits of ``path``."""
    return stat.S_IMODE(path.stat().st_mode)


def _unescape(value: str) -> str:
    """Undo key file escaping as ``g_key_file_get_string`` does.

    Raises:
        ValueError: On an escape sequence GLib rejects.
    """
    escapes = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}
    result: list[str] = []
    chars = iter(value)
    for char in chars:
        if char != "\\":
            result.append(char)
            continue
        following = next(chars, "")
        if following not in escapes:
            raise ValueError(f"invalid escape sequence \\{following}")
        result.append(escapes[following])
    return "".join(result)


def _parse_key_file(text: str) -> dict[str, dict[str, str]]:
    """Parse a key file the way GLib does, for the subset the backend writes.

    Lines end at newlines, with one trailing carriage return dropped. Leading
    whitespace of lines and values is skipped, and the key ends before the first
    ``=``.
    """
    groups: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw_line in text.split("\n"):
        line = raw_line.removesuffix("\r").lstrip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            assert line.endswith("]"), line
            current = groups.setdefault(line[1:-1], {})
            continue
        assert current is not None, "key outside a group"
        key, equals, value = line.partition("=")
        assert equals, f"not a key-value pair: {line!r}"
        current[key.rstrip()] = _unescape(value.lstrip())
    return groups


def _request(
    profile: Profile, credentials: Credentials, paths: InstallerPaths
) -> InstallRequest:
    """Return an install request with the CA saved under ``paths``."""
    paths.create()
    return InstallRequest(
        profile=profile,
        credentials=credentials,
        ca=save_ca(profile.ca_certificate, paths, MESSAGES),
        paths=paths,
        ui=SilentUI("test", MESSAGES),
        messages=MESSAGES,
    )


def _make_p12(password: str) -> tuple[bytes, x509.Certificate]:
    """Return a password protected PKCS#12 file and the certificate inside it."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, USERNAME)])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    p12 = pkcs12.serialize_key_and_certificates(
        b"user", key, certificate, None, BestAvailableEncryption(password.encode())
    )
    return p12, certificate


@pytest.mark.parametrize(
    ("value", "escaped"),
    [
        ("plain", "plain"),
        ("inner space and\ttab", "inner space and\ttab"),
        ("  leading", r"\s\sleading"),
        ("\t leading tab", r"\t\sleading tab"),
        ("trailing  ", "trailing  "),
        ("back\\slash", r"back\\slash"),
        ("\\ after backslash", r"\\ after backslash"),
        ("line\nbreak", r"line\nbreak"),
        ("carriage\rreturn", r"carriage\rreturn"),
        ("ümlaut ; , # = [x]", "ümlaut ; , # = [x]"),
        ("", ""),
    ],
)
def test_escape_value(value: str, escaped: str) -> None:
    """Values are escaped like ``g_key_file_set_string`` does, and round-trip."""
    assert escape_value(value) == escaped
    assert _unescape(escaped) == value


def test_escape_value_rejects_nul() -> None:
    """Key files cannot hold NUL characters."""
    with pytest.raises(ValueError, match="NUL"):
        escape_value("a\0b")


def test_goettingen_config() -> None:
    """The Göttingen profile becomes one PEAP/MSCHAPV2 service."""
    text = render_config(GOETTINGEN, Credentials(USERNAME, PASSWORD), FILES)
    assert text == (
        "[global]\n"
        "Name = eduroam\n"
        "Description = University of Göttingen\n"
        "\n"
        "[service_eduroam]\n"
        "Type = wifi\n"
        "SSID = 656475726f616d\n"
        "EAP = peap\n"
        "CACertFile = /var/lib/connman/eduroamgwdg/ca.pem\n"
        "DomainMatch = eduroam.gwdg.de\n"
        "Identity = alice@uni-goettingen.de\n"
        "AnonymousIdentity = wifi@uni-goettingen.de\n"
        "Phase2 = MSCHAPV2\n"
        "Passphrase = pa$$ 'word\"\n"
    )


def test_ttls_config() -> None:
    """TTLS uses the inner method of the profile.

    Without an anonymous identity, the username is sent outside the tunnel.
    """
    profile = dataclasses.replace(TTLS, anonymous_identity="")
    text = render_config(profile, Credentials(USERNAME, PASSWORD), FILES)
    service = _parse_key_file(text)["service_eduroam"]
    assert service["EAP"] == "ttls"
    assert service["Phase2"] == "PAP"
    assert service["AnonymousIdentity"] == USERNAME
    assert service["Passphrase"] == PASSWORD


def test_tls_config() -> None:
    """TLS names the certificate files and the key passphrase, but no password."""
    text = render_config(TLS, Credentials(USERNAME, "import"), FILES)
    service = _parse_key_file(text)["service_eduroam"]
    assert service == {
        "Type": "wifi",
        "SSID": "656475726f616d",
        "EAP": "tls",
        "CACertFile": "/var/lib/connman/eduroamgwdg/ca.pem",
        "DomainMatch": "eduroam.gwdg.de",
        "Identity": USERNAME,
        "ClientCertFile": "/var/lib/connman/eduroamgwdg/user.pem",
        "PrivateKeyFile": "/var/lib/connman/eduroamgwdg/user.p12",
        "PrivateKeyPassphrase": "import",
    }


def test_several_and_unusual_ssids() -> None:
    """Every SSID gets its own section, with the SSID in hexadecimal."""
    ssids = ("eduroam", "edu roam", "edu_roam", " [x]\n", "")
    profile = dataclasses.replace(GOETTINGEN, ssids=ssids)
    groups = _parse_key_file(
        render_config(profile, Credentials(USERNAME, PASSWORD), FILES)
    )
    assert list(groups) == [
        "global",
        "service_eduroam",
        "service_edu_roam",
        "service_edu_roam_2",
        "service_x",
        "service_ssid",
    ]
    hex_ssids = [groups[name]["SSID"] for name in list(groups)[1:]]
    assert [bytes.fromhex(ssid).decode() for ssid in hex_ssids] == list(ssids)


@pytest.mark.parametrize(
    "password",
    [
        "x\n[service_evil]\nType = wifi\nName = evil",
        "x\r\n[global]\r\nName = evil",
        "x\\n[service_evil]",
        "x\\\n[service_evil]",
        "  spaced  ",
        "\\",
    ],
)
def test_injection_is_escaped(password: str) -> None:
    """A hostile password stays one value and cannot add sections or keys."""
    text = render_config(GOETTINGEN, Credentials(USERNAME, password), FILES)
    groups = _parse_key_file(text)
    assert list(groups) == ["global", "service_eduroam"]
    assert groups["service_eduroam"]["Passphrase"] == password
    assert groups["global"] == {
        "Name": "eduroam",
        "Description": "University of Göttingen",
    }
    assert sum(line.startswith("[") for line in text.splitlines()) == 2


def test_hostile_username_is_escaped() -> None:
    """The username is escaped too, in both identities."""
    username = "a\n[service_evil]\nb"
    profile = dataclasses.replace(GOETTINGEN, anonymous_identity="")
    text = render_config(profile, Credentials(username, PASSWORD), FILES)
    service = _parse_key_file(text)["service_eduroam"]
    assert service["Identity"] == username
    assert service["AnonymousIdentity"] == username
    assert "service_evil" not in _parse_key_file(text)


def test_install_peap(connman_dir: Path, isolated_home: Path) -> None:
    """The CA is copied into the ConnMan directory; only the file is private."""
    paths = InstallerPaths(isolated_home / ".config" / "cat_installer")
    backend = ConnManBackend.connect(MESSAGES)
    assert backend is not None
    assert backend.directory == connman_dir
    instructions = backend.install(
        _request(GOETTINGEN, Credentials(USERNAME, PASSWORD), paths)
    )
    assert instructions is None

    config = connman_dir / "eduroamgwdg.config"
    ca = connman_dir / "eduroamgwdg" / "ca.pem"
    assert _mode(config) == 0o600
    assert _mode(ca) == 0o644
    assert ca.read_text() == paths.ca_file.read_text()
    assert sorted(path.name for path in ca.parent.iterdir()) == ["ca.pem"]
    # No temporary file of the atomic writes is left for ConnMan to see.
    assert sorted(path.name for path in connman_dir.iterdir()) == [
        "eduroamgwdg",
        "eduroamgwdg.config",
    ]
    service = _parse_key_file(config.read_text())["service_eduroam"]
    assert service["CACertFile"] == str(ca)
    assert str(isolated_home) not in config.read_text()


def test_install_tls(connman_dir: Path, isolated_home: Path) -> None:
    """For TLS the personal certificate is copied too, privately."""
    paths = InstallerPaths(isolated_home / ".config" / "cat_installer")
    p12, certificate = _make_p12("import")
    request = _request(TLS, Credentials(USERNAME, "import"), paths)
    paths.user_p12.write_bytes(p12)
    assert ConnManBackend(connman_dir).install(request) is None

    certificates = connman_dir / "eduroamgwdg"
    assert (certificates / "user.p12").read_bytes() == p12
    assert _mode(certificates / "user.p12") == 0o600
    client_cert = certificates / "user.pem"
    assert _mode(client_cert) == 0o600
    assert client_cert.read_bytes() == certificate.public_bytes(Encoding.PEM)
    assert b"PRIVATE KEY" not in client_cert.read_bytes()
    service = _parse_key_file((connman_dir / "eduroamgwdg.config").read_text())[
        "service_eduroam"
    ]
    assert service["ClientCertFile"] == str(client_cert)
    assert service["PrivateKeyFile"] == str(certificates / "user.p12")


def test_install_tls_wrong_password(connman_dir: Path, isolated_home: Path) -> None:
    """A personal certificate which does not unlock stops before writing."""
    paths = InstallerPaths(isolated_home / ".config" / "cat_installer")
    request = _request(TLS, Credentials(USERNAME, "wrong"), paths)
    paths.user_p12.write_bytes(_make_p12("import")[0])
    with pytest.raises(InstallerError, match=MESSAGES.incorrect_password):
        ConnManBackend(connman_dir).install(request)
    assert not (connman_dir / "eduroamgwdg.config").exists()


def test_unsupported_method(connman_dir: Path, isolated_home: Path) -> None:
    """EAP-PWD is refused with a clear message, and nothing is written."""
    paths = InstallerPaths(isolated_home / ".config" / "cat_installer")
    profile = dataclasses.replace(GOETTINGEN, eap_outer=EapMethod.PWD)
    request = _request(profile, Credentials(USERNAME, PASSWORD), paths)
    with pytest.raises(InstallerError) as raised:
        ConnManBackend(connman_dir).install(request)
    assert raised.value.message == "ConnMan does not support EAP-PWD"
    assert list(connman_dir.iterdir()) == []


def test_nul_in_password(connman_dir: Path, isolated_home: Path) -> None:
    """A NUL character stops the installer instead of truncating the password."""
    paths = InstallerPaths(isolated_home / ".config" / "cat_installer")
    request = _request(GOETTINGEN, Credentials(USERNAME, "a\0b"), paths)
    with pytest.raises(InstallerError, match=MESSAGES.connman_invalid_value):
        ConnManBackend(connman_dir).install(request)
    assert list(connman_dir.iterdir()) == []


def test_del_ssids_are_reported(connman_dir: Path, isolated_home: Path) -> None:
    """Old networks cannot be forgotten through a file, so the user is told."""
    paths = InstallerPaths(isolated_home / ".config" / "cat_installer")
    profile = dataclasses.replace(GOETTINGEN, del_ssids=("old", "older"))
    request = _request(profile, Credentials(USERNAME, PASSWORD), paths)
    instructions = ConnManBackend(connman_dir).install(request)
    assert instructions == MESSAGES.connman_del_ssids.format("old, older")
    groups = _parse_key_file((connman_dir / "eduroamgwdg.config").read_text())
    assert list(groups) == ["global", "service_eduroam"]


def test_reinstall_overwrites(connman_dir: Path, isolated_home: Path) -> None:
    """A second run replaces the file, which ConnMan then reloads."""
    paths = InstallerPaths(isolated_home / ".config" / "cat_installer")
    backend = ConnManBackend(connman_dir)
    backend.install(_request(GOETTINGEN, Credentials(USERNAME, "first"), paths))
    backend.install(_request(GOETTINGEN, Credentials(USERNAME, "second"), paths))
    config = connman_dir / "eduroamgwdg.config"
    assert _parse_key_file(config.read_text())["service_eduroam"]["Passphrase"] == (
        "second"
    )
    assert _mode(config) == 0o600


def test_connect_without_connman(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without ConnMan's directory the backend is unusable, and says why."""
    missing = tmp_path / "no-connman"
    monkeypatch.setattr(connman, "CONNMAN_DIRECTORY", missing)
    assert ConnManBackend.connect(MESSAGES) is None
    assert capsys.readouterr().out == MESSAGES.connman_missing.format(missing) + "\n"
    with pytest.raises(InstallerError) as raised:
        select_backend(BackendChoice.CONNMAN, SilentUI("test", MESSAGES), MESSAGES)
    assert raised.value.exit_code is ExitCode.BACKEND_UNAVAILABLE


def test_connman_detected(connman_dir: Path) -> None:
    """A running connmand is configured even though wpa_supplicant runs too."""
    processes = frozenset({"connmand", "wpa_supplicant"})
    backend = select_backend(None, SilentUI("test", MESSAGES), MESSAGES, processes)
    assert isinstance(backend, ConnManBackend)


def test_end_to_end(connman_dir: Path, isolated_home: Path) -> None:
    """``--backend connman`` writes the provisioning file and the CA."""
    argv = ["--silent", "-u", USERNAME, "-p", PASSWORD, "--backend", "connman"]
    assert main(argv) == 0
    config = connman_dir / "eduroamgwdg.config"
    assert _mode(config) == 0o600
    service = _parse_key_file(config.read_text())["service_eduroam"]
    assert service["Identity"] == USERNAME
    assert service["Passphrase"] == PASSWORD
    assert service["AnonymousIdentity"] == "wifi@uni-goettingen.de"
    ca = connman_dir / "eduroamgwdg" / "ca.pem"
    assert service["CACertFile"] == str(ca)
    assert "BEGIN CERTIFICATE" in ca.read_text()
