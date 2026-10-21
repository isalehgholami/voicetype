"""Audible cues for the overlay: a short tone when recording starts, stops or fails.

The cues are synthesised with the standard library only — a sine wave packed
into a WAV file with :mod:`wave` — so VoiceType keeps its "no heavy
dependencies" promise (ADR-0001).  Playback is delegated to whichever command
line player the system happens to have; if none is installed, or the machine
has no audio device, the cue is silently dropped: a missing sound must never
take the daemon down.

The three cues mirror hyprwhspr's ``ping-up`` / ``ping-down`` / ``ping-error``.
"""

from __future__ import annotations

import io
import math
import os
import shutil
import subprocess
import tempfile
import threading
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

from ..log import get_logger

log = get_logger("sounds")

__all__ = [
    "SAMPLE_RATE",
    "PLAYERS",
    "ToneSpec",
    "SOUNDS",
    "SoundPlayer",
    "play_sound",
    "render_wav",
    "write_wav",
]

#: Sample rate of the synthesised cues.  44.1 kHz is what every player expects.
SAMPLE_RATE = 44100

#: The players we try, in order.  All of them read a WAV file.
PLAYERS: tuple[str, ...] = ("pw-play", "paplay", "aplay", "ffplay")

#: Peak amplitude of a cue.  It is a notification, not a soundtrack.
AMPLITUDE = 0.45

#: The fade applied to the start and the end of a tone, in milliseconds.
#: Without it the tone begins and ends with a click.
FADE_MS = 4.0


@dataclass(frozen=True)
class ToneSpec:
    """One cue: a frequency and the tones it is built from."""

    #: The frequency of every tone of the cue, in hertz.
    frequency: float
    #: The length of each tone, in milliseconds.
    durations_ms: tuple[int, ...]
    #: The silence between two tones, in milliseconds.
    gap_ms: int = 0

    @property
    def duration_ms(self) -> int:
        """The total length of the cue, the gaps included."""
        return sum(self.durations_ms) + self.gap_ms * max(0, len(self.durations_ms) - 1)


#: The three cues: recording started, recording stopped and something failed.
SOUNDS: dict[str, ToneSpec] = {
    "up": ToneSpec(frequency=600.0, durations_ms=(150,)),
    "down": ToneSpec(frequency=400.0, durations_ms=(150,)),
    "error": ToneSpec(frequency=400.0, durations_ms=(145, 190), gap_ms=5),
}


def _tone(frequency: float, duration_ms: float, sample_rate: int) -> array:
    """One sine tone with a short fade at each end."""
    count = max(1, int(sample_rate * duration_ms / 1000))
    fade = max(1, int(sample_rate * FADE_MS / 1000))
    samples = array("h")
    for index in range(count):
        envelope = 1.0
        if index < fade:
            envelope = index / fade
        elif index >= count - fade:
            envelope = max(0.0, (count - index) / fade)
        phase = 2.0 * math.pi * frequency * index / sample_rate
        value = math.sin(phase) * envelope * AMPLITUDE
        samples.append(int(max(-1.0, min(1.0, value)) * 32767))
    return samples


