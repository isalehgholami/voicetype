"""STT benchmark: WER / CER and latency per provider over a folder of ``*.wav`` + ``*.txt`` pairs."""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .config import PostprocessConfig
from .metrics import cer, wer
from .postprocess import Pipeline
from .stt import registry
from .stt.base import STTBackend, STTError

LATENCY_TARGET_SECONDS = 1.5


@dataclass
class BenchResult:
    provider: str
    n: int = 0
    wer: float = 0.0
    cer: float = 0.0
    latencies: list[float] = field(default_factory=list)
    errors: int = 0
    skipped_reason: str = ""

    @property
    def p50(self) -> float:
        return statistics.median(self.latencies) if self.latencies else 0.0

    @property
    def p95(self) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        return s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]


def load_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise ValueError(f"{path.name}: WAV must be 16 kHz mono int16")
        return w.readframes(w.getnframes())


def load_corpus(folder: Path) -> list[tuple[Path, str]]:
    items = []
    for wav in sorted(folder.glob("*.wav")):
        txt = wav.with_suffix(".txt")
        if txt.exists():
            items.append((wav, txt.read_text(encoding="utf-8").strip()))
    return items


def evaluate(
    name: str,
    backend: STTBackend,
    items: list[tuple[Path, str]],
    pipeline: Callable[[str], str] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> BenchResult:
    res = BenchResult(name)
    w_sum = c_sum = 0.0
    for wav, ref in items:
        pcm = load_wav(wav)
        t0 = clock()
        try:
            hyp = backend.transcribe(pcm).text
            if pipeline:
                hyp = pipeline(hyp)
        except STTError:
            res.errors += 1
            hyp = ""
        res.latencies.append(clock() - t0)
        w_sum += wer(ref, hyp)
        c_sum += cer(ref, hyp)
        res.n += 1
    if res.n:
        res.wer, res.cer = w_sum / res.n, c_sum / res.n
    return res


def render_markdown(results: list[BenchResult]) -> str:
    lines = [
        "| Provider | n | WER | CER | latency p50 (s) | latency p95 (s) | errors | ≤1.5 s |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.skipped_reason:
            lines.append(f"| {r.provider} | – | – | – | – | – | – | skipped: {r.skipped_reason} |")
            continue
        ok = "yes" if r.p95 <= LATENCY_TARGET_SECONDS else "no"
        lines.append(
            f"| {r.provider} | {r.n} | {r.wer:.3f} | {r.cer:.3f} | {r.p50:.2f} | {r.p95:.2f} | {r.errors} | {ok} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Benchmark STT providers")
    ap.add_argument("folder", nargs="?", default="tests/corpus")
    ap.add_argument("--providers", default="google_free,vosk,whisper")
    ap.add_argument("--out", help="write the markdown table to this file")
    args = ap.parse_args(argv)

    items = load_corpus(Path(args.folder))
    if not items:
        print(f"no wav+txt pairs in {args.folder} (record some with scripts/record_corpus.py)", file=sys.stderr)
        return 2
    pipeline = Pipeline(PostprocessConfig(auto_punctuate=False, capitalize_first=False))
    results: list[BenchResult] = []
    for name in args.providers.split(","):
        name = name.strip()
        try:
            backend = registry.create(name, registry.default_options())
            results.append(evaluate(name, backend, items, pipeline))
        except STTError as e:
            results.append(BenchResult(name, skipped_reason=e.detail or str(e)))
    table = render_markdown(results)
    print(table)
    if args.out:
        Path(args.out).write_text(table + "\n", encoding="utf-8")
    return 0
