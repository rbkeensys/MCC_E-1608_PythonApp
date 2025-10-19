import os
import ctypes as ct
from typing import Callable, List

try:
    from mcculw import ul
    from mcculw.enums import ULRange, DigitalPortType, DigitalIODirection, FunctionType, ScanOptions, AnalogInputMode
except Exception:
    ul = None; ULRange=DigitalPortType=DigitalIODirection=FunctionType=ScanOptions=AnalogInputMode=None

class DaqError(Exception): pass

class DaqDriver:
    def __init__(self, board_num: int, log_tx: Callable[[str], None], log_rx: Callable[[str], None]):
        self.board = board_num; self.log_tx=log_tx; self.log_rx=log_rx; self.connected=False
        self.log_ai_reads=False
        self._scan_running=False; self._scan_low=0; self._scan_high=0; self._scan_num_ch=0; self._scan_rate=0.0; self._scan_count=0; self._scan_last_index=0; self._scan_mem=None
        self.valid_ai: List[int] = list(range(8)); self._probed_ai=False

    def connect(self):
        if ul is None: raise DaqError("mcculw is not installed or cbw64.dll not found.")
        name = ul.get_board_name(self.board); self.connected=True; self.log_rx(f"Connected to board {self.board}: {name}")
        try:
            ul.d_config_port(self.board, DigitalPortType.AUXPORT, DigitalIODirection.OUT); self.log_tx("d_config_port AUXPORT -> OUT")
        except Exception as e: self.log_rx(f"DIO config warning: {e}")

    def disconnect(self):
        try:
            self.stop_ai_scan()
        finally:
            self.connected=False; self.log_rx("Disconnected.")

    def set_ai_mode(self, mode_str: str) -> bool:
        try:
            mode = AnalogInputMode.SINGLE_ENDED if mode_str.upper().startswith("SE") else AnalogInputMode.DIFFERENTIAL
            ul.a_input_mode(self.board, mode); self.log_tx(f"AI mode -> {mode.name}"); return True
        except Exception as e:
            self.log_rx(f"AI mode set not supported here: {e}"); return False

    def probe_ai_channels(self, max_ch: int = 8) -> list[int]:
        valid = []
        for ch in range(max_ch):
            try:
                _ = ul.v_in(self.board, ch, ULRange.BIP10VOLTS); valid.append(ch)
            except Exception as e:
                if "Invalid A/D channel number" in str(e) or "Error 16" in str(e): continue
                else: raise
        self.valid_ai = valid; self._probed_ai=True
        self.log_rx(f"AI probe: valid channels -> {valid}" if valid else "AI probe: no valid channels detected.")
        return valid

    def read_ai_volts(self, ch:int)->float:
        v = ul.v_in(self.board, ch, ULRange.BIP10VOLTS)
        if self.log_ai_reads: self.log_rx(f"AI{ch}={v:.6f}V")
        return v

    def set_ao_volts(self, ch:int, volts:float):
        v = max(-10.0, min(10.0, float(volts))); ul.v_out(self.board, ch, ULRange.BIP10VOLTS, v); self.log_tx(f"AO{ch} <- {v:.4f}V")

    def set_do_bit(self, bit:int, state:bool):
        ul.d_bit_out(self.board, DigitalPortType.AUXPORT, bit, 1 if state else 0); self.log_tx(f"DO{bit} <- {'1' if state else '0'}")

    def get_do_bit(self, bit:int)->bool:
        val = ul.d_bit_in(self.board, DigitalPortType.AUXPORT, bit); self.log_rx(f"DO{bit}? -> {val}"); return bool(val)

    def start_ai_scan(self, low_chan: int, high_chan: int, rate_hz: float, block_size: int):
        if self._scan_running:
            self.stop_ai_scan()

        num_ch = max(1, high_chan - low_chan + 1)
        total_count = max(1, int(block_size)) * num_ch

        # Allocate scaled buffer (float volts)
        self._scan_mem = ul.scaled_win_buf_alloc(total_count)
        if not self._scan_mem:
            raise DaqError("Failed to allocate scan buffer")

        # MUST be an int; UL will adjust it to the nearest achievable rate
        req_rate = int(round(float(rate_hz)))

        opts = (ScanOptions.BACKGROUND | ScanOptions.CONTINUOUS | ScanOptions.SCALEDATA)
        actual_rate = ul.a_in_scan(self.board, low_chan, high_chan, total_count, req_rate,
                                   ULRange.BIP10VOLTS, self._scan_mem, opts)

        # Some UL builds return the adjusted rate, others return None and only change the byref arg internally.
        # Use the returned value if present; otherwise fall back to the requested integer.
        self._scan_running = True
        self._scan_low = low_chan
        self._scan_high = high_chan
        self._scan_num_ch = num_ch
        self._scan_rate = float(actual_rate if actual_rate else req_rate)
        self._scan_count = total_count
        self._scan_last_index = 0

        self.log_rx(f"AI scan started: ch {low_chan}-{high_chan}, "
                    f"req {rate_hz} Hz -> actual {self._scan_rate:.3f} Hz, block {block_size}")

        return self._scan_rate

    def stop_ai_scan(self):
        if not self._scan_running: return
        try:
            ul.stop_background(self.board, FunctionType.AIFUNCTION)
        finally:
            if self._scan_mem: ul.win_buf_free(self._scan_mem)
            self._scan_mem=None; self._scan_running=False; self.log_rx("AI scan stopped")

    def read_ai_new(self):
        if not self._scan_running:
            return None

        status, cur_count, cur_index = ul.get_status(self.board, FunctionType.AIFUNCTION)
        total = self._scan_count
        last = self._scan_last_index
        new_total = (cur_index - last) % total
        if new_total == 0:
            return (self._scan_low, self._scan_num_ch, [[] for _ in range(self._scan_num_ch)])

        # Only process **whole frames** so every channel gets the same number of samples
        frame = self._scan_num_ch
        processed = new_total - (new_total % frame)
        if processed == 0:
            # wait until we have at least one full per-channel sample
            return (self._scan_low, self._scan_num_ch, [[] for _ in range(self._scan_num_ch)])

        def pull(first, count):
            arr = (ct.c_double * count)()
            ul.scaled_win_buf_to_array(self._scan_mem, arr, first, count)
            return list(arr)

        if last + processed <= total:
            data = pull(last, processed)
        else:
            tail = total - last
            head = processed - tail
            data = pull(last, tail) + pull(0, head)

        # Deinterleave; which channel comes first depends on ring position
        ch0_offset = last % frame
        ch_lists = [[] for _ in range(frame)]
        for i, val in enumerate(data):
            ch_idx = (ch0_offset + i) % frame
            ch_lists[ch_idx].append(val)

        # Advance only by what we processed (remainder is left for next tick)
        self._scan_last_index = (last + processed) % total
        return (self._scan_low, self._scan_num_ch, ch_lists)
