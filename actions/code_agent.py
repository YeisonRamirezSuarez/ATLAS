"""
Long programming work in the user's own projects (code reviews, features, fixes, refactors, plans)
handed to Claude Code in the background, with the user's skills (superpowers...) and subagents of
its own. Every step of the run, and of each subagent it launches, shows live in OPERACIONES; the
whole run is logged to Documents/ATLAS agents/<id>.log. A run that ends asking the user something
goes on with the answer (continue_id), in the same Claude Code session.
"""
import asyncio
import threading
import time
from datetime import datetime
from pathlib import Path

LOG_DIR   = Path.home() / "Documents" / "ATLAS agents"
PLUGINS   = ("superpowers", "code-review", "frontend-design")   # dev skills; caveman/ponytail would restyle it
TOOLS     = ["Read", "Edit", "Write", "Glob", "Grep", "Bash", "Skill", "Agent", "TodoWrite", "WebSearch", "WebFetch"]
MAX_TURNS = 400

SYSTEM = """You are a senior developer working for ATLAS, the user's voice assistant, in the project folder
that is your working directory. Do the task completely with real tools: read the code, change it, run
its tests or build.
- Use the user's skills (Skill tool) when they fit, e.g. superpowers: brainstorming, writing-plans,
  subagent-driven-development, requesting-code-review, systematic-debugging. Launch subagents (Agent
  tool) for independent pieces of work, as those skills say.
- Nobody can answer you mid-run. When you need the user's decision (a design question, approval of a
  plan), stop there and end with that question: ATLAS asks the user and continues this run with the answer.
- No git commit, push, merge or branch deletion unless the task asks for it; never reset --hard or force-push.
- Stay in this folder unless the task names other paths. Never touch ATLAS itself or stop its processes.
- End with a short plain summary for ATLAS to tell the user: what you did, what is left, any question."""

_runs: dict[str, dict] = {}   # run id -> {"session": Claude Code session id, "folder": str}


def _step(name: str, inp: dict) -> str:
    """One line for OPERACIONES: the tool and what it is working on."""
    todo = next((t.get("content") for t in inp.get("todos") or [] if t.get("status") == "in_progress"), "")
    what = (inp.get("description") or inp.get("command") or inp.get("file_path") or inp.get("pattern")
            or inp.get("skill") or inp.get("url") or inp.get("query") or todo or "")
    return f"{name}: {' '.join(str(what).split())}"[:90]


async def _run(rid: str, task: str, folder: str, session, show, note) -> tuple[str, bool]:
    """Streams the run: main steps update row rid, each subagent gets its own row until it returns."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, UserMessage, query
    from agent.llm import SKILL_SOURCES, is_auth_error, subscription_options
    from agent.self_improve import _model
    model, env = _model()
    opts = subscription_options(SYSTEM, model, persist=True, cwd=folder, max_turns=MAX_TURNS, tools=TOOLS,
                                allowed_tools=TOOLS, setting_sources=SKILL_SOURCES, plugins=PLUGINS,
                                permission_mode="bypassPermissions", resume=session)
    opts.env.update(env)
    subs = {}   # Agent tool_use id -> its OPERACIONES row
    out = ("FAILED: the run ended without a result", False)
    try:
        async for m in query(prompt=task, options=opts):   # read to the end: leaving early breaks the SDK's generator
            if isinstance(m, AssistantMessage):
                row = subs.get(m.parent_tool_use_id, rid)
                for b in m.content:
                    if hasattr(b, "name"):   # a tool call
                        if b.name in ("Agent", "Task"):
                            subs[b.id] = f"{rid}/{b.id[-6:]}"
                            show(subs[b.id], f"└ subagente: {b.input.get('description') or 'tarea'}", "empezando")
                            note(f"[{row}] subagente {subs[b.id]}: {b.input.get('description')}")
                        else:
                            show(row, None, _step(b.name, b.input))
                            note(f"[{row}] {_step(b.name, b.input)}")
                    elif getattr(b, "text", "").strip():
                        show(row, None, " ".join(b.text.split())[:90])
                        note(f"[{row}] {b.text.strip()}")
            elif isinstance(m, UserMessage) and isinstance(m.content, list):
                for b in m.content:
                    if getattr(b, "tool_use_id", None) in subs:   # a subagent returned
                        note(f"[{subs[b.tool_use_id]}] terminado")
                        show(subs.pop(b.tool_use_id), "")
            elif isinstance(m, ResultMessage):
                _runs[rid] = {"session": m.session_id, "folder": folder}
                err = f"{m.result} {m.errors}"
                out = (f"FAILED: {err}", is_auth_error(err)) if m.is_error else ((m.result or "").strip(), False)
    finally:
        for row in subs.values():
            show(row, "")
    return out


def _work(rid: str, task: str, folder: str, session, player, deliver):
    from agent.llm import subscription_login
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / f"{rid}.log"
    goals = {}

    def show(row, goal, step=""):   # goal None: same row, new step; "" removes the row
        goals[row] = goals.get(row) if goal is None else goal
        if player and goals[row] is not None:
            player.set_job(row, goals[row], step)

    def note(line):
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%H:%M:%S} {line}\n")

    note(f"TASK in {folder}: {task}")
    show(rid, f"code_agent {rid}: {task[:50]}", "empezando")
    try:
        out, auth = asyncio.run(_run(rid, task, folder, session, show, note))
        if auth and subscription_login():   # login missing or expired: sign in once, then retry
            out, auth = asyncio.run(_run(rid, task, folder, session, show, note))
    except Exception as e:
        out = f"FAILED: {type(e).__name__}: {e}"
    finally:
        show(rid, "")
    note(f"RESULT: {out}")
    if player:
        player.write_log(f"SIS: code_agent {rid}: {out[:200]}")
    if deliver:
        deliver(f"[code_agent {rid} finished] {out[:3000]}\nIf it ends with a question for the user, ask it and "
                f"send the answer as task to code_agent with continue_id='{rid}'. Otherwise tell the user briefly.")


def code_agent(parameters: dict, player=None, deliver=None) -> str:
    """Tool entry: starts the run in its own thread and returns at once."""
    task = str(parameters.get("task") or "").strip()
    rid = str(parameters.get("continue_id") or "").strip()
    prev = _runs.get(rid)
    if rid and not prev:
        return f"FAILED: no earlier code_agent run '{rid}' in this session; start a new one with the full task."
    folder = str(parameters.get("folder") or (prev or {}).get("folder") or "").strip().strip('"')
    if not task:
        return "code_agent needs the task: what to do in the project."
    if not folder or not Path(folder).expanduser().is_dir():
        return f"FAILED: no project folder at '{folder}'. Ask the user which folder to work in."
    rid = rid or time.strftime("%H%M%S")
    folder = str(Path(folder).expanduser().resolve())
    threading.Thread(target=_work, args=(rid, task, folder, prev and prev["session"], player, deliver),
                     daemon=True).start()
    return (f"Started in the background (code_agent id {rid}). It is NOT done yet: tell the user briefly. Each "
            f"step and subagent shows in OPERACIONES, the full log is {LOG_DIR / f'{rid}.log'}, and the "
            "result will be sent to you when it ends.")
