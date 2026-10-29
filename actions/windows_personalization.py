"""Windows personalization (accent color, light/dark mode, wallpaper, transparency, accent on
Start/taskbar and title bars) through Windows' own supported mechanisms. The user's rule: never
write the registry directly (no winreg writes, reg.exe or Set-ItemProperty on these keys).

- Accent color and light/dark mode: a .theme file derived from the current theme (wallpaper,
  mode, sounds and cursors kept) applied silently by Windows' theme engine (IThemeManager2 in
  themeui.dll), in a child process so a COM fault can never take ATLAS down.
- Transparency and the two "show accent color" switches have no .theme key Windows 11 honors:
  they are flipped on Settings' own toggle switches by UI Automation, and only a Settings window
  this module opened is closed again.
- Wallpaper: SystemParametersInfo. Status and verification: WinRT UISettings (plus read-only
  registry reads for the values UISettings does not expose).
A change is reported as 'Verified: ...' only when the live read-back matches what was asked."""
import ctypes
import os
import platform
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

_OS = platform.system()
_NO_WINDOW = 0x08000000   # CREATE_NO_WINDOW: no console flashes on screen

# Windows' built-in accent swatches (Settings > Personalization > Colors).
ACCENT_SWATCHES = {
    "rosa": "E3008C", "rosado": "E3008C", "pink": "E3008C", "fucsia": "E3008C", "fuchsia": "E3008C",
    "magenta": "C239B3",
    "rojo": "E81123", "red": "E81123", "rojo oscuro": "C30052", "dark red": "C30052",
    "verde": "107C10", "green": "107C10", "verde claro": "00CC6A", "light green": "00CC6A",
    "verde oscuro": "10893E", "dark green": "10893E", "menta": "00B294", "mint": "00B294",
    "azul": "0078D4", "blue": "0078D4", "azul oscuro": "0063B1", "dark blue": "0063B1",
    "azul claro": "0099BC", "light blue": "0099BC", "celeste": "0099BC", "cian": "0099BC", "cyan": "0099BC",
    "turquesa": "038387", "turquoise": "038387", "teal": "038387",
    "morado": "881798", "purple": "881798", "purpura": "881798",
    "violeta": "8764B8", "violet": "8764B8", "lila": "8E8CD8", "lilac": "8E8CD8", "lavanda": "8E8CD8", "lavender": "8E8CD8",
    "naranja": "F7630C", "orange": "F7630C",
    "amarillo": "FFB900", "yellow": "FFB900", "dorado": "FFB900", "gold": "FFB900",
    "gris": "4A5459", "gray": "4A5459", "grey": "4A5459", "gris oscuro": "4C4A48", "dark gray": "4C4A48",
    "marron": "7E735F", "cafe": "7E735F", "brown": "7E735F",
}

THEMES_DIR   = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Themes"
ATLAS_THEME = THEMES_DIR / "Atlas.theme"

# IThemeManager2 (themeui.dll). Layout as published by SecureUxTheme's ThemeTool (themetool.h).
_CLSID_THEME_MANAGER2 = "9324DA94-50EC-4A14-A770-E90CA03E7C8F"
_IID_THEME_MANAGER2   = "C1E8C83E-845D-4D95-81DB-E283FDFFC000"
_VT_INIT, _VT_ADD_AND_SELECT_THEME = 3, 20
# THEME_APPLY_FLAGS: ignore background, cursors, desktop icons, sounds and screensaver; no hourglass.
# Only colors and light/dark mode come from the file, so nothing else can change by mistake.
_APPLY_COLORS_ONLY = 0x01 | 0x02 | 0x04 | 0x10 | 0x20 | 0x100
_THEMEPACK_SILENT  = 0x04   # no dialogs, no sound

# Settings > Personalization > Colors switches, by language-independent automation id.
_TOGGLES = {
    "transparency":            ("SystemSettings_Personalize_Color_EnableTransparency_ToggleSwitch", "transparency effects"),
    "accent_on_start_taskbar": ("SystemSettings_Personalize_Color_ColorPrevalence_ToggleSwitch", "accent color on Start and taskbar"),
    "accent_on_title_bars":    ("SystemSettings_Personalize_Color_ColorPrevalenceTitleBar_ToggleSwitch", "accent color on title bars and window borders"),
}
ACTIONS = ("accent_color", "theme_mode", "wallpaper", "transparency",
           "accent_on_start_taskbar", "accent_on_title_bars", "status")
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".jfif", ".webp"}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    return re.sub(r"\s+", " ", "".join(c for c in text if not unicodedata.combining(c))).strip()


