"""路径与配置。配置放在程序目录，打包后也能直接改。"""
from __future__ import annotations

import copy
import json
import os
import sys
import threading
from pathlib import Path


APP_NAME = "WizFlow"
APP_ID = "WizFlow.Desktop"
APP_VERSION = "1.2.0"


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = app_dir()
CONFIG_FILE = ROOT / "config.json"
SCRIPTS_DIR = ROOT / "scripts"
ICONS_DIR = ROOT / "icons"
LOGS_DIR = ROOT / "logs"
ASSETS_DIR = ROOT / "assets"
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", str(ROOT)))


def asset_path(name: str) -> Path:
    """打包后资源在 _MEIPASS/assets；源码运行在项目 assets。"""
    bundled = BUNDLE_DIR / "assets" / name
    if bundled.exists():
        return bundled
    return ROOT / "assets" / name

for _d in (SCRIPTS_DIR, ICONS_DIR, LOGS_DIR, ASSETS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


DEFAULTS = {
    "hotkeys": {"start_pause": "F6", "stop": "F7", "record": "F8"},
    "record_trajectory": False,
    "trajectory_interval_ms": 16,
    "loop_count": 1,
    "speed": 1.0,
    "vdisplay": False,
    "vdisplay_parsec": False,
    "hud": {"enabled": True, "x": 0, "y": 0, "saved": False,
            "alpha": 1.0, "click_through": False},
    "keep_awake": True,
    "last_script": "",
    "multi_monitor": {
        "name": "多图标监控",
        "region": [0, 0, 0, 0],
        "capture_mode": "screen",
        "window_title": "",
        "window_class": "",
        "window_process": "",
        "window_hwnd": 0,
        "poll_ms": 150,
        "match_mode": "any",
        "max_actions_per_tick": 1,
        "run_mode": "continuous",
        "timeout_ms": 0,
        "stop_on_match": False,
        "rules": [],
        "links": [],
    },
    "monitor": {
        "name": "监控1",
        "region": [0, 0, 0, 0],
        "capture_mode": "screen",
        "window_title": "",
        "window_class": "",
        "window_process": "",
        "window_hwnd": 0,
        "template": "",
        "threshold": 0.85,
        "poll_ms": 150,
        "timeout_ms": 8000,
        "multiscale": True,
        "click_sync": True,
        "click_button": "left",
        "offset_x": 0,
        "offset_y": 0,
        "hold_ms": 40,
        "wheel_dir": "up",
        "wheel_amount": 3,
        "wheel_interval_ms": 60,
        "run_script": "",
        "script_anchor": [],
    },
    "ui_scale": 1.15,
    "window": {"w": 1180, "h": 790},
}


def _deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path: Path = CONFIG_FILE) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> dict:
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8-sig") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    self.data = _deep_merge(DEFAULTS, raw)
        except Exception:
            # 配置损坏时用默认值继续跑，不阻断启动；原文件保留
            self.data = copy.deepcopy(DEFAULTS)
        return self.data

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    def get(self, dotted: str, default=None):
        cur = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def set(self, dotted: str, value) -> None:
        with self._lock:
            cur = self.data
            parts = dotted.split(".")
            for part in parts[:-1]:
                nxt = cur.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    cur[part] = nxt
                cur = nxt
            cur[parts[-1]] = value

    def update(self, patch: dict) -> None:
        with self._lock:
            self.data = _deep_merge(self.data, patch)
