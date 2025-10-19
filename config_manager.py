import json
from dataclasses import dataclass, field
from typing import List

@dataclass
class AnalogCfg:
    name: str = "AI"
    slope: float = 1.0
    offset: float = 0.0
    cutoffHz: float = 0.0
    units: str = ""

@dataclass
class DigitalOutCfg:
    name: str = "DO"
    normallyOpen: bool = True
    momentary: bool = False
    actuationTime: float = 0.0

@dataclass
class AnalogOutCfg:
    name: str = "AO"
    minV: float = -10.0
    maxV: float = 10.0
    startupV: float = 0.0

@dataclass
class AppConfig:
    boardNum: int = 0
    sampleRateHz: float = 100.0
    blockSize: int = 64
    analogs: List[AnalogCfg] = field(default_factory=lambda: [AnalogCfg() for _ in range(8)])
    digitalOutputs: List[DigitalOutCfg] = field(default_factory=lambda: [DigitalOutCfg() for _ in range(8)])
    analogOutputs: List[AnalogOutCfg] = field(default_factory=lambda: [AnalogOutCfg() for _ in range(2)])
    aiMode: str = "SE"

class ConfigManager:
    @staticmethod
    def load(path: str) -> 'AppConfig':
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        cfg = AppConfig()
        cfg.boardNum = raw.get("boardNum", raw.get("board", cfg.boardNum))
        cfg.sampleRateHz = raw.get("sampleRateHz", raw.get("sampleRate", cfg.sampleRateHz))
        cfg.blockSize = raw.get("blockSize", cfg.blockSize)
        cfg.aiMode = (raw.get("aiMode") or "SE").upper()
        if "analogs" in raw and isinstance(raw["analogs"], list):
            for i in range(min(8, len(raw["analogs"]))):
                a = raw["analogs"][i] or {}
                cfg.analogs[i] = AnalogCfg(
                    name=a.get("name", f"AI{i}"),
                    slope=float(a.get("slope", 1.0)),
                    offset=float(a.get("offset", 0.0)),
                    cutoffHz=float(a.get("cutoffHz", 0.0)),
                    units=a.get("units", ""),
                )
        else:
            for i in range(8):
                cfg.analogs[i] = AnalogCfg(
                    name=raw.get(f"ai{i}Name", f"AI{i}"),
                    slope=float(raw.get(f"ai{i}Slope", 1.0)),
                    offset=float(raw.get(f"ai{i}Offset", 0.0)),
                    cutoffHz=float(raw.get(f"ai{i}FilterCutoffHz", 0.0)),
                    units=raw.get(f"ai{i}Units", ""),
                )
        if "digitalOutputs" in raw and isinstance(raw["digitalOutputs"], list):
            for i in range(min(8, len(raw["digitalOutputs"]))):
                d = raw["digitalOutputs"][i] or {}
                cfg.digitalOutputs[i] = DigitalOutCfg(
                    name=d.get("name", f"DO{i}"),
                    normallyOpen=bool(d.get("normallyOpen", True)),
                    momentary=bool(d.get("momentary", False)),
                    actuationTime=float(d.get("actuationTime", 0.0)),
                )
        else:
            for i in range(8):
                cfg.digitalOutputs[i] = DigitalOutCfg(
                    name=raw.get(f"do{i}Name", f"DO{i}"),
                    normallyOpen=bool(raw.get(f"do{i}normallyOpen", True)),
                    momentary=bool(raw.get(f"do{i}momentary", False)),
                    actuationTime=float(raw.get(f"do{i}actuationTime", 0.0)),
                )
        if "analogOutputs" in raw and isinstance(raw["analogOutputs"], list):
            for i in range(min(2, len(raw["analogOutputs"]))):
                a = raw["analogOutputs"][i] or {}
                cfg.analogOutputs[i] = AnalogOutCfg(
                    name=a.get("name", f"AO{i}"),
                    minV=float(a.get("minV", -10.0)),
                    maxV=float(a.get("maxV", 10.0)),
                    startupV=float(a.get("startupV", 0.0)),
                )
        else:
            for i in range(2):
                cfg.analogOutputs[i] = AnalogOutCfg(
                    name=raw.get(f"ao{i}Name", f"AO{i}"),
                    minV=float(raw.get(f"ao{i}Min", -10.0)),
                    maxV=float(raw.get(f"ao{i}Max", 10.0)),
                    startupV=float(raw.get(f"ao{i}Default", 0.0)),
                )
        return cfg

    @staticmethod
    def to_dict(cfg: 'AppConfig') -> dict:
        return {
            "boardNum": cfg.boardNum,
            "sampleRateHz": cfg.sampleRateHz,
            "blockSize": cfg.blockSize,
            "aiMode": cfg.aiMode,
            "analogs": [
                {"name": a.name, "slope": a.slope, "offset": a.offset, "cutoffHz": a.cutoffHz, "units": a.units}
                for a in cfg.analogs
            ],
            "digitalOutputs": [
                {"name": d.name, "normallyOpen": d.normallyOpen, "momentary": d.momentary, "actuationTime": d.actuationTime}
                for d in cfg.digitalOutputs
            ],
            "analogOutputs": [
                {"name": a.name, "minV": a.minV, "maxV": a.maxV, "startupV": a.startupV}
                for a in cfg.analogOutputs
            ],
        }

    @staticmethod
    def save(path: str, cfg: 'AppConfig'):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ConfigManager.to_dict(cfg), f, indent=2)