def parse_color(text: str) -> str | None:
    """'rosa', 'Dark blue', '#e3008c' or 'E3008C' -> 'E3008C'; None when not understood."""
    t = _norm(text)
    m = re.fullmatch(r"#?([0-9a-f]{6})", t)
    if m:
        return m.group(1).upper()
    if t in ACCENT_SWATCHES:
        return ACCENT_SWATCHES[t]
    # A name inside a phrase ('ponlo de color rosa'): the longest one wins ('azul oscuro' over 'azul').
    hits = [n for n in ACCENT_SWATCHES if re.search(rf"\b{re.escape(n)}\b", t)]
    return ACCENT_SWATCHES[max(hits, key=len)] if hits else None


def parse_switch(value) -> bool | None:
    t = _norm(value)
    if t in ("on", "true", "1", "yes", "si", "enable", "enabled", "activar", "activado", "encender", "encendido", "prender"):
        return True
    if t in ("off", "false", "0", "no", "disable", "disabled", "desactivar", "desactivado", "apagar", "apagado", "quitar"):
        return False
    return None


def parse_mode(value, current_dark: bool) -> bool | None:
    """True for dark, False for light, None when not understood. 'toggle' flips the current one."""
    t = _norm(value)
    if t in ("dark", "oscuro", "modo oscuro", "negro", "night", "noche"):
        return True
    if t in ("light", "claro", "modo claro", "blanco", "day", "dia"):
        return False
    if t in ("toggle", "switch", "cambiar", "alternar", ""):
        return not current_dark
    return None


# ── .theme files (INI text) ────────────────────────────────────────────────────────────────────

def read_theme_text(path: Path) -> str:
    raw = Path(path).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("mbcs" if _OS == "Windows" else "latin-1", errors="replace")


def set_ini_value(text: str, section: str, key: str, value: str | None) -> str:
    """Sets key=value inside [section] (adding either if missing); value None removes the key."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip().lower() == f"[{section.lower()}]"), None)
    if start is None:
        return text if value is None else text.rstrip("\r\n") + f"\r\n\r\n[{section}]\r\n{key}={value}\r\n"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
    for i in range(start + 1, end):
        if lines[i].split("=", 1)[0].strip().lower() == key.lower() and "=" in lines[i]:
            if value is None:
                del lines[i]
            else:
                lines[i] = f"{key}={value}"
            return "\r\n".join(lines) + "\r\n"
    if value is not None:
        while end > start + 1 and not lines[end - 1].strip():   # before the blank line closing the section
            end -= 1
        lines.insert(end, f"{key}={value}")
    return "\r\n".join(lines) + "\r\n"


def get_ini_value(text: str, section: str, key: str) -> str | None:
    in_section = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            in_section = s.lower() == f"[{section.lower()}]"
        elif in_section and "=" in s and s.split("=", 1)[0].strip().lower() == key.lower():
            return s.split("=", 1)[1].strip()
    return None


def build_theme(base: str, color: str, system_dark: bool, apps_dark: bool, wallpaper: str = "") -> str:
    """The current theme with the given accent color and modes; everything else is kept as is."""
    t = set_ini_value(base, "Theme", "DisplayName", "ATLAS")
    t = set_ini_value(t, "Theme", "ThemeId", None)   # a new theme, not a copy claiming the original's id
    if wallpaper:
        t = set_ini_value(t, r"Control Panel\Desktop", "Wallpaper", wallpaper)
    t = set_ini_value(t, "VisualStyles", "ColorizationColor", f"0XC4{color.upper()}")
    t = set_ini_value(t, "VisualStyles", "AutoColorization", "0")
    t = set_ini_value(t, "VisualStyles", "SystemMode", "Dark" if system_dark else "Light")
    return set_ini_value(t, "VisualStyles", "AppMode", "Dark" if apps_dark else "Light")


# ── Live state (read-only) ─────────────────────────────────────────────────────────────────────

_UISETTINGS_PS = r"""
$null = [Windows.UI.ViewManagement.UISettings, Windows.UI.ViewManagement, ContentType = WindowsRuntime]
$want = '%s'
for ($i = 0; $i -lt 20; $i++) {
  $u = New-Object Windows.UI.ViewManagement.UISettings
  $a = $u.GetColorValue('Accent'); $b = $u.GetColorValue('Background')
  $s = '{0:X2}{1:X2}{2:X2} {3:X2}{4:X2}{5:X2} {6}' -f $a.R, $a.G, $a.B, $b.R, $b.G, $b.B, $u.AdvancedEffectsEnabled
  if (-not $want -or $s -like $want) { break }
  Start-Sleep -Milliseconds 200
}
$s
"""


def read_uisettings(expect: str = "") -> dict:
    """WinRT UISettings: accent color, app background (black = dark apps) and transparency.
    expect: a PowerShell -like pattern ('E3008C *'); waits up to ~4 s for the live value to match."""
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _UISETTINGS_PS % expect],
                       capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW)
    m = re.search(r"([0-9A-F]{6}) ([0-9A-F]{6}) (True|False)", r.stdout)
    if not m:
        raise RuntimeError(f"UISettings read failed: {(r.stderr or r.stdout).strip()[:300]}")
    return {"accent": m.group(1), "apps_dark": m.group(2) == "000000", "transparency": m.group(3) == "True"}


def _read_personalize(name: str):
    """Read-only lookup of a value UISettings does not expose (system mode, accent switches)."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _read_dwm(name: str):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM") as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def get_wallpaper() -> str:
    buf = ctypes.create_unicode_buffer(1024)
    ctypes.windll.user32.SystemParametersInfoW(0x0073, len(buf), buf, 0)   # SPI_GETDESKWALLPAPER
    return buf.value


