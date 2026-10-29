"""
Who is talking: Vosk speaker vectors (vosk-model-spk-0.4) matched against the voices enrolled in
Settings > VOCES. It never slows listening: the mic callback only drops a copy of each chunk into a
small queue, a thread does the rest, and when that thread falls behind chunks are lost, not waited for.
Only ~2.5 s of each utterance is analyzed; with no voices enrolled nothing is.

Voices live in memory/voices.json (vectors) and memory/voices/ (the recordings, to listen back), on this PC
only: backed up, never synced or shipped.
A voice match tells ATLAS whom to address. It is not authentication and grants nothing.

api_keys.json (optional):
  "speaker_id":        true   # false = off
  "speaker_threshold": 0.55   # similarity (0..1) a voice must reach to be named; lower names more easily
"""
import contextlib
import json
import os
import queue
import threading
import uuid
import wave
from pathlib import Path

import numpy as np

from wakeword import BASE_DIR, END_SILENCE, RATE, model_path, vosk_model

SPK_MODEL  = BASE_DIR / "models" / "vosk-model-spk-0.4"
VOICES     = BASE_DIR / "memory" / "voices.json"   # its recordings: VOICES.parent / "voices"
THRESHOLD  = 0.55     # synthetic voices: same speaker 0.62-0.82, different at most 0.51
DECIDE     = 40       # voiced 1024-sample chunks (~2.5 s) after which the utterance is decided
MIN_VOICED = 16       # ~1 s: a shorter utterance says too little about the voice
MAX_PCM    = 6 * RATE * 2   # one-shot checks (cascade engine) look at the first 6 s
GOOD_SPEECH = 10.0          # seconds of speech in a sample for full quality
SAME_LOW, SAME_HIGH = 0.45, 0.75   # similarity of a sample's two halves: same voice ~0.7+, two voices ~0.25
recording  = threading.Event()   # Settings is recording a voice sample: the engine ignores the mic

_spk = None


def available() -> bool:
    return SPK_MODEL.is_dir()


class Embedder:
    """Speaker vector of the audio fed since the last take(). Vosk decodes it (the vector is pooled
    over the frames aligned to speech, so a grammar of only [unk] gives none) and returns one
    vector per result; they are averaged by frame count."""

    def __init__(self, cfg: dict | None = None):
        global _spk
        from vosk import KaldiRecognizer, SpkModel
        _spk = _spk or SpkModel(str(SPK_MODEL))
        self.rec = KaldiRecognizer(vosk_model(str(model_path(cfg or {}))), RATE)
        self.rec.SetSpkModel(_spk)
        self.sum, self.frames = 0.0, 0

    def _add(self, result: str):
        r = json.loads(result)
        if r.get("spk"):
            self.sum = self.sum + np.asarray(r["spk"]) * r["spk_frames"]
            self.frames += r["spk_frames"]

    def accept(self, pcm: bytes):
        if self.rec.AcceptWaveform(pcm):
            self._add(self.rec.Result())

    def sums(self):
        """(sum of vectors, frames of speech: 100 per second) of what was fed. Starts over."""
        self._add(self.rec.FinalResult())
        out, self.sum, self.frames = (self.sum, self.frames), 0.0, 0
        return out

    def take(self):
        """Unit vector of what was fed, or None (no speech in it). Starts over."""
        s, frames = self.sums()
        return _unit(s) if frames else None


def _unit(v):
    v = np.asarray(v, dtype=float)
    return v / (np.linalg.norm(v) or 1.0)


def embed(pcm: bytes, cfg: dict | None = None):
    e = Embedder(cfg)
    for i in range(0, len(pcm), 2048):
        e.accept(pcm[i:i + 2048])
    return e.take()


