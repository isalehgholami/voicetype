#!/usr/bin/env python3
"""Benchmark STT providers: python scripts/bench.py [tests/corpus] [--providers a,b] [--out file.md]"""
import sys

from voicetype.bench import main

if __name__ == "__main__":
    sys.exit(main())
