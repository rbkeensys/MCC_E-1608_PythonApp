# etc_device.py
import time
from typing import List, Optional
import numpy as np

# Preferred: ULDAQ (works well with E-series like E-TC)
HAVE_ULDAQ = False
try:
    from uldaq import (
        get_daq_device_inventory,
        DaqDevice,
        InterfaceType,
        TempScale,
        TInFlags,
        ThermocoupleType,
    )
    # Config API is optional on some builds — guard it
    try:
        from uldaq import ConfigItem  # type: ignore
        HAVE_ULDAQ_CFG = True
    except Exception:
        HAVE_ULDAQ_CFG = False
    HAVE_ULDAQ = True
except Exception:
    HAVE_ULDAQ_CFG = False
    HAVE_ULDAQ = False

# Fallback: MCC Universal Library (Windows via InstaCal)
HAVE_MCCULW = False
try:
    from mcculw import ul as _ul
    from mcculw.enums import TempScale as MCCTempScale
    try:
        # These may not exist on all installs; use best-effort
        from mcculw.enums import TcType as MCCTcType
        from mcculw.enums import InfoType, BoardInfo
        HAVE_MCC_SET_TYPE = True
    except Exception:
        HAVE_MCC_SET_TYPE = False
    HAVE_MCCULW = True
except Exception:
    HAVE_MCC_SET_TYPE = False
    HAVE_MCCULW = False


# ---------------- Mapping helpers ----------------

_TC_MAP_ULDAQ = {
    "J": ThermocoupleType.J if HAVE_ULDAQ else None,
    "K": ThermocoupleType.K if HAVE_ULDAQ else None,
    "T": ThermocoupleType.T if HAVE_ULDAQ else None,
    "E": ThermocoupleType.E if HAVE_ULDAQ else None,
    "N": ThermocoupleType.N if HAVE_ULDAQ else None,
    "B": ThermocoupleType.B if HAVE_ULDAQ else None,
    "R": ThermocoupleType.R if HAVE_ULDAQ else None,
    "S": ThermocoupleType.S if HAVE_ULDAQ else None,
}

_TC_MAP_MCC = {
    # Not all installs expose MCCTcType — guard at callsite as well.
    "J": getattr(MCCTcType, "J", None) if HAVE_MCCULW else None,
    "K": getattr(MCCTcType, "K", None) if HAVE_MCCULW else None,
    "T": getattr(MCCTcType, "T", None) if HAVE_MCCULW else None,
    "E": getattr(MCCTcType, "E", None) if HAVE_MCCULW else None,
    "N": getattr(MCCTcType, "N", None) if HAVE_MCCULW else None,
    "B": getattr(MCCTcType, "B", None) if HAVE_MCCULW else None,
    "R": getattr(MCCTcType, "R", None) if HAVE_MCCULW else None,
    "S": getattr(MCCTcType, "S", None) if HAVE_MCCULW else None,
}


