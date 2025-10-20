# pid.py
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple, Dict, Any
import json
import numpy as np

@dataclass
class PIDLoopDef:
    enabled: bool
    kind: str              # "digital" or "analog"
    ai_ch: int
    out_ch: int            # DO ch for digital, AO ch (0/1) for analog
    target: float
    kp: float
    ki: float
    kd: float
    # Optional extras
    out_min: Optional[float] = None   # analog only, clamp low (e.g. -10)
    out_max: Optional[float] = None   # analog only, clamp high (e.g. +10)

class _PIDCore:
    def __init__(self, kp:float, ki:float, kd:float, setpoint:float):
        self.kp = float(kp); self.ki = float(ki); self.kd = float(kd)
        self.setpoint = float(setpoint)
        self._i = 0.0
        self._prev_err = None

    def reset(self):
        self._i = 0.0
        self._prev_err = None

    def step(self, pv: float, dt: float) -> Tuple[float, float]:
        """Return (u, err) one PID step."""
        err = self.setpoint - float(pv)
        # P
        p = self.kp * err
        # I (rectangle)
        self._i += self.ki * err * dt
        # D on error
        d = 0.0
        if self._prev_err is not None and dt > 0:
            d = self.kd * (err - self._prev_err) / dt
        self._prev_err = err
        u = p + self._i + d
        return u, err

class DigitalPID:
    """PID that decides a boolean 'actuate' command; mapping to DO honors normallyOpen."""
    def __init__(self, loop: PIDLoopDef, normally_open: bool):
        assert loop.kind == "digital"
        self.defn = loop
        self.no = bool(normally_open)
        self.core = _PIDCore(loop.kp, loop.ki, loop.kd, loop.target)
        self.last_ai = 0.0
        self.last_err = 0.0
        self.last_actuate = False     # logical 'actuate' (close contact)
        self.last_do_bit = False      # actual DO bit after NO/NC mapping

    def reset(self):
        self.core.reset()

    def process_block(self, ai_block: np.ndarray, dt: float):
        """ai_block is 1-D array of samples for this loop's AI channel."""
        if ai_block.size == 0:
            return
        # Run per-sample; take the last as the command for this block.
        for v in ai_block:
            u, e = self.core.step(v, dt)
            # Threshold at 0: u>=0 => actuate; u<0 => release
            self.last_actuate = (u >= 0.0)
            self.last_ai = float(v)
            self.last_err = float(e)
        # Map logical 'actuate' to actual DO bit honoring NO/NC
        self.last_do_bit = self.last_actuate if self.no else (not self.last_actuate)

class AnalogPID:
    """PID that outputs a clamped analog voltage for AO 0/1."""
    def __init__(self, loop: PIDLoopDef, out_limits: Tuple[float, float]):
        assert loop.kind == "analog"
        lo, hi = out_limits
        if lo is None: lo = -10.0
        if hi is None: hi = 10.0
        self.defn = loop
        self.lo = float(lo); self.hi = float(hi)
        self.core = _PIDCore(loop.kp, loop.ki, loop.kd, loop.target)
        self.last_ai = 0.0
        self.last_err = 0.0
        self.last_ao = 0.0

    def reset(self):
        self.core.reset()

    def process_block(self, ai_block: np.ndarray, dt: float):
        if ai_block.size == 0:
            return
        for v in ai_block:
            u, e = self.core.step(v, dt)
            # Clamp to AO range
            if u < self.lo:
                u = self.lo
            elif u > self.hi:
                u = self.hi
            self.last_ai = float(v)
            self.last_err = float(e)
            self.last_ao = float(u)

