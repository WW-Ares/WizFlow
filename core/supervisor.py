"""后台图案监控：出现/消失时控制主脚本。"""
from __future__ import annotations

import threading
import time
from typing import Callable

from .capture import make_source
from .log import log
from .screen import TemplateMatcher


class ScriptSupervisor(threading.Thread):
    def __init__(self, config_provider: Callable[[], dict],
                 on_action: Callable[[str, str], None]):
        super().__init__(name="wizflow-supervisor", daemon=True)
        self.config_provider = config_provider
        self.on_action = on_action
        self.matcher = TemplateMatcher()
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.enabled = False
        self.present = False
        self.last_score = 0.0
        self.last_error = ""
        self._source = None
        self._source_key = None
        self._last_fire = 0.0

    def stop(self) -> None:
        self._stop.set()

    def _close_source(self):
        src = self._source
        self._source = None
        self._source_key = None
        if src is not None:
            try:
                src.close()
            except Exception:
                pass

    def _ensure_source(self, cfg):
        key = (cfg.get("capture_mode", "screen"), cfg.get("window_hwnd", 0),
               cfg.get("window_title", ""), cfg.get("window_class", ""),
               cfg.get("window_process", ""), tuple(cfg.get("region") or []))
        if self._source is not None and self._source_key == key:
            return self._source
        self._close_source()
        src = make_source(cfg)
        self._source = src
        self._source_key = key
        return src

    def run(self) -> None:
        while not self._stop.is_set():
            cfg = self.config_provider() or {}
            self.enabled = bool(cfg.get("enabled", False))
            if not self.enabled:
                self.present = False
                self._close_source()
                self._stop.wait(0.25)
                continue
            try:
                region = [int(v) for v in (cfg.get("region") or [0, 0, 0, 0])]
                if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
                    raise ValueError("监控区域未设置")
                template = str(cfg.get("template", "") or "")
                if not template:
                    raise ValueError("未选择监控图案")
                source = self._ensure_source(cfg)
                frame = source.grab(region)
                threshold = float(cfg.get("threshold", 0.85))
                res = self.matcher.find(frame, template, threshold,
                                        bool(cfg.get("multiscale", True)))
                hit = res.score >= threshold
                self.last_score = res.score
                self.last_error = ""
                stable = max(1, int(cfg.get("stable_frames", 2)))
                now = time.time()
                cooldown = max(0, int(cfg.get("cooldown_ms", 500))) / 1000.0
                if hit:
                    self._hit_streak = getattr(self, "_hit_streak", 0) + 1
                    self._miss_streak = 0
                else:
                    self._miss_streak = getattr(self, "_miss_streak", 0) + 1
                    self._hit_streak = 0
                if not self.present and self._hit_streak >= stable:
                    self.present = True
                    if now - self._last_fire >= cooldown:
                        self._last_fire = now
                        self._fire(cfg.get("appear", "none"), cfg)
                elif self.present and self._miss_streak >= stable:
                    self.present = False
                    if now - self._last_fire >= cooldown:
                        self._last_fire = now
                        self._fire(cfg.get("disappear", "none"), cfg)
            except Exception as exc:
                self.last_error = str(exc)
                log("后台监控异常: %s" % exc, "WARN")
                self._close_source()
                self._stop.wait(0.5)
                continue
            self._stop.wait(max(0.02, int(cfg.get("poll_ms", 150)) / 1000.0))
        self._close_source()

    def _fire(self, action: str, cfg: dict):
        action = str(action or "none").lower()
        if action in ("", "none"):
            return
        target = str(cfg.get("run_script", "") or "")
        try:
            self.on_action(action, target)
        except Exception as exc:
            log("后台监控动作失败: %s" % exc, "WARN")

    def status(self) -> dict:
        return {"enabled": self.enabled, "present": self.present,
                "score": self.last_score, "error": self.last_error}
