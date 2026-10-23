# actions/send_message.py
# Universal messaging.
# Telegram: real API via Telethon (user's own account) — confirmed by message id.
# WhatsApp / Instagram / other apps: typed with pyautogui — delivery is NOT verified.

import re
import time
from pathlib import Path

import config

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.08
except Exception:  # no display (headless Linux) — GUI senders report it
    pyautogui = None

# Telethon appends ".session" itself.
_TG_SESSION = Path(config.__file__).parent / "telegram"
_TG_LOGIN_CMD = 'python -c "from actions.send_message import telegram_login; telegram_login()"'

_PLATFORMS = {
    "whatsapp": "whatsapp", "wa": "whatsapp", "wp": "whatsapp", "wapp": "whatsapp",
    "instagram": "instagram", "ig": "instagram", "insta": "instagram",
    "telegram": "telegram", "tg": "telegram",
}


def _open_app(app_name: str) -> bool:
    """Opens an app with open_app's per-OS launcher (Windows search, `open -a`, .desktop files)."""
    import platform
    from actions.open_app import _OS_LAUNCHERS
    try:
        return bool(_OS_LAUNCHERS.get(platform.system(), lambda name: False)(app_name))
    except Exception as e:
        print(f"[SendMessage] Could not open {app_name}: {e}")
        return False


def _mod() -> str:
    return "command" if config.is_mac() else "ctrl"


def _write(text: str, interval: float) -> None:
    """pyautogui.write drops á, ñ, ¿, emoji...: paste non-ASCII text through the clipboard."""
    if text.isascii():
        pyautogui.write(text, interval=interval)
        return
    import pyperclip   # in requirements.txt; ImportError is reported as a failure by the caller
    pyperclip.copy(text)
    time.sleep(0.1)
    pyautogui.hotkey(_mod(), "v")


def _send_gui(app: str, receiver: str, message: str) -> str:
    """
    Desktop app via GUI automation (WhatsApp, Messenger, Discord, Signal...).
    Steps: Open app → Ctrl+F contact → Enter → Type → Enter. Cannot confirm delivery.
    """
    if pyautogui is None:
        return f"Could not send via {app}: desktop automation (pyautogui) is unavailable."
    try:
        if not _open_app(app):
            return f"Could not open {app}."

        time.sleep(1.5)
        pyautogui.hotkey(_mod(), "f")
        time.sleep(0.4)
        pyautogui.hotkey(_mod(), "a")
        _write(receiver, interval=0.04)
        time.sleep(1.0)
        pyautogui.press("enter")
        time.sleep(0.8)
        _write(message, interval=0.03)
        time.sleep(0.2)
        pyautogui.press("enter")

        return f"Typed the message to {receiver} in {app} and pressed Enter; delivery NOT verified."

    except Exception as e:
        return f"Failed to send via {app}: {e}"


def _send_instagram(receiver: str, message: str) -> str:
    """
    Sends an Instagram DM via browser (instagram.com). Cannot confirm delivery.
    Steps: Open instagram.com/direct → Search contact → Send
    """
    if pyautogui is None:
        return "Could not send via Instagram: desktop automation (pyautogui) is unavailable."
    try:
        import webbrowser

        webbrowser.open("https://www.instagram.com/direct/new/")
        time.sleep(3.5)

        _write(receiver, interval=0.05)
        time.sleep(1.5)

        pyautogui.press("down")
        time.sleep(0.3)
        pyautogui.press("enter")
        time.sleep(0.5)

        for _ in range(3):
            pyautogui.press("tab")
            time.sleep(0.1)
        pyautogui.press("enter")
        time.sleep(1.5)

        _write(message, interval=0.04)
        time.sleep(0.2)
        pyautogui.press("enter")

        return f"Typed the message to {receiver} in Instagram and pressed Enter; delivery NOT verified."

    except Exception as e:
        return f"Failed to send via Instagram: {e}"


