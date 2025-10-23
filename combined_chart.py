from PyQt6 import QtCore, QtWidgets
import pyqtgraph as pg
import numpy as np


class CombinedChartWindow(QtWidgets.QMainWindow):
    spanChanged = QtCore.pyqtSignal(float)

    def __init__(self,
                 ai_names,
                 ai_units,
                 ao_names,
                 ao_units,
                 ao_default_range=(0.0, 10.0),
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Combined Chart")

        # Names / Units
        self._ai_names = list(ai_names or [])
        self._ai_units = list(ai_units or [])
        self._tc_names = []
        self._tc_units = []
        self._ao_names = list(ao_names or [])
        self._ao_units = list(ao_units or [])
        self._ao_default_range = tuple(ao_default_range) if ao_default_range else (0.0, 10.0)

        # State
        self.ai_rows = []; self.ai_plots = []; self.ai_curves = []; self.ai_locked = []; self.ai_ranges = []
        self.tc_rows = []; self.tc_plots = []; self.tc_curves = []; self.tc_locked = []; self.tc_ranges = []
        self.ao_rows = []; self.ao_plots = []; self.ao_curves = []; self.ao_locked = []; self.ao_ranges = []
        self.do_curves = []; self.do_offsets = np.array([], dtype=float); self.do_amp = 0.85

        # Control bars
        self._ai_ctrl = None
        self._tc_ctrl = None
        self._ao_ctrl = None

        # ===== UI =====
        central = QtWidgets.QWidget(); self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central); outer.setContentsMargins(6,6,6,6); outer.setSpacing(6)

        # X-span (sync with Analog window)
        span_bar = QtWidgets.QWidget(); span_layout = QtWidgets.QHBoxLayout(span_bar)
        span_layout.setContentsMargins(0,0,0,0); span_layout.setSpacing(8)
        span_layout.addWidget(QtWidgets.QLabel("X span (s):"))
        self.sp_span = QtWidgets.QDoubleSpinBox()
        # ↑ increase max to 3600s
        self.sp_span.setRange(0.01, 3600.0); self.sp_span.setDecimals(3); self.sp_span.setSingleStep(0.01); self.sp_span.setValue(5.0)
        self.sp_span.valueChanged.connect(lambda v: self.spanChanged.emit(float(v)))
        span_layout.addWidget(self.sp_span); span_layout.addStretch(1)
        outer.addWidget(span_bar)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        outer.addWidget(splitter, 1)

        # ===== Top area (AI + TC + AO) =====
        top = QtWidgets.QWidget(); top_v = QtWidgets.QVBoxLayout(top)
        top_v.setContentsMargins(0,0,0,0); top_v.setSpacing(6)
        self._top_v = top_v  # keep for adding TC rows later

        # AI
        top_v.addWidget(self._make_section_label("Analog Inputs"))
        self._ensure_ai_rows(len(self._ai_names))
        w_ai_ctrl = self._make_top_ctrl("AI", len(self._ai_names), self._ai_names)
        top_v.addWidget(w_ai_ctrl)
        for i in range(len(self._ai_names)):
            row, plt, curve = self._make_scalar_row(self._ai_names[i], (self._ai_units[i] if i < len(self._ai_units) else ""))
            top_v.addWidget(row)
            self.ai_rows[i] = row; self.ai_plots[i] = plt; self.ai_curves[i] = curve
            self.ai_locked[i] = False; self.ai_ranges[i] = (None, None)
            # disable context menu; allow mouse zoom/pan; hook range + mouse
            pi = plt.getPlotItem(); pi.getViewBox().setMenuEnabled(False)
            self._hook_xrange(plt); self._install_cursor(plt); self._hook_mouse(plt)

        # TC
        top_v.addWidget(self._make_section_label("Thermocouples (°C)"))
        self._ensure_tc_rows(0)
        w_tc_ctrl = self._make_top_ctrl("TC", 0, [])
        top_v.addWidget(w_tc_ctrl)

        # AO
        top_v.addWidget(self._make_section_label("Analog Outputs"))
        self._ensure_ao_rows(len(self._ao_names))
        w_ao_ctrl = self._make_top_ctrl("AO", len(self._ao_names), self._ao_names, default_range=self._ao_default_range)
        top_v.addWidget(w_ao_ctrl)
        for i in range(len(self._ao_names)):
            row, plt, curve = self._make_scalar_row(self._ao_names[i], (self._ao_units[i] if i < len(self._ao_units) else ""))
            top_v.addWidget(row)
            self.ao_rows[i] = row; self.ao_plots[i] = plt; self.ao_curves[i] = curve
            self.ao_locked[i] = True; self.ao_ranges[i] = (float(self._ao_default_range[0]), float(self._ao_default_range[1]))
            pi = plt.getPlotItem(); pi.enableAutoRange(axis='y', enable=False)
            pi.setYRange(self._ao_default_range[0], self._ao_default_range[1], padding=0.0)
            pi.getViewBox().setMenuEnabled(False)
            self._hook_xrange(plt); self._install_cursor(plt); self._hook_mouse(plt)

        splitter.addWidget(top)

        # ===== Bottom: Digital Outputs =====
        bottom = QtWidgets.QWidget(); do_v = QtWidgets.QVBoxLayout(bottom)
        do_v.setContentsMargins(0,0,0,0); do_v.setSpacing(4)
        do_v.addWidget(self._make_section_label("Digital Outputs", margin_top="2px"))

        self.do_plot = pg.PlotWidget()
        dpi = self.do_plot.getPlotItem()
        dpi.showAxis('bottom', show=True)
        vb = dpi.getViewBox(); vb.setMenuEnabled(False); vb.setMouseEnabled(x=True, y=False)
        do_v.addWidget(self.do_plot)
        self._ensure_do_rows(8)
        self._hook_xrange(self.do_plot); self._install_cursor(self.do_plot); self._hook_mouse(self.do_plot)

        # follow-tail behavior (auto-scroll)
        self._follow_tail = True
        self._latest_x = None
        self._suppress_range_cb = False

        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 4); splitter.setStretchFactor(1, 1)

        # Final sync (after rows exist)
        self._ctrl_sync("AI"); self._ctrl_sync("TC"); self._ctrl_sync("AO")

        # Cursor mode
        self._cursor_locked = False
        self._cursor_x = None

    # ===== Public API =====
    def set_follow_tail(self, enabled: bool):
        self._follow_tail = bool(enabled)

    def _hook_xrange(self, plt):
        vb = plt.getPlotItem().getViewBox()
        # On any x-range change: freeze unless near the latest sample
        vb.sigXRangeChanged.connect(self._on_vb_xrange_changed)

    def _on_vb_xrange_changed(self, vb, xrange):
        if self._suppress_range_cb:
            return
        if self._latest_x is None:
            return
        _, x_max = xrange
        span = float(self.sp_span.value())
        tol = max(0.1 * span, 0.5)  # tolerance: near right edge ⇒ resume follow-tail
        self._follow_tail = (x_max >= (self._latest_x - tol))

    def set_span(self, seconds: float):
        self.sp_span.blockSignals(True); self.sp_span.setValue(float(seconds)); self.sp_span.blockSignals(False)

    # ----- Cursor: per-plot scenes -----
    def _install_cursor(self, plt):
        line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen((200, 0, 0), width=1))
        line.setVisible(False)
        plt.addItem(line)
        if not hasattr(self, "_cursors"):
            self._cursors = []
        self._cursors.append({"plt": plt, "line": line})

    def _hook_mouse(self, plt):
        sc = plt.scene()
        sc.sigMouseMoved.connect(lambda pos, p=plt: self._on_mouse_moved_in_plot(p, pos))
        sc.sigMouseClicked.connect(lambda ev, p=plt: self._on_mouse_clicked_in_plot(p, ev))

    def _on_mouse_moved_in_plot(self, plt, pos):
        if self._cursor_locked:
            return
        vb = plt.getPlotItem().getViewBox()
        if not vb.sceneBoundingRect().contains(pos):
            # hide when outside
            for c in getattr(self, "_cursors", []):
                c["line"].setVisible(False)
            return
        mouse_point = vb.mapSceneToView(pos)
        x = mouse_point.x()
        self._cursor_x = x
        # share X across all plots
        for c in getattr(self, "_cursors", []):
            c["line"].setPos(x); c["line"].setVisible(True)

    def _on_mouse_clicked_in_plot(self, plt, ev):
        pos = ev.scenePos()
        if ev.button() == QtCore.Qt.MouseButton.LeftButton:
            self._cursor_locked = not self._cursor_locked
            if not self._cursor_locked:
                self._on_mouse_moved_in_plot(plt, pos)
            ev.accept()
        elif ev.button() == QtCore.Qt.MouseButton.RightButton:
            # pick current cursor X if visible anywhere
            x = None
            for c in getattr(self, "_cursors", []):
                if c["line"].isVisible():
                    x = c["line"].value(); break
            if x is not None:
                self._show_cursor_readout_dialog(float(x))
            ev.accept()

    def _show_cursor_readout_dialog(self, x):
        # collect included values at x
        rows = []
        # AI (included = visible rows at top)
        k_ai = sum(1 for r in self.ai_rows if r is not None and r.isVisible())
        for i in range(k_ai):
            nm = self._ai_names[i] if i < len(self._ai_names) else f"AI{i}"
            val = self._value_at_x(self._x, self._ai_data[i]) if self._ai_data and i < len(self._ai_data) else None
            rows.append((nm, val))
        # TC
        k_tc = sum(1 for r in self.tc_rows if r is not None and r.isVisible())
        for i in range(k_tc):
            nm = self._tc_names[i] if i < len(self._tc_names) else f"TC{i}"
            val = self._value_at_x(self._x, self._tc_data[i]) if self._tc_data and i < len(self._tc_data) else None
            rows.append((nm, val))
        # AO
        k_ao = sum(1 for r in self.ao_rows if r is not None and r.isVisible())
        for i in range(k_ao):
            nm = self._ao_names[i] if i < len(self._ao_names) else f"AO{i}"
            val = self._value_at_x(self._x, self._ao_data[i]) if self._ao_data and i < len(self._ao_data) else None
            rows.append((nm, val))
        # DO (boolean lanes)
        k_do = sum(1 for c in self.do_curves if c.isVisible())
        for i in range(k_do):
            nm = f"DO{i}"
            val = self._value_at_x(self._x, self._do_data[i]) if self._do_data and i < len(self._do_data) else None
            val = None if val is None else (1 if val > 0.5 else 0)
            rows.append((nm, val))

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"Values @ t={x:.3f}s")
        lay = QtWidgets.QFormLayout(dlg)
        for name, val in rows:
            if val is None or (isinstance(val, float) and not np.isfinite(val)):
                txt = "--"
            else:
                txt = f"{val:.6g}"
            lay.addRow(QtWidgets.QLabel(name), QtWidgets.QLabel(txt))
        dlg.resize(280, min(600, 24 * (len(rows) + 2)))
        dlg.exec()

    def _value_at_x(self, x_arr, y_arr):
        if x_arr is None or y_arr is None or len(x_arr) == 0:
            return None
        i = int(np.searchsorted(x_arr, self._cursor_x if self._cursor_x is not None else x_arr[-1], side="left"))
        i = max(0, min(i, len(x_arr) - 1))
        try:
            return float(y_arr[i])
        except Exception:
            return None

    def set_ai_names_units(self, names, units):
        self._ai_names = list(names or []); self._ai_units = list(units or [])
        self._ensure_ai_rows(len(self._ai_names))
        for i in range(len(self._ai_names)):
            unit_txt = f" [{self._ai_units[i]}]" if i < len(self._ai_units) and self._ai_units[i] else ""
            self.ai_plots[i].getPlotItem().setTitle(f"{self._ai_names[i]}{unit_txt}")
        self._ctrl_rebuild_items("AI", len(self._ai_names), self._ai_names)

    def set_tc_names_units(self, names, units):
        self._tc_names = list(names or []); self._tc_units = list(units or [])
        self._ensure_tc_rows(len(self._tc_names))
        for i in range(len(self._tc_names)):
            unit_txt = f" [{self._tc_units[i]}]" if i < len(self._tc_units) and self._tc_units[i] else ""
            self.tc_plots[i].getPlotItem().setTitle(f"{self._tc_names[i]}{unit_txt}")
        self._ctrl_rebuild_items("TC", len(self._tc_names), self._tc_names)

    def set_ao_names_units(self, names, units):
        self._ao_names = list(names or []); self._ao_units = list(units or [])
        self._ensure_ao_rows(len(self._ao_names))
        for i in range(len(self._ao_names)):
            unit_txt = f" [{self._ao_units[i]}]" if i < len(self._ao_units) and self._ao_units[i] else ""
            self.ao_plots[i].getPlotItem().setTitle(f"{self._ao_names[i]}{unit_txt}")
        self._ctrl_rebuild_items("AO", len(self._ao_names), self._ao_names)

    def set_data(self, x, ai_ys, ao_ys, do_ys, tc=None):
        # Keep references for cursor/readout
        self._x = np.asarray(x, dtype=float)
        x = self._x
        n = x.shape[0]

        self._ai_data = ai_ys
        self._ao_data = ao_ys
        self._do_data = do_ys
        self._tc_data = tc

        # --- AI (only included count = len(ai_ys))
        self._ensure_ai_rows(len(ai_ys))
        for i, y in enumerate(ai_ys):
            yy = np.asarray(y, dtype=float)
            if yy.shape[0] != n:
                if yy.shape[0] > n: yy = yy[-n:]
                else: yy = np.concatenate([np.full(n - yy.shape[0], np.nan), yy])
            self.ai_curves[i].setData(x, yy)
            if self.ai_locked[i] and self.ai_ranges[i][0] is not None:
                pi = self.ai_plots[i].getPlotItem()
                pi.enableAutoRange(axis='y', enable=False)
                ymin, ymax = self.ai_ranges[i]
                pi.setYRange(float(ymin), float(ymax), padding=0.0)
        self._show_ai_rows(len(ai_ys))

        # --- TC (optional)
        if tc is not None:
            self._ensure_tc_rows(len(tc))
            for i, y in enumerate(tc):
                yy = np.asarray(y, dtype=float)
                if yy.shape[0] != n:
                    if yy.shape[0] > n: yy = yy[-n:]
                    else: yy = np.concatenate([np.full(n - yy.shape[0], np.nan), yy])
                self.tc_curves[i].setData(x, yy)
                if self.tc_locked[i] and self.tc_ranges[i][0] is not None:
                    pi = self.tc_plots[i].getPlotItem()
                    pi.enableAutoRange(axis='y', enable=False)
                    ymin, ymax = self.tc_ranges[i]
                    pi.setYRange(float(ymin), float(ymax), padding=0.0)
            self._show_tc_rows(len(tc))
        else:
            self._show_tc_rows(0)

        # --- AO
        self._ensure_ao_rows(len(ao_ys))
        for i, y in enumerate(ao_ys):
            yy = np.asarray(y, dtype=float)
            if yy.shape[0] != n:
                if yy.shape[0] > n: yy = yy[-n:]
                else: yy = np.concatenate([np.full(n - yy.shape[0], np.nan), yy])
            self.ao_curves[i].setData(x, yy)
            if self.ao_locked[i] and self.ao_ranges[i][0] is not None:
                pi = self.ao_plots[i].getPlotItem()
                pi.enableAutoRange(axis='y', enable=False)
                ymin, ymax = self.ao_ranges[i]
                pi.setYRange(float(ymin), float(ymax), padding=0.0)
        self._show_ao_rows(len(ao_ys))  # ensure only included AO rows visible

        # --- DO (step mode: len(X) == len(Y) + 1)
        self._ensure_do_rows(len(do_ys))
        if n == 0:
            for c in self.do_curves:
                c.setData([], [])
            self._show_do_rows(0)
        else:
            if n >= 2 and np.isfinite(x[-1]) and np.isfinite(x[-2]):
                dt = float(x[-1] - x[-2])
                if not np.isfinite(dt) or dt == 0.0:
                    dt = 1.0
            else:
                dt = 1.0
            xx = np.concatenate([x, [x[-1] + dt]])  # n+1
            for i, y in enumerate(do_ys):
                yy = np.asarray(y, dtype=float)
                if yy.shape[0] != n:
                    if yy.shape[0] > n: yy = yy[-n:]
                    else: yy = np.concatenate([np.full(n - yy.shape[0], np.nan), yy])
                # On/off → 0/1 → offset stack for lanes
                on = (yy > 0.5).astype(float)
                on = self.do_offsets[i] + self.do_amp * on
                self.do_curves[i].setData(xx, on)
            self._show_do_rows(len(do_ys))

        # latest X (for follow-tail)
        self._latest_x = float(x[-1]) if n else None

        # follow-tail auto-scroll (only if not frozen by manual zoom)
        if self._follow_tail and n:
            left = self._latest_x - float(self.sp_span.value())
            right = self._latest_x
            self._suppress_range_cb = True
            try:
                for plt in self.ai_plots:
                    if plt: plt.getPlotItem().getViewBox().setXRange(left, right, padding=0.0)
                for plt in self.tc_plots:
                    if plt: plt.getPlotItem().getViewBox().setXRange(left, right, padding=0.0)
                for plt in self.ao_plots:
                    if plt: plt.getPlotItem().getViewBox().setXRange(left, right, padding=0.0)
                self.do_plot.getPlotItem().getViewBox().setXRange(left, right, padding=0.0)
            finally:
                self._suppress_range_cb = False

    # ===== Helpers =====
    def _make_section_label(self, txt, margin_top="0px"):
        lbl = QtWidgets.QLabel(txt)
        lbl.setStyleSheet(f"font-weight:600; margin-top:{margin_top};")
        return lbl

    def _make_scalar_row(self, name, unit):
        row = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(row); v.setContentsMargins(0,0,0,0)
        plt = pg.PlotWidget()
        pi = plt.getPlotItem()
        pi.showGrid(x=True, y=True, alpha=0.2); pi.hideAxis('bottom'); pi.getViewBox().setMenuEnabled(False)
        unit_txt = f" [{unit}]" if unit else ""
        pi.setTitle(f"{name}{unit_txt}")
        curve = plt.plot([], [], pen=pg.mkPen(width=2))
        v.addWidget(plt)
        return row, plt, curve

    def _make_top_ctrl(self, section: str, count: int, names: list[str], default_range=None):
        """Creates the control bar and stores its widgets; returns the QWidget for addWidget()."""
        w = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0,0,0,0); h.setSpacing(6)
        h.addWidget(QtWidgets.QLabel(f"{section} scale:"))

        sel = QtWidgets.QComboBox(); sel.addItems([f"{section}{i} - {names[i] if i < len(names) else str(i)}" for i in range(count)])
        h.addWidget(sel)

        chk = QtWidgets.QCheckBox("Auto"); chk.setChecked(section in ("AI","TC"))
        h.addWidget(chk); h.addSpacing(8)

        h.addWidget(QtWidgets.QLabel("Y min:"))
        sp_min = QtWidgets.QDoubleSpinBox(); sp_min.setRange(-1e12,1e12); sp_min.setDecimals(6); sp_min.setSingleStep(0.1)
        h.addWidget(sp_min)
        h.addWidget(QtWidgets.QLabel("Y max:"))
        sp_max = QtWidgets.QDoubleSpinBox(); sp_max.setRange(-1e12,1e12); sp_max.setDecimals(6); sp_max.setSingleStep(0.1)
        h.addWidget(sp_max)

        btn = QtWidgets.QPushButton("Apply"); h.addWidget(btn); h.addStretch(1)

        if section == "AO" and default_range is not None:
            sp_min.setValue(float(default_range[0])); sp_max.setValue(float(default_range[1]))

        ctrl = {"w": w, "sel": sel, "chk": chk, "mn": sp_min, "mx": sp_max, "btn": btn}
        if section == "AI": self._ai_ctrl = ctrl
        elif section == "TC": self._tc_ctrl = ctrl
        else: self._ao_ctrl = ctrl

        sel.currentIndexChanged.connect(lambda _=0, s=section: self._ctrl_sync(s))
        chk.toggled.connect(lambda b, s=section: self._ctrl_auto_toggled(s, b))
        btn.clicked.connect(lambda s=section: self._ctrl_apply(s))
        sp_min.editingFinished.connect(lambda s=section: self._ctrl_apply(s))
        sp_max.editingFinished.connect(lambda s=section: self._ctrl_apply(s))

        return w  # IMPORTANT: return QWidget, not dict

    def _ctrl_rebuild_items(self, section: str, count: int, names: list[str]):
        ctrl = self._ai_ctrl if section=="AI" else self._tc_ctrl if section=="TC" else self._ao_ctrl
        if not ctrl: return
        sel = ctrl["sel"]; cur = sel.currentIndex()
        sel.blockSignals(True); sel.clear()
        sel.addItems([f"{section}{i} - {names[i] if i < len(names) else str(i)}" for i in range(count)])
        sel.blockSignals(False)
        if 0 <= cur < count: sel.setCurrentIndex(cur)
        self._ctrl_sync(section)

    def _ctrl_sync(self, section: str):
        arr_locked, arr_ranges = (self.ai_locked, self.ai_ranges) if section=="AI" else (self.tc_locked, self.tc_ranges) if section=="TC" else (self.ao_locked, self.ao_ranges)
        ctrl = self._ai_ctrl if section=="AI" else self._tc_ctrl if section=="TC" else self._ao_ctrl
        plots = self.ai_plots if section=="AI" else self.tc_plots if section=="TC" else self.ao_plots
        if not ctrl or not plots: return
        idx = max(0, min(ctrl["sel"].currentIndex(), len(plots)-1))
        locked = arr_locked[idx]
        ctrl["chk"].blockSignals(True); ctrl["chk"].setChecked(not locked); ctrl["chk"].blockSignals(False)
        if locked and arr_ranges[idx][0] is not None:
            mn, mx = arr_ranges[idx]
        else:
            vr = plots[idx].getPlotItem().getViewBox().viewRange()[1]
            mn, mx = float(vr[0]), float(vr[1])
        ctrl["mn"].blockSignals(True); ctrl["mx"].blockSignals(True)
        ctrl["mn"].setValue(float(mn)); ctrl["mx"].setValue(float(mx))
        ctrl["mn"].blockSignals(False); ctrl["mx"].blockSignals(False)

    def _ctrl_auto_toggled(self, section: str, checked: bool):
        ctrl = self._ai_ctrl if section=="AI" else self._tc_ctrl if section=="TC" else self._ao_ctrl
        plots = self.ai_plots if section=="AI" else self.tc_plots if section=="TC" else self.ao_plots
        arr_locked, arr_ranges = (self.ai_locked, self.ai_ranges) if section=="AI" else (self.tc_locked, self.tc_ranges) if section=="TC" else (self.ao_locked, self.ao_ranges)
        if not ctrl or not plots: return
        idx = max(0, min(ctrl["sel"].currentIndex(), len(plots)-1))
        if checked:
            pi = plots[idx].getPlotItem(); pi.enableAutoRange(axis='y', enable=True)
            arr_locked[idx] = False; arr_ranges[idx] = (None, None)
        else:
            self._set_fixed_scale(section, idx, float(ctrl["mn"].value()), float(ctrl["mx"].value()))

    def _ctrl_apply(self, section: str):
        ctrl = self._ai_ctrl if section=="AI" else self._tc_ctrl if section=="TC" else self._ao_ctrl
        plots = self.ai_plots if section=="AI" else self.tc_plots if section=="TC" else self.ao_plots
        if not ctrl or not plots: return
        idx = max(0, min(ctrl["sel"].currentIndex(), len(plots)-1))
        self._set_fixed_scale(section, idx, float(ctrl["mn"].value()), float(ctrl["mx"].value()))

    def _set_fixed_scale(self, section: str, idx: int, ymin: float, ymax: float):
        plots = self.ai_plots if section=="AI" else self.tc_plots if section=="TC" else self.ao_plots
        arr_locked, arr_ranges = (self.ai_locked, self.ai_ranges) if section=="AI" else (self.tc_locked, self.tc_ranges) if section=="TC" else (self.ao_locked, self.ao_ranges)
        pi = plots[idx].getPlotItem(); vb = pi.getViewBox(); vb.enableAutoRange(y=False)
        vb.setRange(yRange=(float(ymin), float(ymax)), padding=0.0)
        arr_locked[idx] = True; arr_ranges[idx] = (float(ymin), float(ymax))
        ctrl = self._ai_ctrl if section=="AI" else self._tc_ctrl if section=="TC" else self._ao_ctrl
        if ctrl:
            ctrl["chk"].blockSignals(True); ctrl["chk"].setChecked(False); ctrl["chk"].blockSignals(False)

    # builders / show/hide
    def _ensure_ai_rows(self, n):
        cur = len(self.ai_rows)
        if n <= cur: return
        add = n - cur
        self.ai_rows.extend([None]*add); self.ai_plots.extend([None]*add); self.ai_curves.extend([None]*add)
        self.ai_locked.extend([False]*add); self.ai_ranges.extend([(None,None)]*add)

    def _ensure_tc_rows(self, n):
        cur = len(self.tc_rows)
        if n <= cur: return
        add = n - cur
        self.tc_rows.extend([None]*add); self.tc_plots.extend([None]*add); self.tc_curves.extend([None]*add)
        self.tc_locked.extend([False]*add); self.tc_ranges.extend([(None,None)]*add)
        # build missing rows under the TC control bar
        if self._tc_ctrl and self._top_v:
            insert_base = self._top_v.indexOf(self._tc_ctrl["w"])
            for i in range(cur, n):
                name = self._tc_names[i] if i < len(self._tc_names) else f"TC{i}"
                unit = self._tc_units[i] if i < len(self._tc_units) else "°C"
                row, plt, curve = self._make_scalar_row(name, unit)
                self._top_v.insertWidget(insert_base + 1 + (i - cur), row)
                self.tc_rows[i] = row; self.tc_plots[i] = plt; self.tc_curves[i] = curve
                # hook new plot widgets
                pi = plt.getPlotItem(); pi.getViewBox().setMenuEnabled(False)
                self._hook_xrange(plt); self._install_cursor(plt); self._hook_mouse(plt)

    def _ensure_ao_rows(self, n):
        cur = len(self.ao_rows)
        if n <= cur: return
        add = n - cur
        self.ao_rows.extend([None]*add); self.ao_plots.extend([None]*add); self.ao_curves.extend([None]*add)
        self.ao_locked.extend([True]*add); self.ao_ranges.extend([tuple(self._ao_default_range)]*add)

    def _ensure_do_rows(self, n):
        cur = len(self.do_curves)
        if n <= cur: return
        add = n - cur
        for _ in range(add):
            c = self.do_plot.plot([], [], stepMode=True, pen=pg.mkPen(width=2))
            self.do_curves.append(c)
        self.do_offsets = np.arange(len(self.do_curves), dtype=float)

    def _show_ai_rows(self, k: int):
        for i, row in enumerate(self.ai_rows):
            if row is not None: row.setVisible(i < k)

    def _show_tc_rows(self, k: int):
        for i, row in enumerate(self.tc_rows):
            if row is not None: row.setVisible(i < k)

    def _show_ao_rows(self, k: int):
        for i, row in enumerate(self.ao_rows):
            if row is not None: row.setVisible(i < k)

    def _show_do_rows(self, k: int):
        for i, c in enumerate(self.do_curves):
            c.setVisible(i < k)
