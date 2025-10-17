from PyQt6 import QtWidgets
import pyqtgraph as pg
import numpy as np

class DigitalChartWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Digital Outputs")
        cw = QtWidgets.QWidget()
        self.setCentralWidget(cw)
        self.layout = QtWidgets.QVBoxLayout(cw)

        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setLabel('left', 'DO7..DO0')
        self.plot.setLabel('bottom', 'Time (s)')
        self.layout.addWidget(self.plot)

        # One step-style curve per DO line
        self.curves = [self.plot.plot([], [], stepMode=True, pen=pg.mkPen(width=2)) for _ in range(8)]
        # Offset each DO so they don't overlap (7 at top ... 0 at bottom)
        self.offsets = np.arange(8)[::-1].astype(float)

    # digital_chart.py  (only the set_data body shown)
    def set_data(self, x, states_0_1_history):
        if not x:
            return

        x = np.asarray(x, dtype=float)
        N = x.size

        # Build x_edges with N+1 points for stepMode=True
        if N == 1:
            dx = 1e-3
        else:
            dx = x[-1] - x[-2]
            if not np.isfinite(dx) or dx <= 0:
                dx = (x[-1] - x[0]) / max(1, N - 1) if N > 1 else 1e-3
                if dx <= 0:
                    dx = 1e-3

        x_edges = np.empty(N + 1, dtype=float)
        x_edges[:-1] = x
        x_edges[-1] = x[-1] + dx

        amp = 0.85  # <-- scale to 85% height to avoid overlap
        for i in range(8):
            y = np.asarray(states_0_1_history[i], dtype=float)
            if y.size != N:
                n = min(y.size, N)
                y = y[-n:]
                xe = x_edges[-(n + 1):]
            else:
                xe = x_edges
            self.curves[i].setData(x=xe, y=y * amp + self.offsets[i])