def render_wav(spec: ToneSpec, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Render ``spec`` into the bytes of a mono 16-bit WAV file."""
    frames = array("h")
    for index, duration_ms in enumerate(spec.durations_ms):
        if index:
            silence = max(0, int(sample_rate * spec.gap_ms / 1000))
            frames.extend(array("h", bytes(2 * silence)))
        frames.extend(_tone(spec.frequency, duration_ms, sample_rate))

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(frames.tobytes())
    return buffer.getvalue()


def write_wav(path: os.PathLike | str, spec: ToneSpec, sample_rate: int = SAMPLE_RATE) -> Path:
    """Write ``spec`` to ``path`` as a WAV file and return the path."""
    target = Path(path)
    target.write_bytes(render_wav(spec, sample_rate))
    return target


class SoundPlayer:
    """Plays the overlay's cues with whichever command line player exists.

    The player is deliberately dumb: it never raises.  A missing binary, a
    missing audio device or a player that exits with an error only produces a
    debug log line — a cue is a nicety, never a reason to disturb the daemon.
    """

    def __init__(
        self,
        enabled: bool = False,
        which=shutil.which,
        run=subprocess.Popen,
        sample_rate: int = SAMPLE_RATE,
        directory: os.PathLike | str | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self._which = which
        self._run = run
        self._sample_rate = sample_rate
        self._directory = Path(directory) if directory is not None else None
        self._lock = threading.Lock()
        self._player: str | None = None
        self._player_resolved = False
        self._files: dict[str, Path] = {}
        self._tempdir: Path | None = None
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------------ public
    def play(self, name: str) -> None:
        """Play the cue called ``name`` (``"up"``, ``"down"`` or ``"error"``).

        Unknown names are ignored.  The cue is rendered and handed to the
        player on a background thread, so the caller — the GTK thread — is
        never blocked by it.
        """
        if not self.enabled:
            return
        key = (name or "").strip().lower()
        spec = SOUNDS.get(key)
        if spec is None:
            log.debug("unknown sound %r ignored", name)
            return
        thread = threading.Thread(
            target=self._play,
            args=(key, spec),
            daemon=True,
            name="voicetype-sound",
        )
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()]
            self._threads.append(thread)
        thread.start()

    def close(self) -> None:
        """Wait for the pending cues and remove the files they used."""
        with self._lock:
            threads = list(self._threads)
            files = list(self._files.values())
            directory = self._tempdir
            self._threads = []
            self._files.clear()
            self._tempdir = None
            self._player = None
            self._player_resolved = False

        for thread in threads:
            thread.join(timeout=5)
        for path in files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)

    # ------------------------------------------------------------------ internals
    def _play(self, name: str, spec: ToneSpec) -> None:
        try:
            player = self._player_path()
            if player is None:
                log.debug("no audio player found; the cue is dropped")
                return
            path = self._cue_file(name, spec)
            process = self._run(
                [player, str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            code = process.wait()
            if code:
                log.debug("audio player %s exited with %s", player, code)
        except Exception:  # noqa: BLE001 - a cue must never escape into the daemon
            log.debug("could not play the cue %r", name, exc_info=True)

    def _player_path(self) -> str | None:
        """The first available player, resolved once."""
        with self._lock:
            if not self._player_resolved:
                self._player = self._find_player()
                self._player_resolved = True
            return self._player

    def _find_player(self) -> str | None:
        for name in PLAYERS:
            try:
                path = self._which(name)
            except Exception:  # noqa: BLE001 - a broken PATH lookup is not fatal
                path = None
            if path:
                return path
        return None

    def _cue_file(self, name: str, spec: ToneSpec) -> Path:
        """The WAV file for ``name``, rendered on first use."""
        with self._lock:
            path = self._files.get(name)
            if path is not None:
                return path
            if self._directory is not None:
                directory = self._directory
                directory.mkdir(parents=True, exist_ok=True)
            else:
                if self._tempdir is None:
                    self._tempdir = Path(tempfile.mkdtemp(prefix="voicetype-sounds-"))
                directory = self._tempdir
            path = directory / f"{name}.wav"
            write_wav(path, spec, self._sample_rate)
            self._files[name] = path
            return path


#: The player used by the module level :func:`play_sound` helper.
_default_player: SoundPlayer | None = None
_default_lock = threading.Lock()


def play_sound(name: str) -> None:
    """Play a cue with a process-wide player.

    The overlay uses its own :class:`SoundPlayer` so that the cues follow
    ``overlay.sounds``; this helper is for one-off cues elsewhere.
    """
    global _default_player
    with _default_lock:
        if _default_player is None:
            _default_player = SoundPlayer(enabled=True)
        player = _default_player
    player.play(name)
