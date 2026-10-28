"""Offline checks for the core: no network, no microphone, no API keys.

Run:  python -m unittest discover tests
"""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Logs use emoji; a cp1252 console (Windows, piped output) would raise UnicodeEncodeError mid-action.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

import numpy.random   # imported up front: a lazy import racing a test's leftover thread hit CPython's KeyError
import config
from agent import executor, llm
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

    def test_no_retired_gemini_2_5_flash_default(self):
        # Google 404s "gemini-2.5-flash" for new users; native-audio preview is a different model.
        for p in config.PROVIDERS.values():
            for m in [p["model"], *p.get("models", [])]:
                self.assertNotRegex(m, r"gemini-2\.5-flash$")


class LLMTests(unittest.TestCase):
    def test_subscription_never_uses_api_key(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-x"}), \
             mock.patch.object(llm, "get_config", return_value={"claude_oauth_token": "tok"}):
            env = llm.subscription_options("", "m").env
        self.assertEqual(env["ANTHROPIC_API_KEY"], "")
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "tok")

    def test_skills_only_when_asked(self):
        with mock.patch.object(llm, "get_config", return_value={}):
            lean = llm.subscription_options("", "m")
            full = llm.subscription_options("", "m", tools=["Skill"], setting_sources=llm.SKILL_SOURCES)
        self.assertEqual((lean.setting_sources, lean.tools), ([], []))   # helper calls stay lean
        self.assertEqual(full.setting_sources, ["user", "project"])
        self.assertNotIn("CLAUDE_CONFIG_DIR", full.env)   # the master ~/.claude, shared with the profiles
        self.assertNotIn("settings", lean.extra_args)
        self.assertTrue(json.loads(full.extra_args["settings"])["disableAllHooks"])   # no caveman/superpowers in ATLAS
        self.assertIn("strict-mcp-config", lean.extra_args)   # claude.ai connectors made helpers think they had tools
        with mock.patch.object(llm.Path, "read_text", return_value='{"enabledPlugins": '
                               '{"superpowers@official": true, "caveman@caveman": true}}'):
            dev = json.loads(llm._skills_only(("superpowers",)))["enabledPlugins"]
        self.assertEqual(dev, {"superpowers@official": True, "caveman@caveman": False})

    def test_work_folder_marked_trusted_keeping_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / ".claude.json"
            cfg.write_text(json.dumps({"numStartups": 3, "projects": {"C:/x": {"allowedTools": []}}}), encoding="utf-8")
            llm.trust_folder(tmp, cfg)
            data = json.loads(cfg.read_text(encoding="utf-8"))
            self.assertTrue(data["projects"][Path(tmp).resolve().as_posix()]["hasTrustDialogAccepted"])
            self.assertEqual((data["numStartups"], data["projects"]["C:/x"]), (3, {"allowedTools": []}))
            mtime = cfg.stat().st_mtime_ns
            llm.trust_folder(tmp, cfg)
            self.assertEqual(cfg.stat().st_mtime_ns, mtime)   # already trusted: not rewritten

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


