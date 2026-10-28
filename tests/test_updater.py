"""Updater: data backup/restore, migrations on a version change, release check (network mocked)."""
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import updater


class DataTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        (self.base / "config").mkdir()
        (self.base / "memory" / "skills").mkdir(parents=True)
        (self.base / "config" / "api_keys.json").write_text('{"k": 1}')
        (self.base / "memory" / "long_term.json").write_text('{"m": 1}')
        (self.base / "memory" / "skills" / "s.py").write_text("x = 1")
        (self.base / "memory" / "memory_manager.py").write_text("code")   # code: never backed up

    def test_backup_then_restore_roundtrip(self):
        z = updater.backup(self.base)
        names = set(zipfile.ZipFile(z).namelist())
        self.assertEqual(names, {"config/api_keys.json", "memory/long_term.json", "memory/skills/s.py"})
        (self.base / "memory" / "long_term.json").write_text("broken")
        updater.restore(z, self.base)
        self.assertEqual((self.base / "memory" / "long_term.json").read_text(), '{"m": 1}')

    def test_restore_refuses_code_paths(self):
        bad = self.base / "evil.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("main.py", "print('owned')")
        with self.assertRaises(ValueError):
            updater.restore(bad, self.base)
        self.assertFalse((self.base / "main.py").exists())

    def test_version_change_backs_up_and_migrates_once(self):
        ran = []
        with mock.patch.dict(updater.MIGRATIONS, {"2.1.0": lambda b: ran.append("2.1.0"),
                                                  "9.0.0": lambda b: ran.append("9.0.0")}):
            updater.on_start("2.0.0", self.base, log=lambda m: None)   # first run: stamp only
            self.assertFalse((self.base / "backups").exists())
            updater.on_start("2.1.0", self.base, log=lambda m: None)
            updater.on_start("2.1.0", self.base, log=lambda m: None)
        self.assertEqual(ran, ["2.1.0"])
        self.assertEqual(len(list((self.base / "backups").glob("*before-2.1.0.zip"))), 1)

    def test_failed_migration_keeps_old_stamp(self):
        updater.on_start("2.0.0", self.base, log=lambda m: None)
        with mock.patch.dict(updater.MIGRATIONS, {"2.1.0": lambda b: 1 / 0}):
            with self.assertRaises(ZeroDivisionError):
                updater.on_start("2.1.0", self.base, log=lambda m: None)
        self.assertEqual((self.base / "memory" / ".version").read_text(), "2.0.0")


class CheckTests(unittest.TestCase):
    def reply(self, tag):
        return mock.patch.object(updater.urllib.request, "urlopen",
                                 lambda req, timeout: io.BytesIO(json.dumps(
                                     {"tag_name": tag, "html_url": "https://x/r"}).encode()))

    def test_off_without_repo(self):
        self.assertIn("apagada", updater.check("2.0.0", None))

    def test_newer_and_same(self):
        with self.reply("v2.10.0"):
            self.assertIn("Actualización disponible: v2.10.0", updater.check("2.9.9", "o/r"))
        with self.reply("2.0.0"):
            self.assertIn("está al día", updater.check("2.0.0", "o/r"))

    def test_network_error_is_a_failure(self):
        with mock.patch.object(updater.urllib.request, "urlopen", side_effect=OSError("offline")):
            self.assertTrue(updater.check("2.0.0", "o/r").startswith("No se pudo"))

    def test_tools_list_only_newer_and_skip_failures(self):
        pypi = {"anthropic": "2.10.0", "openai": "3.0.0"}   # newer, older; the rest fail to load
        def get(url, timeout):
            name = url.split("/")[-2]
            if name not in pypi:
                raise OSError("offline")
            return {"info": {"version": pypi[name]}}
        with mock.patch.object(updater, "PACKAGES", ["anthropic", "openai", "ddgs"]), \
             mock.patch.object(updater, "_get_json", get), \
             mock.patch.object(updater.importlib.metadata, "version", lambda p: "2.9.0" if p == "anthropic" else "3.1"), \
             mock.patch.object(updater.shutil, "which", lambda *a, **k: None):
            self.assertEqual(updater.check_tools(), ["anthropic 2.9.0 -> 2.10.0"])


class SelfChangeGuardTests(unittest.TestCase):
    """Chat tools must not rewrite ATLAS's own folder; changes elsewhere still work."""
    def test_file_tools_refuse_own_folder(self):
        from actions import file_controller as fc
        from config import APP_DIR
        own = APP_DIR / "main.py"
        before = own.read_bytes()
        self.assertTrue(fc.write_file(str(own), "x").startswith("Refused"))
        self.assertTrue(fc.delete_file(str(own)).startswith("Refused"))
        self.assertTrue(fc.move_file(str(own), tempfile.mkdtemp()).startswith("Refused"))
        self.assertEqual(own.read_bytes(), before)

    def test_outside_folder_still_allowed(self):
        from actions import file_controller as fc
        f = Path(tempfile.mkdtemp()) / "a.txt"
        self.assertTrue(fc.write_file(str(f), "hi").startswith("Written"))
        self.assertEqual(f.read_text(), "hi")


if __name__ == "__main__":
    unittest.main()
