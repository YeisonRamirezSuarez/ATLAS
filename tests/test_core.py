"""Offline checks for the core: no network, no microphone, no API keys.

Run:  python -m unittest discover tests
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Logs use emoji; a cp1252 console (Windows, piped output) would raise UnicodeEncodeError mid-action.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

import config
from agent import executor, llm, planner
from memory import memory_manager


class ConfigTests(unittest.TestCase):
    def test_version_is_semver(self):
        self.assertRegex(config.VERSION, r"^\d+\.\d+\.\d+$")

    def test_os_detected(self):
        self.assertIn(config.get_os(), {"windows", "mac", "linux"})

    def test_effort_saved_default_or_none(self):
        with mock.patch.object(config, "get_config", return_value={"provider": "claude_sub", "effort": "high"}):
            self.assertEqual(config.get_effort(), "high")
        with mock.patch.object(config, "get_config", return_value={"provider": "claude_sub", "effort": "bogus"}):
            self.assertEqual(config.get_effort(), "low")
            self.assertIsNone(config.get_effort(default=False))
        with mock.patch.object(config, "get_config", return_value={"provider": "groq", "effort": "high"}):
            self.assertIsNone(config.get_effort())

    def test_engine_defaults_to_gemini_without_config(self):
        with mock.patch.object(config, "get_config", side_effect=FileNotFoundError):
            self.assertEqual(config.get_engine()[0], "gemini")


class LLMTests(unittest.TestCase):
    def test_subscription_never_uses_api_key(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-x"}), \
             mock.patch.object(llm, "get_config", return_value={"claude_oauth_token": "tok"}):
            env = llm.subscription_options("", "m").env
        self.assertEqual(env["ANTHROPIC_API_KEY"], "")
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "tok")

    def test_auth_errors_detected(self):
        self.assertTrue(llm.is_auth_error("Failed to authenticate: OAuth session expired"))
        self.assertTrue(llm.is_auth_error("Not logged in · Please run /login"))
        self.assertFalse(llm.is_auth_error("rate limited"))

    def test_fast_switch_off_uses_picked_effort(self):
        seen = []
        with mock.patch.object(llm, "get_engine", return_value=("claude_sub", "m")), \
             mock.patch.object(llm, "get_effort", return_value="high"), \
             mock.patch.object(llm, "_subscription_complete", side_effect=lambda p, s, m, e: seen.append(e) or "ok"):
            for cfg in ({}, {"fast": False}):
                with mock.patch.object(llm, "get_config", return_value=cfg):
                    llm.complete("x", fast=True)
        self.assertEqual(seen, [None, "high"])

    def test_strip_fences(self):
        self.assertEqual(llm.strip_fences('```json\n{"a": 1}\n```'), '{"a": 1}')

    def test_complete_json_finds_json_inside_prose(self):
        with mock.patch.object(llm, "complete", return_value='Here it is: {"steps": []} hope it helps'):
            self.assertEqual(llm.complete_json("x"), {"steps": []})

    def test_missing_key_fails_loudly_instead_of_switching_provider(self):
        with mock.patch.object(llm, "get_engine", return_value=("claude", "m")), \
             mock.patch.object(llm, "get_config", return_value={}):
            with self.assertRaises(llm.LLMError):
                llm.complete("hi")


class PlannerTests(unittest.TestCase):
    def test_valid_fills_defaults(self):
        plan = planner._valid({"steps": [{"tool": "web_search"}]})
        self.assertEqual(plan["steps"][0]["step"], 1)
        self.assertEqual(plan["steps"][0]["parameters"], {})

    def test_valid_rejects_step_without_tool(self):
        for bad in ({}, {"steps": "x"}, {"steps": [{"description": "no tool"}]}):
            with self.assertRaises(ValueError):
                planner._valid(bad)


class ExecutorTests(unittest.TestCase):
    def test_checked_never_reports_empty_as_success(self):
        self.assertIn("not verified", executor._checked("open_app", ""))
        self.assertEqual(executor._checked("open_app", "Opened Chrome."), "Opened Chrome.")
        with self.assertRaises(RuntimeError):
            executor._checked("open_app", "Could not find Chrome.")

    def test_dangerous_patterns(self):
        for code in ("subprocess.run(['pip', 'install', 'x'])", "os.system('rm -rf /tmp/x')",
                     "schtasks /create", "shutil.rmtree(p)", "os.replace(a, b)"):
            self.assertTrue(executor._DANGEROUS.search(code), code)
        self.assertIsNone(executor._DANGEROUS.search("print(sum(range(10)))"))

    def test_risky_script_needs_confirmation_and_yes_runs_the_same_script(self):
        risky = "# PLAN: delete temp files\nimport shutil\nshutil.rmtree('x')"
        ran = []
        with mock.patch.object(executor, "_write_script", return_value=risky), \
             mock.patch.object(executor, "_run_script", side_effect=lambda c: (ran.append(c), (True, "ok"))[1]), \
             mock.patch.object(memory_manager, "find_skills", return_value=[]), \
             mock.patch.object(memory_manager, "save_skill"):
            with self.assertRaises(executor.NeedsConfirmation):
                executor.solve_task("clean temp")
            self.assertEqual(ran, [])
            # "yes" without a pending script must not unlock anything
            executor._pending = None
            with self.assertRaises(executor.NeedsConfirmation):
                executor.solve_task("clean temp", confirmed=True)
            self.assertEqual(executor.solve_task("clean temp", confirmed=True), "ok")
            self.assertEqual(ran, [risky])

    def test_fix_of_approved_script_runs_without_asking_again(self):
        first = "# PLAN: kill it\nimport os\nos.system('taskkill /im x.exe')"
        fix   = first + "\nprint('ok')"
        worse = fix + "\nimport shutil\nshutil.rmtree('y')"
        results = iter([(False, "Traceback: boom"), (True, "ok")])
        with mock.patch.object(executor, "_run_script", side_effect=lambda c: next(results)), \
             mock.patch.object(memory_manager, "find_skills", return_value=[]), \
             mock.patch.object(memory_manager, "save_skill"):
            executor._pending = ("kill x", first)
            with mock.patch.object(executor, "_write_script", return_value=fix):
                self.assertEqual(executor.solve_task("kill x", confirmed=True), "ok")
            results = iter([(False, "Traceback: boom")])
            executor._pending = ("kill x", first)
            with mock.patch.object(executor, "_write_script", return_value=worse):
                with self.assertRaises(executor.NeedsConfirmation):   # a new kind of risk still asks
                    executor.solve_task("kill x", confirmed=True)
        executor._pending = None


class TaskQueueTests(unittest.TestCase):
    def test_hud_sees_queue_steps_and_end(self):
        from agent.task_queue import TaskQueue

        class Exec:
            def execute(self, goal, speak, cancel_flag, on_step):
                on_step("1/2 web_search: find it")
                return "done"
        q, seen = TaskQueue(), []
        q._executor, q.on_change = Exec(), lambda *a: seen.append(a)
        tid = q.submit("find flights")
        q._run_task(q._tasks[tid])
        self.assertEqual(seen, [(tid, "find flights", "QUEUED"), (tid, "find flights", "PLANNING"),
                                (tid, "find flights", "1/2 web_search: find it"), (tid, "", "1/2 web_search: find it")])


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "long_term.json"
        self.patch = mock.patch.object(memory_manager, "MEMORY_PATH", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.dir.cleanup()

    def test_save_load_roundtrip(self):
        mem = memory_manager._empty_memory()
        mem["identity"]["name"] = {"value": "Pablo"}
        memory_manager.save_memory(mem)
        self.assertEqual(memory_manager.load_memory()["identity"]["name"]["value"], "Pablo")
        self.assertFalse(self.path.with_suffix(".tmp").exists())

    def test_builtin_skills_found_without_engram_and_carry_no_user_data(self):
        with mock.patch.object(memory_manager, "_engram", return_value=""):
            found = memory_manager.find_skills("report the RAM usage of this PC")
            self.assertTrue(found and "psutil" in found[0][1])
            self.assertEqual(memory_manager.find_skills("book a flight to Madrid"), [])
        home = str(Path.home()).lower()
        for f in memory_manager.BUILTIN_SKILLS_DIR.glob("*.py"):
            self.assertNotIn(home, f.read_text(encoding="utf-8").lower(), f.name)

    def test_corrupt_file_is_kept_not_overwritten(self):
        self.path.write_text("{broken", encoding="utf-8")
        self.assertEqual(memory_manager.load_memory(), memory_manager._empty_memory())
        backups = list(Path(self.dir.name).glob("long_term.corrupt-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "{broken")


class DesktopSandboxTests(unittest.TestCase):
    def setUp(self):
        from actions import desktop
        self.run = desktop._execute_generated_code

    def test_results_are_honest(self):
        self.assertEqual(self.run("print(1 + 1)"), "2")
        self.assertIn("not verified", self.run("x = 1"))
        self.assertTrue(self.run("UNSAFE").startswith("Could not"))
        self.assertTrue(self.run("raise ValueError('boom')").startswith("Error"))

    def test_no_ctypes_or_getattr(self):
        self.assertTrue(self.run("getattr(Path, 'home')").startswith("Error"))
        self.assertTrue(self.run("ctypes.windll").startswith("Error"))


class MainToolResultTests(unittest.TestCase):
    """main.py needs the audio/UI stack; skipped where it is not installed (CI)."""

    @classmethod
    def setUpClass(cls):
        try:
            import main
        except Exception as e:  # missing PyQt6 / sounddevice / PortAudio
            raise unittest.SkipTest(f"main.py not importable here: {e}")
        cls.main = main

    def test_honest(self):
        h = self.main._honest
        self.assertIn("not verified", h("send_message", None))
        self.assertTrue(h("send_message", "Could not open WhatsApp").startswith("FAILED"))
        self.assertEqual(h("send_message", "Message sent."), "Message sent.")

    def test_tool_timeouts_cover_default(self):
        self.assertIn("default", self.main.TOOL_TIMEOUTS)

    def test_tts_quota_keeps_voice_with_live(self):
        import asyncio, engines
        j = engines.CascadeJarvis.__new__(engines.CascadeJarvis)
        j.cfg, spoke = {"gemini_api_key": "k"}, []

        async def quota(_):
            raise RuntimeError("429 Too Many Requests 'retryDelay': '600s'")

        async def live(text):
            spoke.append(text)
        j._gemini_tts, j._live_tts = quota, live
        with mock.patch.object(engines.edge_tts, "Communicate", side_effect=AssertionError("voice changed")):
            asyncio.run(j._tts("hola"))
            asyncio.run(j._tts("otra"))   # inside the quota window: straight to Live
        self.assertEqual(spoke, ["hola", "otra"])
        self.assertGreater(j._tts_quota_until, engines.time.time() + 500)

    def test_background_speech_follows_user_language(self):
        import engines
        j = engines.CascadeJarvis.__new__(engines.CascadeJarvis)
        j.cfg, j._loop, j._last_heard, said = {}, None, "pon música", []
        j._say = said.append
        with mock.patch("agent.llm.complete", return_value="Ajustando mi enfoque, señor.") as c:
            j.speak("Adjusting my approach, sir.")
        self.assertEqual(said, ["Ajustando mi enfoque, señor."])
        self.assertIn("pon música", c.call_args.args[0])
        with mock.patch("agent.llm.complete", side_effect=RuntimeError("offline")):
            j.speak("Task cancelled, sir.")   # translation down: still speaks
        self.assertEqual(said[-1], "Task cancelled, sir.")

    def test_message_typed_while_speaking_is_answered_after(self):
        import threading, engines
        j = engines.CascadeJarvis.__new__(engines.CascadeJarvis)
        j._loop, j._busy, j._turn, j._wake, j._is_speaking = object(), False, threading.Lock(), None, False
        j.ui, speaking, heard = mock.MagicMock(muted=False), threading.Event(), []

        def ask(text, run_tool, say):
            heard.append(text)
            if text == "first":
                speaking.set()
                threading.Event().wait(0.3)   # still talking when the second message arrives
            return "", 0, 0
        j.llm = mock.MagicMock(ask=ask)
        t = threading.Thread(target=j._on_text_command, args=("first",))
        with mock.patch.object(engines, "_update_memory_async"):
            t.start(); speaking.wait(2)
            j._on_text_command("second")
            t.join()
        self.assertEqual(heard, ["first", "second"])

    def test_task_status_tool_declared(self):
        names = {t["name"] for t in self.main.TOOL_DECLARATIONS}
        self.assertIn("task_status", names)


if __name__ == "__main__":
    unittest.main()
