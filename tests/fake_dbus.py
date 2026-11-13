"""An in-memory stand-in for the parts of dbus-python the installer uses."""

import copy
from dataclasses import dataclass, field
from typing import Any


class DBusException(Exception):
    """Stand-in for ``dbus.exceptions.DBusException``."""


class _Exceptions:
    """Stand-in for the ``dbus.exceptions`` module."""

    DBusException = DBusException


class Array(list[Any]):
    """Stand-in for ``dbus.Array``."""


class Dictionary(dict[str, Any]):
    """Stand-in for ``dbus.Dictionary``."""


class ByteArray(bytes):
    """Stand-in for ``dbus.ByteArray``."""


@dataclass
class FakeConnection:
    """A saved NetworkManager connection."""

    bus: "FakeDbus"
    path: str

    def GetSettings(self) -> dict[str, Any]:
        """Return the settings of this connection."""
        return copy.deepcopy(self.bus.connections[self.path])

    def Delete(self) -> None:
        """Delete this connection."""
        del self.bus.connections[self.path]


@dataclass
class FakeSettings:
    """The NetworkManager Settings interface."""

    bus: "FakeDbus"

    def ListConnections(self) -> list[str]:
        """Return the object paths of the saved connections."""
        return list(self.bus.connections)

    def AddConnection(self, settings: dict[str, Any]) -> str:
        """Save a new connection and return its object path."""
        self.bus.counter += 1
        path = f"/org/freedesktop/NetworkManager/Settings/{self.bus.counter}"
        self.bus.connections[path] = copy.deepcopy(dict(settings))
        return path


@dataclass
class FakeProperties:
    """The D-Bus Properties interface of the NetworkManager object."""

    bus: "FakeDbus"

    def Get(self, interface: str, name: str) -> str:
        """Return the NetworkManager version."""
        assert name == "Version"
        return self.bus.version


@dataclass
class FakeDbus:
    """Stand-in for the ``dbus`` module, holding NetworkManager's state.

    Attributes:
        version: Version NetworkManager reports.
        connections: Saved connections by object path.
        counter: Last object path number handed out.
    """

    version: str = "1.46.0"
    connections: dict[str, dict[str, Any]] = field(default_factory=dict)
    counter: int = 0

    exceptions = _Exceptions
    Array = Array
    Dictionary = Dictionary
    ByteArray = ByteArray

    def SystemBus(self) -> "FakeDbus":
        """Return the bus, which is this object."""
        return self

    def get_object(self, bus_name: str, path: str) -> str:
        """Return the object path, which is all the fake needs."""
        return path

    def Interface(self, path: str, interface: str) -> Any:
        """Return the fake implementing ``interface`` at ``path``."""
        if interface.endswith(".Settings"):
            return FakeSettings(self)
        if interface.endswith(".Connection"):
            return FakeConnection(self, path)
        if interface == "org.freedesktop.DBus.Properties":
            return FakeProperties(self)
        raise DBusException(interface)

    def add_wifi(self, ssid: str, connection_id: str) -> None:
        """Save an existing wireless connection, as a user might have."""
        FakeSettings(self).AddConnection(
            {
                "connection": {"type": "802-11-wireless", "id": connection_id},
                "802-11-wireless": {"ssid": ssid.encode()},
            }
        )
