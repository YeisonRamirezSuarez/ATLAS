"""
Local "Atlas" wake word: Vosk with a closed Spanish grammar. While asleep, no microphone
audio leaves the PC; once the wake word is heard the whole utterance that contained it
("Atlas, abre Chrome") is passed on and ATLAS keeps listening for `wake_window` seconds.

api_keys.json (all optional):
  "wake_word":        "auto"   # auto = on when vosk and the model are installed; true; false = always listening
  "wake_sensitivity": 0.5      # 0..1, higher fires more easily (more false positives, fewer misses)
  "wake_window":      12       # seconds of open mic after the user's last word or after ATLAS answers
  "wake_model":       "models/vosk-model-small-es-0.42"
  "wake_variants":    ["atlas", "atla", "adlas", ...]
  "wake_end_silence": 2.0      # seconds of quiet that end what the user said; raise it for slow speakers
"""
import json
import time
from collections import deque
from pathlib import Path

BASE_DIR      = Path(__file__).resolve().parent
DEFAULT_MODEL = "models/vosk-model-small-es-0.42"
RATE          = 16000
# How Spanish speakers say and Vosk may spell "Atlas". Words the model does not know are
# dropped by Vosk; tools/wake_eval.py lists which ones the installed model accepts.
VARIANTS      = ["atlas", "atla", "adlas", "hatlas", "atlax"]
MAX_UTTERANCE = 15 * RATE // 1024          # ~15 s of 1024-sample mic chunks
PRE_ROLL      = 0.3                         # seconds kept before the wake word, for its onset
END_SILENCE   = 31                          # quiet chunks (~2 s) that end what the user said; wake_end_silence


class Detector:
    """Vosk recognizer limited to the wake variants. feed() returns None mid-utterance, then,
    when Vosk closes it, False or the second (since the first chunk fed) the wake word began."""

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
        start = self.find(json.loads(self.rec.Result()).get("result", []))
        return False if start is None else start

    def find(self, words: list[dict]) -> float | None:
        """Start time of the first variant that appears as consecutive words, each at least
        min_conf sure; None when there is none."""
        seq = [(w.get("word", ""), float(w.get("conf", 0)), float(w.get("start", 0))) for w in words]
        for i in range(len(seq)):
            for v in self.variants:
                hit = seq[i:i + len(v)]
                if [w for w, _, _ in hit] == v and min(c for _, c, _ in hit) >= self.min_conf:
                    return hit[0][2]
        return None

    def finish(self):
        """Close the utterance now and decide: False or the wake word's start time."""
        start = self.find(json.loads(self.rec.FinalResult()).get("result", []))
        return False if start is None else start

    def match(self, words: list[dict]) -> bool:
        return self.find(words) is not None


class WakeGate:
    """Decides which mic chunks may leave the PC."""

    def __init__(self, detect, window: float = 12.0, clock=time.monotonic, finish=None):
        self.detect, self.window, self.clock, self.finish = detect, float(window), clock, finish
        self.until = 0.0
        self.spoke = self.quiet = 0                # voiced / trailing quiet chunks of this utterance
        self.utt   = deque(maxlen=MAX_UTTERANCE)   # (sample offset, chunk) of the utterance being checked
        self.pos   = 0                             # samples given to the detector so far (its clock)

    def awake(self) -> bool:
        return self.clock() < self.until

    def keep_awake(self) -> None:
        self.until = self.clock() + self.window

    def feed(self, pcm: bytes, voiced: bool = False) -> list[bytes]:
        """Chunks to pass on now: [] while asleep, the wake utterance when it fires. voiced = the
        user is talking: the window then runs from their last word, so it never closes mid-sentence
        (half a command stuck downstream used to come out glued to the next wake word)."""
        if self.awake():
            if voiced:
                self.keep_awake()
            return [pcm]
        return self.listen(pcm, voiced)

    def listen(self, pcm: bytes, voiced: bool | None = None) -> list[bytes]:
        """Wake word only, awake or not: while ATLAS talks this is how the user interrupts it.
        [] until an utterance with the wake word closes, then that utterance from the wake word
        on: what was said before it is not a request ("...abre Chrome. Atlas" must not open it)."""
        self.utt.append((self.pos, pcm))
        self.pos += len(pcm) // 2
        fired = self.detect(pcm)
        if voiced is not None:
            self.spoke += voiced
            self.quiet = 0 if voiced else self.quiet + 1
        # Vosk decides only when it hears the utterance end; steady room noise (fan, PC, echo) can
        # hide that end for up to ~20 s, so "Atlas, abre Chrome" sat unanswered and came out with
        # the next "Atlas". Once the user has been quiet ~0.8 s, it decides on what it has.
        if fired is None and self.finish and self.spoke >= 4 and self.quiet >= END_SILENCE:
            fired = self.finish()
        if fired is None:
            return []
        utt = list(self.utt)
        self.utt.clear()
        self.spoke = self.quiet = 0
        if fired is False:
            return []
        self.keep_awake()
        if fired is True or fired > self.pos / RATE:   # no time, or a clock out of step: keep it all
            return [p for _, p in utt]
        start = (fired - PRE_ROLL) * RATE
        return [p for s, p in utt if s + len(p) // 2 > start]


def make_gate(cfg: dict, log=print):
    """WakeGate from api_keys.json, or None (always listening) with the reason logged."""
    mode = cfg.get("wake_word", "auto")
    if mode is False or str(mode).lower() in ("false", "off", "0"):
        log("SIS: Palabra de activación apagada (wake_word=false); siempre escuchando.")
        return None
    path = Path(cfg.get("wake_model") or DEFAULT_MODEL)
    path = path if path.is_absolute() else BASE_DIR / path
    global END_SILENCE
    try:   # seconds -> 1024-sample chunks at 16 kHz; read before the detector starts using it
        END_SILENCE = max(1, round(float(cfg.get("wake_end_silence", 2.0)) * RATE / 1024))
    except (TypeError, ValueError):
        pass
    try:
        if not path.is_dir():
            raise FileNotFoundError(f"Vosk model not found at {path}")
        det = Detector(path, cfg.get("wake_variants") or VARIANTS, cfg.get("wake_sensitivity", 0.5))
    except Exception as e:   # vosk missing, model missing or broken
        log(f"SIS: Palabra de activación no disponible ({e}); siempre escuchando.")
        return None
    log("SIS: Palabra de activación lista. Di 'Atlas'.")
    return WakeGate(det.feed, cfg.get("wake_window", 12), finish=det.finish)