def _telegram_client():
    """TelegramClient for the user's account (api_keys.json), or a failure string."""
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        return "Could not send: Telegram needs the 'telethon' package (pip install telethon)."
    try:
        cfg = config.get_config()
    except Exception:
        cfg = {}
    api_id, api_hash = cfg.get("telegram_api_id"), cfg.get("telegram_api_hash")
    if not api_id or not api_hash:
        return "Could not send: set telegram_api_id and telegram_api_hash in api_keys.json (from my.telegram.org)."
    return TelegramClient(str(_TG_SESSION), int(api_id), str(api_hash), timeout=20)


def _telegram_entity(client, receiver: str):
    """(entity, display name) or (None, failure string). Never guesses between chats."""
    if receiver.startswith("@") or re.fullmatch(r"\+?[\d\s()-]{6,}", receiver):
        return client.get_entity(receiver), receiver

    # ponytail: loads every dialog; pass a limit if accounts get huge
    dialogs = client.get_dialogs()
    want = receiver.casefold()
    hits = ([d for d in dialogs if (d.name or "").casefold() == want]
            or [d for d in dialogs if want in (d.name or "").casefold()])
    if not hits:
        return None, f"Could not send: no Telegram chat named '{receiver}'."
    if len(hits) > 1:
        names = ", ".join(d.name for d in hits[:5])
        return None, f"Could not send: '{receiver}' matches several Telegram chats ({names}). Say the full name or @username."
    return hits[0].entity, hits[0].name


def _send_telegram(receiver: str, message: str) -> str:
    """Sends via the Telegram API; success only when Telegram returns a message id."""
    client = None
    try:
        made = _telegram_client()
        if isinstance(made, str):
            return made
        client = made
        client.connect()
        if not client.is_user_authorized():
            return f"Could not send: Telegram login must be done once interactively. Run: {_TG_LOGIN_CMD}"

        entity, name = _telegram_entity(client, receiver)
        if entity is None:
            return name

        msg = client.send_message(entity, message)
        if isinstance(getattr(msg, "id", None), int):
            return f"Telegram message sent to {name} (message id {msg.id})."
        return "Failed: Telegram did not confirm the message."

    except Exception as e:
        return f"Failed to send via Telegram: {e}"
    finally:
        if client is not None:
            client.disconnect()


def telegram_login():
    """One-time interactive Telegram login (asks phone and code in the console)."""
    client = _telegram_client()
    if isinstance(client, str):
        print(client)
        return
    client.start()
    try:
        print(f"Telegram login OK; session saved to {_TG_SESSION}.session")
    finally:
        client.disconnect()


def send_message(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None
) -> str:
    """
    Called from main.py.

    parameters:
        receiver     : Contact name, @username or phone number
        message_text : The message content
        platform     : whatsapp | instagram | telegram (aliases wa, ig, tg...) | <any app name>
                       Default: whatsapp
    """
    params       = parameters or {}
    receiver     = (params.get("receiver") or "").strip()
    message_text = (params.get("message_text") or "").strip()
    platform     = (params.get("platform") or "").strip() or "WhatsApp"

    if not receiver:
        return "Could not send: please specify who to send the message to, sir."
    if not message_text:
        return "Could not send: please specify what message to send, sir."

    print(f"[SendMessage] 📨 {platform} → {receiver}: {message_text[:40]}")
    if player:
        player.write_log(f"[msg] Sending to {receiver} via {platform}...")

    kind = _PLATFORMS.get(platform.lower().replace(" ", ""))
    if kind == "telegram":
        result = _send_telegram(receiver, message_text)
    elif kind == "instagram":
        result = _send_instagram(receiver, message_text)
    elif kind == "whatsapp":
        result = _send_gui("WhatsApp", receiver, message_text)
    else:
        result = _send_gui(platform, receiver, message_text)

    print(f"[SendMessage] {result}")
    if player:
        player.write_log(f"[msg] {result}")

    return result
