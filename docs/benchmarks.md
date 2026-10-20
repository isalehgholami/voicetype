# Benchmarks (T-0.6 / T-3.3)

**Status: no measurements yet.** Results require a microphone and (for `google_free`) internet access, so they must be produced on your machine:

```bash
python scripts/record_corpus.py            # record the 50 sentences in tests/corpus/sentences.tsv
                                           # (ids 01-20 = the T-0.6 subset: 10 Persian + 10 mixed)
python scripts/bench.py tests/corpus --providers google_free,vosk,whisper --out docs/benchmarks.results.md
```

Metrics: WER/CER after normalization (`voicetype/metrics.py`: NFKC, Arabic→Persian letters, punctuation and ZWNJ removed),
latency = STT + post-processing per utterance (p50/p95, target ≤ 1.5 s for Google).

## Results

_(paste the table from `docs/benchmarks.results.md` here, then pick the default provider accordingly)_

| Provider | n | WER | CER | latency p50 (s) | latency p95 (s) | errors | ≤1.5 s |
|---|---|---|---|---|---|---|---|
| google_free | | | | | | | |
| vosk (fa-small) | | | | | | | |
| vosk (fa) | | | | | | | |
| whisper (small) | | | | | | | |
