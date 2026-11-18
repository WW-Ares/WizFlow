"""全局热键（RegisterHotKey + 消息循环线程）。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
from typing import Callable

from .log import log

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

_NAMED = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "pause": 0x13,
    "capslock": 0x14, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "insert": 0x2D, "delete": 0x2E,
    "win": 0x5B, "apps": 0x5D, "numlock": 0x90, "scrolllock": 0x91,
    "volumeup": 0xAF, "volumedown": 0xAE, "volumemute": 0xAD,
}


def parse_hotkey(text: str) -> tuple[int, int] | None:
    text = (text or "").strip().lower().replace(" ", "")
    if not text:
        return None
    mods = 0
    vk = None
    for part in text.split("+"):
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part in ("win", "meta"):
            mods |= MOD_WIN
        elif part in _NAMED:
            vk = _NAMED[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x6F + int(part[1:])
        elif part.startswith("vk_") and part[3:].isdigit():
            vk = int(part[3:])
    if vk is None:
        return None
    return mods, vk


class HotkeyManager(threading.Thread):
    def __init__(self, bindings: dict[str, Callable[[], None]],
                 on_error: Callable[[str], None] | None = None) -> None:
        super().__init__(name="kmg-hotkeys", daemon=True)
        self.bindings = bindings
        self.on_error = on_error or (lambda _m: None)
        self._stop = threading.Event()
        self._thread_id = 0
        self._ready = threading.Event()
        self._registered: list[int] = []
        self._binding_by_id: dict[int, Callable[[], None]] = {}

    def run(self) -> None:
        self._thread_id = int(kernel32.GetCurrentThreadId())
        msg = wt.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        hid = 1
        for name, cb in self.bindings.items():
            parsed = parse_hotkey(name)
            if not parsed:
                self.on_error("无效热键: %s" % name)
                continue
            mods, vk = parsed
            if not user32.RegisterHotKey(None, hid, mods, vk):
                self.on_error("热键注册失败: %s（可能被其他程序占用）" % name)
            else:
                self._registered.append(hid)
                self._binding_by_id[hid] = cb
            hid += 1
        self._ready.set()
        while not self._stop.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            if msg.message == WM_HOTKEY:
                cb = self._binding_by_id.get(int(msg.wParam))
                if cb:
                    try:
                        cb()
                    except Exception as exc:
                        log("热键回调异常: %s" % exc, "ERROR")
        for hid in self._registered:
            try:
                user32.UnregisterHotKey(None, hid)
            except Exception:
                pass
        self._registered.clear()

    def stop(self) -> None:
        self._stop.set()
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
