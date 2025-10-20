# combined_chart.py
from PyQt6 import QtCore, QtWidgets
import pyqtgraph as pg
import numpy as np


class CombinedChartWindow(QtWidgets.QMainWindow):
    """
    Big window with:
      - AI rows (8) with per-row Auto / Ymin / Ymax / Apply
      - AO rows (2) with the same controls (default fixed 0..10)
      - DO block (1 plot with 8 step traces, fixed scale, no pan/zoom)
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

        # ---------- AI section ----------
        ai_label = QtWidgets.QLabel("Analog Inputs")
        ai_label.setStyleSheet("font-weight:600;")
        outer.addWidget(ai_label)

        self.ai_rows = []          # list[QWidget] for stretch
        self.ai_plots = []         # list[PlotWidget]
        self.ai_curves = []        # list[PlotDataItem]
        self.ai_locked = [False] * len(self._ai_names)
        self.ai_ranges = [(None, None)] * len(self._ai_names)
        self.ai_headers = []       # list of tuples (chk_auto, sp_min, sp_max, btn_apply, lbl_chan)

        for i, nm in enumerate(self._ai_names):
            row, plt, curve, hdr = self._make_analog_row(kind="AI", idx=i, name=nm,
                                                         unit=(self._ai_units[i] if i < len(self._ai_units) else ""))
            outer.addWidget(row)
            self.ai_rows.append(row)
            self.ai_plots.append(plt)
            self.ai_curves.append(curve)
            self.ai_headers.append(hdr)

        # ---------- AO section ----------
        ao_label = QtWidgets.QLabel("Analog Outputs")
        ao_label.setStyleSheet("font-weight:600; margin-top:8px;")
        outer.addWidget(ao_label)

        self.ao_rows, self.ao_plots, self.ao_curves = [], [], []
        self.ao_locked = [True, True]  # default fixed
        self.ao_ranges = [self._ao_default_range, self._ao_default_range]
        self.ao_headers = []

        ao_count = min(2, len(self._ao_names))
        for i in range(ao_count):
            row, plt, curve, hdr = self._make_analog_row(kind="AO", idx=i, name=self._ao_names[i],
                                                         unit=(self._ao_units[i] if i < len(self._ao_units) else ""),
                                                         start_locked=True, start_range=self._ao_default_range)
            outer.addWidget(row)
            self.ao_rows.append(row)
            self.ao_plots.append(plt)
            self.ao_curves.append(curve)
            self.ao_headers.append(hdr)

        # ---------- DO section ----------
        do_label = QtWidgets.QLabel("Digital Outputs")
        do_label.setStyleSheet("font-weight:600; margin-top:8px;")
        outer.addWidget(do_label)

        self.do_plot = pg.PlotWidget()
        dpi = self.do_plot.getPlotItem()
        dpi.showGrid(x=True, y=True, alpha=0.2)
        # fixed scale + no mouse
        vb = dpi.getViewBox()
        vb.setMenuEnabled(False)
        vb.setMouseEnabled(x=False, y=False)
        dpi.enableAutoRange('x', False)
        dpi.enableAutoRange('y', False)

        outer.addWidget(self.do_plot)

        self.do_amp = 0.85
        # lanes top→bottom (7..0), same spacing as your 1.3 window
        self.do_offsets = np.arange(8, dtype=float)[::-1]
        # fix y
        y_min = self.do_offsets[-1] - 0.75
        y_max = self.do_offsets[0] + 0.75
        dpi.setYRange(y_min, y_max, padding=0.0)

        self.do_curves = [self.do_plot.plot([], [], stepMode=True, pen=pg.mkPen(width=2))
                          for _ in range(8)]

    # ---------- builders ----------

    def _make_analog_row(self, kind, idx, name, unit, start_locked=False, start_range=None):
        """Build one analog row (AI or AO) with header + plot."""
        row = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(row)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        # Header
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

        hl.addWidget(lbl_chan)
        hl.addSpacing(8)
        hl.addWidget(chk_auto)
        hl.addSpacing(8)
        hl.addWidget(QtWidgets.QLabel("Y min:"))
        hl.addWidget(sp_min)
        hl.addWidget(QtWidgets.QLabel("Y max:"))
        hl.addWidget(sp_max)
        hl.addWidget(btn_apply)
        hl.addStretch(1)
        v.addWidget(hdr)

        # Plot
        plt = pg.PlotWidget()
        pi = plt.getPlotItem()
        pi.showGrid(x=True, y=True, alpha=0.2)
        # title = configured name + units
        unit_txt = f" [{unit}]" if unit else ""
        pi.setTitle(f"{name}{unit_txt}")
        # disable pg menu
        pi.getViewBox().setMenuEnabled(False)

        curve = plt.plot([], [], pen=pg.mkPen(width=2), clickable=True)
        try:
            curve.setClipToView(True)
            curve.setDownsampling(auto=True, method='peak')
        except Exception:
            pass
        v.addWidget(plt)

        # wire header
        chk_auto.toggled.connect(lambda checked, k=kind, i=idx: self._on_auto_toggled(k, i, checked))
        btn_apply.clicked.connect(lambda _=False, k=kind, i=idx, smin=sp_min, smax=sp_max: self._on_apply(k, i, smin.value(), smax.value()))
        sp_min.editingFinished.connect(lambda k=kind, i=idx, smin=sp_min, smax=sp_max: self._apply_if_manual(k, i, smin.value(), smax.value()))
        sp_max.editingFinished.connect(lambda k=kind, i=idx, smin=sp_min, smax=sp_max: self._apply_if_manual(k, i, smin.value(), smax.value()))

        # initial lock state
        sp_min.setDisabled(chk_auto.isChecked())
        sp_max.setDisabled(chk_auto.isChecked())

        # IMPORTANT: at construction time, self.ai_plots / self.ao_plots are not populated yet,
        # so do NOT call _set_fixed_scale (it indexes those lists).
        if start_locked and start_range:
            ymin = float(start_range[0]);
            ymax = float(start_range[1])
            # mark locked + remember ranges
            if kind == "AI":
                self.ai_locked[idx] = True
                self.ai_ranges[idx] = (ymin, ymax)
            else:  # AO
                self.ao_locked[idx] = True
                self.ao_ranges[idx] = (ymin, ymax)
            # apply directly to the local plot item we just created
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

    def set_ao_names_units(self, names, units):
        self._ao_names = list(names)
        self._ao_units = list(units)
        for i, plt in enumerate(self.ao_plots):
            nm = self._ao_names[i] if i < len(self._ao_names) else f"AO{i}"
            unit = self._ao_units[i] if (i < len(self._ao_units) and self._ao_units[i]) else ""
            unit_txt = f" [{unit}]" if unit else ""
            plt.getPlotItem().setTitle(f"{nm}{unit_txt}")

    def set_data(self, x, ai_ys, ao_ys, do_ys):
        # AI
        x = np.asarray(x, dtype=float)
        n = x.shape[0]
        for i, y in enumerate(ai_ys):
            y_arr = np.asarray(y, dtype=float)
            if y_arr.shape[0] != n:
                if y_arr.shape[0] > n:
                    y_arr = y_arr[-n:]
                else:
                    pad = np.full(n - y_arr.shape[0], np.nan, dtype=float)
                    y_arr = np.concatenate([pad, y_arr])
            self.ai_curves[i].setData(x, y_arr)

            if self.ai_locked[i] and self.ai_ranges[i][0] is not None:
                pi = self.ai_plots[i].getPlotItem()
                pi.enableAutoRange(axis='y', enable=False)
                ymin, ymax = self.ai_ranges[i]
                pi.setYRange(float(ymin), float(ymax), padding=0.0)

            # keep header spinners if manual and user not typing
            chk, sp_min, sp_max, *_ = self.ai_headers[i]
            if (not chk.isChecked()) and (not sp_min.hasFocus()) and (not sp_max.hasFocus()):
                ymin, ymax = self._get_view("AI", i)
                if np.isfinite(ymin) and np.isfinite(ymax):
                    sp_min.blockSignals(True); sp_max.blockSignals(True)
                    sp_min.setValue(float(ymin)); sp_max.setValue(float(ymax))
                    sp_min.blockSignals(False); sp_max.blockSignals(False)

        # AO (two rows)
        ao_count = min(2, len(ao_ys))
        for i in range(ao_count):
            y = np.asarray(ao_ys[i], dtype=float)
            if y.shape[0] != n:
                if y.shape[0] > n:
                    y = y[-n:]
                else:
                    y = np.concatenate([np.full(n - y.shape[0], np.nan, dtype=float), y])
            self.ao_curves[i].setData(x, y)

            if self.ao_locked[i] and self.ao_ranges[i][0] is not None:
                pi = self.ao_plots[i].getPlotItem()
                pi.enableAutoRange(axis='y', enable=False)
                ymin, ymax = self.ao_ranges[i]
                pi.setYRange(float(ymin), float(ymax), padding=0.0)

            chk, sp_min, sp_max, *_ = self.ao_headers[i]
            if (not chk.isChecked()) and (not sp_min.hasFocus()) and (not sp_max.hasFocus()):
                ymin, ymax = self._get_view("AO", i)
                if np.isfinite(ymin) and np.isfinite(ymax):
                    sp_min.blockSignals(True); sp_max.blockSignals(True)
                    sp_min.setValue(float(ymin)); sp_max.setValue(float(ymax))
                    sp_min.blockSignals(False); sp_max.blockSignals(False)

        # DO (fixed)
        N = x.size
        if N == 0:
            return
        dx = (x[-1] - x[-2]) if N > 1 else 1e-3
        if not np.isfinite(dx) or dx <= 0:
            dx = (x[-1] - x[0]) / max(1, N - 1) if N > 1 else 1e-3
            if dx <= 0: dx = 1e-3
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
