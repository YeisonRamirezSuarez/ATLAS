"""
Plain text completions on the provider the user picked in setup (config.get_engine).

Every background brain (planner, script writer, error analyst, memory, summaries) goes
through complete(), so the selected provider drives everything and a missing key fails
loudly instead of silently switching to another vendor.
"""
import json
import re
import subprocess
import threading
import time
from pathlib import Path

from config import PROVIDERS, get_config, get_effort, get_engine, is_windows

# Gemini's Live model is audio-only; text calls use its text siblings (same vendor, same key).
GEMINI_TEXT = {True: "gemini-flash-lite-latest", False: "gemini-flash-latest"}
TIMEOUT = 60  # seconds per request


class LLMError(RuntimeError):
    """No usable answer from the selected provider (missing key, empty reply...)."""


def describe() -> str:
    """'provider/model' used for text calls, for logs and the UI."""
    provider, model = get_engine()
    return f"{provider}/{GEMINI_TEXT[False] if provider == 'gemini' else model}"


def role_engine(role: str | None) -> tuple[str, str] | None:
    """(provider, model) the user assigned to a background role in api_keys.json, e.g.
    "role_models": {"agent": {"provider": "claude", "model": "claude-haiku-5-5"}, "coder": {...}}.
    Opt-in only: without it every role uses the selected engine, as before."""
    try:
        r = (get_config().get("role_models") or {}).get(role or "") or {}
    except Exception:
        return None
    p = r.get("provider") if isinstance(r, dict) else None
    if p not in PROVIDERS:
        return None
    return p, r.get("model") or (GEMINI_TEXT[False] if p == "gemini" else PROVIDERS[p]["model"])


def complete(prompt: str, system: str = "", fast: bool = False, max_tokens: int = 2048, role: str | None = None) -> str:
    t0 = time.monotonic()
    try:
        text, used = _complete(prompt, system, fast, max_tokens, role)
    except Exception as e:
        print(f"[LLM] ❌ {role or 'text'} failed after {time.monotonic() - t0:.1f}s: {str(e)[:200]}")
        raise
    print(f"[LLM] {role or 'text'} → {used} ({time.monotonic() - t0:.1f}s)")
    return text


def _complete(prompt: str, system: str, fast: bool, max_tokens: int, role: str | None) -> tuple[str, str]:
    provider, model = get_engine()
    override = role_engine(role)
    # FAST switch in the side panel: off = quick helper calls think as deep as the rest.
    fast = fast and get_config().get("fast", True)
    # Only an effort the user picked (for the selected engine); quick helper calls keep the model's default depth.
    effort = None if fast or (override and override[0] != provider) else get_effort(default=False)
    if override:
        provider, model = override
    spec = PROVIDERS[provider]
    key = (get_config().get(spec["key"]) or "").strip() if spec["key"] else ""
    if spec["key"] and not key:
        raise LLMError(f"{spec['label']} is the selected provider but '{spec['key']}' is empty in api_keys.json")

    if provider == "gemini":
        from google import genai
        from google.genai import types
        model = model if override else GEMINI_TEXT[fast]
        # Kept in a variable: a temporary Client is collected and closes its connection before the request.
        client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=TIMEOUT * 1000))
        r = client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system or None, max_output_tokens=max_tokens))
        text = r.text
    elif provider == "claude_sub":
        text = _subscription_complete(prompt, system, model, effort)
    elif provider == "claude":
        import anthropic
        kw = {"system": system} if system else {}
        if effort:
            kw["extra_body"] = {"output_config": {"effort": effort}}
        r = anthropic.Anthropic(api_key=key, timeout=TIMEOUT).messages.create(
            model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}], **kw)
        text = "".join(b.text for b in r.content if b.type == "text")
    else:
        import openai
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        url = (provider == "ollama" and get_config().get("base_url")) or spec.get("url")
        limit = {"max_completion_tokens": max_tokens} if url is None else {"max_tokens": max_tokens}
        if effort:
            limit["reasoning_effort"] = effort
        r = openai.OpenAI(api_key=key or "local", base_url=url, timeout=TIMEOUT).chat.completions.create(
            model=model, messages=msgs, **limit)
        text = r.choices[0].message.content

    text = (text or "").strip()
    if not text:
        raise LLMError(f"{provider}/{model} returned an empty answer")
    return text, f"{provider}/{model}"


SKILL_SOURCES = ["user", "project"]   # ~/.claude (never CLAUDE_CONFIG_DIR, see config) + <cwd>/.claude


def _skills_only(keep=()) -> str:
    """--settings for a session that loads ~/.claude only for its skills: the user's hooks and the plugins
    not named in keep (caveman, superpowers...) would inject their own instructions into ATLAS's prompt."""
    try:
        plugins = json.loads((Path.home() / ".claude" / "settings.json").read_text(encoding="utf-8"))["enabledPlugins"]
    except Exception:
        plugins = {}
    return json.dumps({"disableAllHooks": True, "enabledPlugins": {k: k.split("@")[0] in keep for k in plugins}})


