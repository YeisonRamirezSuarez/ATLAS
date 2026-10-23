#computer_settings.py
import json
import re
import sys
import time
import subprocess
import platform
import shutil
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_macos_wifi_interface() -> str:
    try:
        result = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.splitlines()
        for i, line in enumerate(lines):
            if "Wi-Fi" in line or "AirPort" in line:
                for j in range(i, min(i + 4, len(lines))):
                    if lines[j].startswith("Device:"):
                        return lines[j].split(":", 1)[1].strip()
    except Exception:
        pass
    return "en0" 

# ── Windows: real APIs that report the resulting state, instead of key presses ──
# Every function may return a string; computer_settings() passes it to the model as the truth.

def _speakers():
    import comtypes
    from pycaw.pycaw import AudioUtilities
    try:
        comtypes.CoInitialize()  # tools run on executor threads, COM is per-thread
    except OSError:
        pass  # RPC_E_CHANGED_MODE: thread already joined COM in another mode, still usable
    return AudioUtilities.GetSpeakers().EndpointVolume


def _win_volume(delta: int | None = None, absolute: int | None = None, mute: bool | None = None) -> str:
    ev = _speakers()
    if mute is not None:
        ev.SetMute(int(mute), None)
    if absolute is not None or delta is not None:
        level = absolute if absolute is not None else round(ev.GetMasterVolumeLevelScalar() * 100) + delta
        ev.SetMasterVolumeLevelScalar(max(0, min(100, level)) / 100, None)
        if level > 0 and mute is None:
            ev.SetMute(0, None)
    level = round(ev.GetMasterVolumeLevelScalar() * 100)
    return f"Volume is now {level}%{' (muted)' if ev.GetMute() else ''}."


def _win_brightness(delta: int | None = None, absolute: int | None = None) -> str:
    get = "(Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightness -ErrorAction Stop).CurrentBrightness"
    script = get
    if delta is not None or absolute is not None:
        target = f"{absolute}" if absolute is not None else f"({get}) + ({delta})"
        script = (f"$t = [math]::Max(0, [math]::Min(100, {target}));"
                  f"Invoke-CimMethod -InputObject (Get-CimInstance -Namespace root/wmi -ClassName "
                  f"WmiMonitorBrightnessMethods -ErrorAction Stop) -MethodName WmiSetBrightness "
                  f"-Arguments @{{Timeout=1; Brightness=[byte]$t}} | Out-Null; Start-Sleep -Milliseconds 300; {get}")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                       timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    level = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    if r.returncode != 0 or not level.isdigit():
        return ("Could not change brightness: this display does not support software brightness control "
                "(external monitors usually don't).")
    return f"Brightness is now {level}%."


