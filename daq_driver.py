
import os
from typing import Callable
# os.add_dll_directory(r"C:\Program Files (x86)\Measurement Computing\DAQ")
from typing import Callable, List

try:
    from mcculw import ul
    from mcculw.enums import ULRange, DigitalPortType, DigitalIODirection, AnalogInputMode

except Exception:
    ul = None; ULRange=None; DigitalPortType=None; DigitalIODirection=None

class DaqError(Exception): pass

class DaqDriver:
    def __init__(self, board_num: int, log_tx: Callable[[str], None], log_rx: Callable[[str], None]):
        self.board = board_num; self.log_tx=log_tx; self.log_rx=log_rx; self.connected=False
        self.log_ai_reads=False; self.valid_ai: List[int] = list(range(8));  # will be refined after connect
        self._probed_ai = False

    def connect(self):
        if ul is None: raise DaqError("mcculw not installed or cbw64.dll not found.")
        name = ul.get_board_name(self.board); self.connected=True; self.log_rx(f"Connected: {name}")
        try:
            ul.d_config_port(self.board, DigitalPortType.AUXPORT, DigitalIODirection.OUT)
            self.log_tx("AUXPORT -> OUT")
        except Exception as e:
            self.log_rx(f"DIO config warning: {e}")

    def disconnect(self):
        self.connected=False; self.log_rx("Disconnected.")

    def probe_ai_channels(self, max_ch: int = 8) -> list[int]:
        """Try each channel; remember only the ones that succeed (avoid Error 16 spam)."""
        valid = []
        for ch in range(max_ch):
            try:
                # Try a lightweight read just to verify the channel exists
                _ = ul.v_in(self.board, ch, ULRange.BIP10VOLTS)
                valid.append(ch)
            except Exception as e:
                s = str(e)
                # UL raises a ULError; matching by message is portable enough
                if "Invalid A/D channel number" in s or "Error 16" in s:
                    continue
                else:
                    # Unexpected error; surface it
                    raise
        self.valid_ai = valid
        self._probed_ai = True
        if not valid:
            self.log_rx("AI probe: no valid channels detected. Check InstaCal input mode (SE vs DIFF).")
        else:
            self.log_rx(f"AI probe: valid channels -> {valid}")
        return valid

    def set_ai_mode(self, mode_str: str) -> bool:
        """Try to set SE/DIFF in software. Returns True on success."""
        try:
            mode = (AnalogInputMode.SINGLE_ENDED
                    if mode_str.upper().startswith("SE")
                    else AnalogInputMode.DIFFERENTIAL)
            ul.a_input_mode(self.board, mode)
            self.log_tx(f"AI mode -> {mode.name}")
            return True
        except Exception as e:
            self.log_rx(f"AI mode set not supported on this device/driver: {e}")
            return False

    def read_ai_volts(self, ch:int)->float:
        v = ul.v_in(self.board, ch, ULRange.BIP10VOLTS)
        if self.log_ai_reads: self.log_rx(f"AI{ch}={v:.6f}V")
        return v

    def set_ao_volts(self, ch:int, volts:float):
        v = max(-10.0, min(10.0, float(volts))); ul.v_out(self.board, ch, ULRange.BIP10VOLTS, v)
        self.log_tx(f"AO{ch} <- {v:.4f}V")

    def set_do_bit(self, bit:int, state:bool):
        ul.d_bit_out(self.board, DigitalPortType.AUXPORT, bit, 1 if state else 0)
        self.log_tx(f"DO{bit} <- {'1' if state else '0'}")

    def get_do_bit(self, bit:int)->bool:
        val = ul.d_bit_in(self.board, DigitalPortType.AUXPORT, bit); self.log_rx(f"DO{bit}? {val}"); return bool(val)
