import json
import unittest
import urllib.error
import urllib.request
from unittest import mock

import phone_bridge


class FakeAtlas:
    def __init__(self):
        self.asked = []

    def phone_ask(self, text):
        self.asked.append(text)
        return f"dijiste {text}", b"\x01\x00" * 2400


class PhoneBridgeTests(unittest.TestCase):
    def setUp(self):
        cfg = mock.patch("config.get_config", return_value={})   # default switches, not this PC's settings
        cfg.start()
        self.addCleanup(cfg.stop)
        self.srv = phone_bridge.serve(0, "secreto")
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/ask"
        self.atlas = phone_bridge._atlas = FakeAtlas()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        phone_bridge._atlas = None

    def post(self, body: bytes, token="secreto", query=""):
        req = urllib.request.Request(self.url + query, data=body, method="POST",
                                     headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.headers["Content-Type"], r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers["Content-Type"], e.read()

    def test_wrong_token_is_refused_and_atlas_not_called(self):
        code, _, _ = self.post(b"hola", token="otro")
        self.assertEqual(code, 401)
        self.assertEqual(self.atlas.asked, [])

    def test_plain_text_and_json_bodies_reach_atlas(self):
        code, _, body = self.post("qué hora es".encode())
        self.assertEqual((code, json.loads(body)), (200, {"text": "dijiste qué hora es"}))
        self.post(json.dumps({"text": "abre música"}).encode())
        self.assertEqual(self.atlas.asked, ["qué hora es", "abre música"])

    def test_wav_format_returns_atlas_voice(self):
        code, kind, body = self.post(b"hola", query="?format=wav")
        self.assertEqual((code, kind), (200, "audio/wav"))
        self.assertTrue(body.startswith(b"RIFF") and body[8:12] == b"WAVE")

    def test_empty_body_is_rejected(self):
        self.assertEqual(self.post(b"")[0], 413)

    def test_siri_switch_off_refuses_without_calling_atlas(self):
        from unittest import mock
        with mock.patch("config.get_config", return_value={"phone_siri": False}):
            self.assertEqual(self.post(b"hola")[0], 503)
        self.assertEqual(self.atlas.asked, [])


class PushTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.dir = tempfile.TemporaryDirectory()
        cfg = mock.patch("config.get_config", return_value={})   # default switches, not this PC's settings
        cfg.start()
        self.addCleanup(cfg.stop)
        self.old = phone_bridge.TOPIC_PATH
        phone_bridge.TOPIC_PATH = Path(self.dir.name) / "ntfy_topic.txt"

    def tearDown(self):
        phone_bridge.TOPIC_PATH = self.old
        self.dir.cleanup()

    def test_topic_is_random_and_kept(self):
        t = phone_bridge.topic()
        self.assertTrue(t.startswith("atlas-") and len(t) > 15)
        self.assertEqual(phone_bridge.topic(), t)

    def test_voice_goes_as_wav_of_the_speech_the_pc_played(self):
        from unittest import mock
        with mock.patch("requests.Session.put") as put:
            phone_bridge._push("atlas-x", "hola ñandú", b"\x01\x00" * 2400)
        url, kw = put.call_args.args[0], put.call_args.kwargs
        self.assertEqual(url, "https://ntfy.sh/atlas-x")
        self.assertEqual(kw["params"], {"title": "ATLAS", "message": "hola ñandú", "priority": "high",
                                        "filename": "atlas.wav"})
        self.assertEqual(kw["data"], phone_bridge.to_wav(b"\x01\x00" * 2400))

    def test_text_still_goes_when_the_voice_upload_fails_or_nothing_was_spoken(self):
        from unittest import mock
        with mock.patch("requests.Session.put", side_effect=OSError("offline")), mock.patch("requests.Session.post") as post:
            phone_bridge._push("atlas-x", "hola", b"\x01\x00")
        self.assertEqual(post.call_args.kwargs["params"]["message"], "hola")
        with mock.patch("requests.Session.put") as put, mock.patch("requests.Session.post") as post:
            phone_bridge._push("atlas-x", "chao")
        put.assert_not_called()
        self.assertEqual(post.call_args.kwargs["params"]["message"], "chao")

    def test_email_goes_with_subject_atlas_only_when_configured(self):
        from unittest import mock
        with mock.patch("config.get_config", return_value={}), mock.patch("smtplib.SMTP") as smtp:
            phone_bridge._mail("hola", b"\x01\x00")
        smtp.assert_not_called()
        cfg = {"push_email": {"to": "yo@icloud.com", "from": "atlas@x.com", "password": "p", "smtp": "smtp.x.com"}}
        with mock.patch("config.get_config", return_value=cfg), mock.patch("smtplib.SMTP") as smtp:
            phone_bridge._mail("hola", b"\x01\x00" * 2400)
        s = smtp.return_value.__enter__.return_value
        s.starttls.assert_called_once()
        s.login.assert_called_once_with("atlas@x.com", "p")
        msg = s.send_message.call_args.args[0]
        self.assertEqual((msg["To"], msg["Subject"]), ("yo@icloud.com", "ATLAS"))
        body, voice = list(msg.iter_parts())
        self.assertEqual(body.get_content().strip(), "hola")
        self.assertEqual((voice.get_filename(), voice.get_content_type(), voice.get_content()),
                         ("atlas.wav", "audio/wav", phone_bridge.to_wav(b"\x01\x00" * 2400)))

    def test_switches_off_send_neither_notification_nor_email(self):
        from unittest import mock
        cfg = {"phone_push": False, "phone_mail": False,
               "push_email": {"to": "yo@icloud.com", "from": "a@x.com", "password": "p", "smtp": "smtp.x.com"}}
        with mock.patch("config.get_config", return_value=cfg), mock.patch("requests.Session.put") as put, \
                mock.patch("requests.Session.post") as post, mock.patch("smtplib.SMTP") as smtp:
            phone_bridge._push("atlas-x", "hola")
            phone_bridge._mail("hola")
        put.assert_not_called(); post.assert_not_called(); smtp.assert_not_called()

    def test_empty_topic_file_turns_push_off(self):
        from unittest import mock
        phone_bridge.TOPIC_PATH.write_text("", encoding="utf-8")
        with mock.patch.object(phone_bridge, "_push") as sent:
            phone_bridge.push("hola")
        self.assertFalse(phone_bridge.push_on())
        sent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
