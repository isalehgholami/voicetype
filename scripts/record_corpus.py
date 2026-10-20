#!/usr/bin/env python3
"""Record the benchmark corpus: reads tests/corpus/sentences.tsv, asks you to read each sentence.

  python scripts/record_corpus.py [--only 01-20] [--out tests/corpus]

Enter = start recording, Enter again = stop. Writes <id>.wav (16 kHz mono int16) + <id>.txt.
"""
import argparse
import sys
import time
from pathlib import Path

from voicetype.audio import AudioCapture, save_wav


def parse_range(spec: str | None) -> tuple[int, int]:
    if not spec:
        return 0, 10**9
    lo, _, hi = spec.partition("-")
    return int(lo), int(hi or lo)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tests/corpus")
    ap.add_argument("--only", help="id range, e.g. 01-20 (T-0.6 uses 01-20)")
    args = ap.parse_args()
    out = Path(args.out)
    lo, hi = parse_range(args.only)
    for line in (out / "sentences.tsv").read_text(encoding="utf-8").splitlines():
        sid, _, text = line.partition("\t")
        if not sid.strip() or not lo <= int(sid) <= hi:
            continue
        if (out / f"{sid}.wav").exists():
            continue
        print(f"\n[{sid}] {text}")
        input("  Enter to start… ")
        cap = AudioCapture(lambda c: None)
        cap.start()
        t0 = time.time()
        input("  speaking… Enter to stop ")
        pcm = cap.stop()
        save_wav(out / f"{sid}.wav", pcm)
        (out / f"{sid}.txt").write_text(text + "\n", encoding="utf-8")
        print(f"  saved {len(pcm) / 32000:.1f}s ({time.time() - t0:.1f}s wall)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
