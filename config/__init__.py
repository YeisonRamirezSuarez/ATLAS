import json, os, platform
from pathlib import Path

VERSION = "2.3.1"   # single source: UI, installer and Info.plist read it from here

_CONFIG_PATH = Path(__file__).parent / "api_keys.json"
APP_DIR = Path(__file__).resolve().parent.parent

def protected(path) -> str | None:
    """Refusal text when a chat tool would change JARVIS's own folder (code, keys, memory).
    JARVIS changes itself only through the installer/updater, which the user runs."""
    p = Path(path).expanduser().resolve()
    if p == APP_DIR or APP_DIR in p.parents:
        return f"Refused: {p} is inside JARVIS's own folder; my code and data can't be changed from chat."
    return None

def get_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def get_os() -> str:
    """Returns: 'windows' | 'mac' | 'linux', detected from the running system."""
    return {"Darwin": "mac", "Linux": "linux"}.get(platform.system(), "windows")

def is_windows() -> bool: return get_os() == "windows"
def is_mac()     -> bool: return get_os() == "mac"
def is_linux()   -> bool: return get_os() == "linux"

def is_wayland() -> bool:
    """Linux Wayland session (GNOME, KDE Plasma 6, Bazzite...): PyAutoGUI only reaches XWayland windows."""
    return is_linux() and (os.environ.get("XDG_SESSION_TYPE") == "wayland" or "WAYLAND_DISPLAY" in os.environ)

# AI engines. "key" is the api_keys.json field holding the engine's key; "url" is an
# OpenAI-compatible base URL (None = api.openai.com). Gemini runs the native Live voice
# session; the others run speech-to-text -> chat model with tools -> text-to-speech.
# "models" fills the side-panel picker (any other id can be typed); "efforts" are the
# reasoning levels the engine accepts, "effort" the default when none is saved.
_CLAUDE = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]
_CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
PROVIDERS = {
    "gemini":     {"label": "Gemini Live",    "model": "gemini-2.5-flash-native-audio-preview-12-2025", "key": "gemini_api_key"},
    "claude":     {"label": "Claude",         "model": "claude-opus-5-5",          "key": "anthropic_api_key",
                   "models": _CLAUDE, "efforts": _CLAUDE_EFFORTS, "effort": "low"},
    # Claude Pro/Max subscription: rides the Claude Code login (`claude auth login`), no key.
    "claude_sub": {"label": "Claude (subscription)", "model": "claude-opus-5-5",   "key": None,
                   "models": _CLAUDE, "efforts": _CLAUDE_EFFORTS, "effort": "low"},
    "openai":     {"label": "OpenAI",         "model": "gpt-5-mini",               "key": "openai_api_key",     "url": None,
                   "models": ["gpt-5", "gpt-5-mini", "gpt-5-nano"],
                   "efforts": ["minimal", "low", "medium", "high"], "effort": "medium"},
    "openrouter": {"label": "OpenRouter",     "model": "google/gemini-2.5-flash",  "key": "openrouter_api_key", "url": "https://openrouter.ai/api/v1",
                   "models": ["google/gemini-2.5-flash", "anthropic/claude-sonnet-5-5", "openai/gpt-5-mini"]},
    "groq":       {"label": "Groq",           "model": "llama-3.3-70b-versatile",  "key": "groq_api_key",       "url": "https://api.groq.com/openai/v1",
                   "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]},
    "deepseek":   {"label": "DeepSeek",       "model": "deepseek-chat",            "key": "deepseek_api_key",   "url": "https://api.deepseek.com",
                   "models": ["deepseek-chat", "deepseek-reasoner"]},
    "ollama":     {"label": "Ollama (local)", "model": "llama3.1",                 "key": None,                 "url": "http://localhost:11434/v1"},
}

def get_engine() -> tuple[str, str]:
    """(provider, model) from api_keys.json, Gemini Live when unset."""
    try:
        cfg = get_config()
    except Exception:
        cfg = {}
    p = cfg.get("provider") if cfg.get("provider") in PROVIDERS else "gemini"
    return p, cfg.get("model") or PROVIDERS[p]["model"]


def get_effort(default: bool = True) -> str | None:
    """Saved reasoning effort for the selected engine, else its default (when default=True) or None."""
    p = get_engine()[0]
    efforts = PROVIDERS[p].get("efforts") or []
    try:
        e = get_config().get("effort")
    except Exception:
        e = None
    return e if e in efforts else (PROVIDERS[p].get("effort") if default else None)
