import json
import re
import sys
import threading
import subprocess
import tempfile
import os
import platform
from pathlib import Path
from typing import Callable

from agent.planner       import create_plan, replan
from agent.error_handler import analyze_error, ErrorDecision


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


# Action results that mean the action did not happen (actions return these instead of raising).
_FAILURE = re.compile(r"^\W*(could not|couldn't|cannot|can't|failed|failure|error|unable|no se pudo|not found)", re.I)


def _checked(tool: str, result) -> str:
    """Raises on an error-string result; never turns an empty result into a success."""
    text = str(result).strip() if result is not None else ""
    if not text:
        return f"{tool} ran but returned no result; outcome not verified."
    if _FAILURE.search(text):
        raise RuntimeError(text)
    return text


class NeedsConfirmation(Exception):
    """The generated script is risky: the user must say yes before it runs."""


# ponytail: keyword heuristic on our own model's code, not a sandbox; obfuscated code slips through.
_DANGEROUS = re.compile(
    r"os\.(remove|unlink|rmdir|kill)\b|shutil\.rmtree|\.unlink\(|\.rmdir\(|send2trash"
    r"|winreg\.(SetValue|DeleteKey|DeleteValue|CreateKey)|\breg(\.exe)?\s+(add|delete|import)"
    r"|Remove-Item|Clear-RecycleBin|\b(del|erase|rd|rmdir)\s+/|\bformat\s+[a-z]:|diskpart|bcdedit|cipher\s+/w"
    r"|shutdown|Restart-Computer|Stop-Computer|logoff"
    r"|taskkill|Stop-Process|\.kill\(\)|\.terminate\(\)"
    r"|winget|choco\s|msiexec|Install-Package|Set-MpPreference|advfirewall|Set-ExecutionPolicy|net\s+user"
    r"|\bpip\b.{0,30}\binstall|schtasks|netsh|icacls|takeown|\bsc(\.exe)?\s+(delete|config|stop|create)"
    r"|Set-ItemProperty|New-ItemProperty|crontab|systemctl|\bsudo\b|\brm\s+-|chmod|chown|os\.replace|shutil\.move",
    re.IGNORECASE)

_pending: tuple[str, str] | None = None   # (goal, code) waiting for the user's yes
MAX_SOLVE_ATTEMPTS = 3


def _write_script(goal: str, skills: list, failed: tuple[str, str] | None) -> str:
    from agent.llm import complete, strip_fences

    home = Path.home()
    desktop = home / "Desktop"
    if not desktop.exists():
        try:
            import winreg
            key     = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
            desktop = Path(winreg.QueryValueEx(key, "Desktop")[0])
        except Exception:
            pass

    system = (
        f"You write ONE Python script that does the task on this {platform.system()} computer.\n"
        "Line 1: '# PLAN: <one plain sentence saying what the script does>'.\n"
        "Line 2: '# RISK: safe' or '# RISK: dangerous' (dangerous = deletes or overwrites user files, "
        "changes system settings or the registry, installs software, kills processes, shuts down).\n"
        "Print the resulting state (e.g. 'Volume is now 40%'). If the task did not succeed, "
        "raise an exception: never print success you did not verify.\n"
        "Use the standard library or installed packages; if one is missing, install it with "
        "subprocess.run([sys.executable, '-m', 'pip', 'install', ...]).\n"
        "Return ONLY the code. No markdown, no backticks.\n\n"
        f"SYSTEM PATHS:\n  Desktop = r'{desktop}'\n  Downloads = r'{home / 'Downloads'}'\n"
        f"  Documents = r'{home / 'Documents'}'\n  Home = r'{home}'\n"
    )

    prompt = f"Task: {goal}"
    if skills:
        prompt += ("\n\nScripts that worked before for similar tasks. Reuse one as-is if it does this "
                   "exact task, adapt it if close, ignore it if unrelated:\n")
        prompt += "\n".join(f"### {task}\n{code}" for task, code in skills)
    if failed:
        prompt += (f"\n\nYour previous script failed:\n{failed[0]}\n\nError:\n{failed[1][-1500:]}\n\n"
                   "Write a DIFFERENT approach that avoids this error.")

    return strip_fences(complete(prompt, system=system, max_tokens=4096))


