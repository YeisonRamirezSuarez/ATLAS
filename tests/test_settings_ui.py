"""ACCOUNT tab controls write the real config, and SAVE & START does not undo them (temp config file)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication

import config
import ui

app = QApplication.instance() or QApplication([])


class JarvisIdTabTests(unittest.TestCase):
    def test_id_tab_saves_and_save_and_boot_keeps_it(self):
        path = Path(tempfile.mkdtemp()) / "api_keys.json"
        path.write_text(json.dumps({"provider": "gemini", "gemini_api_key": "test-key"}), encoding="utf-8")
        from memory import jarvis_id
        with mock.patch.object(config, "_CONFIG_PATH", path), mock.patch.object(ui, "API_FILE", path), \
             mock.patch.object(jarvis_id, "SESSION_FILE", path.with_name("s.session")), \
             mock.patch.object(jarvis_id, "_session", None):
            ov = ui.SettingsOverlay(json.loads(path.read_text()), can_cancel=True)
            self.assertFalse(hasattr(ov, "_sb_key"))   # the project ships with JARVIS: nothing to paste
            ov._dev_name.setText("Studio PC")
            ov._id_save()
            ov._sync_box.setChecked(True)
            self.assertIn("not signed in", ov._id_status.text())
            got = []
            ov.done.connect(got.append)
            ov._submit()   # the panel's own copy of the config predates the ID tab changes
            saved = json.loads(path.read_text())
        self.assertEqual(saved["device_name"], "Studio PC")
        self.assertTrue(saved["memory_sync"])
        self.assertEqual(got[0]["device_name"], "Studio PC")
        self.assertTrue(got[0]["memory_sync"])


class ThemeTests(unittest.TestCase):
    def test_palettes_map_one_to_one(self):   # restyle() maps colours by value
        for a, b in ((ui._DARK, ui._LIGHT), (ui._LIGHT, ui._DARK)):
            seen = {}
            for k, v in a.items():
                if k != "THEME":
                    self.assertEqual(seen.setdefault(v.lower(), b[k]), b[k], k)

    def test_theme_click_applies_live_and_save_does_not_restart(self):
        path = Path(tempfile.mkdtemp()) / "api_keys.json"
        path.write_text(json.dumps({"provider": "gemini", "gemini_api_key": "test-key"}), encoding="utf-8")
        with mock.patch.object(ui, "API_FILE", path), mock.patch.object(ui, "relaunch") as relaunch:
            first = []   # a config saved once by SAVE & START, as on any real install
            first_ov = ui.SettingsOverlay(json.loads(path.read_text()), can_cancel=True)
            first_ov.done.connect(first.append)
            first_ov._submit()
            path.write_text(json.dumps(first[0]), encoding="utf-8")
            win = ui.MainWindow("")
            win._log.setHtml(f"<span style='color:{ui.C.ARC}'>hi</span>")
            win._show_setup(can_cancel=True)
            ov = win._overlay
            try:
                ov._pick_theme("light")
                self.assertEqual(json.loads(path.read_text())["theme"], "light")
                self.assertIn(ui._LIGHT["OVERLAY"], ov.styleSheet())
                self.assertNotIn(ui._DARK["BG"], win.centralWidget().styleSheet())
                self.assertIn(ui._LIGHT["ARC"], win._log.toHtml())
                self.assertEqual(win._bar_cpu._color, ui._LIGHT["PRI"])
                ov._submit()   # nothing else changed
                relaunch.assert_not_called()
                self.assertIsNone(win._overlay)
            finally:
                ui.restyle("dark")
                win.deleteLater()


if __name__ == "__main__":
    unittest.main()
