from __future__ import annotations

import json
import math
import os
import platform
import random
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

import autostart
from config import PROVIDERS, VERSION, get_effort

from PyQt6.QtCore import (
    QEasingCurve, QMimeData, QObject, QPointF, QRectF, QSize, Qt, QVariantAnimation,
    QMetaObject, QTimer, QUrl, pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QIcon, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QPolygonF, QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractButton, QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QSlider, QTextEdit, QVBoxLayout, QWidget, QProgressBar,
)

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"

_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W,     _MIN_H     = 820, 580
_LEFT_W  = 200
_RIGHT_W = 300

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


# Bundled in assets/fonts (OFL); _load_fonts() falls back to Bahnschrift when they can't load.
_FONT    = "Chakra Petch"   # UI text: squared tech face, readable at small sizes
_DISPLAY = "Orbitron"       # wordmark and big headings
_MONO    = "Consolas"


def _load_fonts():
    global _FONT, _DISPLAY
    for f in (BASE_DIR / "assets" / "fonts").glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(f))
    have = set(QFontDatabase.families())
    _FONT    = _FONT if _FONT in have else "Bahnschrift"
    _DISPLAY = _DISPLAY if _DISPLAY in have else _FONT


class C:
    # ATLAS palette: sky-blue (celeste) light on near-black, ice-white highlights.
    BG        = "#030507"
    PANEL     = "#070b10"
    PANEL2    = "#0a1017"
    BORDER    = "#11202b"
    BORDER_B  = "#1d5a7a"
    BORDER_A  = "#142734"
    PRI       = "#4fd1ff"
    PRI_DIM   = "#2a7fa3"
    PRI_GHO   = "#062231"
    ACC       = "#1f8bff"
    ACC2      = "#a8ecff"
    ARC       = "#8ff0ff"
    ARC_D     = "#0b4f66"
    GREEN     = "#3dffa8"
    GREEN_D   = "#1f9e64"
    RED       = "#ff3b5c"
    RED_D     = "#7a0f22"
    MUTED_C   = "#ff3b5c"
    TEXT      = "#d9f3ff"
    TEXT_DIM  = "#5f8296"
    TEXT_MED  = "#8db4c8"
    WHITE     = "#f3fbff"
    DARK      = "#010203"
    BAR_BG    = "#0a1820"
    GRID      = "#0b1922"
    STEEL     = "#263440"
    STEEL_D   = "#0b1118"
    COPPER    = "#39b6e8"   # reactor coils
    COPPER_D  = "#082a3b"
    THEME     = "dark"
    CORE      = "#f0fdff"   # reactor core light: stays bright in both themes
    ON_PRI    = "#001520"   # text on a filled celeste button
    FIELD     = "#02080c"   # inputs, lists
    OVERLAY   = "rgba(1, 4, 7, 245)"
    HOVER     = "#061a25"
    DRAG      = "#08263a"
    LINE      = "#1a4a5a"
    MUTE_BG   = "#16040a"
    LIVE_BG   = "#00140a"
    LIVE_HOV  = "#001f10"
    AMBER     = "#ffb020"   # error state of the core


_DARK = {k: v for k, v in vars(C).items() if k.isupper()}
# Same identity on paper: one celeste family on cool white, text kept at 4.5:1 or better. The reactor
# housing stays a dark navy puck so the core still glows. Tokens that differ in the other theme never
# share a value (off by one where needed): restyle() maps by value.
_LIGHT = dict(
    BG="#eef4f8", PANEL="#ffffff", PANEL2="#f6fafc", BORDER="#d5e3ec", BORDER_B="#7fb8d8", BORDER_A="#e2edf3",
    PRI="#0369a1", PRI_DIM="#3d8fbd", PRI_GHO="#e0f2fe", ACC="#0284c7", ACC2="#075985",
    ARC="#0a8fd0", ARC_D="#cdeefc", GREEN="#0b7a53", GREEN_D="#9fdcc2", RED="#c8102e", RED_D="#f6d5d9",
    MUTED_C="#c8102e", TEXT="#0b1b26", TEXT_DIM="#557083", TEXT_MED="#33495a", WHITE="#06131c",
    DARK="#f9fcfe", BAR_BG="#e2edf4", GRID="#e3edf4", STEEL="#1d2f3f", STEEL_D="#0c1824",
    COPPER="#38bdf8", COPPER_D="#0b2c40", THEME="light", CORE="#f0fdfe", ON_PRI="#fffefe",
    FIELD="#fffffe", OVERLAY="rgba(248, 251, 253, 250)", HOVER="#e9f5fc", DRAG="#d8eefa", LINE="#8fbfd9",
    MUTE_BG="#fbe3e6", LIVE_BG="#e3f3ec", LIVE_HOV="#d2ecdf", AMBER="#b45309",
)
THEMES = {"dark": _DARK, "light": _LIGHT}


def apply_theme(name: str):
    """Swaps the palette for widgets built from now on; restyle() also repaints the live ones."""
    for k, v in THEMES.get(name, _DARK).items():
        setattr(C, k, v)


def restyle(name: str):
    """Switches the theme without a restart: each old palette colour in live stylesheets, rich text
    and the log becomes the same token's colour in the new palette; painted widgets read C anyway."""
    old = {v.lower(): k for k, v in vars(C).items() if k.isupper() and k != "THEME"}
    apply_theme(name)
    swap = lambda s: re.sub(r"#[0-9a-fA-F]{6}\b|rgba\([^)]*\)",
                            lambda m: getattr(C, old.get(m[0].lower(), ""), m[0]), s)
    for w in QApplication.allWidgets():
        if w.styleSheet():
            w.setStyleSheet(swap(w.styleSheet()))
        if isinstance(w, QLabel) and "#" in w.text():
            w.setText(swap(w.text()))
        elif isinstance(w, ChatView):
            w.retheme()
        elif isinstance(w, LogWidget):
            w.setHtml(swap(w.toHtml()))
            cur = w.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            w.setTextCursor(cur)
        elif isinstance(w, MetricBar):
            w._color = swap(w._color)
        elif isinstance(w, HudCanvas):
            w._bg = w._compass = None   # cached layers
        elif isinstance(w, MainWindow):
            w._title_bar()
        w.update()


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c

