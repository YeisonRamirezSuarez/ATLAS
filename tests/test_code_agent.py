"""Offline checks for actions/code_agent.py: the Claude Code run is mocked, no network.

Run:  python -m unittest discover tests
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage
except ImportError as e:
    raise unittest.SkipTest(f"claude-agent-sdk missing: {e}")

from actions import code_agent as ca


def _fake_run(*_, **__):
    async def gen():
        yield AssistantMessage([ToolUseBlock("toolu_main01", "Agent", {"description": "Review ui.py"})], "m",
                               usage={"input_tokens": 5, "cache_read_input_tokens": 90_000,
                                      "cache_creation_input_tokens": 30_000}, message_id="msg_1")
        yield AssistantMessage([ToolUseBlock("toolu_sub001", "Bash", {"command": "pytest -q"})], "m",
                               parent_tool_use_id="toolu_main01")
        yield UserMessage([ToolResultBlock("toolu_main01", "2 issues")])
        yield AssistantMessage([TextBlock("All done")], "m",
                               usage={"input_tokens": 5, "cache_read_input_tokens": 125_000}, message_id="msg_2")
        yield ResultMessage("success", 1, 1, False, 3, "sess-1", result="Reviewed: 2 issues. Fix them?",
                            usage={"cache_read_input_tokens": 215_000, "output_tokens": 900},
                            model_usage={"claude-sonnet-5-5": {"cacheReadInputTokens": 40_000, "outputTokens": 300}})
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
             mock.patch("agent.llm.get_config", return_value={}), \
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
        run = ca._runs["r1"]
        self.assertEqual((run["session"], run["folder"], run["tasks"]), ("sess-1", self.tmp.name, ["review the code"]))
        self.assertEqual(run["context"], 125_005)   # the main thread's last turn, not a subagent's
        log = (Path(self.tmp.name) / "r1.log").read_text(encoding="utf-8")
        self.assertIn("Bash: pytest -q", log)
        self.assertIn("USAGE: 2 main turns, 1 subagents, main context last 125k / peak 125k", log)
        self.assertIn("claude-sonnet-5-5", log)
        opts = q.call_args.kwargs["options"]
        self.assertIn("Agent", opts.tools)
        self.assertEqual(opts.permission_mode, "bypassPermissions")

    def test_status_says_what_each_running_subagent_is_doing(self):
        seen = []
        player = mock.Mock()
        player.set_job.side_effect = lambda row, goal, step: step == "Bash: pytest -q" and seen.append(ca.status())
        with mock.patch("claude_agent_sdk.query", side_effect=_fake_run), \
             mock.patch("agent.self_improve._model", return_value=("claude-opus-5-5", {})), \
             mock.patch("agent.llm.get_config", return_value={}), mock.patch("agent.llm.trust_folder"):
            ca._work("r1", "review the code", self.tmp.name, None, player, None)
        self.assertIn("Run r1", seen[0])
        self.assertIn("└ subagente: Review ui.py: Bash: pytest -q", seen[0])
        with mock.patch.object(ca, "_claude_roots", return_value=[]):
            idle = ca.code_agent({"action": "status"})
        self.assertEqual(ca.running(), [])
        self.assertIn("No code_agent run launched by ATLAS", idle)
        self.assertIn("No live Claude Code session either", idle)

    def test_a_background_subagent_keeps_its_row_until_its_task_ends(self):
        from claude_agent_sdk import TaskNotificationMessage, TaskProgressMessage, TaskStartedMessage

        def bg_run(*_, **__):
            async def gen():
                yield AssistantMessage([ToolUseBlock("toolu_bg0001", "Agent", {"description": "Council: taste"})], "m")
                yield UserMessage([ToolResultBlock("toolu_bg0001", "Async agent launched successfully.")])
                yield TaskStartedMessage("task_started", {}, "t1", "Council: taste", "u", "s", "toolu_bg0001")
                yield TaskProgressMessage("task_progress", {}, "t1", "Council: taste", {}, "u", "s", "toolu_bg0001", "Bash")
                yield ResultMessage("success", 1, 1, False, 1, "sess-2", result="waiting")   # main turn ends first
                yield TaskNotificationMessage("task_notification", {}, "t1", "completed", "o", "done", "u", "s")
                yield ResultMessage("success", 1, 1, False, 1, "sess-2", result="ok")
            return gen()

        player = mock.Mock()
        with mock.patch("claude_agent_sdk.query", side_effect=bg_run), \
             mock.patch("agent.self_improve._model", return_value=("claude-opus-5-5", {})), \
             mock.patch("agent.llm.get_config", return_value={}), mock.patch("agent.llm.trust_folder"):
            ca._work("r1", "design", self.tmp.name, None, player, None)
        rows = [c.args for c in player.set_job.call_args_list if c.args[0] == "r1/bg0001"]
        self.assertEqual([r[2] for r in rows], ["empezando", "Bash", ""])   # not removed when launched
        log = (Path(self.tmp.name) / "r1.log").read_text(encoding="utf-8")
        self.assertIn("en segundo plano", log)
        self.assertIn("terminado (completed)", log)

    def test_a_continued_run_reports_its_real_result_whatever_the_shapes(self):
        # run 113435: the session finished fine, then "AttributeError: 'list' object has no attribute 'get'"
        # replaced its report. Lists where dicts were expected, block lists as result, empty trailing results.
        from claude_agent_sdk import TaskNotificationMessage, TaskStartedMessage

        def odd_run(*_, **__):
            async def gen():
                yield AssistantMessage([ToolUseBlock("toolu_bg0002", "Agent", {"description": "Fix map"})], "m",
                                       usage=[{"input_tokens": 7, "cache_read_input_tokens": 1000}], message_id="a")
                yield TaskStartedMessage("task_started", {}, "t2", "Fix map", "u", "s", "toolu_bg0002")
                yield ResultMessage("success", 1, 1, False, 2, "sess-3",
                                    result=[{"type": "text", "text": "Fixed search and states."},
                                            {"type": "tool_use", "name": "Bash"}, TextBlock("Question: keep it?")],
                                    usage=[{"output_tokens": 50}], model_usage=[{"outputTokens": 1}])
                n = TaskNotificationMessage("task_notification", {}, "t2", None, "o", "done", "u", "s")
                n.patch = [{"status": "completed"}]
                yield n
                bad = TaskNotificationMessage("task_notification", {}, "t3", "completed", "o", "done", "u", "s")
                bad.task_id = ["t3"]   # unhashable: reading it raises, the run must go on
                yield bad
                yield ResultMessage("success", 1, 1, False, 2, "sess-3", result="")   # trailing background turn
            return gen()

        ca._runs["r1"] = {"session": "sess-3", "folder": self.tmp.name, "tasks": ["build it"], "ended": time.time()}
        player, delivered = mock.Mock(), []
        with mock.patch("claude_agent_sdk.query", side_effect=odd_run), \
             mock.patch("agent.self_improve._model", return_value=("claude-opus-5-5", {})), \
             mock.patch("agent.llm.get_config", return_value={}), mock.patch("agent.llm.trust_folder"):
            ca._work("r1", "fix the review", self.tmp.name, "sess-3", player, delivered.append)
        self.assertNotIn("FAILED", delivered[0])
        self.assertIn("Fixed search and states.\nQuestion: keep it?", delivered[0])
        self.assertEqual(ca._runs["r1"]["tasks"], ["build it", "fix the review"])
        log = (Path(self.tmp.name) / "r1.log").read_text(encoding="utf-8")
        self.assertIn("terminado (completed)", log)          # status read from a list-shaped patch
        self.assertIn("skipped a TaskNotificationMessage", log)
        self.assertIn("RESULT: Fixed search", log)

    def test_result_text_and_fields_from_any_shape(self):
        self.assertEqual(ca._text([{"type": "text", "text": "a"}, {"type": "image"}, "b"]), "a\nb")
        self.assertEqual(ca._text({"result": [TextBlock("x")]}), "x")
        self.assertEqual(ca._text(None), "")
        self.assertEqual(ca._get([{}, {"status": "failed"}], "status"), "failed")
        self.assertEqual(ca._get(None, "status", "?"), "?")

    def test_runs_auto_compact_and_cheap_subagents(self):
        _, _, q = self._go()
        opts = q.call_args.kwargs["options"]
        self.assertEqual(opts.env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "100000")   # the 1M window never compacted
        self.assertEqual(opts.env["CLAUDE_CODE_SUBAGENT_MODEL"], "sonnet")
        self.assertGreater(opts.max_buffer_size, 1 << 20)                         # big screenshots killed runs
        self.assertIn("tail", ca.SYSTEM)

    def test_settings_override_or_turn_off_the_subagent_model(self):
        with mock.patch("agent.llm.get_config", return_value={"code_agent_subagent_model": "inherit",
                                                              "code_agent_compact_window": 400000}):
            env = ca._env()
        self.assertEqual(env, {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "400000"})

    def test_a_big_idle_session_goes_on_fresh_from_a_handoff(self):
        self._go()
        ca._runs["r1"]["ended"] -= 2 * 3600   # past the prompt cache
        with mock.patch.object(ca.threading, "Thread") as t:
            ca.code_agent({"task": "yes, fix them", "continue_id": "r1"})
        args = t.call_args.kwargs["args"]
        self.assertEqual((args[1], args[3]), ("yes, fix them", None))   # same task recorded, no resume
        prompt = args[6]
        for part in ("review the code", "Fix them?", "Bash: pytest -q", "NEW TASK:\nyes, fix them", "git status"):
            self.assertIn(part, prompt)
        self.assertIn("NEW SESSION with a handoff", (Path(self.tmp.name) / "r1.log").read_text(encoding="utf-8"))

    def test_when_to_resume_and_when_to_hand_off(self):
        now = 10_000.0
        self.assertEqual(ca._stale({"context": 110_000, "ended": now - 60}, now), "")      # cache still warm
        self.assertEqual(ca._stale({"context": 50_000, "ended": now - 9999}, now), "")     # small: resume is cheap
        self.assertIn("idle", ca._stale({"context": 110_000, "ended": now - 3600}, now))
        self.assertIn("400k", ca._stale({"context": 400_000, "ended": now - 60}, now))
        self.assertIn("usage limit", ca._stale({"context": 110_000, "ended": now - 60,
                                                "result": "FAILED: You've hit your session limit"}, now))

    def test_continue_resumes_the_same_session(self):
        self._go()
        with mock.patch.object(ca.threading, "Thread") as t:
            msg = ca.code_agent({"task": "yes, fix them", "continue_id": "r1"})
        self.assertIn("r1", msg)
        self.assertEqual(t.call_args.kwargs["args"][2:4], (str(Path(self.tmp.name).resolve()), "sess-1"))
        self.assertIsNone(t.call_args.kwargs["args"][6])   # warm session: plain resume, no handoff

    def test_needs_a_real_folder(self):
        self.assertIn("FAILED", ca.code_agent({"task": "x", "folder": ""}))
        self.assertIn("FAILED", ca.code_agent({"task": "x", "folder": str(Path(self.tmp.name) / "nope")}))
        self.assertIn("FAILED", ca.code_agent({"task": "x", "continue_id": "unknown"}))


def _entry(kind: str, content, **extra) -> str:
    return json.dumps({"type": kind, "message": {"role": kind, "content": content}, **extra})


class LiveSessionsTest(unittest.TestCase):
    """status() also reads Claude Code transcripts ATLAS did not launch (a terminal session, other profiles)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "projects"
        self.now = time.time()
        proj = self.root / "C--Users-WPOSS-Documents-WDS-REPOSITORIOS-VendigMachine"
        sub = proj / "s-vending" / "subagents"
        sub.mkdir(parents=True)
        cwd = "C:/Users/WPOSS/Documents/WDS/REPOSITORIOS/VendigMachine/polaris-vmc"
        (proj / "s-vending.jsonl").write_text("\n".join([
            '{"type": "ai-title", "aiTitle": "Motivo row in visit detail"}',
            _entry("user", "dale agrega la fila motivo", cwd=cwd),
            _entry("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}], cwd=cwd),
            _entry("user", "<task-notification>agent done</task-notification>", cwd=cwd),
            _entry("assistant", [{"type": "text", "text": "Specs pass; an agent checks the row in Chrome."},
                                 {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "npm run build"}}],
                   cwd=cwd),
        ]), encoding="utf-8")
        (sub / "agent-a1.jsonl").write_text(_entry("assistant", [
            {"type": "tool_use", "id": "t3", "name": "Bash", "input": {"description": "Open Chrome at 390 px"}}]),
            encoding="utf-8")
        (sub / "agent-a1.meta.json").write_text('{"description": "E2E sweep of the Motivo row"}', encoding="utf-8")
        (sub / "agent-a2.jsonl").write_text(_entry("assistant", [{"type": "text", "text": "DONE: 382/382 specs"}]),
                                            encoding="utf-8")
        (sub / "agent-a2.meta.json").write_text('{"description": "Run visit specs", "finished": true}',
                                                encoding="utf-8")
        old = self.root / "C--old-project"
        old.mkdir()
        (old / "s-old.jsonl").write_text(_entry("user", "old work"), encoding="utf-8")
        os.utime(old / "s-old.jsonl", (self.now - 5 * 3600, self.now - 5 * 3600))

    def test_status_reports_sessions_started_outside_atlas(self):
        out = ca.status([self.root, self.root], self.now)   # a linked profile lists the same session twice
        self.assertIn("No code_agent run launched by ATLAS", out)
        self.assertEqual(out.count("Session s-vendin"), 1)
        for part in ("VendigMachine/polaris-vmc", "Motivo row in visit detail", "dale agrega la fila motivo",
                     "Now: Bash: npm run build", "Running subagents: E2E sweep of the Motivo row",
                     "Open Chrome at 390 px", "Finished subagents: Run visit specs", "382/382", "working"):
            self.assertIn(part, out)
        for noise in ("task-notification", "tool_result", "old work", "old-project"):
            self.assertNotIn(noise, out)

    def test_nothing_live_says_so(self):
        self.assertIn("No live Claude Code session", ca.sessions([self.root], self.now + 3 * 3600))

    def test_reads_only_the_end_of_a_huge_transcript(self):
        f = next(self.root.glob("*/s-vending.jsonl"))
        last = f.read_bytes().split(b"\n")[-1]
        entries = ca._tail(f, len(last) + 40)   # the cut first line is dropped, not mis-parsed
        self.assertTrue(entries and all(isinstance(e, dict) for e in entries))
        self.assertEqual(entries[-1]["type"], "assistant")


if __name__ == "__main__":
    unittest.main()
