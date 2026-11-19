"""区域监控：配置、单次探测、实时轮询。"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import cv2

from .capture import make_source
from .log import log
from .screen import TemplateMatcher


@dataclass
class MonitorProfile:
    name: str = "监控1"
    region: list = field(default_factory=lambda: [0, 0, 0, 0])
    capture_mode: str = "screen"
    window_title: str = ""
    window_class: str = ""
    window_process: str = ""
    window_hwnd: int = 0
    template: str = ""
    threshold: float = 0.85
    poll_ms: int = 150
    timeout_ms: int = 8000
    multiscale: bool = True
    hit_action: str = "mouse"
    key_combo: str = "1"
    key_hold_ms: int = 40
    click_style: str = "single"
    click_mode: str = "center"
    wheel_dir: str = "up"
    wheel_amount: int = 3
    wheel_interval_ms: int = 60
    fixed_x: int = 0
    fixed_y: int = 0
    click_sync: bool = True
    click_button: str = "left"
    offset_x: int = 0
    offset_y: int = 0
    random_x: int = 0
    random_y: int = 0
    hold_ms: int = 40
    run_script: str = ""
    script_anchor: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "MonitorProfile":
        d = d or {}
        button = str(d.get("click_button", "left"))
        style = str(d.get("click_style", "single"))
        # v1.1.2 的 wheel 写法合并为 滚轮键 + 上/下滚动
        if style == "wheel":
            style = "wheel_up" if str(d.get("wheel_dir", "up")).lower() != "down" else "wheel_down"
            button = "middle"
        elif style in ("wheel_up", "wheel_down"):
            button = "middle"
        return cls(
            name=str(d.get("name", "监控1")),
            region=[int(v) for v in (d.get("region") or [0, 0, 0, 0])][:4],
            capture_mode=str(d.get("capture_mode", "screen")),
            window_title=str(d.get("window_title", "") or ""),
            window_class=str(d.get("window_class", "") or ""),
            window_process=str(d.get("window_process", "") or ""),
            window_hwnd=int(d.get("window_hwnd", 0) or 0),
            template=str(d.get("template", "") or ""),
            threshold=float(d.get("threshold", 0.85)),
            poll_ms=max(20, int(d.get("poll_ms", 150))),
            timeout_ms=max(0, int(d.get("timeout_ms", 8000))),
            multiscale=bool(d.get("multiscale", True)),
            hit_action=str(d.get("hit_action", "mouse")),
            key_combo=str(d.get("key_combo", "1")),
            key_hold_ms=max(1, int(d.get("key_hold_ms", 40))),
            click_style=style,
            click_mode=str(d.get("click_mode", "center")),
            wheel_dir=str(d.get("wheel_dir", "up")),
            wheel_amount=max(1, int(d.get("wheel_amount", 3))),
            wheel_interval_ms=max(0, int(d.get("wheel_interval_ms", 60))),
            fixed_x=int(d.get("fixed_x", 0)),
            fixed_y=int(d.get("fixed_y", 0)),
            click_sync=bool(d.get("click_sync", True)),
            click_button=button,
            offset_x=int(d.get("offset_x", 0)),
            offset_y=int(d.get("offset_y", 0)),
            random_x=int(d.get("random_x", 0)),
            random_y=int(d.get("random_y", 0)),
            hold_ms=max(1, int(d.get("hold_ms", 40))),
            run_script=str(d.get("run_script", "") or ""),
            script_anchor=[int(v) for v in (d.get("script_anchor") or [])][:2],
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name, "region": list(self.region), "template": self.template,
            "capture_mode": self.capture_mode, "window_title": self.window_title,
            "window_class": self.window_class, "window_process": self.window_process,
            "window_hwnd": self.window_hwnd,
            "threshold": self.threshold, "poll_ms": self.poll_ms,
            "timeout_ms": self.timeout_ms, "multiscale": self.multiscale,
            "hit_action": self.hit_action, "key_combo": self.key_combo,
            "key_hold_ms": self.key_hold_ms, "click_style": self.click_style,
            "click_mode": self.click_mode,
            "wheel_dir": self.wheel_dir, "wheel_amount": self.wheel_amount,
            "wheel_interval_ms": self.wheel_interval_ms,
            "fixed_x": self.fixed_x, "fixed_y": self.fixed_y,
            "click_sync": self.click_sync, "click_button": self.click_button,
            "offset_x": self.offset_x, "offset_y": self.offset_y,
            "random_x": self.random_x, "random_y": self.random_y, "hold_ms": self.hold_ms,
            "run_script": self.run_script, "script_anchor": list(self.script_anchor),
        }

    def to_event(self) -> dict:
        ev = self.to_dict()
        ev["type"] = "monitor"
        ev["delay"] = 0
        return ev

    def valid(self) -> tuple[bool, str]:
        if len(self.region) != 4 or self.region[2] <= 0 or self.region[3] <= 0:
            return False, "请先框选监控区域"
        if not self.template:
            return False, "请先抓取/选择图标模板"
        return True, ""


@dataclass
class ProbeResult:
    score: float = 0.0
    matched: bool = False
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    frame: object = None
    cost_ms: float = 0.0
    origin: tuple = (0, 0)

    @property
    def center(self) -> tuple[int, int]:
        x, y, w, h = self.rect
        return x + w // 2, y + h // 2


def probe(matcher: TemplateMatcher, profile: MonitorProfile, source=None) -> ProbeResult:
    ok, _err = profile.valid()
    if not ok:
        return ProbeResult()
    own = source is None
    if own:
        source = make_source(profile.to_dict())
    try:
        t0 = time.perf_counter()
        origin = source.origin
        frame = source.grab(profile.region)
        res = matcher.find(frame, profile.template, profile.threshold, profile.multiscale)
        res.x += int(origin[0]) + int(profile.region[0])
        res.y += int(origin[1]) + int(profile.region[1])
        return ProbeResult(
            score=res.score,
            matched=res.score >= profile.threshold,
            rect=res.rect,
            frame=frame,
            cost_ms=(time.perf_counter() - t0) * 1000.0,
            origin=origin,
        )
    finally:
        if own:
            source.close()


def crop_match(pr: ProbeResult, profile: MonitorProfile):
    """从抓取帧里裁出命中区域，坐标转换为帧内相对坐标。"""
    if pr.frame is None:
        return None
    x, y, w, h = pr.rect
    rx = x - int(pr.origin[0]) - int(profile.region[0])
    ry = y - int(pr.origin[1]) - int(profile.region[1])
    hh, ww = pr.frame.shape[:2]
    x0, y0 = max(0, rx), max(0, ry)
    x1, y1 = min(ww, rx + w), min(hh, ry + h)
    if x1 <= x0 or y1 <= y0:
        return None
    return pr.frame[y0:y1, x0:x1].copy()


class LiveMonitor(threading.Thread):
    """实时监控线程；结果通过 callback 抛出（非 UI 线程，UI 需用队列转接）。"""

    def __init__(self, matcher: TemplateMatcher, profile: MonitorProfile,
                 callback: Callable[[ProbeResult], None],
                 stop_event: threading.Event | None = None) -> None:
        super().__init__(name="kmg-live-monitor", daemon=True)
        self.matcher = matcher
        self.profile = profile
        self.callback = callback
        self._stop = stop_event or threading.Event()

    def run(self) -> None:
        source = None
        try:
            while not self._stop.is_set():
                try:
                    if source is None:
                        source = make_source(self.profile.to_dict())
                    pr = probe(self.matcher, self.profile, source)
                    self.callback(pr)
                except Exception as exc:
                    log("实时监控异常: %s" % exc, "WARN")
                    try:
                        if source is not None:
                            source.close()
                    except Exception:
                        pass
                    source = None
                self._stop.wait(max(0.02, self.profile.poll_ms / 1000.0))
        finally:
            if source is not None:
                source.close()

    def stop(self) -> None:
        self._stop.set()


def annotate(frame, profile: MonitorProfile, pr: ProbeResult):
    """在预览帧上画命中框。"""
    if frame is None:
        return None
    img = frame.copy()
    x, y, w, h = pr.rect
    rx = x - int(pr.origin[0]) - int(profile.region[0])
    ry = y - int(pr.origin[1]) - int(profile.region[1])
    color = (80, 220, 80) if pr.matched else (60, 180, 250)
    cv2.rectangle(img, (rx, ry), (rx + w, ry + h), color, 2)
    cv2.putText(img, "%.3f" % pr.score, (max(2, rx), max(16, ry - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
    return img