def trust_folder(folder, cfg: Path = None) -> None:
    """Claude Code's 'trust this folder' answer, saved the way its dialog does in ~/.claude.json: a headless
    run in an untrusted folder ignores the project's .claude/settings.json (its permissions.allow...)."""
    cfg = cfg or Path.home() / ".claude.json"
    key = Path(folder).resolve().as_posix()
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        project = data.setdefault("projects", {}).setdefault(key, {})
        if project.get("hasTrustDialogAccepted"):
            return   # already trusted: no write, so Claude Code's own writes are rarely raced
        project["hasTrustDialogAccepted"] = True
        tmp = cfg.with_name(cfg.name + ".atlas-tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(cfg)   # atomic: a half-written ~/.claude.json would lose the user's Claude Code setup
    except Exception as e:
        print(f"[LLM] could not mark {key} as trusted in Claude Code: {e}")


def subscription_options(system: str, model: str, persist: bool = False, **kw):
    """Claude Agent SDK options that bill the Claude Code login (Pro/Max), never an API key.
    No user settings/plugins/hooks/skills and no built-in tools unless the caller passes them
    (setting_sources=SKILL_SOURCES plus "Skill" in tools: the master ~/.claude/skills and the
    project's .claude/skills, shared with the user's other Claude Code profiles; their hooks stay
    off, and so do their plugins except those named in plugins=(...), e.g. ("superpowers",)).
    Never the user's MCP servers or claude.ai connectors: a helper that saw them took them for its tools.
    persist=False: the CLI writes no session transcript to ~/.claude/projects (every helper
    call used to leave one, so they piled up as chats). Self-improvement keeps its own."""
    import os
    from claude_agent_sdk import ClaudeAgentOptions
    # The SDK's `claude -v` probe gives up after 2 s (the CLI takes ~4 s to start here) and then
    # waits on the killed probe with no limit: inside ATLAS that wait never ended, so no brain.
    # The CLI ships with the SDK, so the version it checks always matches.
    os.environ["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] = "1"
    env = {k: "" for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") if os.environ.get(k)}
    # Optional long-lived token from `claude setup-token`; otherwise the `claude auth login` session.
    token = (get_config().get("claude_oauth_token") or "").strip()
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    extra = {"strict-mcp-config": None} if persist else {"strict-mcp-config": None, "no-session-persistence": None}
    keep = kw.pop("plugins", ())
    if kw.get("setting_sources"):
        extra["settings"] = _skills_only(keep)
    if kw.get("cwd"):   # the folder the user asked ATLAS to work in
        trust_folder(kw["cwd"])
    return ClaudeAgentOptions(model=model, system_prompt=system or None, env=env, extra_args=extra,
                              **{"tools": [], "setting_sources": [], **kw})


def claude_cli() -> str:
    """Claude Code CLI bundled with claude-agent-sdk, so no separate Claude Code install is needed."""
    import shutil
    import claude_agent_sdk
    exe = Path(claude_agent_sdk.__file__).parent / "_bundled" / ("claude.exe" if is_windows() else "claude")
    return str(exe) if exe.exists() else (shutil.which("claude") or "claude")


def is_auth_error(text) -> bool:
    return bool(re.search(r"authenticat|not logged in|/login", str(text), re.I))


_login_lock, _login_at = threading.Lock(), 0.0


def subscription_login(timeout: int = 600) -> bool:
    """Browser sign-in to the Claude subscription (`claude auth login`), in its own console
    window so any prompt stays visible. One at a time; callers right after a login reuse it."""
    global _login_at
    with _login_lock:
        if time.time() - _login_at < 120:
            return True
        # ponytail: no console on mac/linux; the browser still opens, a paste-code fallback would need a terminal.
        flags = subprocess.CREATE_NEW_CONSOLE if is_windows() else 0
        p = subprocess.Popen([claude_cli(), "auth", "login", "--claudeai"], creationflags=flags)
        try:
            ok = p.wait(timeout) == 0
        except subprocess.TimeoutExpired:
            p.kill()
            ok = False
        if ok:
            _login_at = time.time()
        return ok


def _subscription_complete(prompt: str, system: str, model: str, effort: str | None = None) -> str:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from claude_agent_sdk import ResultMessage, query

    async def run():
        async for m in query(prompt=prompt, options=subscription_options(system, model, max_turns=1, effort=effort)):
            if isinstance(m, ResultMessage):
                if m.is_error:
                    raise LLMError(f"Claude subscription: {m.result or m.errors}")
                return m.result

    for retry in (False, True):
        try:
            # Own thread so callers already inside an event loop still work.
            with ThreadPoolExecutor(1) as ex:
                return ex.submit(asyncio.run, asyncio.wait_for(run(), TIMEOUT * 2)).result()
        except LLMError as e:
            if retry or not is_auth_error(e) or not subscription_login():
                raise


def strip_fences(text: str) -> str:
    return re.sub(r"```(?:json|python)?", "", text).strip().rstrip("`").strip()


def complete_json(prompt: str, system: str = "", fast: bool = False, max_tokens: int = 2048, role: str | None = None):
    """complete() parsed as JSON; raises ValueError (json.JSONDecodeError) on bad output."""
    text = strip_fences(complete(prompt, system, fast, max_tokens, role=role))
    m = re.search(r"[\[{].*[\]}]", text, re.S)   # models sometimes wrap JSON in a sentence
    return json.loads(m.group(0) if m else text)