_ENGLISH = ("pink", "red", "dark red", "green", "light green", "dark green", "mint", "blue", "dark blue",
            "light blue", "teal", "purple", "violet", "lilac", "magenta", "orange", "yellow", "gray", "dark gray", "brown")


def color_name(hex_color: str) -> str:
    """'E3008C' -> '#E3008C (pink)'; a custom color is just its hex."""
    name = next((n for n in _ENGLISH if ACCENT_SWATCHES[n] == hex_color.upper()), None)
    return f"#{hex_color.upper()}" + (f" ({name})" if name else "")


def _current_theme_file() -> Path | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes") as k:
            p = Path(os.path.expandvars(winreg.QueryValueEx(k, "CurrentTheme")[0]))
            if p.is_file():
                return p
    except OSError:
        pass
    for p in (THEMES_DIR / "Custom.theme", Path(os.environ.get("WINDIR", r"C:\Windows")) / "Resources" / "Themes" / "aero.theme"):
        if p.is_file():
            return p
    return None


# ── Settings windows (only one this module opened gets closed) ────────────────────────────────

def _settings_windows() -> set:
    """Top-level windows of the Settings app (SystemSettings.exe inside ApplicationFrameHost)."""
    import psutil
    import win32gui
    import win32process
    found = set()

    def top(hwnd, _):
        if win32gui.GetClassName(hwnd) != "ApplicationFrameWindow" or not win32gui.IsWindowVisible(hwnd):
            return True
        kids = []
        win32gui.EnumChildWindows(hwnd, lambda c, _: kids.append(c) or True, None)
        for c in kids:
            if win32gui.GetClassName(c) == "Windows.UI.Core.CoreWindow":
                try:
                    if psutil.Process(win32process.GetWindowThreadProcessId(c)[1]).name().lower() == "systemsettings.exe":
                        found.add(hwnd)
                except Exception:
                    pass
        return True
    win32gui.EnumWindows(top, None)
    return found


def _close_new_settings(before: set, focus=None) -> None:
    import win32gui
    try:
        opened = _settings_windows() - before
        for hwnd in opened:
            win32gui.PostMessage(hwnd, 0x0010, 0, 0)   # WM_CLOSE, only on the window that was not there before
        deadline = time.time() + 3   # gone before returning: a next call must not reuse a closing window
        while opened and time.time() < deadline:
            time.sleep(0.1)
            opened = {h for h in opened if win32gui.IsWindow(h) and win32gui.IsWindowVisible(h)}
    except Exception as e:
        print(f"[Personalize] could not check Settings windows: {e}")
    if focus:
        try:
            win32gui.SetForegroundWindow(focus)
        except Exception:
            pass


