"""Non-Gemini engines: mic -> STT -> any chat LLM with tools -> edge-tts.

Reuses AtlasLive's mic, speaker and tool runner; only the brain changes.
"""
import asyncio
import concurrent.futures
import contextlib
import difflib
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
from main import (ACK, INSTANT_TOOLS, AtlasLive, TOOL_DECLARATIONS, RESUME_RULE, VoiceGate, clean_tail, live_model,
                  speech_config, voice_settings, _ack_lang, _names_itself, _update_memory_async)
from voice import reader_config

VOICE_RULE = ("\nYour replies are spoken aloud by a TTS voice: plain text only, no markdown, "
              "no lists, no emojis. One or two short sentences. Never run the same task twice at once.")
MAX_ROUNDS  = 25      # tool rounds per request before giving up
MAX_HISTORY = 40      # messages; then the whole history resets (keeps the cached prefix valid)
MAX_RESULT  = 2000    # chars of a tool result sent back to the model
HOLD        = 19200   # bytes (0.4 s) of speech held back until a read ends: clean_tail checks them
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


class _Stoppable:
    """The user interrupted: no new round after this one. A step already asked for still runs
    (CascadeAtlas._run_tool moves it to the background), so a task is never dropped half done."""
    stopped = False

    def stop(self):
        self.stopped = True


class _Claude(_Stoppable):
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

    def ask(self, text, run_tool, say, feed=None):
        if len(self.msgs) > MAX_HISTORY:
            self.msgs, self.system = [], self.sys_fn()
        start = len(self.msgs)
        self.msgs.append({"role": "user", "content": text})
        tin = tout = 0
        self.stopped = False
        for _ in range(MAX_ROUNDS):
            if self.stopped:
                return "", tin, tout
            kw = dict(model=self.model, max_tokens=1024, system=self.system, tools=self.tools,
                      messages=self.msgs, cache_control={"type": "ephemeral"})
            if self.effort:
                kw["output_config"] = {"effort": self.effort}
            if self.fallback:
                kw.update(betas=[self.FALLBACK_BETA], fallbacks="default")
            try:
                with self.c.beta.messages.stream(**kw) as s:
                    for ev in s:   # text reaches the voice while the rest is still being written
                        if feed and ev.type == "content_block_delta" and ev.delta.type == "text_delta":
                            feed(ev.delta.text)
                    r = s.get_final_message()
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
        return f"I stopped after {MAX_ROUNDS} tool rounds without finishing, sir.", tin, tout


class _ClaudeSub:
    """Claude via the Pro/Max subscription (Claude Agent SDK on the Claude Code login).
    One live SDK session on its own event loop keeps the conversation; Atlas tools are
    exposed as an in-process MCP server."""

    def __init__(self, _key, model, sys_fn, _url):
        from claude_agent_sdk import ClaudeSDKClient, create_sdk_mcp_server, tool
        from agent.llm import SKILL_SOURCES, subscription_options
        self.run_tool = None

        def wrap(d):
            async def h(args):
                out = await asyncio.to_thread(self.run_tool, d["name"], args)
                return {"content": [{"type": "text", "text": out}]}
            return tool(d["name"], d["description"],
                        _lower_types(d.get("parameters") or {"type": "object", "properties": {}}))(h)

        server = create_sdk_mcp_server("atlas", tools=[wrap(d) for d in TOOL_DECLARATIONS])
        opts = subscription_options(
            sys_fn(), model, mcp_servers={"atlas": server}, max_turns=MAX_ROUNDS, effort=get_effort(),
            include_partial_messages=True, tools=["Skill"], setting_sources=SKILL_SOURCES,
            # User settings may say bypassPermissions: the voice chat keeps the plain mode, so only
            # these tools run (Atlas tools keep their own confirmations).
            permission_mode="default",
            allowed_tools=["Skill", *(f"mcp__atlas__{d['name']}" for d in TOOL_DECLARATIONS)])
        self.client = ClaudeSDKClient(opts)
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self._call(self.client.connect(), 60)

    def _call(self, coro, timeout=None):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def close(self):
        """Settings saved: a new engine takes over, so this CLI session and its loop end."""
        try:
            self._call(self.client.disconnect(), 30)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)

    def stop(self):
        """The user interrupted: the CLI ends the current reply (a running tool still finishes)."""
        f = asyncio.run_coroutine_threadsafe(self.client.interrupt(), self.loop)
        f.add_done_callback(lambda f: f.exception())   # nothing running is fine

    def ask(self, text, run_tool, say, feed=None):
        from agent.llm import subscription_login
        self.run_tool, self.feed = run_tool, feed
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
        from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock, ToolUseBlock
        from agent.llm import is_auth_error
        await self.client.query(text)
        async for m in self.client.receive_response():
            if isinstance(m, StreamEvent) and self.feed:
                d, kind = m.event.get("delta") or {}, m.event.get("type")
                start = (m.event.get("content_block") or {}).get("type") if kind == "content_block_start" else None
                # The CLI may send text and tool calls as separate messages; a tool starting means the
                # announcement before it is complete, so it is spoken now, not after the tool.
                if kind == "message_start" or start == "tool_use":
                    self.feed(None)
                elif kind == "content_block_delta" and d.get("type") == "text_delta":
                    self.feed(d.get("text", ""))
            elif isinstance(m, AssistantMessage) and any(isinstance(b, ToolUseBlock) for b in m.content):
                said = "".join(b.text for b in m.content if isinstance(b, TextBlock)).strip()
                if said and not self.feed:   # streamed: feed(None) at the tool call already said it
                    await asyncio.to_thread(say, said)
            elif isinstance(m, ResultMessage):
                if m.is_error and is_auth_error(f"{m.result} {m.errors}"):
                    return None
                u = m.usage or {}
                tin = sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
                return (m.result or "").strip(), tin, u.get("output_tokens") or 0
        return "", 0, 0