class PIDManager:
    """Owns all PID loops and integrates with MainWindow."""
    def __init__(self, main_window):
        self.mw = main_window
        self.loops: List[PIDLoopDef] = []
        self.dloops: List[DigitalPID] = []
        self.aloops: List[AnalogPID] = []

    # ---------- config IO ----------
    def load_file(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            js = json.load(f)
        self.loops = []
        for item in js.get("loops", []):
            self.loops.append(PIDLoopDef(**item))
        self._rebuild_instances()

    def save_file(self, path: str):
        js = {"loops": [asdict(lp) for lp in self.loops]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(js, f, indent=2)

    # ---------- build/reset ----------
    def _rebuild_instances(self):
        self.dloops.clear(); self.aloops.clear()
        for lp in self.loops:
            if not lp.enabled:
                continue
            if lp.kind == "digital":
                # NO/NC from main config
                no = True
                try:
                    no = bool(self.mw.cfg.digitalOutputs[lp.out_ch].normallyOpen)
                except Exception:
                    pass
                self.dloops.append(DigitalPID(lp, normally_open=no))
            elif lp.kind == "analog":
                lo = lp.out_min if lp.out_min is not None else -10.0
                hi = lp.out_max if lp.out_max is not None else 10.0
                self.aloops.append(AnalogPID(lp, out_limits=(lo, hi)))

    def reset_states(self):
        for d in self.dloops: d.reset()
        for a in self.aloops: a.reset()

    # ---------- processing ----------
    def process_block(self, ai_block_2d: np.ndarray, dt: float) -> Tuple[Dict[int,bool], Dict[int,float]]:
        """
        ai_block_2d: shape (nsamples, nch)
        Returns: (do_updates, ao_updates) for this block (last-sample decisions).
        """
        if ai_block_2d.size == 0:
            return {}, {}
        ns, nch = ai_block_2d.shape
        do_cmds: Dict[int,bool] = {}
        ao_cmds: Dict[int,float] = {}

        # Digital loops
        for d in self.dloops:
            if 0 <= d.defn.ai_ch < nch:
                d.process_block(ai_block_2d[:, d.defn.ai_ch], dt)
                do_cmds[d.defn.out_ch] = d.last_do_bit

        # Analog loops
        for a in self.aloops:
            if 0 <= a.defn.ai_ch < nch:
                a.process_block(ai_block_2d[:, a.defn.ai_ch], dt)
                ao_cmds[a.defn.out_ch] = a.last_ao

        return do_cmds, ao_cmds

    # ---------- status for UI ----------
    def status_rows(self) -> List[Dict[str, Any]]:
        rows = []
        # Digital
        for d in self.dloops:
            rows.append({
                "type": "Digital PID",
                "ai": d.defn.ai_ch,
                "out": d.defn.out_ch,
                "ai_val": d.last_ai,
                "target": d.core.setpoint,
                "err": d.last_err,
                "out_val": 1 if d.last_do_bit else 0,
                "kp": d.core.kp, "ki": d.core.ki, "kd": d.core.kd,
                "enabled": True
            })
        # Analog
        for a in self.aloops:
            rows.append({
                "type": "Analog PID",
                "ai": a.defn.ai_ch,
                "out": a.defn.out_ch,
                "ai_val": a.last_ai,
                "target": a.core.setpoint,
                "err": a.last_err,
                "out_val": a.last_ao,
                "kp": a.core.kp, "ki": a.core.ki, "kd": a.core.kd,
                "enabled": True
            })
        # Include disabled loops (showed but disabled)
        for lp in self.loops:
            if lp.enabled:
                continue
            rows.append({
                "type": "Digital PID" if lp.kind=="digital" else "Analog PID",
                "ai": lp.ai_ch, "out": lp.out_ch,
                "ai_val": np.nan, "target": lp.target, "err": np.nan,
                "out_val": np.nan, "kp": lp.kp, "ki": lp.ki, "kd": lp.kd, "enabled": False
            })
        return rows

    def is_do_controlled(self, ch: int) -> bool:
        # True if any ENABLED digital PID targets this DO channel
        for lp in self.loops:
            if lp.enabled and lp.kind == "digital" and int(lp.out_ch) == int(ch):
                return True
        return False

    def is_ao_controlled(self, ch: int) -> bool:
        # True if any ENABLED analog PID targets this AO channel
        for lp in self.loops:
            if lp.enabled and lp.kind == "analog" and int(lp.out_ch) == int(ch):
                return True
        return False
