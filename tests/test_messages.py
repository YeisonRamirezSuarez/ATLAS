"""Offline checks for actions/send_message.py: no network, no real GUI input.

Run:  python -m unittest discover tests
"""
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

import config
from actions import send_message as sm
from agent.executor import _FAILURE

CFG = {"telegram_api_id": "123", "telegram_api_hash": "abc"}


def fake_telethon(authorized=True, sent=None, dialogs=()):
    """Fake telethon modules; returns (modules dict, client mock)."""
    client = mock.MagicMock()
    client.is_user_authorized.return_value = authorized
    client.send_message.return_value = sent if sent is not None else types.SimpleNamespace(id=42)
    client.get_dialogs.return_value = list(dialogs)
    sync = types.ModuleType("telethon.sync")
    sync.TelegramClient = mock.MagicMock(return_value=client)
    return {"telethon": types.ModuleType("telethon"), "telethon.sync": sync}, client


def dialog(name):
    return types.SimpleNamespace(name=name, entity=f"<{name}>")


def send(platform="telegram", receiver="@bob", text="hi"):
    return sm.send_message({"receiver": receiver, "message_text": text, "platform": platform})


class TelegramTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(config, "get_config", return_value=CFG)
        p.start()
        self.addCleanup(p.stop)

    def test_missing_telethon(self):
        with mock.patch.dict(sys.modules, {"telethon": None, "telethon.sync": None}):
            r = send()
        self.assertIn("telethon", r)
        self.assertRegex(r, _FAILURE)

    def test_missing_api_keys(self):
        mods, _ = fake_telethon()
        with mock.patch.dict(sys.modules, mods), \
             mock.patch.object(config, "get_config", return_value={}):
            r = send()
        self.assertIn("telegram_api_id and telegram_api_hash", r)
        self.assertRegex(r, _FAILURE)

    def test_not_authorized_gives_login_command(self):
        mods, client = fake_telethon(authorized=False)
        with mock.patch.dict(sys.modules, mods):
            r = send()
        self.assertIn("telegram_login()", r)
        self.assertRegex(r, _FAILURE)
        client.send_message.assert_not_called()
        client.disconnect.assert_called_once()

    def test_success_returns_message_id(self):
        mods, client = fake_telethon()
        with mock.patch.dict(sys.modules, mods):
            r = send(receiver="@bob")
        self.assertEqual(r, "Telegram message sent to @bob (message id 42).")
        self.assertNotRegex(r, _FAILURE)
        client.get_entity.assert_called_once_with("@bob")
        _, kwargs = mods["telethon.sync"].TelegramClient.call_args
        self.assertEqual(kwargs.get("timeout"), 20)
        client.disconnect.assert_called_once()

    def test_reply_without_id_is_failure(self):
        mods, client = fake_telethon(sent=object())
        with mock.patch.dict(sys.modules, mods):
            r = send()
        self.assertEqual(r, "Failed: Telegram did not confirm the message.")
        client.disconnect.assert_called_once()

    def test_contact_name_resolves_single_match(self):
        mods, client = fake_telethon(dialogs=[dialog("Mom"), dialog("Bob Smith")])
        with mock.patch.dict(sys.modules, mods):
            r = send(receiver="mom")
        self.assertIn("sent to Mom", r)
        client.send_message.assert_called_once_with("<Mom>", "hi")

    def test_ambiguous_name_is_failure(self):
        mods, client = fake_telethon(dialogs=[dialog("John Doe"), dialog("John Roe")])
        with mock.patch.dict(sys.modules, mods):
            r = send(receiver="john")
        self.assertRegex(r, _FAILURE)
        self.assertIn("John Doe", r)
        self.assertIn("John Roe", r)
        client.send_message.assert_not_called()

    def test_unknown_name_is_failure(self):
        mods, client = fake_telethon(dialogs=[dialog("Alice")])
        with mock.patch.dict(sys.modules, mods):
            r = send(receiver="zed")
        self.assertRegex(r, _FAILURE)
        client.send_message.assert_not_called()


class RoutingTests(unittest.TestCase):
    def test_platform_aliases(self):
        cases = {"tg": "tg", "Telegram ": "tg", "TELEGRAM": "tg",
                 "wa": "gui", "WhatsApp": "gui", "whats app": "gui",
                 "ig": "ig", "Instagram": "ig", "Signal": "gui"}
        for platform, want in cases.items():
            with mock.patch.object(sm, "_send_telegram", return_value="tg"), \
                 mock.patch.object(sm, "_send_instagram", return_value="ig"), \
                 mock.patch.object(sm, "_send_gui", return_value="gui") as gui:
                self.assertEqual(send(platform=platform), want, platform)
                if want == "gui":
                    app = gui.call_args.args[0]
                    self.assertEqual(app, "Signal" if platform == "Signal" else "WhatsApp")

    def test_missing_receiver_is_failure(self):
        self.assertRegex(send(receiver=" "), _FAILURE)


class GuiTests(unittest.TestCase):
    def test_whatsapp_says_not_verified(self):
        with mock.patch.object(sm, "pyautogui") as gui, \
             mock.patch.object(sm, "_open_app", return_value=True), \
             mock.patch.object(sm.time, "sleep"):
            r = send(platform="whatsapp", receiver="Ana")
        self.assertIn("NOT verified", r)
        self.assertNotRegex(r, _FAILURE)
        gui.write.assert_any_call("hi", interval=0.03)

    def test_whatsapp_open_failure(self):
        with mock.patch.object(sm, "pyautogui"), \
             mock.patch.object(sm, "_open_app", return_value=False), \
             mock.patch.object(sm.time, "sleep"):
            self.assertRegex(send(platform="wa"), _FAILURE)

    def test_whatsapp_exception_is_failure(self):
        with mock.patch.object(sm, "pyautogui") as gui, \
             mock.patch.object(sm, "_open_app", return_value=True), \
             mock.patch.object(sm.time, "sleep"):
            gui.write.side_effect = RuntimeError("boom")
            self.assertRegex(send(platform="wa"), _FAILURE)

    def test_instagram_says_not_verified(self):
        with mock.patch.object(sm, "pyautogui"), \
             mock.patch("webbrowser.open"), \
             mock.patch.object(sm.time, "sleep"):
            self.assertIn("NOT verified", send(platform="ig"))

    def test_no_pyautogui_is_failure(self):
        with mock.patch.object(sm, "pyautogui", None):
            self.assertRegex(send(platform="whatsapp"), _FAILURE)


if __name__ == "__main__":
    unittest.main()
