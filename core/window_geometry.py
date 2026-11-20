"""窗口位置计算：主屏工作区、显示器矩形与可见性判断。"""
from __future__ import annotations

import ctypes


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def primary_work_area() -> tuple[int, int, int, int]:
    """主显示器工作区（不含任务栏），失败时退回 1920×1080。"""
    r = _RECT()
    try:
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
        return int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top)
    except Exception:
        return 0, 0, 1920, 1080


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong),
                ("rcMonitor", _RECT),
                ("rcWork", _RECT),
                ("dwFlags", ctypes.c_ulong)]


def work_area_at(x: int, y: int) -> tuple[int, int, int, int]:
    """指定坐标所在显示器的工作区（不含任务栏）；失败时退回主屏工作区。"""
    try:
        u32 = ctypes.windll.user32
        u32.MonitorFromPoint.restype = ctypes.c_void_p
        u32.MonitorFromPoint.argtypes = [_POINT, ctypes.c_ulong]
        u32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MONITORINFO)]
        mon = u32.MonitorFromPoint(_POINT(int(x), int(y)), 2)  # MONITOR_DEFAULTTONEAREST
        if mon:
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if u32.GetMonitorInfoW(mon, ctypes.byref(info)):
                r = info.rcWork
                return (int(r.left), int(r.top),
                        int(r.right - r.left), int(r.bottom - r.top))
    except Exception:
        pass
    return primary_work_area()


def monitor_rects() -> list[tuple[int, int, int, int]]:
    """所有显示器的 (left, top, width, height)；失败时返回空列表。"""
    try:
        import mss
        with mss.mss() as sct:
            return [(int(m["left"]), int(m["top"]), int(m["width"]), int(m["height"]))
                    for m in sct.monitors[1:]]
    except Exception:
        return []


def rect_visible_enough(x: int, y: int, w: int, h: int,
                        rects: list[tuple[int, int, int, int]] | None = None,
                        min_ratio: float = 0.4) -> bool:
    """窗口与任一显示器的交集面积是否达到阈值。"""
    rects = monitor_rects() if rects is None else rects
    if not rects:
        return True
    for mx, my, mw, mh in rects:
        ix = max(0, min(x + w, mx + mw) - max(x, mx))
        iy = max(0, min(y + h, my + mh) - max(y, my))
        if ix * iy >= min_ratio * w * h:
            return True
    return False


def center_on_primary(w: int, h: int) -> tuple[int, int]:
    """主显示器工作区内居中坐标。"""
    px, py, pw, ph = primary_work_area()
    return px + max(0, (pw - w) // 2), py + max(0, (ph - h) // 2)
