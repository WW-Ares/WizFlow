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
APP_VERSION = "1.4.2"


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
    "script_supervisor": {
        "enabled": False,
        "template": "",
        "threshold": 0.85,
        "poll_ms": 150,
        "stable_frames": 2,
        "cooldown_ms": 500,
        "appear": "none",
        "disappear": "none",
        "run_script": "",
        "region": [0, 0, 0, 0],
        "capture_mode": "screen",
        "window_title": "",
        "window_class": "",
        "window_process": "",
        "window_hwnd": 0,
    },
    "background_rules": [],
    "background_migrated": False,
    "background_group": {"mode": "parallel", "grace_ms": 600, "on_none_present": []},
    "monitor_schemes": [],
    "active_scheme": "",
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


def _migrate_background(data: dict) -> None:
    """把 1.4.0 的 script_supervisor 配置迁成一条后台规则。"""
    if data.get("background_migrated"):
        return
    data["background_migrated"] = True
    if data.get("background_rules"):
        return
    sup = data.get("script_supervisor") or {}
    if not (sup.get("enabled") or sup.get("template") or sup.get("region")):
        return

    def act(op: str, script: str = "") -> dict:
        if op in ("pause", "resume", "stop"):
            return {"type": "script", "op": op, "target": "current"}
        if op == "run":
            return {"type": "script", "op": "run", "target": "script",
                    "script": script, "busy": "switch"}
        return {}

    on_hit = act(str(sup.get("appear", "none")))
    on_miss = act(str(sup.get("disappear", "none")))
    run_script = str(sup.get("run_script", "") or "")
    rule = {
        "id": "rule_legacy",
        "name": "后台监控（迁移）",
        "enabled": bool(sup.get("enabled", False)),
        "priority": 50,
        "template": str(sup.get("template", "") or ""),
        "threshold": float(sup.get("threshold", 0.85) or 0.85),
        "region": list(sup.get("region") or [0, 0, 0, 0]),
        "capture_mode": str(sup.get("capture_mode", "screen") or "screen"),
        "window_title": str(sup.get("window_title", "") or ""),
        "window_class": str(sup.get("window_class", "") or ""),
        "window_process": str(sup.get("window_process", "") or ""),
        "window_hwnd": int(sup.get("window_hwnd", 0) or 0),
        "poll_ms": int(sup.get("poll_ms", 150) or 150),
        "stable_frames": int(sup.get("stable_frames", 2) or 2),
        "cooldown_ms": int(sup.get("cooldown_ms", 500) or 500),
        "multiscale": True,
        "on_hit": [on_hit] if on_hit else [],
        "on_miss": [on_miss] if on_miss else [],
    }
    data["background_rules"] = [rule]


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
                    _migrate_background(self.data)
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
