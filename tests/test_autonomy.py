"""Persistent goals, honest outcomes and loop guards. Offline: the model and the tools are scripted."""
import json
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

from agent import executor, llm
from agent.task_queue import TaskQueue, TaskStatus
from memory import memory_manager


def run(replies, tools=None, state=None, pause=None):
    """Runs the real ReAct loop on scripted replies; returns (result, state, prompts, calls)."""
    replies, prompts, calls = list(replies), [], []
    state = {} if state is None else state

    def fake_llm(prompt, system="", **kw):
        prompts.append(prompt)
        return replies.pop(0)

    def fake_tool(tool, params, speak, approved=False):
        calls.append(tool + (" (approved)" if approved else ""))
        out = (tools or {}).get(tool, "ok")
        if isinstance(out, Exception):
            raise out
        if pause and len(calls) == 1:
            pause.set()
        return out

    with mock.patch.object(llm, "complete_json", side_effect=fake_llm), \
         mock.patch.object(executor, "_call_tool", side_effect=fake_tool), \
         mock.patch.object(memory_manager, "load_memory", return_value={}), \
         mock.patch.object(memory_manager, "find_skills", return_value=[]):
        result = executor.AgentExecutor().execute("the goal", state=state, pause_flag=pause)
    return result, state, prompts, calls


class OutcomeTests(unittest.TestCase):
    def test_outcome_says_what_was_checked(self):
        cases = [({"final": "Done.", "done": True}, "verified"),
                 ({"final": "Done."}, "unverified"),
                 ({"final": "Could not.", "done": False}, "failed"),
                 ({"thought": "x"}, "failed")]
        for reply, outcome in cases:
            self.assertEqual(run([reply])[1]["outcome"], outcome, reply)

    def test_shortcut_final_is_unverified_and_blocked_is_kept(self):
        act = {"thought": "go", "actions": [{"tool": "open_app", "parameters": {}}], "final": "Open."}
        self.assertEqual(run([act])[1]["outcome"], "unverified")
        _, state, _, _ = run([act], tools={"open_app": executor.NeedsConfirmation("NEEDS CONFIRMATION. ask")})
        self.assertEqual(state["outcome"], "blocked")
        self.assertIn("NEEDS CONFIRMATION (asked the user)", state["history"][-1])
        self.assertEqual(state["awaiting"], {"tool": "open_app", "parameters": {}})

    def test_plan_is_saved(self):
        _, state, _, _ = run([{"plan": ["search", "answer"], "final": "ok", "done": True}])
        self.assertEqual(state["plan"], ["search", "answer"])


class LoopGuardTests(unittest.TestCase):
    def test_same_failing_call_is_refused_after_the_limit(self):
        same = {"thought": "again", "actions": [{"tool": "web_search", "parameters": {"query": "x"}}]}
        stop = {"final": "Gave up.", "done": False}
        _, state, _, calls = run([same] * (executor.MAX_SAME_FAILURE + 1) + [stop],
                                 tools={"web_search": RuntimeError("offline")})
        self.assertEqual(len(calls), executor.MAX_SAME_FAILURE)   # the third identical try never ran
        self.assertIn("Not run: this exact action already failed", state["history"][-1])


class ResumeTests(unittest.TestCase):
    def test_pause_keeps_done_actions_and_resume_does_not_repeat_them(self):
        pause = threading.Event()
        two = {"thought": "both", "actions": [{"tool": "file_controller", "parameters": {}},
                                              {"tool": "open_app", "parameters": {}}]}
        state = {}
        with self.assertRaises(executor.Paused):
            run([two], state=state, pause=pause)
        self.assertEqual(len(state["history"]), 1)
        pause.clear()
        result, state, prompts, calls = run([{"final": "Done.", "done": True}], state=state)
        self.assertEqual(calls, [])                              # nothing re-run on resume
        self.assertIn("file_controller", prompts[0])             # the model sees what already happened
        self.assertEqual((result, state["outcome"]), ("Done.", "verified"))

    def test_turn_cap_counts_turns_from_before_the_pause(self):
        state = {"history": [f"Turn {i}: x" for i in range(1, executor.MAX_TURNS)], "brief": []}
        loop = {"thought": "again", "actions": [{"tool": "web_search", "parameters": {"q": 1}}]}
        result, state, prompts, _ = run([loop], state=state)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(state["outcome"], "failed")

    def test_old_turns_reach_the_prompt_in_brief_form(self):
        history = [f"Turn {i}: " + "long output " * 50 for i in range(10)]
        brief = [f"Turn {i}: short" for i in range(10)]
        text = executor._compact(history, brief)
        self.assertIn("Turn 0: short", text)
        self.assertNotIn("Turn 9: short", text)
        self.assertEqual(text.count("long output"), 50 * executor._FULL_TURNS)


class TaskQueueTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "tasks.json"

    def test_unfinished_tasks_come_back_paused_and_never_run_alone(self):
        q = TaskQueue(path=self.path)
        tid = q.submit("long goal")
        q._tasks[tid].state["history"] = ["Turn 1: did a thing"]
        q._save()
        q2 = TaskQueue(path=self.path)
        status = q2.get_status(tid)
        self.assertEqual((status["status"], status["turns"]), ("paused", 1))
        self.assertIn("INTERRUPTED", status["step"])
        self.assertEqual(q2._queue, [])

    def test_unreadable_file_starts_empty_without_touching_it(self):
        self.path.write_text("{broken", encoding="utf-8")
        self.assertEqual(TaskQueue(path=self.path).get_all_statuses(), [])
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")

    def test_pause_resume_and_reported_outcome(self):
        reports = []

        class Exec:
            def execute(self, goal, state, **kw):
                state["outcome"] = "failed"
                return "Could not do it."
        q = TaskQueue(path=self.path)
        q._executor = Exec()
        tid = q.submit("goal", on_complete=lambda t, r: reports.append(r))
        self.assertTrue(q.pause(tid))
        self.assertEqual((q.get_status(tid)["status"], q._queue), ("paused", []))
        self.assertTrue(q.resume(tid))
        q._run_task(q._tasks[tid])
        self.assertEqual(q.get_status(tid)["status"], "failed")
        self.assertEqual(reports, [f"[{executor.OUTCOMES['failed']}] Could not do it."])
        self.assertFalse(q.pause(tid))
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved[0]["status"], "failed")

    def test_paused_run_is_not_reported_as_finished(self):
        class Exec:
            def execute(self, **kw):
                raise executor.Paused()
        q = TaskQueue(path=self.path)
        q._executor = Exec()
        tid = q.submit("goal", on_complete=lambda *a: self.fail("reported"))
        q._run_task(q._tasks[tid])
        self.assertEqual(q._tasks[tid].status, TaskStatus.PAUSED)


