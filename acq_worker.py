from PyQt6 import QtCore
import numpy as np
from filters import OnePoleLPF

class AcqWorker(QtCore.QThread):
    # Emits dict: {"low": int, "num_ch": int, "M": int, "data": np.ndarray[num_ch, M]}
    chunkReady = QtCore.pyqtSignal(object)

    def __init__(self, daq, slopes, offsets, cutoffs, fs_hz, parent=None):
        super().__init__(parent)
        self._daq = daq
        self._stop = False
        self._slopes = np.asarray(slopes, dtype=float)
        self._offsets = np.asarray(offsets, dtype=float)
        self._cutoffs = np.asarray(cutoffs, dtype=float)
        self._fs = float(fs_hz)
        # Create per-channel filters (8 total), but only used for present channels
        self._filters = [OnePoleLPF(float(self._cutoffs[i]), self._fs) for i in range(8)]

    def stop(self):
        self._stop = True

    def run(self):
        # Tight loop to drain scan buffer; sleep a hair to avoid pegging a core
        while not self._stop:
            try:
                pkt = self._daq.read_ai_new()
            except Exception:
                pkt = None
            if not pkt:
                self.msleep(3)
                continue

            low, num_ch, ch_lists = pkt
            if not ch_lists or not any(len(c) for c in ch_lists):
                self.msleep(2)
                continue

            # All lists should be equal length (driver returns full frames), but be defensive
            M = min(len(c) for c in ch_lists) if ch_lists else 0
            if M <= 0:
                continue

            # Convert to (num_ch, M)
            data = np.empty((num_ch, M), dtype=float)
            for i in range(num_ch):
                row = ch_lists[i]
                if len(row) != M:
                    row = row[-M:]
                data[i, :] = np.asarray(row, dtype=float)

            # Apply calibration + optional LPF per physical channel index
            for i in range(num_ch):
                phys_ch = low + i
                # y = sample * slope + offset
                data[i, :] = data[i, :] * self._slopes[phys_ch] + self._offsets[phys_ch]
                if self._cutoffs[phys_ch] > 0.0:
                    self._filters[phys_ch].set_fs(self._fs)
                    data[i, :] = self._filters[phys_ch].process_chunk(data[i, :])

            self.chunkReady.emit({"low": low, "num_ch": num_ch, "M": int(M), "data": data})
            # Small nap to cede GIL and keep UI snappy
            self.msleep(1)
