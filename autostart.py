"""Start JARVIS at login: HKCU Run key (Windows), LaunchAgent (macOS), XDG autostart entry (Linux).
The OS entry itself is the setting, so there is nothing to keep in sync in api_keys.json."""
import os
import platform
import plistlib
import subprocess
import sys
from pathlib import Path

BASE     = Path(__file__).resolve().parent
_OS      = platform.system()
_NAME    = "JARVIS"
_RUN     = r"Software\Microsoft\Windows\CurrentVersion\Run"
_PLIST   = Path.home() / "Library" / "LaunchAgents" / "com.jarvis.autostart.plist"
_DESKTOP = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "jarvis.desktop"


def command() -> list[str]:
    """What the login item runs: the app's own launcher when there is one. --autostart tells
    JARVIS the OS started it, so it greets the user (main.JarvisLive._boot_prompt)."""
    return [*_launcher(), "--autostart"]


def _launcher() -> list[str]:
    if _OS == "Windows":
        # Jarvis.exe next to the code (dev) or one level up (installed: {app}\Jarvis.exe, {app}\app\)
        exe = next((p for p in (BASE / "Jarvis.exe", BASE.parent / "Jarvis.exe") if p.is_file()), None)
        if exe:
            return [str(exe)]
        pyw = Path(sys.executable).with_name("pythonw.exe")
        return [str(pyw if pyw.is_file() else sys.executable), str(BASE / "jarvis.pyw")]
    if _OS == "Darwin" and "/Contents/Resources/" in sys.executable:
        return ["/usr/bin/open", sys.executable.split("/Contents/Resources/")[0], "--args"]   # the .app bundle
    return [sys.executable, str(BASE / "jarvis.pyw")]


def enabled() -> bool:
    if _OS == "Windows":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN) as k:
                winreg.QueryValueEx(k, _NAME)
            return True
        except OSError:
            return False
    return (_PLIST if _OS == "Darwin" else _DESKTOP).exists()


def set_enabled(on: bool) -> None:
    if _OS == "Windows":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, _NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(command()))
            else:
                try:
                    winreg.DeleteValue(k, _NAME)
                except FileNotFoundError:
                    pass
        return
    path = _PLIST if _OS == "Darwin" else _DESKTOP
    if not on:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if _OS == "Darwin":
        path.write_bytes(plistlib.dumps({"Label": "com.jarvis.autostart", "ProgramArguments": command(),
                                         "RunAtLoad": True}))
    else:
        exec_line = " ".join(f'"{c}"' for c in command())
        path.write_text("[Desktop Entry]\nType=Application\nName=JARVIS\n"
                        f"Exec={exec_line}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n", encoding="utf-8")
