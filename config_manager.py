import json
from dataclasses import dataclass, field
from typing import List, Dict, Any

# ---------- Channel configs ----------
@dataclass
class AnalogCfg:
    name: str = "AI"
    slope: float = 1.0
    offset: float = 0.0
    cutoffHz: float = 0.0
    units: str = ""
    include: bool = True

@dataclass
class DigitalOutCfg:
    name: str = "DO"
    normallyOpen: bool = True
    momentary: bool = False
    actuationTime: float = 0.0
    include: bool = True

@dataclass
class AnalogOutCfg:
    name: str = "AO"
    minV: float = -10.0
    maxV: float = 10.0
    startupV: float = 0.0
    include: bool = True

# ---------- Boards ----------
@dataclass
class Board1608Cfg:
    boardNum: int = 0
    sampleRateHz: float = 100.0
    blockSize: int = 64
    aiMode: str = "SE"  # "SE" or "DIFF"

@dataclass
class BoardETCCfg:
    boardNum: int = 0
    sampleRateHz: float = 10.0
    blockSize: int = 128

# ---------- App config ----------
@dataclass
class AppConfig:
    b1608: Board1608Cfg = field(default_factory=Board1608Cfg)
    betc: BoardETCCfg = field(default_factory=BoardETCCfg)
    analogs: List[AnalogCfg] = field(default_factory=lambda: [AnalogCfg() for _ in range(8)])
    digitalOutputs: List[DigitalOutCfg] = field(default_factory=lambda: [DigitalOutCfg() for _ in range(8)])
    analogOutputs: List[AnalogOutCfg] = field(default_factory=lambda: [AnalogOutCfg() for _ in range(2)])
    thermocouples: List[Dict[str, Any]] = field(default_factory=list)

    # ---- Back-compat properties for existing code ----
    @property
    def boardNum(self) -> int:
        return self.b1608.boardNum
    @boardNum.setter
    def boardNum(self, v: int): self.b1608.boardNum = int(v)

    @property
    def sampleRateHz(self) -> float:
        return self.b1608.sampleRateHz
    @sampleRateHz.setter
    def sampleRateHz(self, v: float): self.b1608.sampleRateHz = float(v)

    @property
    def blockSize(self) -> int:
        return self.b1608.blockSize
    @blockSize.setter
    def blockSize(self, v: int): self.b1608.blockSize = int(v)

    @property
    def aiMode(self) -> str:
        return self.b1608.aiMode
    @aiMode.setter
    def aiMode(self, v: str): self.b1608.aiMode = (v or "SE").upper()

    # Original dict-ish .etc for any legacy reads
    @property
    def etc(self) -> Dict[str, Any]:
        return {"board": self.betc.boardNum, "sample_rate_hz": self.betc.sampleRateHz, "block_size": self.betc.blockSize}
    @etc.setter
    def etc(self, d: Dict[str, Any]):
        if not d: return
        self.betc.boardNum = int(d.get("board", self.betc.boardNum))
        self.betc.sampleRateHz = float(d.get("sample_rate_hz", self.betc.sampleRateHz))
        self.betc.blockSize = int(d.get("block_size", self.betc.blockSize))

class ConfigManager:
    @staticmethod
    def load(path: str) -> 'AppConfig':
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        cfg = AppConfig()

        # ----- Boards -----
        b1608 = raw.get("board1608", {})
        cfg.b1608.boardNum     = int(b1608.get("boardNum",  raw.get("boardNum",  raw.get("board", cfg.b1608.boardNum))))
        cfg.b1608.sampleRateHz = float(b1608.get("sampleRateHz", raw.get("sampleRateHz", raw.get("sampleRate", cfg.b1608.sampleRateHz))))
        cfg.b1608.blockSize    = int(b1608.get("blockSize", raw.get("blockSize", cfg.b1608.blockSize)))
        cfg.b1608.aiMode       = (b1608.get("aiMode", raw.get("aiMode", cfg.b1608.aiMode)) or "SE").upper()

        betc = raw.get("boardetc", {})
        cfg.betc.boardNum      = int(betc.get("boardNum", cfg.betc.boardNum))
        cfg.betc.sampleRateHz  = float(betc.get("sampleRateHz", cfg.betc.sampleRateHz))
        cfg.betc.blockSize     = int(betc.get("blockSize", cfg.betc.blockSize))

        # ----- Analogs -----
        if isinstance(raw.get("analogs"), list):
            for i in range(min(8, len(raw["analogs"]))):
                a = raw["analogs"][i] or {}
                cfg.analogs[i] = AnalogCfg(
                    name=a.get("name", f"AI{i}"),
                    slope=float(a.get("slope", 1.0)),
                    offset=float(a.get("offset", 0.0)),
                    cutoffHz=float(a.get("cutoffHz", 0.0)),
                    units=a.get("units", ""),
                    include=bool(a.get("include", True)),
                )
        # ----- Digital outputs -----
        if isinstance(raw.get("digitalOutputs"), list):
            for i in range(min(8, len(raw["digitalOutputs"]))):
                d = raw["digitalOutputs"][i] or {}
                cfg.digitalOutputs[i] = DigitalOutCfg(
                    name=d.get("name", f"DO{i}"),
                    normallyOpen=bool(d.get("normallyOpen", True)),
                    momentary=bool(d.get("momentary", False)),
                    actuationTime=float(d.get("actuationTime", 0.0)),
                    include=bool(d.get("include", True)),
                )
        # ----- Analog outputs -----
        if isinstance(raw.get("analogOutputs"), list):
            for i in range(min(2, len(raw["analogOutputs"]))):
                a = raw["analogOutputs"][i] or {}
                cfg.analogOutputs[i] = AnalogOutCfg(
                    name=a.get("name", f"AO{i}"),
                    minV=float(a.get("minV", -10.0)),
                    maxV=float(a.get("maxV", 10.0)),
                    startupV=float(a.get("startupV", 0.0)),
                    include=bool(a.get("include", True)),
                )

        # ----- Thermocouples -----
        if isinstance(raw.get("thermocouples"), list):
            cfg.thermocouples = list(raw["thermocouples"])

        return cfg

    @staticmethod
    def to_dict(cfg: 'AppConfig'):
        return {
            "board1608": {
                "boardNum": cfg.b1608.boardNum,
                "sampleRateHz": cfg.b1608.sampleRateHz,
                "blockSize": cfg.b1608.blockSize,
                "aiMode": cfg.b1608.aiMode,
            },
            "boardetc": {
                "boardNum": cfg.betc.boardNum,
                "sampleRateHz": cfg.betc.sampleRateHz,
                "blockSize": cfg.betc.blockSize,
            },
            "analogs": [
                {"name": a.name, "slope": a.slope, "offset": a.offset, "cutoffHz": a.cutoffHz, "units": a.units, "include": a.include}
                for a in cfg.analogs
            ],
            "digitalOutputs": [
                {"name": d.name, "normallyOpen": d.normallyOpen, "momentary": d.momentary, "actuationTime": d.actuationTime, "include": d.include}
                for d in cfg.digitalOutputs
            ],
            "analogOutputs": [
                {"name": a.name, "minV": a.minV, "maxV": a.maxV, "startupV": a.startupV, "include": a.include}
                for a in cfg.analogOutputs
            ],
            "thermocouples": cfg.thermocouples,
        }

    @staticmethod
    def save(path: str, cfg: 'AppConfig'):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ConfigManager.to_dict(cfg), f, indent=2)
