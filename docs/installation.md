# Installation and requirements

## Requirements

VoiceType targets Linux with a Wayland or X11 session. Arch with KDE Plasma 6 on Wayland is the primary development target; the installer also supports Debian/Ubuntu, Fedora, openSUSE, Alpine, and Void.

| Purpose | Tools | Needed for |
|---|---|---|
| Audio server | PipeWire with WirePlumber (`pipewire`, `wireplumber`), or PulseAudio | always |
| Audio client | `pw-cat` (shipped by `pipewire-audio`) | recording through PipeWire, so the desktop keeps its microphone |
| Wayland injection | `wl-clipboard` (`wl-copy`) and `ydotool` with a running `ydotoold` | the default paste route on Wayland |
| Wayland injection (alternative) | `wtype` | typing directly instead of via the clipboard |
| X11 injection | `xdotool` and `xclip` | injection on X11 |
| Terminal detection | `kdotool` on Wayland, `xdotool` on X11 | using `Ctrl+Shift+V` instead of `Ctrl+V` in a terminal |
| Overlay | `gtk4`, `gtk4-layer-shell`, `python-gobject`, `python-cairo` | the floating overlay |
| Notifications | `libnotify` (`notify-send`) | desktop notifications on error |
| Media control | `playerctl` | pausing music while recording |
| Python | Python 3.11 or newer | the daemon |
| pipx | `python-pipx` | the isolated installation performed by the installer |

The package names above are the Arch ones. The installer maps them for every supported distribution; the Debian and Ubuntu names differ for a few of them: `libgtk-4-1`, `gir1.2-gtklayershell-0.0`, `python3-gi`, `python3-cairo`, `libnotify-bin`, `python3`, and `pipx`.

Python dependencies are declared in `pyproject.toml` and installed by the installer: `sounddevice` and `SpeechRecognition` are required, while `vosk`, `faster-whisper`, `evdev`, `PySocks`, `translators`, and `googletrans` are optional extras.

Two further requirements apply to specific features:

- **Hold-mode shortcut.** `[hotkey] toggle_mode = "hold"` reads the keyboard through `evdev` and therefore needs the `input` group and write access to `/dev/uinput`. The default `press` mode uses the desktop shortcut and needs neither.
- **Offline recognition.** Vosk needs a model (`voicetype models install fa-small`); Whisper downloads its weights on first use and caches them locally.

## Installation

### Automated installation

The installer is the recommended path. It is distribution-aware, installs into a `pipx` virtual environment, and never modifies your system without `--system-deps`.

```bash
git clone https://github.com/isalehgholami/voicetype.git
cd voicetype
chmod +x scripts/*.sh              # archives may drop the executable bit

./scripts/install-user.sh --system-deps
```

The script runs these steps and reports the result of each one:

1. **Preflight.** Refuses to run as root, and verifies that Python 3.11+ and `pipx` are available. `pipx` is required because PEP 668 marks the system interpreter as externally managed, which breaks `pip install --user`.
2. **Distribution detection.** Reads `/etc/os-release` and selects `pacman`, `apt-get`, `dnf`, `zypper`, `apk`, or `xbps-install`.
3. **System packages.** Checks every dependency from the table above. With `--system-deps` the missing ones are installed through `sudo`; without it, the script stays root-free and prints the exact command for your package manager.
4. **Groups and services.** Adds your account to the `input` and `audio` groups and enables the PipeWire and ydotool user services.
5. **Microphone test.** Records three seconds with `pw-record` and reports the captured level in dBFS.
6. **Python environment.** Installs VoiceType into a `pipx` venv with the selected extras.
7. **Configuration.** Writes `~/.config/voicetype/config.toml` with a short guide at the top. An existing valid file is kept as it is; an invalid one is backed up as `config.toml.bak-<timestamp>` and replaced with the defaults.
8. **Service unit.** Writes `~/.config/systemd/user/voicetype.service`, points it at the installed binary, and enables it for the next login.
9. **Daemon.** Starts the daemon and waits until it answers on its control socket.
10. **Shortcut.** Registers `Meta+Alt+V` as a global shortcut in the place the running desktop expects it: `kglobalshortcutsrc` on KDE Plasma, a custom keybinding on GNOME, a bind line on Hyprland, a `bindsym` line on Sway and i3. On a desktop it does not know, it prints the line to add yourself.
11. **Verification.** Prints a summary of what is working and what still needs attention.

