import sys, json
import math
import time
import numpy as np
from pid import PIDManager, PIDLoopDef
import os

from PyQt6 import QtCore, QtWidgets
def _ro_flags(flags):
    try:
        # PyQt6
        return flags & ~QtCore.Qt.ItemFlag.ItemIsEditable
    except AttributeError:
        # PyQt5
        return flags & ~QtCore.Qt.ItemIsEditable

# ---- Qt5/Qt6 dialog helpers ----
def _dlg_exec(dlg):
    try:
        return dlg.exec()          # PyQt6
    except AttributeError:
        return dlg.exec_()         # PyQt5

try:
    DLG_ACCEPTED = QtWidgets.QDialog.DialogCode.Accepted  # PyQt6
except Exception:
    DLG_ACCEPTED = QtWidgets.QDialog.Accepted             # PyQt5

from typing import Optional
from config_manager import ConfigManager, AppConfig
from daq_driver import DaqDriver, DaqError
from filters import OnePoleLPF
from analog_chart import AnalogChartWindow
from digital_chart import DigitalChartWindow
from script_runner import ScriptRunner
from config_editor import ConfigEditorDialog
from script_editor import ScriptEditorDialog
from collections import deque
from acq_worker import AcqWorker
from combined_chart import CombinedChartWindow
from bisect import bisect_left

class PIDSetupDialog(QtWidgets.QDialog):
    COLS = ["Enable","Type","AI ch","OUT ch","Target","P","I","D"]

    def __init__(self, parent=None, existing=None):
        super().__init__(parent)
        self.setWindowTitle("PID Setup")
        self.resize(800, 360)
        v = QtWidgets.QVBoxLayout(self)
        self.table = QtWidgets.QTableWidget(8, len(self.COLS), self)
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.table)

        btns = QtWidgets.QHBoxLayout()
        self.btn_load = QtWidgets.QPushButton("Load PID.json")
        self.btn_save = QtWidgets.QPushButton("Save PID.json")
        self.btn_ok = QtWidgets.QPushButton("OK")
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        btns.addWidget(self.btn_load); btns.addWidget(self.btn_save)
        btns.addStretch(1)
        btns.addWidget(self.btn_ok); btns.addWidget(self.btn_cancel)
        v.addLayout(btns)

        self.btn_load.clicked.connect(self._on_load)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

        # Pre-fill from existing PIDManager.loops
        if existing:
            for r, lp in enumerate(existing[:8]):
                self._set_row(r, lp)

    def _set_row(self, r, lp):
        def cb(val, checked=True):  # helper
            w = QtWidgets.QCheckBox(); w.setChecked(val); return w
        def combo(val):
            w = QtWidgets.QComboBox(); w.addItems(["digital","analog"])
            w.setCurrentText(val); return w
        def spin(v, lo=0, hi=7, step=1):
            w = QtWidgets.QSpinBox(); w.setRange(lo, hi); w.setValue(int(v)); w.setSingleStep(step); return w
        def dspin(v, lo=-1e9, hi=1e9, step=0.1):
            w = QtWidgets.QDoubleSpinBox(); w.setRange(lo, hi); w.setDecimals(4); w.setSingleStep(step); w.setValue(float(v)); return w

        items = [
            cb(lp.enabled),
            combo(lp.kind),
            spin(lp.ai_ch, 0, 7, 1),
            spin(lp.out_ch, 0, 7 if lp.kind=="digital" else 1, 1),
            dspin(lp.target),
            dspin(lp.kp), dspin(lp.ki), dspin(lp.kd)
        ]
        for c, w in enumerate(items):
            self.table.setCellWidget(r, c, w)

    def _row_to_def(self, r) -> Optional[dict]:
        def getw(c): return self.table.cellWidget(r, c)
        if not self.table.cellWidget(r, 0):  # empty row
            return None
        enabled = getw(0).isChecked()
        kind = getw(1).currentText()
        ai = getw(2).value()
        out = getw(3).value()
        target = getw(4).value()
        kp = getw(5).value(); ki = getw(6).value(); kd = getw(7).value()
        d = dict(enabled=enabled, kind=kind, ai_ch=ai, out_ch=out, target=target, kp=kp, ki=ki, kd=kd)
        if kind == "analog":
            d["out_min"] = -10.0
            d["out_max"] = 10.0
        return d

    def values(self):
        vals = []
        for r in range(self.table.rowCount()):
            d = self._row_to_def(r)
            if d is not None:
                # Ignore completely empty (all defaults) rows
                if (not d["enabled"]) and d["kp"]==0 and d["ki"]==0 and d["kd"]==0:
                    continue
                vals.append(d)
        return vals

    def _on_load(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load PID.json", "", "JSON (*.json)")
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as f:
                js = json.load(f)
            loops = js.get("loops", [])
            self.table.clearContents()
            self.table.setRowCount(max(8, len(loops)))
            for r, item in enumerate(loops[:8]):
                from pid import PIDLoopDef
                self._set_row(r, PIDLoopDef(**item))
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "PID load error", str(e))

    def _on_save(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save PID.json", "PID.json", "JSON (*.json)")
        if not path: return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"loops": self.values()}, f, indent=2)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "PID save error", str(e))