class ConfirmationTests(unittest.TestCase):
    """The task agent never confirms for the user: risky steps stop the task until a real yes."""

    def setUp(self):
        self.got = []
        fake = types.ModuleType("fake_tools")
        fake.tool = lambda parameters, player=None, **kw: self.got.append(parameters) or self.reply
        self.reply = "Done."
        for p in (mock.patch.dict(sys.modules, {"fake_tools": fake}),
                  mock.patch.dict(executor._TOOLS, {t: ("fake_tools", "tool", ()) for t in
                                  ("send_message", "file_controller", "computer_settings", "game_updater", "open_app")}),
                  mock.patch.object(memory_manager, "is_trusted", side_effect=lambda case: case == "saved case")):
            p.start()
            self.addCleanup(p.stop)

    def test_risky_steps_ask_even_when_the_model_says_confirmed(self):
        cheat = {"confirmed": "yes", "_trusted": True, "trusted": "made up"}
        for tool, params in [("send_message", {"receiver": "Ana", "message": "hi"}),
                             ("file_controller", {"action": "delete", "path": "x"}),
                             ("file_controller", {"action": "move", "path": "x"}),
                             ("file_controller", {"action": "organize_desktop"}),
                             ("computer_settings", {"action": "shutdown"}),
                             ("computer_settings", {"action": "close_app"}),
                             ("game_updater", {"action": "update", "shutdown_when_done": "true"})]:
            with self.assertRaises(executor.NeedsConfirmation, msg=(tool, params)):
                executor._call_tool(tool, {**params, **cheat}, None)
        self.assertEqual(self.got, [])

    def test_safe_steps_run_without_the_models_confirmation_fields(self):
        executor._call_tool("file_controller", {"action": "write", "confirmed": "yes", "_trusted": True}, None)
        self.assertEqual(self.got, [{"action": "write"}])

    def test_user_yes_or_saved_trusted_case_lets_the_step_run(self):
        executor._call_tool("send_message", {"receiver": "Ana"}, None, approved=True)
        executor._call_tool("send_message", {"receiver": "Ana", "trusted": "saved case"}, None)
        self.assertEqual(self.got, [{"receiver": "Ana", "confirmed": "yes"}, {"receiver": "Ana", "_trusted": True}])

    def test_tool_that_asks_on_its_own_blocks_the_task(self):
        self.reply = "This will restart the computer. Please confirm by calling again with confirmed=yes."
        with self.assertRaises(executor.NeedsConfirmation):
            executor._call_tool("computer_settings", {"description": "reboot please"}, None)

    def test_generated_code_never_gets_the_models_yes(self):
        with mock.patch.object(executor, "solve_task", return_value="ok") as solve:
            executor._call_tool("generated_code", {"description": "clean temp", "confirmed": "yes"}, None)
        self.assertEqual(solve.call_args.kwargs, {"speak": None, "confirmed": False, "trusted": False})

    def test_yes_for_one_script_does_not_run_another(self):
        risky = "# PLAN: delete temp files\nimport shutil\nshutil.rmtree('x')"
        ran = []
        with mock.patch.object(executor, "_write_script", return_value=risky), \
             mock.patch.object(executor, "_run_script", side_effect=lambda c: ran.append(c) or (True, "ok")), \
             mock.patch.object(memory_manager, "find_skills", return_value=[]), \
             mock.patch.object(memory_manager, "find_failures", return_value=[]):
            executor._pending = ("goal of another task", risky)
            with self.assertRaises(executor.NeedsConfirmation):
                executor.solve_task("clean temp", confirmed=True)
        executor._pending = None
        self.assertEqual(ran, [])

    def test_approved_step_runs_once_first_and_is_not_kept(self):
        step = {"tool": "send_message", "parameters": {"receiver": "Ana"}}
        state = {"history": ["Turn 1: asked"], "brief": ["Turn 1: asked"], "approved": step}
        result, state, prompts, calls = run([{"final": "Sent.", "done": True}], state=state)
        self.assertEqual(calls, ["send_message (approved)"])
        self.assertNotIn("approved", state)
        self.assertIn("Turn 2 (approved by the user)", prompts[0])

    def test_desktop_sandbox_refuses_deleting_and_escaping(self):
        from actions.desktop import _execute_generated_code
        for code in ("Path('x').unlink()", "Path('d').rmdir()", "Path('a').rename('b')",
                     "().__class__.__base__.__subclasses__()"):
            self.assertIn("Could not do it", _execute_generated_code(code), code)
        self.assertEqual(_execute_generated_code("print(1 + 1)"), "2")


