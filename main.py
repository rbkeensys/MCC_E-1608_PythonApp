
import sys, time, json
from PyQt6 import QtCore, QtWidgets

from config_manager import ConfigManager, AppConfig
from daq_driver import DaqDriver, DaqError
from filters import OnePoleLPF
from analog_chart import AnalogChartWindow
from digital_chart import DigitalChartWindow
from script_runner import ScriptRunner
from config_editor import ConfigEditorDialog
from script_editor import ScriptEditorDialog

class ScaleDialog(QtWidgets.QDialog):
    """Dialog for per-trace scaling: Auto or manual Y min/max."""
    def __init__(self, parent, idx, y_min, y_max):
        super().__init__(parent)
        self.setWindowTitle(f"Scale AI{idx}")
        self.setModal(True)  # ensure focus stays here
        form = QtWidgets.QFormLayout(self)

        self.chk_auto = QtWidgets.QCheckBox("Auto-scale")
        self.chk_auto.setChecked(False)  # default to manual (editable)
        form.addRow(self.chk_auto)

        def make_spin(val: float) -> QtWidgets.QDoubleSpinBox:
            sp = QtWidgets.QDoubleSpinBox()
            sp.setRange(-1e12, 1e12)
            sp.setDecimals(6)
            sp.setSingleStep(0.1)
            sp.setKeyboardTracking(True)
            sp.setAccelerated(False)
            sp.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.UpDownArrows)
            sp.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
            # make sure the embedded line edit is writable
            sp.lineEdit().setReadOnly(False)
            sp.setValue(float(val))
            return sp

        self.sp_min = make_spin(y_min)
        self.sp_max = make_spin(y_max)
        form.addRow("Y min", self.sp_min)
        form.addRow("Y max", self.sp_max)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

        self.chk_auto.toggled.connect(self._on_auto)
        self._on_auto(self.chk_auto.isChecked())

    def _on_auto(self, checked: bool):
        # Disable the fields only when Auto is ON
        self.sp_min.setDisabled(checked)
        self.sp_max.setDisabled(checked)

    def result_values(self):
        return self.chk_auto.isChecked(), self.sp_min.value(), self.sp_max.value()

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MCC E-1608 Control")
        self.resize(1300, 800)

        # Model / state
        self.cfg = AppConfig()
        self.daq: DaqDriver | None = None
        self.ai_filters = [OnePoleLPF(0.0, self.cfg.sampleRateHz) for _ in range(8)]
        self.ai_filter_enabled = [False]*8
        self.ai_hist_x: list[float] = []
        self.ai_hist_y: list[list[float]] = [[] for _ in range(8)]
        self.do_hist_y: list[list[int]] = [[] for _ in range(8)]
        self.hist_max_pts = 1000
        self.time_window_s = 5.0     # X-axis window in seconds (0.01..10)
        self.ui_rate_hz = 50.0       # GUI update rate (Hz), not the DAQ sample rate
        self.script_events: list[dict] = []

        # Chart windows (open via View menu)
        self.analog_win = AnalogChartWindow(
            [a.name for a in self.cfg.analogs],
            [a.units for a in self.cfg.analogs],
        )
        self.analog_win.traceClicked.connect(self._on_trace_clicked)
        self.analog_win.requestScale.connect(self._on_request_scale)

        self.digital_win = DigitalChartWindow()

        # UI
        self._build_menu()
        self._build_central()
        self._build_status_panes()

        # Timers, script, apply config...
        self.loop_timer = QtCore.QTimer(self)
        self.loop_timer.timeout.connect(self._loop)
        self.loop_timer.start(int(1000/self.ui_rate_hz))

        self.script = ScriptRunner(self._set_do)
        self.script.tick.connect(self._on_script_tick)

        self._apply_cfg_to_ui()

    # ---------- UI Construction ----------
    def _build_menu(self):
        menubar = self.menuBar()
        filem = menubar.addMenu("&File")
        filem.addAction("Load Config...", self._act_load_cfg)
        filem.addAction("Save Config As...", self._act_save_cfg)
        filem.addAction("Edit Config...", self._act_edit_cfg)
        filem.addSeparator()
        filem.addAction("Load Script...", self._act_load_script)
        filem.addAction("Save Script As...", self._act_save_script)
        filem.addAction("Edit Script...", self._act_edit_script)
        filem.addSeparator()
        filem.addAction("Quit", self.close)

        viewm = menubar.addMenu("&View")
        viewm.addAction("Show Analog Charts", self.analog_win.show)
        viewm.addAction("Show Digital Chart", self.digital_win.show)

    def _build_central(self):
        cw = QtWidgets.QWidget()
        self.setCentralWidget(cw)
        layout = QtWidgets.QGridLayout(cw)

        # Left pane: DO controls
        self.do_panel = QtWidgets.QGroupBox("Digital Outputs")
        layout.addWidget(self.do_panel, 0, 0)
        gl = QtWidgets.QGridLayout(self.do_panel)
        self.do_btns = []
        self.do_chk_no = []
        self.do_chk_mom = []
        self.do_time = []
        for i in range(8):
            btn = QtWidgets.QPushButton(f"{i}: DO")
            btn.setCheckable(True)
            btn.setStyleSheet(
                "QPushButton{background:#4caf50;color:white;} "
                "QPushButton:checked{background:#d32f2f;}"
            )
            btn.clicked.connect(lambda checked, idx=i: self._on_do_clicked(idx, checked))
            btn.pressed.connect(lambda idx=i: self._on_do_pressed(idx))     # momentary press
            btn.released.connect(lambda idx=i: self._on_do_released(idx))   # momentary release
            gl.addWidget(btn, i, 0)
            self.do_btns.append(btn)

            chk_no = QtWidgets.QCheckBox("Normally Open")
            gl.addWidget(chk_no, i, 1)
            self.do_chk_no.append(chk_no)

            chk_m = QtWidgets.QCheckBox("Momentary")
            gl.addWidget(chk_m, i, 2)
            self.do_chk_mom.append(chk_m)

            sp = QtWidgets.QDoubleSpinBox()
            sp.setSuffix(" s")
            sp.setRange(0.0, 3600.0)
            sp.setDecimals(3)
            sp.setSingleStep(0.1)
            sp.setValue(0.0)
            gl.addWidget(sp, i, 3)
            self.do_time.append(sp)

        # Right pane: AO sliders + time controls + run controls
        right = QtWidgets.QGroupBox("Analog Outputs / Timebase / Script")
        layout.addWidget(right, 0, 1)
        rgl = QtWidgets.QGridLayout(right)

        # AO sliders
        self.ao_sliders = []
        self.ao_labels = []
        for i in range(2):
            lab = QtWidgets.QLabel(f"AO{i}: 0.00 V")
            rgl.addWidget(lab, i*2, 0, 1, 2)
            s = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
            s.setRange(-1000, 1000)  # maps to -10..+10 V; then clamped by config
            s.valueChanged.connect(lambda v, idx=i: self._on_ao_slider(idx, v))
            rgl.addWidget(s, i*2+1, 0, 1, 2)
            self.ao_labels.append(lab)
            self.ao_sliders.append(s)

        # X-axis time window (seconds)
        rgl.addWidget(QtWidgets.QLabel("Time window (s)"), 4, 0)
        self.time_spin = QtWidgets.QDoubleSpinBox()
        self.time_spin.setRange(0.01, 10.0)
        self.time_spin.setDecimals(3)
        self.time_spin.setSingleStep(0.01)
        self.time_spin.setValue(self.time_window_s)
        self.time_spin.valueChanged.connect(self._on_time_window)
        rgl.addWidget(self.time_spin, 4, 1)

        # Connection + Script controls
        self.btn_connect = QtWidgets.QPushButton("Connect")
        self.btn_connect.clicked.connect(self._act_connect)
        rgl.addWidget(self.btn_connect, 5, 0)

        self.btn_run = QtWidgets.QPushButton("Run Script")
        self.btn_run.clicked.connect(self._act_run_script)
        rgl.addWidget(self.btn_run, 6, 0)

        self.btn_stop = QtWidgets.QPushButton("Stop/Pause Script")
        self.btn_stop.clicked.connect(self._act_stop_script)
        rgl.addWidget(self.btn_stop, 6, 1)

        self.btn_reset = QtWidgets.QPushButton("Reset Script")
        self.btn_reset.clicked.connect(self._act_reset_script)
        rgl.addWidget(self.btn_reset, 7, 0)

    def _build_status_panes(self):
        dock_tx = QtWidgets.QDockWidget("Sent (Tx)", self)
        dock_rx = QtWidgets.QDockWidget("Received / Debug (Rx)", self)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, dock_tx)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, dock_rx)

        self.tx_text = QtWidgets.QPlainTextEdit()
        self.tx_text.setReadOnly(True)
        self.rx_text = QtWidgets.QPlainTextEdit()
        self.rx_text.setReadOnly(True)
        dock_tx.setWidget(self.tx_text)
        dock_rx.setWidget(self.rx_text)

    # ---------- Logging ----------
    def log_tx(self, msg: str):
        self.tx_text.appendPlainText(msg)

    def log_rx(self, msg: str):
        self.rx_text.appendPlainText(msg)

    # ---------- Config ----------
    def _apply_cfg_to_ui(self):
        for i in range(8):
            self.do_btns[i].setText(f"{i}: {self.cfg.digitalOutputs[i].name}")
            self.do_chk_no[i].setChecked(self.cfg.digitalOutputs[i].normallyOpen)
            self.do_chk_mom[i].setChecked(self.cfg.digitalOutputs[i].momentary)
            self.do_time[i].setValue(self.cfg.digitalOutputs[i].actuationTime)

        for i in range(2):
            a = self.cfg.analogOutputs[i]
            mn = max(-10.0, min(10.0, a.minV))
            mx = max(-10.0, min(10.0, a.maxV))
            if mn > mx:
                mn, mx = mx, mn
            self.ao_sliders[i].setMinimum(int(mn*100))
            self.ao_sliders[i].setMaximum(int(mx*100))
            self.ao_sliders[i].setValue(int(a.startupV*100))
            self.ao_labels[i].setText(f"AO{i}: {a.startupV:.2f} V ({a.name})")

        for i in range(8):
            self.ai_filters[i].set_fs(self.ui_rate_hz)
            self.ai_filters[i].set_cutoff(self.cfg.analogs[i].cutoffHz)
            self.ai_filter_enabled[i] = (self.cfg.analogs[i].cutoffHz > 0.0)

        self.analog_win.setWindowTitle("Analog Inputs — " + ", ".join([a.name for a in self.cfg.analogs]))

    # ---------- Actions ----------
    def _act_load_cfg(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load config.json", "", "JSON (*.json)")
        if not path:
            return
        self.cfg = ConfigManager.load(path)
        self._apply_cfg_to_ui()
        self.log_rx(f"Loaded config: {path}")
        self._act_edit_cfg()

    def _act_save_cfg(self):
        for i in range(8):
            self.cfg.digitalOutputs[i].normallyOpen = self.do_chk_no[i].isChecked()
            self.cfg.digitalOutputs[i].momentary = self.do_chk_mom[i].isChecked()
            self.cfg.digitalOutputs[i].actuationTime = float(self.do_time[i].value())
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save config.json", "config.json", "JSON (*.json)")
        if not path:
            return
        ConfigManager.save(path, self.cfg)
        self.log_rx(f"Saved config: {path}")

    def _act_edit_cfg(self):
        dlg = ConfigEditorDialog(self, self.cfg)
        if dlg.exec():
            self.cfg = dlg.updated_config()
            self._apply_cfg_to_ui()

    def _act_load_script(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load script.json", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.script_events = json.load(f)
            self.script.set_events(self.script_events)
            self.log_rx(f"Loaded script: {path}")
            self._act_edit_script()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Script error", str(e))

    def _act_save_script(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save script.json", "script.json", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.script_events, f, indent=2)
            self.log_rx(f"Saved script: {path}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Save error", str(e))

    def _act_edit_script(self):
        dlg = ScriptEditorDialog(self, self.script_events)
        if dlg.exec():
            self.script_events = dlg.result_events()
            self.script.set_events(self.script_events)

    def _act_connect(self):
        if self.daq and self.daq.connected:
            self.daq.disconnect()
            self.daq = None
            self.btn_connect.setText("Connect")
            return
        try:
            self.daq = DaqDriver(self.cfg.boardNum, self.log_tx, self.log_rx)
            self.daq.log_ai_reads = False
            self.daq.connect()
            self.btn_connect.setText("Disconnect")
            for i in range(2):
                self._apply_ao_slider(i)
        except DaqError as e:
            QtWidgets.QMessageBox.critical(self, "DAQ error", str(e))

    def _act_run_script(self):
        self.script.set_events(self.script_events)
        self.script.run()
        self.log_rx("Script: RUN")

    def _act_stop_script(self):
        self.script.stop()
        self.log_rx("Script: STOP/PAUSE")

    def _act_reset_script(self):
        self.script.reset()
        self.log_rx("Script: RESET")

    # ---------- Callbacks ----------
    def _on_time_window(self, v):
        self.time_window_s = float(v)

    def _on_trace_clicked(self, idx):
        pass

    # def _on_request_scale(self, idx):
    #     y_min, y_max = self.analog_win.get_y_range(idx)
    #     dlg = ScaleDialog(self, idx, y_min, y_max)
    #     if dlg.exec():
    #         auto, mn, mx = dlg.result_values()
    #         if auto:
    #             self.analog_win.autoscale(idx)
    #         else:
    #             if mx < mn:
    #                 mn, mx = mx, mn
    #             self.analog_win.set_fixed_scale(idx, mn, mx)

    def _on_request_scale(self, idx):
        # Quick, rock-solid native prompts
        auto = QtWidgets.QMessageBox.question(
            self, f"Scale AI{idx}",
            "Use Auto-scale?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
        ) == QtWidgets.QMessageBox.StandardButton.Yes

        if auto:
            self.analog_win.autoscale(idx)
            return

        y_min, ok1 = QtWidgets.QInputDialog.getDouble(self, "Y min", "Enter Y min:", 0.0, -1e12, 1e12, 6)
        if not ok1: return
        y_max, ok2 = QtWidgets.QInputDialog.getDouble(self, "Y max", "Enter Y max:", 1.0, -1e12, 1e12, 6)
        if not ok2: return
        if y_max < y_min: y_min, y_max = y_max, y_min
        self.analog_win.set_fixed_scale(idx, y_min, y_max)


    def _on_ao_slider(self, idx, raw_val):
        v = 0.01 * float(raw_val)
        a = self.cfg.analogOutputs[idx]
        v = max(max(-10.0, a.minV), min(min(10.0, a.maxV), v))
        self.ao_labels[idx].setText(f"AO{idx}: {v:.2f} V ({a.name})")
        if self.daq and self.daq.connected:
            self.daq.set_ao_volts(idx, v)

    def _apply_ao_slider(self, idx):
        self._on_ao_slider(idx, self.ao_sliders[idx].value())

    # ---------- Digital Output handlers ----------
    def _on_do_pressed(self, idx):
        if self.do_chk_mom[idx].isChecked():
            no = self.do_chk_no[idx].isChecked()
            self.do_btns[idx].setChecked(True)
            bit_state = True if no else False
            if self.daq and self.daq.connected:
                self._set_do(idx, bit_state)

    def _on_do_released(self, idx):
        if self.do_chk_mom[idx].isChecked():
            no = self.do_chk_no[idx].isChecked()
            self.do_btns[idx].setChecked(False)
            bit_state = False if no else True
            if self.daq and self.daq.connected:
                self._set_do(idx, bit_state)

    def _on_do_clicked(self, idx, checked):
        no = self.do_chk_no[idx].isChecked()
        momentary = self.do_chk_mom[idx].isChecked()
        act_time = float(self.do_time[idx].value())

        if momentary:
            return

        if act_time > 0.0:
            if checked:
                bit_state = True if no else False
                if self.daq and self.daq.connected:
                    self._set_do(idx, bit_state)
                QtCore.QTimer.singleShot(int(act_time * 1000),
                                         lambda: self._release_do(idx, no))
            else:
                self._release_do(idx, no)
        else:
            bit_state = (checked if no else (not checked))
            if self.daq and self.daq.connected:
                self._set_do(idx, bit_state)

    def _release_do(self, idx, normally_open: bool):
        self.do_btns[idx].setChecked(False)
        bit_state = False if normally_open else True
        if self.daq and self.daq.connected:
            self._set_do(idx, bit_state)

    def _set_do(self, idx, state: bool):
        if self.daq and self.daq.connected:
            try:
                self.daq.set_do_bit(idx, state)
            except Exception as e:
                self.log_rx(f"DO error: {e}")

    def _on_script_tick(self, t, relays):
        for i, st in enumerate(relays[:8]):
            blk = self.do_btns[i].blockSignals(True)
            self.do_btns[i].setChecked(bool(st))
            self.do_btns[i].blockSignals(blk)

    # ---------- Main Loop ----------
    def _loop(self):
        now = time.perf_counter()
        if not self.ai_hist_x or (now - self.ai_hist_x[-1]) > 0.5:
            self._prune_history()

        if self.daq and self.daq.connected:
            try:
                yvals = []
                for i in range(8):
                    v = self.daq.read_ai_volts(i)
                    a = self.cfg.analogs[i]
                    v_cal = v * a.slope + a.offset
                    if self.ai_filter_enabled[i] and a.cutoffHz > 0.0:
                        self.ai_filters[i].set_fs(self.ui_rate_hz)
                        v_cal = self.ai_filters[i].process(v_cal)
                    yvals.append(v_cal)
            except Exception as e:
                self.log_rx(f"AI read error: {e}")
                yvals = [0.0]*8
        else:
            yvals = [0.0]*8

        self.ai_hist_x.append(now)
        for i in range(8):
            self.ai_hist_y[i].append(yvals[i])

        t0 = self.ai_hist_x[0] if self.ai_hist_x else now
        x = [t - t0 for t in self.ai_hist_x]
        x_cut, ys_cut = self._tail_by_time(x, self.ai_hist_y, self.time_window_s)
        self.analog_win.set_data(x_cut, ys_cut)

        do_states = [1 if self.do_btns[i].isChecked() else 0 for i in range(8)]
        for i in range(8):
            self.do_hist_y[i].append(do_states[i])
            if len(self.do_hist_y[i]) > len(self.ai_hist_x):
                self.do_hist_y[i] = self.do_hist_y[i][-len(self.ai_hist_x):]

        _, do_cut = self._tail_by_time(x, self.do_hist_y, self.time_window_s)
        self.digital_win.set_data(x_cut, do_cut)

    def _prune_history(self):
        max_pts = int(self.ui_rate_hz * 12)
        if len(self.ai_hist_x) > max_pts:
            trim = len(self.ai_hist_x) - max_pts
            self.ai_hist_x = self.ai_hist_x[trim:]
            for i in range(8):
                self.ai_hist_y[i] = self.ai_hist_y[i][trim:]
                self.do_hist_y[i] = self.do_hist_y[i][trim:]

    @staticmethod
    def _tail_by_time(x, ys, window_s):
        if not x:
            return [], [ [] for _ in ys ]
        t_end = x[-1]
        t_start = t_end - window_s
        start_idx = 0
        for i, xv in enumerate(x):
            if xv >= t_start:
                start_idx = i
                break
        x_cut = x[start_idx:]
        ys_cut = [ y[start_idx:] for y in ys ]
        return x_cut, ys_cut


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
