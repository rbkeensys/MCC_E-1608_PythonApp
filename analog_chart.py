from PyQt6 import QtCore, QtWidgets
import pyqtgraph as pg
import numpy as np

class AnalogChartWindow(QtWidgets.QMainWindow):
    # Emitted when user clicks a trace; MainWindow can still listen if needed
    traceClicked = QtCore.pyqtSignal(int)
    # Reuse existing hook from MainWindow if you want; we keep it here too
    requestScale = QtCore.pyqtSignal(int)

    def __init__(self, names, units):
        super().__init__()
        self.setWindowTitle("Analog Inputs")
        self._names = names
        self._units = units

        cw = QtWidgets.QWidget()
        self.setCentralWidget(cw)

        # --- Outer layout: control bar (top) + grid of plots ---
        outer = QtWidgets.QVBoxLayout(cw)

        # Control bar
        ctrl = QtWidgets.QWidget()
        cl = QtWidgets.QHBoxLayout(ctrl)
        cl.setContentsMargins(6, 6, 6, 6)

        self.cmb_chan = QtWidgets.QComboBox()
        self.cmb_chan.addItems([f"AI{i} — {names[i]}" for i in range(len(names))])
        self.chk_auto = QtWidgets.QCheckBox("Auto-scale Y")
        self.chk_auto.setChecked(True)

        def mkspin():
            sp = QtWidgets.QDoubleSpinBox()
            sp.setRange(-1e12, 1e12)
            sp.setDecimals(6)
            sp.setSingleStep(0.1)
            sp.setKeyboardTracking(True)
            sp.setAccelerated(False)
            sp.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.UpDownArrows)
            sp.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
            sp.lineEdit().setReadOnly(False)  # <- make sure typing works
            return sp

        self.sp_min = mkspin()
        self.sp_max = mkspin()
        self.btn_apply = QtWidgets.QPushButton("Apply")

        cl.addWidget(QtWidgets.QLabel("Channel:"))
        cl.addWidget(self.cmb_chan, 1)
        cl.addSpacing(8)
        cl.addWidget(self.chk_auto)
        cl.addSpacing(8)
        cl.addWidget(QtWidgets.QLabel("Y min:"))
        cl.addWidget(self.sp_min)
        cl.addWidget(QtWidgets.QLabel("Y max:"))
        cl.addWidget(self.sp_max)
        cl.addSpacing(8)
        cl.addWidget(self.btn_apply)
        outer.addWidget(ctrl)

        # Grid of plots
        self.grid = QtWidgets.QGridLayout()
        outer.addLayout(self.grid)

        self.plots = []
        self.curves = []
        self._y_locked = [False] * len(names)
        self._y_ranges = [(None, None)] * len(names)
        self._big = None

        for i, nm in enumerate(names):
            plt = pg.PlotWidget()
            plt.setMinimumHeight(80)
            pi = plt.getPlotItem()
            pi.showGrid(x=True, y=True, alpha=0.2)
            pi.enableAutoRange(axis='y', enable=True)

            # Optional: own context menu toggle (not required now)
            # plt.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.NoContextMenu)

            self.grid.addWidget(plt, i, 0)

            curve = plt.plot([], [], pen=pg.mkPen(width=2), name=nm, clickable=True)
            curve.sigClicked.connect(lambda c, idx=i: self._on_curve_clicked(idx))

            self.plots.append(plt)
            self.curves.append(curve)

        # Wire control bar
        self.chk_auto.toggled.connect(self._on_auto_toggled)
        self.btn_apply.clicked.connect(self._on_apply_clicked)
        self.cmb_chan.currentIndexChanged.connect(self._on_channel_changed)

        # Initialize controls with channel 0’s current view
        self._select_channel(0)
        self._on_auto_toggled(True)  # starts in Auto -> disable spinners

    # ---------- Control bar logic ----------
    def _select_channel(self, idx: int):
        """Populate the controls for channel idx."""
        self.cmb_chan.blockSignals(True)
        self.cmb_chan.setCurrentIndex(idx)
        self.cmb_chan.blockSignals(False)

        # Populate spinboxes with current Y range
        ymin, ymax = self.get_y_range(idx)
        # If nothing has been plotted yet, use a sensible default
        if not np.isfinite(ymin) or not np.isfinite(ymax):
            ymin, ymax = -10.0, 10.0
        self.sp_min.setValue(float(ymin))
        self.sp_max.setValue(float(ymax))

        # Reflect current lock state
        is_auto = not self._y_locked[idx]
        self.chk_auto.blockSignals(True)
        self.chk_auto.setChecked(is_auto)
        self.chk_auto.blockSignals(False)
        self._on_auto_toggled(is_auto)

    def _on_channel_changed(self, idx):
        self._select_channel(idx)

    def _on_auto_toggled(self, checked: bool):
        # Enable/disable editors
        self.sp_min.setDisabled(checked)
        self.sp_max.setDisabled(checked)

    def _on_apply_clicked(self):
        idx = self.cmb_chan.currentIndex()
        if self.chk_auto.isChecked():
            self.autoscale(idx)
        else:
            mn = float(self.sp_min.value())
            mx = float(self.sp_max.value())
            if mx < mn:
                mn, mx = mx, mn
            self.set_fixed_scale(idx, mn, mx)

    # ---------- Plot interactions ----------
    def _on_curve_clicked(self, idx):
        # Enlarge this row and select it in the control bar
        self.toggle_enlarge(idx)
        self._select_channel(idx)
        self.traceClicked.emit(idx)
        # If you still want the app's scale dialog, you can emit:
        # self.requestScale.emit(idx)

    def toggle_enlarge(self, idx):
        for r in range(len(self.plots)):
            self.grid.setRowStretch(r, 3 if r == idx and self._big != idx else 1)
        self._big = None if self._big == idx else idx

    # ---------- External API the MainWindow uses ----------
    def set_data(self, x, ys):
        x = np.asarray(x, dtype=float)
        n = x.shape[0]
        for i, y in enumerate(ys):
            y_arr = np.asarray(y, dtype=float)
            # harden against length mismatches
            if y_arr.shape[0] != n:
                if y_arr.shape[0] > n:
                    y_arr = y_arr[-n:]
                else:
                    pad = np.full(n - y_arr.shape[0], np.nan, dtype=float)
                    y_arr = np.concatenate([pad, y_arr])
            self.curves[i].setData(x, y_arr)

            # apply locked ranges per-trace
            if self._y_locked[i] and self._y_ranges[i][0] is not None:
                pi = self.plots[i].getPlotItem()
                pi.enableAutoRange(axis='y', enable=False)
                ymin, ymax = self._y_ranges[i]
                pi.setYRange(float(ymin), float(ymax), padding=0.0)

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
        # Returns (min, max) of current view
        return tuple(self.plots[idx].getViewBox().viewRange()[1])