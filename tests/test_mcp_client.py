"""MCP client: off by default, allowlist only, a down server fails alone. Uses a tiny local stdio server."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

from agent import executor, mcp_client

SERVER = '''
from mcp.server.mcpserver import MCPServer
s = MCPServer("demo")

@s.tool()
def add(a: int, b: int) -> int:
    """Adds two numbers."""
    return a + b

@s.tool()
def slow() -> str:
    """Hangs."""
    import time
    time.sleep(60)
    return "late"

@s.tool()
def wipe() -> str:
    """Never allowed."""
    return "wiped"

s.run()
'''


class MCPClientTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        (self.dir / "server.py").write_text(SERVER, encoding="utf-8")
        p = mock.patch.object(mcp_client, "CONFIG_PATH", self.dir / "mcp_servers.json")
        p.start()
        self.addCleanup(p.stop)

    def config(self, **demo):
        spec = {"enabled": True, "command": sys.executable, "args": [str(self.dir / "server.py")],
                "allowed_tools": ["add"], **demo}
        mcp_client.CONFIG_PATH.write_text(json.dumps({"servers": {"demo": spec}}), encoding="utf-8")

    def test_no_file_or_disabled_means_no_servers(self):
        self.assertEqual((mcp_client.servers(), mcp_client.hint()), ({}, ""))
        self.config(enabled=False)
        self.assertEqual(mcp_client.servers(), {})
        with self.assertRaises(ValueError):
            mcp_client.call("demo", "add", {"a": 1, "b": 2})
        mcp_client.CONFIG_PATH.write_text("{oops", encoding="utf-8")
        self.assertEqual(mcp_client.servers(), {})

    def test_allowed_tool_runs_over_stdio_and_others_are_refused(self):
        self.config()
        out = executor._call_tool("mcp", {"server": "demo", "tool": "add", "arguments": {"a": 2, "b": 3}}, None)
        self.assertIn("UNTRUSTED", out)
        self.assertEqual(out.splitlines()[-1], "5")
        listing = executor._call_tool("mcp", {"server": "demo"}, None)
        self.assertIn("- add: Adds two numbers.", listing)
        self.assertNotIn("wipe", listing)
        with self.assertRaises(PermissionError):
            mcp_client.call("demo", "wipe")
        self.assertIn("demo: add", mcp_client.hint())

    def test_down_server_fails_only_its_call(self):
        self.config(command=str(self.dir / "missing.exe"), args=[])
        t0 = time.monotonic()
        with self.assertRaises(RuntimeError) as e:
            mcp_client.call("demo", "add", {"a": 1, "b": 1})
        self.assertIn("unavailable", str(e.exception))
        self.assertLess(time.monotonic() - t0, mcp_client.TIMEOUT + 5)
        self.assertIn("mcp: demo", executor.tool_status())

    def test_hanging_tool_times_out_alone(self):
        self.config(allowed_tools=["slow", "add"])
        t0 = time.monotonic()
        with mock.patch.object(mcp_client, "TIMEOUT", 3), self.assertRaises(RuntimeError) as e:
            mcp_client.call("demo", "slow")
        self.assertIn("unavailable", str(e.exception))
        self.assertLess(time.monotonic() - t0, 20)
        self.assertEqual(mcp_client.call("demo", "add", {"a": 1, "b": 1}).splitlines()[-1], "2")   # next call fine

    def test_permissions_are_read_again_on_every_call(self):
        self.config()
        self.assertEqual(mcp_client.call("demo", "add", {"a": 1, "b": 1}).splitlines()[-1], "2")
        self.config(allowed_tools=[])
        with self.assertRaises(PermissionError):
            mcp_client.call("demo", "add", {"a": 1, "b": 1})
        self.config(enabled=False)
        with self.assertRaises(ValueError):
            executor._call_tool("mcp", {"server": "demo", "tool": "add", "arguments": {"a": 1, "b": 1}}, None)


if __name__ == "__main__":
    unittest.main()
