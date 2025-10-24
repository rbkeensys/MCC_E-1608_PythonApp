# config_editor.py
from __future__ import annotations
from typing import List
from PyQt6 import QtCore, QtWidgets
from config_manager import AppConfig, AnalogCfg, DigitalOutCfg, AnalogOutCfg


MAX_AI = 8    # E-1608
MAX_DO = 8    # E-1608
MAX_AO = 2    # E-1608
MAX_TC = 32   # E-TC family (safe upper bound)


class ConfigEditorDialog(QtWidgets.QDialog):
    """
    Full editor with:
      - General (E-1608 + E-TC)
      - Analog Inputs (Add/Remove, Include)
      - Digital Outputs (Add/Remove, Include)
      - Analog Outputs (Include)
      - Thermocouples (Add/Remove, Include, Type)
    Safe with both flat AppConfig (boardNum, ...) and nested (b1608/betc).
    """
    def __init__(self, cfg: AppConfig, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit Config")
        self.resize(980, 680)
        self._cfg = cfg

        v = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        v.addWidget(self.tabs)

        # ----- Tabs -----
        self._build_tab_general(cfg)
        self._build_tab_ai(cfg)
        self._build_tab_do(cfg)
        self._build_tab_ao(cfg)
        self._build_tab_tc(cfg)

        # ----- Buttons -----
        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        self.btn_ok = QtWidgets.QPushButton("OK")
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        btns.addWidget(self.btn_ok); btns.addWidget(self.btn_cancel)
        v.addLayout(btns)

        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

    # ===================== General =====================
    def _build_tab_general(self, cfg: AppConfig):
        w = QtWidgets.QWidget(); form = QtWidgets.QFormLayout(w)

        # Prefer nested b1608, fall back to flat
        b1608 = getattr(cfg, "b1608", None)
        betc  = getattr(cfg, "betc",  None)

        # E-1608
        board1608 = getattr(b1608, "boardNum", getattr(cfg, "boardNum", 0))
        rate1608  = getattr(b1608, "sampleRateHz", getattr(cfg, "sampleRateHz", 100.0))
        block1608 = getattr(b1608, "blockSize", getattr(cfg, "blockSize", 128))
        mode1608  = getattr(b1608, "aiMode", getattr(cfg, "aiMode", "SE"))

        self.sp_1608_board = QtWidgets.QSpinBox(); self.sp_1608_board.setRange(0, 31); self.sp_1608_board.setValue(int(board1608))
        self.dbl_1608_rate = QtWidgets.QDoubleSpinBox(); self.dbl_1608_rate.setDecimals(3); self.dbl_1608_rate.setRange(0.1, 1_000_000.0); self.dbl_1608_rate.setValue(float(rate1608))
        self.sp_1608_block = QtWidgets.QSpinBox(); self.sp_1608_block.setRange(1, 1_000_000); self.sp_1608_block.setValue(int(block1608))
        self.cmb_1608_mode = QtWidgets.QComboBox(); self.cmb_1608_mode.addItems(["SE","DIFF"])
        try:
            self.cmb_1608_mode.setCurrentText(str(mode1608).upper())
        except Exception:
            self.cmb_1608_mode.setCurrentIndex(0)

        form.addRow(QtWidgets.QLabel("<b>E-1608</b>"))
        form.addRow("Board #", self.sp_1608_board)
        form.addRow("Sample rate (Hz)", self.dbl_1608_rate)
        form.addRow("Block size", self.sp_1608_block)
        form.addRow("AI mode", self.cmb_1608_mode)

        # E-TC
        etc_board = getattr(betc, "boardNum", 0)
        etc_rate  = getattr(betc, "sampleRateHz", 10.0)
        etc_block = getattr(betc, "blockSize", 128)

        self.sp_etc_board = QtWidgets.QSpinBox(); self.sp_etc_board.setRange(0, 31); self.sp_etc_board.setValue(int(etc_board))
        self.dbl_etc_rate = QtWidgets.QDoubleSpinBox(); self.dbl_etc_rate.setDecimals(3); self.dbl_etc_rate.setRange(0.1, 100_000.0); self.dbl_etc_rate.setValue(float(etc_rate))
        self.sp_etc_block = QtWidgets.QSpinBox(); self.sp_etc_block.setRange(1, 1_000_000); self.sp_etc_block.setValue(int(etc_block))

        form.addRow(QtWidgets.QLabel(""))
        form.addRow(QtWidgets.QLabel("<b>E-TC</b>"))
        form.addRow("Board #", self.sp_etc_board)
        form.addRow("Sample rate (Hz)", self.dbl_etc_rate)
        form.addRow("Block size", self.sp_etc_block)

        self.tabs.addTab(w, "General")

    # ===================== Analog Inputs =====================
    def _build_tab_ai(self, cfg: AppConfig):
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w)
        self.tbl_ai = QtWidgets.QTableWidget()
        self.tbl_ai.setColumnCount(7)
        self.tbl_ai.setHorizontalHeaderLabels(["Channel","Name","Slope","Offset","Cutoff Hz","Units","Include"])
        self.tbl_ai.horizontalHeader().setStretchLastSection(False)
        v.addWidget(self.tbl_ai)

        # Buttons
        hb = QtWidgets.QHBoxLayout(); hb.addStretch(1)
        self.btn_ai_add = QtWidgets.QPushButton("Add")
        self.btn_ai_remove = QtWidgets.QPushButton("Remove")
        hb.addWidget(self.btn_ai_add); hb.addWidget(self.btn_ai_remove)
        v.addLayout(hb)

        # Fill table
        rows = max(len(getattr(cfg, "analogs", [])), 1)
        self.tbl_ai.setRowCount(min(rows, MAX_AI))
        for r in range(min(rows, MAX_AI)):
            a = cfg.analogs[r]
            self._ai_set_row(r, r, a)

        # Signals
        self.btn_ai_add.clicked.connect(self._ai_on_add)
        self.btn_ai_remove.clicked.connect(self._ai_on_remove)

        self.tabs.addTab(w, "Analog Inputs")

    def _ai_set_row(self, row: int, ch: int, a: AnalogCfg):
        sp = QtWidgets.QSpinBox(); sp.setRange(0, MAX_AI - 1); sp.setValue(int(ch))
        self.tbl_ai.setCellWidget(row, 0, sp)

        self.tbl_ai.setItem(row, 1, QtWidgets.QTableWidgetItem(a.name))
        self.tbl_ai.setItem(row, 2, self._num_item(a.slope))
        self.tbl_ai.setItem(row, 3, self._num_item(a.offset))
        self.tbl_ai.setItem(row, 4, self._num_item(a.cutoffHz))
        self.tbl_ai.setItem(row, 5, QtWidgets.QTableWidgetItem(getattr(a, "units", "") or ""))

        inc = QtWidgets.QTableWidgetItem()
        inc.setFlags(inc.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        inc.setCheckState(QtCore.Qt.CheckState.Checked if getattr(a, "include", True) else QtCore.Qt.CheckState.Unchecked)
        self.tbl_ai.setItem(row, 6, inc)

    def _ai_on_add(self):
        r = self.tbl_ai.rowCount()
        if r >= MAX_AI:
            return
        self.tbl_ai.insertRow(r)
        self._ai_set_row(r, r, AnalogCfg(name=f"AI{r}"))

    def _ai_on_remove(self):
        r = self.tbl_ai.currentRow()
        if r >= 0 and self.tbl_ai.rowCount() > 0:
            self.tbl_ai.removeRow(r)

    # ===================== Digital Outputs =====================
    def _build_tab_do(self, cfg: AppConfig):
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w)
        self.tbl_do = QtWidgets.QTableWidget()
        self.tbl_do.setColumnCount(5)
        self.tbl_do.setHorizontalHeaderLabels(["Channel","Name","Normally Open","Momentary (ms)","Include"])
        self.tbl_do.horizontalHeader().setStretchLastSection(False)
        v.addWidget(self.tbl_do)

        hb = QtWidgets.QHBoxLayout(); hb.addStretch(1)
        self.btn_do_add = QtWidgets.QPushButton("Add")
        self.btn_do_remove = QtWidgets.QPushButton("Remove")
        hb.addWidget(self.btn_do_add); hb.addWidget(self.btn_do_remove)
        v.addLayout(hb)

        rows = max(len(getattr(cfg, "digitalOutputs", [])), 1)
        self.tbl_do.setRowCount(min(rows, MAX_DO))
        for r in range(min(rows, MAX_DO)):
            d = cfg.digitalOutputs[r]
            self._do_set_row(r, r, d)

        self.btn_do_add.clicked.connect(self._do_on_add)
        self.btn_do_remove.clicked.connect(self._do_on_remove)

        self.tabs.addTab(w, "Digital Outputs")

    def _do_set_row(self, row: int, ch: int, d: DigitalOutCfg):
        sp = QtWidgets.QSpinBox(); sp.setRange(0, MAX_DO - 1); sp.setValue(int(ch))
        self.tbl_do.setCellWidget(row, 0, sp)

        self.tbl_do.setItem(row, 1, QtWidgets.QTableWidgetItem(d.name))

        cb_no = QtWidgets.QTableWidgetItem()
        cb_no.setFlags(cb_no.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        cb_no.setCheckState(QtCore.Qt.CheckState.Checked if d.normallyOpen else QtCore.Qt.CheckState.Unchecked)
        self.tbl_do.setItem(row, 2, cb_no)

        # store milliseconds in a normal item (number-as-text)
        self.tbl_do.setItem(row, 3, self._num_item(getattr(d, "actuationTime", 0.0)))

        inc = QtWidgets.QTableWidgetItem()
        inc.setFlags(inc.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        inc.setCheckState(QtCore.Qt.CheckState.Checked if getattr(d, "include", True) else QtCore.Qt.CheckState.Unchecked)
        self.tbl_do.setItem(row, 4, inc)

    def _do_on_add(self):
        r = self.tbl_do.rowCount()
        if r >= MAX_DO:
            return
        self.tbl_do.insertRow(r)
        self._do_set_row(r, r, DigitalOutCfg(name=f"DO{r}"))

    def _do_on_remove(self):
        r = self.tbl_do.currentRow()
        if r >= 0 and self.tbl_do.rowCount() > 0:
            self.tbl_do.removeRow(r)

    # ===================== Analog Outputs =====================
    def _build_tab_ao(self, cfg: AppConfig):
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w)
        self.tbl_ao = QtWidgets.QTableWidget()
        self.tbl_ao.setColumnCount(6)
        self.tbl_ao.setHorizontalHeaderLabels(["Channel","Name","Min V","Max V","Startup V","Include"])
        self.tbl_ao.horizontalHeader().setStretchLastSection(False)
        v.addWidget(self.tbl_ao)

        rows = max(len(getattr(cfg, "analogOutputs", [])), MAX_AO)
        self.tbl_ao.setRowCount(min(rows, MAX_AO))
        for r in range(min(rows, MAX_AO)):
            a = cfg.analogOutputs[r]
            self._ao_set_row(r, r, a)

        self.tabs.addTab(w, "Analog Outputs")

    def _ao_set_row(self, row: int, ch: int, a: AnalogOutCfg):
        sp = QtWidgets.QSpinBox(); sp.setRange(0, MAX_AO - 1); sp.setValue(int(ch))
        self.tbl_ao.setCellWidget(row, 0, sp)

        self.tbl_ao.setItem(row, 1, QtWidgets.QTableWidgetItem(a.name))
        self.tbl_ao.setItem(row, 2, self._num_item(a.minV))
        self.tbl_ao.setItem(row, 3, self._num_item(a.maxV))
        self.tbl_ao.setItem(row, 4, self._num_item(a.startupV))

        inc = QtWidgets.QTableWidgetItem()
        inc.setFlags(inc.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        inc.setCheckState(QtCore.Qt.CheckState.Checked if getattr(a, "include", True) else QtCore.Qt.CheckState.Unchecked)
        self.tbl_ao.setItem(row, 5, inc)

    # ===================== Thermocouples (E-TC) =====================
    def _build_tab_tc(self, cfg: AppConfig):
        w = QtWidgets.QWidget();
        v = QtWidgets.QVBoxLayout(w)
        self.tbl_tc = QtWidgets.QTableWidget()
        # Columns: Include | Ch | Name | Type | Offset
        self.tbl_tc.setColumnCount(5)
        self.tbl_tc.setHorizontalHeaderLabels(["Include", "Ch", "Name", "Type", "Offset (°C)"])
        self.tbl_tc.horizontalHeader().setStretchLastSection(False)
        v.addWidget(self.tbl_tc)

        hb = QtWidgets.QHBoxLayout();
        hb.addStretch(1)
        self.btn_tc_add = QtWidgets.QPushButton("Add")
        self.btn_tc_remove = QtWidgets.QPushButton("Remove")
        hb.addWidget(self.btn_tc_add);
        hb.addWidget(self.btn_tc_remove)
        v.addLayout(hb)

        tc_list = list(getattr(cfg, "thermocouples", []))
        rows = max(len(tc_list), 0)
        self.tbl_tc.setRowCount(min(rows if rows else 0, MAX_TC))
        # if no entries, start empty (user can Add)
        for r in range(min(rows, MAX_TC)):
            d = tc_list[r] or {}
            self._tc_set_row(
                r,
                include=bool(d.get("include", True)),
                ch=int(d.get("ch", r)),
                name=str(d.get("name", f"TC{r}")),
                typ=str(d.get("type", "K")).upper(),
                offset=float(d.get("offset", 0.0)),
            )

        self.btn_tc_add.clicked.connect(self._tc_on_add)
        self.btn_tc_remove.clicked.connect(self._tc_on_remove)

        self.tabs.addTab(w, "Thermocouples (E-TC)")

    def _tc_set_row(self, row: int, include: bool, ch: int, name: str, typ: str, offset: float):
        # 0: Include (check)
        inc = QtWidgets.QTableWidgetItem()
        inc.setFlags(inc.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        inc.setCheckState(QtCore.Qt.CheckState.Checked if include else QtCore.Qt.CheckState.Unchecked)
        self.tbl_tc.setItem(row, 0, inc)

        # 1: Channel (spin)
        sp = QtWidgets.QSpinBox();
        sp.setRange(0, MAX_TC - 1);
        sp.setValue(int(ch))
        self.tbl_tc.setCellWidget(row, 1, sp)

        # 2: Name (text)
        self.tbl_tc.setItem(row, 2, QtWidgets.QTableWidgetItem(name))

        # 3: Type (combo)
        cb = QtWidgets.QComboBox();
        cb.addItems(["J", "K", "T", "E", "N", "B", "R", "S"])
        if typ.upper() not in ["J", "K", "T", "E", "N", "B", "R", "S"]:
            typ = "K"
        cb.setCurrentText(typ.upper())
        self.tbl_tc.setCellWidget(row, 3, cb)

        # 4: Offset (°C) (number)
        self.tbl_tc.setItem(row, 4, self._num_item(offset))

    def _tc_on_add(self):
        r = self.tbl_tc.rowCount()
        if r >= MAX_TC:
            return
        self.tbl_tc.insertRow(r)
        self._tc_set_row(r, include=True, ch=r, name=f"TC{r}", typ="K", offset=0.0)

    def _tc_on_remove(self):
        r = self.tbl_tc.currentRow()
        if r >= 0 and self.tbl_tc.rowCount() > 0:
            self.tbl_tc.removeRow(r)

    # ===================== Helpers =====================
    @staticmethod
    def _num_item(val: float) -> QtWidgets.QTableWidgetItem:
        it = QtWidgets.QTableWidgetItem(f"{float(val):.6g}")
        it.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        return it

    # ===================== Save =====================
    def result_config(self) -> AppConfig:
        """
        Build a new AppConfig from the UI state (non-destructive copy of unknown fields).
        Works with both nested (b1608/betc) and flat AppConfig shapes.
        """
        cfg = self._cfg  # edit in-place to preserve any extra fields the app might rely on

        # ---- Boards (E-1608) ----
        if hasattr(cfg, "b1608"):
            cfg.b1608.boardNum = int(self.sp_1608_board.value())
            cfg.b1608.sampleRateHz = float(self.dbl_1608_rate.value())
            cfg.b1608.blockSize = int(self.sp_1608_block.value())
            cfg.b1608.aiMode = self.cmb_1608_mode.currentText().upper()
        else:
            cfg.boardNum = int(self.sp_1608_board.value())
            cfg.sampleRateHz = float(self.dbl_1608_rate.value())
            cfg.blockSize = int(self.sp_1608_block.value())
            cfg.aiMode = self.cmb_1608_mode.currentText().upper()

        # ---- Boards (E-TC) ----
        if hasattr(cfg, "betc"):
            cfg.betc.boardNum = int(self.sp_etc_board.value())
            cfg.betc.sampleRateHz = float(self.dbl_etc_rate.value())
            cfg.betc.blockSize = int(self.sp_etc_block.value())
        elif hasattr(cfg, "etc"):
            # dict-like property (back-compat)
            cfg.etc = {
                "board": int(self.sp_etc_board.value()),
                "sample_rate_hz": float(self.dbl_etc_rate.value()),
                "block_size": int(self.sp_etc_block.value()),
            }

        # ---- Analog Inputs ----
        ai_rows = min(self.tbl_ai.rowCount(), MAX_AI)
        new_analogs: List[AnalogCfg] = []
        for r in range(ai_rows):
            ch = self.tbl_ai.cellWidget(r, 0).value() if self.tbl_ai.cellWidget(r, 0) else r
            name = self._safe_text(self.tbl_ai.item(r, 1), f"AI{ch}")
            slope = self._safe_float(self.tbl_ai.item(r, 2), 1.0)
            offset = self._safe_float(self.tbl_ai.item(r, 3), 0.0)
            cutoff = self._safe_float(self.tbl_ai.item(r, 4), 0.0)
            units = self._safe_text(self.tbl_ai.item(r, 5), "")
            inc_it = self.tbl_ai.item(r, 6)
            include = (inc_it.checkState() == QtCore.Qt.CheckState.Checked) if inc_it else True
            a = AnalogCfg(name=name, slope=slope, offset=offset, cutoffHz=cutoff, units=units)
            # include may not exist in older AnalogCfg; attach dynamically
            setattr(a, "include", bool(include))
            new_analogs.append(a)
        # Pad to MAX_AI if existing cfg expects 8
        while len(new_analogs) < MAX_AI:
            a = AnalogCfg(name=f"AI{len(new_analogs)}")
            setattr(a, "include", True)
            new_analogs.append(a)
        cfg.analogs = new_analogs[:MAX_AI]

        # ---- Digital Outputs ----
        do_rows = min(self.tbl_do.rowCount(), MAX_DO)
        new_dos: List[DigitalOutCfg] = []
        for r in range(do_rows):
            ch = self.tbl_do.cellWidget(r, 0).value() if self.tbl_do.cellWidget(r, 0) else r
            name = self._safe_text(self.tbl_do.item(r, 1), f"DO{ch}")
            no_it = self.tbl_do.item(r, 2);  normally_open = (no_it.checkState() == QtCore.Qt.CheckState.Checked) if no_it else True
            ms = self._safe_float(self.tbl_do.item(r, 3), 0.0)
            inc_it = self.tbl_do.item(r, 4); include = (inc_it.checkState() == QtCore.Qt.CheckState.Checked) if inc_it else True
            d = DigitalOutCfg(name=name, normallyOpen=bool(normally_open), momentary=False, actuationTime=float(ms))
            setattr(d, "include", bool(include))
            new_dos.append(d)
        while len(new_dos) < MAX_DO:
            d = DigitalOutCfg(name=f"DO{len(new_dos)}")
            setattr(d, "include", True)
            new_dos.append(d)
        cfg.digitalOutputs = new_dos[:MAX_DO]

        # ---- Analog Outputs ----
        ao_rows = min(self.tbl_ao.rowCount(), MAX_AO)
        new_aos: List[AnalogOutCfg] = []
        for r in range(ao_rows):
            ch = self.tbl_ao.cellWidget(r, 0).value() if self.tbl_ao.cellWidget(r, 0) else r
            name = self._safe_text(self.tbl_ao.item(r, 1), f"AO{ch}")
            minv = self._safe_float(self.tbl_ao.item(r, 2), -10.0)
            maxv = self._safe_float(self.tbl_ao.item(r, 3), 10.0)
            st = self._safe_float(self.tbl_ao.item(r, 4), 0.0)
            inc_it = self.tbl_ao.item(r, 5); include = (inc_it.checkState() == QtCore.Qt.CheckState.Checked) if inc_it else True
            a = AnalogOutCfg(name=name, minV=float(minv), maxV=float(maxv), startupV=float(st))
            setattr(a, "include", bool(include))
            new_aos.append(a)
        while len(new_aos) < MAX_AO:
            a = AnalogOutCfg(name=f"AO{len(new_aos)}")
            setattr(a, "include", True)
            new_aos.append(a)
        cfg.analogOutputs = new_aos[:MAX_AO]

        # ---- Thermocouples (pass-through list of dicts) ----
        tc_rows = min(self.tbl_tc.rowCount(), MAX_TC)
        tc_list = []
        for r in range(tc_rows):
            inc_it = self.tbl_tc.item(r, 0)
            include = (inc_it.checkState() == QtCore.Qt.CheckState.Checked) if inc_it else True

            ch = self.tbl_tc.cellWidget(r, 1).value() if self.tbl_tc.cellWidget(r, 1) else r
            name = self._safe_text(self.tbl_tc.item(r, 2), f"TC{ch}")

            typ = "K"
            if isinstance(self.tbl_tc.cellWidget(r, 3), QtWidgets.QComboBox):
                typ = self.tbl_tc.cellWidget(r, 3).currentText().upper()

            offset = self._safe_float(self.tbl_tc.item(r, 4), 0.0)

            tc_list.append(
                {"include": bool(include), "ch": int(ch), "name": name, "type": typ, "offset": float(offset)})
        cfg.thermocouples = tc_list

        return cfg

    # Helpers
    @staticmethod
    def _safe_text(item: QtWidgets.QTableWidgetItem | None, default: str) -> str:
        return item.text() if item and item.text() != "" else default

    @staticmethod
    def _safe_float(item: QtWidgets.QTableWidgetItem | None, default: float) -> float:
        try:
            return float(item.text()) if item and item.text() not in (None, "") else float(default)
        except Exception:
            return float(default)
