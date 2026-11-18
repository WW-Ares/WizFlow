"""内置会话保活：不依赖任何第三方驱动或软件。

做两件事：
1. SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
   阻止系统空闲睡眠、关闭显示器。
2. 空闲超过阈值时，用 1px 鼠标相对移动 + 回移刷新系统输入空闲计时，
   防止屏保/锁屏；用户正在操作时不动鼠标。

这相当于“虚拟屏/远程会话保活”，纯 Windows API，无需 Parsec VDD。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
import time

from .log import log
from .winapi import mouse_move_rel, set_awake

JITTER_INTERVAL = 30.0      # 每 30 秒检查一次
IDLE_TRIGGER_MS = 15000     # 空闲 15 秒才微动，避免干扰正在操作的用户


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def idle_ms() -> int:
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0
        now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return int((now - info.dwTime) & 0xFFFFFFFF)
    except Exception:
        return 0


class _SessionKeepAlive:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self.enabled = False
        self.nudges = 0
        self.last_error = ""
        self._last_nudge = 0.0

    def _nudge(self) -> None:
        try:
            # 相对移动 1px 再回来，产生一次真实输入，刷新空闲计时
            mouse_move_rel(1, 0)
            time.sleep(0.03)
            mouse_move_rel(-1, 0)
            self.nudges += 1
            self._last_nudge = time.time()
        except Exception as exc:
            self.last_error = str(exc)

    def _loop(self, stop: threading.Event) -> None:
        # 必须在这个长期存活的线程里设置，ES_CONTINUOUS 才会持续生效
        set_awake(True)
        try:
            while not stop.is_set():
                set_awake(True)
                if self.enabled and idle_ms() >= IDLE_TRIGGER_MS:
                    self._nudge()
                stop.wait(1.0)
        finally:
            set_awake(False)

    def enable(self) -> str:
        with self._lock:
            self.enabled = True
            self.last_error = ""
            if self._stop is None:
                self._stop = threading.Event()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, args=(self._stop,),
                                                name="wizflow-keepalive", daemon=True)
                self._thread.start()
            log("内置会话保活已开启（睡眠/熄屏阻止 + 空闲输入微动）")
            return ""

    def disable(self) -> None:
        with self._lock:
            self.enabled = False
            stop = self._stop
            if stop is not None:
                stop.set()
            thread = self._thread
            self._thread = None
            self._stop = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        set_awake(False)
        log("内置会话保活已关闭")

    def status(self) -> dict:
        return {
            "enabled": bool(self.enabled),
            "mode": "integrated",
            "awake": bool(self.enabled),
            "nudges": int(self.nudges),
            "idle_ms": idle_ms(),
            "error": self.last_error,
        }


_ctrl = _SessionKeepAlive()


class KeepAlive:
    """兼容原 UI 调用：enable / disable / status。"""

    @property
    def enabled(self) -> bool:
        return bool(_ctrl.enabled)

    def enable(self) -> str:
        return _ctrl.enable()

    def disable(self) -> None:
        _ctrl.disable()

    def status(self) -> dict:
        return _ctrl.status()


keepalive = KeepAlive()


def ensure_on() -> str:
    return keepalive.enable()


def ensure_off() -> None:
    keepalive.disable()


def status() -> dict:
    return keepalive.status()
