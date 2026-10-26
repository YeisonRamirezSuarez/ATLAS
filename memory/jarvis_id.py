"""
JARVIS ID: optional Google sign-in through Supabase Auth, so several devices share one memory.
JARVIS works the same without it and it never changes the selected AI engine.

JARVIS ships with its own project's public URL and publishable key (row level security on the
server guards the data), so a user only signs in; supabase_url / supabase_anon_key in
api_keys.json point to another project. The session tokens are kept encrypted for this Windows
user (DPAPI) and never logged. Setup: docs/jarvis-id-setup.md.
"""
import base64
import hashlib
import html
import json
import platform
import secrets
import time
import uuid
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from config import VERSION, get_config, is_windows, update_config

SESSION_FILE = Path(__file__).resolve().parent / "jarvis_id.session"
PORT     = 53682   # Google sends the browser back here; Supabase must allow http://127.0.0.1:53682/callback
REDIRECT = f"http://127.0.0.1:{PORT}/callback"
TIMEOUT  = 15
# JARVIS's Supabase project. Public by design: never put the secret / service_role key here.
PROJECT_URL = "https://bevrstsvfikouhacidic.supabase.co"
PUBLIC_KEY  = "sb_publishable_yhc161xS2QOWqAwYMaRKsA_iQwS2K1C"


class IDError(RuntimeError):
    """JARVIS ID not configured, not signed in, offline or refused by the server (status: its HTTP code)."""
    status = 0


def _project() -> tuple[str, str]:
    try:
        cfg = get_config()
    except Exception:
        cfg = {}
    url = (cfg.get("supabase_url") or PROJECT_URL).strip().rstrip("/")
    key = (cfg.get("supabase_anon_key") or PUBLIC_KEY).strip()
    if not url or not key:
        raise IDError("JARVIS ID is not set up: supabase_url / supabase_anon_key are empty (docs/jarvis-id-setup.md)")
    return url, key


def configured() -> bool:
    try:
        _project()
        return True
    except IDError:
        return False


def device_id() -> str:
    """Random id made once per install (never the hostname), kept in api_keys.json."""
    cfg = get_config()
    if not cfg.get("device_id"):
        update_config(device_id=str(uuid.uuid4()))
    return get_config()["device_id"]


def device_name() -> str:
    try:
        return (get_config().get("device_name") or "").strip() or platform.node() or "JARVIS"
    except Exception:
        return platform.node() or "JARVIS"


# ── Session storage ───────────────────────────────────────────────────────────

def _dpapi(data: bytes, decrypt: bool = False) -> bytes:
    """Windows DPAPI: only this Windows user on this PC can decrypt."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src, out = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), Blob()
    crypt = ctypes.windll.crypt32
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out)):   # 0x1: never show UI
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


_session: dict | None = None


def _save_session(s: dict | None) -> None:
    global _session
    _session = s
    if s is None:
        SESSION_FILE.unlink(missing_ok=True)
    elif is_windows():
        SESSION_FILE.write_bytes(_dpapi(json.dumps(s).encode()))
    # ponytail: no OS keychain on mac/linux yet, so the session stays in RAM and a restart asks to sign in again.


def _load_session() -> dict | None:
    global _session
    if _session is None and is_windows() and SESSION_FILE.exists():
        try:
            _session = json.loads(_dpapi(SESSION_FILE.read_bytes(), decrypt=True))
        except Exception:   # another Windows user or PC: sign in again
            _session = None
    return _session


# ── Auth ──────────────────────────────────────────────────────────────────────

def _reason(r: requests.Response) -> str:
    try:
        d = r.json()
        return str(d.get("error_description") or d.get("msg") or d.get("message") or d.get("error") or "")[:200]
    except Exception:
        return r.text[:200]


def _token(grant: str, body: dict) -> dict:
    url, key = _project()
    r = requests.post(f"{url}/auth/v1/token", params={"grant_type": grant}, json=body,
                      headers={"apikey": key}, timeout=TIMEOUT)
    if r.status_code != 200:
        raise IDError(f"JARVIS ID sign-in refused ({grant}): HTTP {r.status_code} {_reason(r)}")
    d = r.json()
    s = {"access_token": d["access_token"], "refresh_token": d["refresh_token"],
         "expires_at": time.time() + int(d.get("expires_in") or 3600),
         "user": {"id": d["user"]["id"], "email": d["user"].get("email") or ""}}
    _save_session(s)
    return s


def _page(ok: bool, detail: str) -> bytes:
    """The tab the browser lands on after Google, in the JARVIS HUD colours. detail: email or error."""
    color, title = ("#6ff3ff", "SIGNED IN") if ok else ("#ff2a3d", "SIGN-IN FAILED")
    detail = html.escape(detail or "")
    msg = f"{detail}<br>You can close this tab and go back to JARVIS." if ok else detail
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>JARVIS ID</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(circle at 50% 40%,#0d1a20,#05070b 70%);
color:#ffe2a6;font-family:Bahnschrift,"Segoe UI",sans-serif;text-align:center}}
.ring{{width:120px;height:120px;margin:0 auto 28px;border-radius:50%;border:3px solid {color};
box-shadow:0 0 30px {color}88,inset 0 0 24px {color}55;display:grid;place-items:center;font-size:52px;color:{color}}}
.brand{{color:#ffb627;letter-spacing:.5em;font-size:13px}}h1{{margin:10px 0;letter-spacing:.2em;font-weight:600;color:{color}}}
p{{color:#c19a5b;max-width:420px;margin:0 auto;padding:0 16px;line-height:1.5}}
</style></head><body><main><div class="ring">{"&#10003;" if ok else "!"}</div>
<div class="brand">JARVIS ID</div><h1>{title}</h1><p>{msg}</p></main></body></html>""".encode()