class OverwriteAndRunTests(unittest.TestCase):
    """Replacing a file and running risky code ask first, from the brain and from a background task."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.file = self.dir / "notes.txt"
        self.file.write_text("keep me", encoding="utf-8")
        self.sentinel = self.dir / "sentinel.txt"
        self.sentinel.write_text("x", encoding="utf-8")
        self.risky = self.dir / "risky.py"
        self.risky.write_text(f"import os\nos.remove({str(self.sentinel)!r})\nprint('gone')\n", encoding="utf-8")
        p = mock.patch.object(memory_manager, "is_trusted", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def test_write_over_an_existing_file_asks_and_keeps_it(self):
        from actions.file_controller import file_controller
        for action in ("write", "create_file"):
            out = file_controller({"action": action, "path": str(self.dir), "name": "notes.txt", "content": "new"})
            self.assertTrue(out.startswith("NEEDS CONFIRMATION"), out)
            self.assertEqual(self.file.read_text(encoding="utf-8"), "keep me")
        file_controller({"action": "write", "path": str(self.dir), "name": "new.txt", "content": "a"})
        file_controller({"action": "write", "path": str(self.dir), "name": "notes.txt", "content": "+", "append": True})
        self.assertEqual((self.dir / "new.txt").read_text(encoding="utf-8"), "a")
        self.assertEqual(self.file.read_text(encoding="utf-8"), "keep me+")
        file_controller({"action": "write", "path": str(self.dir), "name": "notes.txt", "content": "new", "confirmed": "yes"})
        self.assertEqual(self.file.read_text(encoding="utf-8"), "new")

    def test_background_task_stops_before_overwriting(self):
        params = {"action": "write", "path": str(self.dir), "name": "notes.txt", "content": "new", "confirmed": "yes"}
        with self.assertRaises(executor.NeedsConfirmation):
            executor._call_tool("file_controller", params, None)
        self.assertEqual(self.file.read_text(encoding="utf-8"), "keep me")
        executor._call_tool("file_controller", params, None, approved=True)
        self.assertEqual(self.file.read_text(encoding="utf-8"), "new")

    def test_run_of_risky_code_asks_and_safe_code_runs(self):
        from actions.code_helper import code_helper
        out = code_helper({"action": "run", "file_path": str(self.risky)})
        self.assertTrue(out.startswith("NEEDS CONFIRMATION") and "os.remove" in out, out)
        self.assertTrue(self.sentinel.exists())
        safe = self.dir / "safe.py"
        safe.write_text("print(6 * 7)\n", encoding="utf-8")
        self.assertIn("42", code_helper({"action": "run", "file_path": str(safe)}))
        self.assertIn("gone", code_helper({"action": "run", "file_path": str(self.risky), "confirmed": "yes"}))
        self.assertFalse(self.sentinel.exists())

    def test_build_never_runs_risky_code_and_the_yes_is_for_that_file(self):
        from actions import code_helper as ch
        written = lambda *a, **k: (self.risky.read_text(encoding="utf-8"), self.risky)
        with mock.patch.object(ch, "_write", side_effect=written), mock.patch.object(ch, "_run_file") as run_file:
            out = ch.code_helper({"action": "build", "description": "clean up", "confirmed": "yes"})
            self.assertTrue(out.startswith("NEEDS CONFIRMATION"), out)
            with self.assertRaises(executor.NeedsConfirmation) as e:
                executor._call_tool("code_helper", {"action": "build", "description": "clean up"}, None)
        run_file.assert_not_called()
        self.assertTrue(self.sentinel.exists())
        self.assertEqual(e.exception.step, {"tool": "code_helper",
                                            "parameters": {"action": "run", "file_path": str(self.risky)}})
        self.assertIn("gone", executor._call_tool("code_helper", e.exception.step["parameters"], None, approved=True))


class DevAgentRiskTests(unittest.TestCase):
    """dev_agent writes a whole project with the LLM: risky code waits for the user's yes before
    installing or running, and the yes runs those exact files once."""

    def setUp(self):
        from actions import dev_agent as da
        self.da = da
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.sentinel = self.root / "sentinel.txt"
        self.sentinel.write_text("x", encoding="utf-8")
        self.code = {"main.py": f"import os\nos.remove({str(self.sentinel)!r})\nprint('gone')\n"}
        plan = {"project_name": "demo", "files": [{"path": "main.py"}], "entry_point": "main.py",
                "run_command": "python main.py", "dependencies": ["requests"]}

        def write(file_info, project_dir, **kw):
            code = self.code[file_info["path"]]
            (project_dir / file_info["path"]).write_text(code, encoding="utf-8")
            return code

        self.installs, self.present, self.real_install = [], set(), da._install_dependencies
        for p in (mock.patch.object(da, "PROJECTS_DIR", self.root),
                  mock.patch.object(da, "_plan_project", return_value=plan),
                  mock.patch.object(da, "_write_file", side_effect=write),
                  mock.patch.object(da, "_open_vscode"),
                  mock.patch.object(da, "_venv_python", return_value=sys.executable),
                  mock.patch.object(da, "_missing_dependencies",
                                    side_effect=lambda deps, d: [x for x in deps if x not in self.present]),
                  mock.patch.object(da, "_install_dependencies",
                                    side_effect=lambda d, p: self.installs.append(list(d)) or self.present.update(d) or "ok"),
                  mock.patch.object(da.time, "sleep"),
                  mock.patch.object(memory_manager, "is_trusted", return_value=False)):
            p.start()
            self.addCleanup(p.stop)

    def test_risky_project_installs_and_runs_nothing_until_yes(self):
        out = self.da.dev_agent({"description": "clean my folder", "confirmed": "yes"})
        self.assertTrue(out.startswith("NEEDS CONFIRMATION") and "os.remove" in out, out)
        self.assertTrue(self.sentinel.exists())
        self.assertEqual(self.installs, [])
        self.assertTrue(self.da.dev_agent({"action": "run", "project_name": "demo"}).startswith("NEEDS CONFIRMATION"))
        self.assertTrue(self.sentinel.exists())
        out = self.da.dev_agent({"action": "run", "project_name": "demo", "confirmed": "yes"})
        self.assertIn("gone", out)
        self.assertFalse(self.sentinel.exists())
        self.assertEqual(self.installs, [["requests"]])

    def test_safe_project_with_its_packages_present_builds_and_runs_as_before(self):
        self.code["main.py"] = "print(6 * 7)\n"
        self.present = {"requests"}
        out = self.da.dev_agent({"description": "multiply"})
        self.assertIn("is working", out)
        self.assertIn("42", out)

    def test_missing_packages_are_installed_only_after_yes(self):
        self.code["main.py"] = "print(6 * 7)\n"
        out = self.da.dev_agent({"description": "multiply", "confirmed": "yes"})
        self.assertTrue(out.startswith("NEEDS CONFIRMATION") and "install from PyPI requests" in out, out)
        self.assertEqual(self.installs, [])
        out = self.da.dev_agent({"action": "run", "project_name": "demo", "confirmed": "yes"})
        self.assertEqual(self.installs, [["requests"]])
        self.assertIn("42", out)

    def test_package_missing_at_run_time_is_asked_not_installed(self):
        self.code["main.py"] = "import notarealpkg_x\n"
        self.present = {"requests"}
        out = self.da.dev_agent({"description": "use a package"})
        self.assertTrue(out.startswith("NEEDS CONFIRMATION") and "notarealpkg-x" in out, out)
        self.assertNotIn("notarealpkg-x", str(self.installs))
        info = json.loads((self.root / "demo" / self.da.RUN_FILE).read_text(encoding="utf-8"))
        self.assertEqual(info["dependencies"], ["requests", "notarealpkg-x"])

    def test_only_package_names_reach_pip(self):
        ok = ["requests", "requests>=2.0,<3", "uvicorn[standard]==0.30", "python-dateutil"]
        bad = ["--index-url=http://evil", "-r x.txt", "git+https://evil/x", "../evil", "pkg @ https://x", "a; rm -rf /"]
        self.assertTrue(all(self.da._dep_ok(d) for d in ok))
        self.assertFalse(any(self.da._dep_ok(d) for d in bad))
        calls = []   # the real installer, with pip itself mocked
        with mock.patch.object(self.da, "_venv_python", return_value="py"), \
             mock.patch.object(self.da, "_missing_dependencies", side_effect=lambda deps, d: deps), \
             mock.patch.object(self.da.subprocess, "run",
                               side_effect=lambda cmd, **kw: calls.append(cmd) or mock.Mock(returncode=0)):
            self.real_install(["requests", "--index-url=http://evil"], self.root)
        self.assertEqual(calls, [["py", "-m", "pip", "install", "requests"]])

    def test_background_task_yes_runs_the_project_not_a_new_build(self):
        with self.assertRaises(executor.NeedsConfirmation) as e:
            executor._call_tool("dev_agent", {"description": "clean my folder"}, None)
        self.assertEqual(e.exception.step, {"tool": "dev_agent", "parameters": {"action": "run", "project_name": "demo"}})
        self.assertTrue(self.sentinel.exists())
        self.assertIn("gone", executor._call_tool("dev_agent", e.exception.step["parameters"], None, approved=True))
        self.assertFalse(self.sentinel.exists())

    def test_unknown_project_fails(self):
        self.assertTrue(self.da.dev_agent({"action": "run", "project_name": "nope"}).startswith("FAILED"))


class CrashTests(unittest.TestCase):
    """ATLAS killed in the middle of a task: done steps stay done, the running one is never repeated blindly."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "tasks.json"

    def test_action_running_at_a_crash_comes_back_blocked_and_unknown(self):
        q = TaskQueue(path=self.path)
        tid = q.submit("tidy and send")
        task = q._tasks[tid]
        two = {"thought": "both", "actions": [{"tool": "file_controller", "parameters": {"action": "write"}},
                                              {"tool": "web_search", "parameters": {"query": "x"}}]}

        def tool(name, params, speak, **kw):
            if name == "web_search":
                raise SystemExit("killed")   # the process dies while this action runs
            return "written"
        with mock.patch.object(llm, "complete_json", return_value=two), \
             mock.patch.object(executor, "_call_tool", side_effect=tool), \
             mock.patch.object(memory_manager, "load_memory", return_value={}), \
             mock.patch.object(memory_manager, "find_skills", return_value=[]), \
             self.assertRaises(SystemExit):
            executor.AgentExecutor().execute(task.goal, state=task.state, on_step=lambda s: q._notify(task, s))

        q2 = TaskQueue(path=self.path)
        status = q2.get_status(tid)
        self.assertEqual((status["status"], q2._queue), ("blocked", []))
        self.assertIn("web_search", status["step"])
        note = q2._tasks[tid].state["history"][-1]
        self.assertIn("OUTPUT: written", note)                       # the finished action is kept as done
        self.assertIn("UNKNOWN: it was running when ATLAS stopped", note)
        self.assertNotIn("inflight", q2._tasks[tid].state)

        self.assertTrue(q2.resume(tid))
        result, state, prompts, calls = run([{"final": "Checked, it ran.", "done": True}], state=q2._tasks[tid].state)
        self.assertEqual(calls, [])                                   # nothing re-run on its own
        self.assertIn("UNKNOWN", prompts[0])
        self.assertEqual(prompts[0].count("UNKNOWN"), 1)

    def test_yes_is_for_the_step_asked_and_only_once(self):
        q = TaskQueue(path=self.path)
        tid = q.submit("goal")
        task = q._tasks[tid]
        step = {"tool": "send_message", "parameters": {"receiver": "Ana"}}
        task.status, task.state["awaiting"] = TaskStatus.BLOCKED, step
        self.assertTrue(q.resume(tid))                                # resumed without a yes: nothing approved
        self.assertNotIn("approved", task.state)
        self.assertNotIn("awaiting", task.state)
        task.status, task.state["awaiting"] = TaskStatus.BLOCKED, step
        self.assertTrue(q.resume(tid, approve=True))
        self.assertEqual(task.state["approved"], step)
        task.status = TaskStatus.PAUSED
        self.assertTrue(q.resume(tid, approve=True))                  # a yes with nothing asked approves nothing
        self.assertNotIn("approved", task.state)

    def test_yes_from_before_a_restart_is_asked_again(self):
        q = TaskQueue(path=self.path)
        tid = q.submit("goal")
        q._tasks[tid].state["approved"] = {"tool": "send_message", "parameters": {}}
        q._save()
        q2 = TaskQueue(path=self.path)
        self.assertEqual(q2.get_status(tid)["status"], "blocked")
        self.assertEqual(q2._tasks[tid].state, {"awaiting": {"tool": "send_message", "parameters": {}}})

    def test_interrupted_without_a_running_action_is_just_paused(self):
        q = TaskQueue(path=self.path)
        tid = q.submit("goal")
        self.assertEqual(TaskQueue(path=self.path).get_status(tid)["status"], "paused")