class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = (0.0, 0.0)   # MB/s down, up
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        self._wmi_temp = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (recv / (1024 * 1024), sent / (1024 * 1024))   # MB/s down, up
        else:
            net = (0.0, 0.0)
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()

        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # NVIDIA
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if r.returncode == 0:
                vals = [float(v.strip()) for v in r.stdout.strip().split("\n") if v.strip()]
                if vals:
                    return sum(vals) / len(vals)
        except Exception:
            pass

        # AMD (Linux)
        if _OS == "Linux":
            try:
                r = subprocess.run(
                    ["rocm-smi", "--showuse", "--csv"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n"):
                        parts = line.split(",")
                        if len(parts) >= 2:
                            try:
                                return float(parts[1].strip().replace("%", ""))
                            except ValueError:
                                pass
            except Exception:
                pass

            # Intel GPU (Linux)
            try:
                r = subprocess.run(
                    ["intel_gpu_top", "-J", "-s", "500"],
                    capture_output=True, text=True, timeout=1
                )
                if r.returncode == 0 and "Render/3D" in r.stdout:
                    import re
                    m = re.search(r'"busy":\s*([\d.]+)', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        # macOS — powermetrics (GPU Engine)
        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["sudo", "-n", "powermetrics", "-n", "1", "-i", "500",
                     "--samplers", "gpu_power"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0 and "GPU" in r.stdout:
                    import re
                    m = re.search(r'GPU\s+Active:\s+([\d.]+)%', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        return -1.0

    def _get_temp(self) -> float:
        try:
            temps = psutil.sensors_temperatures()
            candidates = ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                          "cpu-thermal", "zenpower", "it8688"]
            for name in candidates:
                if name in temps:
                    entries = temps[name]
                    if entries:
                        return entries[0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass
        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["osx-cpu-temp"], capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    import re
                    m = re.search(r"([\d.]+)", r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        # Needs admin on most PCs. Stop after the first failure instead of
        # spawning powershell every 1.5 s forever.
        if _OS == "Windows" and self._wmi_temp:
            try:
                r = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace root/wmi).CurrentTemperature"],
                    capture_output=True, text=True, timeout=3
                )
                if r.returncode == 0 and r.stdout.strip():
                    raw = float(r.stdout.strip().split("\n")[0])
                    return (raw / 10.0) - 273.15
            except Exception:
                pass
            self._wmi_temp = False

        return -1.0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

def _chamfer(r: QRectF, c: float) -> QPolygonF:
    """Rectangle with the top-left and bottom-right corners cut, HUD style."""
    l, t, rt, b = r.left(), r.top(), r.right(), r.bottom()
    return QPolygonF([QPointF(l + c, t), QPointF(rt, t), QPointF(rt, b - c),
                      QPointF(rt - c, b), QPointF(l, b), QPointF(l, t + c)])


# Tool -> HUD task animation. Unknown tools fall back to "system".
TASK_MODES = {
    "apple_music": "media", "youtube_video": "media",
    "code_helper": "code", "dev_agent": "code", "code_agent": "code", "solve_task": "code", "agent_task": "code", "task_status": "code",
    "web_search": "net", "browser_control": "net", "flight_finder": "net", "weather_report": "net",
    "screen_process": "scan",
    "send_message": "comms", "whatsapp_read": "comms", "email": "comms", "reminder": "comms",
    "save_memory": "memory", "search_memory": "memory",
}
# mode: (status title, colour)
TASK_LOOK = {
    "media":  ("REPRODUCIENDO",       C.ARC),
    "code":   ("SINTETIZANDO CÓDIGO", C.GREEN),
    "net":    ("ENLACE A LA RED",     C.ARC),
    "scan":   ("ESCANEO ÓPTICO",      C.RED),
    "comms":  ("TRANSMITIENDO",       C.PRI),
    "memory": ("NÚCLEO DE MEMORIA",   C.ACC2),
    "system": ("CONTROL DEL SISTEMA", C.PRI),
}
_CODE_RING = ("def execute(task):  plan = atlas.think(task)  for step in plan: run(step)  "
              "return verify(result)  0x3FA9 0x7C11 0xE0D2  ")


def _quick(parent: QWidget, qml: str, ctx: dict, overlay: bool = False, clicks: bool = False):
    """qml/<qml> in a QQuickWidget child of parent, ctx as its context properties. overlay: transparent,
    stacked over parent's own painting, click-through unless clicks. None when Qt Quick can't load it: under the
    offscreen platform (tests, CI), with ATLAS_CLASSIC_HUD=1, or on a QML error (printed)."""
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen" or os.environ.get("ATLAS_CLASSIC_HUD"):
        return None
    try:
        from PyQt6.QtQuickWidgets import QQuickWidget
    except ImportError:
        return None
    v = QQuickWidget(parent)
    if overlay:
        v.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)
        v.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not clicks)
        v.setClearColor(Qt.GlobalColor.transparent)
    v.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
    for k, obj in ctx.items():
        v.rootContext().setContextProperty(k, obj)
    v.setSource(QUrl.fromLocalFile(str(BASE_DIR / "qml" / qml)))
    if v.status() != QQuickWidget.Status.Ready:
        print(f"[ATLAS] ⚠️ {qml}: {[e.toString() for e in v.errors()]}")
        v.deleteLater()
        return None
    return v


def mouth_shapes(pcm: bytes, rate: int, peak: float = 3000.0):
    """Mouth shapes for each 20 ms of speech (int16 mono), as a flat list [level, jaw, round, wide, ...],
    and the updated loudness peak (keep it between calls: it adapts to each voice's volume).
    From the spectrum: o/u put almost all energy below 900 Hz (round), a adds some 900-2500 Hz (open),
    e/i more of it (wide); s/f/ch/j are mostly above 3.5 kHz (teeth near, lips spread); quiet = closed."""
    import numpy as np
    a = np.frombuffer(pcm, np.int16).astype(np.float32)
    n = max(32, rate // 50)
    k = len(a) // n
    if not k:
        return [], peak
    f = a[:k * n].reshape(k, n)
    rms = np.sqrt((f ** 2).mean(1))
    spec = np.abs(np.fft.rfft(f * np.hanning(n), axis=1)) ** 2
    hz = np.fft.rfftfreq(n, 1 / rate)
    band = lambda lo, hi: spec[:, (hz >= lo) & (hz < hi)].sum(1)
    low, mid, high = band(150, 900), band(900, 2500), band(3500, 9000)
    out = []
    for i in range(k):
        peak = max(rms[i], peak * 0.997, 1500.0)
        lvl = min(1.0, rms[i] / (0.7 * peak))
        tot = low[i] + mid[i] + high[i] + 1e-6
        fric = high[i] / tot
        r = mid[i] / (low[i] + 1e-6)
        rnd = 1 - min(1.0, max(0.0, (r - 0.03) / 0.12))           # o, u
        wide = min(1.0, max(0.0, (r - 0.25) / 0.35))              # e, i
        voiced = 1 - fric
        on = min(1.0, lvl * 3)
        jaw = voiced * lvl ** 0.8 * (1 - 0.35 * rnd - 0.45 * wide) + fric * 0.12 * on
        out += [round(float(x), 3) for x in (lvl, jaw, voiced * rnd * on, voiced * wide * on + fric * 0.5 * on)]
    return out, peak


def _argb(h: str, a: float) -> str:
    """'#rrggbb' with alpha 0..255 as Qt Quick's '#aarrggbb'."""
    return f"#{max(0, min(255, int(a))):02x}{h[1:]}"


# effort ids from config.PROVIDERS -> names shown in the picker
_EFFORTS = {"minimal": "Mínimo", "low": "Bajo", "medium": "Medio", "high": "Alto", "xhigh": "Muy alto", "max": "Máximo"}


# engine state -> status title
_STATE_ES = {"LISTENING": "ESCUCHANDO", "THINKING": "PENSANDO", "PROCESSING": "PROCESANDO",
             "INITIALISING": "INICIANDO", "STANDBY": "EN ESPERA", "SPEAKING": "HABLANDO"}


_DAYS = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
_MONTHS = ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")


class HudBridge(QObject):
    """What qml/hud.qml shows, as one map: HudCanvas pushes it every frame. toast: a short notice."""
    changed = pyqtSignal()
    toast   = pyqtSignal(str, str)   # kind (start | done), text
    voice   = pyqtSignal("QVariantList", float)   # mouth_shapes() of a chunk, seconds until it is heard

    def __init__(self):
        super().__init__()
        self._s: dict = {}

    @pyqtProperty("QVariantMap", notify=changed)
    def s(self) -> dict:
        return self._s

    def push(self, s: dict):
        if s != self._s:
            self._s = s
            self.changed.emit()


class HudCanvas(QWidget):
    """ATLAS core surrounded by rotating HUD rings. The core, sparks, status and voice wave are
    Qt Quick (qml/hud.qml) layered on top when it loads; else this widget paints them itself."""

    def __init__(self, face_path: str = "", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.mic_off  = False
        self.speaking = False
        self.state    = "INITIALISING"

        self._tick       = 0
        self._boot       = 0.0      # 0 -> 1 power-up animation on launch
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._scan       = 0.0
        self._rings      = [0.0, 120.0, 240.0]
        self._pulses: list[float] = [0.0, 60.0]
        self._wave       = [0.0] * 40
        self._blink      = True
        self._blink_tick = 0
        self._particles: list[list[float]] = []
        self._bg: QPixmap | None = None
        self._task: str | None = None    # TASK_MODES value while a tool runs
        self._task_tool  = ""
        self._task_t0    = 0.0
        self._task_end: float | None = None
        self._task_n     = 0
        self._task_a     = 0.0              # overlay fade 0..1
        self._spec       = [0.0] * 48       # media spectrum bars
        self._jobs: dict[str, list] = {}    # background job id -> [goal, step, started]
        self._err_until  = 0.0              # core shows the error state (amber) until then

        self._bridge = HudBridge()
        self._static_key, self._static_file, self._static_n = None, None, 0
        self._push()
        # Opaque, not an overlay: a transparent layer makes Qt repaint this widget under it every
        # time QML draws. The static background is handed to QML as an image instead (_static_layer).
        ctx = {"hud": self._bridge}
        try:   # Head3D.qml's meshes (made here: registering the type for QML crashed PyQt6 6.11)
            import head3d
            self._face = head3d.geometries()
            ctx.update(self._face)
        except Exception as e:   # no Qt Quick 3D: the HUD keeps the neural net alone
            print(f"[ATLAS] ⚠️ 3D head unavailable: {e}")
        self._qml = _quick(self, "hud.qml", ctx)
        if self._qml:
            self._qml.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self._qml.setClearColor(QColor(C.BG))

        self._t_prev = time.perf_counter()
        self._compass: tuple | None = None   # (key, pixmap): the static compass, drawn once
        self._tmr = QTimer(self)
        self._tmr.setTimerType(Qt.TimerType.PreciseTimer)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def _layer_on(self) -> bool:
        return bool(self._qml) and not self._qml.isHidden()

    def show_layer(self, on: bool):
        """The Qt Quick layer always paints on top of sibling widgets: hidden while the settings panel
        is open, the painted core stands in under it."""
        if self._qml:
            self._qml.setVisible(on)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._qml:
            self._qml.setGeometry(self.rect())

    @property
    def muted(self) -> bool:
        """Red SILENCIADO look: only while idle, so speaking, thinking and tasks still animate with the mic off."""
        return (self.mic_off and not self.speaking and self._task is None
                and self.state not in ("THINKING", "PROCESSING"))

    @muted.setter
    def muted(self, on: bool):
        self.mic_off = on

    def flash_error(self, secs: float = 3.0):
        """Core and status turn amber for a few seconds (an ERR line reached the log)."""
        self._err_until = time.time() + secs

    def _error(self) -> bool:
        return time.time() < self._err_until

    def _tint(self) -> tuple[str, str]:
        """Core light and core edge colours for the current state."""
        if self.muted:
            return C.RED, C.RED_D
        return (C.AMBER, C.AMBER) if self._error() else (C.ARC, C.ACC)

    def _status(self) -> tuple[str, str]:
        """Status line text and colour."""
        if self.muted:
            return "⊘  SILENCIADO", C.RED
        if self._error():
            return "⚠  ERROR", C.AMBER
        if self.speaking:
            return "●  HABLANDO", C.ARC
        if self._task and self._task_a > 0.5:
            title, tc = TASK_LOOK[self._task]
            return f"{'◢' if self._blink else '◣'}  {title}", tc
        sym = {"THINKING": "◈◇", "PROCESSING": "▷▶"}.get(self.state, "●○")[0 if self._blink else 1]
        return f"{sym}  {_STATE_ES.get(self.state, self.state)}", C.GREEN if self.state == "LISTENING" else C.PRI

    def _push(self):
        glow = min(1.0, self._halo / 190)
        arc, edge = self._tint()
        txt, col = self._status()
        state = ("MUTED" if self.muted else "ERROR" if self._error() else "SPEAKING" if self.speaking
                 else self.state)
        self._bridge.push({   # only what changes with state: hud.qml animates the rest itself
            "boot": round(self._boot, 3), "speaking": self.speaking,
            "muted": self.muted, "state": state, "font": _FONT, "barBg": C.BAR_BG, "textDim": C.TEXT_DIM,
            "ring": C.RED_D if self.muted else C.ACC,
            "status": txt, "statusColor": col, "display": _DISPLAY,
            "arc": arc, "arcFade": _argb(arc, 105), "pri": C.RED if self.muted else C.PRI, "acc": edge,
            "core": C.CORE, "version": VERSION,
            "bgUrl": QUrl.fromLocalFile(str(self._static_file)).toString() if self._static_file else "", "steel": C.STEEL, "steelD": C.STEEL_D,
            "coil": C.COPPER, "coilD": C.COPPER_D, "dark": C.THEME == "dark",
            "panel": C.PANEL, "text": C.TEXT, "green": C.GREEN, "font": _FONT,
            # tool overlay and background jobs, animated in hud.qml
            "taskMode": self._task or "", "taskTool": self._task_tool, "taskT0": self._task_t0,
            "taskOn": self._task is not None and (self._task_end is None or time.time() < self._task_end),
            "taskTitle": TASK_LOOK[self._task][0] if self._task else "",
            "taskColor": TASK_LOOK[self._task][1] if self._task else C.ARC,
            "jobs": [list(j) for j in self._jobs.values()], "jobColor": C.ACC2, "codeRing": _CODE_RING,
        })

    def set_task(self, tool: str):
        """Tool name starts its animation; "" ends one. Shown at least 1.5 s so quick tools still register."""
        now = time.time()
        if tool:
            self._task_n += 1
            if self._task is None or self._task_end is not None:
                self._task_t0 = now
            self._task, self._task_tool, self._task_end = TASK_MODES.get(tool, "system"), tool, None
        elif self._task_n:
            self._task_n -= 1
            if not self._task_n:
                self._task_end = max(now, self._task_t0 + 1.5)

    def set_job(self, key: str, goal: str, step: str):
        """Background job (agent_task, a tool still running past its timeout): goal "" removes it."""
        if not goal:
            done = self._jobs.pop(key, None)
            if done:
                self._bridge.toast.emit("done", f"Terminado: {done[0]}")
        else:
            if key not in self._jobs:
                self._bridge.toast.emit("start", f"Trabajando en segundo plano: {goal}")
            self._jobs[key] = [goal, step, self._jobs.get(key, [0, 0, time.time()])[2]]

    def _step(self):
        # f: this tick in 60 fps frames. Timer ticks are uneven (15..31 ms on Windows), so every
        # increment and every easing is scaled by it, else the motion stutters.
        t = time.perf_counter()
        f = min(8.0, (t - self._t_prev) * 60)
        self._t_prev = t
        ease = lambda k: 1 - (1 - k) ** f
        self._tick += f
        on = self._task is not None and (self._task_end is None or time.time() < self._task_end)
        self._task_a += ((1.0 if on else 0.0) - self._task_a) * ease(0.12)
        if not on and self._task_a < 0.02:
            self._task = None
        if self._task == "media":
            for i in range(len(self._spec)):
                self._spec[i] += (random.uniform(0.1, 1.0) * (0.6 + 0.4 * math.sin(self._tick * 0.05 + i * 0.4))
                                  - self._spec[i]) * ease(0.3)
        self._boot = min(1.0, self._boot + 0.008 * f)
        self._blink_tick += f
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
        layer = self._layer_on()
        # With hud.qml up, Python only feeds it state changes: each tick of this timer runs on the GUI
        # thread and must win the GIL from the audio threads first, which stalled QML's frames.
        self._tmr.setInterval(100 if layer else 16)
        if layer:
            self._static_layer()
            self._push()
            return
        now = time.time()
        if now - self._last_t > (0.12 if self.speaking else 0.5):
            if self.speaking:
                self._tgt_scale = random.uniform(1.03, 1.07)
                self._tgt_halo  = random.uniform(145, 190)
            elif self.muted:
                self._tgt_scale = 1.0
                self._tgt_halo  = random.uniform(15, 28)
            else:
                self._tgt_scale = random.uniform(1.0, 1.01)
                self._tgt_halo  = random.uniform(48, 68)
            self._last_t = now

        sp = ease(0.38 if self.speaking else 0.15)
        self._scale += (self._tgt_scale - self._scale) * sp
        self._halo  += (self._tgt_halo  - self._halo)  * sp

        speeds = [1.1, -0.7, 0.35] if self.speaking else [0.4, -0.25, 0.12]
        for i, spd in enumerate(speeds):
            self._rings[i] = (self._rings[i] + spd * f) % 360
        self._scan = (self._scan - (3.0 if self.speaking else 1.2) * f) % 360

        fw  = min(self.width(), self.height())
        lim = fw * 0.5
        spd = (3.6 if self.speaking else 1.6) * f
        self._pulses = [r + spd for r in self._pulses if r + spd < lim]
        if len(self._pulses) < 3 and random.random() < (0.06 if self.speaking else 0.02) * f:
            self._pulses.append(fw * 0.12)

        for i in range(len(self._wave)):
            if self.muted:
                tgt = 0.05
            elif self.speaking:
                tgt = random.uniform(0.15, 1.0) * (1 - abs(i - 19.5) / 26)
            else:
                tgt = 0.12 + 0.08 * math.sin(self._tick * 0.08 + i * 0.5)
            self._wave[i] += (tgt - self._wave[i]) * ease(0.35)

        if self.speaking and random.random() < 0.3:
            cx, cy = self.width() / 2, self.height() / 2
            ang = random.uniform(0, 2 * math.pi)
            r_s = fw * 0.2
            self._particles.append([
                cx + math.cos(ang) * r_s, cy + math.sin(ang) * r_s,
                math.cos(ang) * random.uniform(0.9, 2.4),
                math.sin(ang) * random.uniform(0.9, 2.4), 1.0,
            ])
        self._particles = [
            [p[0]+p[2], p[1]+p[3], p[2]*0.97, p[3]*0.97, p[4]-0.025]
            for p in self._particles if p[4] > 0
        ]

        self.update()

    def _background(self) -> QPixmap:
        # Static layer, rebuilt only on resize: glow, hex grid, vignette.
        W, H = self.width(), self.height()
        px = QPixmap(W, H)
        px.fill(qcol(C.BG))
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QRadialGradient(W / 2, H / 2, max(W, H) * 0.6)
        g.setColorAt(0, qcol(C.ARC_D, 60)); g.setColorAt(1, qcol(C.BG, 0))
        p.fillRect(px.rect(), QBrush(g))

        s, h = 20.0, 20.0 * math.sqrt(3)
        hexagon = [QPointF(s * math.cos(math.radians(60 * k)), s * math.sin(math.radians(60 * k)))
                   for k in range(6)]
        p.setPen(QPen(qcol(C.GRID), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
        col, x = 0, 0.0
        while x < W + s:
            y = h / 2 if col % 2 else 0.0
            while y < H + h:
                p.drawPolygon(QPolygonF([QPointF(x + q.x(), y + q.y()) for q in hexagon]))
                y += h
            x += s * 1.5; col += 1

        v = QRadialGradient(W / 2, H / 2, max(W, H) * 0.72)
        v.setColorAt(0.5, qcol(C.BG, 0)); v.setColorAt(1, qcol(C.BG, 210))
        p.fillRect(px.rect(), QBrush(v))
        p.end()
        return px

    def _static_layer(self):
        """Background, compass, crosshair and title as one image for hud.qml; redone only when the
        size, theme or muted colour changes."""
        W, H = self.width(), self.height()
        gold = C.RED if self.muted else C.PRI
        key = (W, H, C.THEME, gold)
        if key == self._static_key or W < 50 or H < 50:
            return
        self._static_key = key
        px = self._background()
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fw, cx, cy = min(W, H * 0.86), W / 2, H / 2 - H * 0.05
        self._draw_compass(p, cx, cy, fw, fw * 0.47, gold)
        p.setPen(QPen(qcol(gold, 160), 1.5))
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):   # crosshair ticks
            p.drawLine(QPointF(cx + dx * fw * 0.30, cy + dy * fw * 0.30), QPointF(cx + dx * fw * 0.27, cy + dy * fw * 0.27))
        p.end()
        import tempfile
        old = self._static_file
        self._static_n += 1
        self._static_file = Path(tempfile.gettempdir()) / f"atlas-hud-{os.getpid()}-{self._static_n}.png"
        px.save(str(self._static_file))
        self._qml.setClearColor(QColor(C.BG))
        if old:
            old.unlink(missing_ok=True)

    def paintEvent(self, _):
        if self._layer_on():   # hud.qml covers this widget and draws everything
            return
        if self._bg is None or self._bg.size() != self.size():
            self._bg, self._compass = self._background(), None
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        booted = self._compass is not None and self._compass[0][:2] == (self.width(), self.height())
        p.drawPixmap(0, 0, self._compass[1] if booted else self._bg)

        W, H = self.width(), self.height()
        fw = min(W, H * 0.86)
        cx, cy = W / 2, H / 2 - H * 0.05
        b    = 1 - (1 - self._boot) ** 3                 # ease-out power-up
        glow = min(1.0, self._halo / 190)
        arc  = self._tint()[0]                           # core light
        gold = C.RED  if self.muted else C.PRI           # HUD rings
        red  = C.RED_D if self.muted else C.ACC
        p.setOpacity(b)
        k = 0.7 + 0.3 * b                                # rings expand while booting

        quick = self._layer_on()   # glow, pulses and sweep are drawn by qml/hud.qml then

        # ambient glow
        gr = fw * 0.42
        g = QRadialGradient(cx, cy, gr)
        g.setColorAt(0.0, qcol(arc, int(60 + 120 * glow)))
        g.setColorAt(0.4, qcol(arc, int(15 + 45 * glow)))
        g.setColorAt(1.0, qcol(arc, 0))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(g))
        if not quick:
            p.drawEllipse(QPointF(cx, cy), gr, gr)

        # shock-wave pulses
        p.setBrush(Qt.BrushStyle.NoBrush)
        for pr in ([] if quick else self._pulses):
            a = max(0, int(150 * (1.0 - pr / (fw * 0.5))))
            p.setPen(QPen(qcol(arc, a), 1.2))
            p.drawEllipse(QPointF(cx, cy), pr, pr)

        # outer compass: ticks every 5 deg, bearings every 30 deg. Static once booted: drawn once.
        ro = fw * 0.47 * k
        if b < 1:
            self._draw_compass(p, cx, cy, fw, ro, gold)
        else:
            key = (W, H, gold)
            if not self._compass or self._compass[0] != key:   # baked onto the background: one blit a frame
                px = self._bg.copy()
                cp = QPainter(px)
                cp.setRenderHint(QPainter.RenderHint.Antialiasing)
                self._draw_compass(cp, cx, cy, fw, ro, gold)
                cp.end()
                self._compass = (key, px)
        if quick:   # rings, marker, readouts, tool overlays and jobs are hud.qml's
            p.setPen(QPen(qcol(gold, 160), 1.5))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):   # crosshair ticks
                p.drawLine(QPointF(cx + dx * fw * 0.30 * k, cy + dy * fw * 0.30 * k),
                           QPointF(cx + dx * fw * 0.27 * k, cy + dy * fw * 0.27 * k))
            p.setOpacity(1.0)
            self._paint_title(p)
            return

        # bearing marker sweeping the compass
        rad = math.radians(self._scan)
        tip = QPointF(cx + (ro - fw * 0.035) * math.cos(rad), cy - (ro - fw * 0.035) * math.sin(rad))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(qcol(gold)))
        p.drawPolygon(QPolygonF([
            tip,
            QPointF(cx + ro * math.cos(rad + 0.03), cy - ro * math.sin(rad + 0.03)),
            QPointF(cx + ro * math.cos(rad - 0.03), cy - ro * math.sin(rad - 0.03)),
        ]))

        # radar sweep
        if not quick:
            rs = fw * 0.40 * k
            cg = QConicalGradient(cx, cy, self._scan)
            cg.setColorAt(0.0, qcol(arc, int(70 + 60 * glow)))
            cg.setColorAt(0.10, qcol(arc, 0))
            cg.setColorAt(1.0, qcol(arc, 0))
            p.setBrush(QBrush(cg))
            p.drawEllipse(QPointF(cx, cy), rs, rs)
        p.setBrush(Qt.BrushStyle.NoBrush)

        def arcs(r, width, color, base, length, gap, cap=Qt.PenCapStyle.FlatCap):
            pen = QPen(color, width); pen.setCapStyle(cap); p.setPen(pen)
            rect = QRectF(cx - r, cy - r, r * 2, r * 2)
            a = base
            while a < base + 360:
                p.drawArc(rect, int(a * 16), int(length * 16))
                a += length + gap

        # red armour ring, gold dashed ring, thin cyan inner ring
        arcs(fw * 0.415 * k, fw * 0.02, qcol(red, int(140 + 100 * glow)), self._rings[0], 44, 16)
        arcs(fw * 0.375 * k, 1.5, qcol(gold, 170), self._rings[1], 8, 4)
        arcs(fw * 0.355 * k, 3, qcol(gold, 230), self._rings[1] + 30, 60, 60, Qt.PenCapStyle.RoundCap)
        p.setPen(QPen(qcol(arc, 90), 1))
        p.drawEllipse(QPointF(cx, cy), fw * 0.33 * k, fw * 0.33 * k)

        # crosshair ticks
        p.setPen(QPen(qcol(gold, 160), 1.5))
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            p.drawLine(QPointF(cx + dx * fw * 0.30 * k, cy + dy * fw * 0.30 * k),
                       QPointF(cx + dx * fw * 0.27 * k, cy + dy * fw * 0.27 * k))

        if self._task:
            self._draw_task(p, cx, cy, fw * k)

        if not self._layer_on():
            self._paint_core(p, cx, cy, fw, k, glow, arc, gold)

        if self._jobs:
            self._draw_jobs(p, cx, cy, fw * k)

        # corner readouts
        p.setOpacity(1.0)
        pad = 14
        self._paint_title(p)
        out = int(b * (40 + 60 * glow)) if not self.muted else 12
        p.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(arc), 1))
        p.drawText(QRectF(W - 200 - pad, pad, 200, 14), Qt.AlignmentFlag.AlignRight,
                   f"POTENCIA  {out:3d}%")
        bw = 120
        p.fillRect(QRectF(W - pad - bw, pad + 18, bw, 4), qcol(C.BAR_BG))
        p.fillRect(QRectF(W - pad - bw, pad + 18, bw * out / 100, 4), qcol(arc))
        p.setFont(QFont(_FONT, 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(W - 200 - pad, pad + 26, 200, 14), Qt.AlignmentFlag.AlignRight,
                   "INICIANDO ATLAS…" if b < 1 else "SISTEMAS EN LÍNEA")

        if not self._layer_on():
            self._paint_status(p, W, H, cx, cy, fw, arc)

    @staticmethod
    def _paint_title(p: QPainter):
        p.setFont(QFont(_DISPLAY, 10, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(14, 14, 260, 18), Qt.AlignmentFlag.AlignLeft, "ATLAS")
        p.setFont(QFont(_FONT, 8))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(14, 32, 260, 14), Qt.AlignmentFlag.AlignLeft, f"MOTOR NEURONAL  //  v{VERSION}")

    @staticmethod
    def _draw_compass(p: QPainter, cx, cy, fw, ro, gold):
        p.setFont(QFont(_FONT, max(6, int(fw * 0.016))))
        for deg in range(0, 360, 5):
            rad = math.radians(deg)
            ln  = fw * (0.028 if deg % 30 == 0 else 0.012)
            p.setPen(QPen(qcol(gold, 200 if deg % 30 == 0 else 90), 1.2))
            c_, s_ = math.cos(rad), -math.sin(rad)
            p.drawLine(QPointF(cx + ro * c_, cy + ro * s_),
                       QPointF(cx + (ro - ln) * c_, cy + (ro - ln) * s_))
            if deg % 30 == 0:
                rt = ro + fw * 0.025
                p.drawText(QRectF(cx + rt * c_ - 16, cy + rt * s_ - 8, 32, 16),
                           Qt.AlignmentFlag.AlignCenter, f"{(90 - deg) % 360:03d}")

    def _paint_core(self, p: QPainter, cx, cy, fw, k, glow, arc, gold):
        # ── arc reactor ──
        R = fw * 0.24 * self._scale * k
        housing = QRadialGradient(cx, cy - R * 0.3, R * 1.2)
        housing.setColorAt(0, qcol(C.STEEL)); housing.setColorAt(1, qcol(C.STEEL_D))
        p.setBrush(QBrush(housing)); p.setPen(QPen(qcol(gold, 150), 1.5))
        p.drawEllipse(QPointF(cx, cy), R, R)

        r1, r2 = R * 0.56, R * 0.88
        # light leaking between the coils
        ring = QRadialGradient(cx, cy, r2)
        ring.setColorAt(r1 / r2, qcol(arc, int(120 + 135 * glow)))
        ring.setColorAt(1.0, qcol(arc, int(40 + 60 * glow)))
        p.setBrush(QBrush(ring)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), r2, r2)

        # ten copper coils
        for n in range(10):
            a0 = n * 36 + 5 + self._rings[2]
            path = QPainterPath()
            path.arcMoveTo(QRectF(cx - r2, cy - r2, r2 * 2, r2 * 2), a0)
            path.arcTo(QRectF(cx - r2, cy - r2, r2 * 2, r2 * 2), a0, 26)
            path.arcTo(QRectF(cx - r1, cy - r1, r1 * 2, r1 * 2), a0 + 26, -26)
            path.closeSubpath()
            p.setBrush(QBrush(qcol(C.COPPER_D))); p.setPen(QPen(qcol(C.COPPER), 1))
            p.drawPath(path)
            p.setPen(QPen(qcol(C.COPPER, 200), 1))
            for w in range(1, 6):                          # wire windings
                a = math.radians(a0 + 26 * w / 6)
                p.drawLine(QPointF(cx + r1 * math.cos(a), cy - r1 * math.sin(a)),
                           QPointF(cx + r2 * math.cos(a), cy - r2 * math.sin(a)))

        # hex core (as in qml/hud.qml, without the motion): energy fill, electric edge, facets, orb, nodes
        h, edge = R * 0.5, qcol(self._tint()[1])
        hexagon = lambda rr, off=-90: QPolygonF([QPointF(cx + rr * math.cos(math.radians(off + 60 * i)),
                                                         cy + rr * math.sin(math.radians(off + 60 * i)))
                                                 for i in range(6)])
        p.setPen(QPen(qcol(arc, 180), 1.2))
        for v, w in zip(hexagon(h), hexagon(r1 - 2)):          # traces out to the segment ring
            p.drawLine(v, w)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcol(edge.name(), 150), 1))
        p.drawPolygon(hexagon(h * 1.1, -60))
        core = QRadialGradient(cx, cy, h)
        core.setColorAt(0.0, qcol(C.CORE, 255))
        core.setColorAt(0.4 + 0.25 * glow, qcol(arc, 230))
        core.setColorAt(1.0, qcol(arc, 40))
        pen = QPen(edge, max(2.0, R * 0.035)); pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        p.setBrush(QBrush(core)); p.setPen(pen)
        p.drawPolygon(hexagon(h))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcol(C.CORE, 115), 1))
        p.drawPolygon(hexagon(h * 0.68))
        p.setPen(QPen(qcol(C.CORE, 75), 1))
        p.drawPolygon(hexagon(h * 0.42, -60))
        rr = h * (0.26 + 0.1 * glow)                           # the orb
        orb = QRadialGradient(cx, cy, rr)
        orb.setColorAt(0.0, qcol(C.CORE, 255)); orb.setColorAt(0.45, qcol(C.CORE, 180)); orb.setColorAt(1.0, qcol(arc, 0))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(orb))
        p.drawEllipse(QPointF(cx, cy), rr, rr)
        p.setPen(QPen(edge, 1)); p.setBrush(QBrush(qcol(C.CORE)))
        for v in hexagon(h):
            p.drawEllipse(v, R * 0.022, R * 0.022)

        # sparks
        p.setPen(Qt.PenStyle.NoPen)
        for pt in self._particles:
            p.setBrush(QBrush(qcol(arc, max(0, min(255, int(pt[4] * 255))))))
            p.drawEllipse(QPointF(pt[0], pt[1]), 2.2, 2.2)

    def _paint_status(self, p: QPainter, W, H, cx, cy, fw, arc):
        sy = min(H - 58.0, cy + fw * 0.5)
        txt, col = self._status()
        col = qcol(col)

        f = QFont(_DISPLAY, 10, QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 130)
        p.setFont(f); p.setPen(QPen(col, 1))
        p.drawText(QRectF(0, sy, W, 22), Qt.AlignmentFlag.AlignCenter, txt)
        tw = 150
        p.setPen(QPen(qcol(C.ACC, 180), 1.5))
        p.drawLine(QPointF(cx - tw, sy + 11), QPointF(cx - tw + 40, sy + 11))
        p.drawLine(QPointF(cx + tw - 40, sy + 11), QPointF(cx + tw, sy + 11))

        # mirrored voice waveform
        wy, N, bwid = sy + 40, len(self._wave), 6
        wx0 = (W - N * bwid) / 2
        for i, v in enumerate(self._wave):
            hgt = max(1.0, v * 16)
            p.fillRect(QRectF(wx0 + i * bwid, wy - hgt, bwid - 2, hgt * 2),
                       qcol(arc, int(110 + 145 * v)))


    # ── background jobs: a drone per job orbiting the reactor + a numbered ops list, top left ──
    def _draw_jobs(self, p: QPainter, cx: float, cy: float, fw: float):
        t, now, col = self._tick, time.time(), C.ACC2
        jobs = list(self._jobs.values())
        rr = fw * 0.46
        orbit = QRectF(cx - rr, cy - rr, rr * 2, rr * 2)
        p.setFont(QFont(_MONO, 7, QFont.Weight.Bold))
        for i in range(len(jobs)):
            deg = (t * 0.5 + i * 360 / len(jobs)) % 360
            p.setPen(QPen(qcol(col, 110), 2)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(orbit, int(deg * 16), -28 * 16)                       # trail
            a = math.radians(deg)
            x, y = cx + rr * math.cos(a), cy - rr * math.sin(a)
            p.setPen(QPen(qcol(C.WHITE, 230), 1)); p.setBrush(QBrush(qcol(col, 230)))
            p.drawPolygon(QPolygonF([QPointF(x, y - 5), QPointF(x + 5, y), QPointF(x, y + 5), QPointF(x - 5, y)]))
            p.setPen(QPen(qcol(col), 1))
            p.drawText(QPointF(x + 8, y - 6), f"{i + 1:02d}")

        x0, y0, w, rh = 14.0, 52.0, 220.0, 34.0
        rows = max(1, min(len(jobs), int((self.height() * 0.6 - y0 - 18) // (rh + 4))))
        hdr = QRectF(x0, y0, w, 16)
        p.setFont(QFont(_FONT, 8, QFont.Weight.Bold)); p.setPen(QPen(qcol(col), 1))
        p.drawText(hdr, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{'◢' if self._blink else '◣'} OPERACIONES [{len(jobs)}]")
        for i in range(8):                                                  # marching dashes
            lit = (i - t // 4) % 8 < 3
            p.fillRect(QRectF(hdr.right() - (8 - i) * 7, hdr.top() + 6, 5, 3), qcol(col, 230 if lit else 50))
        for i, (goal, step, t0) in enumerate(jobs[:rows]):
            r = QRectF(x0, y0 + 20 + i * (rh + 4), w, rh)
            p.setPen(QPen(qcol(col, 150), 1)); p.setBrush(QBrush(qcol(C.PANEL, 170)))
            p.drawPolygon(_chamfer(r, 6))
            spin = QRectF(r.left() + 7, r.top() + 8, 18, 18)                # spinner, own phase per job
            p.setPen(QPen(qcol(col, 60), 2)); p.drawEllipse(spin)
            pen = QPen(qcol(col), 2); pen.setCapStyle(Qt.PenCapStyle.RoundCap); p.setPen(pen)
            p.drawArc(spin, int(-(t * 7 + i * 120) * 16), 100 * 16)
            p.setFont(QFont(_MONO, 6, QFont.Weight.Bold)); p.setPen(QPen(qcol(C.WHITE, 220), 1))
            p.drawText(spin, Qt.AlignmentFlag.AlignCenter, f"{i + 1:02d}")
            tx, tw = r.left() + 32, w - 40
            p.setFont(QFont(_FONT, 8, QFont.Weight.Bold)); p.setPen(QPen(qcol(C.TEXT), 1))
            el = f"T+{now - t0:4.0f}s"
            p.drawText(QRectF(tx, r.top() + 3, tw, 14), Qt.AlignmentFlag.AlignRight, el)
            p.drawText(QRectF(tx, r.top() + 3, tw - 50, 14), Qt.AlignmentFlag.AlignLeft,
                       p.fontMetrics().elidedText(goal, Qt.TextElideMode.ElideRight, int(tw - 50)))
            p.setFont(QFont(_MONO, 7)); p.setPen(QPen(qcol(col, 220), 1))
            p.drawText(QRectF(tx, r.top() + 17, tw, 12), Qt.AlignmentFlag.AlignLeft,
                       p.fontMetrics().elidedText(step, Qt.TextElideMode.ElideRight, int(tw)))
            sw = tw * 0.25                                                  # scanning bar
            sx = tx + ((t * 2 + i * 40) % (tw + sw)) - sw
            p.fillRect(QRectF(tx, r.bottom() - 3, tw, 1.5), qcol(col, 40))
            p.fillRect(QRectF(max(tx, sx), r.bottom() - 3, min(sw, tx + tw - max(tx, sx)), 1.5), qcol(col, 230))
        if len(jobs) > rows:
            p.setFont(QFont(_MONO, 7)); p.setPen(QPen(qcol(C.TEXT_DIM), 1))
            p.drawText(QPointF(x0, y0 + 20 + rows * (rh + 4) + 10), f"+{len(jobs) - rows} MORE")

    # ── task overlays: each drawn around the reactor, under it ──
    def _draw_task(self, p: QPainter, cx: float, cy: float, fw: float):
        a = self._task_a
        title, tc = TASK_LOOK[self._task]
        A = lambda v: max(0, min(255, int(v * a)))
        t = self._tick
        p.save()
        getattr(self, f"_fx_{self._task}")(p, cx, cy, fw, t, tc, A)
        p.restore()

        # holographic task tag, top centre
        bw, bh = 250, 42
        r = QRectF(cx - bw / 2, 14, bw, bh)
        p.setPen(QPen(qcol(tc, A(200)), 1.2)); p.setBrush(QBrush(qcol(C.PANEL, A(200))))
        p.drawPolygon(_chamfer(r, 9))
        p.setFont(QFont(_FONT, 9, QFont.Weight.Bold)); p.setPen(QPen(qcol(tc, A(255)), 1))
        p.drawText(r.adjusted(12, 4, -12, -20), Qt.AlignmentFlag.AlignLeft, f"◢ {title}")
        p.setFont(QFont(_MONO, 7)); p.setPen(QPen(qcol(C.TEXT, A(200)), 1))
        p.drawText(r.adjusted(12, 21, -12, -4), Qt.AlignmentFlag.AlignLeft,
                   f"{self._task_tool.upper()}   T+{time.time() - self._task_t0:05.1f}s")
        for i in range(10):                                   # marching activity dashes
            lit = (i - t // 4) % 10 < 3
            p.fillRect(QRectF(r.right() - 14 - (10 - i) * 7, r.top() + 10, 5, 3), qcol(tc, A(230 if lit else 50)))

    def _fx_media(self, p, cx, cy, fw, t, tc, A):
        # radial spectrum analyser + notes drifting outward
        n, r0 = len(self._spec), fw * 0.255
        for i, v in enumerate(self._spec):
            ang = math.radians(i * 360 / n + self._rings[2])
            ln = fw * (0.01 + 0.065 * v)
            c_, s_ = math.cos(ang), -math.sin(ang)
            pen = QPen(qcol(C.ARC if v < 0.65 else C.PRI, A(120 + 135 * v)), max(2.0, fw * 0.012))
            pen.setCapStyle(Qt.PenCapStyle.FlatCap); p.setPen(pen)
            p.drawLine(QPointF(cx + r0 * c_, cy + r0 * s_), QPointF(cx + (r0 + ln) * c_, cy + (r0 + ln) * s_))
        p.setFont(QFont(_FONT, max(9, int(fw * 0.03))))
        for k in range(6):
            ph = (t * 0.006 + k / 6) % 1
            ang = math.radians(k * 60 + 25 * math.sin(t * 0.02 + k))
            rr = fw * (0.34 + 0.16 * ph)
            p.setPen(QPen(qcol(C.PRI, A(255 * (1 - ph))), 1))
            p.drawText(QPointF(cx + rr * math.cos(ang), cy - rr * math.sin(ang)), "\u266a" if k % 2 else "\u266b")

    def _fx_code(self, p, cx, cy, fw, t, tc, A):
        # code typed around a rotating ring, cursor at the head, brackets pulsing
        rr, m = fw * 0.295, 78
        n = int((time.time() - self._task_t0) * 28) % len(_CODE_RING) + 1
        shown = _CODE_RING[:n][-m:]
        p.setFont(QFont(_MONO, max(7, int(fw * 0.016)), QFont.Weight.Bold))
        step, base = 360 / m, -self._rings[1] * 1.5
        for i, ch in enumerate(shown):
            p.save(); p.translate(cx, cy); p.rotate(base + i * step)
            p.setPen(QPen(qcol(tc, A(90 + 165 * i / m)), 1))
            p.drawText(QPointF(-3, -rr), ch)
            p.restore()
        if self._blink:
            p.save(); p.translate(cx, cy); p.rotate(base + len(shown) * step)
            p.fillRect(QRectF(-3, -rr - fw * 0.016, fw * 0.011, fw * 0.02), qcol(tc, A(255)))
            p.restore()
        pulse = 0.5 + 0.5 * math.sin(t * 0.12)
        p.setFont(QFont(_MONO, max(12, int(fw * 0.05)), QFont.Weight.Bold))
        p.setPen(QPen(qcol(tc, A(120 + 120 * pulse)), 1))
        d = fw * (0.36 + 0.01 * pulse)
        p.drawText(QRectF(cx - d - 40, cy - 30, 40, 60), Qt.AlignmentFlag.AlignCenter, "{")
        p.drawText(QRectF(cx + d, cy - 30, 40, 60), Qt.AlignmentFlag.AlignCenter, "}")

    def _fx_net(self, p, cx, cy, fw, t, tc, A):
        # wireframe globe behind the reactor + data packets between compass nodes
        r = fw * 0.31
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcol(tc, A(110)), 1))
        for k in range(6):
            w = r * abs(math.cos(math.radians((t * 0.8 + k * 30) % 180)))
            p.drawEllipse(QPointF(cx, cy), w, r)
        for lat in (-60, -30, 0, 30, 60):
            w = r * math.cos(math.radians(lat))
            p.drawEllipse(QPointF(cx, cy - r * math.sin(math.radians(lat))), w, w * 0.22)
        nodes = [QPointF(cx + fw * 0.45 * math.cos(math.radians(d)), cy - fw * 0.45 * math.sin(math.radians(d)))
                 for d in (20, 75, 140, 200, 255, 320)]
        for i, a_ in enumerate(nodes):
            path = QPainterPath(a_); path.quadTo(QPointF(cx, cy), nodes[(i + 2) % len(nodes)])
            p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(QPen(qcol(tc, A(70)), 1)); p.drawPath(path)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.WHITE, A(230))))
            p.drawEllipse(path.pointAtPercent((t * 0.012 + i / len(nodes)) % 1), 2.6, 2.6)
            p.setBrush(QBrush(qcol(tc, A(200)))); p.drawEllipse(a_, 4, 4)

    def _fx_scan(self, p, cx, cy, fw, t, tc, A):
        # sweeping scan line + targeting brackets closing to a lock
        h = fw * 0.46
        y = cy - h + 2 * h * ((t * 0.01) % 1)
        g = QLinearGradient(0, y - fw * 0.08, 0, y)
        g.setColorAt(0, qcol(tc, 0)); g.setColorAt(1, qcol(tc, A(70)))
        p.fillRect(QRectF(cx - h, y - fw * 0.08, 2 * h, fw * 0.08), QBrush(g))
        p.setPen(QPen(qcol(tc, A(230)), 1.5)); p.drawLine(QPointF(cx - h, y), QPointF(cx + h, y))
        lock = min(1.0, (t % 150) / 90)
        d = fw * (0.5 - 0.1 * (1 - (1 - lock) ** 3))
        L = fw * 0.06
        p.setPen(QPen(qcol(tc, A(255)), 2.2))
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            x0, y0 = cx + sx * d, cy + sy * d
            p.drawLine(QPointF(x0, y0), QPointF(x0 - sx * L, y0))
            p.drawLine(QPointF(x0, y0), QPointF(x0, y0 - sy * L))
        if lock >= 1 and self._blink:
            p.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
            p.drawText(QRectF(cx + d - 120, cy - d - 18, 120, 14), Qt.AlignmentFlag.AlignRight, "OBJETIVO FIJADO")

    def _fx_comms(self, p, cx, cy, fw, t, tc, A):
        # transmission wavefronts left/right + uplink signal bars
        p.setBrush(Qt.BrushStyle.NoBrush)
        for side in (0, 180):
            for k in range(4):
                ph = (t * 0.015 + k / 4) % 1
                rr = fw * (0.36 + 0.16 * ph)
                p.setPen(QPen(qcol(tc, A(230 * (1 - ph))), 2.5))
                p.drawArc(QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr), int((side - 22) * 16), 44 * 16)
        p.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
        for side, tag in ((1, "TX"), (-1, "RX")):
            x = cx + side * fw * 0.56
            p.setPen(QPen(qcol(tc, A(255 if self._blink else 120)), 1))
            p.drawText(QRectF(x - 20, cy - fw * 0.1, 40, 14), Qt.AlignmentFlag.AlignCenter, tag)
            for b in range(5):
                lit = b <= (t // 6 + (side > 0) * 2) % 6
                bh = fw * 0.012 * (b + 1)
                p.fillRect(QRectF(x - 14 + b * 6, cy - fw * 0.04 - bh, 4, bh), qcol(tc, A(230 if lit else 50)))

    def _fx_system(self, p, cx, cy, fw, t, tc, A):
        # segmented progress ring filling + counter-rotating gear teeth
        rr, n = fw * 0.29, 36
        done = int(((time.time() - self._task_t0) * 0.5 % 1) * n)
        pen = QPen(qcol(tc), fw * 0.022); pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        rect = QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr)
        for i in range(n):
            pen.setColor(qcol(tc, A(240 if i <= done else 45))); p.setPen(pen)
            p.drawArc(rect, int((90 - i * 10) * 16), -7 * 16)
        rg = fw * 0.258
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(qcol(C.ACC, A(170))))
        for i in range(24):
            p.save(); p.translate(cx, cy); p.rotate(i * 15 - self._rings[0] * 2)
            p.drawRect(QRectF(-fw * 0.006, -rg - fw * 0.012, fw * 0.012, fw * 0.012))
            p.restore()

    def _fx_memory(self, p, cx, cy, fw, t, tc, A):
        # neural net: nodes around the reactor, synapses firing along its edges
        n = 14
        pts = []
        for i in range(n):
            ang = math.radians(i * 360 / n + self._rings[2] * 0.6)
            rr = fw * (0.27 if i % 2 else 0.315)
            pts.append(QPointF(cx + rr * math.cos(ang), cy - rr * math.sin(ang)))
        edges = [(i, (i + j) % n) for i in range(n) for j in (3, 5)]
        p.setPen(QPen(qcol(tc, A(45)), 1))
        for i, j in edges:
            p.drawLine(pts[i], pts[j])
        ph = (t % 10) / 10
        for k in range(4):
            i, j = edges[(t // 10 + k * 7) % len(edges)]
            p.setPen(QPen(qcol(tc, A(220)), 1.8)); p.drawLine(pts[i], pts[j])
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(qcol(C.WHITE, A(255))))
            p.drawEllipse(pts[i] + (pts[j] - pts[i]) * ph, 3, 3)
        p.setPen(QPen(qcol(tc, A(230)), 1.2)); p.setBrush(QBrush(qcol(C.PANEL, A(220))))
        for q in pts:
            p.drawEllipse(q, 4.5, 4.5)


class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawPolygon(_chamfer(QRectF(1, 1, W - 2, H - 2), 6))

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        # segmented gauge
        segs, gap = 14, 2
        bar_x, bar_y, bar_h = 7, H - 10, 5
        sw  = (W - 14 - gap * (segs - 1)) / segs
        lit = round(segs * self._value / 100)
        for i in range(segs):
            p.fillRect(QRectF(bar_x + i * (sw + gap), bar_y, sw, bar_h),
                       bar_col if i < lit else qcol(C.BAR_BG))

        p.setFont(QFont(_FONT, 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

def _log_kind(text: str) -> str:
    """you | ai | file | err | sys, from the line's prefix ("Tú:", "Atlas:", "Archivo:", ...)."""
    tl = text.lower()
    if tl.startswith(("tú:", "you:")):
        return "you"
    if tl.startswith("atlas:"):
        return "ai"
    if tl.startswith(("archivo:", "file:")):
        return "file"
    return "err" if "err" in tl or "falló" in tl else "sys"


def _qml_theme() -> dict:
    """Palette tokens and fonts the QML views read (chat.theme, cmd.theme)."""
    return {"panel": C.PANEL, "panel2": C.PANEL2, "border": C.BORDER, "pri": C.PRI, "priGho": C.PRI_GHO,
            "arc": C.ARC, "text": C.TEXT, "textDim": C.TEXT_DIM, "white": C.WHITE, "green": C.GREEN,
            "red": C.RED, "borderB": C.BORDER_B, "font": _FONT, "display": _DISPLAY,
            "bg": C.BG, "acc": C.ACC, "acc2": C.ACC2, "amber": C.AMBER}


class ChatBridge(QObject):
    """chat.qml's feed: one signal per message, the palette and fonts as a map."""
    message = pyqtSignal(str, str, str)   # kind, text without its prefix, HH:MM
    cleared = pyqtSignal()
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._theme: dict = {}

    @pyqtProperty("QVariantMap", notify=changed)
    def theme(self) -> dict:
        return self._theme

    def set_theme(self, t: dict):
        self._theme = t
        self.changed.emit()


class ChatView(QWidget):
    """The conversation as bubbles (qml/chat.qml), or the plain typed log when Qt Quick can't load."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._bridge = ChatBridge()
        self.retheme()
        self._qml = _quick(self, "chat.qml", {"chat": self._bridge})
        self._log = None if self._qml else LogWidget()
        self.retheme()
        lay.addWidget(self._qml or self._log)

    def clear(self):
        if self._log:
            self._log.clear()
        else:
            self._bridge.cleared.emit()

    def append_log(self, text: str):
        if self._log:
            return self._log.append_log(text)
        kind = _log_kind(text)
        tag, _, rest = text.partition(":")
        body = rest.strip() if tag.strip().lower() in ("tú", "you", "atlas", "archivo", "file", "sis", "sys") else text
        self._bridge.message.emit(kind, body, time.strftime("%H:%M"))

    def retheme(self):
        self._bridge.set_theme(_qml_theme())
        if getattr(self, "_qml", None):   # the panel colour, not white, outside the rounded corners
            self._qml.setClearColor(QColor(C.DARK))


class PaletteBridge(QObject):
    """palette.qml: the commands as a list of maps, its choice back as run(id) or ask(text)."""
    run     = pyqtSignal(str)
    ask     = pyqtSignal(str)
    close   = pyqtSignal()
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._items: list = []
        self._theme: dict = {}

    @pyqtProperty("QVariantList", notify=changed)
    def items(self) -> list:
        return self._items

    @pyqtProperty("QVariantMap", notify=changed)
    def theme(self) -> dict:
        return self._theme

    def set(self, items: list, theme: dict):
        self._items, self._theme = items, theme
        self.changed.emit()


class MemoryBridge(QObject):
    """qml/memory.qml's side: the graph (memory.knowledge), loaded and changed off the UI thread.
    QML calls save(id, value), remove(id) and close(); the graph is dropped on close."""
    save    = pyqtSignal(str, str)
    remove  = pyqtSignal(str)
    close   = pyqtSignal()
    changed = pyqtSignal()
    _done   = pyqtSignal(object, str)   # worker thread -> UI thread: (graph or None, status line)

    def __init__(self):
        super().__init__()
        self._graph: dict = {}
        self._export: dict = {}
        self._theme: dict = {}
        self._busy, self._status = False, ""
        self._done.connect(self._loaded)
        self.save.connect(lambda fid, v: self._run(lambda: kn().correct(fid, v), "Corregido."))
        self.remove.connect(lambda fid: self._run(lambda: kn().forget_everywhere(fid, self._export), "Borrado."))

    @pyqtProperty("QVariantMap", notify=changed)
    def graph(self) -> dict:
        return self._graph

    @pyqtProperty("QVariantMap", notify=changed)
    def theme(self) -> dict:
        return self._theme

    @pyqtProperty(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @pyqtProperty(str, notify=changed)
    def status(self) -> str:
        return self._status

    def refresh(self):
        self._run(None, "")

    def clear(self):
        self._graph, self._export, self._status = {}, {}, ""
        self.changed.emit()

    def _run(self, action, status: str):
        """action (a write) then a fresh graph, in a thread: Engram calls take seconds."""
        self._busy, self._theme = True, _qml_theme()
        self.changed.emit()

        def work():
            try:
                if action:
                    action()
                g = kn().load_graph()
                self._done.emit(g, status)
            except Exception as e:
                self._done.emit(None, f"Error: {e}")
        threading.Thread(target=work, daemon=True).start()

    def _loaded(self, g, status: str):
        if g is not None:
            self._export = g.pop("_export", {})
            self._graph = g
        self._busy, self._status = False, status
        self.changed.emit()
        if status:
            QTimer.singleShot(2500, lambda: (setattr(self, "_status", ""), self.changed.emit()))


def kn():
    from memory import knowledge   # late: pulls memory_manager and config
    return knowledge


class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont(_MONO, 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        self._tag = _log_kind(self._text)
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.ARC),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                "sys":  qcol(C.ACC2),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        if not (self._hovering or self._drag_over):   # marching border only under the mouse: idle repaints cost the HUD frames
            return
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Elige un archivo para ATLAS", str(Path.home()),
            "Todos los archivos (*.*);;"
            "Imágenes (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documentos (*.pdf *.docx *.txt *.md *.pptx);;"
            "Datos (*.csv *.xlsx *.json *.xml);;"
            "Código (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Comprimidos (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol(C.DRAG if z._drag_over else (C.HOVER if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

    def _paint_idle(self, p, W, H, hover):
        # One row: upload arrow left, two lines of text beside it (fits the compact 62 px zone).
        cx, cy = 34, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 9), QPointF(cx, cy + 5))
        p.drawLine(QPointF(cx - 6, cy - 3), QPointF(cx, cy - 9))
        p.drawLine(QPointF(cx + 6, cy - 3), QPointF(cx, cy - 9))
        p.drawLine(QPointF(cx - 10, cy + 9), QPointF(cx + 10, cy + 9))
        tx = cx + 22
        p.setFont(QFont(_FONT, 9, QFont.Weight.DemiBold))
        p.setPen(QPen(qcol(C.TEXT_MED if not hover else C.TEXT), 1))
        p.drawText(QRectF(tx, cy - 17, W - tx, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "Arrastra un archivo o haz clic para adjuntarlo")
        p.setFont(QFont(_FONT, 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, cy + 1, W - tx, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "Imágenes · Video · Audio · PDF · Docs · Código · Datos")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont(_FONT, 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Suelta para cargar")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont(_FONT, 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont(_FONT, 6))
        p.setPen(QPen(qcol(C.LINE), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


def relaunch():
    """Starts a new ATLAS; the caller exits this one. No --autostart: a reboot is not a login."""
    subprocess.Popen([sys.executable, *(a for a in sys.orig_argv[1:] if a != "--autostart")], cwd=os.getcwd())


def audio_devices(kind: str) -> list[str]:
    """Names of the "input" or "output" devices on the default host API, for the settings picker
    (Windows lists each device again under DirectSound/WASAPI). main._audio_device opens them."""
    try:
        import sounddevice as sd
        api = sd.default.hostapi
        return [d["name"] for d in sd.query_devices()
                if d["hostapi"] == api and d[f"max_{kind}_channels"]
                and not any(m in d["name"] for m in ("Asignador de sonido", "Sound Mapper"))]
    except Exception:
        return []


class Toggle(QCheckBox):
    """QCheckBox painted as a switch, or as a small box with a check (box=True) where room is tight.
    The stock indicator showed a bare check when on and nothing at all when off."""

    def __init__(self, text: str = "", box: bool = False, parent=None):
        super().__init__(text, parent)
        self._box = box
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def _ind(self) -> int:
        return 15 if self._box else 38

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        text = fm.horizontalAdvance(self.text()) + 8 if self.text() else 0
        return QSize(self._ind() + text + 2, max(22, fm.height() + 4))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def hitButton(self, pos) -> bool:   # the label is part of the button
        return self.rect().contains(pos)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.35)
        on, mid = self.isChecked(), self.height() / 2
        col = qcol(C.PRI if on else C.TEXT_DIM)
        p.setPen(QPen(col, 1.5))
        p.setBrush(QBrush(col) if on else Qt.BrushStyle.NoBrush)
        r = QRectF(1, mid - 6.5, 13, 13) if self._box else QRectF(1, mid - 9.25, 36, 18.5)
        p.drawRoundedRect(r, 3 if self._box else 9.25, 3 if self._box else 9.25)
        if self._box:
            if on:
                pen = QPen(qcol(C.ON_PRI), 2)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawPolyline(QPolygonF([QPointF(4.5, mid), QPointF(6.8, mid + 2.5), QPointF(11, mid - 2.5)]))
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcol(C.ON_PRI if on else C.TEXT_DIM))
            p.drawEllipse(QPointF(r.right() - 9 if on else r.left() + 9, mid), 6, 6)
        if self.hasFocus():   # keyboard users see where Space will act
            p.setPen(QPen(qcol(C.PRI_DIM), 1, Qt.PenStyle.DotLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(-2, -2, 2, 2), 5, 5)
        if self.text():
            p.setPen(qcol(C.TEXT))
            p.drawText(QRectF(self._ind() + 8, 0, self.width(), self.height()),
                       Qt.AlignmentFlag.AlignVCenter, self.text())


class ThemeCard(QAbstractButton):
    """Checkable card with a tiny painted main window in one palette of THEMES."""

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.name = name
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(104)
        self.setFont(QFont(_FONT, 10))

    def paintEvent(self, _):
        t, on, w = THEMES[self.name], self.isChecked(), self.width()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(qcol(C.PRI if on else (C.PRI_DIM if self.hasFocus() else C.BORDER)), 2 if on else 1))
        p.setBrush(qcol(C.PANEL))
        p.drawRoundedRect(QRectF(1, 1, w - 2, self.height() - 2), 7, 7)
        pv = QRectF(9, 9, w - 18, 62)
        p.setPen(QPen(qcol(t["BORDER"]), 1))
        p.setBrush(qcol(t["BG"]))
        p.drawRoundedRect(pv, 4, 4)
        p.setPen(QPen(qcol(t["BORDER_B"]), 1))
        p.setBrush(qcol(t["PANEL"]))
        side = pv.width() * 0.22
        p.drawRoundedRect(QRectF(pv.left() + 6, pv.top() + 6, side, pv.height() - 12), 2, 2)
        p.drawRoundedRect(QRectF(pv.right() - 6 - side * 1.15, pv.top() + 6, side * 1.15, pv.height() - 12), 2, 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcol(t["ARC_D"]))
        p.drawEllipse(pv.center(), 16, 16)
        p.setBrush(qcol(t["ARC"]))
        p.drawEllipse(pv.center(), 9, 9)
        y = pv.bottom() + 16
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcol(C.PRI if on else C.TEXT_DIM), 4 if on else 1.5))
        p.drawEllipse(QPointF(17, y), 5 if on else 6, 5 if on else 6)
        p.setPen(qcol(C.TEXT))
        p.drawText(QRectF(31, y - 10, w - 40, 20), Qt.AlignmentFlag.AlignVCenter, {"dark": "Oscuro", "light": "Claro"}.get(self.name, self.name.title()))


def switch_theme(name: str):
    """Applied and saved at once, like the startup switch: no restart."""
    if name == C.THEME:
        return
    restyle(name)
    try:
        cfg = json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:   # first boot: SAVE & START writes it
        return
    cfg["theme"] = name
    API_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")


def _ago(iso: str) -> str:
    """'3 min ago' from an ISO time with its zone (sync and device times are UTC)."""
    try:
        s = (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return "?"
    if s >= 86400:
        return f"hace {int(s // 86400)} día{'s' if s >= 172800 else ''}"
    if s >= 3600:
        return f"hace {int(s // 3600)} h"
    return f"hace {int(s // 60)} min" if s >= 60 else "ahora mismo"


def _text(txt: str, pt: float = 9, color: str | None = None, bold: bool = False, wrap: bool = False) -> QLabel:
    w = QLabel(txt)
    w.setFont(QFont(_FONT, int(pt), QFont.Weight.Bold if bold else QFont.Weight.Normal))
    w.setStyleSheet(f"color: {color or C.TEXT};")
    w.setWordWrap(wrap)
    return w


def _button(txt: str, kind: str, slot=None, h: int | None = None) -> QPushButton:
    """kind is the stylesheet id in SettingsOverlay: outline, primary, link, danger."""
    b = QPushButton(txt)
    b.setObjectName(kind)
    big = kind in ("outline", "primary")
    b.setFont(QFont(_FONT, 10 if big else 9, QFont.Weight.Bold if big else QFont.Weight.Normal))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if h:
        b.setFixedHeight(h)
    if slot:
        b.clicked.connect(slot)
    return b


def _field(text: str = "") -> QLineEdit:
    e = QLineEdit(text)
    e.setFont(QFont(_FONT, 10))
    e.setFixedHeight(32)
    return e


def _secret_field(text: str = "") -> tuple[QLineEdit, QFrame]:
    """Password field with a show/hide link inside the same box."""
    box = QFrame()
    box.setObjectName("field")
    box.setFixedHeight(32)
    lay = QHBoxLayout(box)
    lay.setContentsMargins(10, 0, 8, 0)
    e = QLineEdit(text)
    e.setEchoMode(QLineEdit.EchoMode.Password)
    e.setFont(QFont(_FONT, 10))
    eye = _button("👁 mostrar", "eye")
    eye.setCheckable(True)
    eye.toggled.connect(lambda on: (e.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password),
                                    eye.setText("👁 ocultar" if on else "👁 mostrar")))
    lay.addWidget(e, 1)
    lay.addWidget(eye)
    return e, box


def _section(lay, txt: str, first: bool = False):
    if not first:
        lay.addSpacing(12)
    w = _text(txt, 8, C.TEXT_DIM)
    f = w.font()
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
    w.setFont(f)
    lay.addWidget(w)
    lay.addSpacing(2)


def _toggle_row(toggle: Toggle, title: str, sub: str) -> QFrame:
    """Card with a title, a dim line and the switch; a click anywhere on it flips the switch."""
    f = QFrame()
    f.setObjectName("card")
    f.setCursor(Qt.CursorShape.PointingHandCursor)
    lay = QHBoxLayout(f)
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setSpacing(12)
    col = QVBoxLayout()
    col.setSpacing(2)
    col.addWidget(_text(title, 10))
    col.addWidget(_text(sub, 8, C.TEXT_DIM, wrap=True))
    lay.addLayout(col, 1)
    lay.addWidget(toggle)
    f.mousePressEvent = lambda e: toggle.isEnabled() and toggle.toggle()
    return f


def _expander(lay, sub: str, body: QWidget) -> QPushButton:
    """'▸ Advanced' line that shows or hides body, added right below it."""
    b = QPushButton()
    b.setObjectName("adv")
    b.setCheckable(True)
    b.setFont(QFont(_FONT, 9))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    show = lambda on: (b.setText(f"{'▾' if on else '▸'}  Avanzado   ·   {sub}"), body.setVisible(on))
    b.toggled.connect(show)
    show(False)
    lay.addSpacing(8)
    lay.addWidget(b)
    lay.addWidget(body)
    return b


class SettingsOverlay(QWidget):
    """Settings in four tabs: GENERAL (theme, sound, startup), AI ENGINE (engine and keys), ACCOUNT
    (ATLAS ID and shared memory) and MEMORIA (PIN, then the 3D view of what ATLAS knows).
    Shown at first boot and from the CONFIG button."""
    done        = pyqtSignal(dict)
    cancelled   = pyqtSignal()
    open_memory = pyqtSignal()
    _id_done  = pyqtSignal(str, object)   # ATLAS ID work finished in a thread: (what, result or exception)
    # The ACCOUNT tab writes api_keys.json directly; SAVE & START keeps these from disk, not from the copy opened here.
    _ID_KEYS  = ("supabase_url", "supabase_anon_key", "device_name", "memory_sync", "device_id")

    def __init__(self, cfg: dict, can_cancel: bool, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SettingsOverlay {{
                background: {C.OVERLAY}; border: 1px solid {C.BORDER_B}; border-radius: 8px;
            }}
            QLabel {{ background: transparent; }}
            QLineEdit, QComboBox {{
                background: {C.FIELD}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 5px; padding: 3px 10px;
            }}
            QLineEdit:focus, QComboBox:focus {{ border: 1px solid {C.PRI}; }}
            QComboBox QAbstractItemView {{
                background: {C.FIELD}; color: {C.TEXT};
                selection-background-color: {C.PRI_GHO}; selection-color: {C.TEXT};
            }}
            QFrame#card {{ background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 6px; }}
            QFrame#field {{ background: {C.FIELD}; border: 1px solid {C.BORDER}; border-radius: 5px; }}
            QFrame#field QLineEdit {{ border: none; background: transparent; padding: 0; }}
            QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER}; border-radius: 3px; min-height: 24px; }}
            QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
                height: 0; background: none;
            }}
            QPushButton#tab {{
                background: transparent; color: {C.TEXT_DIM};
                border: none; border-bottom: 2px solid transparent; padding: 6px 0 7px;
            }}
            QPushButton#tab:hover {{ color: {C.TEXT_MED}; }}
            QPushButton#tab:checked {{ color: {C.PRI}; border-bottom: 2px solid {C.PRI}; }}
            QPushButton#outline {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px; padding: 0 14px;
            }}
            QPushButton#outline:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
            QPushButton#primary {{
                background: {C.PRI}; color: {C.ON_PRI};
                border: 1px solid {C.PRI}; border-radius: 5px; padding: 0 14px;
            }}
            QPushButton#primary:hover {{ background: {C.ACC2}; border: 1px solid {C.ACC2}; }}
            QPushButton#link, QPushButton#danger, QPushButton#eye {{
                background: transparent; border: none; color: {C.PRI}; padding: 0 2px;
            }}
            QPushButton#danger {{ color: {C.RED}; }}
            QPushButton#eye {{ color: {C.TEXT_DIM}; }}
            QPushButton#link:hover, QPushButton#danger:hover, QPushButton#eye:hover {{ text-decoration: underline; }}
            QPushButton#adv {{
                background: transparent; color: {C.TEXT_MED}; text-align: left;
                border: none; border-top: 1px solid {C.BORDER}; padding: 10px 2px;
            }}
        """)
        self._cfg = dict(cfg)
        self._theme = cfg.get("theme", C.THEME)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 16, 26, 16)
        root.setSpacing(0)
        title = _text("◈  AJUSTES", 13, C.PRI, bold=True)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)
        root.addSpacing(12)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        root.addLayout(bar)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {C.BORDER};")
        root.addWidget(line)
        pages = QStackedWidget()
        root.addWidget(pages, 1)
        tabs = QButtonGroup(self)
        for i, (icon, name, build) in enumerate((("⚙", "GENERAL", self._build_general),
                                                 ("◈", "MOTOR IA", self._build_ai),
                                                 ("☁", "CUENTA", self._build_account),
                                                 ("◉", "MEMORIA", self._build_memory))):
            b = QPushButton(f"{icon}\n{name}")
            b.setObjectName("tab")
            b.setCheckable(True)
            b.setFont(QFont(_FONT, 8))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            tabs.addButton(b, i)
            bar.addWidget(b)
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(0, 12, 8, 4)
            lay.setSpacing(4)
            build(cfg, lay)
            lay.addStretch()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            pages.addWidget(scroll)
        tabs.idClicked.connect(pages.setCurrentIndex)
        tabs.button(0).setChecked(True)
        self._tabs = tabs

        self._err = _text("", 8, C.RED, wrap=True)
        self._err.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._err)
        root.addSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(10)
        if can_cancel:
            row.addWidget(_button("✕  CANCELAR", "outline", self.cancelled.emit, 34))
        row.addWidget(_button("▸  GUARDAR", "primary", self._submit, 34))
        root.addLayout(row)

        self._shown = None
        self._prov.currentIndexChanged.connect(self._on_provider)
        self._prov.setCurrentIndex(max(0, self._prov.findData(cfg.get("provider", "gemini"))))
        self._on_provider()
        self._id_done.connect(self._id_result)
        self._id_refresh()
        if self._signed_in:
            self._id_bg("devices", self._jid().devices)

    # ── GENERAL tab ───────────────────────────────────────────────────────────

    def _build_general(self, cfg, lay):
        _section(lay, "APARIENCIA", first=True)
        row = QHBoxLayout()
        row.setSpacing(12)
        lay.addLayout(row)
        group = QButtonGroup(self)
        for name in THEMES:
            card = ThemeCard(name)
            card.setChecked(name == self._theme)
            card.clicked.connect(lambda _, n=name: self._pick_theme(n))
            group.addButton(card)
            row.addWidget(card)

        # Saved by name: indexes change when devices are plugged in or out.
        _section(lay, "SONIDO")
        self._audio = {}
        row = QHBoxLayout()
        row.setSpacing(10)
        for kind, label in (("input", "Micrófono"), ("output", "Parlante")):
            col = QVBoxLayout()
            col.setSpacing(4)
            col.addWidget(_text(label, 9, C.TEXT_MED))
            cb = QComboBox()
            cb.setFont(QFont(_FONT, 9))
            cb.setFixedHeight(32)
            cb.addItem("Predeterminado del sistema", "")
            cur = cfg.get(f"{kind}_device", "")
            names = audio_devices(kind)
            for n in names if not cur or cur in names else [*names, cur]:
                cb.addItem(n, n)
            cb.setCurrentIndex(max(0, cb.findData(cur)))
            col.addWidget(cb)
            row.addLayout(col, 1)
            self._audio[kind] = cb
        lay.addLayout(row)

        # Every change plays a short sample; saved with GUARDAR like the devices.
        from voice import VOICE_NAME, VOICES
        _section(lay, "VOZ")
        row = QHBoxLayout()
        row.setSpacing(10)
        self._voice, self._speed = QComboBox(), QComboBox()
        for v in VOICES:
            self._voice.addItem(f"{v} (ATLAS)" if v == VOICE_NAME else v, v)
        for k, label in (("lenta", "Lenta"), ("normal", "Normal"), ("rapida", "Rápida")):
            self._speed.addItem(label, k)
        self._voice.setCurrentIndex(max(0, self._voice.findData(cfg.get("voice_name") or VOICE_NAME)))
        self._speed.setCurrentIndex(max(0, self._speed.findData(cfg.get("voice_speed") or "normal")))
        self._volume = QSlider(Qt.Orientation.Horizontal)
        self._volume.setRange(0, 100)
        try:
            self._volume.setValue(int(cfg.get("voice_volume", 100)))
        except (TypeError, ValueError):
            self._volume.setValue(100)
        vol_txt = _text(f"Volumen {self._volume.value()}%", 9, C.TEXT_MED)
        self._volume.valueChanged.connect(lambda v: vol_txt.setText(f"Volumen {v}%"))
        for label, w in (("Voz", self._voice), ("Velocidad", self._speed), (vol_txt, self._volume)):
            col = QVBoxLayout()
            col.setSpacing(4)
            col.addWidget(_text(label, 9, C.TEXT_MED) if isinstance(label, str) else label)
            if isinstance(w, QComboBox):
                w.setFont(QFont(_FONT, 9))
                w.setFixedHeight(32)
                w.currentIndexChanged.connect(self._preview_voice)
            col.addWidget(w)
            row.addLayout(col, 1)
        self._volume.sliderReleased.connect(self._preview_voice)
        lay.addLayout(row)
        reset = QHBoxLayout()
        reset.addStretch(1)
        reset.addWidget(_button("Restaurar voz de ATLAS", "link", self._default_voice))
        lay.addLayout(reset)
        self._samples, self._sample_id = {}, 0

        # Applied on toggle: the login entry lives in the OS, not in api_keys.json.
        _section(lay, "INICIO")
        self._autostart = Toggle()
        self._autostart.setChecked(autostart.enabled())
        self._autostart.toggled.connect(self._set_autostart)
        lay.addWidget(_toggle_row(self._autostart, "Iniciar con Windows" if _OS == "Windows" else "Iniciar al iniciar sesión",
                                  "Abre ATLAS al iniciar sesión."))

    def _default_voice(self):
        from voice import VOICE_NAME
        for w in (self._voice, self._speed):
            w.blockSignals(True)
        self._voice.setCurrentIndex(self._voice.findData(VOICE_NAME))
        self._speed.setCurrentIndex(self._speed.findData("normal"))
        self._volume.setValue(100)
        for w in (self._voice, self._speed):
            w.blockSignals(False)
        self._preview_voice()

    def _preview_voice(self, *_):
        """Says a sample line with the picked voice, speed and volume (Gemini Live, cached per voice and speed)."""
        voice, speed, gain = self._voice.currentData(), self._speed.currentData(), self._volume.value() / 100
        self._sample_id += 1
        me = self._sample_id

        def run():
            import asyncio
            import numpy as np
            import sounddevice as sd
            from voice import PACES, read_aloud
            try:
                pcm = self._samples.get((voice, speed))
                if pcm is None:
                    pcm = self._samples[voice, speed] = asyncio.run(
                        read_aloud("Todos los sistemas en línea. ¿En qué puedo ayudarte?", voice, PACES[speed]))
                if me == self._sample_id:   # a newer change already asked for its own sample
                    sd.play((np.frombuffer(pcm, np.int16) * gain).astype(np.int16), 24000)
            except Exception as e:   # no Gemini key yet, offline
                print(f"[Settings] Voice preview failed: {e}")
        threading.Thread(target=run, daemon=True).start()

    def _pick_theme(self, name: str):
        self._theme = name
        switch_theme(name)

    # ── AI ENGINE tab ─────────────────────────────────────────────────────────

    def _build_ai(self, cfg, lay):
        lay.addWidget(_text("Motor", 9, C.TEXT_MED))
        self._prov = QComboBox()
        self._prov.setFont(QFont(_FONT, 10))
        self._prov.setFixedHeight(32)
        for k, p in PROVIDERS.items():
            self._prov.addItem(p["label"], k)
        lay.addWidget(self._prov)
        self._prov_help = _text("", 8, C.TEXT_DIM, wrap=True)
        lay.addWidget(self._prov_help)

        self._key_lbl = _text("", 9, C.TEXT_MED)
        self._key, self._key_box = _secret_field()
        lay.addSpacing(4)
        lay.addWidget(self._key_lbl)
        lay.addWidget(self._key_box)
        self._login = _button("▸  INICIAR SESIÓN CON CLAUDE", "outline", self._sign_in, 34)
        lay.addSpacing(6)
        lay.addWidget(self._login)

        _section(lay, "MODELO")
        self._model = QComboBox()   # the engine's list; any other id can be typed
        self._model.setEditable(True)
        self._effort = QComboBox()
        for cb in (self._model, self._effort):
            cb.setFont(QFont(_FONT, 10))
            cb.setFixedHeight(32)
        row = QHBoxLayout(); row.setSpacing(10)
        col = QVBoxLayout(); col.setSpacing(4)
        col.addWidget(_text("Modelo", 9, C.TEXT_MED)); col.addWidget(self._model)
        row.addLayout(col, 3)
        self._effort_box = QWidget()
        col = QVBoxLayout(self._effort_box); col.setSpacing(4); col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(_text("Esfuerzo", 9, C.TEXT_MED)); col.addWidget(self._effort)
        row.addWidget(self._effort_box, 2)
        lay.addLayout(row)
        lay.addSpacing(6)
        self._fast = Toggle()
        self._fast.setChecked(cfg.get("fast", True))
        lay.addWidget(_toggle_row(self._fast, "Ayudantes rápidos",
                                  "Las llamadas auxiliares rápidas (revisión del plan, análisis de errores) omiten el esfuerzo de razonamiento."))

        _section(lay, "CLAVES EXTRA")
        lay.addWidget(_text("Clave de Gemini", 9, C.TEXT_MED))
        self._gem, box = _secret_field(cfg.get("gemini_api_key", ""))
        lay.addWidget(box)
        lay.addWidget(_text("Necesaria para la voz, la visión y la búsqueda web.", 8, C.TEXT_DIM))
        lay.addSpacing(6)
        lay.addWidget(_text(f"Clave de OpenRouter  <span style='color:{C.TEXT_DIM}'>· opcional</span>", 9, C.TEXT_MED))
        self._or, box = _secret_field(cfg.get("openrouter_api_key", ""))
        lay.addWidget(box)
        lay.addWidget(_text("Memoria y herramientas de escritorio.", 8, C.TEXT_DIM))

        adv = QWidget()
        a = QVBoxLayout(adv)
        a.setContentsMargins(0, 0, 0, 0)
        a.setSpacing(4)
        self._url_lbl = _text("URL del servidor", 9, C.TEXT_MED)
        self._url = _field(cfg.get("base_url", ""))
        a.addWidget(self._url_lbl)
        a.addWidget(self._url)
        self._adv = _expander(lay, "server URL", adv)
        note = _text("🔒  Las claves se quedan en esta PC.", 8, C.TEXT_DIM)
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addSpacing(6)
        lay.addWidget(note)

    def _on_provider(self):
        # Keep what was typed for the previous engine before swapping the fields.
        if self._shown:
            self._stash(self._shown)
        p = self._prov.currentData()
        info = PROVIDERS[p]
        own_key = info["key"] not in (None, "gemini_api_key", "openrouter_api_key")
        self._key_lbl.setVisible(own_key); self._key_box.setVisible(own_key)
        self._key_lbl.setText(f"Clave de {info['label']}")
        self._key.setText(self._cfg.get(info["key"] or "", ""))
        self._url_lbl.setVisible(p == "ollama"); self._url.setVisible(p == "ollama")
        self._login.setVisible(p == "claude_sub")
        self._prov_help.setText({"claude_sub": "Usa tu cuenta de Claude. No necesita clave.",
                                 "ollama": "Se ejecuta en esta PC. No necesita clave.",
                                 "gemini": "Usa tu clave de Gemini (abajo).",
                                 "openrouter": "Usa tu clave de OpenRouter (abajo)."}.get(p, f"Necesita una clave API de {info['label']}."))
        mine = p == self._cfg.get("provider", "gemini")
        models = info.get("models") or [info["model"]]
        self._model.clear()
        self._model.addItems(models)
        self._model.setCurrentText((self._cfg.get("model") if mine else "") or info["model"])
        efforts = info.get("efforts") or []
        self._effort.clear()
        for e in efforts:
            self._effort.addItem(_EFFORTS.get(e, e), e)
        self._effort.setCurrentIndex(max(0, self._effort.findData(
            self._cfg.get("effort") if mine and self._cfg.get("effort") in efforts else info.get("effort", ""))))
        self._effort_box.setVisible(bool(efforts))
        if p == "ollama":   # a local server: show where it is set
            self._adv.setChecked(True)
        self._shown = p

    # ── MEMORIA tab ───────────────────────────────────────────────────────────
    # The PIN only guards this view inside ATLAS; the memory files stay readable on disk.

    def _build_memory(self, cfg, lay):
        lay.addWidget(_text("Todo lo que ATLAS sabe de ti, como una red neuronal 3D: cómo creció, "
                            "de dónde salió cada dato, y corregir o borrar lo que quieras.", 9, C.TEXT_MED, wrap=True))
        self._mem_box = QWidget()
        self._mem_lay = QVBoxLayout(self._mem_box)
        self._mem_lay.setContentsMargins(0, 0, 0, 0)
        self._mem_lay.setSpacing(4)
        lay.addWidget(self._mem_box)
        self._mem_err = _text("", 8, C.RED, wrap=True)
        lay.addWidget(self._mem_err)
        self._mem_fails = 0
        self._mem_fill()

    def _mem_fill(self):
        while self._mem_lay.count():
            w = self._mem_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        add = self._mem_lay.addWidget
        if not API_FILE.exists():
            add(_text("Primero guarda la configuración con GUARDAR.", 9, C.TEXT_DIM, wrap=True))
            return
        if not kn().has_pin():
            _section(self._mem_lay, "CREA TU CLAVE")
            self._pin1, box1 = _secret_field()
            self._pin2, box2 = _secret_field()
            self._pin1.setPlaceholderText("Clave (mínimo 4 caracteres)")
            self._pin2.setPlaceholderText("Repite la clave")
            add(box1); add(box2)
            self._pin2.returnPressed.connect(self._mem_set_pin)
            self._mem_lay.addSpacing(6)
            add(_button("▸  GUARDAR CLAVE", "primary", self._mem_set_pin, 38))
            add(_text("🔒  La clave protege esta vista dentro de ATLAS, no los archivos de memoria del disco.",
                      8, C.TEXT_DIM, wrap=True))
            return
        _section(self._mem_lay, "CLAVE")
        self._pin, box = _secret_field()
        self._pin.returnPressed.connect(self._mem_open)
        add(box)
        self._mem_lay.addSpacing(6)
        self._mem_go = _button("▸  ABRIR RED", "primary", self._mem_open, 38)
        add(self._mem_go)
        add(_button("¿Olvidaste la clave?", "link", self._mem_forgot))

    def _mem_set_pin(self):
        a, b = self._pin1.text(), self._pin2.text()
        if a != b:
            return self._mem_err.setText("Las claves no coinciden.")
        if not kn().set_pin(a):
            return self._mem_err.setText("Usa al menos 4 caracteres.")
        self._mem_err.setText("")
        self._mem_fill()

    def _mem_open(self):
        if kn().check_pin(self._pin.text()):
            self._mem_fails = 0
            self.open_memory.emit()
            return
        self._pin.clear()
        self._mem_fails += 1
        if self._mem_fails >= 3:   # slow down guessing
            self._mem_go.setEnabled(False)
            self._mem_err.setText("Clave incorrecta. Espera 30 segundos.")
            QTimer.singleShot(30000, lambda: (self._mem_go.setEnabled(True), self._mem_err.setText("")))
        else:
            self._mem_err.setText("Clave incorrecta.")

    def _mem_forgot(self):
        if self._jid().status()["signed_in"]:
            self._mem_err.setText("Inicia sesión con ATLAS ID en el navegador para restablecerla…")
            self._id_bg("pin_reset", lambda: (self._jid().login(), kn().clear_pin()))
        else:
            self._mem_err.setText("Inicia sesión con ATLAS ID (pestaña CUENTA) para restablecerla, o borra "
                                  "\"memory_pin\" de config/api_keys.json.")

    # ── ACCOUNT tab ───────────────────────────────────────────────────────────
    # Network work runs in threads and comes back through _id_done; every control acts at once
    # (no SAVE & START needed) and shows the real result, failures included.

    def _build_account(self, cfg, lay):
        self._acct_out = QWidget()
        v = QVBoxLayout(self._acct_out)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(12)
        hero = QFrame()
        hero.setObjectName("card")
        h = QVBoxLayout(hero)
        h.setContentsMargins(22, 26, 22, 26)
        h.setSpacing(10)
        ring = _text("☁", 20, C.ARC)
        ring.setFixedSize(64, 64)
        ring.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ring.setStyleSheet(f"color: {C.ARC}; border: 2px solid {C.ARC}; border-radius: 32px;")
        h.addWidget(ring, 0, Qt.AlignmentFlag.AlignHCenter)
        for w in (_text("Tu memoria en todos tus equipos", 12, C.PRI, bold=True),
                  _text("Inicia sesión para que lo que ATLAS sabe de ti se sincronice entre tus PC.\n"
                        "Opcional: ATLAS funciona igual sin ella.", 9, C.TEXT_MED, wrap=True)):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            h.addWidget(w)
        h.addSpacing(6)
        self._id_login = _button("G   INICIAR SESIÓN CON GOOGLE", "primary", self._id_sign_in, 36)
        self._id_login.setFixedWidth(260)
        h.addWidget(self._id_login, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(hero)
        note = _text("Las contraseñas y claves nunca salen de esta PC.", 8, C.TEXT_DIM)
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(note)
        lay.addWidget(self._acct_out)

        self._acct_in = QWidget()
        v = QVBoxLayout(self._acct_in)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        card = QFrame()
        card.setObjectName("card")
        c = QHBoxLayout(card)
        c.setContentsMargins(12, 12, 12, 12)
        c.setSpacing(12)
        self._avatar = _text("", 12, C.PRI, bold=True)
        self._avatar.setFixedSize(38, 38)
        self._avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar.setStyleSheet(f"color: {C.PRI}; background: {C.PRI_GHO};"
                                   f" border: 1.5px solid {C.PRI}; border-radius: 19px;")
        col = QVBoxLayout()
        col.setSpacing(2)
        self._email = _text("", 10)
        col.addWidget(self._email)
        col.addWidget(_text("Sesión iniciada con Google", 8, C.TEXT_DIM))
        c.addWidget(self._avatar)
        c.addLayout(col, 1)
        c.addWidget(_button("Cerrar sesión", "link", lambda: self._id_bg("logout", self._jid().logout)))
        v.addWidget(card)
        v.addSpacing(6)

        self._sync_box = Toggle()
        self._sync_box.setChecked(bool(cfg.get("memory_sync")))
        self._sync_box.toggled.connect(self._id_set_sync)
        v.addWidget(_toggle_row(self._sync_box, "Sincronizar memoria entre equipos",
                                "Tus datos y preferencias te siguen. Las contraseñas y claves nunca salen de esta PC."))
        st = QHBoxLayout()
        st.setContentsMargins(2, 4, 2, 0)
        st.setSpacing(8)
        self._dot = QLabel()
        self._dot.setFixedSize(8, 8)
        self._id_status = _text("", 9, C.TEXT_MED, wrap=True)
        st.addWidget(self._dot)
        st.addWidget(self._id_status, 1)
        st.addWidget(_button("⟳ Sincronizar ahora", "link", lambda: self._id_bg("sync", self._sync().sync_now)))
        v.addLayout(st)

        _section(v, "YOUR DEVICES")
        self._devs = QVBoxLayout()
        self._devs.setSpacing(6)
        v.addLayout(self._devs)
        self._rename = QWidget()
        r = QHBoxLayout(self._rename)
        r.setContentsMargins(0, 0, 0, 0)
        self._dev_name = _field(cfg.get("device_name", ""))
        self._dev_name.setPlaceholderText(platform.node())
        r.addWidget(_text("Nombre", 9, C.TEXT_MED))
        r.addWidget(self._dev_name, 1)
        r.addWidget(_button("Guardar", "outline", self._id_save, 32))
        self._rename.hide()
        v.addWidget(self._rename)
        diag = QWidget()
        d = QHBoxLayout(diag)
        d.setContentsMargins(0, 0, 0, 0)
        d.addWidget(_button("Diagnóstico", "outline", self._id_diag, 30))
        d.addStretch()
        _expander(v, "diagnostic", diag)
        lay.addWidget(self._acct_in)

        self._id_msg = _text("", 8, C.TEXT_DIM, wrap=True)
        lay.addSpacing(4)
        lay.addWidget(self._id_msg)

    @staticmethod
    def _jid():
        from memory import atlas_id
        return atlas_id

    @staticmethod
    def _sync():
        from memory import sync
        return sync

    def _id_bg(self, what: str, fn):
        self._id_msg.setText("Trabajando...")

        def run():
            try:
                out = fn()
            except Exception as e:
                out = e
            try:
                self._id_done.emit(what, out)
            except RuntimeError:   # panel closed meanwhile
                pass
        threading.Thread(target=run, daemon=True).start()

    def _id_result(self, what: str, out):
        if what == "pin_reset":   # MEMORIA: signed in again, so the PIN is gone; ask for a new one
            self._mem_err.setText(f"No se restableció: {out}" if isinstance(out, Exception)
                                  else "Clave borrada. Crea una nueva.")
            return self._mem_fill()
        if isinstance(out, Exception):
            self._id_msg.setText(f"FALLÓ: {out}")
        elif what == "devices":
            self._fill_devices(out)
            self._id_msg.setText("")
        elif what == "diag":
            self._id_msg.setText("\n".join(out))
        elif what == "login":
            self._id_msg.setText(f"Sesión iniciada como {out['user']['email']}; este equipo quedó vinculado.")
            self._id_refresh()
            if not self._sync_box.isChecked():   # signing in here is asking for the sync the card promises
                self._sync_box.setChecked(True)
            self._id_bg("devices", self._jid().devices)
        elif what == "remove":
            self._id_bg("devices", self._jid().devices)
        else:
            self._id_msg.setText(out if isinstance(out, str) else "Done.")
            if what == "sync":
                conflicts = self._sync().recent_conflicts(3)
                if conflicts:
                    self._id_msg.setText(out + "\nRecent conflicts (server copy kept):\n" + "\n".join(
                        f"{c[0]}: yours '{c[1]}', kept '{c[2]}'" for c in conflicts))
            if what == "logout":
                self._fill_devices([])
                self._id_msg.setText("Sesión cerrada; la memoria se queda en esta PC.")
        self._id_refresh()

    def _id_refresh(self):
        try:
            st = self._jid().status()
            color, text = self._sync_line(st)
        except Exception as e:
            st, color, text = {"signed_in": False, "email": ""}, C.RED, f"ATLAS ID status FAILED ({e})"
            self._id_msg.setText(text)
        self._signed_in = st["signed_in"]
        self._acct_out.setVisible(not self._signed_in)
        self._acct_in.setVisible(self._signed_in)
        self._email.setText(st["email"])
        self._avatar.setText(st["email"][:1].upper() or "?")
        self._id_status.setText(text)
        self._dot.setStyleSheet(f"background: {color}; border-radius: 4px;")

    def _sync_line(self, st: dict) -> tuple[str, str]:
        """Dot colour and text for the line under the sync switch, from the real sync state."""
        if not st["signed_in"]:
            return C.TEXT_DIM, "No has iniciado sesión; la memoria se queda en esta PC."
        if not self._sync_box.isChecked():
            return C.TEXT_DIM, "La sincronización está apagada; la memoria se queda en esta PC."
        s = self._sync().stats()
        if s["last_error"]:
            return C.RED, f"La última sincronización falló: {s['last_error']}"
        if not s["last_sync"]:
            return C.PRI, "Aún no se ha sincronizado."
        head = f"{s['pending']} changes waiting" if s["pending"] else "Up to date"
        return C.PRI if s["pending"] else C.GREEN, f"{head}  ·  {s['synced']} recuerdos  ·  {_ago(s['last_sync'])}"

    def _fill_devices(self, devs: list[dict]):
        while self._devs.count():
            self._devs.takeAt(0).widget().deleteLater()
        me = self._jid().device_id() if devs else None
        for d in devs:
            mine = d["id"] == me
            row = QFrame()
            row.setObjectName("card")
            h = QHBoxLayout(row)
            h.setContentsMargins(10, 8, 10, 8)
            h.setSpacing(10)
            h.addWidget(_text("▣" if mine else "▭", 12, C.ARC))
            col = QVBoxLayout()
            col.setSpacing(2)
            top = QHBoxLayout()
            top.setSpacing(6)
            name = _text(d["name"] or "?", 10)
            name.setTextFormat(Qt.TextFormat.PlainText)   # names come from the server
            top.addWidget(name)
            if mine:
                badge = _text("ESTE EQUIPO", 7, C.ARC)
                badge.setStyleSheet(f"color: {C.ARC}; border: 1px solid {C.ARC}; border-radius: 8px; padding: 0 6px;")
                top.addWidget(badge)
            top.addStretch()
            col.addLayout(top)
            seen = "active now" if mine else f"seen {_ago(d['last_seen'])}"
            col.addWidget(_text(f"v{d['app_version']}  ·  {seen}", 8, C.TEXT_DIM))
            h.addLayout(col, 1)
            if mine:
                h.addWidget(_button("✎ Renombrar", "link", lambda: self._rename.setVisible(not self._rename.isVisible())))
            else:
                rm = _button("Quitar", "danger")
                rm.clicked.connect(lambda _, b=rm, i=d["id"]: self._id_remove(b, i))
                h.addWidget(rm)
            self._devs.addWidget(row)

    def _id_save(self) -> bool:
        from config import update_config
        try:
            update_config(device_name=self._dev_name.text().strip())
        except Exception as e:   # first boot: api_keys.json exists only after SAVE & START
            self._id_msg.setText(f"FALLÓ al guardar ({e}). Configura primero el motor IA (GUARDAR).")
            return False
        self._id_msg.setText("Nombre del equipo guardado.")
        self._rename.hide()
        self._id_refresh()
        if self._signed_in:   # the device list shows the name stored on the server
            self._id_bg("devices", lambda: (self._jid().register_device(), self._jid().devices())[1])
        return True

    def _id_sign_in(self):
        if self._id_save():
            self._id_bg("login", self._jid().login)
            self._id_msg.setText("Termina de iniciar sesión con Google en el navegador (5 minutos)...")

    def _id_set_sync(self, on: bool):
        from config import update_config
        try:
            update_config(memory_sync=on)
        except Exception as e:
            self._id_msg.setText(f"FALLÓ al guardar ({e}).")
            self._sync_box.blockSignals(True); self._sync_box.setChecked(not on); self._sync_box.blockSignals(False)
            return
        if on and self._jid().status()["signed_in"]:
            self._id_bg("sync", self._sync().sync_now)
        else:
            self._id_msg.setText("Sincronización activada; empieza cuando inicies sesión." if on else "Sincronización desactivada.")
            self._id_refresh()

    def _id_diag(self):
        from memory.memory_manager import diagnose
        self._id_bg("diag", lambda: [*diagnose(), self._sync().describe()])

    def _id_remove(self, btn: QPushButton, dev: str):
        if btn.text() != "Confirm?":   # two clicks: there is no undo
            btn.setText("¿Confirmar?")
            return
        self._id_bg("remove", lambda: self._jid().remove_device(dev))

    def _sign_in(self):
        from agent.llm import subscription_login
        threading.Thread(target=subscription_login, daemon=True).start()
        self._err.setText("Inicia sesión con tu cuenta de Claude en el navegador y luego GUARDAR.")

    def _set_autostart(self, on: bool):
        try:
            autostart.set_enabled(on)
        except OSError as e:
            self._err.setText(f"No se pudo configurar el inicio con el sistema: {e}")
            self._autostart.blockSignals(True); self._autostart.setChecked(not on); self._autostart.blockSignals(False)

    def _stash(self, p: str):
        key = PROVIDERS[p]["key"]
        if key not in (None, "gemini_api_key", "openrouter_api_key"):
            self._cfg[key] = self._key.text().strip()

    def _submit(self):
        p = self._prov.currentData()
        self._stash(p)
        cfg = self._cfg
        cfg.update(
            provider=p,
            model=self._model.currentText().strip(),
            fast=self._fast.isChecked(),
            gemini_api_key=self._gem.text().strip(),
            openrouter_api_key=self._or.text().strip(),
            base_url=self._url.text().strip(),
            input_device=self._audio["input"].currentData(),
            output_device=self._audio["output"].currentData(),
            os_system={"darwin": "mac", "windows": "windows"}.get(_OS.lower(), "linux"),
            theme=self._theme,
            voice_name=self._voice.currentData(),
            voice_speed=self._speed.currentData(),
            voice_volume=self._volume.value(),
        )
        if self._effort.currentData():
            cfg["effort"] = self._effort.currentData()
        need = PROVIDERS[p]["key"]
        if need and not cfg.get(need):
            self._err.setText(f"{PROVIDERS[p]['label']} necesita su clave API.")
            return
        try:
            disk = json.loads(API_FILE.read_text(encoding="utf-8"))
            cfg.update({k: disk[k] for k in self._ID_KEYS if k in disk})
        except Exception:   # first boot: no file yet
            pass
        self.done.emit(cfg)


class ConfigButton(QPushButton):
    """Header button to the settings: chamfered outline, painted slider icon, CONFIG label."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(104, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setToolTip("Motor, claves API e inicio con el sistema")

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = qcol(C.PRI if self.underMouse() else C.PRI_DIM)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QBrush(qcol(C.PRI_GHO if self.underMouse() else C.DARK)))
        p.setPen(QPen(col, 1))
        p.drawPolygon(_chamfer(r, 7))
        # three sliders, knobs at different stops
        cy, x0 = r.center().y(), 14
        p.setPen(QPen(col, 1.4))
        for dy, knob in ((-5, 4), (0, 10), (5, 6)):
            p.drawLine(QPointF(x0, cy + dy), QPointF(x0 + 14, cy + dy))
            p.setBrush(QBrush(col)); p.drawEllipse(QPointF(x0 + knob, cy + dy), 1.9, 1.9)
        f = QFont(_FONT, 8, QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2)
        p.setFont(f)
        p.drawText(QRectF(x0 + 22, 0, r.width() - x0 - 26, r.height()),
                   Qt.AlignmentFlag.AlignCenter, "AJUSTES")


class HudPanel(QWidget):
    """Side panel with cut corners and celeste edge accents."""

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.BG))
        r = QRectF(self.rect()).adjusted(4, 6, -4, -6)
        p.setBrush(QBrush(qcol(C.DARK))); p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.drawPolygon(_chamfer(r, 16))
        p.setPen(QPen(qcol(C.PRI), 2))
        p.drawLine(QPointF(r.left() + 16, r.top()), QPointF(r.left() + 70, r.top()))
        p.drawLine(QPointF(r.right() - 70, r.bottom()), QPointF(r.right() - 16, r.bottom()))
        p.setPen(QPen(qcol(C.ACC), 2))
        p.drawLine(QPointF(r.right(), r.top()), QPointF(r.right(), r.top() + 40))
        p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.left(), r.bottom() - 40))


_FILE_FILTER = ("Todos los archivos (*.*);;"
                "Imágenes (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
                "Documentos (*.pdf *.docx *.txt *.md *.pptx);;"
                "Datos (*.csv *.xlsx *.json *.xml);;"
                "Código (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
                "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
                "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
                "Comprimidos (*.zip *.rar *.tar *.gz *.7z)")


class IconButton(QPushButton):
    """Chamfered square with a painted line glyph (clip, mic, mic_off, full, send, search), so the icons
    share the HUD colours and redraw with the theme. tint: names of C colours (line, fill) overriding
    the default TEXT_MED / PRI-on-hover look."""

    def __init__(self, glyph: str, tip: str = "", size: int = 36, parent=None):
        super().__init__(parent)
        self.glyph, self.tint = glyph, None
        self.setFixedSize(size, size)
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def set_glyph(self, glyph: str, tint: tuple[str, str] | None = None):
        self.glyph, self.tint = glyph, tint
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hov = self.underMouse()
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.tint:
            line = edge = getattr(C, self.tint[0])
            fill = C.LIVE_HOV if hov else getattr(C, self.tint[1])
        else:
            line = C.PRI if hov else C.TEXT_MED
            fill, edge = (C.PRI_GHO, C.PRI) if hov else (C.DARK, C.BORDER_B)
        p.setBrush(QBrush(qcol(fill))); p.setPen(QPen(qcol(edge), 1))
        p.drawPolygon(_chamfer(r, 6))
        k = min(r.width(), r.height()) / 36   # glyphs are drawn in a 36 x 36 box centred on 0,0
        p.translate(r.center()); p.scale(k, k)
        pen = QPen(qcol(line), 1.5 / k)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap); pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        g = self.glyph
        if g == "clip":
            p.rotate(40); p.translate(0, -1)
            path = QPainterPath(QPointF(1.5, -3))
            path.lineTo(1.5, 5);   path.arcTo(QRectF(-1.5, 3.5, 3, 3), 0, -180)
            path.lineTo(-1.5, -6); path.arcTo(QRectF(-1.5, -9, 6, 6), 180, -180)
            path.lineTo(4.5, 7);   path.arcTo(QRectF(-3.5, 3, 8, 8), 0, -180)
            path.lineTo(-3.5, -2)
            p.drawPath(path)
        elif g in ("mic", "mic_off"):
            p.drawRoundedRect(QRectF(-3, -9, 6, 11), 3, 3)
            p.drawArc(QRectF(-6.5, -6.5, 13, 13), 180 * 16, 180 * 16)
            p.drawLine(QPointF(0, 6.5), QPointF(0, 9)); p.drawLine(QPointF(-4, 9), QPointF(4, 9))
            if g == "mic_off":
                p.drawLine(QPointF(-8, -9), QPointF(8, 9))
        elif g == "full":
            for sx in (-1, 1):
                for sy in (-1, 1):
                    p.drawPolyline([QPointF(sx * 8, sy * 3), QPointF(sx * 8, sy * 8), QPointF(sx * 3, sy * 8)])
        elif g == "send":
            p.drawPolyline([QPointF(-4, -7), QPointF(3, 0), QPointF(-4, 7)])
            p.drawLine(QPointF(-9, 0), QPointF(3, 0))
        elif g == "search":
            p.drawEllipse(QPointF(-2, -2), 6, 6)
            p.drawLine(QPointF(2.5, 2.5), QPointF(8, 8))


class HudBox(QWidget):
    """Small panel: cut corners, a celeste accent on its top edge."""

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QBrush(qcol(C.DARK))); p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.drawPolygon(_chamfer(r, 10))
        p.setPen(QPen(qcol(C.PRI), 1.5))
        p.drawLine(QPointF(r.left() + 10, r.top()), QPointF(r.left() + 34, r.top()))


def _box(title: str) -> tuple[HudBox, QVBoxLayout, QLabel]:
    """Box with a spaced title; returns it, its layout and a counter label right of the title."""
    w = HudBox()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setSpacing(4)
    row = QHBoxLayout()
    t = QLabel(title)
    f = QFont(_FONT, 8, QFont.Weight.Bold)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2)
    t.setFont(f)
    t.setStyleSheet(f"color: {C.PRI}; background: transparent;")
    n = _text("", 8, C.TEXT_DIM)
    row.addWidget(t); row.addStretch(); row.addWidget(n)
    lay.addLayout(row)
    return w, lay, n


def _pair(lay: QVBoxLayout, name: str, col: str = C.TEXT) -> QLabel:
    """Row "name ... value" in a box; returns the value label."""
    row = QHBoxLayout()
    b = _text("--", 9, col)
    row.addWidget(_text(name, 9, C.TEXT_DIM)); row.addStretch(); row.addWidget(b)
    lay.addLayout(row)
    return b


class MainWindow(QMainWindow):
    _log_sig   = pyqtSignal(str)
    _state_sig = pyqtSignal(str)
    _ai_sig    = pyqtSignal(str, str)
    _usage_sig = pyqtSignal(int, int)
    _task_sig  = pyqtSignal(str)
    _job_sig   = pyqtSignal(str, str, str)
    _mute_sig  = pyqtSignal(bool)

    def __init__(self, face_path: str):
        super().__init__()
        self.setWindowTitle(f"ATLAS — v{VERSION}")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)
        self.setAcceptDrops(True)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command  = None
        self.on_config_saved  = None   # main's hook: rebuilds the engine from the saved settings
        self._muted           = False
        self._current_file: str | None = None

        central = QWidget()
        central.setStyleSheet(f"background: {C.BG};")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(12, 0, 12, 12)
        root.setSpacing(10)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)

        self.hud = HudCanvas(face_path)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # command bar under the HUD, between the side panels, capped so it stays a compact console
        bar = QWidget()
        bar.setMaximumWidth(700)
        bar.setLayout(self._build_input_row())
        bar_row = QHBoxLayout()
        bar_row.addStretch(1); bar_row.addWidget(bar, 100); bar_row.addStretch(1)
        center = QVBoxLayout()
        center.setSpacing(10)
        center.addWidget(self.hud, 1)
        center.addLayout(bar_row)
        body.addLayout(center, stretch=5)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)

        root.addLayout(body, stretch=1)

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metrik güncelleme timer'ı
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._log_sig.connect(lambda t: _log_kind(t) == "err" and self.hud.flash_error())
        self._state_sig.connect(self._apply_state)
        self._task_sig.connect(self.hud.set_task)
        self._job_sig.connect(self.hud.set_job)
        self._job_sig.connect(self._show_ops)
        self._mute_sig.connect(lambda v: v != self._muted and self._toggle_mute())
        self._ai_sig.connect(self._set_ai)
        self._usage_sig.connect(self._add_usage)

        self._overlay: SettingsOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup(can_cancel=False)

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        self._pal = PaletteBridge()
        self._pal.run.connect(self._run_command)
        self._pal.ask.connect(lambda txt: (self._close_palette(), self._input.setText(txt), self._send()))
        self._pal.close.connect(self._close_palette)
        self._palette = _quick(self.centralWidget(), "palette.qml", {"cmd": self._pal}, overlay=True, clicks=True)
        if self._palette:
            self._palette.hide()
        QShortcut(QKeySequence("Ctrl+K"), self).activated.connect(self._open_palette)
        QShortcut(QKeySequence("Ctrl+B"), self).activated.connect(self._toggle_left)
        self._title_bar()

    def _title_bar(self):
        if _OS == "Windows":
            # Title bar in the theme with celeste border (caption/border colours need Win 11; COLORREF is BGR).
            import ctypes
            hwnd = int(self.winId())
            bgr = lambda h: int(h[5:7] + h[3:5] + h[1:3], 16)
            for attr, val in ((20, int(C.THEME == "dark")), (35, bgr(C.DARK)), (34, bgr(C.PRI))):
                v = ctypes.c_int(val)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4)

    def _commands(self) -> list[dict]:
        """Everything the palette offers, as it stands right now."""
        dark, cfg = C.THEME == "dark", self._load_cfg()
        cmds = [
            {"id": "mute", "title": "Activar micrófono" if self._muted else "Silenciar micrófono", "group": "Voz", "hint": "F4"},
            {"id": "attach", "title": "Adjuntar un archivo…", "group": "Chat", "hint": ""},
            {"id": "clear", "title": "Borrar la conversación", "group": "Chat", "hint": ""},
            {"id": "full", "title": "Salir de pantalla completa" if self.isFullScreen() else "Pantalla completa", "group": "Ventana", "hint": "F11"},
            {"id": "panel", "title": f"{'Ocultar' if self._left_open else 'Mostrar'} panel del sistema", "group": "Ventana", "hint": "Ctrl+B"},
            {"id": "theme", "title": f"Cambiar a tema {'claro' if dark else 'oscuro'}", "group": "Apariencia", "hint": ""},
            {"id": "tab:0", "title": "Ajustes: general (tema, sonido, voz, inicio)", "group": "Ajustes", "hint": ""},
            {"id": "tab:1", "title": "Ajustes: motor IA y claves API", "group": "Ajustes", "hint": ""},
            {"id": "tab:2", "title": "Ajustes: cuenta y sincronización de memoria", "group": "Ajustes", "hint": ""},
            {"id": "tab:3", "title": "Memoria: lo que ATLAS sabe de ti (red 3D)", "group": "Ajustes", "hint": "clave"},
            {"id": "fast", "title": f"Ayudantes rápidos: {'apagar' if cfg.get('fast', True) else 'encender'}", "group": "IA", "hint": ""},
        ]
        info = PROVIDERS.get(cfg.get("provider", "gemini"), PROVIDERS["gemini"])
        cur = self._ai_now or ("", "")
        for kind, items, i in (("model", info.get("models") or [info["model"]], 0), ("effort", info.get("efforts") or [], 1)):
            cmds += [{"id": f"{kind}:{x}", "title": f"{'Modelo' if kind == 'model' else 'Esfuerzo'}: {_EFFORTS.get(x, x)}", "group": "IA", "hint": ""}
                     for x in items if x != cur[i]]
        return cmds

    def _open_palette(self):
        if not self._palette:   # no Qt Quick: the input box is the next best thing
            return self._input.setFocus()
        if self._overlay:
            return
        self._pal.set(self._commands(), _qml_theme())
        self._palette.setGeometry(self.centralWidget().rect())
        self._palette.show()
        self._palette.raise_()
        self._palette.setFocus()
        QMetaObject.invokeMethod(self._palette.rootObject(), "open")

    def _open_memory(self):
        if not getattr(self, "_mem", None):
            self._mem = MemoryBridge()
            self._mem.close.connect(self._close_memory)
            self._memview = _quick(self.centralWidget(), "memory.qml", {"mem": self._mem}, overlay=True, clicks=True)
        if not self._memview:
            return self._log.append_log("SIS: MEMORIA necesita Qt Quick, que no cargó.")
        self.hud.show_layer(False)   # both layers stack on top; one at a time, and the HUD rests meanwhile
        self._memview.setGeometry(self.centralWidget().rect())
        self._memview.show()
        self._memview.raise_()
        self._memview.setFocus()
        self._mem.refresh()

    def _close_memory(self):
        if getattr(self, "_memview", None) and self._memview.isVisible():
            self._memview.hide()
            self._mem.clear()   # nothing of it stays in memory once locked
            self.hud.show_layer(True)
            self._input.setFocus()

    def _close_palette(self):
        if self._palette and self._palette.isVisible():
            self._palette.hide()
            self._input.setFocus()

    def _run_command(self, cid: str):
        self._close_palette()
        kind, _, arg = cid.partition(":")
        if kind == "mute":
            self._toggle_mute()
        elif kind == "attach":
            self._browse()
        elif kind == "clear":
            self._log.clear()
        elif kind == "full":
            self._toggle_fullscreen()
        elif kind == "panel":
            self._toggle_left()
        elif kind == "theme":
            switch_theme("light" if C.THEME == "dark" else "dark")
        elif kind == "tab":
            self._overlay or self._show_setup(can_cancel=True, tab=int(arg))
        elif kind == "fast":
            self._save_flag("fast", not self._load_cfg().get("fast", True))
        elif kind in ("model", "effort"):
            cfg = self._load_cfg()
            if cfg:   # unreadable file: never overwrite the keys
                cfg[kind] = arg
                self._on_setup_done(cfg)   # saves and reboots onto the new brain

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._overlay and self._overlay.isVisible():
            self._place_overlay()
        for v in (getattr(self, "_palette", None), getattr(self, "_memview", None)):
            if v and v.isVisible():
                v.setGeometry(self.centralWidget().rect())

    def showEvent(self, event):   # first-boot settings are placed before the window has its real size
        super().showEvent(event)
        if self._overlay:
            self._place_overlay()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent):
        path = e.mimeData().urls()[0].toLocalFile()
        if path and Path(path).is_file():
            self._on_file_selected(path)

    def _update_metrics(self):
        snap = _metrics.snapshot()
        self._bar_cpu.set_value(snap["cpu"], f"{snap['cpu']:.0f}%")
        self._bar_mem.set_value(snap["mem"], f"{snap['mem']:.0f}%")
        gpu, tmp = snap["gpu"], snap["tmp"]
        self._bar_gpu.set_value(max(gpu, 0), f"{gpu:.0f}%" if gpu >= 0 else "N/A")
        self._bar_tmp.set_value(min(max(tmp, 0), 100), f"{tmp:.0f}°C" if tmp >= 0 else "N/A")
        rate = lambda mb: f"{mb:.1f} MB/s" if mb >= 1 else f"{mb * 1024:.0f} KB/s"
        down, up = snap["net"]
        self._net_down.setText(rate(down)); self._net_up.setText(rate(up))

    def _build_header(self) -> QWidget:
        # Name and version left; state, clock and settings right.
        w = QWidget(); w.setObjectName("hdr")
        w.setFixedHeight(56)
        w.setStyleSheet(f"#hdr {{ background: transparent; border-bottom: 1px solid {C.BORDER}; }}")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 8, 4, 8)
        lay.setSpacing(16)

        title = QLabel("ATLAS")
        tf = QFont(_DISPLAY, 18, QFont.Weight.Black)
        tf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 6)
        title.setFont(tf)
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(title)
        lay.addWidget(_text(f"v{VERSION}", 9, C.TEXT_DIM))
        lay.addStretch()
        self._online = _text("● EN LÍNEA", 9, C.GREEN, bold=True)
        lay.addWidget(self._online)
        self._clock_lbl = QLabel("12:00 AM")
        self._clock_lbl.setFont(QFont(_DISPLAY, 12, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(self._clock_lbl)
        cfg_btn = ConfigButton()
        cfg_btn.clicked.connect(lambda: self._overlay or self._show_setup(can_cancel=True))
        lay.addWidget(cfg_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        return w

    def _tick_clock(self):
        # 12-hour clock; AM/PM by hand: %p is empty or localized under some locales
        self._clock_lbl.setText(time.strftime("%I:%M ").lstrip("0") + ("AM" if time.localtime().tm_hour < 12 else "PM"))
        if self.hud._jobs:   # keeps the elapsed time of background operations moving
            self._show_ops()

    def _build_left_panel(self) -> QWidget:
        # Folds to a thin rail (Ctrl+B, the button at its foot, or the palette) to give the HUD room.
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        self._left_body = QWidget()
        outer.addWidget(self._left_body, 1)
        self._fold_btn = QPushButton()
        self._fold_btn.setFixedHeight(24)
        self._fold_btn.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
        self._fold_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fold_btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_DIM}; border: none; }}"
                                     f" QPushButton:hover {{ color: {C.PRI}; }}")
        self._fold_btn.clicked.connect(self._toggle_left)
        outer.addWidget(self._fold_btn)
        lay = QVBoxLayout(self._left_body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        box, b, _ = _box("SISTEMA")
        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("MEM", C.ACC2)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_tmp = MetricBar("TEMP", C.ARC)
        for bar in (self._bar_cpu, self._bar_mem, self._bar_gpu, self._bar_tmp):
            b.addWidget(bar)
        lay.addWidget(box)

        box, b, _ = _box("RED")
        self._net_down = _pair(b, "Bajada", C.ARC)
        self._net_up = _pair(b, "Subida")
        lay.addWidget(box)

        box, b, _ = _box("NÚCLEO IA")
        self._ai_hdr = _text("--", 9, C.TEXT, wrap=True)
        self._ai_sub = _text("", 8, C.TEXT_DIM, wrap=True)
        b.addWidget(self._ai_hdr); b.addWidget(self._ai_sub)
        self._ai_in = _pair(b, "Tokens entrada", C.ARC)
        self._ai_out = _pair(b, "Tokens salida", C.ARC)
        self._ai_in.setText("0"); self._ai_out.setText("0")
        lay.addWidget(box)
        lay.addStretch()
        self._ai_now = None
        self._tok_in = self._tok_out = 0
        self._left_anim = QVariantAnimation(w, duration=220, easingCurve=QEasingCurve.Type.OutCubic)
        self._left_anim.valueChanged.connect(lambda v: w.setFixedWidth(int(v)))
        self._set_left(not self._load_cfg().get("panel_collapsed", False), animate=False)
        return w

    def _set_left(self, open_: bool, animate: bool = True):
        self._left_open = open_
        self._left_body.setVisible(open_)
        self._fold_btn.setText("‹  OCULTAR" if open_ else "›")
        self._fold_btn.setToolTip(("Ocultar" if open_ else "Mostrar") + " panel del sistema  [Ctrl+B]")
        end = _LEFT_W if open_ else 34
        if animate:
            self._left_anim.stop()
            self._left_anim.setStartValue(self._left_panel.width())
            self._left_anim.setEndValue(end)
            self._left_anim.start()
        else:   # first build: self._left_panel is not set yet
            self._left_body.parentWidget().setFixedWidth(end)

    def _toggle_left(self):
        self._set_left(not self._left_open)
        self._save_flag("panel_collapsed", not self._left_open)

    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_RIGHT_W)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        box, b, self._ops_n = _box("OPERACIONES")
        self._ops = _text("Sin operaciones en curso", 8, C.TEXT_DIM, wrap=True)
        self._ops.setTextFormat(Qt.TextFormat.RichText)
        b.addWidget(self._ops)
        lay.addWidget(box)

        box, b, _ = _box("CONVERSACIÓN")
        self._log = ChatView()
        b.addWidget(self._log, stretch=1)
        lay.addWidget(box, stretch=1)
        return w

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6); row.setContentsMargins(0, 0, 0, 0)
        self._input = QLineEdit()
        self._input.setPlaceholderText('> Escribe una orden o di "Atlas"…')
        self._input.setFont(QFont(_MONO, 10))
        self._input.setFixedHeight(36)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.DARK}; color: {C.WHITE};
                border: 1px solid {C.BORDER_B}; padding: 3px 10px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        def _icon(glyph, tip, slot):
            b = IconButton(glyph, tip)
            b.clicked.connect(slot)
            row.addWidget(b)
            return b

        _icon("search", "Comandos  [Ctrl+K]", self._open_palette)
        _icon("clip", "Adjuntar archivo (o arrástralo a la ventana)", self._browse)
        self._mute_btn = _icon("mic", "Micrófono  [F4]", self._toggle_mute)
        self._style_mute_btn()
        _icon("full", "Pantalla completa  [F11]", self._toggle_fullscreen)
        _icon("send", "Enviar  [Enter]", self._send)
        return row

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Elige un archivo para ATLAS", str(Path.home()), _FILE_FILTER)
        if path:
            self._on_file_selected(path)

    def _show_ops(self, *_):
        jobs, now = list(self.hud._jobs.values()), time.time()
        self._ops_n.setText(str(len(jobs)) if jobs else "")
        if not jobs:
            self._ops.setText("Sin operaciones en curso")
            return
        esc = lambda t: t.replace("&", "&amp;").replace("<", "&lt;")
        self._ops.setText("<br>".join(
            f"<span style='color:{C.TEXT}'>{i + 1:02d}  {esc(goal)}</span>"
            f"  <span style='color:{C.TEXT_DIM}'>T+{now - t0:.0f}s</span>"
            + (f"<br><span style='color:{C.ACC2}'>{esc(step)}</span>" if step else "")
            for i, (goal, step, t0) in enumerate(jobs)))

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        size = _fmt_size(p.stat().st_size)
        self._input.setPlaceholderText(f"{p.name} · {size} · dile a ATLAS qué hacer con él")
        self._log.append_log(f"Archivo: {p.name} ({size}) cargado")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self.hud.state in ("LISTENING", "STANDBY", "MUTED"):   # never hide SPEAKING / THINKING
            self._apply_state("MUTED" if self._muted else "LISTENING")
        self._log.append_log("SIS: Micrófono silenciado." if self._muted else "SIS: Micrófono activo.")

    def _style_mute_btn(self):
        col = C.MUTED_C if self._muted else C.GREEN
        self._mute_btn.set_glyph("mic_off" if self._muted else "mic",
                                 ("MUTED_C", "MUTE_BG") if self._muted else ("GREEN", "LIVE_BG"))
        self._online.setText("● SILENCIADO" if self._muted else "● EN LÍNEA")
        self._online.setStyleSheet(f"color: {col}; background: transparent;")

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self._log.append_log(f"Tú: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")

    def _set_ai(self, label: str, model: str):
        self._ai_now = (model, get_effort() or "")
        self._ai_hdr.setText(model or label)
        voice = self._load_cfg().get("voice_name") or "Charon"
        self._ai_sub.setText(" · ".join(filter(None, (label, _EFFORTS.get(get_effort(), get_effort()), voice))))
        self._ai_sub.setToolTip("Cámbialo en Ajustes › Motor IA  (Ctrl+K)")

    def _save_flag(self, key: str, value):
        """One UI preference into api_keys.json, live, no restart."""
        cfg = self._load_cfg()
        if not cfg:   # unreadable file: never overwrite the keys with just this flag
            return
        cfg[key] = value
        API_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")

    def _add_usage(self, tin: int, tout: int):
        self._tok_in += tin; self._tok_out += tout
        k = lambda n: f"{n / 1000:.1f}k" if n >= 1000 else str(n)
        self._ai_in.setText(k(self._tok_in)); self._ai_out.setText(k(self._tok_out))

    def _load_cfg(self) -> dict:
        try:
            return json.loads(API_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _check_config(self) -> bool:
        d = self._load_cfg()
        key = PROVIDERS.get(d.get("provider", "gemini"), PROVIDERS["gemini"])["key"]
        return bool(d.get(key)) if key else bool(d)

    def _place_overlay(self):
        cw = self.centralWidget()
        ow, oh = 520, min(640, cw.height() - 20)
        self._overlay.setGeometry((cw.width() - ow) // 2, (cw.height() - oh) // 2, ow, oh)

    def _show_setup(self, can_cancel: bool, tab: int = 0):
        self._overlay = ov = SettingsOverlay(self._load_cfg(), can_cancel, self.centralWidget())
        ov._tabs.button(tab).click()
        self._place_overlay()
        ov.done.connect(self._on_setup_done)
        ov.open_memory.connect(lambda: (self._close_overlay(), self._open_memory()))
        ov.cancelled.connect(self._close_overlay)
        self.hud.show_layer(False)
        ov.show()

    def _close_overlay(self):
        self._overlay.deleteLater()
        self._overlay = None
        self.hud.show_layer(True)

    def _on_setup_done(self, cfg: dict):
        same = cfg == self._load_cfg()   # e.g. only the theme changed: it is already live and saved
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
        self._close_overlay()
        if self._ready and same:
            return
        if self._ready:
            # The engine reads its config once at boot: main builds a new one in-process.
            self._log.append_log("SIS: Configuración guardada. Aplicando cambios...")
            if self.on_config_saved:
                self.on_config_saved()
            else:   # no engine hook (an older main): restart the whole app
                relaunch()
                QTimer.singleShot(300, lambda: os._exit(0))
            return
        self._ready = True
        self._apply_state("LISTENING")
        self._log.append_log(f"SIS: Iniciado. Motor={cfg['provider'].upper()}. ATLAS en línea.")

class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


def _taskbar_identity():
    """The window belongs to python.exe, so the taskbar showed "Python" and python's icon. Windows
    takes the name and icon from a Start Menu shortcut carrying our AppUserModelID: create it
    (or stamp the ID on the installer's one) pointing at Atlas.exe, which pinning then starts."""
    try:
        import pythoncom
        from win32com.propsys import propsys, pscon
        from win32com.shell import shell
        exe = next((p for p in (BASE_DIR / "Atlas.exe", BASE_DIR.parent / "Atlas.exe") if p.exists()), None)
        if not exe:
            return
        lnk = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/ATLAS.lnk"
        link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)
        file = link.QueryInterface(pythoncom.IID_IPersistFile)
        if lnk.exists():
            file.Load(str(lnk))
            store = link.QueryInterface(propsys.IID_IPropertyStore)
            if store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue() == "Atlas.Assistant":
                return
        else:
            link.SetPath(str(exe))
            link.SetWorkingDirectory(str(BASE_DIR))
            link.SetIconLocation(str(BASE_DIR / "atlas.ico"), 0)
            link.SetDescription("ATLAS assistant")
        store = link.QueryInterface(propsys.IID_IPropertyStore)
        store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType("Atlas.Assistant"))
        store.Commit()
        file.Save(str(lnk), 0)
    except Exception as e:   # cosmetic: the window works without it
        print(f"[ATLAS] ⚠️ taskbar identity: {e}")


class AtlasUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        _load_fonts()
        if _OS == "Windows":
            import ctypes  # own taskbar group, so Windows shows our icon instead of python's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Atlas.Assistant")
            _taskbar_identity()
        self._app.setWindowIcon(QIcon(str(BASE_DIR / "atlas.ico")))
        try:
            apply_theme(json.loads(API_FILE.read_text(encoding="utf-8")).get("theme", "dark"))
        except Exception:   # first boot: no file yet
            pass
        self._win = MainWindow(face_path)
        self._win.show()
        self.root = _RootShim(self._app)

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):   # thread-safe: the toggle runs on the Qt thread
        self._win._mute_sig.emit(bool(v))

    @property
    def current_file(self) -> str | None:
        return self._win._current_file

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_config_saved(self):
        return self._win.on_config_saved

    @on_config_saved.setter
    def on_config_saved(self, cb):   # called on the Qt thread
        self._win.on_config_saved = cb

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def set_task(self, tool: str):
        """Tool name -> its HUD animation; "" when the tool finishes. Thread-safe."""
        self._win._task_sig.emit(tool)

    def set_job(self, key: str, goal: str, step: str = ""):
        """Background job shown in the HUD; goal "" removes it. Thread-safe."""
        self._win._job_sig.emit(key, goal, step)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def voice_levels(self, pcm: bytes, rate: int, latency: float = 0.0):
        """A speech chunk just handed to the output stream (int16 mono): its sounds shape the HUD face's
        mouth (mouth_shapes). It is heard once the audio queued ahead of it has played, ~latency minus its own length
        later; without that delay the lips ran ahead of the voice. Thread-safe."""
        shapes, self._mouth_peak = mouth_shapes(pcm, rate, getattr(self, "_mouth_peak", 3000.0))
        if shapes:
            self._win.hud._bridge.voice.emit(shapes, max(0.0, latency - len(pcm) / 2 / rate))

    def set_model(self, label: str, model: str):
        self._win._ai_sig.emit(label, model)

    def add_usage(self, tin: int, tout: int):
        self._win._usage_sig.emit(int(tin or 0), int(tout or 0))

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        self.set_state("MUTED" if self.muted else "LISTENING")