"""System configuration through the NetworkManager D-Bus API."""

import getpass
import logging
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple, Self

from ..certificates import CaStore
from ..config import PASSWORD_METHODS, EapMethod, Profile
from ..credentials import Credentials
from ..errors import ExitCode, InstallerError
from ..messages import Messages
from ..paths import InstallerPaths

try:
    import dbus
except ImportError:
    dbus = None

logger = logging.getLogger(__name__)

#: Settings of a connection as sent over D-Bus, by setting name.
type ConnectionSettings = Mapping[str, Any]
#: Decides whether a saved connection should be deleted.
type ConnectionFilter = Callable[[ConnectionSettings], bool]

_BUS_NAME = "org.freedesktop.NetworkManager"
_OBJECT_PATH = "/org/freedesktop/NetworkManager"
_SETTINGS_PATH = f"{_OBJECT_PATH}/Settings"
_SETTINGS_INTERFACE = f"{_BUS_NAME}.Settings"
_CONNECTION_INTERFACE = f"{_SETTINGS_INTERFACE}.Connection"

_WIFI_TYPE = "802-11-wireless"
_WIRED_TYPE = "802-3-ethernet"
_WIRED_CONNECTION_ID = "wired-eduroam"


class NmVersion(NamedTuple):
    """A NetworkManager version, ordered numerically."""

    major: int
    minor: int
    micro: int = 0

    @classmethod
    def parse(cls, text: str) -> Self | None:
        """Parse a version such as ``1.54.3``; ``None`` if it is not one."""
        found = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
        return cls(*(int(part or 0) for part in found.groups())) if found else None

    @property
    def is_supported(self) -> bool:
        """Return whether the Settings API used here is available."""
        return self >= (0, 9)

    @property
    def needs_ca_directory(self) -> bool:
        """Return whether a NetworkManager bug requires hashed CA directories."""
        return (1, 54, 3) <= self < (1, 58, 1)

    @property
    def has_domain_match(self) -> bool:
        """Return whether the ``domain-match`` setting exists."""
        return self >= (1, 24)


