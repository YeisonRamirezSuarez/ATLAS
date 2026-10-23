"""Non-Gemini engines: mic -> STT -> any chat LLM with tools -> edge-tts.

Reuses JarvisLive's mic, speaker and tool runner; only the brain changes.
"""
import asyncio
import io
import json
import re
import threading
import time
import traceback
import types as _t
import wave
from collections import deque

import edge_tts
import miniaudio

from config import PROVIDERS, get_config, get_effort, get_engine
from main import JarvisLive, TOOL_DECLARATIONS, VOICE_NAME, VoiceGate, _update_memory_async

VOICE_RULE = ("\nYour replies are spoken aloud by a TTS voice: plain text only, no markdown, "
              "no lists, no emojis. One or two short sentences.")
MAX_HISTORY = 40      # messages; then the whole history resets (keeps the cached prefix valid)
MAX_RESULT  = 2000    # chars of a tool result sent back to the model
GEMINI_TTS  = "gemini-3.8-flash-lite-tts"   # streams 24 kHz PCM in the Gemini Live voice
READ_ALOUD  = ("You are a text-to-speech engine. Read the user's message aloud exactly as written, word for word, "
               "in its own language. Never answer it, add, skip, translate or comment.")
SAME_LANGUAGE = ("Rewrite the Text in the language of the user's words (a locale code like es-ES names it). "
                 "If it is already in that language, return it unchanged. Keep meaning, names and numbers. "
                 "Output only the text.")


def _lower_types(o):
    """Gemini schemas use "STRING"/"OBJECT"; JSON Schema wants lowercase."""
    if isinstance(o, dict):
        return {k: v.lower() if k == "type" and isinstance(v, str) else _lower_types(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_lower_types(v) for v in o]
    return o


class _Claude:
    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self, key, model, sys_fn, _url):
        import anthropic
        self.err, self.c, self.model, self.sys_fn = anthropic.BadRequestError, anthropic.Anthropic(api_key=key), model, sys_fn
        # Server-side model fallback is opt-in ("allow_model_fallback": true in api_keys.json);
        # when it answers with another model, the log says so.
        self.system, self.msgs = sys_fn(), []
        self.fallback = bool(get_config().get("allow_model_fallback", False))
        self.effort = get_effort()
        self.tools = [{"name": d["name"], "description": d["description"],
                       "input_schema": _lower_types(d.get("parameters") or {"type": "object", "properties": {}})}
                      for d in TOOL_DECLARATIONS]

    def ask(self, text, run_tool, say):
        if len(self.msgs) > MAX_HISTORY:
            self.msgs, self.system = [], self.sys_fn()
        start = len(self.msgs)
        self.msgs.append({"role": "user", "content": text})
        tin = tout = 0
        for _ in range(8):
            kw = dict(model=self.model, max_tokens=1024, system=self.system, tools=self.tools,
                      messages=self.msgs, cache_control={"type": "ephemeral"})
            if self.effort:
                kw["output_config"] = {"effort": self.effort}
            if self.fallback:
                kw.update(betas=[self.FALLBACK_BETA], fallbacks="default")
            try:
                r = self.c.beta.messages.create(**kw)
            except self.err as e:
                if self.fallback and "fallback" in str(e).lower():  # e.g. Haiku: no fallback support
                    self.fallback = False
                    continue
                if self.effort and "effort" in str(e).lower():      # model without effort levels
                    self.effort = None
                    continue
                raise
            if getattr(r, "model", self.model) != self.model:
                print(f"[Claude] FALLBACK: answered by {r.model} instead of {self.model}")
            u = r.usage
            tin += u.input_tokens + (u.cache_read_input_tokens or 0) + (u.cache_creation_input_tokens or 0)
            tout += u.output_tokens
            if r.stop_reason == "refusal":
                del self.msgs[start:]
                return "I can't help with that one, sir.", tin, tout
            self.msgs.append({"role": "assistant", "content": r.content})
            said = "".join(b.text for b in r.content if b.type == "text").strip()
            calls = [b for b in r.content if b.type == "tool_use"]
            if not calls:
                return said, tin, tout
            if said:
                say(said)
            self.msgs.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.id, "content": run_tool(b.name, b.input)} for b in calls]})
        return "I stopped after 8 tool rounds without finishing, sir.", tin, tout


