"""Phone bridge: an iPhone Shortcut (Siri) sends ATLAS a request over the LAN and gets the reply back.

POST /ask with the request as the body (plain text, or JSON {"text": ...}) and the header
"Authorization: Bearer <token>". The reply is JSON {"text": ...}, or with ?format=wav the reply
in ATLAS's own voice as a WAV file. The token lives in config/phone_token.txt (created on first run);
"phone_port" in api_keys.json changes the port (default 8765), "phone_port": 0 turns the bridge off.

Push: everything ATLAS says also goes to the iPhone as an ntfy notification (text + a WAV of the
speech the PC played: the same voice), even when the phone did not ask, once the PC has said it.
The topic is in config/ntfy_topic.txt (random, created on first run; subscribe to it in the ntfy
app). An empty file turns push off.

Settings › iPhone turns each channel off without a restart: "phone_push" (ntfy), "phone_mail" (the
push_email copy) and "phone_siri" (this server answers 503) in api_keys.json.
"""
import hmac
import io
import json
import secrets
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

TOKEN_PATH = Path(__file__).resolve().parent / "config" / "phone_token.txt"
TOPIC_PATH = Path(__file__).resolve().parent / "config" / "ntfy_topic.txt"
NTFY = "https://ntfy.sh"   # public server: the random topic name is the only thing keeping it private
MAX_BODY = 10_000   # bytes: a dictated request, never a file
RATE = 24000        # ATLAS speech: 16-bit mono 24 kHz, the same PCM the PC speaker plays

_atlas = None       # the engine running now; replaced each time the settings are saved
_server = None


def token() -> str:
    if not TOKEN_PATH.exists():
        TOKEN_PATH.write_text(secrets.token_urlsafe(24), encoding="utf-8")
    return TOKEN_PATH.read_text(encoding="utf-8").strip()


def topic() -> str:
    if not TOPIC_PATH.exists():
        TOPIC_PATH.write_text("atlas-" + secrets.token_hex(10), encoding="utf-8")
    return TOPIC_PATH.read_text(encoding="utf-8").strip()


def push_on() -> bool:
    try:
        return bool(topic())
    except OSError:
        return False


def _on(key: str) -> bool:
    """A Settings › iPhone switch (phone_push, phone_mail, phone_siri): read on each message, so
    flipping it needs no restart. On by default, and while api_keys.json can't be read."""
    try:
        from config import get_config
        return bool(get_config().get(key, True))
    except Exception:
        return True


def push(text: str, pcm: bytes = b""):
    """Sends what ATLAS said to the iPhone in the background, with pcm: the speech the PC played
    (16-bit mono 24 kHz), so the iPhone hears the same voice. Never blocks or breaks the reply."""
    if not (text or "").strip():
        return
    t = topic() if push_on() else ""
    if t:
        threading.Thread(target=_push, args=(t, text.strip(), pcm), daemon=True).start()
    threading.Thread(target=_mail, args=(text.strip(), pcm), daemon=True).start()


def _mail(text: str, pcm: bytes = b""):
    """Also emails it when "push_email" is set in api_keys.json:
    {"to": "<address on the iPhone>", "from": "<sender>", "password": "...", "smtp": "<host>", "smtp_port": 587}
    An iPhone Shortcuts automation ("when an email with subject ATLAS arrives") reads it aloud by
    itself, which a notification can't do. SMTP only: ATLAS gets no access to that mailbox."""
    if not _on("phone_mail"):
        return
    from config import get_config
    cfg = get_config().get("push_email") or {}
    if not cfg.get("to"):
        return
    try:
        import smtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = cfg["from"], cfg["to"], "ATLAS"
        msg.set_content(text)
        if pcm:   # ATLAS's voice as the PC played it: the automation plays it with "Play Sound"
            msg.add_attachment(to_wav(pcm), maintype="audio", subtype="wav", filename="atlas.wav")
        port = int(cfg.get("smtp_port", 587))
        with (smtplib.SMTP_SSL(cfg["smtp"], port, timeout=30) if port == 465
              else smtplib.SMTP(cfg["smtp"], port, timeout=30)) as s:
            if port != 465:
                s.starttls()
            s.login(cfg["from"], cfg["password"])
            s.send_message(msg)
    except Exception as e:
        print(f"[ATLAS] ! push email failed: {str(e)[:160]}")


_http = None   # one session kept open: the first push no longer pays for new connections


def _session():
    global _http
    if _http is None:
        import requests
        _http = requests.Session()
    return _http


def _push(t: str, text: str, pcm: bytes = b""):
    if not _on("phone_push"):
        return
    params = {"title": "ATLAS", "message": text[:1500], "priority": "high"}
    if pcm:
        try:
            _session().put(f"{NTFY}/{t}", params={**params, "filename": "atlas.wav"}, data=to_wav(pcm),
                           timeout=60).raise_for_status()
            return
        except Exception as e:   # no voice: the text still goes
            print(f"[ATLAS] ! push voice failed: {str(e)[:160]}")
    try:
        _session().post(f"{NTFY}/{t}", params=params, timeout=20).raise_for_status()
    except Exception as e:
        print(f"[ATLAS] ! push to iPhone failed: {str(e)[:160]}")


def warm():
    """Opens the ntfy connection ahead of the first push."""
    if push_on():
        threading.Thread(target=lambda: _session().head(NTFY, timeout=10), daemon=True).start()


def to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(pcm)
    return buf.getvalue()


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, kind: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/ask":
            return self._json(404, {"error": "not found"})
        given = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(given.encode(), self.server.token.encode()):
            return self._json(401, {"error": "bad token"})
        if not _on("phone_siri"):   # the port stays open, ATLAS just says no
            return self._json(503, {"error": "Siri is off in ATLAS settings"})
        size = int(self.headers.get("Content-Length") or 0)
        if not 0 < size <= MAX_BODY:
            return self._json(413, {"error": "empty or too long"})
        raw = self.rfile.read(size).decode("utf-8", "replace").strip()
        try:
            text = str(json.loads(raw).get("text", "")).strip() if raw.startswith("{") else raw
        except (ValueError, AttributeError):
            text = raw
        if not text:
            return self._json(400, {"error": "no text"})
        atlas = _atlas
        if atlas is None:
            return self._json(503, {"error": "ATLAS is starting"})
        reply, pcm = atlas.phone_ask(text)
        if parse_qs(url.query).get("format") == ["wav"]:
            return self._send(200, to_wav(pcm), "audio/wav")
        self._json(200, {"text": reply})

    def log_message(self, *args):   # the request text is private: keep it out of the console
        pass


def serve(port: int, tok: str) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    srv.token = tok
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def attach(atlas, port: int = 8765):
    """Called by each engine on start: the server starts once and always talks to the newest engine."""
    global _atlas, _server
    _atlas = atlas
    warm()
    if _server is None and port:
        _server = serve(port, token())
        atlas.ui.write_log(f"SIS: Puente iPhone activo en el puerto {port}.")
