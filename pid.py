# pid.py
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple, Dict, Any
import json
import numpy as np

# ---------- Data model ----------
@dataclass
class PIDLoopDef:
    enabled: bool
    kind: str              # "digital" | "analog"
    ai_ch: int
    out_ch: int            # DO ch for digital; AO ch (0/1) for analog
    target: float
    kp: float
    ki: float
    kd: float
    # Analog output limits (only used for analog; defaults applied if None)
    out_min: Optional[float] = None
    out_max: Optional[float] = None
    # NEW: clamps
    err_min: Optional[float] = None     # clamp on error (setpoint - pv)
    err_max: Optional[float] = None
    i_min: Optional[float] = None       # clamp on integral term (already includes Ki)
    i_max: Optional[float] = None
    # pid.py  (inside PIDLoopDef dataclass)
    src: str = "ai"  # "ai" or "tc"  (NEW)

# ---------- Core ----------
class _PIDCore:
    def __init__(self, kp: float, ki: float, kd: float, setpoint: float,
                 err_min: Optional[float] = None, err_max: Optional[float] = None,
                 i_min: Optional[float] = None,   i_max: Optional[float] = None):
        self.kp = float(kp); self.ki = float(ki); self.kd = float(kd)
        self.setpoint = float(setpoint)
        self.err_min = err_min
        self.err_max = err_max
        self.i_min = i_min
        self.i_max = i_max
        self._i = 0.0
        self._prev_err = None

    @staticmethod
    def _clamp(x, lo, hi):
        if lo is not None and x < lo: return lo
        if hi is not None and x > hi: return hi
        return x

    def reset(self):
        self._i = 0.0
        self._prev_err = None

    def step(self, pv: float, dt: float) -> Tuple[float, float]:
        # error, clamped
        err_raw = self.setpoint - float(pv)
        err = self._clamp(err_raw, self.err_min, self.err_max)

        # P
        p = self.kp * err

        # I with clamp on accumulated integral (already scaled by Ki)
        self._i += self.ki * err * dt
        self._i = self._clamp(self._i, self.i_min, self.i_max)

        # D on (clamped) error
        d = 0.0
        if self._prev_err is not None and dt > 0.0:
            d = self.kd * (err - self._prev_err) / dt
        self._prev_err = err

        u = p + self._i + d
        return u, err

# ---------- Loop wrappers ----------
class DigitalPID:
    """PID that decides a boolean 'actuate' command; mapping to DO honors normallyOpen."""
    def __init__(self, loop: PIDLoopDef, normally_open: bool):
        assert loop.kind == "digital"
        self.defn = loop
        self.no = bool(normally_open)
        self.core = _PIDCore(loop.kp, loop.ki, loop.kd, loop.target,
                             loop.err_min, loop.err_max, loop.i_min, loop.i_max)
        self.last_ai = 0.0
        self.last_err = 0.0
        self.last_u   = float('nan')
        self.last_actuate = False     # logical 'actuate' (close)
        self.last_do_bit = False      # actual DO bit after NO/NC mapping

    def reset(self):
        self.core.reset()
        self.last_ai = float('nan')
        self.last_err = float('nan')
        self.last_u   = float('nan')
        self.last_actuate = False
        self.last_do_bit = None

    def process_block(self, ai_block: np.ndarray, dt: float):
        if ai_block.size == 0:
            return
        for v in ai_block:
            u, e = self.core.step(v, dt)
            self.last_u = float(u)      # <--- NEW (control effort)
            self.last_actuate = (u >= 0.0)
            self.last_ai = float(v)
            self.last_err = float(e)
        # Map to physical bit honoring NO/NC
        self.last_do_bit = self.last_actuate if self.no else (not self.last_actuate)

class AnalogPID:
    """PID that outputs a clamped analog voltage for AO 0/1."""
    def __init__(self, loop: PIDLoopDef, out_limits: Tuple[float, float]):
        assert loop.kind == "analog"
        lo, hi = out_limits
        if lo is None: lo = -10.0
        if hi is None: hi =  10.0
        self.defn = loop
        self.lo = float(lo); self.hi = float(hi)
        self.core = _PIDCore(loop.kp, loop.ki, loop.kd, loop.target,
                             loop.err_min, loop.err_max, loop.i_min, loop.i_max)
        self.last_ai = 0.0
        self.last_err = 0.0
        self.last_u = float('nan')  # <--- NEW (control effort)
        self.last_ao = 0.0

    def reset(self):
        self.core.reset()
        self.last_ai = float('nan')
        self.last_err = float('nan')
        self.last_u = float('nan')  # <--- NEW (control effort)
        self.last_ao = float('nan')

    def process_block(self, ai_block: np.ndarray, dt: float):
        if ai_block.size == 0:
            return
        for v in ai_block:
            u, e = self.core.step(v, dt)
            self.last_u = float(u)      # <--- NEW (control effort)
            # Clamp to AO range
            if u < self.lo: u = self.lo
            elif u > self.hi: u = self.hi
            self.last_ai = float(v)
            self.last_err = float(e)
            self.last_ao = float(u)

