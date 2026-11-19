"""低级键盘钩子：录制按键时吞掉系统快捷键。

普通 Tk <KeyPress> 收不到 Alt+Tab、Win+D 这类组合，因为系统先处理了。
这里用 WH_KEYBOARD_LL 在系统之前拦截，返回 1 阻止系统动作，同时把按键
放进队列交给 UI 线程处理。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import queue
import threading

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012

MOD_VKS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C}
NAMED = {
    0x08: "backspace", 0x09: "tab", 0x0D: "enter", 0x13: "pause", 0x14: "capslock",
    0x1B: "esc", 0x20: "space", 0x21: "pageup", 0x22: "pagedown", 0x23: "end",
    0x24: "home", 0x25: "left", 0x26: "up", 0x27: "right", 0x28: "down",
    0x2C: "printscreen", 0x2D: "insert", 0x2E: "delete", 0x5B: "win", 0x5C: "win",
    0x5D: "apps", 0x90: "numlock", 0x91: "scrolllock",
}


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


def vk_name(vk: int) -> str:
    if vk in NAMED:
        return NAMED[vk]
    if 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return "f%d" % (vk - 0x6F)
    if 0x60 <= vk <= 0x69:
        return "num%d" % (vk - 0x60)
    return "vk_%d" % vk


def combo_text(down: set[int], vk: int) -> str:
    parts = []
    if down & {0x11, 0xA2, 0xA3}:
        parts.append("ctrl")
    if down & {0x12, 0xA4, 0xA5}:
        parts.append("alt")
    if down & {0x10, 0xA0, 0xA1}:
        parts.append("shift")
    if down & {0x5B, 0x5C}:
        parts.append("win")
    parts.append(vk_name(vk))
    return "+".join(parts)


class KeyGrabber:
    """start() 后所有键盘事件被吞掉，事件放进 queue。"""

    def __init__(self) -> None:
        self.queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook = None
        self._active = False
        self._ready = threading.Event()
        self._error = ""
        self._down: set[int] = set()
        self._proc_ref = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def error(self) -> str:
        return self._error

    def start(self, timeout: float = 1.0) -> bool:
        if self._active:
            return True
        self._error = ""
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="wizflow-keygrab", daemon=True)
        self._thread.start()
        ok = self._ready.wait(timeout)
        return bool(ok and self._active)

    def _run(self) -> None:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u32.SetWindowsHookExW.restype = ctypes.c_void_p
        u32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p,
                                          ctypes.c_void_p, wt.DWORD]
        u32.CallNextHookEx.restype = ctypes.c_ssize_t
        u32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                       wt.WPARAM, wt.LPARAM]
        u32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        u32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
        u32.GetMessageW.restype = wt.BOOL
        k32.GetModuleHandleW.restype = ctypes.c_void_p
        k32.GetCurrentThreadId.restype = wt.DWORD

        self._thread_id = int(k32.GetCurrentThreadId())
        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)

        def proc(n_code, w_param, l_param):
            if n_code == 0 and self._active:
                try:
                    info = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                    vk = int(info.vkCode)
                    msg = int(w_param)
                    if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        self._down.add(vk)
                        self.queue.put(("down", vk, combo_text(self._down, vk)))
                    elif msg in (WM_KEYUP, WM_SYSKEYUP):
                        self.queue.put(("up", vk, combo_text(self._down, vk)))
                        self._down.discard(vk)
                except Exception:
                    pass
                # 吞掉所有键盘事件，系统收不到 Alt+Tab / Win 等
                return 1
            return u32.CallNextHookEx(None, n_code, w_param, l_param)

        self._proc_ref = HOOKPROC(proc)
        module = k32.GetModuleHandleW(None)
        self._hook = u32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc_ref, module, 0)
        if not self._hook:
            self._error = "键盘钩子安装失败（错误码 %d）" % ctypes.get_last_error()
            self._ready.set()
            return

        self._active = True
        self._ready.set()
        msg = wt.MSG()
        try:
            while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u32.TranslateMessage(ctypes.byref(msg))
                u32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._active = False
            try:
                u32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None
            self._proc_ref = None

    def stop(self) -> None:
        self._active = False
        if self._thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=1.0)
        self._thread = None
        self._thread_id = 0
        self._down.clear()
        self.queue = queue.Queue()

    def drain(self) -> list[tuple]:
        out = []
        while True:
            try:
                out.append(self.queue.get_nowait())
            except queue.Empty:
                break
        return out
