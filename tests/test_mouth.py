import math
import struct
import unittest

import ui


def tone(hz: float, secs: float = 0.2, amp: float = 8000, rate: int = 24000) -> bytes:
    return struct.pack(f"<{int(rate * secs)}h", *(int(amp * math.sin(2 * math.pi * hz * i / rate)) for i in range(int(rate * secs))))


class MouthShapeTests(unittest.TestCase):
    def rows(self, pcm):
        out, _ = ui.mouth_shapes(pcm, 24000)
        return [out[i:i + 4] for i in range(0, len(out), 4)]

    def test_silence_closes_the_mouth(self):
        for level, jaw, rnd, wide in self.rows(bytes(9600)):
            self.assertLess(jaw, 0.05)

    def test_low_vowel_rounds_and_high_hiss_spreads(self):
        o = self.rows(tone(300))[-1]           # all energy under 900 Hz: like o/u
        s = self.rows(tone(6000))[-1]          # all above 3.5 kHz: like s
        self.assertGreater(o[2], 0.5)          # round
        self.assertGreater(s[3], o[3])         # wide
        self.assertLess(s[1], o[1])            # the jaw stays nearly shut on s


if __name__ == "__main__":
    unittest.main()