# ── Applying ───────────────────────────────────────────────────────────────────────────────────

def _apply_theme_com(path: str) -> None:
    """Runs in a child process: IThemeManager2::AddAndSelectTheme, silent, colors and mode only."""
    import uuid
    from ctypes import HRESULT, POINTER, byref, c_int, c_void_p, wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_ubyte * 16)]

    def guid(s):
        g = GUID()
        ctypes.memmove(g.data, uuid.UUID(s).bytes_le, 16)
        return g

    ole32 = ctypes.OleDLL("ole32")
    ole32.CoInitializeEx(None, 2)   # COINIT_APARTMENTTHREADED
    mgr = c_void_p()
    ole32.CoCreateInstance(byref(guid(_CLSID_THEME_MANAGER2)), None, 1,   # CLSCTX_INPROC_SERVER
                           byref(guid(_IID_THEME_MANAGER2)), byref(mgr))
    vtable = ctypes.cast(ctypes.cast(mgr, POINTER(c_void_p))[0], POINTER(c_void_p))
    ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_int)(vtable[_VT_INIT])(mgr, 0)
    ctypes.WINFUNCTYPE(HRESULT, c_void_p, wintypes.HWND, wintypes.LPCWSTR, c_int, c_int)(
        vtable[_VT_ADD_AND_SELECT_THEME])(mgr, None, path, _APPLY_COLORS_ONLY, _THEMEPACK_SILENT)
    ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vtable[2])(mgr)   # Release


def apply_theme(path: Path) -> None:
    before = _settings_windows()
    r = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "--apply-theme", str(path)],
                       capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW)
    _close_new_settings(before)
    if r.returncode != 0:
        raise RuntimeError(f"the Windows theme engine refused the theme: {(r.stderr or r.stdout).strip()[-300:]}")


def _write_derived_theme(color: str | None = None, dark: bool | None = None) -> tuple[str, bool, bool]:
    """Writes Atlas.theme from the current theme and live state. Returns (color, system_dark, apps_dark)."""
    live = read_uisettings()
    system_light = _read_personalize("SystemUsesLightTheme")
    system_dark = live["apps_dark"] if system_light is None else system_light == 0
    apps_dark = live["apps_dark"]
    if dark is not None:
        system_dark = apps_dark = dark
    color = color or live["accent"]
    src = _current_theme_file()
    base = read_theme_text(src) if src else "[Theme]\r\n\r\n[VisualStyles]\r\nPath=%SystemRoot%\\resources\\themes\\Aero\\Aero.msstyles\r\n"
    THEMES_DIR.mkdir(parents=True, exist_ok=True)
    ATLAS_THEME.write_text(build_theme(base, color, system_dark, apps_dark, get_wallpaper()), encoding="utf-16")
    return color, system_dark, apps_dark


def set_accent_color(value: str) -> str:
    color = parse_color(value)
    if not color:
        return (f"Could not understand the color '{value}'. Use a name (rosa, rojo, verde, azul, morado, "
                "naranja, amarillo, gris...) or #RRGGBB.")
    _write_derived_theme(color=color)
    apply_theme(ATLAS_THEME)
    try:
        live = read_uisettings(expect=f"{color} *")
    except Exception as e:
        return f"Could not verify the accent color after applying it: {e}"
    if live["accent"] != color:
        return f"Could not verify the accent color: asked for #{color}, Windows now reports #{live['accent']}."
    return f"Verified: Windows accent color is now {color_name(color)}."


def set_theme_mode(value: str) -> str:
    current = read_uisettings()
    dark = parse_mode(value, current["apps_dark"])
    if dark is None:
        return f"Could not understand the mode '{value}'. Use dark, light or toggle."
    _write_derived_theme(dark=dark)
    apply_theme(ATLAS_THEME)
    try:
        live = read_uisettings(expect=f"* {'000000' if dark else 'FFFFFF'} *")
    except Exception as e:
        return f"Could not verify the mode after applying it: {e}"
    system_ok = _read_personalize("SystemUsesLightTheme") in (None, 0 if dark else 1)
    if live["apps_dark"] != dark or not system_ok:
        return f"Could not verify {'dark' if dark else 'light'} mode: Windows still reports the previous mode."
    return f"Verified: Windows is now in {'dark' if dark else 'light'} mode (apps and system)."


