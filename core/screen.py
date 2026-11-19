"""屏幕抓取 + 图标模板匹配。

坐标一律是虚拟屏幕绝对坐标（物理像素）。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import mss
import numpy as np

from .winapi import virtual_screen_rect

_tls = threading.local()


def _sct() -> "mss.mss":
    s = getattr(_tls, "sct", None)
    if s is None:
        s = mss.mss()
        _tls.sct = s
    return s


def grab(rect) -> np.ndarray:
    """抓取 (x, y, w, h)，返回 BGR ndarray。"""
    x, y, w, h = [int(v) for v in rect]
    if w <= 0 or h <= 0:
        raise ValueError("抓取区域宽高必须大于 0")
    mon = {"left": x, "top": y, "width": w, "height": h}
    raw = _sct().grab(mon)
    arr = np.asarray(raw, dtype=np.uint8)
    return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)


def grab_full() -> np.ndarray:
    return grab(virtual_screen_rect())


def save_png(img: np.ndarray, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("PNG 编码失败")
    buf.tofile(str(path))


def load_image(path: str | Path) -> np.ndarray | None:
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        if data.size == 0:
            return None
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        return img
    except Exception:
        return None


def to_ctk_image(img: np.ndarray):
    """BGR -> PIL Image（供 CustomTkinter 显示）。"""
    from PIL import Image

    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)
    return pil


@dataclass
class MatchResult:
    score: float = 0.0
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    scale: float = 1.0

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.w, self.h


class TemplateMatcher:
    """带缩放的灰度模板匹配。模板很小、区域不大时速度足够。"""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, np.ndarray]] = {}
        self._lock = threading.Lock()

    def _template(self, path: str) -> np.ndarray | None:
        p = Path(path)
        if not p.exists():
            return None
        mtime = p.stat().st_mtime
        key = str(p.resolve())
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] == mtime:
                return hit[1]
        img = load_image(p)
        if img is None:
            return None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        with self._lock:
            self._cache[key] = (mtime, gray)
        return gray

    def find(self, frame_bgr: np.ndarray, template_path: str,
             threshold: float = 0.85, multiscale: bool = True,
             scale_range=(0.55, 1.45), scale_step: float = 0.1) -> MatchResult:
        tpl = self._template(template_path)
        if tpl is None or frame_bgr is None or frame_bgr.size == 0:
            return MatchResult()
        gray = frame_bgr if frame_bgr.ndim == 2 else cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        gh, gw = gray.shape[:2]
        th, tw = tpl.shape[:2]
        if th < 4 or tw < 4 or th > gh or tw > gw:
            return MatchResult()

        best = MatchResult()
        scales = [1.0]
        if multiscale:
            lo, hi = scale_range
            n = max(1, int(round((hi - lo) / max(0.01, scale_step))))
            scales = sorted({round(lo + i * scale_step, 3) for i in range(n + 1)} | {1.0})

        for sc in scales:
            if sc == 1.0:
                cur = tpl
            else:
                nw, nh = int(round(tw * sc)), int(round(th * sc))
                if nw < 4 or nh < 4 or nw > gw or nh > gh:
                    continue
                cur = cv2.resize(tpl, (nw, nh), interpolation=cv2.INTER_AREA)
            if cur.shape[0] > gh or cur.shape[1] > gw:
                continue
            if float(cur.std()) < 1e-3:
                # 纯色模板在 TM_CCOEFF_NORMED 下分母为 0，会恒等 1.0，改用平方差
                res = cv2.matchTemplate(gray, cur, cv2.TM_SQDIFF_NORMED)
                mn, _mx, minloc, _maxloc = cv2.minMaxLoc(res)
                mx = max(0.0, 1.0 - float(mn))
                maxloc = minloc
            else:
                res = cv2.matchTemplate(gray, cur, cv2.TM_CCOEFF_NORMED)
                _min, mx, _minloc, maxloc = cv2.minMaxLoc(res)
            if not np.isfinite(mx):
                continue
            if mx > best.score:
                best = MatchResult(float(mx), int(maxloc[0]), int(maxloc[1]),
                                   int(cur.shape[1]), int(cur.shape[0]), float(sc))
            if best.score >= 0.995:
                break
        return best


def offset_rect(rect, dx: int, dy: int):
    x, y, w, h = [int(v) for v in rect]
    return x + int(dx), y + int(dy), w, h


def wait_for_match(matcher: TemplateMatcher, region, template: str, threshold: float,
                   poll_ms: int, timeout_ms: int, stop_event: threading.Event,
                   multiscale: bool = True, on_probe=None) -> tuple[MatchResult, np.ndarray | None]:
    """阻塞轮询直到命中或超时；返回 (结果, 命中帧)。超时返回 score < threshold 的最佳结果。"""
    t0 = time.perf_counter()
    best = MatchResult()
    best_frame = None
    while not stop_event.is_set():
        frame = grab(region)
        res = matcher.find(frame, template, threshold, multiscale)
        res.x += int(region[0])
        res.y += int(region[1])
        if on_probe:
            on_probe(res)
        if res.score > best.score:
            best, best_frame = res, frame
        if res.score >= threshold:
            return res, frame
        if timeout_ms > 0 and (time.perf_counter() - t0) * 1000 >= timeout_ms:
            break
        stop_event.wait(max(0.01, poll_ms / 1000.0))
    return best, best_frame


