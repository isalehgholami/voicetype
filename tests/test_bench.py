import struct
import wave

from voicetype.bench import BenchResult, evaluate, load_corpus, main, render_markdown
from voicetype.stt.base import STTBackend, STTNetworkError, STTResult


def make_item(folder, sid, ref):
    with wave.open(str(folder / f"{sid}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<h", 100) * 1600)
    (folder / f"{sid}.txt").write_text(ref + "\n", encoding="utf-8")


class Scripted(STTBackend):
    def __init__(self, outputs):
        self.outputs = iter(outputs)

    def transcribe(self, pcm, cancel=None):
        v = next(self.outputs)
        if isinstance(v, Exception):
            raise v
        return STTResult(v)


def test_corpus_has_50_sentences_with_unique_ids():
    from pathlib import Path

    lines = (Path(__file__).parent / "corpus" / "sentences.tsv").read_text(encoding="utf-8").splitlines()
    ids = [ln.split("\t")[0] for ln in lines]
    assert len(ids) == 50 and len(set(ids)) == 50
    assert all(ln.split("\t")[1].strip() for ln in lines)


def test_evaluate_computes_wer_cer_latency_and_errors(tmp_path):
    make_item(tmp_path, "01", "a b c d")
    make_item(tmp_path, "02", "x y")
    make_item(tmp_path, "03", "hello")
    items = load_corpus(tmp_path)
    ticks = iter(range(0, 100, 1))
    res = evaluate("fake", Scripted(["a b c d", "x z", STTNetworkError("down")]), items, clock=lambda: next(ticks) * 0.5)
    assert res.n == 3 and res.errors == 1
    assert abs(res.wer - (0 + 0.5 + 1.0) / 3) < 1e-9
    assert len(res.latencies) == 3 and res.p50 > 0


def test_render_markdown_and_skipped():
    ok = BenchResult("google_free", n=2, wer=0.1, cer=0.05, latencies=[0.4, 0.9])
    skipped = BenchResult("vosk", skipped_reason="model missing")
    text = render_markdown([ok, skipped])
    assert "| google_free | 2 | 0.100 | 0.050 |" in text and "yes" in text
    assert "skipped: model missing" in text


def test_main_without_corpus_returns_2(tmp_path, capsys):
    assert main([str(tmp_path)]) == 2
    assert "no wav+txt pairs" in capsys.readouterr().err