def set_wallpaper(value: str) -> str:
    if not str(value or "").strip():
        return "Could not change the wallpaper: no image path was given."
    path = Path(os.path.expandvars(str(value).strip().strip('"'))).expanduser()
    if not path.is_file():
        return f"Could not change the wallpaper: image not found: {path}"
    if path.suffix.lower() not in _IMAGE_EXT:
        return f"Could not change the wallpaper: {path.suffix} is not an image format Windows uses."
    path = path.resolve()
    ok = ctypes.windll.user32.SystemParametersInfoW(0x0014, 0, str(path), 0x01 | 0x02)   # SPI_SETDESKWALLPAPER
    now = get_wallpaper()
    if not ok or os.path.normcase(now) != os.path.normcase(str(path)):
        return f"Could not verify the wallpaper: Windows reports '{now or 'none'}'."
    return f"Verified: wallpaper is now {path.name}."


def _toggle_state(action: str):
    if action == "transparency":
        return read_uisettings()["transparency"]
    name = "ColorPrevalence"
    value = _read_personalize(name) if action == "accent_on_start_taskbar" else _read_dwm(name)
    return None if value is None else bool(value)


def set_toggle(action: str, value) -> str:
    auto_id, label = _TOGGLES[action]
    want = parse_switch(value)
    if want is None:
        return f"Could not understand '{value}' for {label}. Use on or off."
    if _toggle_state(action) == want:
        return f"Verified: {label} was already {'on' if want else 'off'}."

    from pywinauto import Desktop
    import win32gui
    focus = win32gui.GetForegroundWindow()
    before = _settings_windows()
    os.startfile("ms-settings:colors")
    try:
        # Settings is single-instance: an already open window is reused (and left open afterwards).
        hwnd, deadline = next(iter(before), None), time.time() + 15
        while not hwnd and time.time() < deadline:
            time.sleep(0.1)
            hwnd = next(iter(_settings_windows()), None)
        if not hwnd:
            return "Could not open Windows Settings > Personalization > Colors."
        switch = Desktop(backend="uia").window(handle=hwnd).child_window(auto_id=auto_id)
        switch.wait("exists", timeout=10)
        if not switch.is_enabled():
            hint = " Windows only allows it when Windows mode is dark." if action == "accent_on_start_taskbar" else ""
            return f"Could not change {label}: Windows has that switch disabled.{hint}"
        if bool(switch.get_toggle_state()) != want:
            switch.toggle()
        for _ in range(20):
            if bool(switch.get_toggle_state()) == want and _toggle_state(action) == want:
                return f"Verified: {label} is now {'on' if want else 'off'}."
            time.sleep(0.2)
        return f"Could not verify {label}: Windows still reports it {'off' if want else 'on'}."
    finally:
        _close_new_settings(before, focus)


def status() -> str:
    live = read_uisettings()
    system_light = _read_personalize("SystemUsesLightTheme")
    on_off = lambda v: "unknown" if v is None else ("on" if v else "off")
    parts = [f"Accent color: {color_name(live['accent'])}",
             f"apps mode: {'dark' if live['apps_dark'] else 'light'}",
             f"Windows mode: {'unknown' if system_light is None else ('light' if system_light else 'dark')}",
             f"transparency: {on_off(live['transparency'])}",
             f"accent on Start/taskbar: {on_off(_toggle_state('accent_on_start_taskbar'))}",
             f"accent on title bars: {on_off(_toggle_state('accent_on_title_bars'))}",
             f"wallpaper: {get_wallpaper() or 'none'}"]
    return "; ".join(parts) + "."


def windows_personalization(parameters: dict, response=None, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = (p.get("action") or "status").strip().lower()
    value = str(p.get("value") or p.get("color") or p.get("path") or "").strip()
    if action not in ACTIONS:
        return f"Unknown windows_personalization action '{action}'. Use one of: {', '.join(ACTIONS)}."
    if _OS != "Windows":
        return "Could not personalize: windows_personalization only works on Windows."
    try:
        if action == "status":
            return status()
        if action == "accent_color":
            return set_accent_color(value)
        if action == "theme_mode":
            return set_theme_mode(value)
        if action == "wallpaper":
            return set_wallpaper(value)
        return set_toggle(action, value)
    except Exception as e:
        return f"Could not {action.replace('_', ' ')}: {e}"


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--apply-theme":
    _apply_theme_com(sys.argv[2])
