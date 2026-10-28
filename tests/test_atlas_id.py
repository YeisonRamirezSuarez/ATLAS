"""ATLAS ID offline checks: the Supabase server is faked, nothing leaves this PC."""
import base64
import hashlib
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from memory import atlas_id

CFG = {"supabase_url": "https://example.supabase.co", "supabase_anon_key": "anon-public"}


def _reply(status=200, body=None):
    r = mock.Mock(status_code=status, content=b"x" if body is not None else b"")
    r.json.return_value = body
    return r


TOKENS = {"access_token": "AT-1", "refresh_token": "RT-1", "expires_in": 3600,
          "user": {"id": "u-1", "email": "pablo@example.com"}}


class AtlasIdTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(atlas_id, "SESSION_FILE", Path(self.dir.name) / "s.session"),
                        mock.patch.object(atlas_id, "get_config", return_value=dict(CFG)),
                        mock.patch.object(atlas_id, "_session", None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.dir.cleanup()

    def test_ships_ready_and_api_keys_can_point_elsewhere(self):
        with mock.patch.object(atlas_id, "get_config", return_value={}):
            self.assertEqual(atlas_id._project(), (atlas_id.PROJECT_URL, atlas_id.PUBLIC_KEY))
        self.assertEqual(atlas_id._project(), (CFG["supabase_url"], CFG["supabase_anon_key"]))
        self.assertTrue(atlas_id.PUBLIC_KEY.startswith("sb_publishable_"))   # never a secret key in code

    def test_not_configured_is_a_clear_error_and_app_keeps_working(self):
        with mock.patch.object(atlas_id, "get_config", return_value={}), \
             mock.patch.object(atlas_id, "PROJECT_URL", ""):
            self.assertFalse(atlas_id.configured())
            self.assertFalse(atlas_id.status()["signed_in"])
            with self.assertRaises(atlas_id.IDError):
                atlas_id.login()

    def test_each_install_gets_its_own_random_device_id_once(self):
        def ids():
            store = dict(CFG)
            with mock.patch.object(atlas_id, "get_config", side_effect=lambda: dict(store)), \
                 mock.patch.object(atlas_id, "update_config", side_effect=store.update):
                return atlas_id.device_id(), atlas_id.device_id()
        first, again = ids()
        other, _ = ids()
        self.assertEqual(first, again)   # kept, not remade on every call
        self.assertNotEqual(first, other)

    def test_pkce_login_through_loopback_stores_session_encrypted(self):
        seen = {}

        def browser(url):
            seen["authorize"] = parse_qs(urlparse(url).query)
            seen["t"] = threading.Thread(target=lambda: seen.update(
                page=urllib.request.urlopen(atlas_id.REDIRECT + "?code=one-time").read()))
            seen["t"].start()

        with mock.patch.object(atlas_id.requests, "post", return_value=_reply(200, TOKENS)) as post, \
             mock.patch.object(atlas_id, "register_device") as reg:
            atlas_id.login(timeout=10, open_browser=browser)
        body = post.call_args.kwargs["json"]
        self.assertEqual(post.call_args.kwargs["params"], {"grant_type": "pkce"})
        self.assertEqual(body["auth_code"], "one-time")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(seen["authorize"]["code_challenge"], [challenge])
        self.assertEqual(seen["authorize"]["provider"], ["google"])
        reg.assert_called_once()
        self.assertEqual(atlas_id.status()["email"], "pablo@example.com")
        seen["t"].join(5)
        self.assertIn(b"SIGNED IN", seen["page"])
        self.assertIn(b"pablo@example.com", seen["page"])
        if atlas_id.is_windows():
            raw = atlas_id.SESSION_FILE.read_bytes()
            self.assertNotIn(b"RT-1", raw)
            self.assertNotIn(b"AT-1", raw)
            atlas_id._session = None   # as after a restart: read back from disk
            self.assertEqual(atlas_id.access_token(), "AT-1")

    def test_refused_code_shows_failure_page_escaped(self):
        page = {}

        def browser(url):
            page["t"] = threading.Thread(target=lambda: page.update(body=urllib.request.urlopen(
                atlas_id.REDIRECT + "?code=x").read()))
            page["t"].start()

        with mock.patch.object(atlas_id.requests, "post", return_value=_reply(400, {"msg": "<b>bad</b> code"})):
            with self.assertRaisesRegex(atlas_id.IDError, "did not finish"):
                atlas_id.login(timeout=10, open_browser=browser)
        page["t"].join(5)
        self.assertIn(b"SIGN-IN FAILED", page["body"])
        self.assertIn(b"&lt;b&gt;bad", page["body"])
        self.assertFalse(atlas_id.status()["signed_in"])

    def test_refresh_offline_keeps_session_refused_signs_out(self):
        atlas_id._save_session({**TOKENS, "expires_at": 0, "user": TOKENS["user"]})
        with mock.patch.object(atlas_id.requests, "post", side_effect=requests.ConnectionError):
            with self.assertRaisesRegex(atlas_id.IDError, "offline"):
                atlas_id.access_token()
        self.assertTrue(atlas_id.status()["signed_in"])
        with mock.patch.object(atlas_id.requests, "post", return_value=_reply(400, {"error": "invalid_grant"})):
            with self.assertRaisesRegex(atlas_id.IDError, "sign in again"):
                atlas_id.access_token()
        self.assertFalse(atlas_id.status()["signed_in"])
        self.assertFalse(atlas_id.SESSION_FILE.exists())

    def test_logout_clears_even_when_offline(self):
        atlas_id._save_session({**TOKENS, "expires_at": 9e12, "user": TOKENS["user"]})
        with mock.patch.object(atlas_id.requests, "post", side_effect=requests.ConnectionError):
            atlas_id.logout()
        self.assertFalse(atlas_id.status()["signed_in"])

    def test_remove_device_rejects_crafted_filter(self):
        with self.assertRaises(ValueError):
            atlas_id.remove_device("x&user_id=neq.0")


if __name__ == "__main__":
    unittest.main()
