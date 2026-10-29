"""windows_personalization and the Windows command reference, offline: nothing on this PC changes.
The theme engine, UISettings and Settings are faked; the live behaviour was checked by hand.

Run:  python -m unittest tests.test_windows_personalization
"""
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import windows_personalization as wp
from agent import executor

ROOT = Path(__file__).resolve().parent.parent

BASE_THEME = (
    "[Theme]\r\nDisplayName=Dark\r\nThemeId={896AE3AA-B311-45B5-84E5-750A79C7A47E}\r\n\r\n"
    "[Control Panel\\Cursors]\r\nArrow=%SystemRoot%\\cursors\\aero_arrow.cur\r\n\r\n"
    "[Control Panel\\Desktop]\r\nWallpaper=C:\\old.jpg\r\nPicturePosition=4\r\n\r\n"
    "[VisualStyles]\r\nPath=%SystemRoot%\\resources\\themes\\Aero\\Aero.msstyles\r\nColorizationColor=0XC40078D4\r\n"
    "AutoColorization=1\r\nSystemMode=Dark\r\nAppMode=Dark\r\n\r\n"
    "[Sounds]\r\nSchemeName=Windows\r\n"
)


class ParsingTests(unittest.TestCase):
    def test_spanish_and_english_names_map_to_windows_swatches(self):
        for name, hex_ in {"rosa": "E3008C", "Pink": "E3008C", "rojo": "E81123", "verde": "107C10",
                           "azul": "0078D4", "morado": "881798", "naranja": "F7630C", "amarillo": "FFB900",
                           "gris": "4A5459", "marrón": "7E735F"}.items():
            self.assertEqual(wp.parse_color(name), hex_, name)

    def test_names_inside_phrases_and_hex(self):
        self.assertEqual(wp.parse_color("ponlo de color rosa"), "E3008C")
        self.assertEqual(wp.parse_color("cambia el color de Windows a verde"), "107C10")
        self.assertEqual(wp.parse_color("azul oscuro por favor"), "0063B1")   # longest name wins
        self.assertEqual(wp.parse_color("#e3008c"), "E3008C")
        self.assertEqual(wp.parse_color("2D7D9A"), "2D7D9A")
        self.assertIsNone(wp.parse_color("credo"))   # 'red' inside a word is not a color
        self.assertIsNone(wp.parse_color("#12345"))

    def test_switch_and_mode_words(self):
        self.assertTrue(wp.parse_switch("activar"))
        self.assertFalse(wp.parse_switch("off"))
        self.assertIsNone(wp.parse_switch("maybe"))
        self.assertTrue(wp.parse_mode("modo oscuro", current_dark=False))
        self.assertFalse(wp.parse_mode("claro", current_dark=True))
        self.assertFalse(wp.parse_mode("toggle", current_dark=True))
        self.assertIsNone(wp.parse_mode("purple", current_dark=True))


