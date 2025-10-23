from PyQt6 import QtCore, QtWidgets
import pyqtgraph as pg
import numpy as np


class DigitalChartWindow(QtWidgets.QMainWindow):
    spanChanged = QtCore.pyqtSignal(float)

    def __init__(self, names=None):
        super().__init__()
        self.setWindowTitle("Digital Outputs")

        self._names = list(names or [f"DO{i}" for i in range(8)])

        cw = QtWidgets.QWidget(); self.setCentralWidget(cw)
        lay = QtWidgets.QVBoxLayout(cw); lay.setContentsMargins(6, 6, 6, 6); lay.setSpacing(6)

        # X-span
        ctrl = QtWidgets.QWidget(); hl = QtWidgets.QHBoxLayout(ctrl)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(QtWidgets.QLabel("X span (s):"))
        self.sp_span = QtWidgets.QDoubleSpinBox()
        self.sp_span.setRange(0.01, 100.0); self.sp_span.setDecimals(3); self.sp_span.setSingleStep(0.01); self.sp_span.setValue(5.0)
        self.sp_span.valueChanged.connect(lambda v: self.spanChanged.emit(float(v)))
        hl.addWidget(self.sp_span); hl.addStretch(1)
        lay.addWidget(ctrl)

        # One plot with step traces
        self.plot = pg.PlotWidget()
        pi = self.plot.getPlotItem()
        pi.showGrid(x=True, y=True, alpha=0.2)
        pi.showAxis('bottom', show=True)
        vb = pi.getViewBox(); vb.setMenuEnabled(False); vb.setMouseEnabled(x=False, y=False)
        lay.addWidget(self.plot, 1)

        self.curves = []
        self.offsets = np.array([], dtype=float)
        self.amp = 0.85
        self._ensure_rows(len(self._names))

    # ---------- Public API ----------
    def set_span(self, seconds: float):
        self.sp_span.blockSignals(True)
        self.sp_span.setValue(float(seconds))
        self.sp_span.blockSignals(False)

    def set_names(self, names):
        self._names = list(names or [])
        self._ensure_rows(len(self._names))

    def set_data(self, x, ys):
        """
        stepMode=True requires len(X) == len(Y) + 1.
        We keep Y length 'n' and build X with length 'n+1'.
        """
        x = np.asarray(x, dtype=float)
        n = x.shape[0]
        k = len(ys)
        self._ensure_rows(k)
        self._show_rows(k)

        if n == 0:
            for c in self.curves:
                c.setData([], [])
            return

        # Build X of length n+1
        if n >= 2 and np.isfinite(x[-1]) and np.isfinite(x[-2]):
            dt = float(x[-1] - x[-2])
            if not np.isfinite(dt) or dt == 0.0:
                dt = 1.0
        else:
            dt = 1.0
        xx = np.concatenate([x, [x[-1] + dt]])  # n+1

        # Each DO: Y must be length n (no pad!)
        for i in range(k):
            yy = np.asarray(ys[i], dtype=float)
            if yy.shape[0] != n:
                if yy.shape[0] > n:
                    yy = yy[-n:]
                else:
                    yy = np.concatenate([np.full(n - yy.shape[0], np.nan), yy])

            on = (yy > 0.5).astype(float)  # length n
            on = self.offsets[i] + self.amp * on
            self.curves[i].setData(xx, on)  # X=n+1, Y=n

    # ---------- Internals ----------
    def _ensure_rows(self, count: int):
        cur = len(self.curves)
        if count <= cur:
            return
        for _ in range(count - cur):
            c = self.plot.plot([], [], stepMode=True, pen=pg.mkPen(width=2))
            self.curves.append(c)
        self.offsets = np.arange(len(self.curves), dtype=float)

    def _show_rows(self, k: int):
        for i, c in enumerate(self.curves):
            c.setVisible(i < k)
