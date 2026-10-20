"""Split long PCM into chunks for endpoints with a per-request length limit."""

from __future__ import annotations

from ..audio import SAMPLE_RATE, SAMPLE_WIDTH, rms_level
from .base import CancelToken, STTBackend, STTResult, STTUnintelligibleError

MAX_CHUNK_SECONDS = 50.0
SEARCH_WINDOW_SECONDS = 5.0  # look for a quiet point within the last N seconds of a chunk
FRAME_SECONDS = 0.1


def split_pcm(pcm: bytes, max_seconds: float = MAX_CHUNK_SECONDS) -> list[bytes]:
    """Split at the quietest 100 ms frame near each boundary so words aren't cut mid-way."""
    max_bytes = int(max_seconds * SAMPLE_RATE) * SAMPLE_WIDTH
    if len(pcm) <= max_bytes:
        return [pcm]
    frame = int(FRAME_SECONDS * SAMPLE_RATE) * SAMPLE_WIDTH
    window = int(SEARCH_WINDOW_SECONDS / FRAME_SECONDS)
    chunks: list[bytes] = []
    pos = 0
    while len(pcm) - pos > max_bytes:
        end = pos + max_bytes
        best_cut, best_level = end, 2.0
        for i in range(window):
            cut = end - i * frame
            if cut - pos < frame:
                break
            lvl = rms_level(pcm[cut - frame : cut])
            if lvl < best_level:
                best_level, best_cut = lvl, cut
        chunks.append(pcm[pos:best_cut])
        pos = best_cut
    if pos < len(pcm):
        chunks.append(pcm[pos:])
    return chunks


def transcribe_chunked(backend: STTBackend, pcm: bytes, cancel: CancelToken | None = None) -> STTResult:
    """Transcribe each chunk and join the texts. Raises only if *no* chunk produced text."""
    texts: list[str] = []
    last_unintelligible: STTUnintelligibleError | None = None
    for chunk in split_pcm(pcm):
        if cancel:
            cancel.raise_if_cancelled()
        try:
            res = backend.transcribe(chunk, cancel)
        except STTUnintelligibleError as e:
            last_unintelligible = e
            continue
        if res.text.strip():
            texts.append(res.text.strip())
    if not texts:
        if last_unintelligible:
            raise last_unintelligible
        return STTResult("", True)
    return STTResult(" ".join(texts), True)
