
import math
class OnePoleLPF:
    def __init__(self, cutoff_hz: float, fs_hz: float):
        self.cutoff_hz = max(0.0, float(cutoff_hz))
        self.fs_hz = max(1e-6, float(fs_hz))
        self.y = None
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
        if self.cutoff_hz <= 0.0: return x
        if self.y is None:
            self.y = x; return x
        self.y = self.y + self.alpha * (x - self.y)
        return self.y