class _ClaudeSub:
    """Claude via the Pro/Max subscription (Claude Agent SDK on the Claude Code login).
    One live SDK session on its own event loop keeps the conversation; Jarvis tools are
    exposed as an in-process MCP server."""

    def __init__(self, _key, model, sys_fn, _url):
        from claude_agent_sdk import ClaudeSDKClient, create_sdk_mcp_server, tool
        from agent.llm import subscription_options
        self.run_tool = None

        def wrap(d):
            async def h(args):
                out = await asyncio.to_thread(self.run_tool, d["name"], args)
                return {"content": [{"type": "text", "text": out}]}
            return tool(d["name"], d["description"],
                        _lower_types(d.get("parameters") or {"type": "object", "properties": {}}))(h)

        server = create_sdk_mcp_server("jarvis", tools=[wrap(d) for d in TOOL_DECLARATIONS])
        self.client = ClaudeSDKClient(subscription_options(
            sys_fn(), model, mcp_servers={"jarvis": server}, max_turns=8, effort=get_effort(),
            allowed_tools=[f"mcp__jarvis__{d['name']}" for d in TOOL_DECLARATIONS]))
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self._call(self.client.connect(), 60)

    def _call(self, coro, timeout=None):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def ask(self, text, run_tool, say):
        from agent.llm import subscription_login
        self.run_tool = run_tool
        r = self._call(self._ask(text, say))
        if r is not None:
            return r
        # Login missing or expired: browser sign-in, then a fresh session (the old one kept the dead token).
        say("I opened the browser so you can sign in to Claude, sir.")
        if not subscription_login():
            return "The Claude sign-in did not finish, sir.", 0, 0
        self._call(self.client.disconnect(), 30)
        self._call(self.client.connect(), 60)
        return self._call(self._ask(text, say)) or ("Claude still rejects the sign-in, sir.", 0, 0)

    async def _ask(self, text, say):
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
        from agent.llm import is_auth_error
        await self.client.query(text)
        async for m in self.client.receive_response():
            if isinstance(m, AssistantMessage) and any(isinstance(b, ToolUseBlock) for b in m.content):
                said = "".join(b.text for b in m.content if isinstance(b, TextBlock)).strip()
                if said:
                    await asyncio.to_thread(say, said)
            elif isinstance(m, ResultMessage):
                if m.is_error and is_auth_error(f"{m.result} {m.errors}"):
                    return None
                u = m.usage or {}
                tin = sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
                return (m.result or "").strip(), tin, u.get("output_tokens") or 0
        return "", 0, 0


class _OpenAICompat:
    """OpenAI, OpenRouter, Groq, DeepSeek, Ollama: same chat-completions API."""

    def __init__(self, key, model, sys_fn, url):
        import openai
        self.c, self.model, self.sys_fn = openai.OpenAI(api_key=key or "local", base_url=url or None), model, sys_fn
        self.system, self.msgs = sys_fn(), []
        # Providers default to huge output caps (OpenRouter reserves credit for them).
        # OpenAI proper (no base url) renamed the field; its reasoning tokens count too.
        self.limit = {"max_completion_tokens": 2048} if url is None else {"max_tokens": 1024}
        if get_effort():   # only OpenAI proper lists efforts
            self.limit["reasoning_effort"] = get_effort()
        self.tools = [{"type": "function", "function": {
            "name": d["name"], "description": d["description"],
            "parameters": _lower_types(d.get("parameters") or {"type": "object", "properties": {}})}}
            for d in TOOL_DECLARATIONS]

    def ask(self, text, run_tool, say):
        if len(self.msgs) > MAX_HISTORY:
            self.msgs, self.system = [], self.sys_fn()
        self.msgs.append({"role": "user", "content": text})
        tin = tout = 0
        for _ in range(8):
            r = self.c.chat.completions.create(model=self.model, tools=self.tools, **self.limit,
                                               messages=[{"role": "system", "content": self.system}, *self.msgs])
            if r.usage:
                tin += r.usage.prompt_tokens or 0
                tout += r.usage.completion_tokens or 0
            m = r.choices[0].message
            said = (m.content or "").strip()
            if not m.tool_calls:
                self.msgs.append({"role": "assistant", "content": said})
                return said, tin, tout
            self.msgs.append({"role": "assistant", "content": said or None,
                              "tool_calls": [tc.model_dump(exclude_none=True) for tc in m.tool_calls]})
            if said:
                say(said)
            for tc in m.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except ValueError:
                    args = {}
                self.msgs.append({"role": "tool", "tool_call_id": tc.id, "content": run_tool(tc.function.name, args)})
        return "I stopped after 8 tool rounds without finishing, sir.", tin, tout


