"""Offline checks for actions/image_vision.py: no network, the Claude call is mocked.

Run:  python -m unittest discover tests
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import PIL.Image
    from actions import image_vision as iv
except ImportError as e:
    raise unittest.SkipTest(f"vision dependencies missing: {e}")


class ImageVisionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        for i in range(11):
            PIL.Image.new("RGB", (3000, 1000), (i * 20, 0, 0)).save(self.dir / f"view{i:02d}.png")
        (self.dir / "notes.txt").write_text("not an image")

    def tearDown(self):
        self.tmp.cleanup()

    def test_expand_paths_glob_folder_and_dedupe(self):
        self.assertEqual(len(iv.expand_paths(str(self.dir / "*.png"))), 11)
        self.assertEqual(len(iv.expand_paths([self.dir, self.dir / "view00.png"])), 11)
        self.assertEqual(iv.expand_paths(f"{self.dir / 'view01.png'};{self.dir / 'notes.txt'}"),
                         [self.dir / "view01.png"])

    def test_batches_images_to_claude_view_by_view(self):
        sent = []

        def fake_ask(model, content):
            sent.append(content)
            names = [b["text"] for b in content if b["type"] == "text" and b["text"].startswith("Image")]
            return "\n".join(f"## {n}: ok" for n in names)

        with mock.patch.object(iv, "_vision_engine", return_value=("claude", "claude-opus-5-5")), \
             mock.patch.object(iv, "_ask_api", side_effect=fake_ask):
            out = iv.analyze_images([self.dir], "audit each view")
        self.assertEqual(len(sent), 2)                       # 8 + 3
        images = [b for c in sent for b in c if b["type"] == "image"]
        self.assertEqual(len(images), 11)
        self.assertTrue(all(b["source"]["type"] == "base64" for b in images))
        self.assertIn("Image 11 of 11: view10.png", out)
        self.assertEqual(sent[0][-1]["text"], "Request: audit each view")
        import base64, io
        img = PIL.Image.open(io.BytesIO(base64.b64decode(images[0]["source"]["data"])))
        self.assertLessEqual(max(img.size), iv.MAX_SIDE)    # shrunk before upload

    def test_pasted_image_is_viewed_from_memory(self):
        import io
        buf = io.BytesIO()
        PIL.Image.new("RGB", (40, 30), (0, 0, 255)).save(buf, "PNG")
        sent = []
        with mock.patch.dict(iv.PASTED, clear=True), \
             mock.patch.object(iv, "_vision_engine", return_value=("claude_sub", "claude-opus-5-5")), \
             mock.patch.object(iv, "_ask_subscription", side_effect=lambda m, c: sent.append(c) or "a blue square"), \
             mock.patch.object(iv.Path, "read_bytes", side_effect=AssertionError("read from disk")):
            name = iv.keep_pasted(buf.getvalue())
            out = iv.view_images({"paths": name, "question": "¿qué ves?"})
            self.assertEqual(out, "a blue square")
            self.assertEqual(sent[0][0]["text"], "Image 1 of 1: pasted-1.png")
            self.assertEqual(sent[0][1]["source"]["media_type"], "image/png")
            self.assertTrue(iv.view_images({"paths": "pasted-9.png"}).lower().startswith("could not"))

    def test_tool_reports_missing_images_as_failure(self):
        out = iv.view_images({"paths": str(self.dir / "nothing*.png")})
        self.assertTrue(out.lower().startswith("could not"))

    def test_long_report_is_saved(self):
        with mock.patch.object(iv, "analyze_images", return_value="x" * 5000), \
             mock.patch.object(iv, "REPORT_DIR", self.dir / "reports"):
            out = iv.view_images({"paths": str(self.dir), "question": "q"})
        self.assertIn("Full report", out)
        self.assertEqual(len(list((self.dir / "reports").glob("*.md"))), 1)

    def test_scripts_can_import_the_helper(self):
        from agent import executor
        env = executor._script_env()
        self.assertIn(str(executor.BASE_DIR), env["PYTHONPATH"])
        self.assertIn("view_images", executor._TOOLS)


if __name__ == "__main__":
    unittest.main()
