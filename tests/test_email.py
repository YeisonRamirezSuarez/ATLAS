"""Email tool: send needs a matching confirmation, drafts never send. Network and SMTP are mocked:
these tests never contact a mail server."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import email_client as ec

IMAP = {"email_accounts": [{"name": "personal", "type": "imap", "address": "me@gmail.com", "password": "x"}]}
GRAPH = {"email_accounts": [{"name": "work", "type": "graph", "client_id": "id"}]}
MAIL = {"action": "send", "to": "ana@example.com", "subject": "Hola", "body": "Nos vemos a las 5"}


class SendGateTests(unittest.TestCase):
    def setUp(self):
        ec._pending = None
        p = [mock.patch.object(ec, "get_config", return_value=IMAP),
             mock.patch.object(ec.smtplib, "SMTP_SSL"), mock.patch.object(ec.smtplib, "SMTP"),
             mock.patch.object(ec.imaplib, "IMAP4_SSL")]
        self.cfg, self.ssl, self.plain, _ = [x.start() for x in p]
        for x in p:
            self.addCleanup(x.stop)

    def test_unconfirmed_send_only_asks(self):
        r = ec.email_client(MAIL)
        self.assertTrue(r.startswith("NEEDS CONFIRMATION"), r)
        self.ssl.assert_not_called()
        self.plain.assert_not_called()

    def test_confirmed_yes_without_asking_first_does_not_send(self):
        r = ec.email_client({**MAIL, "confirmed": "yes"})
        self.assertTrue(r.startswith("NEEDS CONFIRMATION"), r)
        self.ssl.assert_not_called()

    def test_changed_text_after_confirmation_is_not_sent(self):
        ec.email_client(MAIL)
        r = ec.email_client({**MAIL, "body": "Otro texto", "confirmed": "yes"})
        self.assertTrue(r.startswith("NEEDS CONFIRMATION"), r)
        self.ssl.assert_not_called()

    def test_matching_confirmation_sends_once(self):
        ec.email_client(MAIL)
        r = ec.email_client({**MAIL, "confirmed": "yes"})
        self.assertTrue(r.startswith("Sent"), r)
        smtp = self.ssl.return_value
        smtp.send_message.assert_called_once()
        self.assertEqual(smtp.send_message.call_args[0][0]["To"], "ana@example.com")
        self.assertIn("NEEDS CONFIRMATION", ec.email_client({**MAIL, "confirmed": "yes"}))   # one yes = one send

    def test_trusted_case_sends_without_asking(self):
        r = ec.email_client({**MAIL, "_trusted": True})
        self.assertTrue(r.startswith("Sent"), r)

    def test_bad_address_fails(self):
        self.assertTrue(ec.email_client({**MAIL, "to": "ana"}).startswith("Could not"))

    def test_no_accounts_fails(self):
        self.cfg.return_value = {}
        self.assertTrue(ec.email_client({"action": "list"}).startswith("Could not"))


class GraphTests(unittest.TestCase):
    def test_draft_posts_to_drafts_not_sendmail(self):
        ec._pending = None
        with mock.patch.object(ec, "get_config", return_value=GRAPH), \
             mock.patch.object(ec._Graph, "__init__", lambda s, a: setattr(s, "acct", a)), \
             mock.patch.object(ec._Graph, "_req", return_value={}) as req:
            r = ec.email_client({**MAIL, "action": "draft"})
        self.assertTrue(r.startswith("Draft saved"), r)
        req.assert_called_once()
        self.assertEqual(req.call_args[0][:2], ("POST", "/me/messages"))

    def test_sign_in_is_not_reported_as_failure(self):
        with mock.patch.object(ec, "get_config", return_value=GRAPH), \
             mock.patch.object(ec._Graph, "__init__", lambda s, a: setattr(s, "acct", a)), \
             mock.patch.object(ec._Graph, "token", side_effect=ec.SignInNeeded("SIGN-IN NEEDED for work: code ABC")):
            r = ec.email_client({"action": "list"})
        self.assertTrue(r.startswith("SIGN-IN NEEDED"), r)


class HelperTests(unittest.TestCase):
    def test_drafts_folder_from_list(self):
        listing = [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Drafts) "/" "[Gmail]/Borradores"']
        self.assertEqual(ec._drafts_folder(listing), '"[Gmail]/Borradores"')
        self.assertEqual(ec._drafts_folder([]), "Drafts")


if __name__ == "__main__":
    unittest.main()
