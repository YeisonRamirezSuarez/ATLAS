"""
Local "Jarvis" wake word: Vosk with a closed Spanish grammar. While asleep, no microphone
audio leaves the PC; once the wake word is heard the whole utterance that contained it
("Jarvis, abre Chrome") is passed on and JARVIS keeps listening for `wake_window` seconds.

api_keys.json (all optional):
  "wake_word":        "auto"   # auto = on when vosk and the model are installed; true; false = always listening
  "wake_sensitivity": 0.5      # 0..1, higher fires more easily (more false positives, fewer misses)
  "wake_window":      12       # seconds of open mic after the wake word or after JARVIS answers
  "wake_model":       "models/vosk-model-small-es-0.42"
  "wake_variants":    ["jarvis", "yarvis", "harbis", ...]
"""
import json
import time
from collections import deque
from pathlib import Path

BASE_DIR      = Path(__file__).resolve().parent
DEFAULT_MODEL = "models/vosk-model-small-es-0.42"
RATE          = 16000
# How Spanish speakers say and Vosk may spell "Jarvis". Words the model does not know are
# dropped by Vosk; tools/wake_eval.py lists which ones the installed model accepts.
VARIANTS      = ["jarvis", "yarvis", "harbis", "jarbis", "yarbis", "harvis", "llarvis"]
MAX_UTTERANCE = 15 * RATE // 1024          # ~15 s of 1024-sample mic chunks


class Detector:
    """Vosk recognizer limited to the wake variants. feed() returns None mid-utterance,
    then True/False when Vosk closes the utterance."""

    def __init__(self, model_path: Path, variants: list[str], sensitivity: float):
        from vosk import KaldiRecognizer, Model, SetLogLevel
        SetLogLevel(-1)
        self.variants = [v.lower().split() for v in variants]
        self.min_conf = 1.0 - min(max(float(sensitivity), 0.0), 1.0)
        grammar = json.dumps([" ".join(v) for v in self.variants] + ["[unk]"])
        self.rec = KaldiRecognizer(Model(str(model_path)), RATE, grammar)
        self.rec.SetWords(True)

    def feed(self, pcm: bytes):
        if not self.rec.AcceptWaveform(pcm):
            return None
        return self.match(json.loads(self.rec.Result()).get("result", []))

    def match(self, words: list[dict]) -> bool:
        """True if a variant appears as consecutive words, each at least min_conf sure."""
        seq = [(w.get("word", ""), float(w.get("conf", 0))) for w in words]
        for v in self.variants:
            for i in range(len(seq) - len(v) + 1):
                hit = seq[i:i + len(v)]
                if [w for w, _ in hit] == v and min(c for _, c in hit) >= self.min_conf:
                    return True
        return False


class WakeGate:
    """Decides which mic chunks may leave the PC."""

    def __init__(self, detect, window: float = 12.0, clock=time.monotonic):
        self.detect, self.window, self.clock = detect, float(window), clock
        self.until = 0.0
        self.utt   = deque(maxlen=MAX_UTTERANCE)   # current utterance, checked for the wake word

    def awake(self) -> bool:
        return self.clock() < self.until

    def keep_awake(self) -> None:
        self.until = self.clock() + self.window

    def feed(self, pcm: bytes) -> list[bytes]:
        """Chunks to pass on now: [] while asleep, the whole wake utterance when it fires."""
        if self.awake():
            return [pcm]
        self.utt.append(pcm)
        fired = self.detect(pcm)
        if fired is None:
            return []
        out = list(self.utt) if fired else []
        self.utt.clear()
        if fired:
            self.keep_awake()
        return out


def make_gate(cfg: dict, log=print):
    """WakeGate from api_keys.json, or None (always listening) with the reason logged."""
    mode = cfg.get("wake_word", "auto")
    if mode is False or str(mode).lower() in ("false", "off", "0"):
        log("SYS: Wake word off (wake_word=false); always listening.")
        return None
    path = Path(cfg.get("wake_model") or DEFAULT_MODEL)
    path = path if path.is_absolute() else BASE_DIR / path
    try:
        if not path.is_dir():
            raise FileNotFoundError(f"Vosk model not found at {path}")
        det = Detector(path, cfg.get("wake_variants") or VARIANTS, cfg.get("wake_sensitivity", 0.5))
    except Exception as e:   # vosk missing, model missing or broken
        log(f"SYS: Wake word unavailable ({e}); always listening.")
        return None
    log("SYS: Wake word on. Say 'Jarvis'.")
    return WakeGate(det.feed, cfg.get("wake_window", 12))
