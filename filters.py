import math
import numpy as np

class OnePoleLPF:
    def __init__(self, cutoff_hz: float, fs_hz: float):
        self.cutoff_hz = max(0.0, float(cutoff_hz))
        self.fs_hz = max(1e-6, float(fs_hz))
        self.y = None  # filter state
        self._update_alpha()

    def _update_alpha(self):
        self.alpha = 1.0 - math.exp(-2.0 * math.pi * self.cutoff_hz / self.fs_hz)

    def set_fs(self, fs_hz: float):
        self.fs_hz = max(1e-6, float(fs_hz))
        self._update_alpha()

    def set_cutoff(self, cutoff_hz: float):
        self.cutoff_hz = max(0.0, float(cutoff_hz))
        self._update_alpha()

    def reset(self):
        self.y = None

    def process(self, x: float) -> float:
        """Single-sample filter (kept for compatibility)."""
        if self.cutoff_hz <= 0.0:
            return x
        if self.y is None:
            self.y = float(x)
            return self.y
        a = float(self.alpha)
        self.y = self.y + a * (float(x) - self.y)
        return self.y

    def process_chunk(self, x_arr: np.ndarray) -> np.ndarray:
        """Vectorized filter over a 1-D numpy array, preserving state across calls."""
        x = np.asarray(x_arr, dtype=float)
        if self.cutoff_hz <= 0.0 or x.size == 0:
            return x
        y = np.empty_like(x, dtype=float)
        a = float(self.alpha)
        yy = self.y
        if yy is None:
            # first sample passes through
            yy = float(x[0])
            y[0] = yy
            start = 1
        else:
            # filter the first sample, then continue
            yy = yy + a * (float(x[0]) - yy)
            y[0] = yy
            start = 1
        for i in range(start, x.size):
            xx = float(x[i])
            yy = yy + a * (xx - yy)
            y[i] = yy
        self.y = yy
        return y
