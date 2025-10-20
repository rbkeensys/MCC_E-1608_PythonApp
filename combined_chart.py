# combined_chart.py
from PyQt6 import QtCore, QtWidgets
import pyqtgraph as pg
import numpy as np


class CombinedChartWindow(QtWidgets.QMainWindow):
    spanChanged = QtCore.pyqtSignal(float)  # <— NEW
    """
    Big window with:
      - AI rows (8) with per-row Auto / Ymin / Ymax / Apply (headers hidden; controlled via top bar)
      - AO rows (2) same controls (default fixed 0..10V; top bar)
      - DO block (1 plot with 8 step traces, fixed scale, no pan/zoom)
    DO block is in a resizable bottom pane via QSplitter.
    External API:
      set_ai_names_units(names, units)
      set_ao_names_units(names, units)
      set_data(x, ai_ys, ao_ys, do_ys)
    """

    def __init__(self, ai_names, ai_units, ao_names, ao_units, ao_default_range=(0.0, 10.0)):
        super().__init__()
        self.setWindowTitle("Combined Chart")

        self._ai_names = list(ai_names)
        self._ai_units = list(ai_units)
        self._ao_names = list(ao_names)
        self._ao_units = list(ao_units)
        self._ao_default_range = (float(ao_default_range[0]), float(ao_default_range[1]))

        cw = QtWidgets.QWidget()
        self.setCentralWidget(cw)
        outer = QtWidgets.QVBoxLayout(cw)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        # ====== TOP X-SPAN BAR (NEW) ======
        span_bar = QtWidgets.QWidget()
        span_layout = QtWidgets.QHBoxLayout(span_bar)
        span_layout.setContentsMargins(0,0,0,0); span_layout.setSpacing(8)
        span_layout.addWidget(QtWidgets.QLabel("X span (s):"))
        self.sp_span = QtWidgets.QDoubleSpinBox()
        self.sp_span.setRange(0.01, 100.0)   # match analog window
        self.sp_span.setDecimals(3)
        self.sp_span.setSingleStep(0.01)
        self.sp_span.setValue(5.0)
        self.sp_span.valueChanged.connect(lambda v: self.spanChanged.emit(float(v)))
        span_layout.addWidget(self.sp_span)
        span_layout.addStretch(1)
        outer.addWidget(span_bar)

        # ========= Top section (AI + AO) =========
        top_section = QtWidgets.QWidget()
        top_v = QtWidgets.QVBoxLayout(top_section)
        top_v.setContentsMargins(0, 0, 0, 0)
        top_v.setSpacing(6)

        # ---------- AI section ----------
        ai_label = QtWidgets.QLabel("Analog Inputs")
        ai_label.setStyleSheet("font-weight:600;")
        top_v.addWidget(ai_label)

        # Top control bar for AI (one bar, select which AIx to edit)
        top_v.addWidget(self._make_top_ctrl("AI", len(self._ai_names), self._ai_names))

        self.ai_rows = []          # list[QWidget] for stretch
        self.ai_plots = []         # list[PlotWidget]
        self.ai_curves = []        # list[PlotDataItem]
        self.ai_locked = [False] * len(self._ai_names)
        self.ai_ranges = [(None, None)] * len(self._ai_names)
        self.ai_headers = []       # list of tuples (chk_auto, sp_min, sp_max, btn_apply, lbl_chan)

        for i, nm in enumerate(self._ai_names):
            row, plt, curve, hdr = self._make_analog_row(
                kind="AI",
                idx=i,
                name=nm,
                unit=(self._ai_units[i] if i < len(self._ai_units) else "")
            )
            top_v.addWidget(row)
            self.ai_rows.append(row)
            self.ai_plots.append(plt)
            self.ai_curves.append(curve)
            self.ai_headers.append(hdr)

        # ---------- AO section ----------
        ao_label = QtWidgets.QLabel("Analog Outputs")
        ao_label.setStyleSheet("font-weight:600; margin-top:8px;")
        top_v.addWidget(ao_label)

        # Top control bar for AO (one bar, select AO0/AO1)
        top_v.addWidget(self._make_top_ctrl("AO", min(2, len(self._ao_names)), self._ao_names,
                                            default_range=self._ao_default_range))

        self.ao_rows, self.ao_plots, self.ao_curves = [], [], []
        self.ao_locked = [True, True]  # default fixed
        self.ao_ranges = [self._ao_default_range, self._ao_default_range]
        self.ao_headers = []

        ao_count = min(2, len(self._ao_names))
        for i in range(ao_count):
            row, plt, curve, hdr = self._make_analog_row(
                kind="AO",
                idx=i,
                name=self._ao_names[i],
                unit=(self._ao_units[i] if i < len(self._ao_units) else ""),
                start_locked=True,
                start_range=self._ao_default_range
            )
            top_v.addWidget(row)
            self.ao_rows.append(row)
            self.ao_plots.append(plt)
            self.ao_curves.append(curve)
            self.ao_headers.append(hdr)

        # ========= Bottom section (DO with label) =========
        do_container = QtWidgets.QWidget()
        do_v = QtWidgets.QVBoxLayout(do_container)
        do_v.setContentsMargins(0, 0, 0, 0)
        do_v.setSpacing(4)

        do_label = QtWidgets.QLabel("Digital Outputs")
        do_label.setStyleSheet("font-weight:600; margin-top:2px;")
        do_v.addWidget(do_label)

        self.do_plot = pg.PlotWidget()
        self.do_plot.setMinimumHeight(220)  # keep usable when splitter is small
        dpi = self.do_plot.getPlotItem()
        # fixed scale + no mouse
        dpi.showAxis('bottom', show=True)  # DO keeps its X axis visible
        vb = dpi.getViewBox()
        vb.setMenuEnabled(False)
        vb.setMouseEnabled(x=False, y=False)
        dpi.enableAutoRange('x', False)
        dpi.enableAutoRange('y', False)
        do_v.addWidget(self.do_plot)

        self.do_amp = 0.85
        # lanes top→bottom (7..0), same spacing as your 1.3 window
        self.do_offsets = np.arange(8, dtype=float)[::-1]
        # fix y
        y_min = self.do_offsets[-1] - 0.75
        y_max = self.do_offsets[0] + 0.75
        dpi.setYRange(y_min, y_max, padding=0.0)

        self.do_curves = [self.do_plot.plot([], [], stepMode=True, pen=pg.mkPen(width=2))
                          for _ in range(8)]

        # ========= Splitter (drag to resize) =========
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(top_section)
        self.splitter.addWidget(do_container)
        # ~75% top, ~25% bottom as a starting point
        self.splitter.setSizes([700, 240])

        outer.addWidget(self.splitter)

        # Initial sync of control bars to current plot states
        self._ctrl_sync("AI")
        self._ctrl_sync("AO")

    # ---------- builders ----------
    def set_span(self, seconds: float):
        self.sp_span.blockSignals(True)
        self.sp_span.setValue(float(seconds))
        self.sp_span.blockSignals(False)

    def _make_analog_row(self, kind, idx, name, unit, start_locked=False, start_range=None):
        """Build one analog row (AI or AO) with header + plot."""
        row = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(row)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        # Header (we keep it constructed but DO NOT add it to the layout to save height)
        hdr = QtWidgets.QWidget()
        hl = QtWidgets.QHBoxLayout(hdr)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)

        lbl_chan = QtWidgets.QLabel(f"{kind}{idx}")
        lbl_chan.setStyleSheet("font-weight:600;")
        chk_auto = QtWidgets.QCheckBox("Auto")
        chk_auto.setChecked(not start_locked)

        def mkspin(val=None):
            sp = QtWidgets.QDoubleSpinBox()
            sp.setRange(-1e12, 1e12)
            sp.setDecimals(6)
            sp.setSingleStep(0.1)
            sp.setKeyboardTracking(True)
            sp.setAccelerated(False)
            sp.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.UpDownArrows)
            sp.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
            sp.lineEdit().setReadOnly(False)
            if val is not None:
                sp.setValue(float(val))
            return sp

        sp_min = mkspin((start_range[0] if (start_locked and start_range) else -10.0))
        sp_max = mkspin((start_range[1] if (start_locked and start_range) else 10.0))
        btn_apply = QtWidgets.QPushButton("Apply")
        btn_apply.setFixedWidth(70)

        # (header widgets wired, but not inserted to layout to save space)
        chk_auto.toggled.connect(lambda checked, k=kind, i=idx: self._on_auto_toggled(k, i, checked))
        btn_apply.clicked.connect(lambda _=False, k=kind, i=idx, smin=sp_min, smax=sp_max: self._on_apply(k, i, smin.value(), smax.value()))
        sp_min.editingFinished.connect(lambda k=kind, i=idx, smin=sp_min, smax=sp_max: self._apply_if_manual(k, i, smin.value(), smax.value()))
        sp_max.editingFinished.connect(lambda k=kind, i=idx, smin=sp_min, smax=sp_max: self._apply_if_manual(k, i, smin.value(), smax.value()))

        # Plot
        plt = pg.PlotWidget()
        pi = plt.getPlotItem()
        pi.showGrid(x=True, y=True, alpha=0.2)
        pi.hideAxis('bottom')
        unit_txt = f" [{unit}]" if unit else ""
        pi.setTitle(f"{name}{unit_txt}")
        pi.getViewBox().setMenuEnabled(False)

        curve = plt.plot([], [], pen=pg.mkPen(width=2), clickable=True)
        try:
            curve.setClipToView(True)
            curve.setDownsampling(auto=True, method='peak')
        except Exception:
            pass
        v.addWidget(plt)

        # initial lock state
        sp_min.setDisabled(chk_auto.isChecked())
        sp_max.setDisabled(chk_auto.isChecked())

        # IMPORTANT: at construction time, self.ai_plots / self.ao_plots are not populated yet,
        # so do NOT call _set_fixed_scale (it indexes those lists).
        if start_locked and start_range:
            ymin = float(start_range[0]); ymax = float(start_range[1])
            if kind == "AI":
                self.ai_locked[idx] = True
                self.ai_ranges[idx] = (ymin, ymax)
            else:  # AO
                self.ao_locked[idx] = True
                self.ao_ranges[idx] = (ymin, ymax)
            pi = plt.getPlotItem()
            pi.enableAutoRange(axis='y', enable=False)
            pi.setYRange(ymin, ymax, padding=0.0)

        return row, plt, curve, (chk_auto, sp_min, sp_max, btn_apply, lbl_chan)

    # ---------- header callbacks ----------

    def _apply_if_manual(self, kind, idx, mn, mx):
        chk = self._get_hdr(kind, idx)[0]
        if not chk.isChecked():
            self._on_apply(kind, idx, mn, mx)

    def _on_auto_toggled(self, kind, idx, checked):
        chk, sp_min, sp_max, *_ = self._get_hdr(kind, idx)
        if kind == "AI":
            self.ai_locked[idx] = not checked
        else:
            self.ao_locked[idx] = not checked
        sp_min.setDisabled(checked)
        sp_max.setDisabled(checked)
        if checked:
            self._autoscale(kind, idx)
        else:
            ymin, ymax = self._get_view(kind, idx)
            if not np.isfinite(ymin) or not np.isfinite(ymax):
                ymin, ymax = -10.0, 10.0
            self._set_fixed_scale(kind, idx, ymin, ymax)
            sp_min.setValue(float(ymin)); sp_max.setValue(float(ymax))

    def _on_apply(self, kind, idx, mn, mx):
        if mx < mn:
            mn, mx = mx, mn
        self._set_fixed_scale(kind, idx, float(mn), float(mx))

    # ---------- external API ----------

    def set_ai_names_units(self, names, units):
        self._ai_names = list(names)
        self._ai_units = list(units)
        for i, plt in enumerate(self.ai_plots):
            nm = self._ai_names[i] if i < len(self._ai_names) else f"AI{i}"
            unit = self._ai_units[i] if (i < len(self._ai_units) and self._ai_units[i]) else ""
            unit_txt = f" [{unit}]" if unit else ""
            plt.getPlotItem().setTitle(f"{nm}{unit_txt}")
        if hasattr(self, "_ai_ctrl"):
            sel = self._ai_ctrl["sel"]
            sel.blockSignals(True)
            sel.clear()
            for i in range(len(self._ai_names)):
                nm = self._ai_names[i] if i < len(self._ai_names) else f"AI{i}"
                sel.addItem(f"AI{i} - {nm}")
            sel.blockSignals(False)
            self._ctrl_sync("AI")

    def set_ao_names_units(self, names, units):
        self._ao_names = list(names)
        self._ao_units = list(units)
        for i, plt in enumerate(self.ao_plots):
            nm = self._ao_names[i] if i < len(self._ao_names) else f"AO{i}"
            unit = self._ao_units[i] if (i < len(self._ao_units) and self._ao_units[i]) else ""
            unit_txt = f" [{unit}]" if unit else ""
            plt.getPlotItem().setTitle(f"{nm}{unit_txt}")
        if hasattr(self, "_ao_ctrl"):
            sel = self._ao_ctrl["sel"]
            sel.blockSignals(True)
            sel.clear()
            for i in range(min(2, len(self._ao_names))):
                nm = self._ao_names[i] if i < len(self._ao_names) else f"AO{i}"
                sel.addItem(f"AO{i} - {nm}")
            sel.blockSignals(False)
            self._ctrl_sync("AO")

    def set_data(self, x, ai_ys, ao_ys, do_ys):
        # ---- AI ----
        x = np.asarray(x, dtype=float)
        n = x.shape[0]
        for i, y in enumerate(ai_ys):
            y_arr = np.asarray(y, dtype=float)
            if y_arr.shape[0] != n:
                if y_arr.shape[0] > n:
                    y_arr = y_arr[-n:]
                else:
                    y_arr = np.concatenate([np.full(n - y_arr.shape[0], np.nan, dtype=float), y_arr])
            self.ai_curves[i].setData(x, y_arr)

            # enforce fixed y-range if locked
            if self.ai_locked[i] and self.ai_ranges[i][0] is not None:
                pi = self.ai_plots[i].getPlotItem()
                pi.enableAutoRange(axis='y', enable=False)
                ymin, ymax = self.ai_ranges[i]
                pi.setYRange(float(ymin), float(ymax), padding=0.0)

        # ---- AO (two rows) ----
        ao_n = min(2, len(ao_ys))
        for i in range(ao_n):
            y = np.asarray(ao_ys[i], dtype=float)
            if y.shape[0] != n:
                if y.shape[0] > n:
                    y = y[-n:]
                else:
                    y = np.concatenate([np.full(n - y.shape[0], np.nan, dtype=float), y])
            self.ao_curves[i].setData(x, y)

            # enforce fixed y-range if locked
            if self.ao_locked[i] and self.ao_ranges[i][0] is not None:
                pi = self.ao_plots[i].getPlotItem()
                pi.enableAutoRange(axis='y', enable=False)
                ymin, ymax = self.ao_ranges[i]
                pi.setYRange(float(ymin), float(ymax), padding=0.0)

        # ---- DO (fixed-scale, stepMode) ----
        N = x.size
        if N == 0:
            return
        dx = (x[-1] - x[-2]) if N > 1 else 1e-3
        if not np.isfinite(dx) or dx <= 0:
            dx = (x[-1] - x[0]) / max(1, N - 1) if N > 1 else 1e-3
            if dx <= 0:
                dx = 1e-3
        x_edges = np.empty(N + 1, dtype=float)
        x_edges[:-1] = x
        x_edges[-1] = x[-1] + dx
        dpi = self.do_plot.getPlotItem()
        dpi.setXRange(x_edges[0], x_edges[-1], padding=0.0)

        for i in range(8):
            y = np.asarray(do_ys[i], dtype=float)
            if y.size != N:
                nn = min(N, y.size)
                y = y[-nn:]
                xe = x_edges[-(nn + 1):]
            else:
                xe = x_edges
            self.do_curves[i].setData(x=xe, y=y * self.do_amp + self.do_offsets[i])

    # ---------- helpers ----------

    def _get_hdr(self, kind, idx):
        return (self.ai_headers[idx] if kind == "AI" else self.ao_headers[idx])

    def _get_view(self, kind, idx):
        pi = (self.ai_plots[idx].getPlotItem() if kind == "AI" else self.ao_plots[idx].getPlotItem())
        return tuple(pi.getViewBox().viewRange()[1])

    def _autoscale(self, kind, idx):
        pi = (self.ai_plots[idx].getPlotItem() if kind == "AI" else self.ao_plots[idx].getPlotItem())
        if kind == "AI":
            self.ai_locked[idx] = False
        else:
            self.ao_locked[idx] = False
        pi.enableAutoRange(axis='y', enable=True)

    def _set_fixed_scale(self, kind, idx, ymin, ymax):
        pi = (self.ai_plots[idx].getPlotItem() if kind == "AI" else self.ao_plots[idx].getPlotItem())
        if kind == "AI":
            self.ai_locked[idx] = True
            self.ai_ranges[idx] = (float(ymin), float(ymax))
        else:
            self.ao_locked[idx] = True
            self.ao_ranges[idx] = (float(ymin), float(ymax))
        pi.enableAutoRange(axis='y', enable=False)
        pi.setYRange(float(ymin), float(ymax), padding=0.0)

    # ---------- top control bars ----------

    def _make_top_ctrl(self, section: str, count: int, names: list[str], default_range=None):
        """Build a compact control bar placed above a section (AI or AO)."""
        w = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)

        lbl = QtWidgets.QLabel(f"{section} scale:")
        sel = QtWidgets.QComboBox()
        items = [f"{section}{i} - {names[i] if i < len(names) else section + str(i)}" for i in range(count)]
        sel.addItems(items)

        chk = QtWidgets.QCheckBox("Auto")
        chk.setChecked(section == "AI")  # AIs default Auto; AOs default fixed (handled in __init__)

        sp_min = QtWidgets.QDoubleSpinBox(); sp_min.setRange(-1e12, 1e12); sp_min.setDecimals(6); sp_min.setSingleStep(0.1)
        sp_max = QtWidgets.QDoubleSpinBox(); sp_max.setRange(-1e12, 1e12); sp_max.setDecimals(6); sp_max.setSingleStep(0.1)
        btn = QtWidgets.QPushButton("Apply"); btn.setFixedWidth(70)

        # default AO range
        if section == "AO" and default_range:
            sp_min.setValue(float(default_range[0])); sp_max.setValue(float(default_range[1]))

        h.addWidget(lbl); h.addWidget(sel); h.addSpacing(8)
        h.addWidget(chk); h.addSpacing(8)
        h.addWidget(QtWidgets.QLabel("Y min:")); h.addWidget(sp_min)
        h.addWidget(QtWidgets.QLabel("Y max:")); h.addWidget(sp_max)
        h.addWidget(btn); h.addStretch(1)

        # wire
        if section == "AI":
            sel.currentIndexChanged.connect(lambda _=0: self._ctrl_sync("AI"))
            chk.toggled.connect(lambda checked: self._ctrl_auto_toggled("AI", checked))
            btn.clicked.connect(lambda: self._ctrl_apply("AI"))
            sp_min.editingFinished.connect(lambda: self._ctrl_apply("AI"))
            sp_max.editingFinished.connect(lambda: self._ctrl_apply("AI"))
            self._ai_ctrl = {"w": w, "sel": sel, "chk": chk, "mn": sp_min, "mx": sp_max, "btn": btn}
        else:
            sel.currentIndexChanged.connect(lambda _=0: self._ctrl_sync("AO"))
            chk.toggled.connect(lambda checked: self._ctrl_auto_toggled("AO", checked))
            btn.clicked.connect(lambda: self._ctrl_apply("AO"))
            sp_min.editingFinished.connect(lambda: self._ctrl_apply("AO"))
            sp_max.editingFinished.connect(lambda: self._ctrl_apply("AO"))
            self._ao_ctrl = {"w": w, "sel": sel, "chk": chk, "mn": sp_min, "mx": sp_max, "btn": btn}

        return w

    def _ctrl_sync(self, section: str):
        """Load control bar from current plot state for the selected channel."""
        if section == "AI":
            idx = self._ai_ctrl["sel"].currentIndex()
            locked = self.ai_locked[idx]
            self._ai_ctrl["chk"].blockSignals(True)
            self._ai_ctrl["chk"].setChecked(not locked)
            self._ai_ctrl["chk"].blockSignals(False)
            if locked and self.ai_ranges[idx][0] is not None:
                mn, mx = self.ai_ranges[idx]
            else:
                mn, mx = self._get_view("AI", idx)
            self._ai_ctrl["mn"].blockSignals(True); self._ai_ctrl["mx"].blockSignals(True)
            if mn is not None and mx is not None:
                self._ai_ctrl["mn"].setValue(float(mn)); self._ai_ctrl["mx"].setValue(float(mx))
            self._ai_ctrl["mn"].blockSignals(False); self._ai_ctrl["mx"].blockSignals(False)
        else:
            idx = self._ao_ctrl["sel"].currentIndex()
            locked = self.ao_locked[idx]
            self._ao_ctrl["chk"].blockSignals(True)
            self._ao_ctrl["chk"].setChecked(not locked)  # <- fixed (no '!locked', no duplicate)
            self._ao_ctrl["chk"].blockSignals(False)
            if locked and self.ao_ranges[idx][0] is not None:
                mn, mx = self.ao_ranges[idx]
            else:
                mn, mx = self._get_view("AO", idx)
            self._ao_ctrl["mn"].blockSignals(True); self._ao_ctrl["mx"].blockSignals(True)
            if mn is not None and mx is not None:
                self._ao_ctrl["mn"].setValue(float(mn)); self._ao_ctrl["mx"].setValue(float(mx))
            self._ao_ctrl["mn"].blockSignals(False); self._ao_ctrl["mx"].blockSignals(False)

    def _ctrl_auto_toggled(self, section: str, checked: bool):
        idx = (self._ai_ctrl["sel"].currentIndex() if section == "AI" else self._ao_ctrl["sel"].currentIndex())
        if checked:
            self._autoscale(section, idx)
        else:
            mn = (self._ai_ctrl["mn"].value() if section == "AI" else self._ao_ctrl["mn"].value())
            mx = (self._ai_ctrl["mx"].value() if section == "AI" else self._ao_ctrl["mx"].value())
            self._set_fixed_scale(section, idx, float(mn), float(mx))

    def _ctrl_apply(self, section: str):
        idx = (self._ai_ctrl["sel"].currentIndex() if section == "AI" else self._ao_ctrl["sel"].currentIndex())
        mn = (self._ai_ctrl["mn"].value() if section == "AI" else self._ao_ctrl["mn"].value())
        mx = (self._ai_ctrl["mx"].value() if section == "AI" else self._ao_ctrl["mx"].value())
        self._set_fixed_scale(section, idx, float(mn), float(mx))
