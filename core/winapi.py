"""Windows 底层封装：DPI、虚拟屏幕、权限、鼠标键盘注入。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

import win32api
import win32con

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def set_dpi_aware() -> None:
    """按物理像素取坐标，必须在创建任何窗口前调用。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def virtual_screen_rect() -> tuple[int, int, int, int]:
    """所有显示器合并后的矩形 (x, y, w, h)，可能含负坐标。"""
    x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    if w <= 0 or h <= 0:
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        x = y = 0
    return int(x), int(y), int(w), int(h)


def primary_size() -> tuple[int, int]:
    return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


def cursor_pos() -> tuple[int, int]:
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def set_cursor_pos(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))


# ---------------------------------------------------------------- 鼠标/键盘
_MOUSE_FLAGS = {
    "left": (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP),
    "right": (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP),
    "middle": (win32con.MOUSEEVENTF_MIDDLEDOWN, win32con.MOUSEEVENTF_MIDDLEUP),
    "x1": (0x00000080, 0x00000100),
    "x2": (0x00000080, 0x00000100),
}


def mouse_down(button: str = "left") -> None:
    button = (button or "left").lower()
    down, _up = _MOUSE_FLAGS.get(button, _MOUSE_FLAGS["left"])
    if button in ("x1", "x2"):
        data = 1 if button == "x1" else 2
        win32api.mouse_event(down, 0, 0, data, 0)
    else:
        win32api.mouse_event(down, 0, 0, 0, 0)


def mouse_up(button: str = "left") -> None:
    button = (button or "left").lower()
    _down, up = _MOUSE_FLAGS.get(button, _MOUSE_FLAGS["left"])
    if button in ("x1", "x2"):
        data = 1 if button == "x1" else 2
        win32api.mouse_event(up, 0, 0, data, 0)
    else:
        win32api.mouse_event(up, 0, 0, 0, 0)


def mouse_wheel(delta: int) -> None:
    win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, int(delta), 0)


def mouse_move_rel(dx: int, dy: int) -> None:
    """相对移动鼠标；用于保活时产生一次真实输入。"""
    win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, int(dx), int(dy), 0, 0)


def click(x: int, y: int, button: str = "left", hold_ms: int = 40) -> None:
    set_cursor_pos(x, y)
    mouse_down(button)
    time.sleep(max(1, hold_ms) / 1000.0)
    mouse_up(button)


# 扩展键：这些虚拟键在 keybd_event 时需要 KEYEVENTF_EXTENDEDKEY
EXTENDED_VKS = {
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,  # PgUp PgDn End Home arrows
    0x2D, 0x2E,  # Insert Delete
    0x6F,  # numpad /
    0x90,  # NumLock
    0xA3,  # Right Ctrl
    0xA5,  # Right Alt
    0x5B, 0x5C,  # Win
    0xAD, 0xAE, 0xAF,  # Volume
}


def key_down(vk: int, extended: bool = False) -> None:
    flags = win32con.KEYEVENTF_EXTENDEDKEY if extended else 0
    win32api.keybd_event(int(vk), 0, flags, 0)


def key_up(vk: int, extended: bool = False) -> None:
    flags = win32con.KEYEVENTF_KEYUP | (win32con.KEYEVENTF_EXTENDEDKEY if extended else 0)
    win32api.keybd_event(int(vk), 0, flags, 0)


def key_press(vk: int, extended: bool = False, hold_ms: int = 40) -> None:
    key_down(vk, extended)
    time.sleep(max(1, hold_ms) / 1000.0)
    key_up(vk, extended)


# ---------------------------------------------------------------- 系统唤醒锁
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def set_awake(on: bool) -> None:
    """阻止系统睡眠/熄屏；关闭时恢复默认。"""
    flags = ES_CONTINUOUS
    if on:
        flags |= ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    try:
        kernel32.SetThreadExecutionState(ctypes.c_uint(flags))
    except Exception:
        pass