class _OpenAICompat(_Stoppable):
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

    def ask(self, text, run_tool, say, feed=None):   # ponytail: no streaming here; Claude streams
        if len(self.msgs) > MAX_HISTORY:
            self.msgs, self.system = [], self.sys_fn()
        self.msgs.append({"role": "user", "content": text})
        tin = tout = 0
        self.stopped = False
        for _ in range(MAX_ROUNDS):
            if self.stopped:
                return "", tin, tout
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
        return f"I stopped after {MAX_ROUNDS} tool rounds without finishing, sir.", tin, tout


# Tool result for a step the user interrupted, and the hidden turn that finishes it later.
STILL_RUNNING = ("STILL RUNNING: the user interrupted you. This step keeps running in the background and its "
                 "result will be sent to you when it finishes. Do not call more tools now and say nothing.")
RESUME = ("[BACKGROUND RESULT] You were interrupted while doing the user's request \"{request}\". "
          "The step {name} has now finished with: {result}\n" + RESUME_RULE)


def _tool_text(fut) -> str:
    try:
        return str((fut.result().response or {}).get("result", ""))[:MAX_RESULT] or "OK"
    except Exception as e:
        return f"FAILED: {e}"


def _same_words(a: str, b: str) -> bool:
    if re.search(r"\d", a):   # "3:45" is spoken "tres cuarenta y cinco": words can't be compared
        return True
    words = lambda s: re.findall(r"\w+", s.lower())
    return difflib.SequenceMatcher(None, words(a), words(b)).ratio() >= 0.6


class _Voice:
    """Speaks a reply while the model is still writing it: the first sentence as soon as it is
    complete, the rest when the message ends. Two TTS calls per message, not one per sentence
    (Gemini TTS has a small daily quota and the Live fallback connects once per call)."""
    FIRST = re.compile(r"(.{12,}?[.!?…:])\s", re.S)

    def __init__(self, say, alive=lambda: True, log=lambda text: None):
        self.say, self.buf, self.spoken, self.last, self.said = say, "", "", None, False
        self.alive = alive   # False once the user interrupted this reply: nothing more is said
        self.log = log       # every message that is spoken, and only those, goes to the log

    def _out(self, text):
        if text.strip() and self.alive():
            self.said = True
            self.last = self.say(text.strip(), wait=False) or self.last

    def feed(self, delta: str | None):
        """Text as the model writes it; None = a new message starts, so finish the previous one."""
        if delta is None:
            return self.buf.strip() and self.end(self.buf)
        self.buf += delta
        if not self.spoken and (m := self.FIRST.match(self.buf)):
            self.spoken = m.group(1).strip()
            self._out(self.spoken)

    def end(self, text: str):
        """A message is complete: say what feed() has not said yet, then start over."""
        text = (text or "").strip()
        if text and self.alive():
            self.log(text)
        self._out(text.removeprefix(self.spoken) if self.spoken else text)
        self.buf, self.spoken = "", ""

    def wait(self):
        try:
            self.last and self.last.result(timeout=90)
        except Exception as e:
            print(f"[ATLAS] ⚠️ TTS: {e}")


