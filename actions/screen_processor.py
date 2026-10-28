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
IMG_MAX_W = 640
IMG_MAX_H = 360
JPEG_Q    = 55

START_TIMEOUT  = 20   # seconds to wait for the Live session to connect
ANSWER_TIMEOUT = 30   # seconds to wait for one vision answer

# DirectShow exists only on Windows; elsewhere let OpenCV pick the backend.
_CAP_API = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY

SYSTEM_PROMPT = (
    "You are ATLAS, a sharp personal AI assistant. "
    "Analyze images with technical precision and intelligence. "
    "Help the user in a way they can understand — don't be overly complex. "
    "Be concise, smart, and helpful. "
    "Respond in maximum 2 short sentences. Speed is priority. "
    "Your answer is read by ATLAS, who tells the user in his own voice."
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


def _monitor_labels(monitors: list[dict]) -> list[str]:
    """Names each monitor by where it sits, left to right ("left", "center", "right").
    The primary one (at 0,0) is marked so the user can tell which is which."""
    order = sorted(range(len(monitors)), key=lambda i: (monitors[i]["left"], monitors[i]["top"]))
    n = len(monitors)
    if n == 1:
        names = ["main screen"]
    elif n == 2:
        names = ["left screen", "right screen"]
    elif n == 3:
        names = ["left screen", "center screen", "right screen"]
    else:
        names = [f"screen {k + 1} from the left" for k in range(n)]
    labels = [""] * n
    for pos, i in enumerate(order):
        m = monitors[i]
        label = f"{names[pos]} ({m['width']}x{m['height']}"
        if n > 1 and m["left"] == 0 and m["top"] == 0:
            label += ", primary"
        labels[i] = label + ")"
    return [labels[i] for i in order]


def _capture_screenshot() -> list[tuple[str, bytes]]:
    """Captures every monitor separately: [(label, image bytes), ...] ordered left to right."""
    with mss.mss() as sct:
        monitors = sorted(sct.monitors[1:] or sct.monitors[:1],
                          key=lambda m: (m["left"], m["top"]))
        labels   = _monitor_labels(monitors)
        shots    = []
        for label, mon in zip(labels, monitors):
            shot = sct.grab(mon)
            shots.append((label, _to_jpeg(mss.tools.to_png(shot.rgb, shot.size))))
    return shots


def _screens_question(labels: list[str], user_text: str) -> str:
    if len(labels) <= 1:
        return user_text
    return (
        f"The user has {len(labels)} monitors; you got one image per monitor, each preceded "
        f"by its label: {', '.join(labels)}. Look at ALL of them before answering, say which "
        "screen each thing you mention is on, and only say something is not visible if it "
        f"is on none of them. Question: {user_text}"
    )


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
        self._ready:     threading.Event                  = threading.Event()
        # One Future per sent request, answered in order by _recv_loop.
        self._waiting: collections.deque                  = collections.deque()

    def start(self):
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
                images = image_bytes if isinstance(image_bytes, list) else [("", image_bytes)]
                parts  = []
                for label, data in images:
                    if label:
                        parts.append({"text": f"Image of the {label}:"})
                    b64 = base64.b64encode(data).decode("utf-8")
                    parts.append({"inline_data": {"mime_type": mime_type, "data": b64}})
                parts.append({"text": user_text})
                await self._session.send_client_content(
                    turns={"parts": parts},
                    turn_complete=True
                )
                print(f"[ScreenProcess] ✅ {len(images)} image(s) sent")
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
                got_any = True   # its audio is never played: ATLAS says the answer in his one voice
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
            if self._waiting:
                fut = self._waiting.popleft()
                if not fut.done():
                    fut.set_result(full)

    def analyze(self, image_bytes: bytes | list[tuple[str, bytes]], mime_type: str, user_text: str,
                timeout: float = ANSWER_TIMEOUT) -> str:
        """Sends the image, or labeled images [(label, bytes), ...], and returns the answer
        text ("" if only audio came back).
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


def _ensure_started():
    with _start_lock:
        _live.start()


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
            screens     = _capture_screenshot()
            if not screens:
                raise RuntimeError("no monitor found")
            image_bytes = screens if len(screens) > 1 else screens[0][1]
            user_text   = _screens_question([label for label, _ in screens], user_text)
            mime_type   = "image/jpeg" if _PIL_OK else "image/png"
            print(f"[ScreenProcess] 🖥️ {len(screens)} screen(s) captured")
    except Exception as e:
        print(f"[ScreenProcess] ❌ Capture error: {e}")
        return f"Could not capture the {angle}: {e}"

    size = sum(len(b) for _, b in image_bytes) if isinstance(image_bytes, list) else len(image_bytes)
    print(f"[ScreenProcess] 📦 {size} bytes → sending")
    try:
        _ensure_started()
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
        _ensure_started()
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