class ETCDevice:
    """
    Thin wrapper for an MCC E-TC device.

    Public API used by main.py:
      - connect() -> bool
      - read_tc_once(enabled_chs: List[int], tc_types: List[str]) -> np.ndarray[float] (°C)
      - read_block(enabled_chs, tc_types, block_len, sp) -> (nsamples x nch, nch)
      - .connected (bool)
    """

    def __init__(self, board: int = 0, sample_rate_hz: float = 10.0, log=None):
        self.board = int(board)
        self.rate = float(sample_rate_hz)
        self.connected: bool = False
        self._mcc_can_set_type = False
        self._warned_mcc_set_type = False

        # ULDAQ objects
        self._daq: Optional['DaqDevice'] = None
        self._tdev = None  # temperature device handle
        self._uldaq_in_use = False

        # MCCULW board number
        self._mcc_board_num: Optional[int] = None

        # cache last-set TC type per channel (to avoid redundant config writes)
        self._last_type_set = {}

        # sample hold state for read_block
        self._last_vals = None
        self._last_ts = 0.0

        # logging
        self._log = log if (log is not None) else (lambda s: None)

    # --------------- Connection ---------------

    def connect(self) -> bool:
        """
        Try ULDAQ first (Ethernet discovery). If not available, fall back to mcculw/Instacal board.
        """
        # Try ULDAQ
        if HAVE_ULDAQ:
            try:
                inv = get_daq_device_inventory(InterfaceType.ETHERNET)
                if not inv:
                    # Some E-TCs enumerate as ANY or USB over certain bridges — broaden search
                    inv = get_daq_device_inventory(InterfaceType.ANY)
                if not inv:
                    self._log("[E-TC] No ULDAQ devices found.")
                else:
                    idx = self.board
                    if idx < 0 or idx >= len(inv):
                        self._log(f"[E-TC] ULDAQ: board index {idx} out of range (0..{len(inv)-1}).")
                    else:
                        desc = inv[idx]
                        self._daq = DaqDevice(desc)
                        self._tdev = self._daq.get_temp_device()
                        self._daq.connect()
                        # Make sure we actually have a temperature subsystem
                        if self._tdev is None:
                            self._log("[E-TC] ULDAQ: temperature device not present on selected descriptor.")
                        else:
                            self._uldaq_in_use = True
                            self.connected = True
                            return True
            except Exception as e:
                self._log(f"[E-TC] ULDAQ connect failed: {e}")

        # Fall back to MCCULW
        if HAVE_MCCULW:
            try:
                self._mcc_board_num = self.board  # InstaCal board number
                # Capability: can we set TC type programmatically on this install?
                try:
                    from mcculw.enums import InfoType, BoardInfo, TcType as MCCTcType  # may not exist
                    self._mcc_can_set_type = hasattr(BoardInfo, "TCTYPE")
                except Exception:
                    self._mcc_can_set_type = False
                self._warned_mcc_set_type = False

                # A quick probe read (channel 0) to validate presence; ignore result
                try:
                    _ = _ul.t_in(self._mcc_board_num, 0, MCCTempScale.CELSIUS)
                except Exception:
                    pass

                self.connected = True
                self._uldaq_in_use = False
                return True
            except Exception as e:
                self._log(f"[E-TC] MCCULW connect failed: {e}")

        self.connected = False
        return False

    # --------------- Reads ---------------

    def _set_tc_type_if_needed(self, ch: int, typ: str):
        """
        Best-effort per-channel type set. Works on ULDAQ when ConfigItem is exposed.
        On MCCULW we try set_config if available; otherwise we skip (InstaCal config is used).
        """
        t = (typ or "K").upper()
        if self._last_type_set.get(ch) == t:
            return  # already set

        # ULDAQ path
        if self._uldaq_in_use and HAVE_ULDAQ and HAVE_ULDAQ_CFG and self._daq is not None:
            try:
                tc_enum = _TC_MAP_ULDAQ.get(t)
                if tc_enum is None:
                    raise ValueError(f"Unknown TC type '{t}' for ULDAQ")
                # ConfigItem naming varies by ULDAQ build; try a few likely options
                try:
                    # Common name on recent builds:
                    self._daq.get_config().set_cfg(ConfigItem.TEMP_SENSOR_TYPE, ch, tc_enum)  # type: ignore
                except Exception:
                    # Older name fallback:
                    self._daq.get_config().set_cfg(ConfigItem.TEMPERATURE_SENSOR_TYPE, ch, tc_enum)  # type: ignore
                self._last_type_set[ch] = t
                return
            except Exception as e:
                # Non-fatal; continue with read
                self._log(f"[E-TC] ULDAQ: set TC type failed on ch {ch}: {e}")

        # MCC UL path (only if install exposes TCTYPE)
        if HAVE_MCCULW and self._mcc_board_num is not None and getattr(self, "_mcc_can_set_type", False):
            try:
                tc_enum = _TC_MAP_MCC.get(t)
                if tc_enum is None:
                    return
                from mcculw.enums import InfoType, BoardInfo
                _ul.set_config(InfoType.BOARDINFO, self._mcc_board_num, ch, BoardInfo.TCTYPE, tc_enum)
                self._last_type_set[ch] = t
            except Exception:
                if not getattr(self, "_warned_mcc_set_type", False):
                    self._log("[E-TC] MCCULW: TC type set not supported on this install; using InstaCal settings.")
                    self._warned_mcc_set_type = True
            return
        # If we reach here, either unsupported or not available – just use current device config
        return

    def read_tc_once(self, enabled_chs: List[int], tc_types: List[str]) -> np.ndarray:
        """
        Read one temperature sample per requested channel, in °C.
        Returns a 1-D float array aligned to enabled_chs.
        """
        if not self.connected or len(enabled_chs) == 0:
            return np.array([], dtype=float)

        out = np.empty(len(enabled_chs), dtype=float)
        # ULDAQ path
        if self._uldaq_in_use and HAVE_ULDAQ and (self._tdev is not None):
            for i, ch in enumerate(enabled_chs):
                typ = (tc_types[i] if i < len(tc_types) else "K") or "K"
                # Try to set the TC type (non-fatal if not supported)
                self._set_tc_type_if_needed(int(ch), str(typ).upper())
                try:
                    v = self._tdev.t_in(int(ch), TempScale.CELSIUS, TInFlags.DEFAULT)
                    out[i] = float(v)
                except Exception as e:
                    self._log(f"[E-TC] ULDAQ t_in failed ch {ch}: {e}")
                    out[i] = np.nan
            return out

        # MCCULW path
        if HAVE_MCCULW and (self._mcc_board_num is not None):
            for i, ch in enumerate(enabled_chs):
                typ = (tc_types[i] if i < len(tc_types) else "K") or "K"
                # Best-effort type set (ignored if not supported)
                self._set_tc_type_if_needed(int(ch), str(typ).upper())
                try:
                    v = _ul.t_in(self._mcc_board_num, int(ch), MCCTempScale.CELSIUS)
                    out[i] = float(v)
                except Exception as e:
                    self._log(f"[E-TC] MCC t_in failed ch {ch}: {e}")
                    out[i] = np.nan
            return out

        # No driver available
        return np.array([np.nan] * len(enabled_chs), dtype=float)

    def read_block(self, enabled_chs: List[int], tc_types: List[str], block_len: int, sp: float):
        """
        Returns a (nsamples, nch) matrix. We "sample-and-hold" TC values across the AI block,
        refreshing at self.rate Hz at most.
        """
        if not self.connected or not enabled_chs:
            return np.empty((0, 0), dtype=float), 0

        now = time.time()
        refresh_period = 1.0 / max(self.rate, 1e-6)
        need_new = (self._last_vals is None) or ((now - self._last_ts) >= refresh_period)

        if need_new:
            vals = self.read_tc_once(enabled_chs, tc_types)  # 1-D
            self._last_vals = vals
            self._last_ts = now

        ns = int(block_len)
        if ns <= 0:
            return np.empty((0, len(enabled_chs)), dtype=float), len(enabled_chs)

        tile = np.tile(self._last_vals, (ns, 1)).astype(float, copy=False)  # (ns, nch)
        return tile, len(enabled_chs)

    # --------------- Lifecycle ---------------

    def disconnect(self):
        try:
            if self._uldaq_in_use and self._daq:
                self._daq.disconnect()
        except Exception:
            pass
        self._daq = None
        self._tdev = None
        self._mcc_board_num = None
        self.connected = False
