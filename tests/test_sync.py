"""Memory sync between two devices against a fake PostgREST server; nothing leaves this PC."""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory import atlas_id, memory_manager, sync


class FakeServer:
    """The parts of PostgREST + the migration's rules that sync uses."""

    def __init__(self):
        self.rows, self.clock, self.online = {}, datetime(2026, 10, 26, tzinfo=timezone.utc), True

    def _tick(self):
        self.clock += timedelta(seconds=1)
        return self.clock.isoformat()

    def rest(self, method, path, headers=None, json=None):
        if not self.online:
            raise atlas_id.IDError("ATLAS ID offline (ConnectionError)")
        q = {k: v[0] for k, v in parse_qs(urlparse(path).query.replace("%2B", "+").replace("+", "%2B")).items()}
        q = {k: v.replace("%2B", "+") for k, v in q.items()}
        fid = q.get("id", "").removeprefix("eq.")
        if method == "GET":
            rows = [self.rows[fid]] if fid else sorted(self.rows.values(), key=lambda r: r["updated_at"])
            if "updated_at" in q:
                since = datetime.fromisoformat(q["updated_at"].removeprefix("gte."))
                rows = [r for r in rows if datetime.fromisoformat(r["updated_at"]) >= since]
            return [dict(r) for r in rows if r]
        if method == "POST":
            if json["id"] in self.rows:
                err = atlas_id.IDError("duplicate key")
                err.status = 409
                raise err
            self.rows[json["id"]] = {**json, "updated_at": self._tick()}
            return [dict(self.rows[json["id"]])]
        if method == "PATCH":
            row = self.rows.get(fid)
            if not row or row["version"] != int(q["version"].removeprefix("eq.")):
                return []
            row.update(json, updated_at=self._tick())
            return [dict(row)]


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.server = FakeServer()
        self.patches = [
            mock.patch.object(sync, "enabled", return_value=True),
            mock.patch.object(memory_manager, "_engram_save_async"),
            mock.patch.object(atlas_id, "rest", side_effect=self.server.rest),
            mock.patch.object(atlas_id, "touch_device", return_value=True),
            mock.patch.object(atlas_id, "status", return_value={"user_id": "u-1"}),
            mock.patch.object(atlas_id, "device_id", return_value="00000000-0000-0000-0000-00000000000a"),
        ]
        for p in self.patches:
            p.start()
        self.device("a")

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def device(self, name):
        """Switch to device `name`: its own long_term.json and sync queue."""
        for p in getattr(self, "_dev", []):
            p.stop()
        self._dev = [mock.patch.object(memory_manager, "MEMORY_PATH", self.dir / f"{name}.json"),
                     mock.patch.object(sync, "DB_PATH", self.dir / f"{name}.db")]
        for p in self._dev:
            p.start()

    def fact(self, category, key):
        return memory_manager.load_memory()[category].get(key, {}).get("value")

    def test_offline_change_waits_then_reaches_the_other_device(self):
        memory_manager.remember("city", "Bucaramanga", "identity")
        self.server.online = False
        self.assertIn("FALLÓ", sync.sync_now())
        self.assertEqual(sync.stats()["pending"], 1)   # not marked as synced
        self.server.online = True
        self.assertIn("1 enviados", sync.sync_now())
        self.assertEqual(sync.stats()["pending"], 0)
        self.device("b")
        self.assertIn("1 recibidos", sync.sync_now())
        self.assertEqual(self.fact("identity", "city"), "Bucaramanga")
        memory_manager.forget("city", "identity")
        sync.sync_now()
        self.device("a")
        sync.sync_now()
        self.assertIsNone(self.fact("identity", "city"))

    def test_conflict_keeps_server_copy_and_saves_local_one(self):
        memory_manager.remember("food", "pizza", "preferences")
        sync.sync_now()
        self.device("b")
        sync.sync_now()
        memory_manager.remember("food", "sushi", "preferences")
        sync.sync_now()   # b wins the race: version 2
        self.device("a")
        memory_manager.remember("food", "tacos", "preferences")   # edited version 1
        self.assertIn("1 conflictos", sync.sync_now())
        self.assertEqual(self.fact("preferences", "food"), "sushi")
        self.assertEqual(sync.recent_conflicts()[0][:3], ("preferences/food", "tacos", "sushi"))
        self.assertEqual(self.server.rows["preferences/food"]["version"], 2)

    def test_secrets_and_odd_keys_never_leave_this_pc(self):
        memory_manager.remember("wifi_password", "hunter2", "notes")
        memory_manager.remember("openai", "sk-abcdefghijklmnop1234", "notes")
        memory_manager.remember("Bad Key!", "x", "notes")
        memory_manager.remember("dog", "Toby", "relationships")
        sync.sync_now()
        self.assertEqual(list(self.server.rows), ["relationships/dog"])

    def test_first_sync_sends_facts_saved_before_sync_was_on(self):
        with mock.patch.object(sync, "enabled", return_value=False):
            memory_manager.remember("job", "developer", "identity")
        self.assertEqual(sync.stats()["pending"], 0)
        self.assertIn("1 enviados", sync.sync_now())

    def test_describe_tells_the_real_state(self):
        with mock.patch.object(atlas_id, "status", return_value={"configured": False, "signed_in": False}):
            self.assertIn("sin configurar", sync.describe())
        memory_manager.remember("dog", "Toby", "relationships")
        with mock.patch.object(atlas_id, "status",
                               return_value={"configured": True, "signed_in": True, "email": "a@b.c", "user_id": "u-1"}):
            self.assertIn("1 pendientes", sync.describe())
            sync.sync_now()
            self.assertIn("1 sincronizados, 0 pendientes", sync.describe())

    def test_removed_device_signs_out(self):
        with mock.patch.object(atlas_id, "touch_device", return_value=False), \
             mock.patch.object(atlas_id, "logout") as logout:
            self.assertIn("removed", sync.sync_now())
        logout.assert_called_once()


if __name__ == "__main__":
    unittest.main()
