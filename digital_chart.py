from PyQt6 import QtWidgets
import pyqtgraph as pg
import numpy as np

class DigitalChartWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Digital Outputs")

        cw = QtWidgets.QWidget()
        self.setCentralWidget(cw)
        lay = QtWidgets.QVBoxLayout(cw)

        self.plot = pg.PlotWidget()
        pi = self.plot.getPlotItem()
        pi.showGrid(x=True, y=True, alpha=0.2)
        pi.setLabel('bottom', 'Time (s)')

        # FIXED SCALE + NO MOUSE INTERACTION (like v1.3)
        vb = pi.getViewBox()
        vb.setMenuEnabled(False)
        vb.setMouseEnabled(x=False, y=False)       # disable drag/zoom
        pi.enableAutoRange('x', False)
        pi.enableAutoRange('y', False)

        lay.addWidget(self.plot)

        # 8 step-mode traces (one plot)
        self.curves = [self.plot.plot([], [], stepMode=True, pen=pg.mkPen(width=2))
                       for _ in range(8)]

        # Vertical placement: top-to-bottom lanes, scaled to 85% band height so neighbors don’t touch
        self.amp = 0.85
        self.offsets = np.arange(8, dtype=float)[::-1]

        # Set a fixed Y range that shows all lanes comfortably
        y_min = self.offsets[-1] - 0.75
        y_max = self.offsets[0] + 0.75
        pi.setYRange(y_min, y_max, padding=0.0)

    def set_data(self, x, states_0_1_history):
        """
        x: array-like of time stamps (N)
        states_0_1_history: list of 8 arrays (each N) with values 0/1
        """
        x = np.asarray(x, dtype=float)
        N = x.size
        if N == 0:
            return

        # For stepMode=True, X must be len(Y)+1 → build an 'edge' vector
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

        # Lock X range to the current window (still not draggable)
        pi = self.plot.getPlotItem()
        pi.setXRange(x_edges[0], x_edges[-1], padding=0.0)

        for i in range(8):
            y = np.asarray(states_0_1_history[i], dtype=float)

            # align lengths defensively
            if y.size != N:
                n = min(y.size, N)
                y = y[-n:]
                xe = x_edges[-(n + 1):]
            else:
                xe = x_edges

            # Map 0/1 to a band centered on the lane’s offset
            self.curves[i].setData(x=xe, y=y * self.amp + self.offsets[i])
