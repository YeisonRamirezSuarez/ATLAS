"""Offline checks for actions/code_agent.py: the Claude Code run is mocked, no network.

Run:  python -m unittest discover tests
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage

from actions import code_agent as ca


def _fake_run(*_, **__):
    async def gen():
        yield AssistantMessage([ToolUseBlock("toolu_main01", "Agent", {"description": "Review ui.py"})], "m")
        yield AssistantMessage([ToolUseBlock("toolu_sub001", "Bash", {"command": "pytest -q"})], "m",
                               parent_tool_use_id="toolu_main01")
        yield UserMessage([ToolResultBlock("toolu_main01", "2 issues")])
        yield AssistantMessage([TextBlock("All done")], "m")
        yield ResultMessage("success", 1, 1, False, 3, "sess-1", result="Reviewed: 2 issues. Fix them?")
    return gen()


class CodeAgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(ca, "LOG_DIR", Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        ca._runs.clear()

    def _go(self, **params):
        player, delivered = mock.Mock(), []
        with mock.patch("claude_agent_sdk.query", side_effect=_fake_run) as q, \
             mock.patch("agent.self_improve._model", return_value=("claude-opus-5-5", {})), \
             mock.patch("agent.llm.trust_folder") as trust:
            ca._work(params.get("rid", "r1"), "review the code", self.tmp.name, params.get("session"),
                     player, delivered.append)
        trust.assert_called_once_with(self.tmp.name)   # Claude Code would ignore an untrusted project's settings
        return player, delivered, q

    def test_subagents_get_their_own_row_with_live_steps(self):
        player, delivered, q = self._go()
        rows = [c.args for c in player.set_job.call_args_list]
        sub = "r1/main01"
        self.assertIn((sub, "└ subagente: Review ui.py", "empezando"), rows)
        self.assertIn((sub, "└ subagente: Review ui.py", "Bash: pytest -q"), rows)   # the subagent's step, on its row
        self.assertIn((sub, "", ""), rows)                                           # gone once it returned
        self.assertEqual(rows[-1], ("r1", "", ""))
        self.assertIn("Fix them?", delivered[0])
        self.assertIn("continue_id='r1'", delivered[0])
        self.assertEqual(ca._runs["r1"], {"session": "sess-1", "folder": self.tmp.name})
        self.assertIn("Bash: pytest -q", (Path(self.tmp.name) / "r1.log").read_text(encoding="utf-8"))
        opts = q.call_args.kwargs["options"]
        self.assertIn("Agent", opts.tools)
        self.assertEqual(opts.permission_mode, "bypassPermissions")

    def test_continue_resumes_the_same_session(self):
        self._go()
        with mock.patch.object(ca.threading, "Thread") as t:
            msg = ca.code_agent({"task": "yes, fix them", "continue_id": "r1"})
        self.assertIn("r1", msg)
        self.assertEqual(t.call_args.kwargs["args"][2:4], (str(Path(self.tmp.name).resolve()), "sess-1"))

    def test_needs_a_real_folder(self):
        self.assertIn("FAILED", ca.code_agent({"task": "x", "folder": ""}))
        self.assertIn("FAILED", ca.code_agent({"task": "x", "folder": str(Path(self.tmp.name) / "nope")}))
        self.assertIn("FAILED", ca.code_agent({"task": "x", "continue_id": "unknown"}))


if __name__ == "__main__":
    unittest.main()
