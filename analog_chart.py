from PyQt6 import QtCore, QtWidgets
import pyqtgraph as pg
import numpy as np


class AnalogChartWindow(QtWidgets.QMainWindow):
    traceClicked = QtCore.pyqtSignal(int)
    requestScale = QtCore.pyqtSignal(int)
    spanChanged = QtCore.pyqtSignal(float)

    def __init__(self, names, units):
        super().__init__()
        self.setWindowTitle("Analog Inputs")

        self._names = list(names or [])
        self._units = list(units or [])

        cw = QtWidgets.QWidget(); self.setCentralWidget(cw)
        lay = QtWidgets.QVBoxLayout(cw); lay.setContentsMargins(6,6,6,6); lay.setSpacing(6)

        # X-span
        ctrl = QtWidgets.QWidget(); hl = QtWidgets.QHBoxLayout(ctrl)
        hl.setContentsMargins(0,0,0,0)
        hl.addWidget(QtWidgets.QLabel("X span (s):"))
        self.sp_span = QtWidgets.QDoubleSpinBox()
        self.sp_span.setRange(0.01, 100.0); self.sp_span.setDecimals(3); self.sp_span.setSingleStep(0.01); self.sp_span.setValue(5.0)
        self.sp_span.valueChanged.connect(lambda v: self.spanChanged.emit(float(v)))
        hl.addWidget(self.sp_span); hl.addStretch(1)
        lay.addWidget(ctrl)

        self.rows = []
        self.plots = []
        self.curves = []
        self._y_locked = []
        self._y_ranges = []

        self._ensure_rows(len(self._names))
        for i in range(len(self._names)):
            row = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(row); v.setContentsMargins(0,0,0,0)

            plt = pg.PlotWidget()
            pi = plt.getPlotItem()
            pi.showGrid(x=True, y=True, alpha=0.2)
            pi.hideAxis('bottom')
            pi.getViewBox().setMenuEnabled(False)

            unit_txt = f" [{self._units[i]}]" if i < len(self._units) and self._units[i] else ""
            pi.setTitle(f"{self._names[i]}{unit_txt}")

            curve = plt.plot([], [], pen=pg.mkPen(width=2))
            v.addWidget(plt)
            lay.addWidget(row)

            self.rows[i] = row
            self.plots[i] = plt
            self.curves[i] = curve
            self._y_locked[i] = False
            self._y_ranges[i] = (None, None)

    # ---- Public API ----
    def set_span(self, seconds: float):
        self.sp_span.blockSignals(True)
        self.sp_span.setValue(float(seconds))
        self.sp_span.blockSignals(False)

    def set_names_units(self, names, units):
        self._names = list(names or [])
        self._units = list(units or [])
        self._ensure_rows(len(self._names))
        for i in range(len(self._names)):
            unit_txt = f" [{self._units[i]}]" if i < len(self._units) and self._units[i] else ""
            self.plots[i].getPlotItem().setTitle(f"{self._names[i]}{unit_txt}")
        self._show_rows(len(self._names))

    def set_data(self, x, ys):
        x = np.asarray(x, dtype=float)
        n = x.shape[0]
        k = len(ys)
        self._ensure_rows(k)
        self._show_rows(k)

        for i in range(k):
            yy = np.asarray(ys[i], dtype=float)
            if yy.shape[0] != n:
                if yy.shape[0] > n:
                    yy = yy[-n:]
                else:
                    yy = np.concatenate([np.full(n - yy.shape[0], np.nan), yy])
            self.curves[i].setData(x, yy)

            if self._y_locked[i] and self._y_ranges[i][0] is not None:
                pi = self.plots[i].getPlotItem()
                pi.enableAutoRange(axis='y', enable=False)
                ymin, ymax = self._y_ranges[i]
                pi.setYRange(float(ymin), float(ymax), padding=0.0)

    # ---- internals ----
    def _ensure_rows(self, count: int):
        cur = len(self.plots)
        if count <= cur:
            return
        add = count - cur
        self.rows.extend([None] * add)
        self.plots.extend([None] * add)
        self.curves.extend([None] * add)
        self._y_locked.extend([False] * add)
        self._y_ranges.extend([(None, None)] * add)

    def _show_rows(self, k: int):
        for i, row in enumerate(self.rows):
            if row is not None:
                row.setVisible(i < k)
