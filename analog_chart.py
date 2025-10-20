from PyQt6 import QtCore, QtWidgets
import pyqtgraph as pg
import numpy as np

class AnalogChartWindow(QtWidgets.QMainWindow):
    traceClicked = QtCore.pyqtSignal(int)
    requestScale = QtCore.pyqtSignal(int)
    spanChanged = QtCore.pyqtSignal(float)   # <— NEW

    def __init__(self, names, units):
        super().__init__()
        self.setWindowTitle("Analog Inputs")
        self._names = names
        self._units = units

        cw = QtWidgets.QWidget()
        self.setCentralWidget(cw)
        outer = QtWidgets.QVBoxLayout(cw)
        outer.setContentsMargins(6,6,6,6)
        outer.setSpacing(6)

        # --- NEW: X-span control bar ---
        ctrl = QtWidgets.QWidget()
        hl = QtWidgets.QHBoxLayout(ctrl)
        hl.setContentsMargins(0,0,0,0); hl.setSpacing(8)
        hl.addWidget(QtWidgets.QLabel("X span (s):"))
        self.sp_span = QtWidgets.QDoubleSpinBox()
        self.sp_span.setRange(0.01, 100.0)      # request: up to 100 s (tweak as you like)
        self.sp_span.setDecimals(3)
        self.sp_span.setSingleStep(0.01)
        self.sp_span.setValue(5.0)              # default; MainWindow will overwrite on init
        self.sp_span.valueChanged.connect(lambda v: self.spanChanged.emit(float(v)))
        hl.addWidget(self.sp_span)
        hl.addStretch(1)
        outer.addWidget(ctrl)

        self.plots = []
        self.curves = []
        self._y_locked = [False] * len(names)
        self._y_ranges = [(None, None)] * len(names)
        self._big = None
        self._headers = []  # (chk_auto, sp_min, sp_max, btn_apply, lbl_chan)

        for i, nm in enumerate(names):
            row = QtWidgets.QWidget()
            row_layout = QtWidgets.QVBoxLayout(row)
            row_layout.setContentsMargins(0,0,0,0)
            row_layout.setSpacing(4)

            # Header strip
            hdr = QtWidgets.QWidget()
            hl = QtWidgets.QHBoxLayout(hdr)
            hl.setContentsMargins(0,0,0,0)
            hl.setSpacing(6)

            # LEFT: channel number only (no duplicate name)
            lbl_chan = QtWidgets.QLabel(f"AI{i}")
            lbl_chan.setStyleSheet("font-weight:600;")

            chk_auto = QtWidgets.QCheckBox("Auto")
            chk_auto.setChecked(True)

            def mkspin():
                sp = QtWidgets.QDoubleSpinBox()
                sp.setRange(-1e12, 1e12)
                sp.setDecimals(6)
                sp.setSingleStep(0.1)
                sp.setKeyboardTracking(True)
                sp.setAccelerated(False)
                sp.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.UpDownArrows)
                sp.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
                sp.lineEdit().setReadOnly(False)
                return sp

            sp_min = mkspin()
            sp_max = mkspin()
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

            row_layout.addWidget(hdr)

            # Plot
            plt = pg.PlotWidget()
            pi = plt.getPlotItem()
            pi.showGrid(x=True, y=True, alpha=0.2)
            pi.enableAutoRange(axis='y', enable=True)

            unit = f" [{self._units[i]}]" if (i < len(self._units) and self._units[i]) else ""
            # CENTER TITLE: config trace name only
            pi.setTitle(f"{nm}{unit}")

            # Disable built-in context menu
            pi.getViewBox().setMenuEnabled(False)

            curve = plt.plot([], [], pen=pg.mkPen(width=2), name=nm, clickable=True)
            try:
                curve.setClipToView(True)
                curve.setDownsampling(auto=True, method='peak')
            except Exception:
                pass
            curve.sigClicked.connect(lambda c, idx=i: self._on_curve_clicked(idx))

            row_layout.addWidget(plt)
            outer.addWidget(row)

            # wire header actions
            chk_auto.toggled.connect(lambda checked, idx=i: self._on_auto_toggled(idx, checked))
            btn_apply.clicked.connect(lambda _=False, idx=i, smin=sp_min, smax=sp_max: self._on_apply(idx, smin.value(), smax.value()))

            ymin, ymax = self._view_range_of(pi)
            if not np.isfinite(ymin) or not np.isfinite(ymax):
                ymin, ymax = -10.0, 10.0
            sp_min.setValue(float(ymin))
            sp_max.setValue(float(ymax))
            sp_min.setDisabled(True)
            sp_max.setDisabled(True)

            self.plots.append(plt)
            self.curves.append(curve)
            self._headers.append((chk_auto, sp_min, sp_max, btn_apply, lbl_chan))


    # --- header helpers ---
    def set_span(self, seconds: float):
        self.sp_span.blockSignals(True)
        self.sp_span.setValue(float(seconds))
        self.sp_span.blockSignals(False)

    def _on_auto_toggled(self, idx: int, checked: bool):
        self._y_locked[idx] = not checked
        sp_min = self._headers[idx][1]
        sp_max = self._headers[idx][2]
        sp_min.setDisabled(checked)
        sp_max.setDisabled(checked)
        if checked:
            self.autoscale(idx)
        else:
            ymin, ymax = self.get_y_range(idx)
            if not np.isfinite(ymin) or not np.isfinite(ymax):
                ymin, ymax = -10.0, 10.0
            self.set_fixed_scale(idx, ymin, ymax)
            sp_min.setValue(float(ymin))
            sp_max.setValue(float(ymax))

    def _on_apply(self, idx: int, mn: float, mx: float):
        if self._headers[idx][0].isChecked():
            return
        if mx < mn:
            mn, mx = mx, mn
        self.set_fixed_scale(idx, mn, mx)

    # --- plot interactions ---
    def _on_curve_clicked(self, idx):
        self.toggle_enlarge(idx)
        self.traceClicked.emit(idx)

    def toggle_enlarge(self, idx):
        lay = self.centralWidget().layout()
        for r in range(len(self.plots)):
            lay.setStretch(r, 3 if (r == idx and self._big != idx) else 1)
        self._big = None if self._big == idx else idx

    def set_names_units(self, names, units):
        """Update per-plot titles from the current config."""
        self._names = list(names)
        self._units = list(units)
        for i, plt in enumerate(self.plots):
            nm = self._names[i] if i < len(self._names) else f"AI{i}"
            unit = self._units[i] if (i < len(self._units) and self._units[i]) else ""
            unit_txt = f" [{unit}]" if unit else ""
            plt.getPlotItem().setTitle(f"{nm}{unit_txt}")

    def set_data(self, x, ys):
        x = np.asarray(x, dtype=float)
        n = x.shape[0]
        for i, y in enumerate(ys):
            y_arr = np.asarray(y, dtype=float)
            if y_arr.shape[0] != n:
                if y_arr.shape[0] > n:
                    y_arr = y_arr[-n:]
                else:
                    pad = np.full(n - y_arr.shape[0], np.nan, dtype=float)
                    y_arr = np.concatenate([pad, y_arr])
            self.curves[i].setData(x, y_arr)

            if self._y_locked[i] and self._y_ranges[i][0] is not None:
                pi = self.plots[i].getPlotItem()
                pi.enableAutoRange(axis='y', enable=False)
                ymin, ymax = self._y_ranges[i]
                pi.setYRange(float(ymin), float(ymax), padding=0.0)

            # keep header boxes in sync if manual
            chk_auto, sp_min, sp_max, _, _ = self._headers[i]
            if (not chk_auto.isChecked()) and (not sp_min.hasFocus()) and (not sp_max.hasFocus()):
                ymin, ymax = self.get_y_range(i)
                if np.isfinite(ymin) and np.isfinite(ymax):
                    sp_min.blockSignals(True); sp_max.blockSignals(True)
                    sp_min.setValue(float(ymin)); sp_max.setValue(float(ymax))
                    sp_min.blockSignals(False); sp_max.blockSignals(False)

            # after creating sp_min, sp_max (and still inside the loop for channel i)
            sp_min.editingFinished.connect(lambda idx=i, smin=sp_min, smax=sp_max: self._apply_if_manual(idx, smin, smax))
            sp_max.editingFinished.connect(lambda idx=i, smin=sp_min, smax=sp_max: self._apply_if_manual(idx, smin, smax))

    def _apply_if_manual(self, idx, smin, smax):
        # Only apply if Auto is OFF for this channel
        chk_auto = self._headers[idx][0]
        if not chk_auto.isChecked():
            self._on_apply(idx, float(smin.value()), float(smax.value()))

    def autoscale(self, idx):
        self._y_locked[idx] = False
        pi = self.plots[idx].getPlotItem()
        pi.enableAutoRange(axis='y', enable=True)

    def set_fixed_scale(self, idx, ymin, ymax):
        self._y_locked[idx] = True
        self._y_ranges[idx] = (float(ymin), float(ymax))
        pi = self.plots[idx].getPlotItem()
        pi.enableAutoRange(axis='y', enable=False)
        pi.setYRange(float(ymin), float(ymax), padding=0.0)

    def get_y_range(self, idx):
        return tuple(self.plots[idx].getViewBox().viewRange()[1])

    @staticmethod
    def _view_range_of(plot_item):
        return tuple(plot_item.getViewBox().viewRange()[1])