class CascadeJarvis(JarvisLive):

    def __init__(self, ui):
        super().__init__(ui)
        self.cfg   = get_config()
        self._busy = False
        self._turn = threading.Lock()   # one exchange at a time; a message typed mid-reply waits its turn

    # ---- brain -------------------------------------------------------------
    def _run_tool(self, name, args) -> str:
        fc = _t.SimpleNamespace(id=None, name=name, args=args)
        fr = asyncio.run_coroutine_threadsafe(self._execute_tool(fc), self._loop).result()
        return str((fr.response or {}).get("result", ""))[:MAX_RESULT] or "OK"

    def _handle(self, pcm=None, text=None):
        with self._turn:
            self._busy = True
            try:
                self.ui.set_state("THINKING")
                if pcm is not None:
                    text = self._stt(pcm).strip()
                    if not text:
                        return
                    self.ui.write_log(f"You: {text}")
                self._last_heard = text   # language background speech is put into
                reply, tin, tout = self.llm.ask(text, self._run_tool, self._say)
                self.ui.add_usage(tin, tout)
                if reply:
                    self.ui.write_log(f"Jarvis: {reply}")
                    self._say(reply)
                threading.Thread(target=_update_memory_async, args=(text, reply), daemon=True).start()
            except Exception as e:
                traceback.print_exc()
                self.ui.write_log(f"ERR: {str(e)[:160]}")
            finally:
                self._busy = False
                if self._wake:
                    self._wake.keep_awake()
                if not self._is_speaking and not self.ui.muted:
                    self.ui.set_state("LISTENING")

    def _on_text_command(self, text: str):  # UI already calls this in a thread
        if self._loop:
            self._handle(text=text)   # mid-reply: waits for it instead of being dropped

    # ---- ears --------------------------------------------------------------
    def _stt_engine(self) -> str:
        """'openai' or 'google'. "stt_engine" in api_keys.json; auto = OpenAI when its key exists."""
        e = self.cfg.get("stt_engine", "auto")
        return e if e in ("openai", "google") else ("openai" if self.cfg.get("openai_api_key") else "google")

    def _stt(self, pcm: bytes) -> str:
        if self._stt_engine() == "openai":
            import openai
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm)
            return openai.OpenAI(api_key=self.cfg["openai_api_key"]).audio.transcriptions.create(
                model="gpt-4o-mini-transcribe", file=("speech.wav", buf.getvalue())).text
        import speech_recognition as sr
        try:
            return sr.Recognizer().recognize_google(sr.AudioData(pcm, 16000, 2),
                                                    language=self.cfg.get("stt_language", "es-ES"))
        except sr.UnknownValueError:
            return ""

    async def _vad_loop(self):
        pre, loud, buf, voiced, silent = deque(maxlen=8), VoiceGate(), [], 0, 0
        while True:
            d = (await self.out_queue.get())["data"]
            if self._busy:
                buf = []
                continue
            hot = loud(d)
            if not buf:
                if hot:
                    buf, voiced, silent = [*pre, d], 1, 0
                    pre.clear()
                else:
                    pre.append(d)
                continue
            buf.append(d)
            voiced += hot
            silent = 0 if hot else silent + 1
            if silent >= 13 or len(buf) > 470:   # ~0.8 s pause, or 30 s cap
                pcm, buf = b"".join(buf), []
                if voiced >= 4:                   # ignore clicks and coughs
                    self._busy = True
                    threading.Thread(target=self._handle, args=(pcm,), daemon=True).start()

    # ---- mouth -------------------------------------------------------------
    async def _tts(self, text: str):
        """Same voice as Gemini Live (Charon) when a Gemini key exists; edge-tts only when Gemini can't speak.
        Gemini TTS free tier allows 10 requests a day; past that, a Live session reads the text instead."""
        if self.cfg.get("gemini_api_key"):
            self._tts_sent = False
            readers = [self._live_tts]
            if time.time() >= getattr(self, "_tts_quota_until", 0):
                readers.insert(0, self._gemini_tts)
            for read in readers:
                try:
                    return await read(text)
                except Exception as e:
                    if self._tts_sent:   # half spoken: repeating it all in another voice is worse
                        return print(f"[JARVIS] ⚠️ {read.__name__} cut off: {str(e)[:200]}")
                    if read == self._gemini_tts and "429" in str(e):
                        m = re.search(r"retryDelay\W+(\d+)s", str(e))
                        self._tts_quota_until = time.time() + (int(m.group(1)) if m else 3600)
                        print(f"[JARVIS] Gemini TTS quota used up; Gemini Live reads for the next "
                              f"{(self._tts_quota_until - time.time()) / 3600:.1f} h")
                    else:
                        print(f"[JARVIS] ⚠️ {read.__name__} failed: {str(e)[:200]}")
            print("[JARVIS] ⚠️ Gemini voice unavailable, using edge-tts")
        mp3 = b"".join([c["data"] async for c in edge_tts.Communicate(
            text, self.cfg.get("tts_voice", "es-CO-GonzaloNeural")).stream() if c["type"] == "audio"])
        if not mp3:
            return
        pcm = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16,
                               nchannels=1, sample_rate=24000).samples.tobytes()
        self._turn_done = False
        for i in range(0, len(pcm), 4096):
            self.audio_in_queue.put_nowait(pcm[i:i + 4096])
        self._turn_done = True

    def _gemini(self):
        from google import genai
        if not getattr(self, "_tts_client", None):
            self._tts_client = genai.Client(api_key=self.cfg["gemini_api_key"], http_options={"api_version": "v1beta"})
        return self._tts_client

    @staticmethod
    def _voice():
        from google.genai import types
        return types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE_NAME)))

    def _play(self, parts):
        for p in parts or []:
            if p.inline_data:   # audio/l16; rate=24000; channels=1, playable as-is
                self.audio_in_queue.put_nowait(p.inline_data.data)
                self._tts_sent = True

    async def _gemini_tts(self, text: str):
        from google.genai import types
        cfg = types.GenerateContentConfig(response_modalities=["AUDIO"], speech_config=self._voice())
        self._turn_done = False
        try:
            async for ch in await self._gemini().aio.models.generate_content_stream(
                    model=GEMINI_TTS, contents=text, config=cfg):
                if ch.candidates:
                    self._play(getattr(ch.candidates[0].content, "parts", None))
        finally:
            self._turn_done = True

    async def _live_tts(self, text: str):
        # ponytail: one Live session per sentence (~1 s to connect); keep one open if that lag annoys.
        from google.genai import types
        cfg = types.LiveConnectConfig(response_modalities=["AUDIO"], system_instruction=READ_ALOUD,
                                      speech_config=self._voice())
        model = "models/" + PROVIDERS["gemini"]["model"].removeprefix("models/")
        self._turn_done = False
        try:
            async with self._gemini().aio.live.connect(model=model, config=cfg) as session:
                await session.send_client_content(turns={"role": "user", "parts": [{"text": text}]},
                                                  turn_complete=True)
                async for r in session.receive():   # ends at turn_complete
                    if r.server_content and r.server_content.model_turn:
                        self._play(r.server_content.model_turn.parts)
        finally:
            self._turn_done = True

    def speak(self, text: str):
        """Tools and background tasks talk through here, often in English: say it in the user's language.
        The brain's own replies already are, so they go straight to _say."""
        if not text:
            return
        try:
            on_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            on_loop = False
        if on_loop:   # translating blocks: never on the event loop
            return threading.Thread(target=self.speak, args=(text,), daemon=True).start()
        self._say(self._user_language(text))

    def _user_language(self, text: str) -> str:
        hint = getattr(self, "_last_heard", "") or self.cfg.get("stt_language", "es-ES")
        try:
            from agent.llm import complete
            return complete(f"User's words: {hint[:200]}\n\nText:\n{text}", system=SAME_LANGUAGE,
                            fast=True, max_tokens=1024).strip() or text
        except Exception as e:
            print(f"[JARVIS] ⚠️ could not translate speech: {e}")
            return text

    def _say(self, text: str):
        if self._loop and text:
            fut = asyncio.run_coroutine_threadsafe(self._tts(text), self._loop)
            try:
                on_loop = asyncio.get_running_loop() is self._loop
            except RuntimeError:
                on_loop = False
            if not on_loop:  # waiting on the loop's own thread would deadlock
                try:
                    fut.result(timeout=30)
                except Exception as e:
                    print(f"[JARVIS] ⚠️ TTS: {e}")

    # ---- boot --------------------------------------------------------------
    async def run(self):
        p, model = get_engine()
        info = PROVIDERS[p]
        self.ui.set_model(info["label"], model)
        brain = {"claude": _Claude, "claude_sub": _ClaudeSub}.get(p, _OpenAICompat)
        self.llm = brain(self.cfg.get(info["key"]) if info["key"] else None, model,
                         lambda: self._system_text() + VOICE_RULE,
                         (p == "ollama" and self.cfg.get("base_url")) or info.get("url"))
        self._loop          = asyncio.get_running_loop()
        self.audio_in_queue = asyncio.Queue()
        self.out_queue      = asyncio.Queue()
        self.ui.set_state("LISTENING")
        self.ui.write_log(f"SYS: JARVIS online. Engine={info['label']} ({model}). STT={self._stt_engine()}.")
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._listen_audio())
            tg.create_task(self._play_audio())
            tg.create_task(self._vad_loop())
