# eduroam-gwdg

Configures [eduroam](https://eduroam.org/) on Linux for members of the University
of Göttingen. It adds the connection to NetworkManager, or generates a
configuration file for `wpa_supplicant` or `iwd`.

This is a modernized, modular version of the single-file installer produced by
GÉANT's [CAT](https://cat.eduroam.org/). See [Legacy script](#legacy-script) and
[Acknowledgments](#acknowledgments). For the terms, see [License](#license).

## Requirements

- Linux
- Python 3.12 or newer, which [uv](https://docs.astral.sh/uv/) can provide for you
- uv or pip
- For NetworkManager only: `dbus-python`, see below

## Installation

### With uv

Generating a `wpa_supplicant` or `iwd` file needs only:

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
| `--wpa_conf`       | Only generate a `wpa_supplicant` file                          |
| `--iwd_conf`       | Only generate an `iwd` file; wins over `--wpa_conf`            |
| `--gui NAME`       | Prefer `tty`, `tkinter`, `zenity`, `kdialog` or `yad`          |
| `-d`, `--debug`    | Log debugging information to standard error                    |

> [!WARNING]
> A password given with `--password` is visible in your shell history and to other
> local users in the process list. Prefer the prompt on shared machines.

### User interface

`--silent` and `--gui tty` always stay in the terminal, as does any session where
`DISPLAY` is unset. Otherwise the choice is, in order:

1. the tool named by `--gui`, if it is installed
2. tkinter, if `--gui` was given and tkinter is available
3. the first of `yad`, `zenity` and `kdialog` that is installed
4. the terminal

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
> The `wpa_supplicant` and `iwd` files contain your password in clear text. They
> are created with mode `0600`.

To use the `iwd` file, move it to `/var/lib/iwd/`. To use the `wpa_supplicant`
file, pass it with `wpa_supplicant -c`.

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
├── system.py               Desktop and network interface detection
├── errors.py               Exit codes, InstallerError
├── backends/
│   ├── network_manager.py  Connections over D-Bus
│   ├── wpa_supplicant.py   wpa_supplicant configuration
│   └── iwd.py              iwd configuration
└── ui/
    ├── __init__.py         create_ui() chooses the interface
    ├── base.py             UserInterface abstract base class
    ├── tty.py              Terminal
    ├── tk.py               tkinter
    ├── dialogs.py          zenity, kdialog, yad
    └── silent.py           No interaction
```

`installer.run_installer()` is the best place to start reading. It drives the steps
in order and is the only module which knows about all the others.

Three changes cover most future work:

- **Another institution:** create a `Profile` in `config.py`.
- **A translation:** create a `Messages` instance with translated fields, and pass
  it to `run_installer()`.
- **Another dialog tool:** subclass `DialogUI` in `ui/dialogs.py` and add it to
  `_DIALOG_TOOLS` in `ui/__init__.py`.

## Development

```sh
uv sync                 # environment with ruff and mypy
uv run ruff check src main.py   # lint
uv run ruff format src main.py  # format
uv run mypy                     # strict type checking
```

## Differences from the original script

Apart from the changes below, behaviour is unchanged.

### Requirements

- Python 3.12 or newer, and `cryptography` is now mandatory. This removes the
  fallbacks to pyOpenSSL and the `openssl` command for reading personal certificates.
- NetworkManager older than 0.9 is no longer supported. The original crashed on
  those versions, so that code path could not run.
- `--gui` rejects unknown values instead of ignoring them.

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
