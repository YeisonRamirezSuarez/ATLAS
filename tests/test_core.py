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
            asyncio.run(j._tts_now("hola"))
            asyncio.run(j._tts_now("otra"))   # inside the quota window: straight to Live
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

    def test_message_typed_while_speaking_interrupts_and_is_answered(self):
        import threading, engines
        j = engines.CascadeJarvis.__new__(engines.CascadeJarvis)
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
        live = m.JarvisLive.__new__(m.JarvisLive)
        cascade = engines.CascadeJarvis.__new__(engines.CascadeJarvis)
        for j in (live, cascade):
            j.audio_in_queue = asyncio.Queue()
        with mock.patch.object(m, "ack_audio", return_value=b"\x01" * 5000):
            for j in (live, cascade):
                cascade._voice = engines._Voice(lambda text, wait: None)
                j._acknowledge("computer_settings")   # instant: no ack
                self.assertTrue(j.audio_in_queue.empty())
                j._acknowledge("web_search")
                self.assertEqual(j.audio_in_queue.qsize(), 2)
                j._acknowledge("file_controller")   # once per turn
                self.assertEqual(j.audio_in_queue.qsize(), 2)
            live._turn_audio = False   # new turn, and the model announced it itself
            cascade._voice = engines._Voice(lambda text, wait: None)
            cascade._voice.end("Lo busco ahora mismo, señor.")
            live._spoke(mark=True)
            for j in (live, cascade):
                j._acknowledge("web_search")
                self.assertEqual(j.audio_in_queue.qsize(), 2)

    def test_gemini_voice_still_works_after_a_reply(self):
        import engines
        j = engines.CascadeJarvis.__new__(engines.CascadeJarvis)
        j._voice = engines._Voice(lambda text, wait: None)   # what every reply leaves behind
        cfg = j._speech_config()   # 2.3.0 called the _Voice instead: Jarvis fell back to edge-tts
        self.assertEqual(cfg.voice_config.prebuilt_voice_config.voice_name, engines.VOICE_NAME)

    def test_wake_word_interrupts_even_while_awake(self):
        import wakeword
        results = iter([None, None, True])
        g = wakeword.WakeGate(lambda pcm: next(results))
        g.keep_awake()
        self.assertEqual(g.feed(b"a"), [b"a"])   # awake: normal speech passes as it is
        self.assertEqual(g.listen(b"x"), [])
        self.assertEqual(g.listen(b"y"), [])
        self.assertEqual(g.listen(b"z"), [b"x", b"y", b"z"])   # "Jarvis, para": the whole utterance
        self.assertTrue(self.main._names_itself("Soy J.A.R.V.I.S., su asistente"))
        self.assertFalse(self.main._names_itself("Claro, señor."))

    def test_interrupt_drops_queued_audio_and_the_rest_of_the_live_reply(self):
        import asyncio
        m = self.main
        j = m.JarvisLive.__new__(m.JarvisLive)
        j._is_speaking, j._speaking_lock, j._wake, j._turn_done, j._drop_audio = True, __import__("threading").Lock(), None, False, False
        j.ui = mock.MagicMock(muted=False)
        j.audio_in_queue = asyncio.Queue()
        for _ in range(3):
            j.audio_in_queue.put_nowait(b"\x00" * 64)
        j._interrupt_now()
        self.assertTrue(j.audio_in_queue.empty())
        self.assertFalse(j._is_speaking)
        self.assertTrue(j._drop_audio)   # the server is still sending this reply

    def test_interrupted_reply_goes_silent_but_its_step_still_runs(self):
        import engines
        said, alive = [], [True]
        v = engines._Voice(lambda text, wait: said.append(text), lambda: alive[0])
        v.end("Primera parte.")
        alive[0] = False
        v.feed("Segunda parte que ya no. ")
        v.end("Segunda parte que ya no.")
        self.assertEqual(said, ["Primera parte."])

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
        j = engines.CascadeJarvis.__new__(engines.CascadeJarvis)
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
        j = m.JarvisLive.__new__(m.JarvisLive)
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
        j = m.JarvisLive.__new__(m.JarvisLive)
        j._is_speaking, j._speaking_lock, j._wake, j._turn_done = False, threading.Lock(), None, False
        j.ui = mock.MagicMock(muted=False)

        async def run():
            j.audio_in_queue = asyncio.Queue()
            task = asyncio.create_task(j._play_audio())
            j.audio_in_queue.put_nowait(b"\0" * 64)
            await asyncio.sleep(0.1)
            self.assertTrue(j._is_speaking)
            j._turn_done = True   # streamed TTS: marked done only after its last chunk played
            await asyncio.sleep(0.6)
            stuck = j._is_speaking
            task.cancel()
            return stuck
        with mock.patch.object(m.sd, "RawOutputStream"):
            self.assertFalse(asyncio.run(run()))

    def test_boot_prompt_introduces_on_empty_memory_and_greets_on_login(self):
        m = self.main
        j = m.JarvisLive.__new__(m.JarvisLive)
        with mock.patch.object(m, "load_memory", return_value={"identity": {}, "style": {"humor": "low"}}):
            self.assertIn("first time", j._boot_prompt())
            self.assertIsNone(j._boot_prompt())   # once per run
        j._booted = False
        known = {"identity": {"name": {"value": "Pablo"}}}
        with mock.patch.object(m, "load_memory", return_value=known):
            with mock.patch.object(m.sys, "argv", ["jarvis.pyw"]):
                self.assertIsNone(j._boot_prompt())   # a normal start says nothing
            j._booted = False
            with mock.patch.object(m.sys, "argv", ["jarvis.pyw", "--autostart"]):
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
                    self.assertIn("jarvis.pyw", path.read_text(encoding="utf-8", errors="replace"))
                    autostart.set_enabled(False)
                    self.assertFalse(autostart.enabled())

    def test_windows_prefers_installed_launcher(self):
        import autostart
        with tempfile.TemporaryDirectory() as d:
            app = Path(d) / "app"; app.mkdir()
            (Path(d) / "Jarvis.exe").touch()
            with mock.patch.object(autostart, "_OS", "Windows"), mock.patch.object(autostart, "BASE", app):
                self.assertEqual(autostart.command(), [str(Path(d) / "Jarvis.exe"), "--autostart"])


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
            self.assertTrue(any(l.startswith("Engram write/read/delete: ok") for l in lines), lines)
            self.assertIn("No memories found", memory_manager.search_memory("jarvis boot probe"))

    def test_boot_diagnostic_reports_failure(self):
        import subprocess
        bad = subprocess.CompletedProcess([], 1, "", "error: disk full\n")
        with mock.patch.object(memory_manager, "ENGRAM_BIN", "engram"), \
             mock.patch.object(memory_manager.subprocess, "run", return_value=bad):
            self.assertIn("FAILED at write (error: disk full)", memory_manager.diagnose()[-1])


if __name__ == "__main__":
    unittest.main()