class ReActTests(unittest.TestCase):
    """Task eval set: scripted model replies, real loop. Each case also pins the LLM call count."""

    def run_agent(self, replies, tools=None, cancel=None):
        replies, prompts, calls, spoken, steps = list(replies), [], [], [], []
        tools = tools or {}

        def fake_llm(prompt, system="", **kw):
            prompts.append(prompt)
            r = replies.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        def fake_tool(tool, params, speak):
            calls.append((tool, params))
            out = tools.get(tool, "ok")
            if isinstance(out, Exception):
                raise out
            return out

        with mock.patch.object(llm, "complete_json", side_effect=fake_llm), \
             mock.patch.object(executor, "_call_tool", side_effect=fake_tool), \
             mock.patch.object(memory_manager, "load_memory", return_value={}):
            result = executor.AgentExecutor().execute("the goal", speak=spoken.append,
                                                      cancel_flag=cancel, on_step=steps.append)
        return result, prompts, calls, spoken, steps

    def test_simple_task_costs_one_call(self):
        result, prompts, calls, spoken, steps = self.run_agent([
            {"thought": "open it", "actions": [{"tool": "open_app", "parameters": {"app_name": "Chrome"}}],
             "final": "Chrome is open, sir."}])
        self.assertEqual(len(prompts), 1)
        self.assertEqual(calls, [("open_app", {"app_name": "Chrome"})])
        self.assertEqual((result, spoken), ("Chrome is open, sir.", ["Chrome is open, sir."]))
        self.assertEqual(steps, ["1. open_app: open it"])

    def test_next_turn_sees_outputs_and_answers_from_them(self):
        result, prompts, calls, _, _ = self.run_agent([
            {"thought": "look it up", "actions": [{"tool": "web_search", "parameters": {"query": "btc"}}]},
            {"thought": "answer", "final": "Bitcoin is at 90k, sir.", "done": True}],
            tools={"web_search": "BTC 90,000 USD"})
        self.assertEqual(len(prompts), 2)
        self.assertIn("OUTPUT: BTC 90,000 USD", prompts[1])
        self.assertEqual(result, "Bitcoin is at 90k, sir.")

    def test_error_is_observed_and_later_actions_wait(self):
        result, prompts, calls, _, _ = self.run_agent([
            {"thought": "write then open", "final": "Done, sir.", "actions": [
                {"tool": "file_controller", "parameters": {"action": "write"}},
                {"tool": "cmd_control", "parameters": {"task": "open it"}}]},
            {"thought": "retry", "actions": [{"tool": "generated_code", "parameters": {"description": "x"}}],
             "final": "Saved, sir."}],
            tools={"file_controller": RuntimeError("Could not write: disk full")})
        self.assertEqual([t for t, _ in calls], ["file_controller", "generated_code"])
        self.assertIn("ERROR: Could not write: disk full", prompts[1])
        self.assertEqual(result, "Saved, sir.")

    def test_failed_goal_is_reported_not_claimed(self):
        result, *_ = self.run_agent([{"thought": "no way", "final": "I could not find it, sir.", "done": False}])
        self.assertEqual(result, "I could not find it, sir.")

    def test_bad_reply_gets_one_retry_then_fails(self):
        result, prompts, *_ = self.run_agent([ValueError("no JSON"), {"final": "ok, sir.", "done": True}])
        self.assertIn("could not be used", prompts[1])
        self.assertEqual(result, "ok, sir.")
        with self.assertRaises(RuntimeError):
            self.run_agent([ValueError("no JSON"), ValueError("no JSON")])

    def test_turn_cap_and_cancel(self):
        loop = {"thought": "again", "actions": [{"tool": "web_search", "parameters": {}}]}
        result, prompts, *_ = self.run_agent([loop] * executor.MAX_TURNS)
        self.assertEqual(len(prompts), executor.MAX_TURNS)
        self.assertIn("stopped after", result)
        stop = threading.Event(); stop.set()
        result, prompts, calls, *_ = self.run_agent([], cancel=stop)
        self.assertEqual((result, prompts, calls), ("Task cancelled, sir.", [], []))

    def test_risky_script_asks_and_stops(self):
        result, prompts, _, spoken, _ = self.run_agent(
            [{"thought": "delete", "actions": [{"tool": "generated_code", "parameters": {"description": "rm"}}]}],
            tools={"generated_code": executor.NeedsConfirmation("NEEDS CONFIRMATION. ask")})
        self.assertEqual((len(prompts), result), (1, "NEEDS CONFIRMATION. ask"))


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

    def test_trusted_case_runs_risky_script_without_asking(self):
        risky = "# PLAN: delete temp files\nimport shutil\nshutil.rmtree('x')"
        with mock.patch.object(executor, "_write_script", return_value=risky), \
             mock.patch.object(executor, "_run_script", return_value=(True, "ok")), \
             mock.patch.object(memory_manager, "find_skills", return_value=[]), \
             mock.patch.object(memory_manager, "save_skill"):
            self.assertEqual(executor.solve_task("clean temp", trusted=True), "ok")

    def test_fix_of_approved_script_runs_without_asking_again(self):
        first = "# PLAN: kill it\nimport os\nos.system('taskkill /im x.exe')"
        fix   = first + "\nprint('ok')"
        worse = fix + "\nimport shutil\nshutil.rmtree('y')"
        results = iter([(False, "Traceback: boom"), (True, "ok")])
        with mock.patch.object(executor, "_run_script", side_effect=lambda c: next(results)), \
             mock.patch.object(memory_manager, "find_skills", return_value=[]), \
             mock.patch.object(memory_manager, "save_skill"), \
             mock.patch.object(executor, "_research", return_value=""):
            executor._pending = ("kill x", first)
            with mock.patch.object(executor, "_write_script", return_value=fix):
                self.assertEqual(executor.solve_task("kill x", confirmed=True), "ok")
            results = iter([(False, "Traceback: boom")])
            executor._pending = ("kill x", first)
            with mock.patch.object(executor, "_write_script", return_value=worse):
                with self.assertRaises(executor.NeedsConfirmation):   # a new kind of risk still asks
                    executor.solve_task("kill x", confirmed=True)
        executor._pending = None

    def test_failed_attempt_researches_once_and_research_errors_do_not_block(self):
        results = iter([(False, "ModuleNotFoundError: x"), (False, "boom"), (True, "done")])
        seen = []

        def write(goal, skills, failed, research=""):
            seen.append(research)
            return "# PLAN: print\nprint(1)"
        with mock.patch.object(executor, "_write_script", side_effect=write), \
             mock.patch.object(executor, "_run_script", side_effect=lambda c: next(results)), \
             mock.patch.object(memory_manager, "find_skills", return_value=[]), \
             mock.patch.object(memory_manager, "save_skill"), \
             mock.patch("actions.web_search._ddg_search",
                        return_value=[{"title": "T", "snippet": "use y", "url": "https://e.x"}]) as ddg:
            self.assertEqual(executor.solve_task("do x"), "done")
        self.assertEqual(ddg.call_count, 1)
        self.assertEqual(seen[0], "")
        self.assertIn("https://e.x", seen[1])
        with mock.patch("actions.web_search._ddg_search", side_effect=OSError("offline")):
            self.assertEqual(executor._research("do x", "err"), "")


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

    def test_saved_skill_records_verification_and_keeps_old_versions(self):
        with mock.patch.object(memory_manager, "SKILLS_DIR", Path(self.dir.name) / "skills"), \
             mock.patch.object(memory_manager, "_engram_save_async") as engram, \
             mock.patch.object(memory_manager, "datetime") as clock:
            from datetime import datetime
            for second in (1, 2):
                clock.now.return_value = datetime(2026, 10, 23, 10, 0, second)
                memory_manager.save_skill("show ram", "# PLAN: print ram", "# PLAN: print ram\nprint(1)")
            files = sorted((Path(self.dir.name) / "skills").glob("*.py"))
        self.assertEqual(len(files), 2)
        lines = files[0].read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "# PLAN: print ram")
        self.assertTrue(lines[-1].startswith("# VERIFIED: 2026-10-23 on "))
        self.assertIn("VERIFIED: 2026-10-23", engram.call_args[0][1])

    def test_style_persists_rejects_unknown_values_and_resets(self):
        with mock.patch.object(memory_manager, "_engram_save_async") as engram:
            msg = memory_manager.set_style({"humor": "LOW", "detail": "brief", "tone": "rude"})
        self.assertIn("Rejected tone=rude", msg)
        mem = memory_manager.load_memory()
        self.assertEqual(mem["style"], {"humor": "low", "detail": "brief"})
        engram.assert_not_called()
        self.assertIn("humor: low", memory_manager.format_style_for_prompt(mem))
        self.assertNotIn("humor", memory_manager.format_memory_for_prompt(mem))
        self.assertEqual(memory_manager._all_entries(mem), [])   # the size trim never drops it
        self.assertIn("humor=low", memory_manager.set_style({}))
        memory_manager.set_style({}, reset=True)
        self.assertEqual(memory_manager.format_style_for_prompt(memory_manager.load_memory()), "")

    def test_trusted_cases_saved_only_by_name_and_removable(self):
        self.assertFalse(memory_manager.is_trusted("delete temp files"))
        memory_manager.set_trusted("Delete  temp files")
        memory_manager.set_trusted("delete temp files")   # no duplicate
        mem = memory_manager.load_memory()
        self.assertEqual(mem["trusted"], ["Delete temp files"])
        self.assertTrue(memory_manager.is_trusted("delete temp files"))
        self.assertFalse(memory_manager.is_trusted("delete all files"))
        self.assertFalse(memory_manager.is_trusted(""))
        self.assertIn("Delete temp files", memory_manager.format_trusted_for_prompt(mem))
        self.assertEqual(memory_manager._all_entries(mem), [])   # the size trim never drops it
        self.assertIn("always ask", memory_manager.set_trusted("delete temp files", remove=True))
        self.assertEqual(memory_manager.format_trusted_for_prompt(memory_manager.load_memory()), "")

    def test_prompt_keeps_humor_out_of_failures_and_emergencies(self):
        text = (Path(__file__).resolve().parent.parent / "core" / "prompt.txt").read_text(encoding="utf-8")
        for rule in ("NO humor when the user is distressed", "emergency", "you do not have human feelings",
                     "Never let a joke delay, replace or soften a", "never present a guess as certain"):
            self.assertIn(rule, text)

    def test_prompt_searches_memory_first_on_personal_topics(self):
        text = (Path(__file__).resolve().parent.parent / "core" / "prompt.txt").read_text(encoding="utf-8")
        for rule in ("call search_memory FIRST", "Never ask the user something already stored in memory"):
            self.assertIn(rule, text)

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


