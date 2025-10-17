
from PyQt6 import QtCore, QtWidgets
from config_manager import AppConfig, AnalogCfg, DigitalOutCfg, AnalogOutCfg

class ConfigEditorDialog(QtWidgets.QDialog):
    def __init__(self, parent, cfg: AppConfig):
        super().__init__(parent)
        self.setWindowTitle("Edit Config")
        self._cfg = cfg
        self.resize(900, 600)
        layout = QtWidgets.QVBoxLayout(self)

        tabs = QtWidgets.QTabWidget()
        layout.addWidget(tabs)

        # ---- General ----
        gen = QtWidgets.QWidget()
        gform = QtWidgets.QFormLayout(gen)
        self.sp_board = QtWidgets.QSpinBox(); self.sp_board.setRange(0, 31); self.sp_board.setValue(cfg.boardNum)
        self.sp_rate = QtWidgets.QDoubleSpinBox(); self.sp_rate.setRange(0.1, 200000.0); self.sp_rate.setDecimals(3); self.sp_rate.setValue(cfg.sampleRateHz)
        self.sp_block = QtWidgets.QSpinBox(); self.sp_block.setRange(1, 65536); self.sp_block.setValue(cfg.blockSize)
        gform.addRow("Board #", self.sp_board)
        gform.addRow("Sample Rate (Hz)", self.sp_rate)
        gform.addRow("Block Size", self.sp_block)
        tabs.addTab(gen, "General")

        # ---- Analog Inputs ----
        aiw = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(aiw)
        self.tbl_ai = QtWidgets.QTableWidget(8, 5)
        self.tbl_ai.setHorizontalHeaderLabels(["Name", "Slope", "Offset", "Cutoff Hz", "Units"])
        self.tbl_ai.horizontalHeader().setStretchLastSection(True)
        for i, a in enumerate(cfg.analogs):
            self.tbl_ai.setItem(i, 0, QtWidgets.QTableWidgetItem(a.name))
            for col, val in enumerate([a.slope, a.offset, a.cutoffHz], start=1):
                item = QtWidgets.QTableWidgetItem(str(val))
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
                self.tbl_ai.setItem(i, col, item)
            self.tbl_ai.setItem(i, 4, QtWidgets.QTableWidgetItem(a.units or ""))
        vbox.addWidget(self.tbl_ai)
        tabs.addTab(aiw, "Analog Inputs")

        # ---- Digital Outputs ----
        dow = QtWidgets.QWidget()
        vbox2 = QtWidgets.QVBoxLayout(dow)
        self.tbl_do = QtWidgets.QTableWidget(8, 4)
        self.tbl_do.setHorizontalHeaderLabels(["Name", "Normally Open", "Momentary", "Actuation Time (s)"])
        self.tbl_do.horizontalHeader().setStretchLastSection(True)
        for i, d in enumerate(cfg.digitalOutputs):
            self.tbl_do.setItem(i, 0, QtWidgets.QTableWidgetItem(d.name))
            chk_no = QtWidgets.QTableWidgetItem(); chk_no.setFlags(chk_no.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable); chk_no.setCheckState(QtCore.Qt.CheckState.Checked if d.normallyOpen else QtCore.Qt.CheckState.Unchecked)
            chk_mo = QtWidgets.QTableWidgetItem(); chk_mo.setFlags(chk_mo.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable); chk_mo.setCheckState(QtCore.Qt.CheckState.Checked if d.momentary else QtCore.Qt.CheckState.Unchecked)
            self.tbl_do.setItem(i, 1, chk_no)
            self.tbl_do.setItem(i, 2, chk_mo)
            item_time = QtWidgets.QTableWidgetItem(str(d.actuationTime)); item_time.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
            self.tbl_do.setItem(i, 3, item_time)
        vbox2.addWidget(self.tbl_do)
        tabs.addTab(dow, "Digital Outputs")

        # ---- Analog Outputs ----
        aow = QtWidgets.QWidget()
        vbox3 = QtWidgets.QVBoxLayout(aow)
        self.tbl_ao = QtWidgets.QTableWidget(2, 4)
        self.tbl_ao.setHorizontalHeaderLabels(["Name", "Min V", "Max V", "Startup V"])
        self.tbl_ao.horizontalHeader().setStretchLastSection(True)
        for i, a in enumerate(cfg.analogOutputs):
            self.tbl_ao.setItem(i, 0, QtWidgets.QTableWidgetItem(a.name))
            for col, val in enumerate([a.minV, a.maxV, a.startupV], start=1):
                item = QtWidgets.QTableWidgetItem(str(val))
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
                self.tbl_ao.setItem(i, col, item)
        vbox3.addWidget(self.tbl_ao)
        tabs.addTab(aow, "Analog Outputs")

        # Buttons
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def updated_config(self) -> AppConfig:
        cfg = AppConfig()
        cfg.boardNum = int(self.sp_board.value())
        cfg.sampleRateHz = float(self.sp_rate.value())
        cfg.blockSize = int(self.sp_block.value())

        # AI
        for i in range(8):
            name = self.tbl_ai.item(i, 0).text() if self.tbl_ai.item(i, 0) else f"AI{i}"
            slope = float(self.tbl_ai.item(i, 1).text()) if self.tbl_ai.item(i, 1) else 1.0
            offset = float(self.tbl_ai.item(i, 2).text()) if self.tbl_ai.item(i, 2) else 0.0
            cutoff = float(self.tbl_ai.item(i, 3).text()) if self.tbl_ai.item(i, 3) else 0.0
            units = self.tbl_ai.item(i, 4).text() if self.tbl_ai.item(i, 4) else ""
            cfg.analogs[i] = AnalogCfg(name=name, slope=slope, offset=offset, cutoffHz=cutoff, units=units)

        # DO
        for i in range(8):
            name = self.tbl_do.item(i, 0).text() if self.tbl_do.item(i, 0) else f"DO{i}"
            no = self.tbl_do.item(i, 1).checkState() == QtCore.Qt.CheckState.Checked
            mo = self.tbl_do.item(i, 2).checkState() == QtCore.Qt.CheckState.Checked
            t = float(self.tbl_do.item(i, 3).text()) if self.tbl_do.item(i, 3) else 0.0
            cfg.digitalOutputs[i] = DigitalOutCfg(name=name, normallyOpen=no, momentary=mo, actuationTime=t)

        # AO
        for i in range(2):
            name = self.tbl_ao.item(i, 0).text() if self.tbl_ao.item(i, 0) else f"AO{i}"
            minv = float(self.tbl_ao.item(i, 1).text()) if self.tbl_ao.item(i, 1) else -10.0
            maxv = float(self.tbl_ao.item(i, 2).text()) if self.tbl_ao.item(i, 2) else 10.0
            st = float(self.tbl_ao.item(i, 3).text()) if self.tbl_ao.item(i, 3) else 0.0
            cfg.analogOutputs[i] = AnalogOutCfg(name=name, minV=minv, maxV=maxv, startupV=st)

        return cfg