def _win_shutdown(*args: str) -> str:
    r = subprocess.run(["shutdown", *args], capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return "" if r.returncode == 0 else (r.stderr or r.stdout).strip() or f"exit code {r.returncode}"


# ── macOS / Linux: same contract, the resulting level is read back, never assumed ──

def _run(*argv: str) -> str:
    """stdout of a command; RuntimeError with the reason when it fails or is not installed."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"{argv[0]}: {e}") from None
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip() or f"{argv[0]} exit code {r.returncode}")
    return r.stdout.strip()


def _step(delta: int) -> str:
    return f"{abs(delta)}%{'+' if delta > 0 else '-'}"


def _unix_volume(delta: int | None = None, absolute: int | None = None, mute: bool | None = None) -> str:
    """macOS: osascript. Linux: wpctl (PipeWire: Fedora, Bazzite, Ubuntu 22.10+) or pactl (PulseAudio)."""
    try:
        if _OS == "Darwin":
            cmds = []
            if mute is not None:
                cmds.append(f"set volume {'with' if mute else 'without'} output muted")
            if absolute is not None:
                cmds.append(f"set volume output volume {absolute}")
            if delta is not None:
                cmds.append(f"set volume output volume (output volume of (get volume settings) + {delta})")
            # -> "output volume:45, input volume:75, alert volume:100, output muted:false"
            out = _run("osascript", *[x for c in cmds for x in ("-e", c)], "-e", "get volume settings")
            m, muted = re.search(r"output volume:(\d+)", out), "output muted:true" in out
            level = int(m.group(1)) if m else None
        elif shutil.which("wpctl"):
            sink = "@DEFAULT_AUDIO_SINK@"
            if mute is not None:
                _run("wpctl", "set-mute", sink, "1" if mute else "0")
            if absolute is not None:
                _run("wpctl", "set-volume", sink, f"{absolute}%")
            if delta is not None:
                _run("wpctl", "set-volume", "-l", "1.0", sink, _step(delta))
            out = _run("wpctl", "get-volume", sink)          # -> "Volume: 0.45 [MUTED]"
            m, muted = re.search(r"([\d.]+)", out), "MUTED" in out
            level = round(float(m.group(1)) * 100) if m else None
        else:
            sink = "@DEFAULT_SINK@"
            if mute is not None:
                _run("pactl", "set-sink-mute", sink, "1" if mute else "0")
            if absolute is not None:
                _run("pactl", "set-sink-volume", sink, f"{absolute}%")
            if delta is not None:
                _run("pactl", "set-sink-volume", sink, f"{'+' if delta > 0 else '-'}{abs(delta)}%")
            m = re.search(r"(\d+)%", _run("pactl", "get-sink-volume", sink))
            muted = "yes" in _run("pactl", "get-sink-mute", sink)
            level = int(m.group(1)) if m else None
    except RuntimeError as e:
        return f"Could not change the volume: {e}"
    if level is None:
        return "Volume command ran but the new level could not be read; effect not verified."
    return f"Volume is now {level}%{' (muted)' if muted else ''}."


def _unix_brightness(delta: int | None = None, absolute: int | None = None) -> str:
    """Linux: brightnessctl (backlight via sysfs, works on X11 and Wayland). macOS: brightness keys."""
    if _OS == "Darwin":
        if absolute is not None:
            return "Could not set an exact brightness: macOS only supports brightness up/down here."
        try:
            _run("osascript", "-e", f'tell application "System Events" to key code {144 if delta > 0 else 145}')
        except RuntimeError as e:
            return f"Could not change brightness: {e}"
        return "Sent the brightness key; effect not verified."
    if not shutil.which("brightnessctl"):
        return "Could not change brightness: brightnessctl is not installed (it works on X11 and Wayland)."
    try:
        if absolute is not None:
            _run("brightnessctl", "-q", "set", f"{absolute}%")
        elif delta:
            _run("brightnessctl", "-q", "set", _step(delta))
        out = _run("brightnessctl", "-m")    # -> "intel_backlight,backlight,400,40%,1000"
    except RuntimeError as e:
        return f"Could not change brightness: {e}"
    parts = out.splitlines()[0].split(",") if out else []
    if len(parts) < 4:
        return "Brightness command ran but the new level could not be read; effect not verified."
    return f"Brightness is now {parts[3]}."


_volume     = _win_volume if _OS == "Windows" else _unix_volume
_brightness = _win_brightness if _OS == "Windows" else _unix_brightness

def volume_up():      return _volume(delta=10)
def volume_down():    return _volume(delta=-10)
def volume_mute():    return _volume(mute=True)
def volume_unmute():  return _volume(mute=False)
def volume_set(value: int):     return _volume(absolute=max(0, min(100, int(value))))
def brightness_up():   return _brightness(delta=10)
def brightness_down(): return _brightness(delta=-10)
def brightness_set(value: int): return _brightness(absolute=max(0, min(100, int(value))))

def close_app():
    if _OS == "Darwin": pyautogui.hotkey("command", "q")
    else:               pyautogui.hotkey("alt", "f4")

def close_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "w")
    else:               pyautogui.hotkey("ctrl", "w")

def full_screen():
    if _OS == "Darwin": pyautogui.hotkey("ctrl", "command", "f")
    else:               pyautogui.press("f11")

def minimize_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "m")
    else:               pyautogui.hotkey("win", "down")

def maximize_window():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to keystroke "f" '
            'using {control down, command down}'],
            capture_output=True)
    elif _OS == "Windows":
        pyautogui.hotkey("win", "up")
    else:
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-b", "add,maximized_vert,maximized_horz"],
                capture_output=True)
        except Exception:
            pyautogui.hotkey("super", "up")

def snap_left():
    if _OS == "Windows":
        pyautogui.hotkey("win", "left")
    elif _OS == "Linux":
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-e", "0,0,0,960,1080"],
                capture_output=True)
        except Exception:
            pass

def snap_right():
    if _OS == "Windows":
        pyautogui.hotkey("win", "right")
    elif _OS == "Linux":
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-e", "0,960,0,960,1080"],
                capture_output=True)
        except Exception:
            pass

def switch_window():
    if _OS == "Darwin": pyautogui.hotkey("command", "tab")
    else:               pyautogui.hotkey("alt", "tab")

def show_desktop():
    if _OS == "Darwin":   pyautogui.hotkey("fn", "f11")
    elif _OS == "Windows": pyautogui.hotkey("win", "d")
    else:                  pyautogui.hotkey("super", "d")

def open_task_manager():
    if _OS == "Windows":
        pyautogui.hotkey("ctrl", "shift", "esc")
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "Activity Monitor"])
    else:
        for cmd in [["gnome-system-monitor"], ["xfce4-taskmanager"], ["htop"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                break


def focus_search():
    if _OS == "Darwin": pyautogui.hotkey("command", "l")
    else:               pyautogui.hotkey("ctrl", "l")

# Media keys reach Spotify, YouTube, the Music app... whatever owns the media session.
# Windows gives no feedback, so the result says the key was sent, not that the song changed.
def play_pause():  pyautogui.press("playpause"); return "Sent the play/pause media key."
def next_track():  pyautogui.press("nexttrack"); return "Sent the next-track media key."
def prev_track():  pyautogui.press("prevtrack"); return "Sent the previous-track media key."

def refresh_page():
    if _OS == "Darwin": pyautogui.hotkey("command", "r")
    else:               pyautogui.press("f5")

def close_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "w")
    else:               pyautogui.hotkey("ctrl", "w")

def new_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "t")
    else:               pyautogui.hotkey("ctrl", "t")

def next_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "bracketright")
    else:               pyautogui.hotkey("ctrl", "tab")

def prev_tab():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "bracketleft")
    else:               pyautogui.hotkey("ctrl", "shift", "tab")

def go_back():
    if _OS == "Darwin": pyautogui.hotkey("command", "left")
    else:               pyautogui.hotkey("alt", "left")

def go_forward():
    if _OS == "Darwin": pyautogui.hotkey("command", "right")
    else:               pyautogui.hotkey("alt", "right")

def zoom_in():
    if _OS == "Darwin": pyautogui.hotkey("command", "equal")
    else:               pyautogui.hotkey("ctrl", "equal")

def zoom_out():
    if _OS == "Darwin": pyautogui.hotkey("command", "minus")
    else:               pyautogui.hotkey("ctrl", "minus")

def zoom_reset():
    if _OS == "Darwin": pyautogui.hotkey("command", "0")
    else:               pyautogui.hotkey("ctrl", "0")

def find_on_page():
    if _OS == "Darwin": pyautogui.hotkey("command", "f")
    else:               pyautogui.hotkey("ctrl", "f")

def reload_page_n(n: int):
    for _ in range(max(1, n)):
        refresh_page()
        time.sleep(0.8)


def scroll_up(amount: int = 500):    pyautogui.scroll(amount)
def scroll_down(amount: int = 500):  pyautogui.scroll(-amount)

def scroll_top():
    if _OS == "Darwin": pyautogui.hotkey("command", "up")
    else:               pyautogui.hotkey("ctrl", "home")

def scroll_bottom():
    if _OS == "Darwin": pyautogui.hotkey("command", "down")
    else:               pyautogui.hotkey("ctrl", "end")

def page_up():   pyautogui.press("pageup")
def page_down(): pyautogui.press("pagedown")


def copy():
    if _OS == "Darwin": pyautogui.hotkey("command", "c")
    else:               pyautogui.hotkey("ctrl", "c")

def paste():
    if _OS == "Darwin": pyautogui.hotkey("command", "v")
    else:               pyautogui.hotkey("ctrl", "v")

def cut():
    if _OS == "Darwin": pyautogui.hotkey("command", "x")
    else:               pyautogui.hotkey("ctrl", "x")

def undo():
    if _OS == "Darwin": pyautogui.hotkey("command", "z")
    else:               pyautogui.hotkey("ctrl", "z")

def redo():
    if _OS == "Darwin": pyautogui.hotkey("command", "shift", "z")
    else:               pyautogui.hotkey("ctrl", "y")

def select_all():
    if _OS == "Darwin": pyautogui.hotkey("command", "a")
    else:               pyautogui.hotkey("ctrl", "a")

def save_file():
    if _OS == "Darwin": pyautogui.hotkey("command", "s")
    else:               pyautogui.hotkey("ctrl", "s")

def press_enter():   pyautogui.press("enter")
def press_escape():  pyautogui.press("escape")
def press_key(key: str): pyautogui.press(key)

def type_text(text: str, press_enter_after: bool = False):
    if not text:
        return
    if _PYPERCLIP:
        pyperclip.copy(str(text))
        time.sleep(0.15)
        paste()
    else:
        pyautogui.write(str(text), interval=0.03)
    if press_enter_after:
        time.sleep(0.1)
        pyautogui.press("enter")

def take_screenshot():
    if _OS == "Windows":
        pyautogui.hotkey("win", "shift", "s")
    elif _OS == "Darwin":
        pyautogui.hotkey("command", "shift", "3")
    else:
        for cmd in [["scrot"], ["gnome-screenshot"], ["import", "-window", "root", "screenshot.png"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return
        pyautogui.hotkey("ctrl", "print_screen")

def lock_screen():
    if _OS == "Windows":
        import ctypes
        if not ctypes.windll.user32.LockWorkStation():
            return f"Could not lock the PC (error {ctypes.GetLastError()})."
        return "PC locked."
    elif _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
    else:
        for cmd in [
            ["gnome-screensaver-command", "-l"],
            ["xdg-screensaver", "lock"],
            ["loginctl", "lock-session"],
        ]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.run(cmd, capture_output=True)
                return

def open_system_settings():
    if _OS == "Windows":
        pyautogui.hotkey("win", "i")
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "System Preferences"])
    else:
        for cmd in [["gnome-control-center"], ["xfce4-settings-manager"], ["kcmshell5"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return

def open_file_explorer():
    if _OS == "Windows":
        pyautogui.hotkey("win", "e")
    elif _OS == "Darwin":
        subprocess.Popen(["open", str(Path.home())])
    else:
        for cmd in [["nautilus"], ["thunar"], ["dolphin"], ["nemo"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return
        subprocess.Popen(["xdg-open", str(Path.home())])

def sleep_display():
    if _OS == "Windows":
        try:
            import ctypes
            ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
        except Exception as e:
            print(f"[Settings] sleep_display failed: {e}")
    elif _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
    else:
        subprocess.run(["xset", "dpms", "force", "off"], capture_output=True)

def open_run():
    if _OS == "Windows":
        pyautogui.hotkey("win", "r")

def dark_mode():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell app "System Events" to tell appearance preferences '
            'to set dark mode to not dark mode'],
            capture_output=True)
    elif _OS == "Windows":
        try:
            import winreg
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            current, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.CloseKey(key)
        except Exception as e:
            print(f"[Settings] dark_mode registry failed: {e}")
    else:
        try:
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True, text=True
            )
            current = result.stdout.strip()
            new_scheme = "'default'" if "dark" in current else "'prefer-dark'"
            subprocess.run(
                ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", new_scheme],
                capture_output=True
            )
        except Exception as e:
            print(f"[Settings] dark_mode Linux failed: {e}")

def toggle_wifi():
    if _OS == "Darwin":
        iface = _get_macos_wifi_interface()
        result = subprocess.run(
            ["networksetup", "-getairportpower", iface],
            capture_output=True, text=True
        )
        state = "off" if "On" in result.stdout else "on"
        subprocess.run(["networksetup", "-setairportpower", iface, state],
            capture_output=True)
    elif _OS == "Windows":
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "$adapter = Get-NetAdapter | Where-Object {$_.PhysicalMediaType -eq 'Native 802.11'};"
                 "if ($adapter.Status -eq 'Up') { Disable-NetAdapter -Name $adapter.Name -Confirm:$false }"
                 "else { Enable-NetAdapter -Name $adapter.Name -Confirm:$false }"],
                capture_output=True, timeout=10
            )
        except Exception as e:
            print(f"[Settings] toggle_wifi Windows failed: {e}")
    else:
        try:
            result = subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True)
            state  = "off" if "enabled" in result.stdout else "on"
            subprocess.run(["nmcli", "radio", "wifi", state], capture_output=True)
        except Exception as e:
            print(f"[Settings] toggle_wifi Linux failed: {e}")

def restart_computer():
    if _OS == "Windows":
        error = _win_shutdown("/r", "/t", "15")
        return f"Could not restart: {error}" if error else \
               "Restarting in 15 seconds. Say 'cancel shutdown' to abort."
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to restart'],
            capture_output=True)
    else:
        subprocess.run(["systemctl", "reboot"], capture_output=True)

def shutdown_computer():
    if _OS == "Windows":
        error = _win_shutdown("/s", "/t", "15")
        return f"Could not shut down: {error}" if error else \
               "Shutting down in 15 seconds. Say 'cancel shutdown' to abort."
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to shut down'],
            capture_output=True)
    else:
        subprocess.run(["systemctl", "poweroff"], capture_output=True)

def cancel_shutdown():
    if _OS == "Windows":
        error = _win_shutdown("/a")
        return f"Nothing to cancel: {error}" if error else "Shutdown/restart cancelled."
    subprocess.run(["shutdown", "-c"], capture_output=True)

def sleep_computer():
    if _OS == "Windows":
        import ctypes
        # SetSuspendState(hibernate=False, force=False, wakeupEvents=False)
        if not ctypes.windll.powrprof.SetSuspendState(False, False, False):
            return "Could not put the PC to sleep."
        return "PC went to sleep."
    if _OS == "Darwin":
        subprocess.run(["pmset", "sleepnow"], capture_output=True)
    else:
        subprocess.run(["systemctl", "suspend"], capture_output=True)

ACTION_MAP: dict[str, callable] = {
    "volume_up":           volume_up,
    "volume_down":         volume_down,
    "mute":                volume_mute,
    "unmute":              volume_unmute,
    "brightness_up":       brightness_up,
    "brightness_down":     brightness_down,
    "sleep_display":       sleep_display,
    "screen_off":          sleep_display,
    "sleep":               sleep_computer,
    "play_pause":          play_pause,
    "pause_video":         play_pause,
    "next_track":          next_track,
    "prev_track":          prev_track,
    "close_app":           close_app,
    "close_window":        close_window,
    "full_screen":         full_screen,
    "fullscreen":          full_screen,
    "minimize":            minimize_window,
    "maximize":            maximize_window,
    "snap_left":           snap_left,
    "snap_right":          snap_right,
    "switch_window":       switch_window,
    "show_desktop":        show_desktop,
    "task_manager":        open_task_manager,
    "focus_search":        focus_search,
    "refresh_page":        refresh_page,
    "reload":              refresh_page,
    "close_tab":           close_tab,
    "new_tab":             new_tab,
    "next_tab":            next_tab,
    "prev_tab":            prev_tab,
    "go_back":             go_back,
    "go_forward":          go_forward,
    "zoom_in":             zoom_in,
    "zoom_out":            zoom_out,
    "zoom_reset":          zoom_reset,
    "find_on_page":        find_on_page,
    "scroll_up":           scroll_up,
    "scroll_down":         scroll_down,
    "scroll_top":          scroll_top,
    "scroll_bottom":       scroll_bottom,
    "page_up":             page_up,
    "page_down":           page_down,
    "copy":                copy,
    "paste":               paste,
    "cut":                 cut,
    "undo":                undo,
    "redo":                redo,
    "select_all":          select_all,
    "save":                save_file,
    "enter":               press_enter,
    "escape":              press_escape,
    "screenshot":          take_screenshot,
    "lock_screen":         lock_screen,
    "open_settings":       open_system_settings,
    "file_explorer":       open_file_explorer,
    "open_run":            open_run,
    "dark_mode":           dark_mode,
    "toggle_wifi":         toggle_wifi,
    "restart":             restart_computer,
    "shutdown":            shutdown_computer,
    "cancel_shutdown":     cancel_shutdown,
}

# Names models tend to invent, mapped to the real action.
_ALIASES = {
    "lock": "lock_screen", "lock_pc": "lock_screen", "lock_computer": "lock_screen",
    "next": "next_track", "next_song": "next_track", "skip": "next_track", "skip_track": "next_track",
    "previous": "prev_track", "previous_track": "prev_track", "previous_song": "prev_track",
    "play": "play_pause", "pause": "play_pause", "resume": "play_pause", "toggle_mute": "mute",
    "set_volume": "volume_set", "set_brightness": "brightness_set", "power_off": "shutdown",
    "turn_off": "shutdown", "reboot": "restart", "abort_shutdown": "cancel_shutdown", "suspend": "sleep",
}

_DANGEROUS_ACTIONS = {"restart", "shutdown"}


def _detect_action(description: str) -> dict:
    from or_client import client

    available = ", ".join(sorted(ACTION_MAP.keys())) + \
                ", volume_set, brightness_set, type_text, press_key, reload_n"

    prompt = f"""You are an intent detector for a computer control assistant.
The user issued a command (possibly in any language): "{description}"
Available actions: {available}
Return ONLY a valid JSON object: {{"action": "action_name", "value": null_or_value}}
Rules:
- For volume_set / brightness_set: value is an integer 0-100.
- For type_text: value is the exact text to type.
- For press_key: value is the key name (e.g. "f5", "tab", "enter").
- For reload_n: value is an integer.
- Return ONLY the JSON, no explanation, no markdown."""

    try:
        raw  = client.chat_json(prompt, system="Return only valid JSON. No extra text.")
        return raw
    except Exception as e:
        print(f"[Settings] Intent detection failed: {e}")
        return {"action": description.lower().replace(" ", "_"), "value": None}
    
def computer_settings(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    if not _PYAUTOGUI:
        return "pyautogui is not installed. Run: pip install pyautogui"

    params      = parameters or {}
    raw_action  = params.get("action", "").strip()
    description = params.get("description", "").strip()
    value       = params.get("value", None)

    if not raw_action and description:
        detected   = _detect_action(description)
        raw_action = detected.get("action", "")
        if value is None:
            value = detected.get("value")

    action = raw_action.lower().strip().replace(" ", "_").replace("-", "_")
    action = _ALIASES.get(action, action)

    known = set(ACTION_MAP) | {"volume_set", "brightness_set", "type_text", "write_on_screen", "type",
                               "write", "press_key", "reload_n", "refresh_n", "reload_page_n"}
    if action not in known and (description or raw_action):
        detected = _detect_action(description or raw_action)
        action   = _ALIASES.get(str(detected.get("action", "")), str(detected.get("action", "")))
        if value is None:
            value = detected.get("value")

    if not action:
        return "No action could be determined."

    print(f"[Settings] Action: {action}  Value: {value}  OS: {_OS}")
    if player:
        player.write_log(f"[Settings] {action}")

    if action in _DANGEROUS_ACTIONS:
        confirmed = str(params.get("confirmed", "")).lower()
        if confirmed not in ("yes", "true", "1", "confirm"):
            return (
                f"This will {action} the computer. "
                f"Please confirm by calling again with confirmed=yes."
            )

    if action in ("volume_set", "brightness_set"):
        if value is None or not str(value).strip("% ").isdigit():
            return f"{action} needs a value from 0 to 100."
        setter = volume_set if action == "volume_set" else brightness_set
        try:
            return setter(int(str(value).strip("% "))) or f"{action} to {value}% sent."
        except Exception as e:
            return f"Could not {action.replace('_', ' ')}: {e}"

    if action in ("type_text", "write_on_screen", "type", "write"):
        text = str(value or params.get("text", "")).strip()
        if not text:
            return "No text provided to type."
        enter_after = str(params.get("press_enter", "false")).lower() in ("true", "1", "yes")
        type_text(text, press_enter_after=enter_after)
        return f"Typed: {text[:80]}"

    if action == "press_key":
        key = str(value or params.get("key", "")).strip()
        if not key:
            return "No key specified."
        press_key(key)
        return f"Pressed: {key}"

    if action in ("reload_n", "refresh_n", "reload_page_n"):
        try:
            reload_page_n(int(value or 1))
            return f"Reloaded {value or 1} time(s)."
        except Exception as e:
            return f"Reload failed: {e}"

    if action == "scroll_up":
        scroll_up(int(value or 500))
        return "Scrolled up."

    if action == "scroll_down":
        scroll_down(int(value or 500))
        return "Scrolled down."

    func = ACTION_MAP.get(action)
    if not func:
        return f"Unknown action: '{raw_action}'."

    try:
        # Most actions only send a key press: say that, not that it worked.
        return func() or f"Sent {action}; effect not verified."
    except Exception as e:
        print(f"[Settings] Action failed ({action}): {e}")
        return f"Action failed ({action}): {e}"