class ScaleDialog(QtWidgets.QDialog):
    def __init__(self, parent, idx, y_min, y_max):
        super().__init__(parent)
        self.setWindowTitle(f"Scale AI{idx}")
        self.setModal(True)
        form = QtWidgets.QFormLayout(self)
        self.chk_auto = QtWidgets.QCheckBox("Auto-scale")
        self.chk_auto.setChecked(False)
        form.addRow(self.chk_auto)

        def mk(v):
            sp = QtWidgets.QDoubleSpinBox()
            sp.setRange(-1e12, 1e12)
            sp.setDecimals(6)
            sp.setSingleStep(0.1)
            sp.setKeyboardTracking(True)
            sp.setAccelerated(False)
            sp.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.UpDownArrows)
            sp.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
            sp.lineEdit().setReadOnly(False)
            sp.setValue(float(v))
            return sp

        self.sp_min = mk(y_min)
        self.sp_max = mk(y_max)
        form.addRow("Y min", self.sp_min)
        form.addRow("Y max", self.sp_max)
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)
        self.chk_auto.toggled.connect(self._on_auto)
        self._on_auto(self.chk_auto.isChecked())

    def _on_auto(self, checked: bool):
        self.sp_min.setDisabled(checked)
        self.sp_max.setDisabled(checked)

    def result_values(self):
        return self.chk_auto.isChecked(), self.sp_min.value(), self.sp_max.value()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MCC E-1608 Control")
        self.resize(1300, 800)

        # Core state
        self.cfg = AppConfig()
        self.daq = None
        self.ai_filters = [OnePoleLPF(0.0, self.cfg.sampleRateHz) for _ in range(8)]
        self.ai_filter_enabled = [False] * 8
        self.ai_hist_x = []
        self.ai_hist_y = [[] for _ in range(8)]
        self.do_hist_y = [[] for _ in range(8)]
        self.time_window_s = 5.0
        self.ui_rate_hz = 50.0
        self.sample_period = 1.0 / max(1e-6, self.cfg.sampleRateHz)
        self._history_headroom = 1.25  # 25% margin
        self._rebuild_histories_for_span()  # NEW: create deques sized for current span & sample rate
        self.script_events = []

        self.do_state = [False] * 8  # <-- add: current DO state for plotting

        # Windows
        self.analog_win = AnalogChartWindow(
            [a.name for a in self.cfg.analogs],
            [a.units for a in self.cfg.analogs],
        )
        self.analog_win.requestScale.connect(self._on_request_scale)
        self.digital_win = DigitalChartWindow()

        # Combined chart window
        # Collect AO names/units robustly from current config
        def _ao_field(name, default=""):
            return getattr(self.cfg, name, default)
        ao_names = []
        ao_units = []
        if hasattr(self.cfg, "aouts"):
            for i in range(2):
                nm = getattr(self.cfg.aouts[i], "name", f"AO{i}") if i < len(self.cfg.aouts) else f"AO{i}"
                un = getattr(self.cfg.aouts[i], "units", "")
                ao_names.append(nm); ao_units.append(un)
        else:
            for i in range(2):
                nm = _ao_field(f"ao{i}Name", f"AO{i}")
                un = _ao_field(f"ao{i}Units", "")
                ao_names.append(nm); ao_units.append(un)

        self.combined_win = CombinedChartWindow(
            ai_names=[a.name for a in self.cfg.analogs],
            ai_units=[a.units for a in self.cfg.analogs],
            ao_names=ao_names,
            ao_units=ao_units,
            ao_default_range=(0.0, 10.0),
        )

        # Build UI
        self._build_menu()
        self._build_central()
        self._build_status_panes()
        # Try to load default config/script automatically at startup
        try:
            import os
            if os.path.exists("config.json"):
                self._act_load_cfg("config.json")
                self.log_rx("[INFO] Loaded default config.json")
            else:
                self.log_rx("[INFO] No default config.json found")

            if os.path.exists("script.json"):
                self._act_load_script("script.json")
                self.log_rx("[INFO] Loaded default script.json")
            else:
                self.log_rx("[INFO] No default script.json found")
        except Exception as e:
            self.log_rx(f"[WARN] Could not auto-load defaults: {e}")

        self.pid_mgr = PIDManager(self)
        # Try to autoload PID.json (optional)
        try:
            if os.path.exists("PID.json"):
                self.pid_mgr.load_file("PID.json")
                self.log_rx("[INFO] Loaded PID.json")
        except Exception as e:
            self.log_rx(f"[WARN] PID.json load failed: {e}")

        # Build/refresh the PID status table once at startup
        self._pid_refresh_table_structure()
        self._pid_update_table_values()

        # Timers
        self.loop_timer = QtCore.QTimer(self)
        self.loop_timer.timeout.connect(self._loop)
        self.loop_timer.start(int(1000 / self.ui_rate_hz))

        # Render timer decoupled from acquisition
        self.render_rate_hz = 25.0
        self.render_timer = QtCore.QTimer(self)
        self.render_timer.timeout.connect(self._render)
        self.render_timer.start(int(1000 / self.render_rate_hz))

        # Queues, worker, script
        self._chunk_queue = deque()
        self.acq_thread = None
        self.script = ScriptRunner(self._set_do)
        self.script.tick.connect(self._on_script_tick)

        # Apply config to UI and titles
        self._apply_cfg_to_ui()
        self.analog_win.set_names_units(
            [a.name for a in self.cfg.analogs],
            [a.units for a in self.cfg.analogs],
        )

        # AO histories and defaults
        self.ao_hist_y = [[], []]
        def _ao_default(i, fallback=0.0):
            if hasattr(self.cfg, "aouts"):
                v = getattr(self.cfg.aouts[i], "startupV", None) if i < len(self.cfg.aouts) else None
                if v is None:
                    v = getattr(self.cfg.aouts[i], "default", None) if i < len(self.cfg.aouts) else None
                return float(v) if v is not None else fallback
            v = getattr(self.cfg, f"ao{i}Default", None)
            return float(v) if v is not None else fallback
        self.ao_value = [_ao_default(0, 0.0), _ao_default(1, 0.0)]

        # hook up span sync from both windows (after you create the windows)
        self.analog_win.spanChanged.connect(self._on_span_changed)
        self.combined_win.spanChanged.connect(self._on_span_changed)

        # also push the current span into both windows once
        self.analog_win.set_span(self.time_window_s)
        self.combined_win.set_span(self.time_window_s)

    def _effective_rate_hz(self) -> float:
        # prefer actual; fall back to requested
        try:
            return 1.0 / float(self.sample_period) if self.sample_period > 0 else float(self.cfg.sampleRateHz)
        except Exception:
            return float(getattr(self.cfg, "sampleRateHz", 1000.0))

    def _target_history_len(self, span_s: float) -> int:
        rate = max(1.0, self._effective_rate_hz())
        return int(math.ceil(rate * float(span_s) * self._history_headroom))

    def _rebuild_histories_for_span(self):
        """Ensure histories can hold the full span at current rate (with headroom)."""
        cap = max(256, self._target_history_len(self.time_window_s))

        # rebuild as deques while preserving tail
        def redq(seq, maxlen):
            if isinstance(seq, deque):
                data = list(seq)
            else:
                data = list(seq)
            return deque(data[-maxlen:], maxlen=maxlen)

        # X history
        self.ai_hist_x = redq(getattr(self, "ai_hist_x", []), cap)

        # AI
        self.ai_hist_y = [redq(ch, cap) for ch in getattr(self, "ai_hist_y", [[] for _ in range(8)])]

        # DO
        self.do_hist_y = [redq(ch, cap) for ch in getattr(self, "do_hist_y", [[] for _ in range(8)])]

        # AO (if you track them)
        self.ao_hist_y = [redq(ch, cap) for ch in getattr(self, "ao_hist_y", [[] for _ in range(2)])]

    def _on_span_changed(self, seconds: float):
        """User changed X-span in either window: sync both + ensure buffers can hold it."""
        self.time_window_s = float(seconds)
        # sync spinboxes without feedback
        if hasattr(self, "analog_win"):   self.analog_win.set_span(self.time_window_s)
        if hasattr(self, "combined_win"): self.combined_win.set_span(self.time_window_s)

        # (optional) if you have a span control on the main page, update it too:
        # if hasattr(self, "sp_timewin"):
        #     self.sp_timewin.blockSignals(True)
        #     self.sp_timewin.setValue(self.time_window_s)
        #     self.sp_timewin.blockSignals(False)

        # Make sure histories are large enough for the new span
        self._rebuild_histories_for_span()

    def _build_menu(self):
        m=self.menuBar(); f=m.addMenu("&File")
        f.addAction("Load Config...", self._act_load_cfg);
        f.addAction("Save Config As...", self._act_save_cfg);
        f.addAction("Edit Config...", self._act_edit_cfg)
        f.addSeparator();
        f.addAction("Load Script...", self._act_load_script);
        f.addAction("Save Script As...", self._act_save_script);
        f.addAction("Edit Script...", self._act_edit_script)
        f.addSeparator();
        f.addAction("Quit", self.close)
        v=m.addMenu("&View");
        v.addAction("Show Analog Charts", self.analog_win.show);
        v.addAction("Show Digital Chart", self.digital_win.show);
        v.addAction("Show Combined Chart", self.combined_win.show)

        # ---- PID Menu ----
        pid_menu = self.menuBar().addMenu("&PID")

        # self.act_pid_edit = QtWidgets.QAction("Edit PID…", self)
        # self.act_pid_load = QtWidgets.QAction("Load PID.json…", self)
        # self.act_pid_save = QtWidgets.QAction("Save PID.json…", self)
        #
        # self.act_pid_edit.triggered.connect(self._act_pid_setup)  # you already have this dialog method
        # self.act_pid_load.triggered.connect(self._act_pid_load_json)
        # self.act_pid_save.triggered.connect(self._act_pid_save_json)

        pid_menu.addAction("Edit PID...", self._act_pid_setup)
        pid_menu.addSeparator()
        pid_menu.addAction("Load PID...", self._act_pid_load_json)
        pid_menu.addAction("Save PID...", self._act_pid_save_json)

        # pid_menu.addAction(self.act_pid_edit)
        # pid_menu.addAction(self.act_pid_load)
        # pid_menu.addAction(self.act_pid_save)


    def _build_central(self):
        cw=QtWidgets.QWidget(); self.setCentralWidget(cw); grid=QtWidgets.QGridLayout(cw)
        gb=QtWidgets.QGroupBox("Digital Outputs"); grid.addWidget(gb,0,0); gl=QtWidgets.QGridLayout(gb)
        self.do_btns=[]; self.do_chk_no=[]; self.do_chk_mom=[]; self.do_time=[]
        for i in range(8):
            btn=QtWidgets.QPushButton(f"{i}: DO"); btn.setCheckable(True)
            btn.setStyleSheet("QPushButton{background:#4caf50;color:white;} QPushButton:checked{background:#d32f2f;}")
            btn.clicked.connect(lambda checked, idx=i: self._on_do_clicked(idx, checked))
            btn.pressed.connect(lambda idx=i: self._on_do_pressed(idx)); btn.released.connect(lambda idx=i: self._on_do_released(idx))
            gl.addWidget(btn,i,0); self.do_btns.append(btn)
            chk_no=QtWidgets.QCheckBox("Normally Open"); gl.addWidget(chk_no,i,1); self.do_chk_no.append(chk_no)
            chk_m=QtWidgets.QCheckBox("Momentary"); gl.addWidget(chk_m,i,2); self.do_chk_mom.append(chk_m)
            sp=QtWidgets.QDoubleSpinBox(); sp.setSuffix(" s"); sp.setRange(0.0,3600.0); sp.setDecimals(3); sp.setSingleStep(0.1); sp.setValue(0.0); gl.addWidget(sp,i,3); self.do_time.append(sp)
        right=QtWidgets.QGroupBox("Analog Outputs / Timebase / Script"); grid.addWidget(right,0,1); rgl=QtWidgets.QGridLayout(right)

        # ---- PID header row ----
        self.btn_pid_setup = QtWidgets.QPushButton("PID Setup…")
        self.btn_pid_setup.clicked.connect(self._act_pid_setup)
        rgl.addWidget(self.btn_pid_setup, 7, 0, 1, 2)

        # ---- PID live table (up to 8 rows) ----
        self.pid_table = QtWidgets.QTableWidget(0, 11, self)
        self.pid_table.setHorizontalHeaderLabels([
            "Enable", "Type", "AIch", "OUTch", "AIValue", "Target", "CurrentError", "OutputValue", "P", "I", "D"
        ])
        self.pid_table.horizontalHeader().setStretchLastSection(True)
        rgl.addWidget(self.pid_table, 8, 0, 1, 2)

        self.ao_sliders=[]; self.ao_labels=[]
        for i in range(2):
            lab=QtWidgets.QLabel(f"AO{i}: 0.00 V"); rgl.addWidget(lab,i*2,0,1,2)
            s=QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal); s.setRange(-1000,1000); s.valueChanged.connect(lambda v, idx=i: self._on_ao_slider(idx, v)); rgl.addWidget(s,i*2+1,0,1,2)
            self.ao_labels.append(lab); self.ao_sliders.append(s)
        rgl.addWidget(QtWidgets.QLabel("Time window (s)"),4,0)
        self.time_spin=QtWidgets.QDoubleSpinBox(); self.time_spin.setRange(0.01,100.0); self.time_spin.setDecimals(3); self.time_spin.setSingleStep(0.01); self.time_spin.setValue(self.time_window_s)
        self.time_spin.valueChanged.connect(self._on_time_window); rgl.addWidget(self.time_spin,4,1)
        self.btn_connect=QtWidgets.QPushButton("Connect")
        self.btn_connect.clicked.connect(self._act_connect); rgl.addWidget(self.btn_connect,5,0)
        self.btn_apply_cfg = QtWidgets.QPushButton("Apply Config")
        self.btn_apply_cfg.setToolTip("Re-apply the current config to the UI and device")
        self.btn_apply_cfg.clicked.connect(self._act_apply_config); rgl.addWidget(self.btn_apply_cfg,5,1)
        self.btn_run=QtWidgets.QPushButton("Run Script")
        self.btn_run.clicked.connect(self._act_run_script); rgl.addWidget(self.btn_run,6,0)
        self.btn_stop=QtWidgets.QPushButton("Stop/Pause Script")
        self.btn_stop.clicked.connect(self._act_stop_script); rgl.addWidget(self.btn_stop,6,1)
        self.btn_reset=QtWidgets.QPushButton("Reset Script")
        self.btn_reset.clicked.connect(self._act_reset_script); rgl.addWidget(self.btn_reset,7,0)

    def _build_status_panes(self):
        tx=QtWidgets.QDockWidget("Sent (Tx)", self); rx=QtWidgets.QDockWidget("Received / Debug (Rx)", self)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, tx); self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, rx)
        self.tx_text=QtWidgets.QPlainTextEdit(); self.tx_text.setReadOnly(True); self.rx_text=QtWidgets.QPlainTextEdit(); self.rx_text.setReadOnly(True)
        tx.setWidget(self.tx_text); rx.setWidget(self.rx_text)
    def log_tx(self, msg): self.tx_text.appendPlainText(msg)
    def log_rx(self, msg): self.rx_text.appendPlainText(msg)

    def _apply_cfg_to_ui(self):
        names = []
        for i in range(8):
            self.do_btns[i].setText(f"{i}: {self.cfg.digitalOutputs[i].name}")
            self.do_chk_no[i].setChecked(self.cfg.digitalOutputs[i].normallyOpen)
            self.do_chk_mom[i].setChecked(self.cfg.digitalOutputs[i].momentary)
            self.do_time[i].setValue(self.cfg.digitalOutputs[i].actuationTime)
        for i in range(2):
            a=self.cfg.analogOutputs[i]; mn=max(-10.0,min(10.0,a.minV)); mx=max(-10.0,min(10.0,a.maxV))
            if mn>mx: mn,mx=mx,mn
            self.ao_sliders[i].setMinimum(int(mn*100)); self.ao_sliders[i].setMaximum(int(mx*100)); self.ao_sliders[i].setValue(int(a.startupV*100))
            self.ao_labels[i].setText(f"AO{i}: {a.startupV:.2f} V ({a.name})")
        for i in range(8):
            self.ai_filters[i].set_fs(self.ui_rate_hz); self.ai_filters[i].set_cutoff(self.cfg.analogs[i].cutoffHz)
            self.ai_filter_enabled[i]=(self.cfg.analogs[i].cutoffHz>0.0)
        self.analog_win.setWindowTitle("Analog Inputs — " + ", ".join([a.name for a in self.cfg.analogs]))
        self.analog_win.set_names_units(
            [a.name for a in self.cfg.analogs],
            [a.units for a in self.cfg.analogs],
        )
        for i in range(8):
            nm = None
            # Try a few common layouts; keep whatever matches your Config class
            if hasattr(self.cfg, "douts"):  # e.g., self.cfg.douts[i].name
                nm = getattr(self.cfg.douts[i], "name", None)
            if nm is None and hasattr(self.cfg, "dos"):  # e.g., self.cfg.dos[i].name
                nm = getattr(self.cfg.dos[i], "name", None)
            if nm is None and hasattr(self.cfg, "doNames"):  # e.g., list of strings
                nm = self.cfg.doNames[i]
            if nm is None and hasattr(self.cfg, "do0Name"):  # legacy flat keys
                nm = getattr(self.cfg, f"do{i}Name", None)
            if not nm:
                nm = f"DO{i}"
            names.append(nm)

            # Update combined chart titles
            self.combined_win.set_ai_names_units(
                [a.name for a in self.cfg.analogs],
                [a.units for a in self.cfg.analogs],
            )

            # AO names/units (same logic you used above at creation)
            ao_names = []
            ao_units = []
            for i in range(2):
                nm = getattr(self.cfg, f"ao{i}Name", f"AO{i}")
                u = getattr(self.cfg, f"ao{i}Units", "")
                ao_names.append(nm)
                ao_units.append(u)

            self.combined_win.set_ao_names_units(ao_names, ao_units)

    def _act_apply_config(self):
        """Re-apply the current in-memory config to UI and, if connected, to the device."""
        try:
            # 1) Push config to UI (names/units, AO slider ranges/defaults, DO UI, etc.)
            if hasattr(self, "_apply_cfg_to_ui"):
                self._apply_cfg_to_ui()

            # 2) If not connected, we’re done (UI reflects config; device will pick it up on connect)
            if not (self.daq and getattr(self.daq, "connected", False)):
                self.log_rx("Config applied to UI (device is disconnected).")
                return

            # 3) If connected: stop worker and scan (if running)
            try:
                if hasattr(self, "acq_thread") and self.acq_thread:
                    self.acq_thread.stop()
                    self.acq_thread.wait(1000)
                    self.acq_thread = None
            except Exception as e:
                self.log_rx(f"Acq worker stop: {e}")

            try:
                if hasattr(self.daq, "stop_ai_scan"):
                    self.daq.stop_ai_scan()
            except Exception as e:
                self.log_rx(f"AI scan stop: {e}")

            # 4) Apply DAQ-side settings
            try:
                if hasattr(self.daq, "set_ai_mode") and hasattr(self.cfg, "aiMode"):
                    self.daq.set_ai_mode(self.cfg.aiMode)
            except Exception as e:
                self.log_rx(f"AI mode set error: {e}")

            # Validate channels (handles SE/DIFF)
            try:
                valid = self.daq.probe_ai_channels(8)
            except Exception:
                valid = list(range(8))
            high = max(valid) if valid else 0
            for i in range(8):
                # show/hide curves to match valid channels
                if hasattr(self.analog_win, "curves"):
                    self.analog_win.curves[i].setVisible(i in valid)

            # 5) Restart the hardware scan with new rate/block
            actual_rate = self.daq.start_ai_scan(
                0, high, float(self.cfg.sampleRateHz), int(self.cfg.blockSize)
            )
            self.sample_period = 1.0 / max(1e-6, float(actual_rate))
            self._rebuild_histories_for_span()

            # 6) Restart background acquisition worker (if your app uses it)
            try:
                from acq_worker import AcqWorker  # safe even if unused elsewhere
                slopes = [a.slope for a in self.cfg.analogs]
                offsets = [a.offset for a in self.cfg.analogs]
                cutoffs = [a.cutoffHz for a in self.cfg.analogs]
                self.acq_thread = AcqWorker(self.daq, slopes, offsets, cutoffs, actual_rate, self)
                if hasattr(self, "_on_chunk_ready"):
                    self.acq_thread.chunkReady.connect(
                        self._on_chunk_ready, QtCore.Qt.ConnectionType.QueuedConnection
                    )
                self.acq_thread.start()
            except Exception as e:
                # If you don't use AcqWorker, this is fine; plotting still works with your existing loop.
                self.log_rx(f"Acq worker init: {e}")

            # 7) Reset histories (keeps charts consistent with new scaling/rates)
            try:
                self.ai_hist_x.clear()
                for i in range(8):
                    self.ai_hist_y[i].clear()
                    self.do_hist_y[i].clear()
            except Exception:
                pass

            # 8) Re-apply AO slider ranges/defaults and push to hardware
            try:
                for i in range(2):
                    self._apply_ao_slider(i)
            except Exception:
                pass

            self.log_rx("Config applied to running device.")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Apply Config Error", str(e))

    def _ensure_queue(self):
        # Create the chunk queue if it doesn't exist yet
        if not hasattr(self, "_chunk_queue") or self._chunk_queue is None:
            from collections import deque
            self._chunk_queue = deque()

    def _act_load_cfg(self, path=None, show_editor=True):
        if not path:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load config.json", "", "JSON (*.json)")
            if not path:
                return
        self.cfg = ConfigManager.load(path)
        self._apply_cfg_to_ui()
        self.log_rx(f"Loaded config: {path}")
        if show_editor:
            self._act_edit_cfg()

    def _act_save_cfg(self):
        for i in range(8):
            self.cfg.digitalOutputs[i].normallyOpen=self.do_chk_no[i].isChecked()
            self.cfg.digitalOutputs[i].momentary=self.do_chk_mom[i].isChecked()
            self.cfg.digitalOutputs[i].actuationTime=float(self.do_time[i].value())
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,"Save config.json","config.json","JSON (*.json)")
        if not path: return
        ConfigManager.save(path,self.cfg); self.log_rx(f"Saved config: {path}")

    def _act_edit_cfg(self):
        dlg=ConfigEditorDialog(self,self.cfg)
        if dlg.exec():
            self.cfg=dlg.updated_config(); self.sample_period = 1.0 / max(1e-6, self.cfg.sampleRateHz); self._apply_cfg_to_ui()
            self._rebuild_histories_for_span()

    def _act_load_script(self, path=None, show_editor=True):
        if not path:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load script.json", "", "JSON (*.json)")
            if not path:
                return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.script_events = json.load(f)
            self.log_rx(f"Loaded script: {path}")
            if show_editor:
                self._act_edit_script()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Script error", str(e))

    def _act_save_script(self):
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,"Save script.json","script.json","JSON (*.json)")
        if not path: return
        try:
            with open(path,"w",encoding="utf-8") as f: json.dump(self.script_events,f,indent=2)
            self.log_rx(f"Saved script: {path}")
        except Exception as e: QtWidgets.QMessageBox.critical(self,"Save error",str(e))

    def _act_edit_script(self):
        dlg=ScriptEditorDialog(self,self.script_events)
        if dlg.exec(): self.script_events=dlg.result_events()

    def _act_pid_load_json(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load PID.json", "", "JSON (*.json)")
        if not path:
            return
        try:
            self.pid_mgr.load_file(path)
            self.log_rx(f"[INFO] Loaded {path}")
            self._pid_refresh_table_structure()
            self._pid_update_table_values()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "PID load error", str(e))

    def _act_pid_save_json(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save PID.json", "PID.json", "JSON (*.json)")
        if not path:
            return
        try:
            self.pid_mgr.save_file(path)
            self.log_rx(f"[INFO] Saved {path}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "PID save error", str(e))

    def _act_connect(self):
        self._ensure_queue()

        # Disconnect path
        if self.daq and getattr(self.daq, 'connected', False):
            try:
                if hasattr(self, 'acq_thread') and self.acq_thread:
                    self.acq_thread.stop()
                    self.acq_thread.wait(1000)
                    self.acq_thread = None
            except Exception:
                pass
            self.daq.disconnect()
            self.daq = None
            self.btn_connect.setText("Connect")
            return

        # Connect path
        try:
            self.daq = DaqDriver(self.cfg.boardNum, self.log_tx, self.log_rx)
            self.daq.connect()
            _ = self.daq.set_ai_mode(self.cfg.aiMode)
            valid = self.daq.probe_ai_channels(8)
            high = max(valid) if valid else 0
            for i in range(8):
                self.analog_win.curves[i].setVisible(i in valid)

            # Start hardware scan
            actual_rate = self.daq.start_ai_scan(0, high, self.cfg.sampleRateHz, self.cfg.blockSize)
            self.sample_period = 1.0 / max(1e-6, float(actual_rate))
            self._rebuild_histories_for_span()

            # Start background acquisition worker (does calibration + LPF)
            slopes = [a.slope for a in self.cfg.analogs]
            offsets = [a.offset for a in self.cfg.analogs]
            cutoffs = [a.cutoffHz for a in self.cfg.analogs]
            self.acq_thread = AcqWorker(self.daq, slopes, offsets, cutoffs, actual_rate, self)
            self.acq_thread.chunkReady.connect(self._on_chunk_ready, QtCore.Qt.ConnectionType.QueuedConnection)
            self.acq_thread.start()

            # Reset histories and the queue
            self.ai_hist_x.clear()
            for i in range(8):
                self.ai_hist_y[i].clear()
                self.do_hist_y[i].clear()

            if not hasattr(self, "_chunk_queue"):
                self._chunk_queue = deque()
            self._chunk_queue.clear()

            self.btn_connect.setText("Disconnect")
            for i in range(2):
                self._apply_ao_slider(i)
        except DaqError as e:
            QtWidgets.QMessageBox.critical(self, "DAQ error", str(e))

    def _on_time_window(self, v):
        self.time_window_s=float(v)
        self._on_span_changed(float(v))

    def _on_chunk_ready(self, payload: object):
        self._ensure_queue()
        # payload: {"low": int, "num_ch": int, "M": int, "data": np.ndarray[num_ch, M]}
        self._chunk_queue.append(payload)

    def _on_request_scale(self, idx):
        y_min,y_max=self.analog_win.get_y_range(idx); dlg=ScaleDialog(self,idx,y_min,y_max)
        if dlg.exec():
            auto,mn,mx=dlg.result_values()
            if auto: self.analog_win.autoscale(idx)
            else:
                if mx<mn: mn,mx=mx,mn
                self.analog_win.set_fixed_scale(idx,mn,mx)

    def _on_ao_slider(self, idx, raw_val):
        v = 0.01 * float(raw_val)
        a = self.cfg.analogOutputs[idx]
        v = max(max(-10.0, a.minV), min(min(10.0, a.maxV), v))
        self.ao_labels[idx].setText(f"AO{idx}: {v:.2f} V ({a.name})")

        # Remember current AO for plotting in Combined window
        if not hasattr(self, "ao_value"):
            self.ao_value = [0.0, 0.0]
        self.ao_value[idx] = float(v)

        # Send to hardware if connected
        if self.daq and getattr(self.daq, "connected", False):
            self.daq.set_ao_volts(idx, v)

    def _apply_ao_slider(self, idx):
        self._on_ao_slider(idx, self.ao_sliders[idx].value())
        #self.ao_value[idx] = self.ao_sliders[idx].value()

    def _on_do_pressed(self, idx):
        if self.do_chk_mom[idx].isChecked():
            no=self.do_chk_no[idx].isChecked(); self.do_btns[idx].setChecked(True)
            if self.daq and getattr(self.daq,"connected",False): self._set_do(idx, True if no else False)

    def _on_do_released(self, idx):
        if self.do_chk_mom[idx].isChecked():
            no=self.do_chk_no[idx].isChecked(); self.do_btns[idx].setChecked(False)
            if self.daq and getattr(self.daq,"connected",False): self._set_do(idx, False if no else True)

    def _on_do_clicked(self, idx, checked):
        no=self.do_chk_no[idx].isChecked(); momentary=self.do_chk_mom[idx].isChecked(); act_time=float(self.do_time[idx].value())
        if momentary: return
        if act_time>0.0:
            if checked:
                if self.daq and getattr(self.daq,"connected",False): self._set_do(idx, True if no else False)
                QtCore.QTimer.singleShot(int(act_time*1000), lambda: self._release_do(idx, no))
            else: self._release_do(idx, no)
        else:
            state=(checked if no else (not checked))
            if self.daq and getattr(self.daq,"connected",False): self._set_do(idx, state)

    def _release_do(self, idx, no):
        self.do_btns[idx].setChecked(False)
        if self.daq and getattr(self.daq,"connected",False): self._set_do(idx, False if no else True)

    def _set_do(self, idx, state: bool):
        self.do_state[idx] = bool(state)  # <-- remember for plotting
        if self.daq and getattr(self.daq,"connected",False):
            try: self.daq.set_do_bit(idx, state)
            except Exception as e: self.log_rx(f"DO error: {e}")

    def _on_script_tick(self, t, relays):
        for i,st in enumerate(relays[:8]):
            blk=self.do_btns[i].blockSignals(True); self.do_btns[i].setChecked(bool(st)); self.do_btns[i].blockSignals(blk)

    def _render(self):
        try:
            # Skip rendering until we’re connected and actually have samples
            if not (self.daq and getattr(self.daq, "connected", False)):
                return

            # Snapshot histories as lists
            x_list = list(self.ai_hist_x)
            if not hasattr(self, "_x0") or self._x0 is None or (x_list and x_list[0] < self._x0): self._x0 = float(x_list[0])
            if not x_list:
                return

            ai_list = [list(ch) for ch in self.ai_hist_y]  # 8
            do_list = [list(ch) for ch in self.do_hist_y]  # 8
            ao_list = [list(ch) for ch in getattr(self, "ao_hist_y", [[], []])]  # 2

            # Window by time span
            span = float(self.time_window_s)
            t_end = x_list[-1]
            t_start = t_end - span
            i0 = bisect_left(x_list, t_start)
            x_cut = x_list[i0:]
            if not x_cut:
                return
            N = len(x_cut)

            def cut_align(seq, fill=0.0):
                out = seq[i0:]
                if len(out) < N:
                    out = [fill] * (N - len(out)) + out
                elif len(out) > N:
                    out = out[-N:]
                return out

            ys_cut = [cut_align(ch, fill=np.nan) for ch in ai_list]
            do_cut = [cut_align(ch, fill=0.0) for ch in do_list]

            ao_vals = getattr(self, "ao_value", [0.0, 0.0])
            ao_cut = []
            for idx, ch in enumerate(ao_list[:2]):
                fillv = float(ao_vals[idx]) if idx < len(ao_vals) else 0.0
                ao_cut.append(cut_align(ch, fill=fillv))

            x_arr = np.asarray(x_cut, dtype=float)
            if x_arr.size:
                x_arr = np.asarray(x_cut, dtype=float) - float(self._x0)

            if hasattr(self, "analog_win") and self.analog_win.isVisible():
                self.analog_win.set_data(x_arr, ys_cut)
            if hasattr(self, "digital_win") and self.digital_win.isVisible():
                self.digital_win.set_data(x_arr, do_cut)
            if hasattr(self, "combined_win") and self.combined_win.isVisible():
                self.combined_win.set_data(x_arr, ys_cut, ao_cut, do_cut)

        except Exception as e:
            if self.daq and getattr(self.daq, "connected", False):
                self.log_rx(f"Render error: {e}")

    def _drain_chunks(self, max_batches: int = 8):
        """Pop up to max_batches blocks from the acq queue and append to histories.

        X is a relative time axis that starts at 0.0 for the first sample and
        advances by the current sample period (self.sample_period).
        """
        if not hasattr(self, "_chunk_queue") or not self._chunk_queue:
            return

        sp = float(self.sample_period if self.sample_period > 0 else 1.0 / max(1.0, self.cfg.sampleRateHz))
        last_x = float(self.ai_hist_x[-1]) if len(self.ai_hist_x) > 0 else None

        batches = 0
        while self._chunk_queue and batches < max_batches:
            payload = self._chunk_queue.popleft()
            batches += 1

            data = payload.get("data", None)
            if data is None:
                continue

            arr = np.asarray(data, dtype=float)
            if arr.ndim == 1:
                arr = arr.reshape(1, -1)
            num_ch, M = int(arr.shape[0]), int(arr.shape[1])

            # --- PID: run loops on this block and apply outputs ---
            try:
                # ai_block_2d shape must be (nsamples, nch)
                ai_block_2d = arr.T  # we currently have (nch, M) -> transpose
                do_updates, ao_updates = self.pid_mgr.process_block(ai_block_2d, float(sp))

                # Apply DO updates ONLY if that channel is currently controlled by an enabled loop
                for ch, bit in do_updates.items():
                    if self.pid_mgr.is_do_controlled(ch):
                        self._set_do(int(ch), bool(bit))  # updates self.do_state too

                # Apply AO updates ONLY if that channel is currently controlled by an enabled loop
                for ch, volts in ao_updates.items():
                    if self.pid_mgr.is_ao_controlled(ch):
                        self.ao_value[int(ch)] = float(volts)
                        if self.daq and getattr(self.daq, "connected", False):
                            self.daq.set_ao_volts(int(ch), float(volts))

                # Update the PID live table after each processed block
                self._pid_update_table_values()
            except Exception as e:
                self.log_rx(f"[PID] block apply: {e}")
            # --- end PID block ---

            # Build relative X for this block
            start = 0.0 if (last_x is None) else (last_x + sp)
            x_block = start + np.arange(M, dtype=float) * sp
            last_x = float(x_block[-1])

            # Append to histories
            self.ai_hist_x.extend(x_block.tolist())

            # AI channels; if fewer channels scanned, pad remaining with NaNs
            for ch in range(8):
                if ch < num_ch:
                    self.ai_hist_y[ch].extend(arr[ch, :].tolist())
                else:
                    self.ai_hist_y[ch].extend([np.nan] * M)

            # DO: repeat current state across this block
            for di in range(8):
                self.do_hist_y[di].extend([1.0 if self.do_state[di] else 0.0] * M)

            # AO: repeat current AO volts across this block
            for ai in range(2):
                val = float(self.ao_value[ai]) if hasattr(self, "ao_value") else 0.0
                self.ao_hist_y[ai].extend([val] * M)

    def _loop(self):
        self._drain_chunks(max_batches=8)
        #self._prune_history()

    def _reset_histories(self):
        """Clear and (re)size histories to fit the current span & rate; X restarts at 0.0."""
        cap = max(256, self._target_history_len(getattr(self, "time_window_s", 5.0)))
        self.ai_hist_x = deque(maxlen=cap)
        self.ai_hist_y = [deque(maxlen=cap) for _ in range(8)]
        self.do_hist_y = [deque(maxlen=cap) for _ in range(8)]
        self.ao_hist_y = [deque(maxlen=cap) for _ in range(2)]

    def _prune_history(self):
        max_pts=int(max(1.0, self.cfg.sampleRateHz)*12.0)
        if len(self.ai_hist_x)>max_pts:
            trim=len(self.ai_hist_x)-max_pts; self.ai_hist_x=self.ai_hist_x[trim:]
            for i in range(8):
                self.ai_hist_y[i]=self.ai_hist_y[i][trim:]; self.do_hist_y[i]=self.do_hist_y[i][trim:]

    def _act_run_script(self):
        # Use whatever is currently in the editor buffer
        self.script.set_events(self.script_events)
        self.script.run()
        self.log_rx("Script: RUN")

    def _act_stop_script(self):
        self.script.stop()
        self.log_rx("Script: STOP/PAUSE")

    def _act_reset_script(self):
        self.script.reset()
        self.log_rx("Script: RESET")

    def _act_pid_setup(self):
        dlg = PIDSetupDialog(self, existing=self.pid_mgr.loops)
        if _dlg_exec(dlg) == DLG_ACCEPTED:
            vals = dlg.values()
            if len(vals) > 8:
                QtWidgets.QMessageBox.warning(self, "PID", "Max 8 PID loops.")
                vals = vals[:8]
            self.pid_mgr.loops = [PIDLoopDef(**v) for v in vals]
            self.pid_mgr._rebuild_instances()
            self.pid_mgr.reset_states()
            try:
                self.pid_mgr.save_file("PID.json")
                self.log_rx("[INFO] Saved PID.json")
            except Exception as e:
                self.log_rx(f"[WARN] PID.json save failed: {e}")
            self._pid_refresh_table_structure()
            self._pid_update_table_values()

    def _pid_refresh_table_structure(self):
        loops = getattr(self.pid_mgr, "loops", [])
        self.pid_table.setRowCount(len(loops))
        self.pid_table.setColumnCount(11)
        self.pid_table.setHorizontalHeaderLabels([
            "Enable", "Type", "AIch", "OUTch", "AIValue", "Target", "CurrentError", "OutputValue", "P", "I", "D"
        ])
        self.pid_table.horizontalHeader().setStretchLastSection(True)

        for r, lp in enumerate(loops):
            # Enable (editable)
            cb = QtWidgets.QCheckBox()
            cb.setChecked(bool(lp.enabled))
            cb.stateChanged.connect(lambda state, row=r: self._pid_on_enable_row(row, state))
            self.pid_table.setCellWidget(r, 0, cb)

            # Type (editable)
            typ = QtWidgets.QComboBox()
            typ.addItems(["digital", "analog"])
            typ.setCurrentText(lp.kind)
            typ.currentTextChanged.connect(lambda text, row=r: self._pid_on_type_changed(row, text))
            self.pid_table.setCellWidget(r, 1, typ)

            # AI ch (editable)
            sp_ai = QtWidgets.QSpinBox()
            sp_ai.setRange(0, 7)
            sp_ai.setValue(int(lp.ai_ch))
            sp_ai.valueChanged.connect(lambda val, row=r: self._pid_on_ai_changed(row, val))
            self.pid_table.setCellWidget(r, 2, sp_ai)

            # OUT ch (editable; range depends on type)
            sp_out = QtWidgets.QSpinBox()
            if lp.kind == "analog":
                sp_out.setRange(0, 1)
            else:
                sp_out.setRange(0, 7)
            sp_out.setValue(int(lp.out_ch))
            sp_out.valueChanged.connect(lambda val, row=r: self._pid_on_out_changed(row, val))
            self.pid_table.setCellWidget(r, 3, sp_out)

            # AIValue (read-only)
            it = QtWidgets.QTableWidgetItem("—");
            it.setFlags(_ro_flags(it.flags()))
            self.pid_table.setItem(r, 4, it)

            # Target (editable)
            dsp_tgt = QtWidgets.QDoubleSpinBox()
            dsp_tgt.setDecimals(4);
            dsp_tgt.setRange(-1e9, 1e9);
            dsp_tgt.setSingleStep(0.1)
            dsp_tgt.setValue(float(lp.target))
            dsp_tgt.valueChanged.connect(lambda val, row=r: self._pid_on_target_changed(row, val))
            self.pid_table.setCellWidget(r, 5, dsp_tgt)

            # CurrentError (read-only)
            it = QtWidgets.QTableWidgetItem("—");
            it.setFlags(_ro_flags(it.flags()))
            self.pid_table.setItem(r, 6, it)

            # OutputValue (read-only)
            it = QtWidgets.QTableWidgetItem("—");
            it.setFlags(_ro_flags(it.flags()))
            self.pid_table.setItem(r, 7, it)

            # P/I/D (editable)
            for c, key in enumerate(["kp", "ki", "kd"], start=8):
                dsp = QtWidgets.QDoubleSpinBox()
                dsp.setDecimals(6);
                dsp.setRange(-1e6, 1e6);
                dsp.setSingleStep(0.01)
                dsp.setValue(float(getattr(lp, key)))
                dsp.valueChanged.connect(lambda val, row=r, k=key: self._pid_on_gain_changed(row, k, val))
                self.pid_table.setCellWidget(r, c, dsp)

    def _pid_update_table_values(self):
        # Build a quick lookup of runtime objects by (kind, ai, out)
        rt = {"digital": {}, "analog": {}}
        for d in getattr(self.pid_mgr, "dloops", []):
            rt["digital"][(d.defn.ai_ch, d.defn.out_ch)] = ("digital", d.last_ai, d.last_err, 1 if d.last_do_bit else 0)
        for a in getattr(self.pid_mgr, "aloops", []):
            rt["analog"][(a.defn.ai_ch, a.defn.out_ch)] = ("analog", a.last_ai, a.last_err, a.last_ao)

        loops = getattr(self.pid_mgr, "loops", [])
        for r, lp in enumerate(loops):
            # AI value
            ai_val = "—";
            err_val = "—";
            out_val = "—"
            key = (lp.ai_ch, lp.out_ch)
            hit = rt[lp.kind].get(key)
            if hit:
                _, ai, err, outv = hit
                ai_val = f"{ai:.4f}" if (isinstance(ai, (int, float)) and math.isfinite(ai)) else "—"
                err_val = f"{err:.4f}" if (isinstance(err, (int, float)) and math.isfinite(err)) else "—"
                if lp.kind == "digital":
                    out_val = str(int(bool(outv)))
                else:
                    out_val = f"{float(outv):.3f}" if (isinstance(outv, (int, float)) and math.isfinite(outv)) else "—"

            for col, txt in [(4, ai_val), (6, err_val), (7, out_val)]:
                it = self.pid_table.item(r, col)
                if it is None:
                    it = QtWidgets.QTableWidgetItem()
                    it.setFlags(_ro_flags(QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled))
                    self.pid_table.setItem(r, col, it)
                it.setText(txt)

def _pid_rebuild(self):
    self.pid_mgr._rebuild_instances()
    self.pid_mgr.reset_states()
    self._pid_update_table_values()

def _pid_on_enable_row(self, row, state):
    enabled = int(state) != 0
    try:
        self.pid_mgr.loops[row].enabled = enabled
        self._pid_rebuild()
    except Exception as e:
        self.log_rx(f"[PID] enable toggle failed: {e}")

def _pid_on_type_changed(self, row, text):
    try:
        self.pid_mgr.loops[row].kind = text
        # adjust OUT range for this row
        w = self.pid_table.cellWidget(row, 3)
        if isinstance(w, QtWidgets.QSpinBox):
            if text == "analog":
                w.setRange(0, 1)
                if w.value() > 1: w.setValue(0)
            else:
                w.setRange(0, 7)
        self._pid_rebuild()
    except Exception as e:
        self.log_rx(f"[PID] type change failed: {e}")

def _pid_on_ai_changed(self, row, val):
    try:
        self.pid_mgr.loops[row].ai_ch = int(val)
        self._pid_rebuild()
    except Exception as e:
        self.log_rx(f"[PID] AI ch change failed: {e}")

def _pid_on_out_changed(self, row, val):
    try:
        self.pid_mgr.loops[row].out_ch = int(val)
        self._pid_rebuild()
    except Exception as e:
        self.log_rx(f"[PID] OUT ch change failed: {e}")

def _pid_on_target_changed(self, row, val):
    try:
        self.pid_mgr.loops[row].target = float(val)
        # no rebuild needed for tuning only
        self._pid_update_table_values()
    except Exception as e:
        self.log_rx(f"[PID] target change failed: {e}")

def _pid_on_gain_changed(self, row, key, val):
    try:
        setattr(self.pid_mgr.loops[row], key, float(val))
        # no full rebuild; core uses new gains next step, but to be safe:
        self.pid_mgr.reset_states()
        self._pid_update_table_values()
    except Exception as e:
        self.log_rx(f"[PID] gain change failed: {e}")

# ---- Bind the standalone PID handlers to MainWindow (so self._pid_* works) ----
MainWindow._pid_rebuild = _pid_rebuild
MainWindow._pid_on_enable_row = _pid_on_enable_row
MainWindow._pid_on_type_changed = _pid_on_type_changed
MainWindow._pid_on_ai_changed = _pid_on_ai_changed
MainWindow._pid_on_out_changed = _pid_on_out_changed
MainWindow._pid_on_target_changed = _pid_on_target_changed
MainWindow._pid_on_gain_changed = _pid_on_gain_changed

def main():
    app=QtWidgets.QApplication(sys.argv); w=MainWindow(); w.show(); return app.exec()
if __name__=="__main__": raise SystemExit(main())
