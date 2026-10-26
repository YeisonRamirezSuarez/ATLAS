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


def complete(prompt: str, system: str = "", fast: bool = False, max_tokens: int = 2048) -> str:
    provider, model = get_engine()
    # FAST switch in the side panel: off = quick helper calls think as deep as the rest.
    fast = fast and get_config().get("fast", True)
    # Only an effort the user picked; quick helper calls keep the model's default depth.
    effort = None if fast else get_effort(default=False)
    spec = PROVIDERS[provider]
    key = (get_config().get(spec["key"]) or "").strip() if spec["key"] else ""
    if spec["key"] and not key:
        raise LLMError(f"{spec['label']} is the selected provider but '{spec['key']}' is empty in api_keys.json")

    if provider == "gemini":
        from google import genai
        from google.genai import types
        model = GEMINI_TEXT[fast]
        r = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=TIMEOUT * 1000)).models.generate_content(
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
    return text


def subscription_options(system: str, model: str, persist: bool = False, **kw):
    """Claude Agent SDK options that bill the Claude Code login (Pro/Max), never an API key.
    No user settings/plugins/hooks and no built-in tools unless the caller passes them.
    persist=False: the CLI writes no session transcript to ~/.claude/projects (every helper
    call used to leave one, so they piled up as chats). Self-improvement keeps its own."""
    import os
    from claude_agent_sdk import ClaudeAgentOptions
    env = {k: "" for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") if os.environ.get(k)}
    # Optional long-lived token from `claude setup-token`; otherwise the `claude auth login` session.
    token = (get_config().get("claude_oauth_token") or "").strip()
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    extra = {} if persist else {"no-session-persistence": None}
    return ClaudeAgentOptions(model=model, system_prompt=system or None, setting_sources=[],
                              env=env, extra_args=extra, **{"tools": [], **kw})


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


def complete_json(prompt: str, system: str = "", fast: bool = False, max_tokens: int = 2048):
    """complete() parsed as JSON; raises ValueError (json.JSONDecodeError) on bad output."""
    text = strip_fences(complete(prompt, system, fast, max_tokens))
    m = re.search(r"[\[{].*[\]}]", text, re.S)   # models sometimes wrap JSON in a sentence
    return json.loads(m.group(0) if m else text)
