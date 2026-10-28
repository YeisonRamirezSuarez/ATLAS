"""Linux/macOS volume and brightness adapters with the system commands faked. Run: python -m unittest discover tests"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(errors="replace")   # tool logs print emoji; cp1252 consoles can't encode them

from actions import computer_settings as cs


def fake(outputs):
    """_run stand-in: answers by command name + subcommand, records every call."""
    calls = []

    def run(*argv):
        calls.append(argv)
        out = outputs.get(" ".join(argv[:2]), "")
        if isinstance(out, Exception):
            raise out
        return out
    return run, calls


class UnixVolumeTests(unittest.TestCase):
    def test_wpctl_reads_level_back(self):
        run, calls = fake({"wpctl get-volume": "Volume: 0.55 [MUTED]"})
        with mock.patch.object(cs, "_OS", "Linux"), mock.patch.object(cs, "_run", run), \
             mock.patch.object(cs.shutil, "which", lambda n: "/usr/bin/wpctl"):
            self.assertEqual(cs._unix_volume(delta=10), "Volume is now 55% (muted).")
        self.assertIn(("wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", "10%+"), calls)

    def test_pactl_fallback(self):
        run, _ = fake({"pactl get-sink-volume": "Volume: front-left: 26214 /  40% / -23.88 dB",
                       "pactl get-sink-mute": "Mute: no"})
        with mock.patch.object(cs, "_OS", "Linux"), mock.patch.object(cs, "_run", run), \
             mock.patch.object(cs.shutil, "which", lambda n: None):
            self.assertEqual(cs._unix_volume(absolute=40), "Volume is now 40%.")

    def test_mac_osascript(self):
        run, _ = fake({"osascript -e": "output volume:30, input volume:75, alert volume:100, output muted:false"})
        with mock.patch.object(cs, "_OS", "Darwin"), mock.patch.object(cs, "_run", run):
            self.assertEqual(cs._unix_volume(delta=-10), "Volume is now 30%.")

    def test_missing_tool_is_a_failure(self):
        run, _ = fake({"pactl set-sink-mute": RuntimeError("pactl: No such file")})
        with mock.patch.object(cs, "_OS", "Linux"), mock.patch.object(cs, "_run", run), \
             mock.patch.object(cs.shutil, "which", lambda n: None):
            self.assertTrue(cs._unix_volume(mute=True).startswith("Could not"))


class UnixBrightnessTests(unittest.TestCase):
    def test_brightnessctl_reads_level_back(self):
        run, _ = fake({"brightnessctl -m": "intel_backlight,backlight,400,40%,1000"})
        with mock.patch.object(cs, "_OS", "Linux"), mock.patch.object(cs, "_run", run), \
             mock.patch.object(cs.shutil, "which", lambda n: "/usr/bin/brightnessctl"):
            self.assertEqual(cs._unix_brightness(delta=-10), "Brightness is now 40%.")

    def test_no_brightnessctl_is_a_failure(self):
        with mock.patch.object(cs, "_OS", "Linux"), mock.patch.object(cs.shutil, "which", lambda n: None):
            self.assertTrue(cs._unix_brightness(absolute=50).startswith("Could not"))

    def test_mac_key_is_not_claimed(self):
        run, _ = fake({})
        with mock.patch.object(cs, "_OS", "Darwin"), mock.patch.object(cs, "_run", run):
            self.assertIn("not verified", cs._unix_brightness(delta=10))


class TypingTests(unittest.TestCase):
    def setUp(self):
        from actions import computer_control as cc
        self.cc = cc
        self.gui = mock.MagicMock()
        self.clip = mock.MagicMock()
        for name, val in (("pyautogui", self.gui), ("pyperclip", self.clip), ("_PYAUTOGUI", True),
                          ("_PYPERCLIP", True)):
            p = mock.patch.object(cc, name, val, create=True)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(cc.time, "sleep", lambda s: None)
        p.start()
        self.addCleanup(p.stop)

    def test_non_ascii_is_pasted_not_typed(self):
        with mock.patch.object(self.cc, "_get_os", lambda: "linux"):
            self.cc._type("¿Qué tal, señor?")
        self.gui.typewrite.assert_not_called()
        self.clip.copy.assert_called_with("¿Qué tal, señor?")
        self.gui.hotkey.assert_called_with("ctrl", "v")

    def test_mac_uses_command_key(self):
        with mock.patch.object(self.cc, "_get_os", lambda: "mac"):
            self.cc._clipboard_paste("hola")
        self.gui.hotkey.assert_called_with("command", "v")

    def test_wayland_result_is_flagged(self):
        with mock.patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}), \
             mock.patch("config.get_os", lambda: "linux"), mock.patch.object(self.cc, "_get_os", lambda: "linux"):
            out = self.cc.computer_control({"action": "press", "key": "enter"})
        self.assertIn("Wayland", out)
        self.assertIn("not verified", out)


class CloseAppTests(unittest.TestCase):
    def proc(self, pid, name):
        p = mock.MagicMock(info={"pid": pid, "name": name})
        p.is_running.return_value = False
        return p

    @unittest.skipUnless(__import__("importlib.util").util.find_spec("psutil"), "psutil not installed")
    def test_closing_an_app_never_closes_atlas(self):
        self.assertIn("ATLAS itself", cs.close_app("Atlas"))
        procs = [self.proc(1, "pythonw.exe"), self.proc(2, "Spotify.exe"), self.proc(3, "Spotify.exe")]
        with mock.patch.object(cs, "_OS", "Windows"), mock.patch.object(cs, "_own_pids", return_value={1}), \
             mock.patch("psutil.process_iter", return_value=procs), \
             mock.patch.object(cs.subprocess, "run") as run, mock.patch.object(cs.time, "sleep"), \
             mock.patch.object(cs.subprocess, "CREATE_NO_WINDOW", 0, create=True):
            self.assertEqual(cs.close_app("spotify"), "Closed Spotify.exe.")
            self.assertEqual(run.call_args[0][0], ["taskkill", "/pid", "2", "/pid", "3"])   # no /F, not ATLAS
            self.assertIn("not running", cs.close_app("python"))   # ATLAS's own process is off limits

    def test_exact_name_wins_and_short_names_do_not_match_everything(self):
        self.assertTrue(cs._matches("whatsapp", "WhatsApp.Root.exe"))
        self.assertFalse(cs._matches("co", "Code.exe"))
        self.assertTrue(cs._matches("Visual Studio Code", "VisualStudioCode.exe"))

    def test_keyboard_action_does_not_hit_atlas_when_it_is_the_only_window(self):
        with mock.patch.object(cs, "away_from_atlas", return_value=False), \
             mock.patch.object(cs, "minimize_window") as mini:
            self.assertIn("ATLAS itself", cs.computer_settings({"action": "minimize"}))
            mini.assert_not_called()
            self.assertIn("ATLAS itself", cs.computer_settings({"action": "close_app"}))


if __name__ == "__main__":
    unittest.main()
