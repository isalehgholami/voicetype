#!/usr/bin/env bash
# T-0.1 — prepare the target machine (Arch + Plasma 6 Wayland)
set -euo pipefail

sudo pacman -S --needed pipewire pipewire-pulse wireplumber python python-pip python-pipx python-gobject python-cairo \
    gtk4 gtk4-layer-shell wtype ydotool wl-clipboard xdotool xclip portaudio libnotify

target_user="${SUDO_USER:-$USER}"
sudo usermod -aG input,audio "$target_user"
echo "Groups updated; log out and back in so they take effect."

systemctl --user enable --now pipewire wireplumber || true

# ydotool daemon (needed to send the Ctrl+V paste key)
systemctl --user enable --now ydotool.service 2>/dev/null || \
    echo "No user ydotool.service found; start the daemon manually (sudo ydotoold &)."

echo "--- checks ---"
echo "groups: $(groups)"

# Record 3 s and validate the WAV we actually produced.
# `timeout` exits 124 when *it* stops the command, so the exit status of
# `timeout` says nothing about the recording — validate the artifact instead.
test_wav="${TMPDIR:-/tmp}/voicetype-test.wav"
rm -f "$test_wav"
echo "recording 3 seconds..."
pw_err="$(timeout --signal=INT 3 pw-record "$test_wav" 2>&1 >/dev/null || true)"
python3 - "$test_wav" "$pw_err" <<'PY'
import array
import math
import sys
import wave

path, pw_err = sys.argv[1], sys.argv[2]


def fail(msg: str) -> None:
    print(f"pw-record FAILED — {msg}")
    if pw_err.strip():
        print(f"  pw-record said: {pw_err.strip()}")
    sys.exit(1)


try:
    with wave.open(path) as w:
        rate = w.getframerate()
        frames = w.getnframes()
        raw = w.readframes(frames)
except Exception as exc:  # missing file, truncated header, ...
    fail(f"cannot read {path}: {exc}")

if frames == 0:
    fail("no audio frames captured")

samples = array.array("h")
samples.frombytes(raw[: len(raw) // 2 * 2])
if sys.byteorder == "big":
    samples.byteswap()
rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0
dbfs = 20 * math.log10(rms) if rms > 0 else -99.0
print(f"pw-record OK — {frames / rate:.1f} s captured, RMS {dbfs:.0f} dBFS")
if dbfs < -60:
    print("hint: level is very low — check the mic is unmuted and is the default source")
PY
rm -f "$test_wav"