async def _until(cond, secs=5.0):
    """Polls cond instead of a fixed sleep: CI runners without audio take a while to list devices."""
    import asyncio
    for _ in range(int(secs / 0.02)):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return cond()


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

    def test_saving_settings_ends_the_engine(self):
        import asyncio, types
        ui, stopped = types.SimpleNamespace(on_config_saved=None), []

        class Engine:
            async def run(self):
                try:
                    await asyncio.sleep(60)
                finally:
                    stopped.append(True)   # audio streams close in finally / with blocks

        threading.Timer(0.2, lambda: ui.on_config_saved()).start()   # the Save click, from the Qt thread
        asyncio.run(self.main._run_until_saved(ui, Engine()))
        self.assertEqual(stopped, [True])

    def test_one_voice_reconnects_live_before_edge_tts(self):
        import asyncio, engines
        j = engines.CascadeAtlas.__new__(engines.CascadeAtlas)
        j.cfg, spoke, calls = {"gemini_api_key": "k"}, [], iter([RuntimeError("closed while idle"), None])

        async def live(text):
            if e := next(calls):
                raise e
            spoke.append(text)
        j._live_tts = live
        with mock.patch.object(engines.edge_tts, "Communicate", side_effect=AssertionError("voice changed")):
            asyncio.run(j._tts_now("hola"))
        self.assertEqual(spoke, ["hola"])

    def test_interrupted_read_keeps_the_same_live_session(self):
        import asyncio, contextlib, engines, numpy as np
        from types import SimpleNamespace as NS
        opened = []

        class Session:   # one message stream; each turn: 3 audio chunks, then turn_complete
            def __init__(self):
                self.msgs = asyncio.Queue()
            async def __aenter__(self):
                opened.append(self)
                return self
            async def __aexit__(self, *exc):
                return False
            async def send_client_content(self, turns, turn_complete):
                tag = {"one": -1000, "two": 1000}[turns["parts"][0]["text"]]
                for _ in range(3):
                    part = NS(inline_data=NS(data=np.full(12000, tag, np.int16).tobytes()))
                    self.msgs.put_nowait(NS(server_content=NS(output_transcription=None, model_turn=NS(parts=[part]))))
                self.msgs.put_nowait(None)
            async def receive(self):
                while (m := await self.msgs.get()) is not None:
                    await asyncio.sleep(0.01)
                    yield m

        j = engines.CascadeAtlas.__new__(engines.CascadeAtlas)
        j._tts_client = NS(aio=NS(live=NS(connect=lambda **kw: Session())))

        async def go():
            j.audio_in_queue = asyncio.Queue()
            t = asyncio.ensure_future(j._live_tts("one"))
            await asyncio.sleep(0.025)
            t.cancel()   # the user interrupted the first read
            with contextlib.suppress(asyncio.CancelledError):
                await t
            j.audio_in_queue = asyncio.Queue()
            await j._live_tts("two")
            return b"".join(j.audio_in_queue.get_nowait() for _ in range(j.audio_in_queue.qsize()))

        played = np.frombuffer(asyncio.run(go()), np.int16)
        self.assertEqual(len(opened), 1)        # a new session would read in another tone
        self.assertGreaterEqual(played.min(), 0)   # nothing of the cut read leaks into the next one
        self.assertIn(1000, set(played))

    def test_tail_noise_is_cut_and_speech_kept(self):
        import numpy as np
        rng = np.random.default_rng(0)
        t = np.arange(24000) / 24000
        speech = (8000 * np.sin(2 * np.pi * 180 * t)).astype(np.int16)
        hiss = rng.integers(-20000, 20000, 3600).astype(np.int16)   # 150 ms radio-like burst
        pcm = np.concatenate([speech, np.zeros(6000, np.int16), hiss]).tobytes()
        out = np.frombuffer(self.main.clean_tail(pcm), np.int16)
        self.assertLessEqual(len(out), 24000 + 6000)
        self.assertLess(abs(out[-2000:].astype(int)).max(), 300)
        self.assertEqual(len(self.main.clean_tail(speech.tobytes())), len(speech) * 2)   # no hiss: untouched length
        self.assertEqual(out[-1], 0)   # faded: no click

    def test_slow_tool_moves_to_background_and_reports_later(self):
        import asyncio, threading
        m = self.main
        j = m.AtlasLive.__new__(m.AtlasLive)
        j.ui, done, notes = mock.MagicMock(), threading.Event(), []
        j._deliver = lambda note: (notes.append(note), done.set())

        async def go():
            with mock.patch.object(m, "TOOL_TIMEOUTS", {"default": 0.05}),                  mock.patch.object(m, "get_config", return_value={}):
                return await j._run_blocking("web_search", lambda: threading.Event().wait(0.3) or "Found 3 flights")
        self.assertEqual(asyncio.run(go()), m.STILL_WORKING)
        self.assertTrue(done.wait(2))
        self.assertIn("Found 3 flights", notes[0])

    def test_background_speech_follows_user_language(self):
        import engines
        j = engines.CascadeAtlas.__new__(engines.CascadeAtlas)
        j.cfg, j._loop, j._last_heard, said = {}, None, "pon música", []
        j._say = said.append
        with mock.patch("agent.llm.complete", return_value="Ajustando mi enfoque, señor.") as c:
            j.speak("Adjusting my approach, sir.")
        self.assertEqual(said, ["Ajustando mi enfoque, señor."])
        self.assertIn("pon música", c.call_args.args[0])
        with mock.patch("agent.llm.complete", side_effect=RuntimeError("offline")):
            j.speak("Task cancelled, sir.")   # translation down: still speaks
        self.assertEqual(said[-1], "Task cancelled, sir.")

    def test_reader_that_says_something_else_is_logged_and_reset(self):
        import asyncio, engines
        from types import SimpleNamespace as NS
        audio = NS(server_content=NS(output_transcription=None, model_turn=NS(parts=[NS(inline_data=NS(data=bytes(64)))])))

        class Session:
            async def send_client_content(self, **k): pass
            async def receive(self):
                yield NS(server_content=NS(output_transcription=NS(text=self.heard), model_turn=None))
                yield audio
        j = engines.CascadeAtlas.__new__(engines.CascadeAtlas)
        j.ui, j._reads, j._play, session = mock.MagicMock(), 0, lambda pcm: None, Session()
        j._reader, j._close_reader = mock.AsyncMock(return_value=session), mock.AsyncMock()
        text = "Entendido, señor. No la toco y le aviso cuando termine."
        session.heard = "Entendido, señor, no la toco y le aviso cuando termine."
        asyncio.run(j._live_tts(text))
        j._close_reader.assert_not_called()   # same words, other punctuation: fine
        session.heard = "Son las tres y cuarenta y cinco, señor."
        asyncio.run(j._live_tts("Son las 3:45, señor."))
        j._close_reader.assert_not_called()   # numbers are read out in words
        session.heard = "Entendido señor, quedo a la espera."
        asyncio.run(j._live_tts(text))
        j._close_reader.assert_called_once()
        self.assertIn("quedo a la espera", j.ui.write_log.call_args.args[0])

    def test_message_typed_while_speaking_interrupts_and_is_answered(self):
        import threading, engines
        j = engines.CascadeAtlas.__new__(engines.CascadeAtlas)
        j._loop, j._busy, j._turn, j._wake, j._is_speaking = mock.MagicMock(), False, threading.Lock(), None, False
        j._epoch, j._tts_futs = 0, set()
        j.ui, speaking, heard = mock.MagicMock(muted=False), threading.Event(), []

        def ask(text, run_tool, say, feed=None):
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
        j.llm.stop.assert_called_once()   # the first reply was cut, not waited for
        self.assertEqual(j._epoch, 1)
        j._loop.call_soon_threadsafe.assert_called_once_with(j._interrupt_now)

    def test_reply_starts_speaking_at_first_sentence_without_repeating_it(self):
        import engines
        said = []
        v = engines._Voice(lambda text, wait: said.append(text))
        for delta in ("Claro, señor. Ya ", "abrí Chrome y ", "busqué el vuelo."):
            v.feed(delta)
        self.assertEqual(said, ["Claro, señor."])   # spoken while the model still writes
        v.end("Claro, señor. Ya abrí Chrome y busqué el vuelo.")
        self.assertEqual(said[1:], ["Ya abrí Chrome y busqué el vuelo."])
        v.end("Listo.")   # nothing streamed for this message: said whole
        self.assertEqual(said[-1], "Listo.")
        for delta in (None, "Voy a revisar tu pantalla ahora. Un ", "momento.", None):   # text-only message, then a tool call
            v.feed(delta)
        self.assertEqual(said[-2:], ["Voy a revisar tu pantalla ahora.", "Un momento."])

    def test_silent_slow_tool_gets_a_short_ack_on_every_engine(self):
        import asyncio, engines
        m = self.main
        live = m.AtlasLive.__new__(m.AtlasLive)
        live.audio_in_queue = asyncio.Queue()
        with mock.patch.object(m, "ack_audio", return_value=b"\x01" * 5000):
            live._acknowledge("computer_settings")   # instant: no ack
            self.assertTrue(live.audio_in_queue.empty())
            live._acknowledge("web_search")
            self.assertEqual(live.audio_in_queue.qsize(), 2)
            live._acknowledge("file_controller")   # once per turn
            self.assertEqual(live.audio_in_queue.qsize(), 2)
            live._turn_audio = False   # new turn, and the model announced it itself
            live._spoke(mark=True)
            live._acknowledge("web_search")
            self.assertEqual(live.audio_in_queue.qsize(), 2)
        # cascade: the same reader as the reply says it, not a clip from another session
        cascade, said = engines.CascadeAtlas.__new__(engines.CascadeAtlas), []
        cascade._voice = engines._Voice(lambda text, wait: said.append(text))
        cascade._acknowledge("computer_settings")
        cascade._acknowledge("web_search")
        cascade._acknowledge("file_controller")
        self.assertEqual(said, [m.ACK[m._ack_lang()]])
        cascade._voice = engines._Voice(lambda text, wait: said.append(text))
        cascade._voice.end("Lo busco ahora mismo, señor.")
        cascade._acknowledge("web_search")
        self.assertEqual(said[1:], ["Lo busco ahora mismo, señor."])

    def test_gemini_voice_still_works_after_a_reply(self):
        import engines
        j = engines.CascadeAtlas.__new__(engines.CascadeAtlas)
        j._voice = engines._Voice(lambda text, wait: None)   # what every reply leaves behind
        import voice
        with mock.patch.object(voice, "get_config", return_value={}):
            cfg = engines.speech_config()   # 2.3.0 called the _Voice instead: Atlas fell back to edge-tts
        self.assertEqual(cfg.voice_config.prebuilt_voice_config.voice_name, self.main.VOICE_NAME)

    def test_hud_shows_speaking_while_a_step_runs(self):
        import asyncio, threading
        from types import SimpleNamespace as NS
        m = self.main
        j = m.AtlasLive.__new__(m.AtlasLive)
        j.ui, j._wake, j._speaking_lock, j._is_speaking = mock.MagicMock(muted=False), None, threading.Lock(), False
        j._acknowledge = lambda name: None
        states = lambda: [c.args[0] for c in j.ui.set_state.call_args_list]

        async def step(fc):
            j.set_speaking(True)    # "en eso estoy" starts playing while the step runs
            self.assertEqual(states()[-1], "SPEAKING")
            j.set_speaking(False)   # said: still working
            self.assertEqual(states()[-1], "THINKING")
            j.set_speaking(True)
        j._dispatch_tool = step
        j.ui.set_state.reset_mock()
        asyncio.run(j._execute_tool(NS(name="web_search")))
        self.assertEqual(states()[0], "THINKING")
        self.assertEqual(states()[-1], "SPEAKING")   # the step ended mid-sentence: still talking
        j.set_speaking(False)
        self.assertEqual(states()[-1], "LISTENING")
        j.ui.muted = True   # muted: it still talks and works, idle shows SILENCIADO
        j.set_speaking(True)
        self.assertEqual(states()[-1], "SPEAKING")
        j.set_speaking(False)
        self.assertEqual(states()[-1], "MUTED")

    def test_silent_read_is_asked_again(self):
        import asyncio, engines
        from types import SimpleNamespace as NS
        sent = []

        class Session:   # first turn: a thought only, no audio (1 in 3 short lines on the real API)
            async def __aenter__(self):
                return self
            async def __aexit__(self, *exc):
                return False
            async def send_client_content(self, turns, turn_complete):
                sent.append(turns["parts"][0]["text"])
            async def receive(self):
                if len(sent) > 1:
                    yield NS(server_content=NS(output_transcription=None, model_turn=NS(parts=[NS(inline_data=NS(data=b"\x10\x00" * 2000))])))

        j = engines.CascadeAtlas.__new__(engines.CascadeAtlas)
        j._tts_client = NS(aio=NS(live=NS(connect=lambda **kw: Session())))

        async def go():
            j.audio_in_queue = asyncio.Queue()
            await j._live_tts("Listo, señor.")
            return j.audio_in_queue.qsize()
        self.assertGreater(asyncio.run(go()), 0)
        self.assertEqual(sent, ["Listo, señor.", "Listo, señor."])

    def test_wake_word_interrupts_even_while_awake(self):
        import wakeword
        results = iter([None, None, True])
        g = wakeword.WakeGate(lambda pcm: next(results))
        g.keep_awake()
        self.assertEqual(g.feed(b"a"), [b"a"])   # awake: normal speech passes as it is
        self.assertEqual(g.listen(b"x"), [])
        self.assertEqual(g.listen(b"y"), [])
        self.assertEqual(g.listen(b"z"), [b"x", b"y", b"z"])   # "Atlas, para": the whole utterance
        self.assertTrue(self.main._names_itself("Soy A.T.L.A.S., su asistente"))

        self.assertFalse(self.main._names_itself("Claro, señor."))

    def test_barge_in_needs_the_user_louder_than_the_echo(self):
        import numpy as np
        tone = lambda amp: (np.sin(np.arange(1024) / 5) * amp).astype(np.int16).tobytes()
        b = self.main.BargeIn({})
        for _ in range(40):
            b.hear(tone(1500))                               # ATLAS's own voice coming back
        self.assertFalse(b.allows([tone(1500)] * 12))        # "hasta las..." heard as "Atlas": echo, ignored
        self.assertTrue(b.allows([tone(200)] * 3 + [tone(4500)] * 9))   # the user, close to the mic
        self.assertFalse(self.main.BargeIn({"barge_in": "off"}).allows([tone(9000)] * 9))
        self.assertTrue(self.main.BargeIn({"barge_in": "instant"}).allows([tone(10)]))
        self.assertEqual(self.main.BargeIn({"barge_in_level_mult": "x"}).mult, 2.0)

    def test_interrupt_drops_queued_audio_and_the_rest_of_the_live_reply(self):
        import asyncio
        m = self.main
        j = m.AtlasLive.__new__(m.AtlasLive)
        j._is_speaking, j._speaking_lock, j._wake, j._turn_done, j._drop_audio = True, __import__("threading").Lock(), None, False, False
        j.ui = mock.MagicMock(muted=False)
        j.audio_in_queue = asyncio.Queue()
        for _ in range(3):
            j.audio_in_queue.put_nowait(b"\x10\x27" * 4000)
        j._interrupt_now()
        self.assertEqual(j.audio_in_queue.qsize(), 1)   # only a short fade-out is left to play
        fade = j.audio_in_queue.get_nowait()
        self.assertIs(fade, j._fade)
        self.assertEqual((len(fade), fade[-2:]), (9600, b"\x00\x00"))
        self.assertFalse(j._is_speaking)
        self.assertTrue(j._drop_audio)   # the server is still sending this reply

    def test_interrupted_reply_goes_silent_but_its_step_still_runs(self):
        import engines
        said, logged, alive = [], [], [True]
        v = engines._Voice(lambda text, wait: said.append(text), lambda: alive[0], logged.append)
        v.end("Primera parte.")
        alive[0] = False
        v.feed("Segunda parte que ya no. ")
        v.end("Segunda parte que ya no.")
        self.assertEqual(said, ["Primera parte."])
        self.assertEqual(logged, said)   # the log shows what was said, not a reply cut short

        brain = engines._OpenAICompat.__new__(engines._OpenAICompat)
        brain.model, brain.tools, brain.limit, brain.system, brain.msgs, brain.sys_fn = "m", [], {}, "", [], str
        call = mock.MagicMock(id="1", function=mock.MagicMock(arguments="{}"))
        call.function.name = "web_search"
        reply = mock.MagicMock(usage=None, choices=[mock.MagicMock(message=mock.MagicMock(content="", tool_calls=[call]))])

        def create(**_):
            brain.stop()   # the user interrupts while the model is answering
            return reply
        brain.c = mock.MagicMock()
        brain.c.chat.completions.create.side_effect = create
        run_tool = mock.MagicMock(return_value="STILL RUNNING")
        self.assertEqual(brain.ask("busca algo", run_tool, said.append)[0], "")
        run_tool.assert_called_once()   # the step the model asked for is not dropped
        self.assertEqual(brain.c.chat.completions.create.call_count, 1)   # but no new round

    def test_step_interrupted_keeps_running_and_resumes_the_task(self):
        import asyncio, threading, engines
        j = engines.CascadeAtlas.__new__(engines.CascadeAtlas)
        j._epoch, j._turn_text = 0, "limpia los temporales y dime cuánto liberaste"
        loop = asyncio.new_event_loop()
        threading.Thread(target=loop.run_forever, daemon=True).start()
        j._loop, release = loop, threading.Event()

        async def execute(fc):
            await asyncio.to_thread(release.wait, 5)   # a slow script
            return mock.MagicMock(response={"result": "Freed 2 GB"})
        j._execute_tool = execute
        resumed = threading.Event()
        j._handle = mock.MagicMock(side_effect=lambda **kw: resumed.set())
        threading.Timer(0.3, lambda: setattr(j, "_epoch", 1)).start()   # the user interrupts
        self.assertEqual(j._run_tool("solve_task", {}), engines.STILL_RUNNING)
        self.assertFalse(resumed.is_set())   # still working
        release.set()
        self.assertTrue(resumed.wait(5))
        text = j._handle.call_args.kwargs["text"]
        self.assertIn("Freed 2 GB", text)
        self.assertIn("limpia los temporales", text)
        self.assertIn("cancel", text)
        self.assertFalse(j._handle.call_args.kwargs["remember"])
        loop.call_soon_threadsafe(loop.stop)

    def test_live_tool_dropped_by_interruption_reports_back_as_a_turn(self):
        import asyncio
        m = self.main
        j = m.AtlasLive.__new__(m.AtlasLive)
        j._cancelled_calls = {"c1"}
        j.session = mock.MagicMock(send_tool_response=mock.AsyncMock(), send_client_content=mock.AsyncMock())
        fc = mock.MagicMock(id="c1")
        fc.name = "solve_task"

        async def execute(call):
            fr = mock.MagicMock(id=call.id, response={"result": "done"})
            fr.name = call.name
            return fr
        j._execute_tool = execute
        asyncio.run(j._answer_tools([fc]))
        j.session.send_tool_response.assert_not_called()
        text = j.session.send_client_content.call_args.kwargs["turns"]["parts"][0]["text"]
        self.assertIn("solve_task: done", text)
        j._cancelled_calls = set()
        asyncio.run(j._answer_tools([fc]))
        j.session.send_tool_response.assert_called_once()

    def test_mic_reopens_when_turn_ends_after_last_chunk(self):
        import asyncio, threading
        m = self.main
        j = m.AtlasLive.__new__(m.AtlasLive)
        j._is_speaking, j._speaking_lock, j._wake, j._turn_done = False, threading.Lock(), None, False
        j.ui = mock.MagicMock(muted=False)

        async def run():
            j.audio_in_queue = asyncio.Queue()
            task = asyncio.create_task(j._play_audio())
            j.audio_in_queue.put_nowait(b"\0" * 64)
            self.assertTrue(await _until(lambda: j._is_speaking))
            j._turn_done = True   # streamed TTS: marked done only after its last chunk played
            # 5 s: well under the 10 s idle fallback, so this still proves _turn_done reopens the mic
            stuck = not await _until(lambda: not j._is_speaking)
            task.cancel()
            return stuck
        with mock.patch.object(m.sd, "RawOutputStream"):
            self.assertFalse(asyncio.run(run()))

    def test_lost_output_device_reopens_instead_of_ending_the_engine(self):
        import asyncio, threading
        m = self.main
        j = m.AtlasLive.__new__(m.AtlasLive)
        j._is_speaking, j._speaking_lock, j._wake, j._turn_done = False, threading.Lock(), None, False
        j.ui = mock.MagicMock(muted=False)
        gone, fresh = mock.MagicMock(latency=0.0), mock.MagicMock(latency=0.0)
        gone.write.side_effect = m.sd.PortAudioError("There is no driver installed on your system.")

        async def run():
            j.audio_in_queue = asyncio.Queue()
            task = asyncio.create_task(j._play_audio())
            j.audio_in_queue.put_nowait(b"\0" * 64)
            await _until(lambda: fresh.write.called or task.done())
            alive = not task.done()
            task.cancel()
            return alive
        with mock.patch.object(m.sd, "RawOutputStream", side_effect=[gone, fresh, fresh]):
            self.assertTrue(asyncio.run(run()))
        fresh.write.assert_called()   # the chunk played on the reopened output

    def test_boot_prompt_introduces_on_empty_memory_and_greets_on_login(self):
        m = self.main
        j = m.AtlasLive.__new__(m.AtlasLive)
        with mock.patch.object(m, "load_memory", return_value={"identity": {}, "style": {"humor": "low"}}):
            self.assertIn("first time", j._boot_prompt())
            self.assertIsNone(j._boot_prompt())   # once per run
        j._booted = False
        known = {"identity": {"name": {"value": "Pablo"}}}
        with mock.patch.object(m, "load_memory", return_value=known):
            with mock.patch.object(m.sys, "argv", ["atlas.pyw"]):
                self.assertIsNone(j._boot_prompt())   # a normal start says nothing
            j._booted = False
            with mock.patch.object(m.sys, "argv", ["atlas.pyw", "--autostart"]):
                self.assertIn("computer just started", j._boot_prompt())

    def test_task_status_tool_declared(self):
        names = {t["name"] for t in self.main.TOOL_DECLARATIONS}
        self.assertIn("task_status", names)


