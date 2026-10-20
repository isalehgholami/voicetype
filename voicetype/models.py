"""Vosk model management: locate, download, verify and extract (zip-slip safe)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelInfo:
    key: str
    dirname: str
    url: str
    size_mb: int
    sha256: str | None = None  # Alphacephei publishes no checksums; pass --sha256 to enforce one


MODELS: dict[str, ModelInfo] = {
    "fa-small": ModelInfo("fa-small", "vosk-model-small-fa-0.42", "https://alphacephei.com/vosk/models/vosk-model-small-fa-0.42.zip", 53),
    "fa": ModelInfo("fa", "vosk-model-fa-0.42", "https://alphacephei.com/vosk/models/vosk-model-fa-0.42.zip", 1600),
}  # fmt: skip


class ModelError(RuntimeError):
    pass


def user_models_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "voicetype" / "models"


def search_dirs() -> list[Path]:
    return [Path("/usr/share/vosk"), user_models_dir()]


def installed(dirs: list[Path] | None = None) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for d in dirs or search_dirs():
        for info in MODELS.values():
            p = d / info.dirname
            if p.is_dir():
                found[info.key] = p
    return found


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        if root != target and root not in target.parents:
            raise ModelError(f"unsafe path in archive: {member.filename}")
    zf.extractall(dest)


def install(
    key: str,
    dest_dir: Path | None = None,
    *,
    url: str | None = None,
    expected_sha256: str | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    if key not in MODELS:
        raise ModelError(f"unknown model '{key}' (available: {', '.join(MODELS)})")
    info = MODELS[key]
    dest_dir = dest_dir or user_models_dir()
    final = dest_dir / info.dirname
    if final.is_dir():
        return final
    expected = expected_sha256 or info.sha256
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest_dir) as tmp:
        archive = Path(tmp) / "model.zip"
        try:
            with urllib.request.urlopen(url or info.url, timeout=60) as resp, open(archive, "wb") as out:  # noqa: S310
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                while block := resp.read(1 << 20):
                    out.write(block)
                    done += len(block)
                    if progress:
                        progress(done, total)
        except OSError as e:
            raise ModelError(f"download failed: {e}") from e
        digest = sha256_file(archive)
        if expected and digest != expected.lower():
            raise ModelError(f"checksum mismatch: expected {expected}, got {digest}")
        try:
            with zipfile.ZipFile(archive) as zf:
                if zf.testzip() is not None:
                    raise ModelError("corrupt archive")
                extract_to = Path(tmp) / "x"
                safe_extract(zf, extract_to)
        except zipfile.BadZipFile as e:
            raise ModelError(f"not a valid zip archive: {e}") from e
        extracted = extract_to / info.dirname
        if not extracted.is_dir():
            raise ModelError(f"archive does not contain '{info.dirname}/'")
        (extracted / "voicetype-manifest.json").write_text(json.dumps({"key": key, "sha256": digest}), encoding="utf-8")
        shutil.move(str(extracted), str(final))
    return final
