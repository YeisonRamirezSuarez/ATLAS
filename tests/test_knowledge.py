"""MEMORIA graph, deletes and PIN, offline: memory and Engram export are faked."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from memory import knowledge as kn

MEMORY = {"identity": {"name": {"value": "Pablo", "updated": "2026-10-26"}},
          "projects": {"atlas_assistant": {"value": "builds a voice assistant with python", "updated": "2026-10-26"}},
          "preferences": {"likes_python": {"value": "prefers python for scripts", "updated": "2026-10-20"}}}
EXPORT = {"observations": [
    {"id": 1, "type": "conversation", "title": "hola", "content": "User: hola\nAtlas: hola Pablo", "created_at": "2026-10-26 20:00:00"},
    {"id": 2, "type": "identity", "title": "identity/name", "content": "name: Pablo", "created_at": "2026-10-26 20:01:00"},
    {"id": 3, "type": "projects", "title": "projects/atlas_assistant", "content": "atlas assistant: builds a voice assistant with python", "created_at": "2026-10-26 21:30:00"},
    {"id": 4, "type": "wishes", "title": "wishes/buy_guitar", "content": "buy guitar: an acoustic guitar", "created_at": "2026-10-25 18:00:00"},
    {"id": 5, "type": "wishes", "title": "wishes/buy_guitar", "content": "buy guitar: a red acoustic guitar", "created_at": "2026-10-26 18:00:00"},
]}


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.g = kn.build_graph(MEMORY, EXPORT)
        self.by = {n["id"]: n for n in self.g["nodes"]}

    def test_facts_from_memory_and_archived_ones_from_engram(self):
        facts = {i for i, n in self.by.items() if n["kind"] == "fact"}
        self.assertEqual(facts, {"fact:identity/name", "fact:projects/atlas_assistant",
                                 "fact:preferences/likes_python", "fact:wishes/buy_guitar"})
        guitar = self.by["fact:wishes/buy_guitar"]
        self.assertTrue(guitar["archived"])
        self.assertEqual(guitar["value"], "a red acoustic guitar")   # latest value, prefix stripped
        self.assertFalse(self.by["fact:identity/name"]["archived"])

    def test_learned_time_is_the_first_engram_save_else_the_day(self):
        self.assertEqual(self.by["fact:wishes/buy_guitar"]["t"], kn._utc("2026-10-25 18:00:00"))
        self.assertEqual(self.by["fact:preferences/likes_python"]["t"], kn._day("2026-10-20"))
        self.assertNotIn(":", self.by["fact:preferences/likes_python"]["when"])   # date only, no made-up time
        self.assertEqual(self.g["start"], kn._day("2026-10-20"))

    def test_conversation_linked_only_when_just_before_the_fact(self):
        self.assertEqual(self.by["fact:identity/name"]["conv"], "conv:1")
        self.assertIn("hola Pablo", self.by["fact:identity/name"]["excerpt"])
        self.assertEqual(self.by["fact:projects/atlas_assistant"]["conv"], "")   # 90 min later: no guess
        self.assertEqual(self.by["conv:1"]["kind"], "conv")

    def test_links_to_you_conversation_and_shared_words(self):
        idx = {n["id"]: i for i, n in enumerate(self.g["nodes"])}
        kinds = {(self.g["nodes"][a]["id"], self.g["nodes"][b]["id"], k) for a, b, k in self.g["links"]}
        self.assertIn(("you", "fact:identity/name", "you"), kinds)
        self.assertIn(("fact:identity/name", "conv:1", "conv"), kinds)
        pair = {"fact:projects/atlas_assistant", "fact:preferences/likes_python"}   # both say "python"
        self.assertTrue(any({a, b} == pair and k == "related" for a, b, k in kinds))
        self.assertTrue(all(0 <= a < len(idx) and 0 <= b < len(idx) for a, b, _ in self.g["links"]))

    def test_layout_is_stable_and_on_the_sphere(self):
        again = {n["id"]: n for n in kn.build_graph(MEMORY, EXPORT)["nodes"]}
        for i, n in self.by.items():
            self.assertEqual((n["x"], n["y"], n["z"]), (again[i]["x"], again[i]["y"], again[i]["z"]))
            if n["kind"] == "fact":
                self.assertAlmostEqual((n["x"] ** 2 + n["y"] ** 2 + n["z"] ** 2) ** 0.5, 0.85, places=2)

    def test_empty_memory_and_no_engram(self):
        g = kn.build_graph({}, {})
        self.assertEqual([n["id"] for n in g["nodes"]], ["you"])
        self.assertIsNone(g["start"])


class WriteTests(unittest.TestCase):
    def test_forget_everywhere_removes_fact_and_its_engram_observations(self):
        with mock.patch.object(kn.mm, "forget") as forget, mock.patch.object(kn.mm, "_engram", return_value="ok") as eng:
            gone = kn.forget_everywhere("fact:wishes/buy_guitar", EXPORT)
        forget.assert_called_once_with("buy_guitar", "wishes")
        self.assertEqual(gone, 2)
        self.assertEqual(sorted(c.args[1] for c in eng.call_args_list), ["4", "5"])
        self.assertTrue(all(c.args[0] == "delete" and "--hard" in c.args for c in eng.call_args_list))

    def test_correct_goes_through_update_memory(self):
        with mock.patch.object(kn.mm, "update_memory") as upd:
            kn.correct("fact:identity/name", "  Pablo R. ")
            kn.correct("fact:bogus/x", "nope")
            kn.correct("fact:identity/name", "   ")
        upd.assert_called_once_with({"identity": {"name": {"value": "Pablo R."}}})


class PinTests(unittest.TestCase):
    def test_pin_is_hashed_checked_and_cleared(self):
        path = Path(tempfile.mkdtemp()) / "api_keys.json"
        path.write_text(json.dumps({"provider": "gemini"}), encoding="utf-8")
        with mock.patch.object(config, "_CONFIG_PATH", path):
            self.assertFalse(kn.has_pin())
            self.assertFalse(kn.set_pin("12"))   # too short
            self.assertTrue(kn.set_pin("4821"))
            self.assertNotIn("4821", path.read_text())
            self.assertTrue(kn.check_pin("4821"))
            self.assertFalse(kn.check_pin("4822"))
            kn.clear_pin()
            self.assertFalse(kn.has_pin())
            self.assertEqual(json.loads(path.read_text())["provider"], "gemini")
        with mock.patch.object(config, "_CONFIG_PATH", path.with_name("missing.json")):
            self.assertFalse(kn.has_pin())


if __name__ == "__main__":
    unittest.main()