@dataclass(slots=True)
class NetworkManagerBackend:
    """A connection to NetworkManager.

    Attributes:
        bus: System D-Bus connection.
        settings: NetworkManager Settings interface.
        version: Version of the running NetworkManager.
        messages: Texts to show.
    """

    bus: Any
    settings: Any
    version: NmVersion
    messages: Messages

    @classmethod
    def connect(cls, messages: Messages) -> Self | None:
        """Connect to NetworkManager, explaining on stdout why that failed.

        Returns:
            The backend, or ``None`` if NetworkManager cannot be used.
        """
        if dbus is None:
            print(messages.dbus_missing)
            return None
        try:
            bus = dbus.SystemBus()
        except AttributeError:
            # An empty dbus package exists: it gets shipped by pyqt5.
            print(messages.dbus_broken)
            return None
        except dbus.exceptions.DBusException:
            print(messages.dbus_unreachable)
            return None

        version = _read_version(bus)
        if version is None or not version.is_supported:
            print(messages.nm_not_supported)
            return None
        logger.debug("NM version: %s", ".".join(map(str, version)))
        settings = dbus.Interface(
            bus.get_object(_BUS_NAME, _SETTINGS_PATH), _SETTINGS_INTERFACE
        )
        logger.debug("NM connection worked")
        return cls(bus, settings, version, messages)

    def install(
        self,
        profile: Profile,
        credentials: Credentials,
        ca: CaStore,
        paths: InstallerPaths,
        *,
        wired: bool = False,
    ) -> None:
        """Replace the connections of ``profile`` with freshly configured ones.

        Args:
            profile: Connections to configure.
            credentials: What to authenticate with.
            ca: Installed CA certificates.
            paths: Installation directories.
            wired: Also configure 802.1X on wired interfaces.
        """
        security = self._security_settings(profile, credentials, ca, paths)
        for ssid in profile.ssids:
            self._delete_wifi_connection(ssid)
            self._add_wifi_connection(ssid, security)
        for ssid in profile.del_ssids:
            self._delete_wifi_connection(ssid)
        if wired:
            self._delete_connections(_is_wired_eduroam)
            self._add_wired_connection(security)

    def _delete_wifi_connection(self, ssid: str) -> None:
        """Delete saved connections to the given SSID."""
        self._delete_connections(lambda settings: _is_wifi_for(settings, ssid))

    def _delete_connections(self, should_delete: ConnectionFilter) -> None:
        """Delete saved connections selected by ``should_delete``.

        Raises:
            InstallerError: If the connections cannot be listed.
        """
        try:
            paths = self.settings.ListConnections()
        except dbus.exceptions.DBusException as error:
            raise InstallerError(
                self.messages.dbus_error, ExitCode.DBUS_ERROR
            ) from error
        for path in paths:
            connection = dbus.Interface(
                self.bus.get_object(_BUS_NAME, path), _CONNECTION_INTERFACE
            )
            try:
                if should_delete(connection.GetSettings()):
                    logger.debug("deleting connection: %s", path)
                    connection.Delete()
            except dbus.exceptions.DBusException:
                logger.debug("skipping connection: %s", path)

    def _security_settings(
        self,
        profile: Profile,
        credentials: Credentials,
        ca: CaStore,
        paths: InstallerPaths,
    ) -> dict[str, Any]:
        """Return the ``802-1x`` settings shared by all connections."""
        settings: dict[str, Any] = {
            "eap": [profile.eap_outer.lower()],
            "identity": credentials.username,
        }
        if ca.is_directory:
            settings["ca-path"] = str(ca.path)
        else:
            settings["ca-cert"] = _file_reference(ca.path)

        if self.version.has_domain_match:
            settings["domain-match"] = profile.servers_cn
        else:
            settings["altsubject-matches"] = dbus.Array(profile.servers)

        if profile.eap_outer in PASSWORD_METHODS:
            settings["password"] = credentials.password
            settings["password-flags"] = 1
            settings["phase2-auth"] = profile.eap_inner.lower()
            if profile.anonymous_identity:
                settings["anonymous-identity"] = profile.anonymous_identity
        elif profile.eap_outer is EapMethod.TLS:
            settings["client-cert"] = _file_reference(paths.user_p12)
            settings["private-key"] = _file_reference(paths.user_p12)
            settings["private-key-password"] = credentials.password
            settings["private-key-password-flags"] = 1
        return settings

    def _add_wifi_connection(self, ssid: str, security: dict[str, Any]) -> None:
        """Add a connection to a wireless network."""
        self.settings.AddConnection(
            dbus.Dictionary(
                {
                    "connection": dbus.Dictionary(
                        {
                            "type": _WIFI_TYPE,
                            "uuid": str(uuid.uuid4()),
                            "permissions": _user_permissions(),
                            "id": ssid,
                        }
                    ),
                    _WIFI_TYPE: dbus.Dictionary(
                        {
                            "ssid": dbus.ByteArray(ssid.encode("utf-8")),
                            "security": f"{_WIFI_TYPE}-security",
                        }
                    ),
                    f"{_WIFI_TYPE}-security": dbus.Dictionary(
                        {
                            "key-mgmt": "wpa-eap",
                            "proto": ["rsn"],
                            "pairwise": ["ccmp"],
                            "group": ["ccmp"],
                        }
                    ),
                    "802-1x": dbus.Dictionary(security),
                    **_automatic_ip_settings(),
                }
            )
        )

    def _add_wired_connection(self, security: dict[str, Any]) -> None:
        """Add the 802.1X connection for wired interfaces."""
        logger.debug("adding %s", _WIRED_CONNECTION_ID)
        self.settings.AddConnection(
            dbus.Dictionary(
                {
                    "connection": dbus.Dictionary(
                        {
                            "type": _WIRED_TYPE,
                            "autoconnect-priority": 100,
                            "autoconnect-retries": 3,
                            "uuid": str(uuid.uuid4()),
                            "permissions": _user_permissions(),
                            "id": _WIRED_CONNECTION_ID,
                        }
                    ),
                    "802-1x": dbus.Dictionary(
                        {**security, "optional": True, "auth-timeout": 10}
                    ),
                    **_automatic_ip_settings(),
                }
            )
        )


def _read_version(bus: Any) -> NmVersion | None:
    """Return the version of the running NetworkManager, if it can be determined."""
    try:
        properties = dbus.Interface(
            bus.get_object(_BUS_NAME, _OBJECT_PATH), "org.freedesktop.DBus.Properties"
        )
        version: str = properties.Get(_BUS_NAME, "Version")
    except dbus.exceptions.DBusException:
        return None
    logger.debug("Actual NM version: %s", version)
    return NmVersion.parse(version)


def _file_reference(path: object) -> Any:
    """Return a path in the NUL-terminated URI form used for certificate settings."""
    return dbus.ByteArray(f"file://{path}\0".encode())


def _automatic_ip_settings() -> dict[str, Any]:
    """Return IP settings which configure both protocols automatically."""
    return {
        "ipv4": dbus.Dictionary({"method": "auto"}),
        "ipv6": dbus.Dictionary({"method": "auto"}),
    }


def _user_permissions() -> list[str]:
    """Return connection permissions limiting it to the current user."""
    return [f"user:{getpass.getuser()}"]


def _is_wifi_for(settings: ConnectionSettings, ssid: str) -> bool:
    """Return whether a saved connection is for the given wireless network."""
    if settings["connection"]["type"] != _WIFI_TYPE:
        return False
    return bytes(settings[_WIFI_TYPE]["ssid"]) == ssid.encode("utf-8")


def _is_wired_eduroam(settings: ConnectionSettings) -> bool:
    """Return whether a saved connection is a previously installed wired one."""
    connection = settings["connection"]
    is_wired: bool = connection["type"] == _WIRED_TYPE
    return is_wired and connection["id"] == _WIRED_CONNECTION_ID
