"""Offline checks for actions/gemini_image.py: Gemini and FLUX are mocked, no network or GPU."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import gemini_image as gi


class GenerateImageTest(unittest.TestCase):
    def test_empty_prompt(self):
        self.assertTrue(gi.generate_image({"prompt": "  "}).startswith("Could not"))

    def test_gemini_first(self):
        with mock.patch.object(gi, "gemini_images", return_value=(["a.png"], "")) as g, \
             mock.patch.object(gi, "flux_images") as f:
            out = gi.generate_image({"prompt": "un elefante"})
        self.assertIn("Gemini made: a.png", out)
        g.assert_called_once_with("un elefante", [])
        f.assert_not_called()

    def test_falls_back_to_flux(self):
        with mock.patch.object(gi, "gemini_images", side_effect=RuntimeError("no Google session")), \
             mock.patch.object(gi, "flux_images", return_value=["b.png"]):
            out = gi.generate_image({"prompt": "un elefante"})
        self.assertIn("Local FLUX made: b.png", out)
        self.assertIn("no Google session", out)

    def test_no_image_reply_falls_back(self):
        with mock.patch.object(gi, "gemini_images", return_value=([], "No puedo crear eso")), \
             mock.patch.object(gi, "flux_images", return_value=["c.png"]):
            out = gi.generate_image({"prompt": "x"})
        self.assertIn("c.png", out)
        self.assertIn("No puedo crear eso", out)

    def test_edit_or_gemini_only_never_uses_flux(self):
        for params in ({"prompt": "x", "files": "a.png; b.png"}, {"prompt": "x", "engine": "gemini"}):
            with mock.patch.object(gi, "gemini_images", side_effect=RuntimeError("down")), \
                 mock.patch.object(gi, "flux_images") as f:
                out = gi.generate_image(params)
            self.assertTrue(out.startswith("Could not"))
            f.assert_not_called()

    def test_flux_only(self):
        with mock.patch.object(gi, "gemini_images") as g, \
             mock.patch.object(gi, "flux_images", return_value=["d.png"]):
            out = gi.generate_image({"prompt": "x", "engine": "flux"})
        g.assert_not_called()
        self.assertEqual(out, "Local FLUX made: d.png")

    def test_stale_cookies_regrab_once(self):
        calls = []

        async def fake(prompt, files, jar, stamp):
            calls.append(jar)
            if jar.get("__Secure-1PSID") == "old":
                raise RuntimeError("auth")
            return ["e.png"], ""

        with mock.patch.object(gi, "_ask_gemini", fake), \
             mock.patch.object(gi.COOKIES.__class__, "read_text", return_value='{"__Secure-1PSID": "old"}'), \
             mock.patch.object(gi, "grab_cookies", return_value={"__Secure-1PSID": "new"}), \
             mock.patch.object(gi, "OUT_DIR", Path(__file__).parent):
            self.assertEqual(gi.gemini_images("x"), (["e.png"], ""))
        self.assertEqual([c["__Secure-1PSID"] for c in calls], ["old", "new"])

    def test_registered(self):
        from agent import executor
        self.assertIn("generate_image", executor._TOOLS)


if __name__ == "__main__":
    unittest.main()
