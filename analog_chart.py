from PyQt6 import QtCore, QtWidgets
import pyqtgraph as pg
import numpy as np

class AnalogChartWindow(QtWidgets.QMainWindow):
    traceClicked = QtCore.pyqtSignal(int)
    requestScale = QtCore.pyqtSignal(int)

    def __init__(self, names, units):
        super().__init__()
        self.setWindowTitle("Analog Inputs")
        self._names = names
        self._units = units

        cw = QtWidgets.QWidget(); self.setCentralWidget(cw)
        self.layout = QtWidgets.QGridLayout(cw)

        self.plots = []
        self.curves = []
        self._y_locked = [False] * len(names)
        self._y_ranges = [(None, None)] * len(names)

        for i, nm in enumerate(names):
            plt = pg.PlotWidget(); plt.setMinimumHeight(80)
            pi = plt.getPlotItem(); pi.showGrid(x=True, y=True, alpha=0.2); pi.enableAutoRange(axis='y', enable=True)
            self.layout.addWidget(plt, i, 0)
            curve = plt.plot([], [], pen=pg.mkPen(width=2), name=nm, clickable=True)
            curve.sigClicked.connect(lambda c, idx=i: self._on_curve_clicked(idx))
            self.plots.append(plt); self.curves.append(curve)

    def _on_curve_clicked(self, idx):
        self.toggle_enlarge(idx); self.requestScale.emit(idx)

    def toggle_enlarge(self, idx):
        for r in range(len(self.plots)):
            self.layout.setRowStretch(r, 3 if r == idx and getattr(self, "_big", -1)!=idx else 1)
        self._big = None if getattr(self, "_big", -1)==idx else idx

    def set_data(self, x, ys):
        x = np.asarray(x, dtype=float)
        n = x.shape[0]
        for i, y in enumerate(ys):
            y_arr = np.asarray(y, dtype=float)
            if y_arr.shape[0] != n:
                # align lengths (pad on the left with NaNs or trim the head)
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

    def autoscale(self, idx):
        self._y_locked[idx] = False
        self.plots[idx].getPlotItem().enableAutoRange(axis='y', enable=True)

    def set_fixed_scale(self, idx, ymin, ymax):
        self._y_locked[idx] = True; self._y_ranges[idx] = (float(ymin), float(ymax))
        pi = self.plots[idx].getPlotItem(); pi.enableAutoRange(axis='y', enable=False); pi.setYRange(float(ymin), float(ymax), padding=0.0)

    def get_y_range(self, idx):
        return tuple(self.plots[idx].getViewBox().viewRange()[1])