class AutostartTests(unittest.TestCase):
    def test_login_entry_written_and_removed_on_linux_and_mac(self):
        import autostart
        with tempfile.TemporaryDirectory() as d:
            for os_name, attr in (("Linux", "_DESKTOP"), ("Darwin", "_PLIST")):
                path = Path(d) / os_name / "entry"
                with mock.patch.object(autostart, "_OS", os_name), mock.patch.object(autostart, attr, path):
                    self.assertFalse(autostart.enabled())
                    autostart.set_enabled(True)
                    self.assertTrue(autostart.enabled())
                    self.assertIn("atlas.pyw", path.read_text(encoding="utf-8", errors="replace"))
                    autostart.set_enabled(False)
                    self.assertFalse(autostart.enabled())

    def test_windows_prefers_installed_launcher(self):
        import autostart
        with tempfile.TemporaryDirectory() as d:
            app = Path(d) / "app"; app.mkdir()
            (Path(d) / "Atlas.exe").touch()
            with mock.patch.object(autostart, "_OS", "Windows"), mock.patch.object(autostart, "BASE", app):
                self.assertEqual(autostart.command(), [str(Path(d) / "Atlas.exe"), "--autostart"])


class SelfImproveTests(unittest.TestCase):
    def setUp(self):
        from agent import self_improve
        self.si = self_improve
        self.base = Path(tempfile.mkdtemp())
        for rel, text in {"main.py": "x = 1\n", "core/prompt.txt": "Don't guess the user's feelings.\n", "memory/skills/s.py": "learned\n",
                          "python/lib.py": "runtime\n"}.items():
            (self.base / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.base / rel).write_text(text, encoding="utf-8")
        for p in (mock.patch.object(self_improve, "BASE", self.base),
                  mock.patch.object(self_improve, "SNAPS", self.base / "backups" / "code")):
            p.start()
            self.addCleanup(p.stop)

    def test_code_files_leave_out_user_data_and_runtime(self):
        self.assertEqual(self.si.code_files(), ["core/prompt.txt", "main.py"])

    def test_broken_change_is_rolled_back(self):
        def agent(_):
            (self.base / "main.py").write_text("x = (\n", encoding="utf-8")
            (self.base / "actions").mkdir()
            (self.base / "actions" / "new.py").write_text("y = 2\n", encoding="utf-8")
            return "added it"
        with mock.patch.object(self.si, "_agent", side_effect=agent):
            out = self.si.improve("add y")
        self.assertTrue(out.startswith("FAILED"), out)
        self.assertEqual((self.base / "main.py").read_text(encoding="utf-8"), "x = 1\n")
        self.assertFalse((self.base / "actions" / "new.py").exists())
        self.assertEqual((self.base / "memory/skills/s.py").read_text(encoding="utf-8"), "learned\n")
        self.assertIn("Nothing to undo", self.si.undo())

    def test_working_change_stays_and_undo_reverts_it(self):
        def agent(_):
            (self.base / "main.py").write_text("x = 2\n", encoding="utf-8")
            return "x is 2 now"
        with mock.patch.object(self.si, "_agent", side_effect=agent):
            out = self.si.improve("make x 2")
        self.assertTrue(out.startswith("Done and verified (1 files: main.py)"), out)
        self.assertEqual((self.base / "main.py").read_text(encoding="utf-8"), "x = 2\n")
        self.si.undo()
        self.assertEqual((self.base / "main.py").read_text(encoding="utf-8"), "x = 1\n")