# ---------- Manager ----------
class PIDManager:
    """Owns all PID loops and integrates with MainWindow."""
    def __init__(self, main_window):
        self.mw = main_window
        self.loops: List[PIDLoopDef] = []
        self.dloops: List[DigitalPID] = []
        self.aloops: List[AnalogPID] = []

    # ----- config IO -----
    def load_file(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            js = json.load(f)
        cleaned: List[PIDLoopDef] = []

        allowed = {
            "enabled","kind","ai_ch","out_ch","target","kp","ki","kd",
            "out_min","out_max","err_min","err_max","i_min","i_max","src"
        }

        for item in js.get("loops", []):
            # filter unknown keys so older/newer files don't break
            d = {k: item[k] for k in allowed if k in item}
            # required fallbacks if missing
            d.setdefault("src", "ai")
            d.setdefault("enabled", True)
            d.setdefault("kind", "digital")
            d.setdefault("ai_ch", 0)
            d.setdefault("out_ch", 0)
            d.setdefault("target", 0.0)
            d.setdefault("kp", 0.0); d.setdefault("ki", 0.0); d.setdefault("kd", 0.0)
            cleaned.append(PIDLoopDef(**d))

        self.loops = cleaned
        self._rebuild_instances()

    def save_file(self, path: str):
        js = {"loops": [asdict(lp) for lp in self.loops]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(js, f, indent=2)

    # ----- build/reset -----
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
                hi = lp.out_max if lp.out_max is not None else  10.0
                self.aloops.append(AnalogPID(lp, out_limits=(lo, hi)))

    def reset_states(self):
        for d in self.dloops: d.reset()
        for a in self.aloops: a.reset()

    # ----- processing -----
    def process_block(self, ai_block_2d, dt: float, tc_block_2d=None):
        if ai_block_2d is None and tc_block_2d is None:
            return {}, {}
        n_ai = 0 if ai_block_2d is None else ai_block_2d.shape[1]
        n_tc = 0 if tc_block_2d is None else tc_block_2d.shape[1]
        do_cmds, ao_cmds = {}, {}

        for d in self.dloops:
            lp = d.defn
            if lp.src == "tc" and n_tc and 0 <= lp.ai_ch < n_tc:
                d.process_block(tc_block_2d[:, lp.ai_ch], dt);
                do_cmds[lp.out_ch] = d.last_do_bit
            elif lp.src == "ai" and n_ai and 0 <= lp.ai_ch < n_ai:
                d.process_block(ai_block_2d[:, lp.ai_ch], dt);
                do_cmds[lp.out_ch] = d.last_do_bit

        for a in self.aloops:
            lp = a.defn
            if lp.src == "tc" and n_tc and 0 <= lp.ai_ch < n_tc:
                a.process_block(tc_block_2d[:, lp.ai_ch], dt);
                ao_cmds[lp.out_ch] = a.last_ao
            elif lp.src == "ai" and n_ai and 0 <= lp.ai_ch < n_ai:
                a.process_block(ai_block_2d[:, lp.ai_ch], dt);
                ao_cmds[lp.out_ch] = a.last_ao

        return do_cmds, ao_cmds

    # ----- UI status -----
    def status_rows(self) -> List[Dict[str, Any]]:
        rows = []
        # Digital enabled
        for d in self.dloops:
            rows.append({
                "type": "Digital PID",
                "ai": d.defn.ai_ch,
                "out": d.defn.out_ch,
                "ai_val": d.last_ai,
                "target": d.core.setpoint,
                "pid_sum": d.last_u,
                "out_val": 1 if d.last_do_bit else 0,
                "kp": d.core.kp, "ki": d.core.ki, "kd": d.core.kd,
                "enabled": True
            })
        # Analog enabled
        for a in self.aloops:
            rows.append({
                "type": "Analog PID",
                "ai": a.defn.ai_ch,
                "out": a.defn.out_ch,
                "ai_val": a.last_ai,
                "target": a.core.setpoint,
                "pid_sum": a.last_u,
                "out_val": a.last_ao,
                "kp": a.core.kp, "ki": a.core.ki, "kd": a.core.kd,
                "enabled": True
            })
        # Disabled (for display)
        for lp in self.loops:
            if lp.enabled:
                continue
            rows.append({
                "type": "Digital PID" if lp.kind == "digital" else "Analog PID",
                "ai": lp.ai_ch, "out": lp.out_ch,
                "ai_val": np.nan, "target": lp.target, "pid_sum": np.nan,
                "out_val": np.nan, "kp": lp.kp, "ki": lp.ki, "kd": lp.kd, "enabled": False
            })
        return rows

    # ----- guards so disabled loops never write outputs -----
    def is_do_controlled(self, ch: int) -> bool:
        for lp in self.loops:
            if lp.enabled and lp.kind == "digital" and int(lp.out_ch) == int(ch):
                return True
        return False

    def is_ao_controlled(self, ch: int) -> bool:
        for lp in self.loops:
            if lp.enabled and lp.kind == "analog" and int(lp.out_ch) == int(ch):
                return True
        return False

    def apply_loop_updates(self, row: int):
        """Push the edited PIDLoopDef at index 'row' into any live instances that reference it."""
        if row < 0 or row >= len(self.loops):
            return
        lp = self.loops[row]

        # Digital instances
        for d in self.dloops:
            if d.defn is lp:  # same object reference
                c = d.core
                c.kp = float(lp.kp)
                c.ki = float(lp.ki)
                c.kd = float(lp.kd)
                c.setpoint = float(lp.target)
                c.err_min = lp.err_min
                c.err_max = lp.err_max
                c.i_min = lp.i_min
                c.i_max = lp.i_max
                d.reset()  # start fresh with new tuning

        # Analog instances
        for a in self.aloops:
            if a.defn is lp:
                c = a.core
                c.kp = float(lp.kp)
                c.ki = float(lp.ki)
                c.kd = float(lp.kd)
                c.setpoint = float(lp.target)
                c.err_min = lp.err_min
                c.err_max = lp.err_max
                c.i_min = lp.i_min
                c.i_max = lp.i_max
                # also update AO clamps if provided
                a.lo = float(lp.out_min) if lp.out_min is not None else -10.0
                a.hi = float(lp.out_max) if lp.out_max is not None else  10.0
                a.reset()  # start fresh with new tuning
