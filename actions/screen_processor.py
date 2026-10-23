import asyncio
import base64
import io
import json
import re
import os
import sys
import time
import threading
import collections
import concurrent.futures
import cv2
import mss
import mss.tools
import sounddevice as sd
import numpy as np
from pathlib import Path

try:
    import PIL.Image
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

from google import genai
from google.genai import types

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

IMG_MAX_W = 640
IMG_MAX_H = 360
JPEG_Q    = 55

START_TIMEOUT  = 20   # seconds to wait for the Live session to connect
ANSWER_TIMEOUT = 30   # seconds to wait for one vision answer

# DirectShow exists only on Windows; elsewhere let OpenCV pick the backend.
_CAP_API = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY

SYSTEM_PROMPT = (
    "You are JARVIS from Iron Man movies. "
    "Analyze images with technical precision and intelligence. "
    "Help the user in a way they can understand — don't be overly complex. "
    "Be concise, smart, and helpful like Tony Stark's AI assistant. "
    "Respond in maximum 2 short sentences. Speed is priority. "
    "Address the user as 'sir' for a tone of respect. "
    "Ask if the user needs any further help with their problem."
)


def _get_api_key() -> str:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            keys = json.load(f)
        key = keys.get("gemini_api_key", "")
        if not key:
            raise ValueError("gemini_api_key is empty in api_keys.json")
        return key
    except Exception as e:
        raise RuntimeError(f"Could not use vision: {e}")


def _get_camera_index() -> int:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if "camera_index" in cfg:
            return int(cfg["camera_index"])
    except Exception:
        pass

    print("[Camera] 🔍 No camera index in config. Auto-detecting...")
    best_index = 0

    for idx in range(6):
        cap = cv2.VideoCapture(idx, _CAP_API)
        if not cap.isOpened():
            cap.release()
            continue
        for _ in range(5):
            cap.read()
        ret, frame = cap.read()
        cap.release()
        if ret and frame is not None and frame.mean() > 5:
            best_index = idx
            print(f"[Camera] ✅ Camera found at index {idx} — saving to config.")
            break
        else:
            print(f"[Camera] ⚠️  Index {idx}: no valid frame.")

    try:
        cfg = {}
        if API_CONFIG_PATH.exists():
            with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        cfg["camera_index"] = best_index
        with open(API_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
        print(f"[Camera] 💾 Camera index {best_index} saved to config.")
    except Exception as e:
        print(f"[Camera] ⚠️  Could not save camera index: {e}")

    return best_index


def _to_jpeg(img_bytes: bytes) -> bytes:
    if not _PIL_OK:
        return img_bytes
    img = PIL.Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img.thumbnail([IMG_MAX_W, IMG_MAX_H], PIL.Image.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_Q, optimize=False)
    return buf.getvalue()


def _capture_screenshot() -> bytes:
    with mss.mss() as sct:
        shot      = sct.grab(sct.monitors[1])
        png_bytes = mss.tools.to_png(shot.rgb, shot.size)
    return _to_jpeg(png_bytes)


def _capture_camera() -> bytes:
    camera_index = _get_camera_index()
    cap = cv2.VideoCapture(camera_index, _CAP_API)
    try:
        if not cap.isOpened():
            raise RuntimeError(f"Camera could not be opened: index {camera_index}")
        for _ in range(10):
            cap.read()
        ret, frame = cap.read()
    finally:
        cap.release()
    if not ret or frame is None:
        raise RuntimeError("Could not capture camera frame.")
    if _PIL_OK:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = PIL.Image.fromarray(rgb)
        img.thumbnail([IMG_MAX_W, IMG_MAX_H], PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_Q, optimize=False)
        return buf.getvalue()
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
    return buf.tobytes()


class _LiveSession:

    def __init__(self):
        self._loop:      asyncio.AbstractEventLoop | None = None
        self._thread:    threading.Thread | None          = None
        self._session                                     = None
        self._out_queue: asyncio.Queue | None             = None
        self._audio_in:  asyncio.Queue | None             = None
        self._ready:     threading.Event                  = threading.Event()
        self._player                                      = None
        # One Future per sent request, answered in order by _recv_loop.
        self._waiting: collections.deque                  = collections.deque()

    def start(self, player=None):
        if player is not None:
            self._player = player
        if not (self._thread and self._thread.is_alive()):
            self._thread = threading.Thread(
                target=self._run_loop, daemon=True, name="VisionSessionThread"
            )
            self._thread.start()
        if not self._ready.wait(timeout=START_TIMEOUT):
            raise RuntimeError(f"vision session did not connect within {START_TIMEOUT}s")

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self):
        self._out_queue = asyncio.Queue(maxsize=30)
        self._audio_in  = asyncio.Queue()

        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"}
        )

        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            system_instruction=SYSTEM_PROMPT,
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )

        while True:
            try:
                print("[ScreenProcess] 🔌 Vision session connecting...")
                async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
                    self._session = session
                    self._ready.set()
                    print("[ScreenProcess] ✅ Vision session connected")
                    async with asyncio.TaskGroup() as tg:
                        tg.create_task(self._send_loop())
                        tg.create_task(self._recv_loop())
                        tg.create_task(self._play_loop())
            except Exception as e:
                print(f"[ScreenProcess] ⚠️ Disconnected: {e} — reconnecting...")
                self._session = None
                self._ready.clear()
                # Requests sent on the dead session will never be answered.
                while self._waiting:
                    fut = self._waiting.popleft()
                    if not fut.done():
                        fut.set_exception(ConnectionError(f"vision session dropped: {e}"))
                await asyncio.sleep(2)

    async def _send_loop(self):
        while True:
            image_bytes, mime_type, user_text, fut = await self._out_queue.get()
            # Queue before sending so the answer can never arrive first.
            self._waiting.append(fut)
            try:
                b64 = base64.b64encode(image_bytes).decode("utf-8")
                await self._session.send_client_content(
                    turns={
                        "parts": [
                            {"inline_data": {"mime_type": mime_type, "data": b64}},
                            {"text": user_text}
                        ]
                    },
                    turn_complete=True
                )
                print("[ScreenProcess] ✅ Image sent")
            except Exception as e:
                print(f"[ScreenProcess] ⚠️ Send error: {e}")
                self._waiting.remove(fut)
                fut.set_exception(e)

    async def _recv_loop(self):
        # receive() stops at each turn_complete, so call it once per turn.
        # ponytail: answers are matched to requests FIFO; overlapping requests that the
        # model interrupts could shift them, serialize requests if that shows up.
        while True:
            chunks: list[str] = []
            got_any = False
            async for response in self._session.receive():
                got_any = True
                if response.data:
                    await self._audio_in.put(response.data)
                sc = response.server_content
                if not sc:
                    continue
                if sc.output_transcription and sc.output_transcription.text:
                    chunks.append(sc.output_transcription.text.strip())
                for part in (sc.model_turn.parts if sc.model_turn else None) or []:
                    if part.text and not part.thought:
                        chunks.append(part.text.strip())
            if not got_any:
                # An empty receive() means the server closed the session: reconnect.
                raise ConnectionError("vision session closed by the server")
            full = re.sub(r'\s+', ' ', " ".join(chunks)).strip()
            if full:
                print(f"[ScreenProcess] 💬 {full}")
                if self._player:
                    self._player.write_log(f"Jarvis: {full}")
            if self._waiting:
                fut = self._waiting.popleft()
                if not fut.done():
                    fut.set_result(full)

    async def _play_loop(self):
        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()
        try:
            while True:
                chunk = await self._audio_in.get()
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[ScreenProcess] ❌ Play error: {e}")
            raise
        finally:
            stream.stop()
            stream.close()

    def analyze(self, image_bytes: bytes, mime_type: str, user_text: str,
                timeout: float = ANSWER_TIMEOUT) -> str:
        """Sends the image and returns the answer text ("" if only audio came back).
        Raises TimeoutError when no answer arrives in time."""
        if not self._loop or not self.is_ready():
            raise ConnectionError("vision session is not connected")
        fut = concurrent.futures.Future()
        asyncio.run_coroutine_threadsafe(
            self._out_queue.put((image_bytes, mime_type, user_text, fut)),
            self._loop
        )
        return fut.result(timeout=timeout)

    def is_ready(self) -> bool:
        return self._ready.is_set()