def _run_script(code: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
    print(f"[Solver] 🐍 Running {f.name}")
    try:
        # UTF-8 pipes: a cp1252 console made print() of accents/emoji crash otherwise-working scripts.
        r = subprocess.run([sys.executable, f.name], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=120, cwd=str(Path.home()), env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return r.returncode == 0, (r.stdout.strip() if r.returncode == 0 else r.stderr.strip() or r.stdout.strip())
    except subprocess.TimeoutExpired:
        return False, "Script timed out after 120 seconds."
    finally:
        try:
            os.unlink(f.name)
        except OSError:
            pass


def _risks(code: str) -> set[str]:
    """What makes a script need the user's yes: each dangerous call it makes."""
    found = {m.group(0).lower() for m in _DANGEROUS.finditer(code)}
    return found | {"marked dangerous"} if re.search(r"#\s*RISK:\s*dangerous", code, re.I) else found


def _plan_line(code: str) -> str:
    m = re.search(r"#\s*PLAN:\s*(.+)", code)
    return m.group(1).strip() if m else "run a custom script"


def solve_task(goal: str, speak: Callable | None = None, confirmed: bool = False) -> str:
    """Writes and runs Python for `goal`, retrying with each error, reusing and saving learned skills.
    Raises NeedsConfirmation before running risky code until called again with confirmed=True."""
    global _pending
    from memory.memory_manager import find_skills, save_skill

    pending, _pending = _pending, None
    allow_risk = confirmed and pending is not None   # yes only counts for a script the user heard about
    if allow_risk:
        goal = pending[0]
    if speak:
        speak("Working out a way to do this, sir.")

    skills = find_skills(goal)
    failed = None
    approved = _risks(pending[1]) if allow_risk else None
    for attempt in range(1, MAX_SOLVE_ATTEMPTS + 1):
        code = pending[1] if allow_risk and attempt == 1 else _write_script(goal, skills, failed)
        plan = _plan_line(code)
        risks = _risks(code)
        # A fix of the approved script may keep its risks; asking again for every retry was a loop the user could not exit.
        if risks and not (approved is not None and risks <= approved):
            _pending = (goal, code)
            raise NeedsConfirmation(
                f"NEEDS CONFIRMATION. The script would: {plan.rstrip('.')}. Tell the user that in one sentence "
                "and ask. Only after a clear yes, call solve_task again with confirmed='yes'.")

        ok, out = _run_script(code)
        print(f"[Solver] {'✅' if ok else '❌'} attempt {attempt}: {out[:200]}")
        if ok:
            if all(code != known for _, known in skills):
                save_skill(goal, plan, code)
            return out[:1000] or "Task completed."
        failed = (code, out)

    raise RuntimeError(f"Could not solve after {MAX_SOLVE_ATTEMPTS} attempts. Last error: {failed[1][-300:]}")


def _inject_context(params: dict, tool: str, step_results: dict, goal: str = "") -> dict:
    if not step_results:
        return params

    params = dict(params)

    if tool == "file_controller" and params.get("action") in ("write", "create_file"):
        content = params.get("content", "")
        if not content or len(content) < 50:
            all_results = [
                v for v in step_results.values()
                if v and len(v) > 100 and v not in ("Done.", "Completed.")
            ]
            if all_results:
                combined = "\n\n---\n\n".join(all_results)
                translated = _translate_to_goal_language(combined, goal)
                params["content"] = translated
                print(f"[Executor] 💉 Injected + translated content")

    return params
def _detect_language(text: str) -> str:
    from agent.llm import complete
    try:
        return complete(
            f"What language is this text written in? "
            f"Reply with ONLY the language name in English (e.g. Turkish, English, French).\n\n"
            f"Text: {text[:200]}", fast=True, max_tokens=20)
    except Exception:
        return "English"


def _translate_to_goal_language(content: str, goal: str) -> str:
    if not goal:
        return content
    try:
        from agent.llm import complete

        target_lang = _detect_language(goal)
        print(f"[Executor] 🌐 Translating to: {target_lang}")

        prompt = (
            f"You are a professional translator. "
            f"Translate the following text into {target_lang}.\n"
            f"IMPORTANT:\n"
            f"- Translate EVERYTHING, leave nothing in English\n"
            f"- Keep all facts, numbers, and data intact\n"
            f"- Keep the structure and formatting\n"
            f"- Output ONLY the translated text, nothing else\n\n"
            f"Text to translate:\n{content[:4000]}"
        )
        translated = complete(prompt, max_tokens=4096)
        print(f"[Executor] ✅ Translation done ({target_lang})")
        return translated
    except Exception as e:
        print(f"[Executor] ⚠️ Translation failed: {e}")
        return content

# tool name -> (module, function, extra kwargs). Every result goes through _checked().
_TOOLS = {
    "open_app":          ("actions.open_app",          "open_app",          ()),
    "web_search":        ("actions.web_search",        "web_search",        ()),
    "game_updater":      ("actions.game_updater",      "game_updater",      ("speak",)),
    "browser_control":   ("actions.browser_control",   "browser_control",   ()),
    "file_controller":   ("actions.file_controller",   "file_controller",   ()),
    "code_helper":       ("actions.code_helper",       "code_helper",       ("speak",)),
    "dev_agent":         ("actions.dev_agent",         "dev_agent",         ("speak",)),
    "send_message":      ("actions.send_message",      "send_message",      ()),
    "reminder":          ("actions.reminder",          "reminder",          ()),
    "youtube_video":     ("actions.youtube_video",     "youtube_video",     ()),
    "weather_report":    ("actions.weather_report",    "weather_action",    ()),
    "computer_settings": ("actions.computer_settings", "computer_settings", ()),
    "desktop_control":   ("actions.desktop",           "desktop_control",   ()),
    "computer_control":  ("actions.computer_control",  "computer_control",  ()),
    "flight_finder":     ("actions.flight_finder",     "flight_finder",     ("speak",)),
}


def _call_tool(tool: str, parameters: dict, speak: Callable | None) -> str:
    if tool in _TOOLS:
        import importlib
        mod, fn, extra = _TOOLS[tool]
        kw = {"speak": speak} if "speak" in extra else {}
        return _checked(tool, getattr(importlib.import_module(mod), fn)(parameters=parameters, player=None, **kw))

    if tool == "screen_process":
        from actions.screen_processor import screen_process
        return _checked(tool, screen_process(parameters=parameters, player=None))

    # cmd_control has no module of its own: it is a generated script behind the risk check.
    if tool in ("generated_code", "cmd_control"):
        description = parameters.get("description") or parameters.get("task") or ""
        if not description:
            raise ValueError(f"{tool} requires a 'description' or 'task' parameter.")
        return solve_task(description, speak=speak)

    # An unknown tool is a planning error: fail so the planner replans, never run free-form code.
    raise ValueError(f"Unknown tool '{tool}'. Use one of: {', '.join([*_TOOLS, 'screen_process', 'generated_code'])}.")

class AgentExecutor:

    MAX_REPLAN_ATTEMPTS = 2

    def execute(
        self,
        goal:        str,
        speak:       Callable | None        = None,
        cancel_flag: threading.Event | None = None,
        on_step:     Callable | None        = None,
    ) -> str:
        print(f"\n[Executor] 🎯 Goal: {goal}")

        replan_attempts = 0
        completed_steps = []
        step_results    = {}   # keyed 'replan.step' so a revised plan does not overwrite earlier results
        skipped_steps   = []
        plan            = create_plan(goal)

        while True:
            steps = plan.get("steps", [])

            if not steps:
                msg = "I couldn't create a valid plan for this task, sir."
                if speak: speak(msg)
                return msg

            success      = True
            failed_step  = None
            failed_error = ""

            for step in steps:
                if cancel_flag and cancel_flag.is_set():
                    if speak: speak("Task cancelled, sir.")
                    return "Task cancelled."

                step_num = step.get("step", "?")
                tool     = step.get("tool", "generated_code")
                desc     = step.get("description", "")
                params   = step.get("parameters", {})

                params = _inject_context(params, tool, step_results, goal=goal)
                if on_step: on_step(f"{step_num}/{len(steps)} {tool}: {desc}")

                print(f"\n[Executor] ▶️ Step {step_num}: [{tool}] {desc}")

                attempt = 1
                step_ok = False

                while attempt <= 3:
                    if cancel_flag and cancel_flag.is_set():
                        break
                    try:
                        result = _call_tool(tool, params, speak)
                        step_results[f"{replan_attempts}.{step_num}"] = result
                        completed_steps.append(step)
                        print(f"[Executor] ✅ Step {step_num} done: {str(result)[:100]}")
                        step_ok = True
                        break

                    except NeedsConfirmation as e:
                        if speak: speak(str(e))
                        return str(e)

                    except Exception as e:
                        error_msg = str(e)
                        print(f"[Executor] ❌ Step {step_num} attempt {attempt} failed: {error_msg}")

                        recovery = analyze_error(step, error_msg, attempt=attempt)
                        decision = recovery["decision"]
                        user_msg = recovery.get("user_message", "")

                        if speak and user_msg:
                            speak(user_msg)

                        if decision == ErrorDecision.RETRY:
                            attempt += 1
                            import time; time.sleep(2)
                            continue

                        elif decision == ErrorDecision.SKIP:
                            print(f"[Executor] ⏭️ Skipping step {step_num}")
                            skipped_steps.append(step)
                            step_ok = True
                            break

                        elif decision == ErrorDecision.ABORT:
                            msg = f"Task aborted, sir. {recovery.get('reason', '')}"
                            if speak: speak(msg)
                            return msg

                        else: 
                            fix_suggestion = recovery.get("fix_suggestion", "")
                            if fix_suggestion and tool != "generated_code":
                                try:
                                    res = solve_task(
                                        f"{desc}\n(The {tool} tool failed: {error_msg[:300]}. Hint: {fix_suggestion})",
                                        speak=speak,
                                    )
                                    step_results[f"{replan_attempts}.{step_num}"] = res
                                    completed_steps.append(step)
                                    step_ok = True
                                    break
                                except NeedsConfirmation as nc:
                                    if speak: speak(str(nc))
                                    return str(nc)
                                except Exception as fix_err:
                                    print(f"[Executor] ⚠️ Fix failed: {fix_err}")

                            failed_step  = step
                            failed_error = error_msg
                            success      = False
                            break

                if not step_ok and not failed_step:
                    failed_step  = step
                    failed_error = "Max retries exceeded"
                    success      = False

                if not success:
                    break

            if success:
                return self._summarize(goal, completed_steps, skipped_steps, step_results, speak)

            if replan_attempts >= self.MAX_REPLAN_ATTEMPTS:
                msg = f"Task failed after {replan_attempts} replan attempts, sir."
                if speak: speak(msg)
                return msg

            if speak: speak("Adjusting my approach, sir.")

            replan_attempts += 1
            plan = replan(goal, completed_steps, failed_step, failed_error)

    def _summarize(self, goal: str, completed_steps: list, skipped_steps: list,
                   step_results: dict, speak: Callable | None) -> str:
        """Factual summary: what ran, what was skipped, with the real step outputs attached."""
        from agent.llm import complete
        facts = "\n".join(f"- {s.get('description', '')}" for s in completed_steps)
        skipped = "\n".join(f"- {s.get('description', '')}" for s in skipped_steps)
        outputs = "\n".join(f"[{k}] {str(v)[:300]}" for k, v in step_results.items())
        fallback = (f"Finished {len(completed_steps)} steps for: {goal[:60]}."
                    + (f" Skipped {len(skipped_steps)} that failed." if skipped_steps else ""))
        try:
            summary = complete(
                f'User goal: "{goal}"\nCompleted steps:\n{facts or "(none)"}\n'
                f"Skipped after failing:\n{skipped or '(none)'}\nStep outputs:\n{outputs[:3000]}\n\n"
                "Write one natural sentence saying what was actually done, based only on the outputs. "
                "Mention anything skipped or unverified. Address the user as 'sir'.",
                fast=True, max_tokens=200)
        except Exception as e:
            print(f"[Executor] summary failed: {e}")
            summary = fallback
        if speak: speak(summary)
        return f"{summary}\n\nStep outputs:\n{outputs[:1500]}" if outputs else summary