def login(timeout: int = 300, open_browser=webbrowser.open) -> dict:
    """Google sign-in in the browser (OAuth PKCE with a loopback redirect). Blocks until done."""
    _project()
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    got = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            if u.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            q = parse_qs(u.query)
            code, error = q.get("code", [""])[0], (q.get("error_description") or q.get("error") or [""])[0]
            if code:   # swap the code here, so the page tells the real outcome
                try:
                    got["session"] = _token("pkce", {"auth_code": code, "code_verifier": verifier})
                except (IDError, requests.RequestException) as e:
                    error = str(e) if isinstance(e, IDError) else f"JARVIS ID offline ({type(e).__name__})"
            got["error"] = "" if "session" in got else error or "no code in the reply"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            email = got.get("session", {}).get("user", {}).get("email", "")
            self.wfile.write(_page("session" in got, email or got["error"]))

        def log_message(self, *a):   # the URL carries the one-time code: keep it out of the logs
            pass

    try:
        srv = HTTPServer(("127.0.0.1", PORT), Callback)
    except OSError as e:
        raise IDError(f"Port {PORT} is busy, close what uses it and retry ({e})")
    srv.timeout = 1
    url, _ = _project()
    open_browser(f"{url}/auth/v1/authorize?" + urlencode({
        "provider": "google", "redirect_to": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "s256"}))
    end = time.time() + timeout
    try:
        while not got and time.time() < end:
            srv.handle_request()
    finally:
        srv.server_close()
    if "session" not in got:
        raise IDError(f"Google sign-in did not finish: {got.get('error') or 'timed out'}")
    register_device()
    return got["session"]


def access_token() -> str:
    """Valid access token, refreshed when about to expire. A refused refresh signs out;
    a network failure keeps the session for the next try."""
    s = _load_session()
    if not s:
        raise IDError("Not signed in to JARVIS ID")
    if s["expires_at"] - time.time() > 60:
        return s["access_token"]
    try:
        return _token("refresh_token", {"refresh_token": s["refresh_token"]})["access_token"]
    except requests.RequestException as e:
        raise IDError(f"JARVIS ID offline ({type(e).__name__})")
    except IDError:
        _save_session(None)
        raise IDError("JARVIS ID session expired: sign in again")


def logout() -> None:
    s = _load_session()
    if s and configured():
        url, key = _project()
        try:   # revoke on the server; the local session goes either way
            requests.post(f"{url}/auth/v1/logout", headers={"apikey": key, "Authorization": f"Bearer {s['access_token']}"},
                          timeout=TIMEOUT)
        except requests.RequestException:
            pass
    _save_session(None)


def status() -> dict:
    s = _load_session()
    return {"configured": configured(), "signed_in": bool(s), "email": (s or {}).get("user", {}).get("email", ""),
            "user_id": (s or {}).get("user", {}).get("id", "")}


def rest(method: str, path: str, **kw):
    """PostgREST call as the signed-in user (RLS applies on the server)."""
    url, key = _project()
    headers = {"apikey": key, "Authorization": f"Bearer {access_token()}", **kw.pop("headers", {})}
    try:
        r = requests.request(method, f"{url}/rest/v1/{path}", headers=headers, timeout=TIMEOUT, **kw)
    except requests.RequestException as e:
        raise IDError(f"JARVIS ID offline ({type(e).__name__})")
    if r.status_code >= 400:
        err = IDError(f"JARVIS ID {method} {path.split('?')[0]}: HTTP {r.status_code} {_reason(r)}")
        err.status = r.status_code
        raise err
    return r.json() if r.content else None


def register_device() -> None:
    """This install in the user's device list (upsert by its random id)."""
    rest("POST", "devices?on_conflict=id", headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
         json={"id": device_id(), "name": device_name(), "platform": platform.platform(), "app_version": VERSION,
               "last_seen": datetime.now(timezone.utc).isoformat()})


def touch_device() -> bool:
    """Updates last_seen; False when this device was removed from the account (from another device)."""
    return bool(rest("PATCH", f"devices?id=eq.{device_id()}", headers={"Prefer": "return=representation"},
                     json={"last_seen": datetime.now(timezone.utc).isoformat(), "app_version": VERSION}))


def devices() -> list[dict]:
    return rest("GET", "devices?select=id,name,platform,app_version,last_seen&order=last_seen.desc") or []


def remove_device(dev_id: str) -> None:
    rest("DELETE", f"devices?id=eq.{uuid.UUID(dev_id)}")   # a real uuid, never a crafted filter
