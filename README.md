# eduroam-gwdg

Configures [eduroam](https://eduroam.org/) on Linux for members of the University
of Göttingen. It detects the network service in use and configures NetworkManager,
ConnMan, iwd or wpa_supplicant, or generates a configuration file for
`wpa_supplicant` or `iwd`.

This is a modernized, modular version of the single-file installer produced by
GÉANT's [CAT](https://cat.eduroam.org/). See [Legacy script](#legacy-script) and
[Acknowledgments](#acknowledgments). For the terms, see [License](#license).

## Requirements

- Linux
- Python 3.12 or newer, which [uv](https://docs.astral.sh/uv/) can provide for you
- uv or pip
- For NetworkManager only: `dbus-python`, see below
- For ConnMan, iwd and wpa_supplicant: `pkexec` or `sudo`, see
  [Administrator rights](#administrator-rights)

## Installation

### With uv

Everything except NetworkManager needs only:

```sh
uv sync
```

Configuring NetworkManager also needs `dbus-python`. It is distributed as source
only, so first install a compiler and its build dependencies:

| Distribution  | Command                                                         |
| ------------- | --------------------------------------------------------------- |
| Debian/Ubuntu | `sudo apt install build-essential pkg-config libdbus-1-dev libglib2.0-dev` |
| Fedora        | `sudo dnf install gcc pkgconf-pkg-config dbus-devel glib2-devel` |
| Arch          | `sudo pacman -S base-devel dbus glib2`                          |

Then:

```sh
uv sync --extra nm
```

If `dbus-python` is missing, the installer says so and offers to write a
`wpa_supplicant` file instead.

### With pip

On a device without uv, check the Python version first. Unlike uv, pip cannot
install Python for you, and older versions fail with a `SyntaxError` or `ImportError`:

```sh
python3 --version
```

Then install into a virtual environment. Debian and Ubuntu may need
`sudo apt install python3-venv` first.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

To configure NetworkManager, use the `dbus-python` from your distribution, such as
`python3-dbus` on Debian and Ubuntu. Create the environment with
`python3 -m venv --system-site-packages .venv` so that it can see that package.
Alternatively, install the build dependencies above and uncomment `dbus-python` in
`requirements.txt`.

## Usage

Run the installer as the user who will connect, not as root. NetworkManager
connections are restricted to the user who created them.

```sh
uv run main.py
```

If you installed with pip, run:

```sh
.venv/bin/python main.py
```

| Option             | Effect                                                         |
| ------------------ | -------------------------------------------------------------- |
| `-u`, `--username` | Set the username                                               |
| `-p`, `--password` | Set the password                                               |
| `-s`, `--silent`   | Ask nothing and accept all prompts; use with `-u` and `-p`     |
| `--pfxfile PATH`   | Personal certificate file, for certificate-based profiles      |
| `--backend NAME`   | Configure this service instead of detecting it, see below      |
| `--wpa_conf`       | Same as `--backend wpa_supplicant-file`                        |
| `--iwd_conf`       | Same as `--backend iwd-file`; wins over `--wpa_conf`           |
| `--gui NAME`       | Prefer `tty`, `tkinter`, `zenity`, `kdialog` or `yad`          |
| `-d`, `--debug`    | Log debugging information to standard error                    |

> [!WARNING]
> A password given with `--password` is visible in your shell history and to other
> local users in the process list. Prefer the prompt on shared machines.

### Network services

Without `--backend`, the installer configures the first of these that is running:

| `--backend`           | Service        | What it does                                         |
| --------------------- | -------------- | ---------------------------------------------------- |
| `networkmanager`      | NetworkManager | Adds the connection over D-Bus, for your user only   |
| `connman`             | ConnMan        | Writes `/var/lib/connman/eduroamgwdg.config`         |
| `iwd`                 | iwd            | Writes `/var/lib/iwd/eduroam.8021x`                  |
| `wpa_supplicant`      | wpa_supplicant | Adds the network to the file it runs with            |
| `iwd-file`            | none           | Writes `eduroam.8021x` to the working directory      |
| `wpa_supplicant-file` | none           | Writes `cat_installer.conf` to the installer directory |

NetworkManager and ConnMan come first because they run iwd or wpa_supplicant
themselves and would overwrite changes made below them. If none is detected,
NetworkManager is tried anyway. If the detected service cannot be used, for example
because `dbus-python` is missing, the installer offers a `wpa_supplicant` file
instead.

ConnMan and iwd pick up the new file by themselves. For wpa_supplicant:

- The installer reads which file it runs with from its command line, and changes
  only a marked region of that file. Your other networks are kept, and the
  previous version is saved next to it as `<file>.<date>-<time>.bak`.
- Earlier entries for eduroam outside the region are removed, so they cannot
  conflict.
- A running wpa_supplicant is told to reload with `wpa_cli reconfigure`. If that
  is not possible, or wpa_supplicant is not running, the installer tells you what
  to run.
- If wpa_supplicant runs without a configuration file, it is controlled by another
  program, and the installer refuses to change it.

These services run as root, possibly before your home directory is mounted, so
their certificates are copied to system directories:
`/var/lib/connman/eduroamgwdg/` and `/etc/wpa_supplicant/eduroam-gwdg/`. iwd
embeds the certificate in its file.

### Administrator rights

ConnMan, iwd and wpa_supplicant keep their configuration in system directories.
When the installer first needs to write there, it starts a small helper through
`pkexec`, or `sudo` if `pkexec` is not installed. You authenticate once, and the
helper carries out the remaining operations of that run. Passwords and other file
content are passed to it through a pipe, never on a command line. Files are
replaced in one step, so a service never reads a half-written file.

NetworkManager needs no administrator rights.

### User interface

In a terminal, the installer asks its questions there, formatted with
[Rich](https://github.com/Textualize/rich). It stays line based, so a `pkexec` or
`sudo` password prompt can appear in between. The choice is, in order:

1. no interaction, with `--silent`
2. the terminal, with `--gui tty`
3. the dialog tool named by `--gui`, if it is installed and a graphical session is
   running
4. tkinter, if `--gui` was given but not satisfied above, and `DISPLAY` is set
5. the terminal, if standard input and output are both terminals
6. the first of `yad`, `zenity` and `kdialog` that is installed, then tkinter, in
   a graphical session; for example when started from a file manager
7. the terminal

A graphical session means that `DISPLAY` or `WAYLAND_DISPLAY` is set. tkinter
needs `DISPLAY`, which XWayland provides on Wayland.

### Generated files

The installer directory is `$XDG_CONFIG_HOME/cat_installer`, by default
`~/.config/cat_installer`. It is readable only by you.

| File                                | Content                                   |
| ----------------------------------- | ----------------------------------------- |
| `ca.pem`                            | Trusted CA certificates                   |
| `ca/`                               | Hashed certificates, see below            |
| `user.p12`                          | Personal certificate, if the profile uses one |
| `cat_installer.conf`                | `wpa_supplicant` configuration            |
| `<ssid>.8021x` in the working directory | `iwd` configuration                   |

> [!CAUTION]
> The `wpa_supplicant`, `iwd` and ConnMan files contain your password in clear
> text. They are created with mode `0600`.

To use the file from `--backend iwd-file`, move it to `/var/lib/iwd/`. To use the
file from `--backend wpa_supplicant-file`, pass it with `wpa_supplicant -c`.

iwd names files after the network. A name that is not only letters, digits, `-`,
`_` and spaces becomes `=` followed by the name in hexadecimal.

`ca/` is used only for NetworkManager 1.54.3 to 1.58.0, which needs one hashed file
per certificate, and only when a profile has several certificates. The Göttingen
profile has one, so it always uses `ca.pem`.

### Exit codes

| Code | Meaning                                                      |
| ---- | ------------------------------------------------------------ |
| 0    | Success                                                      |
| 1    | Stopped by the user, or the personal certificate is unusable |
| 2    | The profile contains no CA certificate                       |
| 3    | NetworkManager connections could not be read over D-Bus      |
| 4    | The service named by `--backend` cannot be used here         |
| 5    | Administrator rights were not available or were refused      |

## Architecture

```text
main.py                     Entry point
src/
├── cli.py                  Command line parsing, exit codes
├── installer.py            The installation sequence
├── config.py               Profile dataclass and the Göttingen profile
├── messages.py             All user-facing text
├── credentials.py          Username validation, collecting credentials
├── certificates.py         CA installation, reading PKCS#12 files
├── paths.py                Installer file locations, private file writes
├── privileged.py           System file access, with one privileged session
├── privileged_helper.py    The helper running as root, standard library only
├── system.py               Desktop, process and network interface detection
├── errors.py               Exit codes, InstallerError
├── backends/
│   ├── __init__.py         select_backend() detects the service
│   ├── base.py             Backend abstract base class
│   ├── network_manager.py  Connections over D-Bus
│   ├── connman.py          ConnMan provisioning files
│   ├── iwd.py              iwd configuration
│   └── wpa_supplicant.py   wpa_supplicant configuration
└── ui/
    ├── __init__.py         create_ui() chooses the interface
    ├── base.py             UserInterface abstract base class
    ├── terminal.py         Terminal, with Rich
    ├── tk.py               tkinter
    ├── dialogs.py          zenity, kdialog, yad
    └── silent.py           No interaction
tests/                      pytest suite; never touches the real system
```

`installer.run_installer()` is the best place to start reading. It drives the steps
in order and is the only module which knows about all the others.

These changes cover most future work:

- **Another institution:** create a `Profile` in `config.py`.
- **Another network service:** subclass `Backend` in `backends/base.py`, and add
  it to `BACKENDS` and, if it can be detected, `AUTO_DETECT_ORDER` in
  `backends/__init__.py`. Write system files with the functions in
  `privileged.py`.
- **A translation:** create a `Messages` instance with translated fields, and pass
  it to `run_installer()`.
- **Another dialog tool:** subclass `DialogUI` in `ui/dialogs.py` and add it to
  `_DIALOG_TOOLS` in `ui/__init__.py`.

## Development

```sh
uv sync                               # environment with ruff, mypy and pytest
uv run ruff check src main.py tests   # lint
uv run ruff format src main.py tests  # format
uv run mypy                           # strict type checking
uv run pytest                         # tests
```

The tests run on any system, macOS included. They redirect every system path into
a temporary directory, replace D-Bus with an in-memory NetworkManager, and fail if
anything tries to gain administrator rights. They cannot show that a real
service accepts the configuration, so test changes to a backend on a Linux
system running that service.

## Differences from the original script

Apart from the changes below, behaviour is unchanged.

### New features

- ConnMan, iwd and standalone wpa_supplicant are configured directly, chosen by
  detecting the running service, or with `--backend`.
- The terminal interface uses Rich, and is preferred whenever the installer runs
  in a terminal. The dialog tools and tkinter are used when there is no terminal.
- Wayland sessions without XWayland can use the dialog tools.

### Requirements

- Python 3.12 or newer, and `cryptography` is now mandatory. This removes the
  fallbacks to pyOpenSSL and the `openssl` command for reading personal certificates.
- NetworkManager older than 0.9 is no longer supported. The original crashed on
  those versions, so that code path could not run.
- `--gui` rejects unknown values instead of ignoring them.
- `rich` is a new dependency.

### Security fixes

- `wpa_supplicant` and `iwd` files were world-readable; they are now `0600`.
- The `iwd` privileged write passed the configuration, including the password,
  through a shell command. A password containing shell syntax could run commands
  as root. It now goes over standard input, without a shell.

### Bug fixes

- The NetworkManager 1.54.3 to 1.58.0 CA workaround was never applied, because the
  flag was set on the wrong object.
- NetworkManager versions were compared as floats. 1.3 to 1.9 counted as newer than
  1.24, while 0.9 to 1.2, 1.10 to 1.23 and 1.100 crashed the installer.
- Certificate passwords were passed as the wrong type: silent mode always failed,
  and interactive mode stored `b'...'` in the configuration.
- `--password` was not checked against the certificate in interactive mode.
- A certificate with neither common name nor email address crashed the installer.
  It now asks for the username.
- `wpa_supplicant` TLS configurations had an unterminated quote and the wrong key
  path, and multiple networks were written without a line break between them.
- The `iwd` privileged write used a relative path, which `pkexec` resolves elsewhere.
- `--gui tkinter` never selected tkinter.
- A missing `XDG_CONFIG_HOME` directory crashed the installer.
- Removing the wired connection matched any name contained in `wired-eduroam`, such
  as `wired`. It now requires an exact match.
- SSIDs were decoded byte by byte, so non-ASCII names never matched.
- `yad` was passed a literal quoted image name and a misspelled option, and `zenity`
  was passed empty arguments.
- Values in `wpa_supplicant` files were not escaped. A password containing `"`
  broke the file, and a newline could add settings. Such values are now written
  in hexadecimal, which wpa_supplicant accepts for every text setting.
- Values in `iwd` files were not escaped either. A backslash was misread, and a
  newline could add settings.
- At the end of input, such as a closed pipe, the terminal interface stops with
  exit code 1 instead of a traceback.

## Legacy script

The original single-file installer,
[`eduroam-linux-University_Goettingen-University_of_Gottingen.py`](eduroam-linux-University_Goettingen-University_of_Gottingen.py),
is kept unmodified for legacy purposes, as a reference and for comparing behaviour.
It is not used by this project and receives none of the fixes listed above, so use
`main.py` for new installations.

## Acknowledgments

This project is derived from the CAT Linux installer, and exists thanks to the work
of its authors and contributors.

**Authors**

- Tomasz Wolniewicz
- Michał Gasewicz (NetworkManager support)

**Contributors**

- [Steffen Klemer](https://github.com/sklemer1)
- [ikreb7](https://github.com/ikreb7)
- [Dimitri Papadopoulos Orfanos](https://github.com/DimitriPapadopoulos)
- [sdasda7777](https://github.com/sdasda7777)
- [Matt Jolly](http://gitlab.com/Matt.Jolly)
- Benoit Le Texier
- [jmontes](https://github.com/m4r4d0n4)

Contributions to the original work were made on behalf of the GÉANT project, which
received funding from the European Union's Framework Programme 7 and Horizon 2020
research and innovation programme. The original copyright notices are retained in
[`src/__init__.py`](src/__init__.py).

## License

This project is distributed under the GÉANT Standard Open Source Software Outward
Licence, the same license as CAT, because derivative works must keep it. The full
text is in [`LICENSE`](LICENSE). By using, modifying or redistributing this project,
you accept those terms. If you pass it on, you must do so under the same license and
make a reasonable effort to get your recipients' consent to it.

This is a modified version of CAT, as described in
[Differences from the original script](#differences-from-the-original-script). It is
an unofficial project, and is not endorsed by GÉANT, the authors of CAT, the
University of Göttingen or GWDG.
