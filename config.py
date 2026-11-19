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
APP_VERSION = "1.8.2"


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
    "monitor_v2_migrated": False,
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


def _profile_actions(p: dict) -> list[dict]:
    hit = str(p.get("hit_action", "mouse"))
    style = str(p.get("click_style", "single"))
    if hit == "key":
        return [{"type": "key", "keys": str(p.get("key_combo", "1") or "1"),
                 "hold_ms": int(p.get("key_hold_ms", 40) or 40)}]
    if hit == "script":
        return [{"type": "script", "op": "run", "target": "script",
                 "path": str(p.get("run_script", "") or ""),
                 "anchor": list(p.get("script_anchor") or []), "wait": True}]
    if style in ("wheel_up", "wheel_down"):
        return [{"type": "wheel",
                 "direction": "up" if style == "wheel_up" else "down",
                 "amount": int(p.get("wheel_amount", 3) or 3),
                 "interval_ms": int(p.get("wheel_interval_ms", 60) or 60),
                 "position": "center"}]
    mode = str(p.get("click_mode", "center"))
    pos = "center" if mode == "center" else ("follow" if mode == "follow" else "absolute")
    act = {"type": "click", "button": str(p.get("click_button", "left")),
           "clicks": 2 if style == "double" else 1,
           "hold_ms": int(p.get("hold_ms", 40) or 40),
           "position": pos,
           "offset": [int(p.get("offset_x", 0) or 0), int(p.get("offset_y", 0) or 0)],
           "random_x": abs(int(p.get("random_x", 0) or 0)),
           "random_y": abs(int(p.get("random_y", 0) or 0))}
    if pos == "absolute":
        act["point"] = [int(p.get("fixed_x", 0) or 0), int(p.get("fixed_y", 0) or 0)]
    return [act]


def _migrate_monitor_v2(data: dict) -> None:
    """把 monitor_profile / background_rules 合并进 multi_monitor。"""
    if data.get("monitor_v2_migrated"):
        return
    data["monitor_v2_migrated"] = True
    profile = data.get("monitor") or {}
    group = data.get("multi_monitor") or {}
    rules = [r for r in (group.get("rules") or []) if isinstance(r, dict)]
    links = [l for l in (group.get("links") or []) if isinstance(l, dict)]
    bg = [r for r in (data.get("background_rules") or []) if isinstance(r, dict)]

    have_tpl = {(r.get("template"), tuple(r.get("roi") or [])) for r in rules}
    if profile.get("template") and (profile.get("template"), tuple()) not in have_tpl:
        rules.insert(0, {
            "id": "rule_profile",
            "name": profile.get("name") or "主规则",
            "enabled": True,
            "resident": False,
            "priority": 50,
            "template": profile.get("template", ""),
            "template_rect": [],
            "roi": [],
            "threshold": float(profile.get("threshold", 0.85) or 0.85),
            "multiscale": bool(profile.get("multiscale", True)),
            "cooldown_ms": 500,
            "stable_frames": 1,
            "require_absence": True,
            "max_triggers": 0,
            "group": "",
            "actions": _profile_actions(profile),
            "miss_actions": [],
        })

    existing_ids = {str(r.get("id")) for r in rules}
    for i, r in enumerate(bg):
        rid = str(r.get("id") or ("bg%d" % (i + 1)))
        if rid in existing_ids:
            rid = rid + "_bg"
        existing_ids.add(rid)
        rules.append({
            "id": rid,
            "name": str(r.get("name", "后台规则")),
            "enabled": bool(r.get("enabled", True)),
            "resident": True,
            "priority": int(r.get("priority", 50) or 50),
            "template": str(r.get("template", "") or ""),
            "template_rect": [],
            "roi": [],
            "threshold": float(r.get("threshold", 0.85) or 0.85),
            "multiscale": bool(r.get("multiscale", True)),
            "cooldown_ms": int(r.get("cooldown_ms", 500) or 500),
            "stable_frames": int(r.get("stable_frames", 1) or 1),
            "require_absence": True,
            "max_triggers": 0,
            "group": "",
            "actions": [dict(a) for a in (r.get("on_hit") or []) if isinstance(a, dict)],
            "miss_actions": [dict(a) for a in (r.get("on_miss") or []) if isinstance(a, dict)],
        })

    region = list(group.get("region") or [0, 0, 0, 0])
    if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
        region = list(profile.get("region") or [0, 0, 0, 0])
    if (len(region) != 4 or region[2] <= 0 or region[3] <= 0) and rules:
        region = list(rules[0].get("region") or [0, 0, 0, 0])
    group.update({
        "region": region,
        "capture_mode": group.get("capture_mode") or profile.get("capture_mode", "screen"),
        "window_title": group.get("window_title") or profile.get("window_title", ""),
        "window_class": group.get("window_class") or profile.get("window_class", ""),
        "window_process": group.get("window_process") or profile.get("window_process", ""),
        "window_hwnd": int(group.get("window_hwnd") or profile.get("window_hwnd", 0) or 0),
        "poll_ms": int(group.get("poll_ms") or profile.get("poll_ms", 150) or 150),
        "rules": rules,
        "links": links,
    })
    data["multi_monitor"] = group

    for scheme in data.get("monitor_schemes") or []:
        if not isinstance(scheme, dict) or scheme.get("_v2"):
            continue
        sp = scheme.get("profile") or {}
        sg = scheme.get("group") or {}
        srules = [r for r in (sg.get("rules") or []) if isinstance(r, dict)]
        if not srules and sp.get("template"):
            srules = [{
                "id": "rule_profile",
                "name": sp.get("name") or "主规则",
                "enabled": True,
                "resident": False,
                "priority": 50,
                "template": sp.get("template", ""),
                "template_rect": [],
                "roi": [],
                "threshold": float(sp.get("threshold", 0.85) or 0.85),
                "multiscale": bool(sp.get("multiscale", True)),
                "cooldown_ms": 500,
                "stable_frames": 1,
                "require_absence": True,
                "max_triggers": 0,
                "group": "",
                "poll_ms": 0,
                "actions": _profile_actions(sp),
                "miss_actions": [],
            }]
        sregion = list(sg.get("region") or [0, 0, 0, 0])
        if len(sregion) != 4 or sregion[2] <= 0 or sregion[3] <= 0:
            sregion = list(sp.get("region") or [0, 0, 0, 0])
        sg.update({
            "region": sregion,
            "capture_mode": sg.get("capture_mode") or sp.get("capture_mode", "screen"),
            "window_title": sg.get("window_title") or sp.get("window_title", ""),
            "window_class": sg.get("window_class") or sp.get("window_class", ""),
            "window_process": sg.get("window_process") or sp.get("window_process", ""),
            "window_hwnd": int(sg.get("window_hwnd") or sp.get("window_hwnd", 0) or 0),
            "poll_ms": int(sg.get("poll_ms") or sp.get("poll_ms", 150) or 150),
            "rules": srules,
            "links": [l for l in (sg.get("links") or []) if isinstance(l, dict)],
        })
        scheme["group"] = sg
        scheme["_v2"] = True


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
                    _migrate_monitor_v2(self.data)
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
