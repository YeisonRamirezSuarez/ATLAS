"""Skill reuse end to end: a saved skill is found (in Spanish or English, after a restart), run as-is
or in part, rejected with a reason, or passed over for a native tool. The coder (LLM) is scripted;
the scripts really run. Run: python -m unittest discover tests"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(errors="replace")   # logs print emoji; cp1252 consoles can't encode them

from agent import executor, llm
from memory import memory_manager as mm

ROOT = Path(__file__).resolve().parent.parent


class SkillReuseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.skills = self.tmp / "skills"
        self.mark = self.tmp / "side_effect.txt"   # what a skill's unrequested action leaves behind
        for target, kw in ((mm, {"SKILLS_DIR": self.skills, "BUILTIN_SKILLS_DIR": self.tmp / "builtin",
                                 "SKILL_LOG": self.tmp / "decisions.jsonl"}),
                           (executor, {"_native_sent": {}, "_pending": None})):
            for name, value in kw.items():
                p = mock.patch.object(target, name, value)
                p.start()
                self.addCleanup(p.stop)
        for name, value in (("_engram", ""), ("find_failures", [])):
            p = mock.patch.object(mm, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        for target, name in ((mm, "_engram_save_async"), (mm, "save_failure"), (executor, "_research")):
            p = mock.patch.object(target, name, return_value="")
            p.start()
            self.addCleanup(p.stop)
        self.prompts = []

    def coder(self, *answers):
        replies = iter(answers)
        return mock.patch.object(llm, "complete", side_effect=lambda p, **kw: self.prompts.append(p) or next(replies))

    def save(self, task, code):
        mm.save_skill(task, code.split("\n", 1)[0], code)
        return sorted(self.skills.glob("*.py"))

    def decisions(self):
        log = mm.SKILL_LOG
        return [json.loads(l)["decision"] for l in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    def composite(self):
        """Reports the RAM and also does something nobody asked for: writes a file."""
        return (f"# PLAN: show the RAM usage and save a report file\n# RISK: safe\nfrom pathlib import Path\n\n"
                f"MARK = Path(r'{self.mark}')\nUNIT = 1024 ** 2\n\n\ndef ram():\n    return f'RAM used: {{3 * UNIT // UNIT}} MB'\n\n\n"
                f"def report(text):\n    MARK.write_text(text)\n\n\nprint(ram())\nreport(ram())\n")

    def test_saved_skill_is_selected_run_as_is_and_checked(self):
        self.save("print the result of 17 times 23", "# PLAN: print 17*23\n# RISK: safe\nprint(17 * 23)")
        with self.coder("REUSE 1"):
            self.assertEqual(executor.solve_task("print the result of 17 times 23"), "391")   # really ran
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(len(list(self.skills.glob("*.py"))), 1)    # nothing new saved
        self.assertEqual(self.decisions(), ["reused"])

    def test_one_function_of_a_composite_skill_runs_alone(self):
        self.save("show the RAM usage and save a report", self.composite())
        ran = []
        real_run = executor._run_script
        with self.coder("PARTIAL 1 ram()"), \
             mock.patch.object(executor, "_run_script", side_effect=lambda c: ran.append(c) or real_run(c)):
            self.assertEqual(executor.solve_task("show the RAM usage"), "RAM used: 3 MB")
        self.assertFalse(self.mark.exists())                  # the report part never ran
        self.assertNotIn("report(", ran[0])
        self.assertIn("UNIT = 1024 ** 2", ran[0])             # the constant it needs came along
        self.assertIn("# RISK: safe", ran[0])                 # the skill's own risk mark is kept
        self.assertEqual(self.decisions(), ["partial"])
        # the separated part is saved and comes back for the same task next time
        self.assertTrue(any("run only ram()" in c for _, c in mm.find_skills("show the RAM usage")))

    def test_part_that_cannot_be_separated_is_not_forced(self):
        code = ("# PLAN: count files and delete old ones\nimport os\nfiles = os.listdir('.')\n\n"
                "def count():\n    return len(files)\n\nprint(count())\n")
        with self.assertRaises(ValueError):    # count() needs what the skill's own work sets
            executor._extract(code, "count()", "t")
        for bad in ("count(os.getcwd())", "os.listdir('.')", "nope()"):
            with self.assertRaises(ValueError, msg=bad):
                executor._extract(code, bad, "t")
        self.save("count the files and delete old ones", code)
        new = "# PLAN: count files\n# RISK: safe\n# NOT REUSED: 1: side_effects: count() needs the listing it makes\nprint(1)"
        with self.coder("PARTIAL 1 count()", new), mock.patch.object(executor, "_run_script", return_value=(True, "1")) as run:
            self.assertEqual(executor.solve_task("count the files"), "1")
        run.assert_called_once_with(new)
        self.assertIn("cannot run apart", self.prompts[1])
        self.assertEqual(self.decisions(), ["not_separable", "new"])

    def test_skill_with_unrequested_actions_is_not_run_and_the_reason_is_logged(self):
        self.save("show the RAM usage and save a report", self.composite())
        new = "# PLAN: show RAM\n# RISK: safe\n# NOT REUSED: 1: side_effects: it also writes a report file\nprint('RAM used: 3 MB')"
        with self.coder(new):
            self.assertEqual(executor.solve_task("show the RAM usage"), "RAM used: 3 MB")
        self.assertFalse(self.mark.exists())
        self.assertEqual(self.decisions(), ["new"])
        self.assertIn("side_effects", mm.SKILL_LOG.read_text(encoding="utf-8"))

    def test_new_script_without_a_reason_is_asked_once_more_then_flagged(self):
        self.save("show the RAM usage and save a report", self.composite())
        bare = "# PLAN: show RAM\nprint(3)"
        with self.coder(bare, bare), mock.patch.object(executor, "_run_script", return_value=(True, "3")), \
             mock.patch.object(mm, "save_skill"):
            executor.solve_task("show the RAM usage")
        self.assertEqual(len(self.prompts), 2)
        self.assertIn("NOT REUSED", self.prompts[1])
        self.assertEqual(self.decisions(), ["unexplained"])

    def test_native_tool_preferred_nothing_runs_and_the_reason_is_logged(self):
        with self.coder("NATIVE windows_personalization: it sets the accent color and reads it back"), \
             mock.patch.object(executor, "_run_script") as run:
            with self.assertRaises(executor.UseNativeTool) as e:
                executor.solve_task("set the accent color to blue")
        run.assert_not_called()
        mm.save_failure.assert_not_called()            # not a failure of the task
        self.assertIn("Call windows_personalization", str(e.exception))
        self.assertEqual(self.decisions(), ["native"])

    def test_native_tool_that_already_failed_is_not_sent_back(self):
        script = "# PLAN: explain\nraise SystemExit('use the windows_personalization tool')"
        for goal, sent in (("set the accent color to blue; windows_personalization failed: access denied", {}),
                           ("set the accent color to blue", {"windows_personalization": executor.time.time()})):
            executor._native_sent.clear()
            executor._native_sent.update(sent)   # the second: the same tool was sent back moments ago
            self.prompts.clear()
            with self.coder("NATIVE windows_personalization: accent", script), \
                 mock.patch.object(executor, "_run_script", return_value=(True, "x")), mock.patch.object(mm, "save_skill"):
                executor.solve_task(goal)
            self.assertIn("already tried", self.prompts[1])
        with self.coder("NATIVE magic_wand: it is magic", script), \
             mock.patch.object(executor, "_run_script", return_value=(True, "x")), mock.patch.object(mm, "save_skill"):
            executor.solve_task("draw a circle")
        self.assertIn("not a tool", self.prompts[-1])
        self.assertEqual(self.decisions().count("native_rejected"), 2)

    def test_spanish_and_english_requests_find_the_same_skills(self):
        self.save("Turn off the Bluetooth radio on this Windows 11 PC", "# PLAN: bluetooth off\nprint('off')")
        self.save("Change the Windows 11 system accent color to green", "# PLAN: accent\nprint('green')")
        self.save("descarga las imágenes de la carpeta compartida", "# PLAN: descargar\nprint('ok')")
        for es, en, task in (("apaga el bluetooth", "turn the bluetooth off", "Turn off the Bluetooth"),
                             ("cambia el color de acento a azul", "change the accent color to blue", "Change the Windows"),
                             ("descarga las imagenes de esa carpeta", "download the images from the folder", "descarga")):
            for goal in (es, en):
                found = mm.find_skills(goal)
                self.assertTrue(found and found[0][0].startswith(task), (goal, found))
        for goal in ("reserva un vuelo a Madrid", "book a flight to Madrid"):
            self.assertEqual(mm.find_skills(goal), [], goal)

    def test_skill_found_after_a_restart_without_engram(self):
        self.save("muestra el uso de la RAM", "# PLAN: ram\nprint(3)")
        probe = ("import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1])\n"
                 "from memory import memory_manager as mm\n"
                 "mm.SKILLS_DIR, mm.BUILTIN_SKILLS_DIR, mm.ENGRAM_BIN = Path(sys.argv[2]), Path(sys.argv[3]), None\n"
                 "print([t for t, _ in mm.find_skills('show me the RAM usage')])")
        out = subprocess.run([sys.executable, "-c", probe, str(ROOT), str(self.skills), str(self.tmp / "builtin")],
                             capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertIn("muestra el uso de la RAM", out.stdout, out.stderr)

    def test_solved_task_is_reused_next_time_not_written_again(self):
        code = "# PLAN: print 17*23\n# RISK: safe\nprint(17 * 23)"
        with self.coder(code):
            self.assertEqual(executor.solve_task("print the result of 17 times 23"), "391")
        with self.coder("REUSE 1"):
            self.assertEqual(executor.solve_task("imprime el resultado de 17 por 23"), "391")
        self.assertEqual(len(list(self.skills.glob("*.py"))), 1)
        self.assertEqual(self.decisions(), ["none_found", "reused"])
        self.assertIn("REUSE <n>", self.prompts[1])


if __name__ == "__main__":
    unittest.main()
