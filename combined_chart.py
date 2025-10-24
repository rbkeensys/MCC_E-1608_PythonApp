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
        self._ao_names = list(ao_names or [])
        self._ao_units = list(ao_units or [])
        self._tc_names = []
        self._tc_units = []
        self._ao_default_range = tuple(ao_default_range) if ao_default_range else (0.0, 10.0)

        # State arrays
        self.ai_rows = []; self.ai_plots = []; self.ai_curves = []; self.ai_locked = []; self.ai_ranges = []
        self.ao_rows = []; self.ao_plots = []; self.ao_curves = []; self.ao_locked = []; self.ao_ranges = []
        self.tc_rows = []; self.tc_plots = []; self.tc_curves = []; self.tc_locked = []; self.tc_ranges = []
        self.do_curves = []; self.do_offsets = np.array([], dtype=float); self.do_amp = 0.85

        # Cursor / follow-tail / pause
        self._cursors = []
        self._cursor_x = None
        self._last_scene_pos = None
        self._follow_tail = True
        self._latest_x = None
        self._suppress_range_cb = False
        self._paused = False

        # last data (for popup + pause caching)
        self._x = np.array([], dtype=float)
        self._ai_data = []
        self._ao_data = []
        self._tc_data = None
        self._do_data = []
        self._user_interacting = False  # only freeze on manual zoom/pan

        # ===== UI =====
        central = QtWidgets.QWidget(); self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central); outer.setContentsMargins(6,6,6,6); outer.setSpacing(6)

        # Top controls: span + Reset/Pause/Resume
        ctrl_bar = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(ctrl_bar)
        h.setContentsMargins(0,0,0,0); h.setSpacing(8)
        h.addWidget(QtWidgets.QLabel("X span (s):"))
        self.sp_span = QtWidgets.QDoubleSpinBox()
        self.sp_span.setRange(0.01, 3600.0); self.sp_span.setDecimals(3); self.sp_span.setSingleStep(0.01); self.sp_span.setValue(5.0)
        self.sp_span.valueChanged.connect(lambda v: self.spanChanged.emit(float(v)))
        h.addWidget(self.sp_span)

        self.btn_reset = QtWidgets.QPushButton("Reset")
        self.btn_reset.clicked.connect(self._on_reset_clicked)
        h.addWidget(self.btn_reset)

        self.btn_pause = QtWidgets.QPushButton("Pause")
        self.btn_pause.clicked.connect(self._on_pause_clicked)
        h.addWidget(self.btn_pause)

        self.btn_resume = QtWidgets.QPushButton("Resume")
        self.btn_resume.clicked.connect(self._on_resume_clicked)
        h.addWidget(self.btn_resume)

        h.addStretch(1)
        outer.addWidget(ctrl_bar)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        outer.addWidget(splitter, 1)

        # ===== Top area containers in the requested order =====
        top = QtWidgets.QWidget(); top_v = QtWidgets.QVBoxLayout(top)
        top_v.setContentsMargins(0,0,0,0); top_v.setSpacing(6)

        # ---- Analog Inputs ----
        self._ai_box = QtWidgets.QWidget(); self._ai_v = QtWidgets.QVBoxLayout(self._ai_box)
        self._ai_v.setContentsMargins(0,0,0,0); self._ai_v.setSpacing(6)
        self._ai_v.addWidget(self._make_section_label("Analog Inputs"))
        self._ai_ctrl = self._make_top_ctrl("AI", len(self._ai_names), self._ai_names)
        self._ai_v.addWidget(self._ai_ctrl["w"])
        self._ensure_ai_rows(len(self._ai_names), build_ui=True)

        # ---- Analog Outputs ----
        self._ao_box = QtWidgets.QWidget(); self._ao_v = QtWidgets.QVBoxLayout(self._ao_box)
        self._ao_v.setContentsMargins(0,0,0,0); self._ao_v.setSpacing(6)
        self._ao_v.addWidget(self._make_section_label("Analog Outputs"))
        self._ao_ctrl = self._make_top_ctrl("AO", len(self._ao_names), self._ao_names, default_range=self._ao_default_range)
        self._ao_v.addWidget(self._ao_ctrl["w"])
        self._ensure_ao_rows(len(self._ao_names), build_ui=True)

        # ---- Thermocouples ----
        self._tc_box = QtWidgets.QWidget(); self._tc_v = QtWidgets.QVBoxLayout(self._tc_box)
        self._tc_v.setContentsMargins(0,0,0,0); self._tc_v.setSpacing(6)
        self._tc_v.addWidget(self._make_section_label("Thermocouples (°C)"))
        self._tc_ctrl = self._make_top_ctrl("TC", 0, [])
        self._tc_v.addWidget(self._tc_ctrl["w"])
        self._ensure_tc_rows(0, build_ui=True)

        # Add containers in precise order
        # Add containers in precise order WITH a vertical splitter so each can be resized
        self._sec_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self._sec_splitter.setHandleWidth(8)
        self._sec_splitter.setChildrenCollapsible(False)

        self._sec_splitter.addWidget(self._ai_box)
        self._sec_splitter.addWidget(self._ao_box)
        self._sec_splitter.addWidget(self._tc_box)

        # Give AI most space, then TC, then AO by default (tweak to taste)
        self._sec_splitter.setStretchFactor(0, 3)  # AI
        self._sec_splitter.setStretchFactor(1, 1)  # AO
        self._sec_splitter.setStretchFactor(2, 2)  # TC

        top_v.addWidget(self._sec_splitter)

        splitter.addWidget(top)

        # ===== Bottom: Digital Outputs =====
        bottom = QtWidgets.QWidget(); do_v = QtWidgets.QVBoxLayout(bottom)
        do_v.setContentsMargins(0,0,0,0); do_v.setSpacing(4)
        do_v.addWidget(self._make_section_label("Digital Outputs", margin_top="2px"))

        self.do_plot = pg.PlotWidget()
        dpi = self.do_plot.getPlotItem()
        dpi.showAxis('bottom', show=True)
        vb = dpi.getViewBox()
        vb.setMenuEnabled(False)
        vb.setMouseEnabled(x=True, y=False)
        dpi.enableAutoRange(x=False, y=True)
        do_v.addWidget(self.do_plot)
        self.do_plot.viewport().installEventFilter(self)
        self._ensure_do_rows(8)

        # Hooks
        self._hook_xrange(self.do_plot); self._install_cursor(self.do_plot); self._hook_mouse(self.do_plot)

        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 4); splitter.setStretchFactor(1, 1)

        # Link all X-axes to DO so zoom/pan applies to all
        self._link_all_x_to_do()

        # Initial ctrl sync
        self._ctrl_sync("AI"); self._ctrl_sync("AO"); self._ctrl_sync("TC")

        # Popup handle
        self._popup = None

    # ===== Public API =====
    def eventFilter(self, obj, ev):
        t = ev.type()
        # QEvent.Type numbers are stable in PyQt6
        from PyQt6.QtCore import QEvent, Qt
        if t == QEvent.Type.Wheel:
            self._user_interacting = True
        elif t == QEvent.Type.MouseButtonPress:
            self._user_interacting = True
        elif t == QEvent.Type.MouseButtonRelease:
            self._user_interacting = False
        # don't consume the event
        return False

    def set_follow_tail(self, enabled: bool):
        self._follow_tail = bool(enabled)

    def set_span(self, seconds: float):
        self.sp_span.blockSignals(True); self.sp_span.setValue(float(seconds)); self.sp_span.blockSignals(False)

    def set_ai_names_units(self, names, units):
        self._ai_names = list(names or []); self._ai_units = list(units or [])
        self._ctrl_rebuild_items("AI", len(self._ai_names), self._ai_names)
        self._ensure_ai_rows(len(self._ai_names), build_ui=True)
        for i in range(len(self._ai_names)):
            unit_txt = f" [{self._ai_units[i]}]" if i < len(self._ai_units) and self._ai_units[i] else ""
            self.ai_plots[i].getPlotItem().setTitle(f"{self._ai_names[i]}{unit_txt}")
        self._link_all_x_to_do()

    def set_tc_names_units(self, names, units):
        self._tc_names = list(names or []); self._tc_units = list(units or [])
        self._ctrl_rebuild_items("TC", len(self._tc_names), self._tc_names)
        self._ensure_tc_rows(len(self._tc_names), build_ui=True)
        for i in range(len(self._tc_names)):
            unit_txt = f" [{self._tc_units[i]}]" if i < len(self._tc_units) and self._tc_units[i] else ""
            self.tc_plots[i].getPlotItem().setTitle(f"{self._tc_names[i]}{unit_txt}")
        self._link_all_x_to_do()

    def set_ao_names_units(self, names, units):
        self._ao_names = list(names or []); self._ao_units = list(units or [])
        self._ctrl_rebuild_items("AO", len(self._ao_names), self._ao_names)
        self._ensure_ao_rows(len(self._ao_names), build_ui=True)
        for i in range(len(self._ao_names)):
            unit_txt = f" [{self._ao_units[i]}]" if i < len(self._ao_units) and self._ao_units[i] else ""
            self.ao_plots[i].getPlotItem().setTitle(f"{self._ao_names[i]}{unit_txt}")
        self._link_all_x_to_do()

    # ===== Core render =====
    def set_data(self, x, ai_ys, ao_ys, do_ys, tc=None):
        # Always update caches so popup "Current" can be live even when paused
        self._x = np.asarray(x, dtype=float)
        self._ai_data = ai_ys
        self._ao_data = ao_ys
        self._do_data = do_ys
        self._tc_data = tc
        n = self._x.shape[0]
        self._latest_x = float(self._x[-1]) if n else None

        if self._paused:
            # Do not update curves or ranges; keep the frozen display
            return

        x = self._x

        # --- AI
        self._ensure_ai_rows(len(ai_ys), build_ui=False)
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

        # --- AO
        self._ensure_ao_rows(len(ao_ys), build_ui=False)
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
        self._show_ao_rows(len(ao_ys))

        # --- TC (optional)
        if tc is not None:
            self._ensure_tc_rows(len(tc), build_ui=False)
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

        # --- DO (step mode)
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
            xx = np.concatenate([x, [x[-1] + dt]])  # n+1 for stepMode
            for i, y in enumerate(do_ys):
                yy = np.asarray(y, dtype=float)
                if yy.shape[0] != n:
                    if yy.shape[0] > n: yy = yy[-n:]
                    else: yy = np.concatenate([np.full(n - yy.shape[0], np.nan), yy])
                on = (yy > 0.5).astype(float)
                on = self.do_offsets[i] + self.do_amp * on
                self.do_curves[i].setData(xx, on)
            self._show_do_rows(len(do_ys))

        # follow-tail auto-scroll
        if self._follow_tail and n:
            left = self._latest_x - float(self.sp_span.value())
            right = self._latest_x
            vb = self.do_plot.getPlotItem().getViewBox()
            (cur_left, cur_right), _ = vb.viewRange()
            # update only if it actually changed (prevents jitter)
            if abs(cur_left - left) > 1e-9 or abs(cur_right - right) > 1e-9:
                self._suppress_range_cb = True
                try:
                    vb.setXRange(left, right, padding=0.0)
                finally:
                    self._suppress_range_cb = False

        # Keep cursor under mouse after refresh
        self._sync_cursor_to_mouse()

    # ===== Reset / Pause / Resume =====
    def _on_reset_clicked(self):
        # Cursor off
        self._cursor_x = None
        self._last_scene_pos = None
        for c in self._cursors:
            c["line"].setVisible(False)

        # Y scales: AI & TC auto; AO to default
        for arr_plots, arr_locked, arr_ranges in [
            (self.ai_plots, self.ai_locked, self.ai_ranges),
            (self.tc_plots, self.tc_locked, self.tc_ranges),
        ]:
            for i, plt in enumerate(arr_plots):
                if plt is None: continue
                pi = plt.getPlotItem(); pi.enableAutoRange(axis='y', enable=True)
                arr_locked[i] = False; arr_ranges[i] = (None, None)
        for i, plt in enumerate(self.ao_plots):
            if plt is None: continue
            pi = plt.getPlotItem(); pi.enableAutoRange(axis='y', enable=False)
            pi.setYRange(self._ao_default_range[0], self._ao_default_range[1], padding=0.0)
            self.ao_locked[i] = True; self.ao_ranges[i] = tuple(self._ao_default_range)

        # X: follow-tail on to current span
        self._paused = False
        self._follow_tail = True
        if self._latest_x is not None:
            left = self._latest_x - float(self.sp_span.value())
            right = self._latest_x
            self._suppress_range_cb = True
            try:
                for plt in self._all_plots_except_do():
                    plt.getPlotItem().getViewBox().setXRange(left, right, padding=0.0)
                self.do_plot.getPlotItem().getViewBox().setXRange(left, right, padding=0.0)
            finally:
                self._suppress_range_cb = False

    def _on_pause_clicked(self):
        self._paused = True   # curves stop updating, caches keep filling

    def _on_resume_clicked(self):
        self._paused = False  # curves update again; keep zoom level
        # Do not force follow-tail; user controls zoom. Use Reset to re-enable follow-tail if desired.

    # ===== Interaction / cursor / linking =====
    def _link_all_x_to_do(self):
        for plt in self._all_plots_except_do():
            plt.setXLink(self.do_plot)

    def _all_plots_except_do(self):
        out = []
        for arr in (self.ai_plots, self.ao_plots, self.tc_plots):
            for p in arr:
                if p: out.append(p)
        return out

    def _hook_xrange(self, plt):
        vb = plt.getPlotItem().getViewBox()
        vb.sigXRangeChanged.connect(self._on_vb_xrange_changed)

    def _on_vb_xrange_changed(self, vb, xrange):
        # Ignore programmatic range changes while auto-following; we set the range in set_data().
        if self._suppress_range_cb:
            return
        if self._follow_tail and not self._paused:
            return
        # When paused (or follow-tail disabled), still keep the cursor synced.
        self._sync_cursor_to_mouse()

    def _install_cursor(self, plt):
        line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen((200, 0, 0), width=1))
        line.setVisible(False)
        plt.addItem(line)
        self._cursors.append({"plt": plt, "line": line})

    def _hook_mouse(self, plt):
        sc = plt.scene()
        sc.sigMouseMoved.connect(lambda pos, p=plt: self._on_mouse_moved_in_plot(p, pos))
        sc.sigMouseClicked.connect(lambda ev, p=plt: self._on_mouse_clicked_in_plot(p, ev))

    def _on_mouse_moved_in_plot(self, plt, pos):
        self._last_scene_pos = pos
        vb = plt.getPlotItem().getViewBox()
        if not vb.sceneBoundingRect().contains(pos):
            # hide cursor when leaving the window
            for c in self._cursors:
                c["line"].setVisible(False)
            return
        mouse_point = vb.mapSceneToView(pos)
        x = mouse_point.x()
        self._cursor_x = x
        for c in self._cursors:
            c["line"].setPos(x); c["line"].setVisible(True)

    def _on_mouse_clicked_in_plot(self, plt, ev):
        # Left click no longer freezes; right click opens live popup
        if ev.button() == QtCore.Qt.MouseButton.RightButton:
            self._open_or_update_popup()

    def _sync_cursor_to_mouse(self):
        pos = self._last_scene_pos
        if pos is None:
            return
        for c in self._cursors:
            vb = c["plt"].getPlotItem().getViewBox()
            if vb.sceneBoundingRect().contains(pos):
                x = vb.mapSceneToView(pos).x()
                self._cursor_x = x
                for cc in self._cursors:
                    cc["line"].setPos(x); cc["line"].setVisible(True)
                return
        # outside all plots -> hide
        for cc in self._cursors:
            cc["line"].setVisible(False)

    # ===== Live popup =====
    def _open_or_update_popup(self):
        if self._popup is None:
            self._popup = _LiveReadoutDialog(self)
        self._popup.show()
        self._popup.raise_()
        self._popup.activateWindow()

    def _value_at_x(self, x_arr, y_arr, xq):
        if x_arr is None or y_arr is None or len(x_arr) == 0 or xq is None:
            return None
        i = int(np.searchsorted(x_arr, xq, side="left"))
        i = max(0, min(i, len(x_arr) - 1))
        try:
            return float(y_arr[i])
        except Exception:
            return None

    # ===== Controls and builders =====
    def _make_section_label(self, txt, margin_top="0px"):
        lbl = QtWidgets.QLabel(txt)
        lbl.setStyleSheet(f"font-weight:600; margin-top:{margin_top};")
        return lbl

    def _make_scalar_row(self, name, unit):
        row = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(row); v.setContentsMargins(0,0,0,0)
        plt = pg.PlotWidget()
        pi = plt.getPlotItem()
        pi.showGrid(x=True, y=True, alpha=0.2)
        pi.hideAxis('bottom')
        pi.getViewBox().setMenuEnabled(False)
        pi.enableAutoRange(x=False, y=True)  # X never auto; Y can auto
        unit_txt = f" [{unit}]" if unit else ""
        pi.setTitle(f"{name}{unit_txt}")
        curve = plt.plot([], [], pen=pg.mkPen(width=2))
        v.addWidget(plt)
        plt.viewport().installEventFilter(self)
        self._hook_xrange(plt); self._install_cursor(plt); self._hook_mouse(plt)
        return row, plt, curve

    def _make_top_ctrl(self, section: str, count: int, names: list[str], default_range=None):
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

        ctrl = {"w": w, "sel": sel, "chk": chk, "mn": sp_min, "mx": sp_max, "btn": btn}

        if section == "AO" and default_range is not None:
            sp_min.setValue(float(default_range[0])); sp_max.setValue(float(default_range[1]))

        sel.currentIndexChanged.connect(lambda _=0, s=section: self._ctrl_sync(s))
        chk.toggled.connect(lambda b, s=section: self._ctrl_auto_toggled(s, b))
        btn.clicked.connect(lambda s=section: self._ctrl_apply(s))
        sp_min.editingFinished.connect(lambda s=section: self._ctrl_apply(s))
        sp_max.editingFinished.connect(lambda s=section: self._ctrl_apply(s))

        return ctrl

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
        arr_locked, arr_ranges = (
            (self.ai_locked, self.ai_ranges) if section == "AI"
            else (self.tc_locked, self.tc_ranges) if section == "TC"
            else (self.ao_locked, self.ao_ranges)
        )
        ctrl = self._ai_ctrl if section == "AI" else self._tc_ctrl if section == "TC" else self._ao_ctrl
        plots = self.ai_plots if section == "AI" else self.tc_plots if section == "TC" else self.ao_plots
        if not ctrl or not plots:
            return
        idx = max(0, min(ctrl["sel"].currentIndex(), len(plots) - 1))
        locked = arr_locked[idx]

        ctrl["chk"].blockSignals(True)
        ctrl["chk"].setChecked(not locked)   # Auto = not locked
        ctrl["chk"].blockSignals(False)

        if locked and arr_ranges[idx][0] is not None:
            mn, mx = arr_ranges[idx]
        else:
            vr = plots[idx].getPlotItem().getViewBox().viewRange()[1]
            mn, mx = float(vr[0]), float(vr[1])

        ctrl["mn"].blockSignals(True); ctrl["mx"].blockSignals(True)
        ctrl["mn"].setValue(float(mn)); ctrl["mx"].setValue(float(mx))
        ctrl["mn"].blockSignals(False); ctrl["mx"].blockSignals(False)

    def _ctrl_auto_toggled(self, section: str, checked: bool):
        plots = self.ai_plots if section=="AI" else self.tc_plots if section=="TC" else self.ao_plots
        arr_locked, arr_ranges = (self.ai_locked, self.ai_ranges) if section=="AI" else (self.tc_locked, self.tc_ranges) if section=="TC" else (self.ao_locked, self.ao_ranges)
        if not plots: return
        idx = max(0, min((self._ai_ctrl if section=="AI" else self._tc_ctrl if section=="TC" else self._ao_ctrl)["sel"].currentIndex(), len(plots)-1))
        if checked:
            pi = plots[idx].getPlotItem(); pi.enableAutoRange(axis='y', enable=True)
            arr_locked[idx] = False; arr_ranges[idx] = (None, None)
        else:
            self._set_fixed_scale(section, idx, float((self._ai_ctrl if section=="AI" else self._tc_ctrl if section=="TC" else self._ao_ctrl)["mn"].value()),
                                  float((self._ai_ctrl if section=="AI" else self._tc_ctrl if section=="TC" else self._ao_ctrl)["mx"].value()))

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

    # ===== Builders / show/hide =====
    def _ensure_ai_rows(self, n, build_ui=False):
        cur = len(self.ai_rows)
        if n > cur:
            add = n - cur
            self.ai_rows.extend([None]*add); self.ai_plots.extend([None]*add); self.ai_curves.extend([None]*add)
            self.ai_locked.extend([False]*add); self.ai_ranges.extend([(None,None)]*add)
        if build_ui:
            for i in range(n):
                if self.ai_rows[i] is None:
                    name = self._ai_names[i] if i < len(self._ai_names) else f"AI{i}"
                    unit = self._ai_units[i] if i < len(self._ai_units) else ""
                    row, plt, curve = self._make_scalar_row(name, unit)
                    self._ai_v.addWidget(row)
                    self.ai_rows[i] = row;
                    self.ai_plots[i] = plt;
                    self.ai_curves[i] = curve
                    # Link later if DO exists
                    if hasattr(self, "do_plot") and self.do_plot:
                        plt.setXLink(self.do_plot)

    def _ensure_ao_rows(self, n, build_ui=False):
        cur = len(self.ao_rows)
        if n > cur:
            add = n - cur
            self.ao_rows.extend([None]*add); self.ao_plots.extend([None]*add); self.ao_curves.extend([None]*add)
            self.ao_locked.extend([True]*add); self.ao_ranges.extend([tuple(self._ao_default_range)]*add)
        if build_ui:
            for i in range(n):
                if self.ao_rows[i] is None:
                    name = self._ao_names[i] if i < len(self._ao_names) else f"AO{i}"
                    unit = self._ao_units[i] if i < len(self._ao_units) else ""
                    row, plt, curve = self._make_scalar_row(name, unit)
                    self._ao_v.addWidget(row)
                    self.ao_rows[i] = row;
                    self.ao_plots[i] = plt;
                    self.ao_curves[i] = curve
                    if hasattr(self, "do_plot") and self.do_plot:
                        plt.setXLink(self.do_plot)
                    pi = self.ao_plots[i].getPlotItem()
                    pi.enableAutoRange(axis='y', enable=False)
                    pi.setYRange(self._ao_default_range[0], self._ao_default_range[1], padding=0.0)

    def _ensure_tc_rows(self, n, build_ui=False):
        cur = len(self.tc_rows)
        if n > cur:
            add = n - cur
            self.tc_rows.extend([None]*add); self.tc_plots.extend([None]*add); self.tc_curves.extend([None]*add)
            self.tc_locked.extend([False]*add); self.tc_ranges.extend([(None,None)]*add)
        if build_ui:
            for i in range(n):
                if self.tc_rows[i] is None:
                    name = self._tc_names[i] if i < len(self._tc_names) else f"TC{i}"
                    unit = self._tc_units[i] if i < len(self._tc_units) else "°C"
                    row, plt, curve = self._make_scalar_row(name, unit)
                    self._tc_v.addWidget(row)
                    self.tc_rows[i] = row; self.tc_plots[i] = plt; self.tc_curves[i] = curve
                    if hasattr(self, "do_plot") and self.do_plot:
                        plt.setXLink(self.do_plot)

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

    def _show_ao_rows(self, k: int):
        for i, row in enumerate(self.ao_rows):
            if row is not None: row.setVisible(i < k)

    def _show_tc_rows(self, k: int):
        for i, row in enumerate(self.tc_rows):
            if row is not None: row.setVisible(i < k)

    def _show_do_rows(self, k: int):
        for i, c in enumerate(self.do_curves):
            c.setVisible(i < k)


