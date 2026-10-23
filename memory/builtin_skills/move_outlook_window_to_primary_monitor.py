# PLAN: Find the visible Chrome Outlook PWA window, note which monitor it is on, move it to the primary monitor, maximize it, bring it to the foreground, and check the result.
# RISK: safe

import ctypes
import ctypes.wintypes as wt
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD), ("szDevice", wt.WCHAR * 32)]

MONITORINFOF_PRIMARY = 1
MONITOR_DEFAULTTONEAREST = 2
SW_RESTORE, SW_SHOW, SW_MAXIMIZE = 9, 5, 3
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
GA_ROOTOWNER = 3
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

user32.MonitorFromWindow.restype = wt.HMONITOR
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetAncestor.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
kernel32.OpenProcess.restype = wt.HANDLE


def monitor_info(hmon):
    mi = MONITORINFOEXW()
    mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
    if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        raise RuntimeError("GetMonitorInfoW failed")
    return mi


# Enumerate monitors
monitors = []
MonitorEnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)

def _mon_cb(hmon, hdc, rect, lp):
    mi = monitor_info(hmon)
    monitors.append((hmon, mi.szDevice, bool(mi.dwFlags & MONITORINFOF_PRIMARY),
                     (mi.rcMonitor.left, mi.rcMonitor.top, mi.rcMonitor.right, mi.rcMonitor.bottom),
                     (mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom)))
    return True

user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_mon_cb), 0)
if not monitors:
    raise RuntimeError("No monitors found")

primary = next((m for m in monitors if m[2]), None)
if primary is None:
    raise RuntimeError("Primary monitor not found")


def describe_monitor(hmon):
    for i, m in enumerate(monitors, 1):
        if m[0] == hmon:
            l, t, r, b = m[3]
            return f"Monitor {i} ({m[1]}{', PRIMARY' if m[2] else ''}, {r-l}x{b-t} at {l},{t})"
    mi = monitor_info(hmon)
    return f"{mi.szDevice}"


def process_name(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.split("\\")[-1].lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


# Find Outlook windows
candidates = []
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

def _win_cb(hwnd, lp):
    if not user32.IsWindowVisible(hwnd):
        return True
    n = user32.GetWindowTextLengthW(hwnd)
    if n == 0:
        return True
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    title = buf.value
    if "outlook" not in title.lower():
        return True
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    candidates.append((hwnd, title, cls.value, process_name(hwnd)))
    return True

user32.EnumWindows(EnumWindowsProc(_win_cb), 0)
if not candidates:
    raise RuntimeError("No visible window with 'Outlook' in its title was found")

def score(c):
    hwnd, title, cls, proc = c
    s = 0
    if proc == "chrome.exe":
        s += 4
    if cls == "Chrome_WidgetWin_1":
        s += 2
    if "google chrome" not in title.lower():
        s += 1  # PWA windows usually lack the browser suffix
    return s

candidates.sort(key=score, reverse=True)
hwnd, title, cls, proc = candidates[0]

src_mon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
src_desc = describe_monitor(src_mon)

# Move to the primary monitor and maximize
if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
    user32.ShowWindow(hwnd, SW_RESTORE)
    time.sleep(0.3)

wl, wt_, wr, wb = primary[4]
w = max(800, (wr - wl) * 3 // 4)
h = max(600, (wb - wt_) * 3 // 4)
if not user32.SetWindowPos(hwnd, None, wl + 40, wt_ + 40, w, h, SWP_NOZORDER | SWP_NOACTIVATE):
    raise RuntimeError(f"SetWindowPos failed (error {ctypes.get_last_error()})")
time.sleep(0.3)
user32.ShowWindow(hwnd, SW_MAXIMIZE)
time.sleep(0.4)

# Bring the window to the foreground
def force_foreground(target):
    fg = user32.GetForegroundWindow()
    cur_tid = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    tgt_tid = user32.GetWindowThreadProcessId(target, None)
    # Alt key tap unlocks the SetForegroundWindow restriction
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    attached = []
    for tid in {fg_tid, tgt_tid}:
        if tid and tid != cur_tid and user32.AttachThreadInput(cur_tid, tid, True):
            attached.append(tid)
    try:
        user32.ShowWindow(target, SW_MAXIMIZE)
        user32.BringWindowToTop(target)
        user32.SetForegroundWindow(target)
        user32.SetActiveWindow(target)
        user32.SetFocus(target)
    finally:
        for tid in attached:
            user32.AttachThreadInput(cur_tid, tid, False)

def is_fg(target):
    fg = user32.GetForegroundWindow()
    return fg == target or (fg and user32.GetAncestor(fg, GA_ROOTOWNER) == target)

for _ in range(5):
    force_foreground(hwnd)
    time.sleep(0.4)
    if is_fg(hwnd):
        break

# Verify
dst_mon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
on_primary = monitor_info(dst_mon).dwFlags & MONITORINFOF_PRIMARY
maximized = bool(user32.IsZoomed(hwnd))
foreground = is_fg(hwnd)

r = wt.RECT()
user32.GetWindowRect(hwnd, ctypes.byref(r))

print(f"Window: '{title}' (process {proc or 'unknown'}, class {cls}, hwnd {hwnd})")
print(f"Moved from: {src_desc}")
print(f"Now on: {describe_monitor(dst_mon)}")
print(f"Maximized: {maximized} | Foreground: {foreground} | Rect: ({r.left},{r.top})-({r.right},{r.bottom})")
if len(candidates) > 1:
    print("Other 'Outlook' windows ignored: " + "; ".join(f"'{c[1]}' ({c[3]})" for c in candidates[1:]))

if not on_primary:
    raise RuntimeError("Outlook window is not on the primary monitor")
if not maximized:
    raise RuntimeError("Outlook window was moved to the primary monitor but is not maximized")
if not foreground:
    raise RuntimeError("Outlook window is maximized on the primary monitor but Windows refused to bring it to the foreground")

print("Outlook is maximized on the primary monitor and in the foreground.")