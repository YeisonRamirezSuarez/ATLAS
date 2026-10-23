"""
Measures wake word false negatives / false positives on your own recordings.

  tools/wake_eval/positive/*.wav   clips where someone says "Jarvis" (any variant)
  tools/wake_eval/negative/*.wav   clips of normal talk, TV, music... without it

WAV: 16 kHz, mono, 16-bit. Run:  python tools/wake_eval.py [model_dir] [clips_dir]
Prints, per sensitivity, how many positives were missed and how many negatives fired,
and which wake variants the model actually knows.
"""
import json
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import wakeword  # noqa: E402


def fires(det, path: Path) -> bool:
    det.rec.Reset()
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (wakeword.RATE, 1, 2):
            raise ValueError(f"{path.name}: needs 16 kHz mono 16-bit")
        while chunk := w.readframes(1024):
            if det.feed(chunk):
                return True
    return det.match(json.loads(det.rec.FinalResult()).get("result", []))


def main():
    model = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / wakeword.DEFAULT_MODEL
    clips = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "tools" / "wake_eval"
    pos, neg = sorted((clips / "positive").glob("*.wav")), sorted((clips / "negative").glob("*.wav"))
    if not pos and not neg:
        sys.exit(f"No clips in {clips}/positive or {clips}/negative")

    words = model / "graph" / "words.txt"
    if words.exists():
        vocab = {ln.split()[0] for ln in words.read_text(encoding="utf-8").splitlines() if ln.strip()}
        known = [v for v in wakeword.VARIANTS if all(t in vocab for t in v.split())]
        print(f"Variants in model vocabulary: {known or 'NONE - set wake_variants to known words'}")

    print(f"{len(pos)} positive / {len(neg)} negative clips")
    for s in (0.3, 0.5, 0.7, 0.9):
        det = wakeword.Detector(model, wakeword.VARIANTS, s)
        missed = [p.name for p in pos if not fires(det, p)]
        false = [n.name for n in neg if fires(det, n)]
        print(f"sensitivity {s}: missed {len(missed)}/{len(pos)}  false alarms {len(false)}/{len(neg)}"
              + (f"  missed={missed[:5]}" if missed else "") + (f"  false={false[:5]}" if false else ""))


if __name__ == "__main__":
    main()
