import tempfile
import unittest
from pathlib import Path

import numpy as np

import speaker
from speaker import DECIDE, END_SILENCE, MIN_VOICED, Tracker

VEC = {b"A": [1.0, 0.0], b"B": [0.0, 1.0], b"X": [-0.6, -0.8]}   # X matches neither A nor B


class FakeEmbedder:
    """Speaker vector = VEC of the first byte of the chunks fed since take()."""
    def __init__(self):
        self.who = None

    def accept(self, pcm):
        self.who = self.who or pcm[:1]

    def take(self):
        v, self.who = self.who and np.array(VEC[self.who]), None
        return v


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "voices.json"

    def tearDown(self):
        self.dir.cleanup()

    def tracker(self, out=None):
        self.changes, self.out = [], []
        return Tracker(lambda *a: self.changes.append(a), None, {}, self.path, FakeEmbedder,
                       out=self.out.append if out else None)

    @staticmethod
    def say(t, who, voiced, quiet=END_SILENCE):
        for i in range(voiced):
            t.step(who + bytes([i % 256]), True)
        for _ in range(quiet):
            t.step(b"_", False)


class StoreTest(Base):
    def test_add_remove_identify(self):
        self.assertEqual(speaker.load(self.path), {})
        self.assertEqual(speaker.add("Ana", [2, 0], self.path), 1)
        self.assertEqual(speaker.add("Ana", [1, 0.1], self.path), 2)
        speaker.add("Beto", [0, 1], self.path)
        profs = speaker.profiles(speaker.load(self.path))
        self.assertEqual(speaker.identify(np.array([1.0, 0.0]), profs)[0], "Ana")
        self.assertEqual(speaker.identify(np.array([-0.6, -0.8]), profs)[0], None)
        speaker.remove("Ana", self.path)
        self.assertEqual(list(speaker.load(self.path)), ["Beto"])

    def test_recordings_go_with_their_sample(self):
        speaker.add("Ana", [1, 0], self.path, 0.8, b"\x01\x00" * 100)
        speaker.add("Ana", [1, 0], self.path, 0.5, b"\x02\x00" * 100)
        first, second = (speaker.audio(s, self.path) for s in speaker.load(self.path)["Ana"])
        self.assertTrue(first.is_file() and second.is_file())
        speaker.remove("Ana", self.path, index=0)
        self.assertFalse(first.exists())
        self.assertEqual([s["quality"] for s in speaker.load(self.path)["Ana"]], [0.5])
        speaker.remove("Ana", self.path, index=0)   # its last sample: the voice goes too
        self.assertEqual(speaker.load(self.path), {})
        self.assertFalse(second.exists())

    @unittest.skipUnless(speaker.available(), "speaker model not installed")
    def test_assess_silence(self):
        a = speaker.assess(bytes(16000 * 2 * 3))
        self.assertIsNone(a["vec"])
        self.assertEqual(a["quality"], 0.0)


class TrackerTest(Base):
    def setUp(self):
        super().setUp()
        speaker.add("Ana", VEC[b"A"], self.path)
        speaker.add("Beto", VEC[b"B"], self.path)

    def test_names_on_change_only(self):
        t = self.tracker()
        self.say(t, b"A", DECIDE + 5)
        self.say(t, b"A", MIN_VOICED)
        self.say(t, b"B", MIN_VOICED)
        self.say(t, b"X", MIN_VOICED)
        self.assertEqual([(n, talking) for n, _, talking in self.changes],
                         [("Ana", True), ("Beto", False), (None, False)])

    def test_short_utterance_not_analyzed(self):
        t = self.tracker()
        self.say(t, b"A", MIN_VOICED - 1)
        self.assertEqual(self.changes, [])

    def test_hold_passes_enrolled_drops_unknown(self):
        t = self.tracker(out=True)
        self.say(t, b"A", MIN_VOICED)
        self.assertEqual(sum(c[:1] == b"A" for c in self.out), MIN_VOICED)
        self.assertEqual(self.out[-END_SILENCE:], [b"_"] * END_SILENCE)   # held, then released in order
        self.out.clear()
        self.say(t, b"X", DECIDE + 10)
        self.assertEqual(self.out, [])            # its trailing pause too
        t.step(b"_", False)
        self.assertEqual(self.out, [b"_"])        # silence between utterances passes

    def test_hold_releases_mid_sentence(self):
        t = self.tracker(out=True)
        self.say(t, b"B", DECIDE, quiet=0)
        self.assertEqual(len(self.out), DECIDE)   # decided at DECIDE: no need to wait for the pause
        t.step(b"B!", True)
        self.assertEqual(self.out[-1], b"B!")

    def test_hold_short_utterance_follows_last_voice(self):
        t = self.tracker(out=True)
        self.say(t, b"A", 3)
        self.assertEqual(self.out, [])            # nobody decided yet: dropped
        self.say(t, b"A", MIN_VOICED)
        self.out.clear()
        self.say(t, b"A", 3)
        self.assertEqual(len(self.out), 3 + END_SILENCE)
        self.say(t, b"X", MIN_VOICED)
        self.out.clear()
        self.say(t, b"A", 3)
        self.assertEqual(self.out, [])

    def test_hold_without_voices_passes_everything(self):
        self.path.unlink()
        t = self.tracker(out=True)
        self.say(t, b"X", MIN_VOICED)
        self.assertEqual(len(self.out), MIN_VOICED + END_SILENCE)

    def test_run_stops_and_fails_open(self):
        t = self.tracker(out=True)
        t.vad = lambda pcm: 1 / 0
        t.allow, t.held = None, [b"A1"]
        t.feed(b"A2")
        t.stop()
        t._run()   # returns at the stop sentinel
        self.assertEqual(self.out, [b"A1"])


if __name__ == "__main__":
    unittest.main()
