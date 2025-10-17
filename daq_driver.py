import threading
import time
from typing import Callable, List, Optional


try:
    import os
    os.add_dll_directory(r"C:\Program Files (x86)\Measurement Computing\DAQ")
    from mcculw import ul
    from mcculw.enums import ULRange, DigitalPortType, DigitalIODirection
except Exception as e:
    ul = None
    ULRange = None
    DigitalPortType = None
    DigitalIODirection = None

class DaqError(Exception):
    pass

class DaqDriver:
    """Small wrapper around mcculw for E-1608."""
    def __init__(self, board_num: int, log_tx: Callable[[str], None], log_rx: Callable[[str], None]):
        self.board = board_num
        self.log_tx = log_tx
        self.log_rx = log_rx
        self.connected = False

    def connect(self):
        if ul is None:
            raise DaqError("mcculw is not installed.")
        # Simple probe: get_board_name may raise if not present
        name = ul.get_board_name(self.board)
        self.connected = True
        self.log_rx(f"Connected to board {self.board}: {name}")
        # Configure DIO port as output (AUXPORT = 8 lines)
        try:
            ul.d_config_port(self.board, DigitalPortType.AUXPORT, DigitalIODirection.OUT)
            self.log_tx("d_config_port AUXPORT -> OUT")
        except Exception as e:
            self.log_rx(f"DIO config warning: {e}")

    def disconnect(self):
        self.connected = False
        self.log_rx("Disconnected.")

    # ---------- Analog In (software-paced) ----------
    def read_ai_volts(self, ch: int) -> float:
        v = ul.v_in(self.board, ch, ULRange.BIP10VOLTS)
        self.log_rx(f"AI{ch} = {v:.6f} V")
        return v

    # ---------- Analog Out ----------
    def set_ao_volts(self, ch: int, volts: float):
        v = max(-10.0, min(10.0, float(volts)))
        ul.v_out(self.board, ch, ULRange.BIP10VOLTS, v)
        self.log_tx(f"AO{ch} <- {v:.4f} V (clamped ±10V)")

    # ---------- Digital Out ----------
    def set_do_bit(self, bit: int, state: bool):
        ul.d_bit_out(self.board, DigitalPortType.AUXPORT, bit, 1 if state else 0)
        self.log_tx(f"DO{bit} <- {'1' if state else '0'}")

    def get_do_bit(self, bit: int) -> bool:
        # Readback via input port; E-1608 supports bidirectional port A.
        val = ul.d_bit_in(self.board, DigitalPortType.AUXPORT, bit)
        self.log_rx(f"DO{bit}? -> {val}")
        return bool(val)
