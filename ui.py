from __future__ import annotations

import json
import math
import os
import platform
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

import autostart
from config import PROVIDERS, VERSION, get_effort

from PyQt6.QtCore import (
    QEasingCurve, QMimeData, QObject, QPointF, QRectF, QSize, Qt,
    QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QIcon, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QPolygonF, QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QTextEdit,
    QVBoxLayout, QWidget, QProgressBar,
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
_LEFT_W  = 172
_RIGHT_W = 340

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


_FONT = "Bahnschrift"   # DIN-style face bundled with Windows 10+
_MONO = "Consolas"


class C:
    # Iron Man palette: hot-rod red and gold armour, arc-reactor cyan.
    BG        = "#05070b"
    PANEL     = "#0a0d12"
    PANEL2    = "#0d1016"
    BORDER    = "#2b1a1c"
    BORDER_B  = "#7a1d24"
    BORDER_A  = "#3d2226"
    PRI       = "#ffb627"
    PRI_DIM   = "#a36d12"
    PRI_GHO   = "#2a1c05"
    ACC       = "#e0202e"
    ACC2      = "#ffd166"
    ARC       = "#6ff3ff"
    ARC_D     = "#0b5d6e"
    GREEN     = "#3dffa8"
    GREEN_D   = "#1f9e64"
    RED       = "#ff2a3d"
    RED_D     = "#7a0f18"
    MUTED_C   = "#ff2a3d"
    TEXT      = "#ffe2a6"
    TEXT_DIM  = "#7d6644"
    TEXT_MED  = "#c19a5b"
    WHITE     = "#fff6e5"
    DARK      = "#07090d"
    BAR_BG    = "#1b140a"
    GRID      = "#0d1a20"
    STEEL     = "#3a4048"
    STEEL_D   = "#101317"
    COPPER    = "#d08a4a"
    COPPER_D  = "#4a2a14"


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c

class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
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
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
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
    """Rectangle with the top-left and bottom-right corners cut, Stark HUD style."""
    l, t, rt, b = r.left(), r.top(), r.right(), r.bottom()
    return QPolygonF([QPointF(l + c, t), QPointF(rt, t), QPointF(rt, b - c),
                      QPointF(rt - c, b), QPointF(l, b), QPointF(l, t + c)])


# Tool -> HUD task animation. Unknown tools fall back to "system".
TASK_MODES = {
    "apple_music": "media", "youtube_video": "media",
    "code_helper": "code", "dev_agent": "code", "solve_task": "code", "agent_task": "code", "task_status": "code",
    "web_search": "net", "browser_control": "net", "flight_finder": "net", "weather_report": "net",
    "screen_process": "scan",
    "send_message": "comms", "email": "comms", "reminder": "comms",
    "save_memory": "memory", "search_memory": "memory",
}
# mode: (status title, colour)
TASK_LOOK = {
    "media":  ("AUDIO STREAM",     C.ARC),
    "code":   ("CODE SYNTHESIS",   C.GREEN),
    "net":    ("GLOBAL NET LINK",  C.ARC),
    "scan":   ("OPTICAL SCAN",     C.RED),
    "comms":  ("TRANSMITTING",     C.PRI),
    "memory": ("MEMORY CORE",      C.ACC2),
    "system": ("SYSTEM OVERRIDE",  C.PRI),
}
_CODE_RING = ("def execute(task):  plan = jarvis.think(task)  for step in plan: run(step)  "
              "return verify(result)  0x3FA9 0x7C11 0xE0D2  ")


class HudCanvas(QWidget):
    """Arc reactor core surrounded by rotating Iron Man helmet-HUD rings."""

    def __init__(self, face_path: str = "", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
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

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

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
            self._jobs.pop(key, None)
        else:
            self._jobs[key] = [goal, step, self._jobs.get(key, [0, 0, time.time()])[2]]

    def _step(self):
        self._tick += 1
        on = self._task is not None and (self._task_end is None or time.time() < self._task_end)
        self._task_a += ((1.0 if on else 0.0) - self._task_a) * 0.12
        if not on and self._task_a < 0.02:
            self._task = None
        if self._task == "media":
            for i in range(len(self._spec)):
                self._spec[i] += (random.uniform(0.1, 1.0) * (0.6 + 0.4 * math.sin(self._tick * 0.05 + i * 0.4))
                                  - self._spec[i]) * 0.3
        self._boot = min(1.0, self._boot + 0.008)
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

        sp = 0.38 if self.speaking else 0.15
        self._scale += (self._tgt_scale - self._scale) * sp
        self._halo  += (self._tgt_halo  - self._halo)  * sp

        speeds = [1.1, -0.7, 0.35] if self.speaking else [0.4, -0.25, 0.12]
        for i, spd in enumerate(speeds):
            self._rings[i] = (self._rings[i] + spd) % 360
        self._scan = (self._scan - (3.0 if self.speaking else 1.2)) % 360

        fw  = min(self.width(), self.height())
        lim = fw * 0.5
        spd = 3.6 if self.speaking else 1.6
        self._pulses = [r + spd for r in self._pulses if r + spd < lim]
        if len(self._pulses) < 3 and random.random() < (0.06 if self.speaking else 0.02):
            self._pulses.append(fw * 0.12)

        for i in range(len(self._wave)):
            if self.muted:
                tgt = 0.05
            elif self.speaking:
                tgt = random.uniform(0.15, 1.0) * (1 - abs(i - 19.5) / 26)
            else:
                tgt = 0.12 + 0.08 * math.sin(self._tick * 0.08 + i * 0.5)
            self._wave[i] += (tgt - self._wave[i]) * 0.35

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

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
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
        v.setColorAt(0.5, qcol("#000000", 0)); v.setColorAt(1, qcol("#000000", 210))
        p.fillRect(px.rect(), QBrush(v))
        p.end()
        return px

    def paintEvent(self, _):
        if self._bg is None or self._bg.size() != self.size():
            self._bg = self._background()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(0, 0, self._bg)

        W, H = self.width(), self.height()
        fw = min(W, H * 0.86)
        cx, cy = W / 2, H / 2 - H * 0.05
        b    = 1 - (1 - self._boot) ** 3                 # ease-out power-up
        glow = min(1.0, self._halo / 190)
        arc  = C.RED  if self.muted else C.ARC           # reactor light
        gold = C.RED  if self.muted else C.PRI           # HUD rings
        red  = C.RED_D if self.muted else C.ACC
        p.setOpacity(b)
        k = 0.7 + 0.3 * b                                # rings expand while booting

        # ambient glow
        gr = fw * 0.42
        g = QRadialGradient(cx, cy, gr)
        g.setColorAt(0.0, qcol(arc, int(60 + 120 * glow)))
        g.setColorAt(0.4, qcol(arc, int(15 + 45 * glow)))
        g.setColorAt(1.0, qcol(arc, 0))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(cx, cy), gr, gr)

        # shock-wave pulses
        p.setBrush(Qt.BrushStyle.NoBrush)
        for pr in self._pulses:
            a = max(0, int(150 * (1.0 - pr / (fw * 0.5))))
            p.setPen(QPen(qcol(arc, a), 1.2))
            p.drawEllipse(QPointF(cx, cy), pr, pr)

        # outer compass: ticks every 5 deg, bearings every 30 deg
        ro = fw * 0.47 * k
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

        # core
        rc = R * 0.5
        p.setPen(QPen(qcol(arc, 255), max(2.0, R * 0.05)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), rc, rc)
        core = QRadialGradient(cx, cy, rc)
        core.setColorAt(0.0, qcol(C.WHITE, 255))
        core.setColorAt(0.35 + 0.25 * glow, qcol(arc, 230))
        core.setColorAt(1.0, qcol(arc, 40))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(core))
        p.drawEllipse(QPointF(cx, cy), rc * 0.92, rc * 0.92)
        tri = QPolygonF([QPointF(cx + rc * 0.78 * math.cos(math.radians(90 + 120 * n)),
                                 cy - rc * 0.78 * math.sin(math.radians(90 + 120 * n)))
                         for n in range(3)])
        p.setBrush(QBrush(qcol(C.WHITE, int(60 + 80 * glow))))
        p.setPen(QPen(qcol(C.WHITE, 230), max(1.5, R * 0.035)))
        p.drawPolygon(tri)

        # sparks
        p.setPen(Qt.PenStyle.NoPen)
        for pt in self._particles:
            p.setBrush(QBrush(qcol(arc, max(0, min(255, int(pt[4] * 255))))))
            p.drawEllipse(QPointF(pt[0], pt[1]), 2.2, 2.2)

        if self._jobs:
            self._draw_jobs(p, cx, cy, fw * k)

        # corner readouts
        p.setOpacity(1.0)
        pad = 14
        p.setFont(QFont(_FONT, 10, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(pad, pad, 260, 18), Qt.AlignmentFlag.AlignLeft, "STARK INDUSTRIES")
        p.setFont(QFont(_FONT, 8))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(pad, pad + 18, 260, 14), Qt.AlignmentFlag.AlignLeft,
                   f"ARC REACTOR  //  v{VERSION}")
        out = int(b * (40 + 60 * glow)) if not self.muted else 12
        p.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(arc), 1))
        p.drawText(QRectF(W - 200 - pad, pad, 200, 14), Qt.AlignmentFlag.AlignRight,
                   f"CORE OUTPUT  {out:3d}%")
        bw = 120
        p.fillRect(QRectF(W - pad - bw, pad + 18, bw, 4), qcol(C.BAR_BG))
        p.fillRect(QRectF(W - pad - bw, pad + 18, bw * out / 100, 4), qcol(arc))
        p.setFont(QFont(_FONT, 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(W - 200 - pad, pad + 26, 200, 14), Qt.AlignmentFlag.AlignRight,
                   "BOOTING STARK OS…" if b < 1 else "ALL SYSTEMS NOMINAL")

        # status
        sy = min(H - 58.0, cy + fw * 0.5)
        if self.muted:
            txt, col = "⊘  MUTED",     qcol(C.RED)
        elif self.speaking:
            txt, col = "●  SPEAKING",  qcol(C.ARC)
        elif self._task and self._task_a > 0.5:
            title, tc = TASK_LOOK[self._task]
            txt, col = f"{'◢' if self._blink else '◣'}  {title}", qcol(tc)
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"
            txt, col = f"{sym}  THINKING",   qcol(C.PRI)
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"
            txt, col = f"{sym}  PROCESSING", qcol(C.PRI)
        elif self.state == "LISTENING":
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  LISTENING",  qcol(C.GREEN)
        else:
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  {self.state}", qcol(C.PRI)

        f = QFont(_FONT, 11, QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 140)
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
                   f"{'◢' if self._blink else '◣'} BACKGROUND OPS [{len(jobs)}]")
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
            p.drawText(QRectF(cx + d - 120, cy - d - 18, 120, 14), Qt.AlignmentFlag.AlignRight, "TARGET LOCK")

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
        tl = self._text.lower()
        if   tl.startswith("you:"):    self._tag = "you"
        elif tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):   self._tag = "file"
        elif "err" in tl:              self._tag = "err"
        else:                          self._tag = "sys"
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
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
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

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
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
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont(_FONT, 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont(_FONT, 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont(_FONT, 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

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
        p.setPen(QPen(qcol("#1e5c6a"), 1))
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


class SettingsOverlay(QWidget):
    """AI engine and API keys. Shown at first boot and from the CONFIG button."""
    done      = pyqtSignal(dict)
    cancelled = pyqtSignal()

    def __init__(self, cfg: dict, can_cancel: bool, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SettingsOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
            QLineEdit, QComboBox {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 8px;
            }}
            QLineEdit:focus, QComboBox:focus {{ border: 1px solid {C.PRI}; }}
            QComboBox QAbstractItemView {{
                background: #000d12; color: {C.TEXT}; selection-background-color: {C.PRI_GHO};
            }}
        """)
        self._cfg = dict(cfg)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 18, 28, 18)
        layout.setSpacing(4)

        def _lbl(txt, font_size=8, bold=False, color=C.TEXT_DIM,
                 align=Qt.AlignmentFlag.AlignLeft):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont(_FONT, font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            layout.addWidget(w)
            return w

        def _field(label, secret=True):
            lbl = _lbl(label)
            e = QLineEdit()
            if secret:
                e.setEchoMode(QLineEdit.EchoMode.Password)
            e.setFont(QFont(_FONT, 10))
            e.setFixedHeight(30)
            layout.addWidget(e)
            layout.addSpacing(4)
            return lbl, e

        _lbl("◈  SYSTEM CONFIGURATION", 13, True, C.PRI, Qt.AlignmentFlag.AlignCenter)
        _lbl("Keys stay on this computer (config/api_keys.json).", 8, color=C.PRI_DIM,
             align=Qt.AlignmentFlag.AlignCenter)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        _lbl("AI ENGINE")
        self._prov = QComboBox()
        self._prov.setFont(QFont(_FONT, 10))
        self._prov.setFixedHeight(30)
        for k, p in PROVIDERS.items():
            self._prov.addItem(p["label"], k)
        layout.addWidget(self._prov)
        layout.addSpacing(4)

        _, self._model   = _field("MODEL", secret=False)
        self._key_lbl, self._key = _field("ENGINE API KEY")
        self._url_lbl, self._url = _field("BASE URL", secret=False)
        _, self._gem = _field("GEMINI API KEY  ·  Gemini voice + vision/search tools")
        _, self._or  = _field("OPENROUTER API KEY  ·  memory + desktop tools")
        self._gem.setText(cfg.get("gemini_api_key", ""))
        self._or.setText(cfg.get("openrouter_api_key", ""))
        self._url.setText(cfg.get("base_url", ""))

        self._login = QPushButton("▸  SIGN IN WITH CLAUDE (browser)")
        self._login.setFont(QFont(_FONT, 10, QFont.Weight.Bold))
        self._login.setFixedHeight(30)
        self._login.setCursor(Qt.CursorShape.PointingHandCursor)
        self._login.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.PRI};"
                                  f" border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}"
                                  f" QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}")
        self._login.clicked.connect(self._sign_in)
        layout.addWidget(self._login)
        layout.addSpacing(6)

        # Applied on toggle: the login entry lives in the OS, not in api_keys.json.
        self._autostart = QCheckBox("START WITH SYSTEM  ·  open JARVIS when you log in")
        self._autostart.setFont(QFont(_FONT, 8))
        self._autostart.setStyleSheet(f"QCheckBox {{ color: {C.TEXT}; background: transparent; }}")
        self._autostart.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autostart.setChecked(autostart.enabled())
        self._autostart.toggled.connect(self._set_autostart)
        layout.addWidget(self._autostart)

        self._err = _lbl("", 8, color=C.RED, align=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()

        row = QHBoxLayout()
        for txt, slot, show in (("✕  CANCEL", self.cancelled.emit, can_cancel),
                                ("▸  SAVE & BOOT", self._submit, True)):
            if not show:
                continue
            b = QPushButton(txt)
            b.setFont(QFont(_FONT, 10, QFont.Weight.Bold))
            b.setFixedHeight(34)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
            """)
            b.clicked.connect(slot)
            row.addWidget(b)
        layout.addLayout(row)

        self._shown = None
        self._prov.currentIndexChanged.connect(self._on_provider)
        self._prov.setCurrentIndex(max(0, self._prov.findData(cfg.get("provider", "gemini"))))
        self._on_provider()

    def _on_provider(self):
        # Keep what was typed for the previous engine before swapping the fields.
        if self._shown:
            self._stash(self._shown)
        p = self._prov.currentData()
        info = PROVIDERS[p]
        own_key = info["key"] not in (None, "gemini_api_key", "openrouter_api_key")
        self._key_lbl.setVisible(own_key); self._key.setVisible(own_key)
        self._key_lbl.setText(f"{info['label'].upper()} API KEY")
        self._key.setText(self._cfg.get(info["key"] or "", ""))
        self._url_lbl.setVisible(p == "ollama"); self._url.setVisible(p == "ollama")
        self._login.setVisible(p == "claude_sub")
        self._model.setPlaceholderText(info["model"])
        self._model.setText(self._cfg.get("model", "") if p == self._cfg.get("provider", "gemini") else "")
        self._shown = p

    def _sign_in(self):
        from agent.llm import subscription_login
        threading.Thread(target=subscription_login, daemon=True).start()
        self._err.setText("Sign in with your Claude account in the browser, then SAVE & BOOT.")

    def _set_autostart(self, on: bool):
        try:
            autostart.set_enabled(on)
        except OSError as e:
            self._err.setText(f"Start with system failed: {e}")
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
            model=self._model.text().strip(),
            gemini_api_key=self._gem.text().strip(),
            openrouter_api_key=self._or.text().strip(),
            base_url=self._url.text().strip(),
            os_system={"darwin": "mac", "windows": "windows"}.get(_OS.lower(), "linux"),
        )
        need = PROVIDERS[p]["key"]
        if need and not cfg.get(need):
            self._err.setText(f"{PROVIDERS[p]['label']} needs its API key.")
            return
        self.done.emit(cfg)


class ConfigButton(QPushButton):
    """Header button to the settings: chamfered outline, painted slider icon, CONFIG label."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(92, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setToolTip("Engine, API keys and start with system")

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
                   Qt.AlignmentFlag.AlignCenter, "CONFIG")


class HudPanel(QWidget):
    """Side panel with cut corners and gold edge accents."""

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
        self.setWindowTitle(f"JARVIS — v{VERSION}")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command  = None
        self._muted           = False
        self._current_file: str | None = None

        central = QWidget()
        central.setStyleSheet(f"background: {C.BG};")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)

        self.hud = HudCanvas(face_path)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        body.addWidget(self.hud, stretch=5)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)

        root.addLayout(body, stretch=1)
        root.addWidget(self._build_footer())

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
        self._state_sig.connect(self._apply_state)
        self._task_sig.connect(self.hud.set_task)
        self._job_sig.connect(self.hud.set_job)
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

        if _OS == "Windows":
            # Dark Windows title bar with gold border (caption/border colours need Win 11).
            import ctypes
            hwnd = int(self.winId())
            for attr, val in ((20, 1), (35, 0x000D0907), (34, 0x0027B6FF)):
                v = ctypes.c_int(val)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._overlay and self._overlay.isVisible():
            self._place_overlay()

    def _update_metrics(self):
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        self._bar_mem.set_value(mem, f"{mem:.0f}%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        self._bar_net.set_value(net_pct, net_str)

        # GPU
        gpu = snap["gpu"]
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._bar_gpu.set_value(0, "N/A")

        # TMP
        tmp = snap["tmp"]
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
        else:
            self._bar_tmp.set_value(0, "N/A")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("UP  --:--")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"PROC  {proc_count}")
        except Exception:
            self._proc_lbl.setText("PROC  --")


    def _build_header(self) -> QWidget:
        w = QWidget(); w.setObjectName("hdr")
        w.setFixedHeight(66)
        w.setStyleSheet(f"#hdr {{ background: {C.DARK}; border-bottom: 2px solid qlineargradient("
                        f"x1:0, y1:0, x2:1, y2:0, stop:0 {C.ACC}, stop:0.5 {C.PRI}, stop:1 {C.ACC}); }}")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 8, 16, 8)

        def _badge(txt, color=C.TEXT_MED):
            l = QLabel(txt)
            l.setFont(QFont(_FONT, 8))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_badge(f"◢ STARK INDUSTRIES  //  v{VERSION}", C.ACC))
        lay.addStretch()

        mid = QVBoxLayout(); mid.setSpacing(1)
        title = QLabel("JARVIS")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tf = QFont(_FONT, 18, QFont.Weight.Bold)
        tf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 6)
        title.setFont(tf)
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        mid.addWidget(title)
        sub = QLabel("JUST A RATHER VERY INTELLIGENT SYSTEM")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setFont(QFont(_FONT, 7))
        sub.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        mid.addWidget(sub)
        lay.addLayout(mid)
        lay.addStretch()

        cfg_btn = ConfigButton()
        cfg_btn.clicked.connect(lambda: self._overlay or self._show_setup(can_cancel=True))
        lay.addWidget(cfg_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        lay.addSpacing(16)

        right_col = QVBoxLayout(); right_col.setSpacing(2)
        self._clock_lbl = QLabel("12:00:00 AM")
        self._clock_lbl.setFont(QFont(_FONT, 14, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        # Fixed width for the widest digits: proportional digits otherwise shift the CONFIG button every second.
        fm = self._clock_lbl.fontMetrics()
        self._clock_lbl.setFixedWidth(6 * max(fm.horizontalAdvance(d) for d in "0123456789")
                                      + fm.horizontalAdvance(":: ") + max(map(fm.horizontalAdvance, ("AM", "PM"))) + 6)
        right_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont(_FONT, 7))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._date_lbl)
        lay.addLayout(right_col)
        return w

    def _tick_clock(self):
        # AM/PM by hand: %p is empty or localized under some locales
        self._clock_lbl.setText(time.strftime("%I:%M:%S ") + ("AM" if time.localtime().tm_hour < 12 else "PM"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))

    def _build_left_panel(self) -> QWidget:
        w = HudPanel()
        w.setFixedWidth(_LEFT_W)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 16, 14, 16)
        lay.setSpacing(6)

        hdr = QLabel("◈ SYS MONITOR")
        hdr.setFont(QFont(_FONT, 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)
        lay.addSpacing(2)

        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("MEM", C.ACC2)
        self._bar_net = MetricBar("NET", C.GREEN)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_tmp = MetricBar("TMP", "#ff6688")

        for bar in [self._bar_cpu, self._bar_mem, self._bar_net,
                    self._bar_gpu, self._bar_tmp]:
            lay.addWidget(bar)

        lay.addSpacing(4)

        info_panel = QWidget()
        info_panel.setStyleSheet(
            f"background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 4px;"
        )
        ip_lay = QVBoxLayout(info_panel)
        ip_lay.setContentsMargins(6, 5, 6, 5)
        ip_lay.setSpacing(3)

        self._uptime_lbl = QLabel("UP  --:--")
        self._uptime_lbl.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
        self._uptime_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        ip_lay.addWidget(self._uptime_lbl)

        self._proc_lbl = QLabel("PROC  --")
        self._proc_lbl.setFont(QFont(_FONT, 8))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        ip_lay.addWidget(self._proc_lbl)

        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"OS  {os_name}")
        os_lbl.setFont(QFont(_FONT, 8))
        os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
        ip_lay.addWidget(os_lbl)

        lay.addWidget(info_panel)
        lay.addSpacing(4)

        ai_panel = QWidget()
        ai_panel.setStyleSheet(
            f"background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 4px;"
        )
        ai_lay = QVBoxLayout(ai_panel)
        ai_lay.setContentsMargins(6, 5, 6, 5)
        ai_lay.setSpacing(2)
        for attr, txt, font, col in (
            ("_ai_hdr",   "◈ AI CORE  --",   QFont(_FONT, 8, QFont.Weight.Bold), C.GREEN),
            ("_ai_tok",   "IN 0  ·  OUT 0",  QFont(_MONO, 7),                     C.ARC),
        ):
            l = QLabel(txt); l.setFont(font); l.setWordWrap(True)
            l.setStyleSheet(f"color: {col}; background: transparent; border: none;")
            ai_lay.addWidget(l)
            setattr(self, attr, l)
        # Brain picker: model (any id can be typed) + reasoning effort. APPLY saves and reboots.
        combo_css = (f"QComboBox {{ background: #000d12; color: {C.TEXT}; border: 1px solid {C.BORDER};"
                     f" border-radius: 3px; padding: 1px 4px; }}"
                     f" QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT};"
                     f" selection-background-color: {C.PRI_GHO}; }}")
        self._ai_model, self._ai_effort = QComboBox(), QComboBox()
        self._ai_model.setEditable(True)
        self._ai_effort.setToolTip("Reasoning effort")
        for cb in (self._ai_model, self._ai_effort):
            cb.setFont(QFont(_FONT, 7))
            cb.setStyleSheet(combo_css)
            cb.currentTextChanged.connect(self._ai_changed)
            ai_lay.insertWidget(ai_lay.count() - 1, cb)
        self._ai_apply = QPushButton("▸ APPLY")
        self._ai_apply.setFont(QFont(_FONT, 7, QFont.Weight.Bold))
        self._ai_apply.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ai_apply.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.PRI};"
                                     f" border: 1px solid {C.PRI_DIM}; border-radius: 3px; padding: 2px; }}"
                                     f" QPushButton:hover {{ background: {C.PRI_GHO}; }}")
        self._ai_apply.clicked.connect(self._ai_save)
        self._ai_apply.hide()
        ai_lay.insertWidget(ai_lay.count() - 1, self._ai_apply)
        # FAST: quick helper calls (planner check, error analyst) skip the effort. Saved live, no reboot.
        self._ai_fast = QCheckBox("FAST helpers")
        self._ai_fast.setFont(QFont(_FONT, 7))
        self._ai_fast.setStyleSheet(f"QCheckBox {{ color: {C.TEXT}; }}")
        self._ai_fast.setToolTip("On: quick helper calls use the model's default effort.\n"
                                 "Off: they use the effort picked above too.")
        self._ai_fast.setChecked(self._load_cfg().get("fast", True))
        self._ai_fast.toggled.connect(self._ai_fast_set)
        ai_lay.insertWidget(ai_lay.count() - 1, self._ai_fast)
        self._ai_now = None
        self._tok_in = self._tok_out = 0
        lay.addWidget(ai_panel)
        lay.addStretch()
        return w
    def _build_right_panel(self) -> QWidget:
        w = HudPanel()
        w.setFixedWidth(_RIGHT_W)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 16, 14, 16)
        lay.setSpacing(6)

        def _sec(txt):
            l = QLabel(f"▸ {txt}")
            l.setFont(QFont(_FONT, 7, QFont.Weight.Bold))
            l.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            return l

        lay.addWidget(_sec("ACTIVITY LOG"))
        self._log = LogWidget()
        lay.addWidget(self._log, stretch=1)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_sec("FILE UPLOAD"))
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        self._file_hint = QLabel("No file loaded — drop or click above to upload")
        self._file_hint.setFont(QFont(_FONT, 7))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._file_hint.setWordWrap(True)
        lay.addWidget(self._file_hint)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_sec("COMMAND INPUT"))
        lay.addLayout(self._build_input_row())

        self._mute_btn = QPushButton("🎙  MICROPHONE ACTIVE")
        self._mute_btn.setFixedHeight(30)
        self._mute_btn.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        lay.addWidget(self._mute_btn)

        fs_btn = QPushButton("⛶  FULLSCREEN  [F11]")
        fs_btn.setFixedHeight(26)
        fs_btn.setFont(QFont(_FONT, 7))
        fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fs_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{
                color: {C.PRI}; border: 1px solid {C.BORDER_B};
            }}
        """)
        fs_btn.clicked.connect(self._toggle_fullscreen)
        lay.addWidget(fs_btn)

        return w

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(5)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command or question…")
        self._input.setFont(QFont(_FONT, 9))
        self._input.setFixedHeight(30)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d14; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 7px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        send = QPushButton("▸")
        send.setFixedSize(30, 30)
        send.setFont(QFont(_FONT, 11, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def _build_footer(self) -> QWidget:
        w = QWidget(); w.setObjectName("ftr")
        w.setFixedHeight(22)
        w.setStyleSheet(f"#ftr {{ background: {C.DARK}; border-top: 1px solid {C.BORDER_B}; }}")
        lay = QHBoxLayout(w); lay.setContentsMargins(14, 0, 14, 0)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(txt); l.setFont(QFont(_FONT, 7))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("[F4] Mute  ·  [F11] Fullscreen"))
        lay.addStretch()
        lay.addWidget(_fl(f"JARVIS v{VERSION}  ·  CLASSIFIED"))
        lay.addStretch()
        lay.addWidget(_fl("© STARK INDUSTRIES", C.PRI_DIM))
        return w

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"{icon}  {p.name}  ·  {size}  ·  Tell JARVIS what to do with it")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
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
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇  MICROPHONE MUTED")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #140006; color: {C.MUTED_C};
                    border: 1px solid {C.MUTED_C}; border-radius: 3px;
                }}
            """)
        else:
            self._mute_btn.setText("🎙  MICROPHONE ACTIVE")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #00140a; color: {C.GREEN};
                    border: 1px solid {C.GREEN}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #001f10; }}
            """)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")

    def _set_ai(self, label: str, model: str):
        self._ai_hdr.setText(f"◈ AI CORE  {label.upper()}")
        info = PROVIDERS.get(self._load_cfg().get("provider", "gemini"), PROVIDERS["gemini"])
        self._ai_now = (model, get_effort() or "")
        for cb, items, cur in ((self._ai_model, info.get("models") or [info["model"]], model),
                               (self._ai_effort, info.get("efforts") or [], self._ai_now[1])):
            cb.blockSignals(True)
            cb.clear()
            cb.addItems(items if cur in items or not cur else [*items, cur])
            cb.setCurrentText(cur)
            cb.blockSignals(False)
        self._ai_effort.setVisible(bool(info.get("efforts")))
        self._ai_apply.hide()

    def _ai_changed(self, _=None):
        pick = (self._ai_model.currentText().strip(), self._ai_effort.currentText())
        self._ai_apply.setVisible(bool(self._ai_now and pick[0]) and pick != self._ai_now)

    def _ai_save(self):
        cfg = self._load_cfg()
        cfg["model"] = self._ai_model.currentText().strip()
        if self._ai_effort.currentText():
            cfg["effort"] = self._ai_effort.currentText()
        self._on_setup_done(cfg)   # writes api_keys.json and reboots onto the new brain

    def _ai_fast_set(self, on: bool):
        cfg = self._load_cfg()
        if not cfg:   # unreadable file: never overwrite the keys with just this flag
            return
        cfg["fast"] = on
        API_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")

    def _add_usage(self, tin: int, tout: int):
        self._tok_in += tin; self._tok_out += tout
        k = lambda n: f"{n / 1000:.1f}k" if n >= 1000 else str(n)
        self._ai_tok.setText(f"IN {k(self._tok_in)}  ·  OUT {k(self._tok_out)}")

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
        ow, oh = 480, min(560, cw.height() - 20)
        self._overlay.setGeometry((cw.width() - ow) // 2, (cw.height() - oh) // 2, ow, oh)

    def _show_setup(self, can_cancel: bool):
        self._overlay = ov = SettingsOverlay(self._load_cfg(), can_cancel, self.centralWidget())
        self._place_overlay()
        ov.done.connect(self._on_setup_done)
        ov.cancelled.connect(self._close_overlay)
        ov.show()

    def _close_overlay(self):
        self._overlay.deleteLater()
        self._overlay = None

    def _on_setup_done(self, cfg: dict):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
        self._close_overlay()
        if self._ready:
            # The engine reads its config once at boot: restart the whole app.
            self._log.append_log("SYS: Config saved. Rebooting JARVIS...")
            # sys.executable, not orig_argv[0]: under a uv venv that is the base python, without the
            # venv's packages, so the new JARVIS died at `import numpy` and never came back.
            subprocess.Popen([sys.executable, *sys.orig_argv[1:]], cwd=os.getcwd())
            QTimer.singleShot(300, lambda: os._exit(0))
            return
        self._ready = True
        self._apply_state("LISTENING")
        self._log.append_log(f"SYS: Initialised. Engine={cfg['provider'].upper()}. JARVIS online.")

class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        if _OS == "Windows":
            import ctypes  # own taskbar group, so Windows shows our icon instead of python's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Stark.Jarvis.MarkXXXIX")
        self._app.setWindowIcon(QIcon(str(BASE_DIR / "jarvis.ico")))
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
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

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
        if not self.muted:
            self.set_state("LISTENING")