class EngramTests(unittest.TestCase):
    """Failures are reported as failures, never as an empty result."""

    def test_failed_call_is_none_with_reason_and_search_says_failed(self):
        import subprocess
        bad = subprocess.CompletedProcess([], 1, "", "Update available: 1 -> 2\nTo update:\nerror: database is locked\n")
        with mock.patch.object(memory_manager, "ENGRAM_BIN", "engram"), \
             mock.patch.object(memory_manager.subprocess, "run", return_value=bad):
            self.assertIsNone(memory_manager._engram("context"))
            self.assertEqual(memory_manager.ENGRAM_ERROR, "error: database is locked")
            self.assertIn("FAILED", memory_manager.search_memory("anything"))
            self.assertIsNone(memory_manager._engram_save("t", "c", "notes"))
            self.assertEqual(memory_manager.recent_memories(), "")

    @unittest.skipUnless(memory_manager.ENGRAM_BIN, "engram not installed")
    def test_real_engram_save_is_confirmed_in_an_isolated_store(self):
        import os
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"ENGRAM_DATA_DIR": d}):
            obs = memory_manager._engram_save("isolated test", "zebra quartz 4711", "notes")
            self.assertIsInstance(obs, int)
            self.assertIn("zebra quartz 4711", memory_manager.search_memory("zebra quartz"))
            self.assertIsNotNone(memory_manager._engram("delete", str(obs), "--hard", project=False))
            self.assertIn("No memories found", memory_manager.search_memory("zebra quartz"))

    @unittest.skipUnless(memory_manager.ENGRAM_BIN, "engram not installed")
    def test_boot_diagnostic_round_trip_leaves_no_probe(self):
        import os
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"ENGRAM_DATA_DIR": d}):
            lines = memory_manager.diagnose()
            self.assertTrue(any(l.startswith("Engram escribir/leer/borrar: ok") for l in lines), lines)
            self.assertIn("No memories found", memory_manager.search_memory("atlas boot probe"))

    def test_boot_diagnostic_reports_failure(self):
        import subprocess
        bad = subprocess.CompletedProcess([], 1, "", "error: disk full\n")
        with mock.patch.object(memory_manager, "ENGRAM_BIN", "engram"), \
             mock.patch.object(memory_manager.subprocess, "run", return_value=bad):
            self.assertIn("FALLÓ al escribir (error: disk full)", memory_manager.diagnose()[-1])


if __name__ == "__main__":
    unittest.main()
