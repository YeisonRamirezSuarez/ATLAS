"""Wake word gate logic, without vosk or a microphone. Run: python -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wakeword


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def detector(script):
    """Detector stand-in: returns the scripted results chunk by chunk."""
    it = iter(script)
    return lambda pcm: next(it)


class WakeGateTests(unittest.TestCase):
    def test_nothing_leaves_while_asleep(self):
        gate = wakeword.WakeGate(detector([None, None, False]), clock=FakeClock())
        self.assertEqual([gate.feed(b"a"), gate.feed(b"b"), gate.feed(b"c")], [[], [], []])
        self.assertFalse(gate.awake())
        self.assertEqual(len(gate.utt), 0)   # finished utterance is dropped

    def test_wake_flushes_the_whole_utterance_then_passes_through(self):
        clock = FakeClock()
        gate = wakeword.WakeGate(detector([None, None, True]), window=10, clock=clock)
        gate.feed(b"jar"); gate.feed(b"vis abre")
        self.assertEqual(gate.feed(b"chrome"), [b"jar", b"vis abre", b"chrome"])
        self.assertEqual(gate.feed(b"next"), [b"next"])
        clock.t += 11
        self.assertFalse(gate.awake())

    def test_keep_awake_extends_window(self):
        clock = FakeClock()
        gate = wakeword.WakeGate(detector([]), window=10, clock=clock)
        gate.keep_awake()
        clock.t += 9
        gate.keep_awake()
        clock.t += 9
        self.assertTrue(gate.awake())


class DetectorMatchTests(unittest.TestCase):
    def det(self, sensitivity):
        d = wakeword.Detector.__new__(wakeword.Detector)   # no vosk model needed for match()
        d.variants = [["jarvis"], ["har", "bis"]]
        d.min_conf = 1.0 - sensitivity
        return d

    def test_match_needs_confidence(self):
        words = [{"word": "[unk]", "conf": 1.0}, {"word": "jarvis", "conf": 0.6}]
        self.assertTrue(self.det(0.5).match(words))    # needs >= 0.5
        self.assertFalse(self.det(0.2).match(words))   # needs >= 0.8

    def test_multiword_variant(self):
        d = self.det(0.5)
        self.assertTrue(d.match([{"word": "har", "conf": 0.9}, {"word": "bis", "conf": 0.7}]))
        self.assertFalse(d.match([{"word": "bis", "conf": 0.9}, {"word": "har", "conf": 0.9}]))


class MakeGateTests(unittest.TestCase):
    def test_off(self):
        self.assertIsNone(wakeword.make_gate({"wake_word": False}, log=lambda m: None))

    def test_missing_model_falls_back_to_always_listening(self):
        logs = []
        self.assertIsNone(wakeword.make_gate({"wake_model": "no/such/model"}, log=logs.append))
        self.assertIn("unavailable", logs[0])


if __name__ == "__main__":
    unittest.main()
