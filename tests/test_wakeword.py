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

    def test_only_audio_from_the_wake_word_on_is_passed(self):
        chunk = b"\x00" * (wakeword.RATE // 2 * 2)   # 0.5 s of 16-bit audio
        gate = wakeword.WakeGate(detector([None, None, None, 1.6]), clock=FakeClock())
        out = [gate.feed(c) for c in (b"A" + chunk[1:], b"B" + chunk[1:], b"C" + chunk[1:], b"D" + chunk[1:])]
        # "abre Chrome" (0-1 s) was said before "Jarvis" (1.6 s): only its pre-roll on goes out
        self.assertEqual([c[:1] for c in out[-1]], [b"C", b"D"])

    def test_decides_when_the_user_stops_even_if_vosk_never_hears_the_end(self):
        finished = []
        gate = wakeword.WakeGate(lambda pcm: None, clock=FakeClock(), finish=lambda: finished.append(1) or True)
        for _ in range(5):
            self.assertEqual(gate.feed(b"speech", voiced=True), [])
        for _ in range(wakeword.END_SILENCE - 1):
            self.assertEqual(gate.feed(b"quiet", voiced=False), [])
        self.assertEqual(finished, [])   # a short pause is not the end
        out = gate.feed(b"quiet", voiced=False)
        self.assertEqual(finished, [1])
        self.assertEqual(out[:5], [b"speech"] * 5)   # "Jarvis, abre Chrome" goes out now, not at the next wake word
        self.assertTrue(gate.awake())

    def test_window_runs_from_the_users_last_word(self):
        clock = FakeClock()
        gate = wakeword.WakeGate(detector([None]), window=10, clock=clock)
        gate.keep_awake()
        clock.t += 9
        gate.feed(b"talking", voiced=True)   # still speaking when the window was about to close
        clock.t += 9
        self.assertEqual(gate.feed(b"rest of it"), [b"rest of it"])
        clock.t += 2
        self.assertEqual(gate.feed(b"x"), [])   # silent for the whole window: standby

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