def assess(pcm: bytes, cfg: dict | None = None) -> dict:
    """A sample recorded in Settings: vec (None = no speech heard), seconds of speech, loudness (rms),
    share of clipped samples and quality 0..1: enough speech, and its two halves sound like one voice
    (a noisy room, a second person or a bad mic pull them apart)."""
    e, halves = Embedder(cfg), []
    mid = len(pcm) // 4 * 2
    for part in (pcm[:mid], pcm[mid:]):
        for i in range(0, len(part), 2048):
            e.accept(part[i:i + 2048])
        halves.append(e.sums())
    (s1, f1), (s2, f2) = halves
    a = np.frombuffer(pcm, np.int16).astype(np.float32)
    speech = (f1 + f2) / 100
    same = float(np.dot(_unit(s1), _unit(s2))) if f1 and f2 else 0.0
    return {"vec": _unit(s1 + s2) if f1 + f2 else None, "speech": speech,
            "rms": float(np.sqrt(np.mean(a ** 2))) if a.size else 0.0,
            "clipped": float(np.mean(np.abs(a) >= 32000)) if a.size else 0.0,
            "quality": round(min(speech / GOOD_SPEECH, 1.0)
                             * min(max((same - SAME_LOW) / (SAME_HIGH - SAME_LOW), 0.0), 1.0), 2)}


# ── enrolled voices ──────────────────────────────────────────────────────────

def load(path: Path = VOICES) -> dict[str, list]:
    """{name: [{"vec": [...], "quality": 0..1, "audio": "file.wav"}, ...]}; {} when there are none or
    the file is unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(voices: dict, path: Path):
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(voices), encoding="utf-8")
    os.replace(tmp, path)


def audio(sample: dict, path: Path = VOICES) -> Path | None:
    """The recording of a sample, if it was kept."""
    f = sample.get("audio") and path.parent / "voices" / sample["audio"]
    return f if f and f.is_file() else None


def add(name: str, vec, path: Path = VOICES, quality: float | None = None, pcm: bytes | None = None) -> int:
    """Adds a sample to a voice (a new name starts a new voice), with its recording (16 kHz int16 mono)
    when given. Returns the voice's sample count."""
    sample = {"vec": [round(float(x), 5) for x in vec], "quality": quality}
    if pcm:
        sample["audio"] = f"{uuid.uuid4().hex[:12]}.wav"
        (path.parent / "voices").mkdir(parents=True, exist_ok=True)
        with wave.open(str(path.parent / "voices" / sample["audio"]), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm)
    voices = load(path)
    voices.setdefault(name, []).append(sample)
    _save(voices, path)
    return len(voices[name])


def remove(name: str, path: Path = VOICES, index: int | None = None):
    """Deletes a voice, or only its sample number index (the voice goes with its last sample)."""
    voices = load(path)
    samples = voices.get(name, [])
    gone = samples if index is None else samples[index:index + 1]
    if not gone:
        return
    for s in gone:
        if f := audio(s, path):
            f.unlink(missing_ok=True)
    if index is None or len(samples) == 1:
        del voices[name]
    else:
        del samples[index]
    _save(voices, path)


def profiles(voices: dict) -> dict:
    """{name: unit mean of its samples}: more samples, steadier match."""
    return {n: _unit(np.mean([_unit(s["vec"]) for s in samples], axis=0)) for n, samples in voices.items() if samples}


def identify(vec, profs: dict, threshold: float = THRESHOLD):
    """(name or None, best similarity). None = not an enrolled voice."""
    if vec is None or not profs:
        return None, 0.0
    score, name = max((float(np.dot(vec, p)), n) for n, p in profs.items())
    return (name if score >= threshold else None), score


def who(pcm: bytes, cfg: dict):
    """One-shot check of a whole utterance (cascade engine): (name or None, score), or None when
    speaker identification is off, unavailable or has no voices."""
    if cfg.get("speaker_id", True) is False or not available():
        return None
    profs = profiles(load())
    if not profs:
        return None
    vec = embed(pcm[:MAX_PCM], cfg)
    return None if vec is None else identify(vec, profs, float(cfg.get("speaker_threshold", THRESHOLD)))


# ── live tracking ────────────────────────────────────────────────────────────