Log out and back in after the first run so that the `input` and `audio` group memberships take effect.

#### Installer options

| Option | Effect |
|---|---|
| *(no arguments)* | Install or update, keeping the extras installed by a previous run. The default extra is `socks`. |
| `<extras>` | Comma-separated list of `socks`, `vosk`, `whisper`, `hotkey`, `translate`. Extras from previous runs are preserved unless `--reset-extras` is given. |
| `--system-deps` | Install the missing system packages with `sudo`. |
| `--reinstall` | Delete the `pipx` venv and rebuild it from scratch. |
| `--reset-extras` | Install exactly the requested extras and drop the others. |
| `--no-hotkey` | Leave the global shortcut untouched. |
| `--no-service` | Do not touch the systemd user unit. |
| `--no-mic-check` | Skip the microphone test. |
| `--hotkey <combo>` | Register a different shortcut, for example `--hotkey Ctrl+Alt+Space`. The `HOTKEY` environment variable has the same effect. |
| `--desktop <name>` | Register the shortcut for a specific desktop: `auto` (the default), `kde`, `gnome`, `hyprland`, `sway`, or `i3`. |
| `--hotkey-only` | Only register the global shortcut — nothing is installed or updated. |
| `-v`, `--verbose` | Show the full output of every command the script runs. |
| `-q`, `--quiet` | Report problems only. |
| `--help` | Print the usage summary. |

The `translate` extra is always installed, whether or not you ask for it, so that the configured translation language can be validated and enabling translation later needs no further work.

The installer is idempotent. Run it again after `git pull` to update the code and its dependencies, rebuild a broken venv, rewrite the unit file, and restart the daemon so the new code is live:

```bash
git pull && ./scripts/install-user.sh
```

#### Verifying the installation

```bash
voicetype doctor
```

`doctor` walks the whole environment and prints one line per check. A line marked `[✓]` is satisfied, `[✗]` is a required item that is missing, and `[!]` is an optional item that is missing or degraded. Each failing line comes with a hint describing what has to be installed or fixed.

### Manual installation

If you would rather manage the environment yourself, install into a virtual environment and start the daemon by hand:

```bash
git clone https://github.com/isalehgholami/voicetype.git
cd voicetype

python3 -m venv ~/.venvs/voicetype
source ~/.venvs/voicetype/bin/activate
pip install -e ".[socks,vosk]"          # or: pip install -e .

voicetype config init # write ~/.config/voicetype/config.toml
voicetype run                           # run the daemon in the foreground
```

Then bind `voicetype-toggle` to your preferred shortcut. The installer does this for you on KDE Plasma, GNOME, Hyprland, Sway and i3; with a manual installation, use *System Settings → Keyboard → Shortcuts → Custom Shortcuts* on KDE Plasma, and bind `voicetype-toggle` in the compositor or desktop configuration everywhere else.

`pipx` is a convenient alternative to a hand-made virtual environment:

```bash
pipx install "voicetype[socks,vosk]"
```

To run the daemon at login, copy `packaging/voicetype.service` into `~/.config/systemd/user/`. Adjust two paths: `ExecStart` must point at your `voicetype` binary, and `ExecStartPre` at the `wait-display` helper in `packaging/`, which delays start-up until the session has a display. Then run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now voicetype
```

### Distribution packages

A `PKGBUILD` is included for Arch:

```bash
cd packaging && makepkg -si
```

`python-sounddevice` and `python-speechrecognition` are not in the Arch repositories; install them from the AUR with `yay` or `paru` first. The package installs the daemon, the systemd user unit in `/usr/lib/systemd/user/`, and a desktop entry for the toggle command. An AUR package definition is available in `packaging/aur/`.

```bash
systemctl --user enable --now voicetype
```

### Uninstalling

```bash
systemctl --user disable --now voicetype
pipx uninstall voicetype
rm ~/.config/systemd/user/voicetype.service
rm -rf ~/.config/voicetype ~/.local/lib/voicetype
rm -f ~/.local/share/applications/voicetype-toggle.desktop
rm -f ~/.local/share/kglobalaccel/voicetype-toggle.desktop
```

The last two files hold the KDE shortcut. Remove the binding in *System Settings → Keyboard → Shortcuts* as well. If you installed the Arch package, remove the unit from `/usr/lib/systemd/user/voicetype.service` and the package itself with `pacman -Rns voicetype-git`.
