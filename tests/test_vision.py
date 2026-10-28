"""Offline checks for actions/screen_processor.py: no network, screen, camera or audio.

Run:  python -m unittest discover tests
"""
import asyncio
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Logs use emoji; a cp1252 console (Windows, piped output) would raise UnicodeEncodeError mid-action.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

try:
    import PIL.Image
    from actions import screen_processor as sp
except (ImportError, OSError) as e:  # CI without the audio/vision stack
    raise unittest.SkipTest(f"vision dependencies missing: {e}")

FAILURE = re.compile(r"^\W*(could not|couldn't|cannot|can't|failed|failure|error|unable|no se pudo|not found)", re.I)


class _FakeSession:
    """Answers each sent request with one turn, like the real Live API."""

    def __init__(self):
        self.sent = asyncio.Queue()

    async def send_client_content(self, turns, turn_complete):
        await self.sent.put(turns["parts"][-1]["text"])

    async def receive(self):
        # Like google-genai: one call yields a single turn, then stops.
        question = await self.sent.get()
        content = SimpleNamespace(
            output_transcription=SimpleNamespace(text=f"answer to {question}"),
            model_turn=None, turn_complete=True)
        yield SimpleNamespace(data=b"\0\0", server_content=content)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class ScreenProcessTests(unittest.TestCase):
    def test_missing_key_reports_could_not(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "api_keys.json"
            cfg.write_text(json.dumps({"gemini_api_key": ""}), encoding="utf-8")
            with mock.patch.object(sp, "API_CONFIG_PATH", cfg), \
                 mock.patch.object(sp, "_capture_screenshot") as cap:
                result = sp.screen_process({"text": "what is this?"})
        self.assertEqual(result, "Could not use vision: gemini_api_key is empty in api_keys.json")
        cap.assert_not_called()

    def test_capture_failure_is_reported(self):
        with mock.patch.object(sp, "_get_api_key", return_value="k"), \
             mock.patch.object(sp, "_capture_screenshot", side_effect=OSError("no display")):
            result = sp.screen_process({"text": "what is this?"})
        self.assertRegex(result, FAILURE)
        self.assertIn("no display", result)

    def test_camera_released_when_read_fails(self):
        cam = mock.MagicMock()
        cam.isOpened.return_value = True
        cam.read.side_effect = RuntimeError("device lost")
        with mock.patch.object(sp, "_get_camera_index", return_value=0), \
             mock.patch.object(sp.cv2, "VideoCapture", return_value=cam):
            with self.assertRaises(RuntimeError):
                sp._capture_camera()
        cam.release.assert_called_once()

    def test_analyze_timeout_is_reported(self):
        with mock.patch.object(sp, "_get_api_key", return_value="k"), \
             mock.patch.object(sp, "_capture_screenshot", return_value=[("main screen", b"jpg")]), \
             mock.patch.object(sp, "_ensure_started"), \
             mock.patch.object(sp._live, "analyze", side_effect=TimeoutError):
            result = sp.screen_process({"text": "what is this?"})
        self.assertRegex(result, FAILURE)

    def test_answer_text_or_audio_only(self):
        with mock.patch.object(sp, "_get_api_key", return_value="k"), \
             mock.patch.object(sp, "_capture_screenshot", return_value=[("main screen", b"jpg")]), \
             mock.patch.object(sp, "_ensure_started"), \
             mock.patch.object(sp._live, "analyze", side_effect=["A red window.", ""]):
            self.assertEqual(sp.screen_process({"text": "q"}), "Vision answer: A red window.")
            self.assertNotRegex(sp.screen_process({"text": "q"}), FAILURE)

    def test_all_monitors_labeled_left_to_right(self):
        # The user's layout: DISPLAY3 left, DISPLAY1 primary at 0,0, DISPLAY2 right.
        monitors = [
            {"left": -1600, "top": 0, "width": 3520 + 1600, "height": 1206},   # virtual desktop
            {"left": 0, "top": 0, "width": 1920, "height": 1200},
            {"left": 1920, "top": 6, "width": 1920, "height": 1080},
            {"left": -1600, "top": 6, "width": 1600, "height": 900},
        ]
        sct = mock.MagicMock()
        sct.__enter__.return_value = sct
        sct.monitors = monitors
        sct.grab.side_effect = lambda m: SimpleNamespace(rgb=b"", size=(m["width"], m["height"]))
        with mock.patch.object(sp.mss, "mss", return_value=sct),              mock.patch.object(sp.mss.tools, "to_png", return_value=b"png"),              mock.patch.object(sp, "_to_jpeg", side_effect=lambda b: b"jpg"):
            shots = sp._capture_screenshot()
        self.assertEqual([label for label, _ in shots], [
            "left screen (1600x900)", "center screen (1920x1200, primary)", "right screen (1920x1080)"])
        self.assertEqual(sct.grab.call_count, 3)   # each monitor, not the virtual desktop

    def test_every_screen_is_sent_with_its_label(self):
        shots = [("left screen (1600x900)", b"a"), ("center screen (1920x1200, primary)", b"b"),
                 ("right screen (1920x1080)", b"c")]
        with mock.patch.object(sp, "_get_api_key", return_value="k"),              mock.patch.object(sp, "_capture_screenshot", return_value=shots),              mock.patch.object(sp, "_ensure_started"),              mock.patch.object(sp._live, "analyze", return_value="Chrome is on the right screen.") as an:
            result = sp.screen_process({"text": "puedes ver el navegador de Chrome"})
        self.assertEqual(result, "Vision answer: Chrome is on the right screen.")
        images, _, question = an.call_args.args
        self.assertEqual(images, shots)
        self.assertIn("right screen", question)
        self.assertIn("ALL", question)
        self.assertIn("puedes ver el navegador de Chrome", question)

    def test_analyze_without_session_fails_fast(self):
        with self.assertRaises(ConnectionError):
            sp._LiveSession().analyze(b"jpg", "image/jpeg", "q", timeout=1)

    def test_jpeg_downscale_caps_width(self):
        buf = io.BytesIO()
        PIL.Image.new("RGB", (3840, 2160)).save(buf, format="PNG")
        out = PIL.Image.open(io.BytesIO(sp._to_jpeg(buf.getvalue())))
        self.assertEqual(out.format, "JPEG")
        self.assertLessEqual(out.width, sp.IMG_MAX_W)


class LiveSessionTests(unittest.TestCase):
    def test_every_request_gets_its_answer(self):
        client = mock.MagicMock()
        client.aio.live.connect.return_value = _FakeSession()
        with mock.patch.object(sp, "_get_api_key", return_value="k"), \
             mock.patch.object(sp.genai, "Client", return_value=client):
            live = sp._LiveSession()
            live.start()
            # Before the fix the receive loop ended after the first turn.
            self.assertEqual(live.analyze(b"jpg", "image/jpeg", "one", timeout=5), "answer to one")
            self.assertEqual(live.analyze(b"jpg", "image/jpeg", "two", timeout=5), "answer to two")
            shots = [("left screen", b"a"), ("right screen", b"b")]
            self.assertEqual(live.analyze(shots, "image/jpeg", "three", timeout=5), "answer to three")


if __name__ == "__main__":
    unittest.main()