_live       = _LiveSession()
_start_lock = threading.Lock()


def _ensure_started(player=None):
    with _start_lock:
        _live.start(player=player)


def screen_process(
    parameters:     dict,
    response:       str | None = None,
    player=None,
    session_memory=None,
) -> str:
    user_text = (parameters or {}).get("text") or (parameters or {}).get("user_text", "")
    user_text = (user_text or "").strip()
    if not user_text:
        return "Could not analyze the image: no 'text' question was given."

    angle = ((parameters or {}).get("angle") or "screen").lower().strip()
    print(f"[ScreenProcess] angle={angle!r}  text={user_text!r}")

    try:
        _get_api_key()
    except RuntimeError as e:
        return str(e)

    try:
        if angle == "camera":
            image_bytes = _capture_camera()
            mime_type   = "image/jpeg"
            print("[ScreenProcess] 📷 Camera captured")
        else:
            image_bytes = _capture_screenshot()
            mime_type   = "image/jpeg" if _PIL_OK else "image/png"
            print("[ScreenProcess] 🖥️ Screen captured")
    except Exception as e:
        print(f"[ScreenProcess] ❌ Capture error: {e}")
        return f"Could not capture the {angle}: {e}"

    print(f"[ScreenProcess] 📦 {len(image_bytes)} bytes → sending")
    try:
        _ensure_started(player=player)
        text = _live.analyze(image_bytes, mime_type, user_text)
    except TimeoutError:
        return f"Failed: the vision model did not answer within {ANSWER_TIMEOUT}s."
    except Exception as e:
        print(f"[ScreenProcess] ❌ Vision error: {e}")
        return f"Error in the vision session: {e}"

    if text:
        return f"Vision answer: {text}"
    return "The vision model answered aloud; its text is unavailable."


def warmup_session(player=None):
    try:
        _ensure_started(player=player)
    except Exception as e:
        print(f"[ScreenProcess] ⚠️ Warmup error: {e}")


if __name__ == "__main__":
    print("[TEST] screen_processor.py v8 — image-only session")
    print("=" * 50)
    mode    = input("screen / camera (default: screen): ").strip().lower() or "screen"
    request = input("Question (Enter for default): ").strip() or "What do you see? Be brief."

    t0 = time.perf_counter()
    warmup_session()
    print(f"Session ready — {time.perf_counter()-t0:.2f}s\n")

    t1     = time.perf_counter()
    result = screen_process({"angle": mode, "text": request}, player=None)
    print(f"Done — {time.perf_counter()-t1:.3f}s | {result}")
    time.sleep(3)  # let the last audio chunks play
