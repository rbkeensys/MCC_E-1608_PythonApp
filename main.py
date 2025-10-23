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
from etc_device import ETCDevice
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
    """
    Editor for PID loops with:
      - Enable
      - Type (Digital PID / Analog PID)   [derived from output selector]
      - Source (ai | tc)
      - AI ch
      - OUT (D0..D7, A0..A1)              [changes kind+out_ch]
      - Target, P, I, D
      - Clamps (ErrMin, ErrMax, IMin, IMax, OutMin, OutMax for analog)
      - Add / Remove
    """
    def __init__(self, pid_mgr, parent=None):
        super().__init__(parent)
        from PyQt6 import QtGui
        self.setWindowTitle("Edit PID Loops")
        self.resize(980, 520)
        self.pid_mgr = pid_mgr

        v = QtWidgets.QVBoxLayout(self)

        # Table
        self.table = QtWidgets.QTableWidget(self)
        self.table.setColumnCount(14)
        self.table.setHorizontalHeaderLabels([
            "Enable","Type","Source","AI ch","OUT (D/A)","Target","P","I","D",
            "ErrMin","ErrMax","IMin","IMax","OutMin/Max"
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.table)

        # Buttons row
        hb = QtWidgets.QHBoxLayout()
        hb.addStretch(1)
        self.btn_add = QtWidgets.QPushButton("Add")
        self.btn_remove = QtWidgets.QPushButton("Remove")
        self.btn_ok = QtWidgets.QPushButton("OK")
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        hb.addWidget(self.btn_add); hb.addWidget(self.btn_remove)
        hb.addSpacing(16)
        hb.addWidget(self.btn_ok); hb.addWidget(self.btn_cancel)
        v.addLayout(hb)

        self.btn_add.clicked.connect(self._on_add)
        self.btn_remove.clicked.connect(self._on_remove)
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

        # Fill rows from manager loops
        self._refresh_from_mgr()

    # ---------- helpers ----------
    def _refresh_from_mgr(self):
        loops = getattr(self.pid_mgr, "loops", [])
        self.table.setRowCount(len(loops))
        for r, lp in enumerate(loops):
            self._fill_row(r, lp)

    def _fill_row(self, r, lp):
        # Enable
        cb_en = QtWidgets.QCheckBox()
        cb_en.setChecked(bool(getattr(lp, "enabled", True)))
        cb_en.stateChanged.connect(lambda _s, row=r: self._on_enable(row))
        self.table.setCellWidget(r, 0, cb_en)

        # Type (derived; read-only label)
        typ = "Digital PID" if lp.kind == "digital" else "Analog PID"
        it_typ = QtWidgets.QTableWidgetItem(typ)
        it_typ.setFlags(it_typ.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(r, 1, it_typ)

        # Source (ai|tc)
        src_cb = QtWidgets.QComboBox()
        src_cb.addItems(["ai","tc"])
        src_cb.setCurrentText(getattr(lp, "src", "ai"))
        src_cb.currentTextChanged.connect(lambda _t, row=r: self._on_src(row))
        self.table.setCellWidget(r, 2, src_cb)

        # AI channel
        sp_ai = QtWidgets.QSpinBox(); sp_ai.setRange(0, 31); sp_ai.setValue(int(lp.ai_ch))
        sp_ai.valueChanged.connect(lambda _v, row=r: self._on_ai(row))
        self.table.setCellWidget(r, 3, sp_ai)

        # OUT (D0..D7, A0..A1)
        out_combo = QtWidgets.QComboBox()
        for i in range(8): out_combo.addItem(f"D{i}")
        for i in range(2): out_combo.addItem(f"A{i}")
        # set current based on lp.kind/out_ch
        cur = f"{'D' if lp.kind=='digital' else 'A'}{int(lp.out_ch)}"
        idx = out_combo.findText(cur)
        out_combo.setCurrentIndex(max(0, idx))
        out_combo.currentTextChanged.connect(lambda _t, row=r: self._on_out(row))
        self.table.setCellWidget(r, 4, out_combo)

        # Target, P, I, D
        for col, val in [(5, lp.target), (6, lp.kp), (7, lp.ki), (8, lp.kd)]:
            self.table.setItem(r, col, self._num_item(val))

        # ErrMin/ErrMax, IMin/IMax
        self.table.setItem(r, 9,  self._maybe_num_item(getattr(lp, "err_min", None)))
        self.table.setItem(r, 10, self._maybe_num_item(getattr(lp, "err_max", None)))
        self.table.setItem(r, 11, self._maybe_num_item(getattr(lp, "i_min", None)))
        self.table.setItem(r, 12, self._maybe_num_item(getattr(lp, "i_max", None)))

        # OutMin/Max (analog only) — show as text "lo..hi" for convenience
        if lp.kind == "analog":
            lo = getattr(lp, "out_min", None)
            hi = getattr(lp, "out_max", None)
            txt = "" if (lo is None and hi is None) else f"{'' if lo is None else lo}..{'' if hi is None else hi}"
        else:
            txt = ""  # not applicable to digital
        self.table.setItem(r, 13, QtWidgets.QTableWidgetItem(txt))

    @staticmethod
    def _num_item(val) -> QtWidgets.QTableWidgetItem:
        it = QtWidgets.QTableWidgetItem(f"{float(val):.6g}")
        it.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        return it

    @staticmethod
    def _maybe_num_item(val) -> QtWidgets.QTableWidgetItem:
        if val is None or (isinstance(val, str) and val.strip() == ""):
            return QtWidgets.QTableWidgetItem("")
        try:
            f = float(val)
            return PIDSetupDialog._num_item(f)
        except Exception:
            return QtWidgets.QTableWidgetItem(str(val))

    # ---------- row ops ----------
    def _on_add(self):
        from pid import PIDLoopDef
        loops = self.pid_mgr.loops
        # default new as digital on DO0
        new = PIDLoopDef(kind="digital", enabled=True, src="ai",
                         ai_ch=0, out_ch=0, target=0.0, kp=1.0, ki=0.0, kd=0.0)
        loops.append(new)
        self.table.insertRow(self.table.rowCount())
        self._fill_row(self.table.rowCount()-1, new)

    def _on_remove(self):
        r = self.table.currentRow()
        if r < 0 or r >= self.table.rowCount(): return
        del self.pid_mgr.loops[r]
        self.table.removeRow(r)

    # ---------- cell handlers (write-through to pid_mgr.loops) ----------
    def _loop_at(self, row):
        loops = getattr(self.pid_mgr, "loops", [])
        if 0 <= row < len(loops): return loops[row]
        return None

    def _on_enable(self, row):
        lp = self._loop_at(row);  lp.enabled = bool(self.table.cellWidget(row, 0).isChecked())

    def _on_src(self, row):
        lp = self._loop_at(row);  lp.src = str(self.table.cellWidget(row, 2).currentText())

    def _on_ai(self, row):
        lp = self._loop_at(row);  lp.ai_ch = int(self.table.cellWidget(row, 3).value())

    def _on_out(self, row):
        """Parse Dn/Am and update kind + out_ch + type label."""
        lp = self._loop_at(row)
        txt = str(self.table.cellWidget(row, 4).currentText())
        if txt and txt[0] in ("D","A"):
            k = "digital" if txt[0] == "D" else "analog"
            ch = int(txt[1:])
            lp.kind = k
            lp.out_ch = ch
            # update type label column (col 1)
            it = self.table.item(row, 1)
            if it: it.setText("Digital PID" if k=="digital" else "Analog PID")

    # ---------- collect back to pid_mgr ----------
    def accept(self):
        # Pull scalar columns into loop defs, preserving previous values on blanks
        for r in range(self.table.rowCount()):
            lp = self._loop_at(r)

            def _getf(c, current):
                it = self.table.item(r, c)
                if it is None:
                    return current
                txt = (it.text() or "").strip()
                if txt == "":
                    return current
                try:
                    return float(txt)
                except Exception:
                    return current

            lp.target = _getf(5, getattr(lp, "target", 0.0))
            lp.kp = _getf(6, getattr(lp, "kp", 1.0))
            lp.ki = _getf(7, getattr(lp, "ki", 0.0))
            lp.kd = _getf(8, getattr(lp, "kd", 0.0))
            lp.err_min = _getf(9, getattr(lp, "err_min", None))
            lp.err_max = _getf(10, getattr(lp, "err_max", None))
            lp.i_min = _getf(11, getattr(lp, "i_min", None))
            lp.i_max = _getf(12, getattr(lp, "i_max", None))

            # parse OutMin/Max "lo..hi"
            it = self.table.item(r, 13)
            txt = (it.text() if it else "").strip()
            lo = getattr(lp, "out_min", None)
            hi = getattr(lp, "out_max", None)
            if txt:
                parts = txt.split("..")
                try:
                    lo = float(parts[0]) if parts[0] != "" else lo
                except Exception:
                    pass
                try:
                    hi = float(parts[1]) if len(parts) > 1 and parts[1] != "" else hi
                except Exception:
                    pass
            lp.out_min = lo
            lp.out_max = hi

        super().accept()


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
        self.setWindowTitle("MCC Control")
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

        # --- E-TC / Thermocouples state ---
        self.etc = None                  # ETCDevice (if you wire it later)
        self.tc_enabled = []             # list[int] of TC channel indexes (per config order)
        self.tc_types = []               # list[str] like "K","J",...
        self.tc_names = []               # list[str] display names
        self.tc_include = []             # list[bool] include in charts
        self.tc_hist_y = []              # list[deque] histories per TC channel (aligned to ai_hist_x)
        self.tc_titles = []              # list[str] "Name (TCx Type)"

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
            self.log_rx(f"[WARN] Could not auto-load defaults: {e}")

        try:
            etc_cfg = getattr(self.cfg, "etc", None)
            if etc_cfg:
                self.etc = ETCDevice(board=int(etc_cfg.get("board", 0)),
                                     sample_rate_hz=float(etc_cfg.get("sample_rate_hz", 10)))
                if self.etc.connect():
                    self.log_rx(f"[INFO] E-TC connected (board={self.etc.board}, {self.etc.rate:.1f} Hz)")
                else:
                    self.log_rx("[WARN] E-TC not connected")
        except Exception as e:
            self.log_rx(f"[WARN] E-TC init failed: {e}")

        self.pid_mgr = PIDManager(self)
        # Try auto-load PID.json (do not error if missing)
        try:
            import os
            if os.path.exists("PID.json"):
                self.pid_mgr.load_file("PID.json")
                self.pid_path = "PID.json"
                # refresh any on-screen PID pane
                self._pid_refresh_table_structure()
                self._pid_update_table_values()
                self.log_rx("[INFO] PID.json loaded.")
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

    def _mk_deque(self):
        """Deque sized to current span & rate."""
        cap = max(256, self._target_history_len(self.time_window_s))
        return deque(maxlen=cap)

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

        # TC (if present)
        if hasattr(self, "tc_hist_y") and self.tc_hist_y:
            self.tc_hist_y = [redq(ch, cap) for ch in self.tc_hist_y]

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
        f.addAction("Load Config...", self._act_load_cfg)
        f.addAction("Save Config As...", self._act_save_cfg)
        f.addAction("Edit Config...", self._act_edit_cfg)
        f.addSeparator()
        f.addAction("Load Script...", self._act_load_script)
        f.addAction("Save Script As...", self._act_save_script)
        f.addAction("Edit Script...", self._act_edit_script)
        f.addSeparator();
        f.addAction("Edit TCs...", self._act_edit_tc)
        f.addSeparator()
        f.addAction("Quit", self.close)
        v=m.addMenu("&View")
        v.addAction("Show Analog Charts", self.analog_win.show)
        v.addAction("Show Digital Chart", self.digital_win.show)
        v.addAction("Show Combined Chart", self.combined_win.show)
        v.addAction("Show Thermocouple Chart", self._open_tc_window)

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
        self.btn_pid_reset = QtWidgets.QPushButton("Reset PIDs")
        self.btn_pid_reset.clicked.connect(self._act_pid_reset)
        rgl.addWidget(self.btn_pid_setup, 8, 0, 1, 1)
        rgl.addWidget(self.btn_pid_reset, 8, 1, 1, 1)

        # ---- PID live table (up to 8 rows) ----
        self.pid_table = QtWidgets.QTableWidget(0, 11, self)
        self.pid_table.setHorizontalHeaderLabels([
            "Enable", "Type", "AIch", "OUTch", "AIValue", "Target", "PID u", "OutputValue", "P", "I", "D"
        ])
        self.pid_table.horizontalHeader().setStretchLastSection(True)
        rgl.addWidget(self.pid_table, 9, 0, 1, 2)

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

            # --- Thermocouples (E-TC) from cfg ---
            tcs = getattr(self.cfg, "thermocouples", [])
            self.tc_enabled, self.tc_types, self.tc_names, self.tc_include = [], [], [], []
            self.tc_hist_y = []
            for item in tcs:
                ch = int(item.get("ch", 0))
                name = str(item.get("name", f"TC{ch}"))
                ttype = str(item.get("type", "K")).upper()
                include = bool(item.get("include", True))
                self.tc_enabled.append(ch)
                self.tc_types.append(ttype)
                self.tc_names.append(name)
                self.tc_include.append(include)
                self.tc_hist_y.append(self._mk_deque())

            self.tc_titles = [f"{self.tc_names[i]} (TC{self.tc_enabled[i]} {self.tc_types[i]})" for i in
                              range(len(self.tc_enabled))]

            # Keep TC titles in any existing windows
            if hasattr(self, "tc_win") and self.tc_win:
                self.tc_win.set_names_units(self.tc_titles, ["°C"] * len(self.tc_titles))
            if hasattr(self, "combined_win") and self.combined_win and hasattr(self.combined_win, "set_tc_names_units"):
                self.combined_win.set_tc_names_units(self.tc_titles, ["°C"] * len(self.tc_titles))

        # ---- Cache names (used by charts & combined) ----
        # AI
        try:
            self.ai_names = [a.name if getattr(a, "name", None) else f"AI{i}" for i, a in enumerate(self.cfg.analogs)]
        except Exception:
            self.ai_names = [f"AI{i}" for i in range(8)]

        # DO
        try:
            self.do_names = [d.name if getattr(d, "name", None) else f"DO{i}" for i, d in
                             enumerate(self.cfg.digitalOutputs)]
        except Exception:
            self.do_names = [f"DO{i}" for i in range(8)]

        # AO
        try:
            self.ao_names = [a.name if getattr(a, "name", None) else f"AO{i}" for i, a in
                             enumerate(self.cfg.analogOutputs)]
        except Exception:
            self.ao_names = [f"AO{i}" for i in range(2)]

        # --- Thermocouples from config (optional) ---
        tcs = getattr(self.cfg, "thermocouples", [])
        # reset TC lists every time we apply config
        self.tc_enabled, self.tc_types, self.tc_names, self.tc_include = [], [], [], []
        self.tc_hist_y = []
        for item in tcs:
            ch = int(item.get("ch", 0))
            name = str(item.get("name", f"TC{ch}"))
            ttype = str(item.get("type", "K")).upper()
            include = bool(item.get("include", True))
            self.tc_enabled.append(ch)
            self.tc_types.append(ttype)
            self.tc_names.append(name)
            self.tc_include.append(include)
            self.tc_hist_y.append(self._mk_deque())

        # Titles like "MyProbe (TC0 K)"
        self.tc_titles = [
            f"{self.tc_names[i]} (TC{self.tc_enabled[i]} {self.tc_types[i]})"
            for i in range(len(self.tc_enabled))
        ]

        # If TC windows exist, refresh their titles (safe no-ops if they don't)
        if hasattr(self, "tc_win") and self.tc_win:
            self.tc_win.set_names_units(self.tc_titles, ["°C"] * len(self.tc_titles))
        if hasattr(self, "combined_win") and self.combined_win:
            if hasattr(self.combined_win, "set_tc_names_units"):
                self.combined_win.set_tc_names_units(self.tc_titles, ["°C"] * len(self.tc_titles))


        # --- Thermocouples from config ---
        tcs = getattr(self.cfg, "thermocouples", [])
        self.tc_enabled.clear();
        self.tc_types.clear();
        self.tc_names.clear();
        self.tc_include.clear();
        self.tc_hist_y.clear()

        for item in tcs:
            ch = int(item.get("ch", 0))
            name = str(item.get("name", f"TC{ch}"))
            ttype = str(item.get("type", "K")).upper()
            include = bool(item.get("include", True))
            self.tc_enabled.append(ch)
            self.tc_types.append(ttype)
            self.tc_names.append(name)
            self.tc_include.append(include)
            self.tc_hist_y.append(self._mk_deque())  # same length as AI histories

        # Make pretty series titles like "TC0 (K) — Name"
        self.tc_titles = [f"{self.tc_names[i]} (TC{self.tc_enabled[i]} {self.tc_types[i]})"
                          for i in range(len(self.tc_enabled))]

        if hasattr(self, "tc_win") and self.tc_win:
            self.tc_win.set_names_units(self.tc_titles, ["°C"] * len(self.tc_titles))
        if hasattr(self, "combined_win") and self.combined_win:
            # prepare combined for TC names (section is created dynamically on first data)
            if hasattr(self.combined_win, "set_tc_names_units"):
                self.combined_win.set_tc_names_units(self.tc_titles, ["°C"] * len(self.tc_titles))

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
        from config_editor import ConfigEditorDialog
        # snapshot for change detection
        try:
            from config_manager import ConfigManager
            import copy
            before = ConfigManager.to_dict(self.cfg)
        except Exception:
            before = None

        dlg = ConfigEditorDialog(self.cfg, self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.cfg = dlg.result_config()
            self._apply_cfg_to_ui()

            changed = True
            try:
                after = ConfigManager.to_dict(self.cfg)
                changed = (before is None) or (after != before)
            except Exception:
                changed = True

            if changed:
                mb = QtWidgets.QMessageBox(self)
                mb.setWindowTitle("Save Config?")
                mb.setText("Configuration has changed. Do you want to save it?")
                mb.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Save |
                                      QtWidgets.QMessageBox.StandardButton.Discard |
                                      QtWidgets.QMessageBox.StandardButton.Cancel)
                mb.setDefaultButton(QtWidgets.QMessageBox.StandardButton.Save)
                res = mb.exec()
                if res == QtWidgets.QMessageBox.StandardButton.Save:
                    # save to last known config path or ask
                    path = getattr(self, "config_path", None)
                    if not path:
                        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save Config As...", "config.json",
                                                                        "JSON (*.json)")
                    if path:
                        try:
                            from config_manager import ConfigManager
                            ConfigManager.save(path, self.cfg)
                            self.config_path = path
                            self.log_rx(f"[INFO] Config saved to {path}")
                        except Exception as e:
                            QtWidgets.QMessageBox.critical(self, "Config save error", str(e))
                elif res == QtWidgets.QMessageBox.StandardButton.Cancel:
                    return

    def _act_edit_tc(self):
        from config_editor import ConfigEditorDialog
        dlg = ConfigEditorDialog(self.cfg, self)  # cfg first, parent second
        # jump to the TC tab if present
        try:
            tabs = dlg.findChild(QtWidgets.QTabWidget)
            if tabs:
                for i in range(tabs.count()):
                    if "thermocouple" in tabs.tabText(i).lower():
                        tabs.setCurrentIndex(i)
                        break
        except Exception:
            pass
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.cfg = dlg.result_config()
            self._apply_cfg_to_ui()

    def _act_load_script(self, path=None, show_editor=True):
        #path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load Script", "", "JSON (*.json)")
        if not path:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load script.json", "", "JSON (*.json)")
        if not path:
            self.log_rx(f"[INFO] Load script file, path fail")
            return
        import json
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Critical: update the exact attribute(s) your editor uses
        self.script_path = path
        # Assign to both common names so runner + editor see the same object
        self.script_events = data
        self.script_model = data

        # Kick whatever table your UI already has (try common names, no new helpers)
        for fn in ("_script_refresh_table_structure",
                   "_script_refresh_table",
                   "_rebuild_script_table",
                   "_script_to_ui",
                   "_update_script_table"):
            if hasattr(self, fn):
                try:
                    getattr(self, fn)()
                    break
                except Exception:
                    pass
        self.log_rx(f"[INFO] Script loaded: {path}")
        self._act_edit_script()

    def _act_save_script(self):
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,"Save script.json","script.json","JSON (*.json)")
        if not path: return
        try:
            with open(path,"w",encoding="utf-8") as f: json.dump(self.script_events,f,indent=2)
            self.log_rx(f"Saved script: {path}")
        except Exception as e: QtWidgets.QMessageBox.critical(self,"Save error",str(e))

    def _act_edit_script(self):
        # Build the dialog safely without guessing the ctor order
        dlg = None
        try:
            from script_editor import ScriptEditorDialog
        except Exception:
            QtWidgets.QMessageBox.critical(self, "Error", "script_editor.py not found")
            return

        model = getattr(self, "script_model", {})  # whatever you store the script in

        # Try common signatures in a safe order
        try:
            # (parent, model)
            dlg = ScriptEditorDialog(self, model)
        except TypeError:
            try:
                # (model, parent)
                dlg = ScriptEditorDialog(model, self)
            except TypeError:
                try:
                    # (parent) only
                    dlg = ScriptEditorDialog(self)
                except TypeError:
                    # () only, then try to inject later
                    dlg = ScriptEditorDialog()

        # If dialog has a setter, inject the model
        for setter in ("set_model", "set_script", "load_script", "setData"):
            if hasattr(dlg, setter):
                try:
                    getattr(dlg, setter)(model)
                    break
                except Exception:
                    pass

        # Snapshot to detect changes
        try:
            import json
            before = json.dumps(model, sort_keys=True)
        except Exception:
            before = None

        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            # Pull result back if dialog exposes a getter
            new_model = None
            for getter in ("result_script", "get_model", "get_script", "data"):
                if hasattr(dlg, getter):
                    try:
                        new_model = getattr(dlg, getter)()
                        break
                    except Exception:
                        pass
            if new_model is not None:
                self.script_model = new_model

            changed = True
            try:
                import json
                after = json.dumps(self.script_model, sort_keys=True)
                changed = (before is None) or (after != before)
            except Exception:
                changed = True

            if changed:
                mb = QtWidgets.QMessageBox(self)
                mb.setWindowTitle("Save Script?")
                mb.setText("Script has changed. Do you want to save it?")
                mb.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Save |
                                      QtWidgets.QMessageBox.StandardButton.Discard |
                                      QtWidgets.QMessageBox.StandardButton.Cancel)
                mb.setDefaultButton(QtWidgets.QMessageBox.StandardButton.Save)
                res = mb.exec()
                if res == QtWidgets.QMessageBox.StandardButton.Save:
                    # Save to current path if you have one; otherwise reuse your existing menu handler
                    spath = getattr(self, "script_path", None)
                    if spath:
                        try:
                            import json
                            with open(spath, "w", encoding="utf-8") as f:
                                json.dump(self.script_model, f, indent=2)
                            self.log_rx(f"[INFO] Script saved to {spath}")
                        except Exception as e:
                            QtWidgets.QMessageBox.critical(self, "Script save error", str(e))
                    else:
                        self._act_save_script()
                elif res == QtWidgets.QMessageBox.StandardButton.Cancel:
                    return

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

    def _act_pid_reset(self):
        try:
            self.pid_mgr.reset_states()
            self._pid_update_table_values()
            self.log_rx("[INFO] PID states reset (I and D memory cleared)")
        except Exception as e:
            self.log_rx(f"[PID] reset failed: {e}")

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
            self.etc = None
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

            # --- E-TC connect (optional) ---
            try:
                from etc_device import ETCDevice
                b = self.cfg.betc.boardNum
                r = self.cfg.betc.sampleRateHz
                self.etc = ETCDevice(board=b, sample_rate_hz=r, log=self.log_rx)
                if self.etc.connect():
                    self.log_rx(f"[INFO] E-TC connected (board={b}, {r:.1f} Hz)")
                    self.log_rx(
                        f"DEBUG betc: boardNum={self.cfg.betc.boardNum}, sampleRateHz={self.cfg.betc.sampleRateHz}")
                    self.log_rx(f"DEBUG etc dict: {self.cfg.etc}")
                else:
                    self.log_rx("[WARN] E-TC not connected")
            except Exception as e:
                self.log_rx(f"[WARN] E-TC init failed: {e}")

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
            # ---- Build time axis safely, even if empty ----
            if not hasattr(self, "ai_hist_x") or self.ai_hist_x is None:
                x_arr = np.array([], dtype=float)
            else:
                x_arr = np.asarray(self.ai_hist_x, dtype=float)
            N = int(x_arr.shape[0])

            # ---- Slice AI history into per-channel lists (safe-blank if needed) ----
            ys_cut = []
            if hasattr(self, "ai_hist_y") and self.ai_hist_y:
                for dq in self.ai_hist_y:
                    arr = np.asarray(dq, dtype=float)
                    if arr.shape[0] > N and N > 0:
                        arr = arr[-N:]
                    ys_cut.append(arr)

            # AO history (if you keep it)
            ao_cut = []
            if hasattr(self, "ao_hist_y") and self.ao_hist_y:
                for dq in self.ao_hist_y:
                    arr = np.asarray(dq, dtype=float)
                    if arr.shape[0] > N and N > 0:
                        arr = arr[-N:]
                    ao_cut.append(arr)

            # DO history (bits as 0/1)
            do_cut = []
            if hasattr(self, "do_hist_y") and self.do_hist_y:
                for dq in self.do_hist_y:
                    arr = np.asarray(dq, dtype=float)
                    if arr.shape[0] > N and N > 0:
                        arr = arr[-N:]
                    do_cut.append(arr)

            # ========== Respect "Include" flags ==========
            # AI
            ai_cfg = list(getattr(self.cfg, "analogs", []))
            ai_includes = [getattr(a, "include", True) for a in ai_cfg]
            ai_names = [getattr(a, "name", f"AI{i}") for i, a in enumerate(ai_cfg)]
            ai_units = [getattr(a, "units", "") for a in ai_cfg]

            ys_cut_inc = [ys_cut[i] for i, inc in enumerate(ai_includes) if inc and i < len(ys_cut)]
            ai_names_inc = [ai_names[i] for i, inc in enumerate(ai_includes) if inc and i < len(ai_names)]
            ai_units_inc = [ai_units[i] for i, inc in enumerate(ai_includes) if inc and i < len(ai_units)]

            # DO
            do_cfg = list(getattr(self.cfg, "digitalOutputs", []))
            do_includes = [getattr(d, "include", True) for d in do_cfg]
            do_names = [getattr(d, "name", f"DO{i}") for i, d in enumerate(do_cfg)]
            do_cut_inc = [do_cut[i] for i, inc in enumerate(do_includes) if inc and i < len(do_cut)]
            do_names_inc = [do_names[i] for i, inc in enumerate(do_includes) if inc and i < len(do_names)]

            # TC (already built in histories)
            tc_series = None
            if getattr(self, "tc_hist_y", None):
                raw_tc = [np.asarray(dq, dtype=float) for dq in self.tc_hist_y]
                tc_series = [raw_tc[i] for i, inc in enumerate(self.tc_include) if inc]

            if getattr(self, "tc_win", None) and self.tc_win.isVisible() and tc_series is not None:
                tc_names_inc = [self.tc_titles[i] for i, inc in enumerate(self.tc_include) if inc]
                self.tc_win.set_names_units(tc_names_inc, ["°C"] * len(tc_names_inc))
                self.tc_win.set_data(x_arr, tc_series)


            # ---------- Push to windows ----------
            if getattr(self, "analog_win", None) and self.analog_win.isVisible():
                self.analog_win.set_names_units(ai_names_inc, ai_units_inc)
                self.analog_win.set_data(x_arr, ys_cut_inc)

            if getattr(self, "digital_win", None) and self.digital_win.isVisible():
                # set_names_units may not exist in older class; guard
                if hasattr(self.digital_win, "set_names_units"):
                    self.digital_win.set_names_units(do_names_inc, [""] * len(do_names_inc))
                self.digital_win.set_data(x_arr, do_cut_inc)

            if getattr(self, "tc_win", None) and self.tc_win.isVisible() and tc_series is not None:
                tc_names_inc = [self.tc_titles[i] for i, inc in enumerate(self.tc_include) if inc]
                self.tc_win.set_names_units(tc_names_inc, ["°C"] * len(tc_names_inc))
                self.tc_win.set_data(x_arr, tc_series)

            if getattr(self, "combined_win", None) and self.combined_win.isVisible():
                self.combined_win.set_data(x_arr, ys_cut_inc, ao_cut, do_cut_inc, tc=tc_series)

        except Exception as e:
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

            # --- Read a thermocouple block (hold values across M samples) ---
            tc_block_2d = None
            try:
                if self.etc and self.etc.connected and self.tc_enabled:
                    # Select only included TC channels for charts; PIDs can still use any TC index by config order
                    # Build ordered channel/type lists for the E-TC read
                    enabled_chs = self.tc_enabled
                    tc_types = self.tc_types
                    tc_block_2d, _nchtc = self.etc.read_block(enabled_chs, tc_types, M, sp)  # (M, n_tc)
            except Exception as e:
                self.log_rx(f"[E-TC] read error: {e}")
                tc_block_2d = None

            # --- PID: run loops on this block and apply outputs ---
            try:
                # ai_block_2d shape must be (nsamples, nch)
                ai_block_2d = arr.T  # we currently have (nch, M) -> transpose
                do_updates, ao_updates = self.pid_mgr.process_block(arr.T, float(sp), tc_block_2d=tc_block_2d)

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

            # --- Read a TC block (hold last values across this AI block) ---
            tc_block_2d = None
            try:
                if self.etc and getattr(self.etc, "connected", False) and self.tc_enabled:
                    enabled_chs = self.tc_enabled
                    tc_types = self.tc_types
                    tc_block_2d, _nchtc = self.etc.read_block(enabled_chs, tc_types, M, sp)  # (M, n_tc)
            except Exception as e:
                self.log_rx(f"[E-TC] read error: {e}")
                tc_block_2d = None

            # --- Extend TC histories ---
            if tc_block_2d is not None:
                n_tc = tc_block_2d.shape[1]
                for i in range(n_tc):
                    self.tc_hist_y[i].extend(tc_block_2d[:, i].tolist())

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

    def _open_tc_window(self):
        if not hasattr(self, "tc_win") or self.tc_win is None:
            names = getattr(self, "tc_titles", [])
            units = ["°C"] * len(names)
            self.tc_win = AnalogChartWindow(names, units)
            self.tc_win.setWindowTitle("Thermocouples (°C)")
            self.tc_win.spanChanged.connect(self._on_span_changed)
            self.tc_win.set_span(self.time_window_s)
        else:
            self.tc_win.set_names_units(self.tc_titles, ["°C"] * len(self.tc_titles))
        self.tc_win.show();
        self.tc_win.raise_()

    def _act_pid_setup(self):
        # if a path was chosen earlier but not yet loaded, ensure it’s loaded
        try:
            if getattr(self, "pid_path", None) and not getattr(self.pid_mgr, "loops", []):
                self.pid_mgr.load_file(self.pid_path)
        except Exception:
            pass
        dlg = PIDSetupDialog(self.pid_mgr, self)

        # Make a snapshot to detect changes
        try:
            before = [vars(type("X", (object,), {})())]  # dummy to force except
        except Exception:
            before = None
        # Safer snapshot:
        try:
            import copy
            before = copy.deepcopy([vars(lp).copy() for lp in self.pid_mgr.loops])
        except Exception:
            before = None

        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            # Rebuild running instances and reset states
            self.pid_mgr._rebuild_instances()
            self.pid_mgr.reset_states()
            self._pid_refresh_table_structure()
            self._pid_update_table_values()

            # Detect changes
            changed = True
            try:
                import copy
                after = copy.deepcopy([vars(lp).copy() for lp in self.pid_mgr.loops])
                changed = (before is None) or (after != before)
            except Exception:
                changed = True

            if changed:
                mb = QtWidgets.QMessageBox(self)
                mb.setWindowTitle("Save PID changes?")
                mb.setText("PID settings have changed. Do you want to save them?")
                mb.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Save |
                                      QtWidgets.QMessageBox.StandardButton.Discard |
                                      QtWidgets.QMessageBox.StandardButton.Cancel)
                mb.setDefaultButton(QtWidgets.QMessageBox.StandardButton.Save)
                res = mb.exec()
                if res == QtWidgets.QMessageBox.StandardButton.Save:
                    # save to last known pid_path or ask
                    path = getattr(self, "pid_path", None)
                    if not path:
                        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save PID As...", "PID.json",
                                                                        "JSON (*.json)")
                    if path:
                        try:
                            self.pid_mgr.save_file(path)
                            self.pid_path = path
                            self.log_rx(f"[INFO] PID saved to {path}")
                        except Exception as e:
                            QtWidgets.QMessageBox.critical(self, "PID save error", str(e))
                elif res == QtWidgets.QMessageBox.StandardButton.Cancel:
                    return

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

        # Digital: use control effort 'u' (P+I+D); OutputValue is 0/1 after NO/NC mapping
        for d in getattr(self.pid_mgr, "dloops", []):
            u = getattr(d, "last_u", float("nan"))  # computed PID sum
            out_bit = None
            if isinstance(getattr(d, "last_do_bit", None), bool):
                out_bit = 1 if d.last_do_bit else 0
            rt["digital"][(d.defn.ai_ch, d.defn.out_ch)] = (
                "digital", d.last_ai, u, out_bit
            )

        # Analog: show pre-clamp PID sum (last_u_pre) as PID Result; OutputValue is clamped AO volts
        for a in getattr(self.pid_mgr, "aloops", []):
            u_pre = getattr(a, "last_u_pre", getattr(a, "last_u", float("nan")))
            rt["analog"][(a.defn.ai_ch, a.defn.out_ch)] = (
                "analog", a.last_ai, u_pre, a.last_ao
            )

        loops = getattr(self.pid_mgr, "loops", [])
        for r, lp in enumerate(loops):
            ai_val = "—"
            pid_val = "—"  # <-- PID Result (P+I+D)
            out_val = "—"
            key = (lp.ai_ch, lp.out_ch)
            hit = rt[lp.kind].get(key)

            if hit:
                _, ai, u, outv = hit

                # AI value
                if isinstance(ai, (int, float)) and math.isfinite(ai):
                    ai_val = f"{float(ai):.4f}"

                # PID Result (control effort, pre-clamp for analog)
                if isinstance(u, (int, float)) and math.isfinite(u):
                    pid_val = f"{float(u):.4f}"

                # OutputValue
                if lp.kind == "digital":
                    out_val = "—" if outv is None else str(int(bool(outv)))
                else:
                    if isinstance(outv, (int, float)) and math.isfinite(outv):
                        out_val = f"{float(outv):.3f}"
                    else:
                        out_val = "—"

            for col, txt in [(4, ai_val), (6, pid_val), (7, out_val)]:
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
        self.pid_mgr.apply_loop_updates(row)       # <-- push into live core
        self._pid_update_table_values()
        # optional autosave:
        # self.pid_mgr.save_file("PID.json")
    except Exception as e:
        self.log_rx(f"[PID] target change failed: {e}")

def _pid_on_gain_changed(self, row, key, val):
    try:
        setattr(self.pid_mgr.loops[row], key, float(val))
        self.pid_mgr.apply_loop_updates(row)       # <-- push into live core
        self._pid_update_table_values()
        # optional autosave:
        # self.pid_mgr.save_file("PID.json")
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
if __name__ == "__main__":
    import sys
    from PyQt6 import QtWidgets, QtCore
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()

    # Only auto-open via a timer so the main window exists as the parent
    # Toggle any you want to auto-open:
    AUTO_OPEN_CONFIG = False
    AUTO_OPEN_SCRIPT = False
    AUTO_OPEN_PID    = False

    if AUTO_OPEN_CONFIG:
        QtCore.QTimer.singleShot(0, w._act_edit_cfg)
    if AUTO_OPEN_SCRIPT:
        QtCore.QTimer.singleShot(0, w._act_edit_script)
    if AUTO_OPEN_PID:
        QtCore.QTimer.singleShot(0, w._act_pid_setup)

    sys.exit(app.exec())