class ThemeFileTests(unittest.TestCase):
    def test_derived_theme_changes_only_color_and_mode(self):
        t = wp.build_theme(BASE_THEME, "E3008C", system_dark=True, apps_dark=False, wallpaper=r"C:\live.jpg")
        self.assertEqual(wp.get_ini_value(t, "VisualStyles", "ColorizationColor"), "0XC4E3008C")
        self.assertEqual(wp.get_ini_value(t, "VisualStyles", "AutoColorization"), "0")
        self.assertEqual(wp.get_ini_value(t, "VisualStyles", "SystemMode"), "Dark")
        self.assertEqual(wp.get_ini_value(t, "VisualStyles", "AppMode"), "Light")
        self.assertEqual(wp.get_ini_value(t, r"Control Panel\Desktop", "Wallpaper"), r"C:\live.jpg")
        # kept: cursors, sounds, visual style, wallpaper position
        self.assertEqual(wp.get_ini_value(t, r"Control Panel\Cursors", "Arrow"), r"%SystemRoot%\cursors\aero_arrow.cur")
        self.assertEqual(wp.get_ini_value(t, "Sounds", "SchemeName"), "Windows")
        self.assertEqual(wp.get_ini_value(t, r"Control Panel\Desktop", "PicturePosition"), "4")
        self.assertIn("Aero.msstyles", wp.get_ini_value(t, "VisualStyles", "Path"))
        self.assertIsNone(wp.get_ini_value(t, "Theme", "ThemeId"))
        self.assertEqual(wp.get_ini_value(t, "Theme", "DisplayName"), "ATLAS")

    def test_missing_keys_and_sections_are_added(self):
        t = wp.build_theme("[Theme]\r\nDisplayName=x\r\n", "107C10", system_dark=False, apps_dark=False)
        self.assertEqual(wp.get_ini_value(t, "VisualStyles", "ColorizationColor"), "0XC4107C10")
        self.assertEqual(wp.get_ini_value(t, "VisualStyles", "SystemMode"), "Light")

    def test_reads_utf16_theme_files(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.theme"
            p.write_text(BASE_THEME, encoding="utf-16")
            self.assertEqual(wp.get_ini_value(wp.read_theme_text(p), "VisualStyles", "AppMode"), "Dark")


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        theme = Path(self.dir.name) / "Atlas.theme"
        base = Path(self.dir.name) / "dark.theme"
        base.write_text(BASE_THEME, encoding="utf-16")
        self.applied = []
        self.live = {"accent": "4A5459", "apps_dark": True, "transparency": False}
        self.system_light = 0
        patches = [mock.patch.object(wp, "_OS", "Windows"),
                   mock.patch.object(wp, "THEMES_DIR", Path(self.dir.name)),
                   mock.patch.object(wp, "ATLAS_THEME", theme),
                   mock.patch.object(wp, "_current_theme_file", return_value=base),
                   mock.patch.object(wp, "_read_personalize", side_effect=lambda name: self.system_light),
                   mock.patch.object(wp, "get_wallpaper", return_value=r"C:\live.jpg"),
                   mock.patch.object(wp, "read_uisettings", side_effect=lambda expect="": dict(self.live)),
                   mock.patch.object(wp, "apply_theme", side_effect=self.fake_apply)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.dir.cleanup)

    def fake_apply(self, path):
        """What the theme engine does: the live state follows the applied file."""
        text = wp.read_theme_text(path)
        self.applied.append(text)
        self.live["accent"] = wp.get_ini_value(text, "VisualStyles", "ColorizationColor")[4:]
        self.live["apps_dark"] = wp.get_ini_value(text, "VisualStyles", "AppMode") == "Dark"
        self.system_light = int(wp.get_ini_value(text, "VisualStyles", "SystemMode") == "Light")

    def test_accent_color_is_verified_by_read_back(self):
        r = wp.windows_personalization({"action": "accent_color", "value": "rosa"})
        self.assertEqual(r, "Verified: Windows accent color is now #E3008C (pink).")
        t = self.applied[0]
        self.assertEqual(wp.get_ini_value(t, r"Control Panel\Desktop", "Wallpaper"), r"C:\live.jpg")
        self.assertEqual(wp.get_ini_value(t, "VisualStyles", "AppMode"), "Dark")   # mode kept

    def test_mismatching_read_back_is_a_failure(self):
        wp.apply_theme.side_effect = lambda path: None   # Windows ignored the theme
        r = wp.windows_personalization({"action": "accent_color", "value": "verde"})
        self.assertTrue(r.startswith("Could not verify"), r)
        self.assertRegex(r, executor._FAILURE)
        self.assertNotIn("Verified", r)

    def test_unknown_color_is_not_applied(self):
        r = wp.windows_personalization({"action": "accent_color", "value": "plaid"})
        self.assertTrue(r.startswith("Could not understand"))
        self.assertEqual(self.applied, [])

    def test_theme_mode_keeps_accent(self):
        r = wp.windows_personalization({"action": "theme_mode", "value": "claro"})
        self.assertIn("Verified", r)
        self.assertFalse(self.live["apps_dark"])
        self.assertEqual(self.system_light, 1)
        self.assertEqual(wp.get_ini_value(self.applied[0], "VisualStyles", "ColorizationColor"), "0XC44A5459")

    def test_toggle_already_in_state_does_not_open_settings(self):
        with mock.patch.object(wp, "_toggle_state", return_value=False), \
             mock.patch.object(wp.os, "startfile", create=True) as start:
            r = wp.windows_personalization({"action": "transparency", "value": "off"})
        self.assertTrue(r.startswith("Verified"), r)
        start.assert_not_called()

    def test_wallpaper_needs_an_existing_image(self):
        self.assertTrue(wp.windows_personalization({"action": "wallpaper", "value": ""}).startswith("Could not"))
        self.assertTrue(wp.windows_personalization({"action": "wallpaper", "value": r"C:\nope\x.jpg"}).startswith("Could not"))

    def test_unknown_action_and_other_os(self):
        self.assertIn("Unknown", wp.windows_personalization({"action": "fly"}))
        with mock.patch.object(wp, "_OS", "Linux"):
            self.assertTrue(wp.windows_personalization({"action": "status"}).startswith("Could not"))


class NoRegistryWritesTests(unittest.TestCase):
    def test_module_never_writes_the_registry(self):
        src = (ROOT / "actions" / "windows_personalization.py").read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        code = re.sub(r'"""[\s\S]*?"""', "", code)   # the docstrings describe the rule, they do not break it
        for forbidden in ("SetValue", "CreateKey", "DeleteValue", "DeleteKey", "reg add", "reg.exe",
                          "Set-ItemProperty", "New-ItemProperty", "KEY_WRITE", "KEY_ALL_ACCESS", "KEY_SET_VALUE"):
            self.assertNotIn(forbidden, code)


class WindowsReferenceTests(unittest.TestCase):
    def headings(self, goal):
        return [l for l in executor.windows_reference(goal).splitlines() if l.startswith("## ")]

    def test_general_rules_always_and_sections_by_keyword(self):
        self.assertEqual(self.headings("open github.com"), ["## General rules"])
        self.assertIn("## Windows Update and hotfixes", self.headings("check pending Windows Update"))
        self.assertIn("## Processes and apps running", self.headings("¿qué procesos usan más memoria?"))
        self.assertIn("## Services", self.headings("muestra los servicios"))

    def test_error_text_alone_pulls_no_extra_sections(self):
        self.assertEqual(self.headings("fix error 0x80070005 in my script"), ["## General rules"])

    def test_reference_stays_within_budget(self):
        everything = "process service network update scheduled task power settings hardware event repair file audio com object defender install"
        self.assertLessEqual(len(executor.windows_reference(everything)), executor.MAX_REFERENCE + 200)

    def test_script_prompt_gets_reference_and_personalization_rule(self):
        seen = {}
        with mock.patch.object(executor.platform, "system", return_value="Windows"), \
             mock.patch("agent.llm.complete", side_effect=lambda p, system, max_tokens, **kw: seen.update(system=system) or "print(1)"), \
             mock.patch("memory.memory_manager.log_skill_decision"):
            executor._write_script("list running services", [], None)
        self.assertIn("WINDOWS COMMAND REFERENCE", seen["system"])
        self.assertIn("Get-Service", seen["system"])
        self.assertIn("windows_personalization", seen["system"])
        self.assertIn("never write the registry", seen["system"])


class DeclarationTests(unittest.TestCase):
    def test_tool_declared_dispatched_and_routed(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"name": "windows_personalization"', main)
        self.assertIn('elif name == "windows_personalization":', main)
        self.assertIn("windows_personalization", executor._TOOLS)
        prompt = (ROOT / "core" / "prompt.txt").read_text(encoding="utf-8")
        self.assertIn("windows_personalization", prompt)


if __name__ == "__main__":
    unittest.main()
