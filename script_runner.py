
from PyQt6 import QtCore
import json, time

class ScriptRunner(QtCore.QObject):
    tick = QtCore.pyqtSignal(float, list)
    finished = QtCore.pyqtSignal()

    def __init__(self, set_do_callable):
        super().__init__()
        self._events = []
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._t0 = None
        self._paused = True
        self._pause_t = 0.0
        self._cursor = 0
        self._set_do = set_do_callable
        self._period_ms = 10

    def load_script(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            self._events = json.load(f)
        self.reset()

    def get_events(self):
        return list(self._events)

    def set_events(self, events):
        self._events = list(events or [])
        self.reset()

    def reset(self):
        self._t0 = None; self._pause_t = 0.0; self._paused = True; self._cursor = 0; self._timer.stop()

    def run(self):
        if not self._events: return
        if self._t0 is None: self._t0 = time.perf_counter()
        else:
            self._t0 = time.perf_counter() - self._pause_t; self._pause_t = 0.0
        self._paused = False; self._timer.start(self._period_ms)

    def stop(self):
        if self._paused: return
        self._paused = True; self._timer.stop()
        self._pause_t = time.perf_counter() - (self._t0 or time.perf_counter())

    def _on_tick(self):
        if self._paused or self._t0 is None: return
        t = time.perf_counter() - self._t0
        last_relays = None
        while self._cursor < len(self._events) and t >= float(self._events[self._cursor]["time"]):
            rel = self._events[self._cursor].get("relays", [False]*8)
            for i, st in enumerate(rel[:8]): self._set_do(i, bool(st))
            last_relays = rel; self._cursor += 1
        if last_relays is None and self._cursor > 0: last_relays = self._events[self._cursor-1].get("relays", [False]*8)
        if last_relays is None: last_relays = [False]*8
        self.tick.emit(t, last_relays)
        if self._cursor >= len(self._events): self._timer.stop(); self.finished.emit()