class CascadeAtlas(AtlasLive):

    def __init__(self, ui):
        super().__init__(ui)
        self.cfg   = get_config()
        self._busy = False
        self._turn = threading.Lock()   # one exchange at a time; a message typed mid-reply waits its turn
        self._epoch = 0                 # +1 per interruption: replies from an older one stay silent
        self._tts_futs = set()          # speech queued or playing, cancelled on interruption

    # ---- brain -------------------------------------------------------------
    def _spoke(self, mark=False) -> bool:
        voice = getattr(self, "_voice", None)
        if voice and mark:
            voice.said = True
        return not voice or voice.said or not voice.alive()   # interrupted reply: no "on it" either

    def _acknowledge(self, name: str):
        """The "on it" line is read by the same Live session as the reply: the cached clip comes from
        another session, with its own pitch and pace, and sounded like a second speaker."""
        if name not in INSTANT_TOOLS and not self._spoke():
            self._voice.end(ACK[_ack_lang()])

    def _run_tool(self, name, args) -> str:
        epoch, request = self._epoch, getattr(self, "_turn_text", "")
        fc = _t.SimpleNamespace(id=None, name=name, args=args)
        fut = asyncio.run_coroutine_threadsafe(self._execute_tool(fc), self._loop)
        while not fut.done():
            if epoch != self._epoch:
                # Interrupted mid-step: the step keeps running and the brain is free for the new
                # request. Its result comes back as a hidden turn that finishes the task.
                fut.add_done_callback(lambda f: threading.Thread(
                    target=self._resume, args=(request, name, f), daemon=True).start())
                return STILL_RUNNING
            concurrent.futures.wait([fut], timeout=0.2)
        return _tool_text(fut)

    def _resume(self, request: str, name: str, fut):
        self._handle(text=RESUME.format(request=request[:300], name=name, result=_tool_text(fut)), remember=False)

    def _handle(self, pcm=None, text=None, remember=True):
        with self._turn:
            self._busy = True
            try:
                self._show_state()
                if pcm is not None:
                    text = self._stt(pcm).strip()
                    if not text:
                        return
                    self.ui.write_log(f"Tú: {text}")
                if remember:
                    self._last_heard = text   # language background speech is put into
                self._turn_text = text        # what an interrupted step resumes
                self._says_name = False
                epoch = self._epoch
                voice = self._voice = _Voice(self._say, lambda: epoch == self._epoch,
                                             lambda said: self.ui.write_log(f"Atlas: {said}"))
                reply, tin, tout = self.llm.ask(text, self._run_tool, voice.end, voice.feed)
                self.ui.add_usage(tin, tout)
                voice.end(reply)
                voice.wait()
                if remember:
                    threading.Thread(target=_update_memory_async, args=(text, reply), daemon=True).start()
            except Exception as e:
                traceback.print_exc()
                self.ui.write_log(f"ERROR: {str(e)[:160]}")
            finally:
                self._busy = False
                if self._wake:
                    self._wake.keep_awake()
                self._show_state()

    def _occupied(self) -> bool:
        return self._is_speaking or self._busy   # talking, or working on a request

    def interrupt(self):
        """Wake word while talking or working, or a typed message: silence now, end the brain's turn,
        take the new request. A step that is running keeps going and resumes the task when done."""
        self._epoch += 1
        for f in list(self._tts_futs):
            f.cancel()
        stop = getattr(getattr(self, "llm", None), "stop", None)
        if stop and self._turn.locked():
            stop()
        self._busy = False   # the VAD takes the utterance that interrupted
        super().interrupt()

    def _on_text_command(self, text: str):  # UI already calls this in a thread
        if self._loop:
            if self._turn.locked() or self._is_speaking:
                self.interrupt()      # a typed message cuts the current reply
            self._handle(text=text)   # waits for the interrupted one to wrap up

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
        async with self._tts_lock:   # one piece at a time, in the order _say queued them
            self._says_name = _names_itself(text)
            await self._tts_now(text)

    async def _tts_now(self, text: str):
        """One voice for everything: a Live session kept open reads each reply (same model and voice as
        the Gemini Live engine, no reconnect per sentence). edge-tts only when Gemini can't speak at all."""
        if self.cfg.get("gemini_api_key") and not getattr(self, "_gemini_denied", False):
            self._tts_sent = False
            for _ in range(2):   # a session the server closed while idle: reconnect once
                try:
                    return await self._live_tts(text)
                except Exception as e:
                    if self._tts_sent:   # half spoken: repeating it all in another voice is worse
                        return print(f"[ATLAS] ⚠️ Live voice cut off: {str(e)[:200]}")
                    if "403" in str(e) or "PERMISSION_DENIED" in str(e) or "free tier is not available" in str(e):
                        # the key's project can't use Gemini at all: no point trying it again every sentence
                        self._gemini_denied = True
                        print(f"[ATLAS] ⚠️ Gemini voice denied for this key ({str(e)[:120]}); edge-tts until restart")
                        break
                    print(f"[ATLAS] ⚠️ Live voice failed: {str(e)[:200]}")
            print("[ATLAS] ⚠️ Gemini voice unavailable, using edge-tts")
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

    def _play(self, pcm: bytes):
        if pcm:
            self.audio_in_queue.put_nowait(pcm)
            self._tts_sent = True

    async def _reader(self):
        """The open Live session that reads replies aloud. Each new session picks a slightly different
        pitch and pace, so it is kept as long as possible: reopened only after an error, when the voice
        settings change, and every 50 reads (its context grows with each one)."""
        voice, pace, _ = voice_settings()
        drain, self._drain = getattr(self, "_drain", None), None
        if drain:   # an interrupted read: its turn must end before the next one starts
            try:
                await asyncio.wait_for(drain, 3)
            except asyncio.CancelledError:
                await self._close_reader()
                raise
            except Exception:
                await self._close_reader()
        if getattr(self, "_read_stack", None) and (self._reads >= 50 or self._read_voice != (voice, pace)):
            await self._close_reader()
        if not getattr(self, "_read_stack", None):
            stack = contextlib.AsyncExitStack()
            self._read_session = await stack.enter_async_context(
                self._gemini().aio.live.connect(model=live_model(), config=reader_config(voice, pace)))
            self._read_stack, self._reads, self._read_voice = stack, 0, (voice, pace)
        return self._read_session

    async def _close_reader(self):
        stack, self._read_stack = getattr(self, "_read_stack", None), None
        if stack:
            try:
                await stack.aclose()
            except Exception:
                pass

    @staticmethod
    async def _skip_turn(session):
        async for _ in session.receive():   # ends at turn_complete
            pass

    async def _live_tts(self, text: str):
        session = await self._reader()
        self._reads += 1
        self._turn_done, tail, sent, heard = False, b"", False, ""
        try:
            for _ in range(2):   # now and then the server sends a turn with no audio (only its transcript): ask again
                await session.send_client_content(turns={"role": "user", "parts": [{"text": text}]}, turn_complete=True)
                sent = True
                async for r in session.receive():   # ends at turn_complete
                    if r.server_content and r.server_content.output_transcription:
                        heard += r.server_content.output_transcription.text or ""
                    if r.server_content and r.server_content.model_turn:
                        tail += b"".join(p.inline_data.data for p in r.server_content.model_turn.parts or [] if p.inline_data)
                        if len(tail) > HOLD:   # the end is held back to strip a noise burst there
                            self._play(tail[:-HOLD])
                            tail = tail[-HOLD:]
                self._play(clean_tail(tail))
                if tail:
                    break
                heard = ""
            else:
                raise ConnectionError("no audio twice")   # _tts_now retries in a new session
            if heard and not _same_words(text, heard):
                # The reader said something else (it answered or reworded the line): the log would no
                # longer match the voice. A fresh session drops whatever context led it there.
                self.ui.write_log(f"SIS: La voz dijo «{heard.strip()}» en lugar del texto; reinicio la voz.")
                await self._close_reader()
        except asyncio.CancelledError:
            if sent:   # interrupted: drop the rest of this turn in the background, keep the session
                self._drain = asyncio.ensure_future(self._skip_turn(session))
            else:
                await self._close_reader()
            raise
        except BaseException:
            await self._close_reader()   # a half-read turn would leak into the next read
            raise
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
            print(f"[ATLAS] ⚠️ could not translate speech: {e}")
            return text

    def _say(self, text: str, wait: bool = True):
        if not (self._loop and text):
            return None
        fut = asyncio.run_coroutine_threadsafe(self._tts(text), self._loop)
        self._tts_futs.add(fut)
        fut.add_done_callback(self._tts_futs.discard)
        try:
            on_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            on_loop = False
        if wait and not on_loop:  # waiting on the loop's own thread would deadlock
            try:
                fut.result(timeout=90)
            except Exception as e:
                print(f"[ATLAS] ⚠️ TTS: {e}")
        return fut

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
        self._tts_lock      = asyncio.Lock()
        self.audio_in_queue = asyncio.Queue()
        self.out_queue      = asyncio.Queue()
        self.ui.set_state("LISTENING")
        self.ui.write_log(f"SIS: ATLAS en línea. Motor={info['label']} ({model}). STT={self._stt_engine()}.")
        if boot := self._boot_prompt():
            threading.Thread(target=self._handle, kwargs={"text": boot, "remember": False}, daemon=True).start()
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._listen_audio())
            tg.create_task(self._play_audio())
            tg.create_task(self._vad_loop())