class Tracker:
    """Follows who is talking in the mic stream and calls on_change(name, score, talking) when that
    changes. name None = a voice that is not enrolled; talking = decided while the user still speaks.

    With out ("speaker_only"), every mic chunk comes through here and out(chunk) gets only what enrolled
    voices say: each utterance is held until its voice is decided. That adds no wait: the brain answers
    after the user's pause anyway, and the decision comes with that pause (or ~2.5 s in, mid-sentence).
    An utterance too short to tell ("sí", "para") goes with the last voice decided."""

    def __init__(self, on_change, vad, cfg: dict | None = None, path: Path = VOICES, embedder=None, out=None):
        cfg = cfg or {}
        self.on_change, self.vad, self.path, self.out = on_change, vad, path, out
        self.threshold = float(cfg.get("speaker_threshold", THRESHOLD))
        self.new_embedder = embedder or (lambda: Embedder(cfg))
        self.emb = None
        self.q = queue.Queue(maxsize=256)  # ~16 s: a whole wake utterance arrives at once
        self.last = ...                    # nobody named yet
        self.profs, self.mtime = {}, None
        self.spoke = self.quiet = 0
        self.done = False                  # this utterance is decided: the rest is not analyzed
        self.allow, self.held = True, []   # out only: pass this utterance? (None = not decided yet)

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="speaker").start()
        return self

    def stop(self):
        with contextlib.suppress(queue.Full):
            self.q.put(None, timeout=1)

    def feed(self, pcm: bytes):
        """Called from the mic callback: never blocks. The VAD runs in the tracker's thread."""
        try:
            self.q.put_nowait(pcm)
        except queue.Full:
            pass

    def _run(self):
        while (pcm := self.q.get()) is not None:
            try:
                self.step(pcm, self.vad(pcm))
            except Exception as e:   # a broken model or file must not take the engine down
                print(f"[ATLAS] ⚠️ speaker id: {e}")
                self._release(True)  # fails open: ATLAS deaf would be worse
                self.emb, self.spoke, self.quiet, self.done = None, 0, 0, False

    def _reload(self):
        """Voices enrolled or deleted in Settings apply from the next utterance."""
        try:
            m = self.path.stat().st_mtime
        except OSError:
            m = None
        if m != self.mtime:
            self.mtime, self.profs = m, profiles(load(self.path))

    def _pass(self, pcm: bytes):
        if self.out and self.allow:
            self.out(pcm)
        elif self.out and self.allow is None:
            self.held.append(pcm)

    def _release(self, ok: bool):
        self.allow, held, self.held = ok, self.held, []
        if ok and self.out:
            for p in held:
                self.out(p)

    def step(self, pcm: bytes, voiced: bool):
        if not self.spoke:
            if not voiced:
                return self.out and self.out(pcm)   # silence between utterances passes
            self._reload()
            self.allow = None if self.out and self.profs else True   # no voices enrolled: everyone
        self.spoke += voiced
        self.quiet = 0 if voiced else self.quiet + 1
        self._pass(pcm)
        if self.profs and not self.done:
            self.emb = self.emb or self.new_embedder()
            self.emb.accept(pcm)
            if self.spoke >= DECIDE:
                self._decide(talking=True)
        if self.quiet >= END_SILENCE:   # the utterance ended
            if self.profs and not self.done and self.spoke >= MIN_VOICED:
                self._decide(talking=False)
            elif self.emb and not self.done:
                self.emb.take()         # too short to analyze: drop it
            if self.allow is None:      # not decided: it goes with the last voice decided
                self._release(isinstance(self.last, str))
            self.spoke = self.quiet = 0
            self.done = False

    def _decide(self, talking: bool):
        self.done = True
        vec = self.emb.take()
        if vec is None:
            return
        name, score = identify(vec, self.profs, self.threshold)
        if self.allow is None:
            self._release(name is not None)
        if name != self.last:
            self.last = name
            self.on_change(name, score, talking)


def make_tracker(on_change, vad, cfg: dict, log=print, out=None):
    """Tracker, or None when off or the speaker model is missing. Runs even with no voices enrolled
    (it then only reads a queue), so voices enrolled in Settings work without a restart.
    out: "speaker_only" is on, see Tracker."""
    if cfg.get("speaker_id", True) is False:
        if out:
            log("SIS: 'Solo voces registradas' necesita el reconocimiento de voces (speaker_id); respondo a todos.")
        return None
    if not available():
        log("SIS: Reconocimiento de voces no disponible (falta models/vosk-model-spk-0.4)."
            + (" Respondo a todos." if out else ""))
        return None
    if out and not load():
        log("SIS: 'Solo voces registradas' está activo pero no hay voces registradas; respondo a todos.")
    return Tracker(on_change, vad, cfg, out=out).start()


if __name__ == "__main__":   # python speaker.py file.pcm  -> who is it? (16 kHz int16 mono)
    import sys
    print(who(Path(sys.argv[1]).read_bytes(), {}))
