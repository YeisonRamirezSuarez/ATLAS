"""ACCOUNT tab controls write the real config, and SAVE & START does not undo them (temp config file)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication

import config
import ui

app = QApplication.instance() or QApplication([])


class AtlasIdTabTests(unittest.TestCase):
    def test_id_tab_saves_and_save_and_boot_keeps_it(self):
        path = Path(tempfile.mkdtemp()) / "api_keys.json"
        path.write_text(json.dumps({"provider": "gemini", "gemini_api_key": "test-key"}), encoding="utf-8")
        from memory import atlas_id
        with mock.patch.object(config, "_CONFIG_PATH", path), mock.patch.object(ui, "API_FILE", path), \
             mock.patch.object(atlas_id, "SESSION_FILE", path.with_name("s.session")), \
             mock.patch.object(atlas_id, "_session", None):
            ov = ui.SettingsOverlay(json.loads(path.read_text()), can_cancel=True)
            self.assertFalse(hasattr(ov, "_sb_key"))   # the project ships with ATLAS: nothing to paste
            ov._dev_name.setText("Studio PC")
            ov._id_save()
            ov._sync_box.setChecked(True)
            self.assertIn("No has iniciado sesión", ov._id_status.text())
            got = []
            ov.done.connect(got.append)
            ov._submit()   # the panel's own copy of the config predates the ID tab changes
            saved = json.loads(path.read_text())
        self.assertEqual(saved["device_name"], "Studio PC")
        self.assertTrue(saved["memory_sync"])
        self.assertEqual(got[0]["device_name"], "Studio PC")
        self.assertTrue(got[0]["memory_sync"])


class VoiceTests(unittest.TestCase):
    def test_each_change_previews_and_default_restores_atlas(self):
        cfg = {"provider": "gemini", "gemini_api_key": "k", "voice_name": "Puck", "voice_speed": "rapida",
               "voice_volume": 40}
        played = []
        with mock.patch("voice.read_aloud", mock.AsyncMock(return_value=b"\x10\x00" * 4)), \
             mock.patch("sounddevice.play", side_effect=lambda a, sr: played.append(a.tolist())), \
             mock.patch.object(ui.threading, "Thread", side_effect=lambda target, daemon: mock.Mock(start=target)):
            ov = ui.SettingsOverlay(cfg, can_cancel=True)
            self.assertEqual((ov._voice.currentData(), ov._speed.currentData(), ov._volume.value()), ("Puck", "rapida", 40))
            ov._speed.setCurrentIndex(ov._speed.findData("lenta"))
            self.assertEqual(played, [[6] * 4])   # 16 at 40% volume
            ov._default_voice()
            got = []
            ov.done.connect(got.append)
            ov._submit()
        self.assertEqual(len(played), 2)   # one sample per change, the reset included
        self.assertEqual((got[0]["voice_name"], got[0]["voice_speed"], got[0]["voice_volume"]), ("Charon", "normal", 100))


class ThemeTests(unittest.TestCase):
    def test_palettes_map_one_to_one(self):   # restyle() maps colours by value
        for a, b in ((ui._DARK, ui._LIGHT), (ui._LIGHT, ui._DARK)):
            seen = {}
            for k, v in a.items():
                if k != "THEME":
                    self.assertEqual(seen.setdefault(v.lower(), b[k]), b[k], k)

    def test_bundled_fonts_load(self):   # else every label falls back to the system face
        from PyQt6.QtGui import QFontDatabase
        ui._load_fonts()
        self.assertEqual((ui._FONT, ui._DISPLAY), ("Chakra Petch", "Orbitron"))
        self.assertIn(ui._FONT, QFontDatabase.families())

    def test_hud_qml_loads(self):   # a QML error would silently drop to the classic painted core
        from PyQt6.QtQml import QQmlComponent, QQmlEngine
        hud = ui.HudCanvas()
        self.assertIsNone(hud._qml)   # offscreen: classic core
        engine = QQmlEngine()
        engine.rootContext().setContextProperty("hud", hud._bridge)
        comp = QQmlComponent(engine, ui.QUrl.fromLocalFile(str(ui.BASE_DIR / "qml" / "hud.qml")))
        obj = comp.create()
        self.assertIsNotNone(obj, [e.toString() for e in comp.errors()])
        hud._push()   # every key the QML reads is in the map
        self.assertTrue({"boot", "status", "arc", "acc", "core", "ring"} <= set(hud._bridge.s))
        # per-frame values live in QML now: a push is only a change of state, never every frame
        self.assertFalse({"wave", "glow", "scale", "scan"} & set(hud._bridge.s))

    def test_error_line_turns_the_core_amber_for_a_while(self):
        hud = ui.HudCanvas()
        hud.state = "LISTENING"
        hud.flash_error(secs=60)
        hud._push()
        st = hud._bridge.s
        self.assertEqual((st["state"], st["arc"], st["acc"]), ("ERROR", ui.C.AMBER, ui.C.AMBER))
        self.assertIn("ERROR", st["status"])
        hud._err_until = 0   # over: back to the normal look
        hud._push()
        self.assertEqual((hud._bridge.s["state"], hud._bridge.s["arc"]), ("LISTENING", ui.C.ARC))
        hud.resize(400, 400)
        hud.grab()   # the painted fallback core draws without errors

    def test_mic_off_still_shows_speaking_and_thinking(self):
        hud = ui.HudCanvas()
        hud.muted, hud.state, hud.speaking = True, "SPEAKING", True
        hud._push()
        self.assertEqual((hud._bridge.s["state"], hud._bridge.s["muted"]), ("SPEAKING", False))
        hud.state, hud.speaking = "THINKING", False
        hud._push()
        self.assertEqual(hud._bridge.s["state"], "THINKING")
        hud.state = "MUTED"   # idle: the red SILENCIADO look
        hud._push()
        self.assertEqual((hud._bridge.s["state"], hud._bridge.s["muted"]), ("MUTED", True))
        self.assertIn("SILENCIADO", hud._bridge.s["status"])

    def test_chat_qml_takes_messages(self):
        from PyQt6.QtCore import QObject
        from PyQt6.QtQml import QQmlComponent, QQmlEngine
        view = ui.ChatView()
        self.assertIsNotNone(view._log)   # offscreen: the plain typed log
        engine = QQmlEngine()
        engine.rootContext().setContextProperty("chat", view._bridge)
        comp = QQmlComponent(engine, ui.QUrl.fromLocalFile(str(ui.BASE_DIR / "qml" / "chat.qml")))
        root = comp.create()
        self.assertIsNotNone(root, [e.toString() for e in comp.errors()])
        view._log, got = None, []   # as with Qt Quick loaded
        view._bridge.message.connect(lambda *a: got.append(a))
        for line in ("Tú: hola", "Atlas: Hola, ¿qué hacemos?", "SIS: Motor=Gemini (modelo: x)", "ERROR: no hay micrófono"):
            view.append_log(line)
        self.assertEqual([(k, b) for k, b, _ in got], [("you", "hola"), ("ai", "Hola, ¿qué hacemos?"),
                                                      ("sys", "Motor=Gemini (modelo: x)"), ("err", "ERROR: no hay micrófono")])
        self.assertEqual(root.findChild(QObject, "list").property("count"), 4)
        view.clear()
        self.assertEqual(root.findChild(QObject, "list").property("count"), 0)

    def test_palette_filters_runs_and_asks(self):
        from PyQt6.QtCore import Q_ARG, QMetaObject, QObject
        from PyQt6.QtQml import QQmlComponent, QQmlEngine
        bridge, ran, asked = ui.PaletteBridge(), [], []
        bridge.run.connect(ran.append)
        bridge.ask.connect(asked.append)
        bridge.set([{"id": "theme", "title": "Switch to light theme", "group": "Look", "hint": ""},
                    {"id": "mute", "title": "Mute microphone", "group": "Voice", "hint": "F4"}], ui._qml_theme())
        engine = QQmlEngine()
        engine.rootContext().setContextProperty("cmd", bridge)
        comp = QQmlComponent(engine, ui.QUrl.fromLocalFile(str(ui.BASE_DIR / "qml" / "palette.qml")))
        root = comp.create()
        self.assertIsNotNone(root, [e.toString() for e in comp.errors()])
        QMetaObject.invokeMethod(root, "open")
        field = root.findChild(QObject, "field")
        field.setProperty("text", "voice")   # group names match too
        self.assertEqual([c["id"] for c in root.property("shown").toVariant()], ["mute", "ask"])
        QMetaObject.invokeMethod(root, "pick", Q_ARG("QVariant", 0))
        field.setProperty("text", "qué hora es")
        QMetaObject.invokeMethod(root, "pick", Q_ARG("QVariant", 0))
        self.assertEqual((ran, asked), (["mute"], ["qué hora es"]))

    def test_jobs_toast_once_on_start_and_on_end(self):
        hud, got = ui.HudCanvas(), []
        hud._bridge.toast.connect(lambda *a: got.append(a))
        hud.set_job("j1", "index photos", "step 1")
        hud.set_job("j1", "index photos", "step 2")   # progress: no new toast
        hud.set_job("j1", "", "")
        hud.set_job("j1", "", "")                     # already gone: nothing
        self.assertEqual(got, [("start", "Trabajando en segundo plano: index photos"), ("done", "Terminado: index photos")])

    def test_system_panel_folds_and_remembers(self):
        path = Path(tempfile.mkdtemp()) / "api_keys.json"
        path.write_text(json.dumps({"provider": "gemini", "gemini_api_key": "k"}), encoding="utf-8")
        with mock.patch.object(ui, "API_FILE", path):
            win = ui.MainWindow("")
            try:
                self.assertTrue(win._left_open)
                win._toggle_left()
                self.assertFalse(win._left_open)
                self.assertTrue(json.loads(path.read_text())["panel_collapsed"])
                self.assertIn("Mostrar panel del sistema", [c["title"] for c in win._commands()])
                again = ui.MainWindow("")   # next start opens folded
                self.assertFalse(again._left_open)
                self.assertEqual(again._left_panel.width(), 34)
                again.deleteLater()
            finally:
                win.deleteLater()

    def test_ai_tab_saves_model_effort_and_fast(self):
        path = Path(tempfile.mkdtemp()) / "api_keys.json"
        cfg = {"provider": "claude_sub", "model": "", "effort": "high", "fast": True}
        path.write_text(json.dumps(cfg), encoding="utf-8")
        with mock.patch.object(ui, "API_FILE", path):
            ov = ui.SettingsOverlay(dict(cfg), can_cancel=True)
            self.assertEqual(ov._effort.currentData(), "high")
            self.assertGreater(ov._model.count(), 1)   # the engine's model list, not a blank field
            ov._model.setCurrentText(ov._model.itemText(1))
            ov._effort.setCurrentIndex(ov._effort.findData("medium"))   # shown as "Medio"
            ov._fast.setChecked(False)
            got = []
            ov.done.connect(got.append)
            ov._submit()
        self.assertEqual((got[0]["model"], got[0]["effort"], got[0]["fast"]), (ov._model.itemText(1), "medium", False))

    def test_memory_qml_loads_a_graph(self):
        from PyQt6.QtCore import QObject
        from PyQt6.QtQml import QQmlComponent, QQmlEngine
        from memory import knowledge
        bridge = ui.MemoryBridge()
        graph = knowledge.build_graph({"identity": {"name": {"value": "Pablo", "updated": "2026-10-20"}},
                                       "projects": {"atlas": {"value": "voice assistant", "updated": "2026-10-26"}}}, {})
        bridge._loaded(graph, "")
        bridge._theme = ui._qml_theme()
        engine = QQmlEngine()
        engine.rootContext().setContextProperty("mem", bridge)
        comp = QQmlComponent(engine, ui.QUrl.fromLocalFile(str(ui.BASE_DIR / "qml" / "memory.qml")))
        root = comp.create()
        self.assertIsNotNone(root, [e.toString() for e in comp.errors()])
        self.assertEqual(root.property("factCount"), 2)
        closed = []
        bridge.close.connect(lambda: closed.append(1))
        bridge.clear()
        self.assertEqual(root.property("factCount"), 0)   # locked: nothing left in the view

    def test_memory_tab_pin_flow(self):
        path = Path(tempfile.mkdtemp()) / "api_keys.json"
        path.write_text(json.dumps({"provider": "gemini", "gemini_api_key": "k"}), encoding="utf-8")
        with mock.patch.object(config, "_CONFIG_PATH", path), mock.patch.object(ui, "API_FILE", path):
            ov = ui.SettingsOverlay(json.loads(path.read_text()), can_cancel=True)
            opened = []
            ov.open_memory.connect(lambda: opened.append(1))
            ov._pin1.setText("4821"); ov._pin2.setText("4822")
            ov._mem_set_pin()
            self.assertIn("no coinciden", ov._mem_err.text())
            ov._pin2.setText("4821")
            ov._mem_set_pin()
            self.assertNotIn("4821", path.read_text())
            ov._pin.setText("0000"); ov._mem_open()
            self.assertEqual(opened, [])
            ov._pin.setText("4821"); ov._mem_open()
            self.assertEqual(opened, [1])

    def test_theme_click_applies_live_and_save_does_not_restart(self):
        path = Path(tempfile.mkdtemp()) / "api_keys.json"
        path.write_text(json.dumps({"provider": "gemini", "gemini_api_key": "test-key"}), encoding="utf-8")
        with mock.patch.object(ui, "API_FILE", path), mock.patch.object(ui, "relaunch") as relaunch:
            first = []   # a config saved once by SAVE & START, as on any real install
            first_ov = ui.SettingsOverlay(json.loads(path.read_text()), can_cancel=True)
            first_ov.done.connect(first.append)
            first_ov._submit()
            path.write_text(json.dumps(first[0]), encoding="utf-8")
            win = ui.MainWindow("")
            win._log._log.setHtml(f"<span style='color:{ui.C.ARC}'>hi</span>")
            win._show_setup(can_cancel=True)
            ov = win._overlay
            try:
                ov._pick_theme("light")
                self.assertEqual(json.loads(path.read_text())["theme"], "light")
                self.assertIn(ui._LIGHT["OVERLAY"], ov.styleSheet())
                self.assertNotIn(ui._DARK["BG"], win.centralWidget().styleSheet())
                self.assertIn(ui._LIGHT["ARC"], win._log._log.toHtml())
                self.assertEqual(win._bar_cpu._color, ui._LIGHT["PRI"])
                ov._submit()   # nothing else changed
                relaunch.assert_not_called()
                self.assertIsNone(win._overlay)
            finally:
                ui.restyle("dark")
                win.deleteLater()


if __name__ == "__main__":
    unittest.main()
