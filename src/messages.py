"""User-facing strings, kept together so they can be translated."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Messages:
    """Every string shown to the user.

    The defaults are English. A translation is a new instance with the relevant
    fields replaced. ``yes`` and ``no`` are also what the terminal prompt accepts,
    matched on their first letter.
    """

    quit: str = "Really quit?"
    credentials_prompt: str = "Please enter your credentials:"
    username_prompt: str = "enter your userid"
    enter_password: str = "enter password"
    enter_import_password: str = "enter your import password"
    incorrect_password: str = "incorrect password"
    repeat_password: str = "repeat your password"
    passwords_differ: str = "passwords do not match"
    empty_field: str = "one of the fields was empty"
    installation_finished: str = "Installation successful"
    cat_dir_exists: str = "Directory {} exists; some of its files may be overwritten"
    cont: str = "Continue?"
    nm_not_supported: str = "This NetworkManager version is not supported"
    cert_error: str = "Certificate file not found, looks like a CAT error"
    dbus_error: str = "DBus connection problem, a sudo might help"
    dbus_missing: str = "WARNING: Cannot import the dbus module - please install it!"
    dbus_broken: str = "DBus not properly installed"
    dbus_unreachable: str = "Can't connect to DBus"
    yes: str = "Y"
    no: str = "N"
    ok: str = "OK"
    p12_filter: str = "personal certificate file (p12 or pfx)"
    all_filter: str = "All files"
    p12_title: str = "personal certificate file (p12 or pfx)"
    certificate_filter: str = "Certificate file"
    file_not_found: str = "file not found"
    save_wpa_conf: str = (
        "DBus module not found - please install dbus-python! NetworkManager "
        "configuration failed, but we can generate a wpa_supplicant configuration "
        "file if you wish. Be warned that your connection password will be saved "
        "in this file as clear text."
    )
    wrong_username_format: str = (
        "Error: Your username must be of the form 'xxx@institutionID' "
        "e.g. 'john@example.net'!"
    )
    wrong_realm: str = (
        "Error: your username must be in the form of 'xxx@{}'. "
        "Please enter the username in the correct format."
    )
    wrong_realm_suffix: str = (
        "Error: your username must be in the form of 'xxx@institutionID' and end "
        "with '{}'. Please enter the username in the correct format."
    )
    user_cert_missing: str = "personal certificate file not found"
    wired: str = (
        "Configure eduroam also on the wired Ethernet?\n\n Please be aware that "
        "doing so will introduce a connection delay lasting up to 30 sec. when you "
        "connect to an open wired network."
    )
    backend_unavailable: str = "{} cannot be configured on this system"
    privileges_unavailable: str = (
        "Administrator rights are needed to write {}, but neither pkexec nor sudo "
        "is installed"
    )
    privileges_refused: str = "Could not write {} with administrator rights"

    # iwd and wpa_supplicant system backends
    iwd_not_installed: str = "iwd does not seem to be installed"
    iwd_start: str = (
        "iwd is not running. Start it to connect, for example with:\n\n"
        "    sudo systemctl enable --now iwd\n\n"
        "To get an address, iwd needs EnableNetworkConfiguration=true in "
        "/etc/iwd/main.conf, or another network manager such as systemd-networkd."
    )
    wpa_supplicant_no_config: str = (
        "wpa_supplicant runs without a configuration file the installer can find; "
        "it is probably controlled over D-Bus by another program"
    )
    wpa_supplicant_no_interface: str = "No wireless network interface was found"
    wpa_supplicant_restart: str = (
        "Restart wpa_supplicant to load the new configuration from {}"
    )
    wpa_supplicant_start: str = (
        "wpa_supplicant is not running on {0}. Start it to connect, for example "
        "with:\n\n"
        "    sudo systemctl enable --now wpa_supplicant@{0}\n\n"
        "To get an address, {0} also needs a DHCP client, such as systemd-networkd "
        "or dhcpcd."
    )

    # ConnMan backend
    connman_missing: str = "ConnMan does not seem to be installed: {} does not exist"
    connman_unsupported_method: str = "ConnMan does not support EAP-{}"
    connman_invalid_value: str = "ConnMan cannot store values containing NUL characters"
    connman_del_ssids: str = (
        "Networks which are no longer used may still be saved in ConnMan: {}. "
        "You can remove them in your network settings."
    )


MESSAGES = Messages()
