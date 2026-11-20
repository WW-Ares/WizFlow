"""屏幕 / 窗口抓取源。

- screen: 现有 mss 桌面抓取
- window: Windows Graphics Capture（WGC），可抓被遮挡窗口
- window_gdi: PrintWindow(PW_RENDERFULLCONTENT) 兜底

窗口模式下 rect 一律是“窗口客户区相对坐标”，origin 提供客户区左上角的屏幕坐标。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import threading
import time
from dataclasses import dataclass

import numpy as np

from .log import log
from . import screen as desktop

try:
    from windows_capture import WindowsCapture as _WindowsCapture
except Exception:
    _WindowsCapture = None

u32 = ctypes.WinDLL("user32", use_last_error=True)
g32 = ctypes.WinDLL("gdi32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0

u32.EnumWindows.restype = wt.BOOL
u32.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
u32.IsWindow.argtypes = [wt.HWND]
u32.IsWindow.restype = wt.BOOL
u32.IsWindowVisible.argtypes = [wt.HWND]
u32.IsWindowVisible.restype = wt.BOOL
u32.IsIconic.argtypes = [wt.HWND]
u32.IsIconic.restype = wt.BOOL
u32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
u32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
try:
    dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
    dwm.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
except Exception:
    dwm = None
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                           ctypes.POINTER(wt.DWORD)]
k32.CloseHandle.argtypes = [wt.HANDLE]


class CaptureError(Exception):
    pass


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    pid: int
    process: str
    x: int
    y: int
    width: int
    height: int

    @property
    def label(self) -> str:
        return "%s  [%s]" % (self.title or "(无标题)", self.process or self.class_name)


def _process_name(pid: int) -> str:
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
    except Exception:
        pass
    finally:
        k32.CloseHandle(h)
    return ""


def window_outer_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    if not hwnd or not u32.IsWindow(hwnd):
        return None
    r = wt.RECT()
    if not u32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top)


def extended_frame_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    if dwm is not None:
        r = wt.RECT()
        try:
            if dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) == 0:
                w, h = int(r.right - r.left), int(r.bottom - r.top)
                if w > 0 and h > 0:
                    return int(r.left), int(r.top), w, h
        except Exception:
            pass
    return window_outer_rect(hwnd)


def client_rect_screen(hwnd: int) -> tuple[int, int, int, int] | None:
    if not hwnd or not u32.IsWindow(hwnd):
        return None
    r = wt.RECT()
    if not u32.GetClientRect(hwnd, ctypes.byref(r)):
        return None
    pt = wt.POINT(0, 0)
    if not u32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    w, h = int(r.right - r.left), int(r.bottom - r.top)
    if w <= 0 or h <= 0:
        return None
    return int(pt.x), int(pt.y), w, h


def list_windows(exclude_pid: int = 0) -> list[WindowInfo]:
    out: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _l):
        try:
            if not u32.IsWindowVisible(hwnd):
                return True
            n = u32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            title = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, title, n + 1)
            cls = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(hwnd, cls, 256)
            pid = wt.DWORD(0)
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if exclude_pid and int(pid.value) == int(exclude_pid):
                return True
            rect = client_rect_screen(hwnd)
            if rect is None:
                return True
            out.append(WindowInfo(int(hwnd), title.value, cls.value, int(pid.value),
                                  _process_name(int(pid.value)), *rect))
        except Exception:
            pass
        return True

    u32.EnumWindows(cb, 0)
    out.sort(key=lambda w: (w.process.lower(), w.title.lower()))
    return out


def window_is_fullscreen(hwnd: int) -> bool:
    """判断窗口是否是无边框全屏（独占全屏的典型形态）。"""
    if not hwnd:
        return False
    try:
        rect = wt.RECT()
        if not u32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect)):
            return False
        style = u32.GetWindowLongW(wt.HWND(hwnd), -16) & 0xFFFFFFFF
        if not (style & 0x80000000):
            return False
        if style & 0x00C00000:
            return False
        import mss
        with mss.mss() as sct:
            for mon in sct.monitors[1:]:
                if (abs(rect.left - mon["left"]) <= 2
                        and abs(rect.top - mon["top"]) <= 2
                        and abs((rect.right - rect.left) - mon["width"]) <= 2
                        and abs((rect.bottom - rect.top) - mon["height"]) <= 2):
                    return True
    except Exception:
        pass
    return False


def find_window(hwnd: int = 0, title: str = "", class_name: str = "",
                process: str = "") -> int:
    if hwnd and u32.IsWindow(hwnd):
        return int(hwnd)
    title_l = (title or "").lower()
    class_l = (class_name or "").lower()
    proc_l = (process or "").lower()
    best = 0
    best_score = -1
    for w in list_windows():
        if class_l and w.class_name.lower() != class_l:
            continue
        if proc_l and proc_l not in w.process.lower():
            continue
        score = 0
        if title_l and title_l == w.title.lower():
            score += 4
        elif title_l and title_l in w.title.lower():
            score += 2
        if class_l:
            score += 1
        if proc_l:
            score += 1
        if score > best_score:
            best, best_score = w.hwnd, score
    return best

class CaptureSource:
    mode = "screen"

    @property
    def origin(self) -> tuple[int, int]:
        return 0, 0

    def grab(self, rect) -> np.ndarray:
        raise NotImplementedError

    def close(self) -> None:
        pass

    @property
    def alive(self) -> bool:
        return True


class DesktopSource(CaptureSource):
    mode = "screen"

    def grab(self, rect) -> np.ndarray:
        return desktop.grab(rect)


class WindowWGCSource(CaptureSource):
    mode = "window"

    def __init__(self, hwnd: int, fps: int = 30):
        self._last_ts = 0.0
        if _WindowsCapture is None:
            raise CaptureError("windows-capture 未安装或加载失败")
        self.hwnd = int(hwnd)
        self._frame: np.ndarray | None = None
        self._lock = threading.RLock()
        self._closed = False
        self._cap = _WindowsCapture(window_hwnd=self.hwnd, cursor_capture=False,
                                   draw_border=False,
                                   minimum_update_interval=max(16, int(1000 / max(1, fps))))

        @self._cap.event
        def on_frame_arrived(frame, control):
            try:
                self._last_ts = time.time()
                arr = frame.frame_buffer[:, :, :3]
                with self._lock:
                    self._frame = np.ascontiguousarray(arr).copy()
            except Exception as exc:
                log("WGC 帧处理失败: %s" % exc, "WARN")

        @self._cap.event
        def on_closed():
            self._closed = True

        self._control = self._cap.start_free_threaded()

    @property
    def origin(self) -> tuple[int, int]:
        r = client_rect_screen(self.hwnd)
        return (r[0], r[1]) if r else (0, 0)

    def _client_offset(self) -> tuple[int, int]:
        c = client_rect_screen(self.hwnd)
        f = extended_frame_rect(self.hwnd)
        if c and f:
            return int(c[0] - f[0]), int(c[1] - f[1])
        return 0, 0

    @property
    def alive(self) -> bool:
        return bool(u32.IsWindow(self.hwnd)) and not self._closed

    def grab(self, rect) -> np.ndarray:
        with self._lock:
            frame = self._frame
        if u32.IsIconic(self.hwnd):
            raise CaptureError("窗口已最小化，无法抓取画面")
        if frame is not None and time.time() - self._last_ts > 3.0:
            raise CaptureError("窗口画面已停止更新（可能已最小化或被系统挂起）")
        if frame is None:
            deadline = time.time() + 1.5
            while time.time() < deadline:
                time.sleep(0.02)
                with self._lock:
                    frame = self._frame
                if frame is not None:
                    break
        if frame is None:
            raise CaptureError("窗口暂无画面（可能已最小化或捕获被系统阻止）")
        x, y, w, h = [int(v) for v in rect]
        ox, oy = self._client_offset()
        x += ox
        y += oy
        fh, fw = frame.shape[:2]
        x = max(0, min(x, fw - 1))
        y = max(0, min(y, fh - 1))
        w = max(1, min(w, fw - x))
        h = max(1, min(h, fh - y))
        return frame[y:y + h, x:x + w].copy()

    def close(self) -> None:
        try:
            self._control.stop()
        except Exception:
            pass
        try:
            self._control.wait()
        except Exception:
            pass


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class WindowGDISource(CaptureSource):
    mode = "window_gdi"

    def __init__(self, hwnd: int):
        self.hwnd = int(hwnd)

    @property
    def origin(self) -> tuple[int, int]:
        r = client_rect_screen(self.hwnd)
        return (r[0], r[1]) if r else (0, 0)

    @property
    def alive(self) -> bool:
        return bool(u32.IsWindow(self.hwnd))

    def _capture_client(self) -> np.ndarray:
        if u32.IsIconic(self.hwnd):
            raise CaptureError("窗口已最小化，无法抓取画面")
        crect = client_rect_screen(self.hwnd)
        orect = window_outer_rect(self.hwnd)
        if crect is None or orect is None:
            raise CaptureError("窗口客户区无效（窗口可能已关闭或最小化）")
        cx, cy, cw, ch = crect
        ox, oy, ow, oh = orect
        if ow <= 0 or oh <= 0:
            raise CaptureError("窗口尺寸无效")
        hdc = u32.GetDC(self.hwnd)
        if not hdc:
            raise CaptureError("GetDC 失败")
        mem = g32.CreateCompatibleDC(hdc)
        bmp = g32.CreateCompatibleBitmap(hdc, ow, oh)
        old = g32.SelectObject(mem, bmp)
        ok = u32.PrintWindow(self.hwnd, mem, PW_RENDERFULLCONTENT)
        if not ok:
            g32.BitBlt(mem, 0, 0, ow, oh, hdc, 0, 0, SRCCOPY)
        bi = _BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = ow
        bi.bmiHeader.biHeight = -oh
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = BI_RGB
        buf = ctypes.create_string_buffer(ow * oh * 4)
        g32.GetDIBits(mem, bmp, 0, oh, buf, ctypes.byref(bi), DIB_RGB_COLORS)
        g32.SelectObject(mem, old)
        g32.DeleteObject(bmp)
        g32.DeleteDC(mem)
        u32.ReleaseDC(self.hwnd, hdc)
        arr = np.frombuffer(buf.raw, dtype=np.uint8).reshape(oh, ow, 4)
        offx, offy = cx - ox, cy - oy
        sub = arr[offy:offy + ch, offx:offx + cw, :3]
        if sub.shape[0] != ch or sub.shape[1] != cw:
            raise CaptureError("PrintWindow 画面尺寸不匹配")
        return sub.copy()

    def grab(self, rect) -> np.ndarray:
        frame = self._capture_client()
        x, y, w, h = [int(v) for v in rect]
        fh, fw = frame.shape[:2]
        x = max(0, min(x, fw - 1))
        y = max(0, min(y, fh - 1))
        w = max(1, min(w, fw - x))
        h = max(1, min(h, fh - y))
        return frame[y:y + h, x:x + w].copy()

    def close(self) -> None:
        pass


def make_source(cfg: dict) -> CaptureSource:
    """根据配置创建抓取源；窗口源失败时自动降级 GDI。"""
    cfg = cfg or {}
    mode = str(cfg.get("capture_mode", "screen")).lower()
    if mode == "screen":
        return DesktopSource()
    hwnd = find_window(int(cfg.get("window_hwnd", 0) or 0),
                       str(cfg.get("window_title", "") or ""),
                       str(cfg.get("window_class", "") or ""),
                       str(cfg.get("window_process", "") or ""))
    if not hwnd:
        raise CaptureError("未找到绑定窗口，请重新选择窗口")
    if mode == "window_gdi":
        return WindowGDISource(hwnd)
    try:
        return WindowWGCSource(hwnd)
    except Exception as exc:
        log("WGC 启动失败，降级 GDI: %s" % exc, "WARN")
        return WindowGDISource(hwnd)
