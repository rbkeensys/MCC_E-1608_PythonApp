from PyQt6 import QtCore, QtWidgets

class ScriptEditorDialog(QtWidgets.QDialog):
    def __init__(self, parent, events: list[dict] | None):
        super().__init__(parent)
        self.setWindowTitle("Edit Script (time + DO0..DO7)")
        self.resize(900, 500)
        layout = QtWidgets.QVBoxLayout(self)

        toolbar = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton("Add Row")
        self.btn_del = QtWidgets.QPushButton("Delete Selected")
        self.btn_sort = QtWidgets.QPushButton("Sort by Time")
        toolbar.addWidget(self.btn_add); toolbar.addWidget(self.btn_del); toolbar.addWidget(self.btn_sort); toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.table = QtWidgets.QTableWidget(0, 9, self)
        self.table.setHorizontalHeaderLabels(["Time (s)","DO0","DO1","DO2","DO3","DO4","DO5","DO6","DO7"])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject); layout.addWidget(btns)

        self.btn_add.clicked.connect(self._on_add); self.btn_del.clicked.connect(self._on_del); self.btn_sort.clicked.connect(self._on_sort)

        if events:
            for ev in events:
                self._append_row(ev.get("time", 0.0), ev.get("relays", [False]*8))

    def _append_row(self, t: float, relays: list[bool]):
        r = self.table.rowCount(); self.table.insertRow(r)
        item_t = QtWidgets.QTableWidgetItem(str(float(t)))
        item_t.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(r, 0, item_t)
        for c in range(8):
            it = QtWidgets.QTableWidgetItem(); it.setFlags(it.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.CheckState.Checked if (relays[c] if c < len(relays) else False) else QtCore.Qt.CheckState.Unchecked)
            self.table.setItem(r, 1+c, it)

    def _on_add(self): self._append_row(0.0, [False]*8)
    def _on_del(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows: self.table.removeRow(r)
    def _on_sort(self): self.table.sortItems(0, QtCore.Qt.SortOrder.AscendingOrder)

    def result_events(self) -> list[dict]:
        evs = []
        for r in range(self.table.rowCount()):
            t = float(self.table.item(r, 0).text()) if self.table.item(r, 0) else 0.0
            rel = []
            for c in range(8):
                it = self.table.item(r, 1+c)
                rel.append(it.checkState() == QtCore.Qt.CheckState.Checked if it else False)
            evs.append({"time": t, "relays": rel})
        return evs
