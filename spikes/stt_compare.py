#!/usr/bin/env python3
"""T-0.6 — compare Persian STT quality (Google free / Vosk / Whisper). Thin wrapper over voicetype.bench.

  python spikes/stt_compare.py tests/corpus      # needs *.wav (16 kHz mono) + *.txt pairs
"""
import sys

from voicetype.bench import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["tests/corpus"]))
