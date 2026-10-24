"""
JARVIS improving its own code, only when the user asks for it: a new ability it lacks or a fix
to one it has, without waiting for a release.

Each change: snapshot of the code -> Claude Code edits it with real tools (read, edit, run the
tests) -> compile + test check -> rolled back on its own if anything broke. undo() puts the
code from before the last change back. User data (keys, memory, learned skills) is never in a
snapshot, so neither a change nor an undo can touch it.
"""
import asyncio
import os
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

BASE  = Path(__file__).resolve().parent.parent
SNAPS = BASE / "backups" / "code"
KEEP  = 10   # newest snapshots kept
# Bundled runtimes and user data, never code JARVIS owns. memory/skills: scripts this PC learned.
SKIP  = {"python", "backups", "__pycache__", ".git", ".venv", "skills", "ms-playwright", "models", "bin",
         "dist", "build", "node_modules"}
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

SYSTEM = """You are improving JARVIS, the Python voice assistant whose install folder is your working
directory, at its user's request. Make exactly the change asked for, completely, and nothing else.
- Read the code first. Tools: declaration in TOOL_DECLARATIONS and a branch in _dispatch_tool (main.py),
  the work in a module under actions/. Behavior rules: core/prompt.txt. Match the surrounding style.
- Never touch config/api_keys.json, memory/long_term.json, memory/skills/, python/ or backups/.
- Never remove or weaken safety: confirmations, trusted cases, prompt-injection guards, honesty rules.
- Install a package only when indispensable: {python} -m pip install <name>.
- Add or adjust a test in tests/ when practical, run `{python} -m unittest discover -s tests` and fix
  every failure before you finish.
- End with one or two plain sentences in English: what changed and how the user uses it."""


def _python() -> str:
    """Console interpreter next to the running one (JARVIS itself may run under pythonw)."""
    exe = Path(sys.executable)
    con = exe.with_name("python.exe")
    return str(con if exe.name.lower() == "pythonw.exe" and con.exists() else exe)


def code_files(base: Path = None) -> list[str]:
    base = base or BASE
    out = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP]
        rel = Path(root).relative_to(base)
        for f in files:
            if f.endswith((".py", ".pyw")) or (rel.parts[:1] == ("core",) and f.endswith(".txt")):
                out.append((rel / f).as_posix())
    return sorted(out)


def snapshot(label: str = "before-change") -> Path:
    SNAPS.mkdir(parents=True, exist_ok=True)
    out = SNAPS / f"{datetime.now():%Y%m%d-%H%M%S-%f}-{label}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in code_files():
            z.write(BASE / rel, rel)
    for old in sorted(SNAPS.glob("*.zip"))[:-KEEP]:
        old.unlink()
    return out


def restore(snap: Path) -> None:
    """Code back to the snapshot: files added since are deleted, changed ones rewritten."""
    with zipfile.ZipFile(snap) as z:
        names = set(z.namelist())
        for rel in set(code_files()) - names:
            (BASE / rel).unlink()
        z.extractall(BASE)


def _check(tests: bool) -> tuple[bool, str]:
    run = lambda *a, t=120: subprocess.run([_python(), *a], cwd=BASE, capture_output=True, text=True,
                                           encoding="utf-8", errors="replace", timeout=t, creationflags=NO_WINDOW,
                                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    r = run("-m", "py_compile", *(f for f in code_files() if f.endswith((".py", ".pyw"))))   # prompt.txt is not code
    if r.returncode == 0 and tests and (BASE / "tests").is_dir():
        r = run("-m", "unittest", "discover", "-s", "tests", t=900)
    return r.returncode == 0, (r.stdout + r.stderr)[-1500:]


def _model() -> tuple[str, dict]:
    from config import get_config, get_engine
    provider, model = get_engine()
    env = {}
    if provider == "claude":   # an API key instead of the subscription
        env["ANTHROPIC_API_KEY"] = (get_config().get("anthropic_api_key") or "").strip()
    return (model if provider in ("claude", "claude_sub") else "claude-opus-5-5"), env


async def _agent_once(request: str) -> tuple[str, bool]:
    from claude_agent_sdk import ResultMessage, query
    from agent.llm import is_auth_error, subscription_options
    model, env = _model()
    opts = subscription_options(SYSTEM.replace("{python}", _python()), model, cwd=str(BASE), max_turns=80,
                                tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
                                allowed_tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
                                permission_mode="bypassPermissions")
    opts.env.update(env)
    out = ("", False)
    async for m in query(prompt=request, options=opts):   # read to the end: leaving early breaks the SDK's generator
        if isinstance(m, ResultMessage):
            err = f"{m.result} {m.errors}"
            out = (err, is_auth_error(err)) if m.is_error else ((m.result or "").strip(), False)
    return out


def _agent(request: str) -> str:
    from agent.llm import subscription_login
    out, auth = asyncio.run(_agent_once(request))
    if auth and subscription_login():   # login missing or expired: sign in once, then retry
        out, auth = asyncio.run(_agent_once(request))
    if auth:
        raise RuntimeError("Claude sign-in needed for self-improvement (Claude Code login)")
    return out


def improve(request: str) -> str:
    before = {f: (BASE / f).read_bytes() for f in code_files()}
    snap = snapshot()
    tests_ok = _check(tests=True)[0]   # tests already failing: gate on compiling only
    try:
        summary = _agent(request)
    except Exception as e:
        restore(snap)
        snap.unlink()
        return f"FAILED: could not change the code ({e}). Nothing was changed."
    ok, out = _check(tests=tests_ok)
    if not ok:
        restore(snap)
        snap.unlink()
        return f"FAILED: the change broke the checks, so it was rolled back. Last output: {out[-400:]}"
    changed = [f for f in code_files() if before.get(f) != (BASE / f).read_bytes()]
    changed += [f for f in before if not (BASE / f).exists()]
    if not changed:
        snap.unlink()
        return f"No code was changed. {summary}"
    return (f"Done and verified ({len(changed)} files: {', '.join(changed[:6])}). {summary} "
            "It loads after a restart: ask the user, then call self_improve action=restart.")


def undo() -> str:
    snaps = sorted(SNAPS.glob("*.zip"))
    if not snaps:
        return "Nothing to undo: no self-improvement snapshot."
    restore(snaps[-1])
    snaps[-1].unlink()   # the next undo goes one change further back
    return "Code restored to before the last self-improvement. It loads after a restart."