class RoleModelTests(unittest.TestCase):
    def test_role_uses_its_own_model_only_when_configured(self):
        cfg = {"role_models": {"coder": {"provider": "claude", "model": "m-coder"}, "agent": {"provider": "nope"}}}
        with mock.patch.object(llm, "get_config", return_value=cfg):
            self.assertEqual(llm.role_engine("coder"), ("claude", "m-coder"))
            self.assertIsNone(llm.role_engine("agent"))     # unknown provider: the selected engine as before
            self.assertIsNone(llm.role_engine(None))
        with mock.patch.object(llm, "get_config", return_value={"role_models": {"x": {"provider": "gemini"}}}):
            self.assertEqual(llm.role_engine("x"), ("gemini", llm.GEMINI_TEXT[False]))

    def test_role_override_reaches_the_provider(self):
        seen = []
        cfg = {"role_models": {"coder": {"provider": "claude_sub", "model": "m-coder"}}}
        with mock.patch.object(llm, "get_engine", return_value=("gemini", "live")), \
             mock.patch.object(llm, "get_effort", return_value="high"), \
             mock.patch.object(llm, "get_config", return_value=cfg), \
             mock.patch.object(llm, "_subscription_complete", side_effect=lambda p, s, m, e: seen.append((m, e)) or "ok"):
            self.assertEqual(llm.complete("x", role="coder"), "ok")
        self.assertEqual(seen, [("m-coder", None)])   # effort picked for another provider is not carried over


if __name__ == "__main__":
    unittest.main()