class _LiveReadoutDialog(QtWidgets.QDialog):
    """Live-updating values dialog. Mode toggles between 'follow cursor' and 'current' (latest)."""

    def __init__(self, parent: CombinedChartWindow):
        super().__init__(parent)
        self.setWindowTitle("Values")
        self._parent = parent
        self._mode = "cursor"  # or "current"

        self._form = QtWidgets.QFormLayout(self)
        self._form.setContentsMargins(10,10,10,10)
        self._form.setSpacing(6)

        # Rows: created on first timer tick when we know visible counts
        self._rows = []  # list of (name_label, value_label)

        # Buttons
        btn_bar = QtWidgets.QHBoxLayout()
        self.btn_mode = QtWidgets.QPushButton("Current")
        self.btn_mode.clicked.connect(self._toggle_mode)
        btn_bar.addWidget(self.btn_mode)
        btn_bar.addStretch(1)
        self._form.addRow(btn_bar)

        # Timer
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(150)  # ~6-7 Hz
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self.resize(320, 480)

    def showEvent(self, e):
        # Rebuild rows (visible traces might have changed)
        self._clear_rows()
        # Restart the live updates every time it’s shown
        try:
            self._timer.start()
        except Exception:
            pass
        return super().showEvent(e)

    def _clear_rows(self):
        # Keep the first row (the button bar), remove the rest
        # QFormLayout rows: 0 = button bar we added in __init__
        try:
            while self._form.rowCount() > 1:
                self._form.removeRow(1)
        except Exception:
            # Fallback: brute force remove all and re-add button bar
            pass
        self._rows = []

    def closeEvent(self, e):
        try: self._timer.stop()
        except: pass
        return super().closeEvent(e)

    def _toggle_mode(self):
        if self._mode == "cursor":
            self._mode = "current"
            self.btn_mode.setText("Follow Cursor")
        else:
            self._mode = "cursor"
            self.btn_mode.setText("Current")

    def _tick(self):
        p: CombinedChartWindow = self._parent

        # Build rows lazily using *visible* traces in the parent
        if not self._rows:
            names = []
            # AI
            for i, r in enumerate(p.ai_rows):
                if r is not None and r.isVisible():
                    nm = p._ai_names[i] if i < len(p._ai_names) else f"AI{i}"
                    names.append(nm)
            # AO
            for i, r in enumerate(p.ao_rows):
                if r is not None and r.isVisible():
                    nm = p._ao_names[i] if i < len(p._ao_names) else f"AO{i}"
                    names.append(nm)
            # TC
            for i, r in enumerate(p.tc_rows):
                if r is not None and r.isVisible():
                    nm = p._tc_names[i] if i < len(p._tc_names) else f"TC{i}"
                    names.append(nm)
            # DO
            for i, c in enumerate(p.do_curves):
                if c.isVisible():
                    names.append(f"DO{i}")

            for nm in names:
                lnm = QtWidgets.QLabel(nm)
                lval = QtWidgets.QLabel("--")
                self._form.addRow(lnm, lval)
                self._rows.append((lnm, lval))

        # Determine X to sample at
        if self._mode == "current":
            xq = p._latest_x
        else:
            xq = p._cursor_x

        # Update values
        idx = 0
        # AI
        for i, r in enumerate(p.ai_rows):
            if r is not None and r.isVisible():
                val = p._value_at_x(p._x, p._ai_data[i] if i < len(p._ai_data) else None, xq)
                self._rows[idx][1].setText("--" if val is None else f"{val:.6g}")
                idx += 1
        # AO
        for i, r in enumerate(p.ao_rows):
            if r is not None and r.isVisible():
                val = p._value_at_x(p._x, p._ao_data[i] if i < len(p._ao_data) else None, xq)
                self._rows[idx][1].setText("--" if val is None else f"{val:.6g}")
                idx += 1
        # TC
        for i, r in enumerate(p.tc_rows):
            if r is not None and r.isVisible():
                arr = (p._tc_data[i] if (p._tc_data and i < len(p._tc_data)) else None)
                val = p._value_at_x(p._x, arr, xq)
                self._rows[idx][1].setText("--" if val is None else f"{val:.6g}")
                idx += 1
        # DO
        for i, c in enumerate(p.do_curves):
            if c.isVisible():
                arr = (p._do_data[i] if i < len(p._do_data) else None)
                val = p._value_at_x(p._x, arr, xq)
                if val is None:
                    txt = "--"
                else:
                    try: txt = "1" if float(val) > 0.5 else "0"
                    except: txt = "--"
                self._rows[idx][1].setText(txt)
                idx += 1
