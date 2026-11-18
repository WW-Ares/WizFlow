"""键鼠录制：pynput 全局钩子 -> 事件字典。

录制轨迹开关打开时记录 mouse move（按间隔采样）；关闭时只记录点击/滚轮/键盘。
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from pynput import keyboard, mouse

from .log import log
from .winapi import EXTENDED_VKS, cursor_pos

_KEY_ALIASES = {
    "ctrl_l": "ctrl", "ctrl_r": "ctrl_r", "shift_l": "shift", "shift_r": "shift_r",
    "alt_l": "alt", "alt_r": "alt_r", "cmd": "win", "cmd_l": "win", "cmd_r": "win_r",
    "page_up": "pageup", "page_down": "pagedown", "caps_lock": "capslock",
    "num_lock": "numlock", "scroll_lock": "scrolllock", "print_screen": "printscreen",
    "menu": "apps",
}


def _key_info(key) -> tuple[int, str, int]:
    vk = 0
    name = ""
    try:
        if isinstance(key, keyboard.KeyCode):
            vk = int(getattr(key, "vk", 0) or 0)
            name = key.char or ""
        else:
            name = getattr(key, "name", "") or str(key).replace("Key.", "")
            val = getattr(key, "value", None)
            if val is not None:
                vk = int(getattr(val, "vk", 0) or 0)
    except Exception:
        pass
    name = _KEY_ALIASES.get(name, name)
    if not name:
        name = "vk_%d" % vk
    extended = 1 if vk in EXTENDED_VKS else 0
    return vk, name, extended


def _button_name(button) -> str:
    name = getattr(button, "name", str(button))
    name = name.replace("Button.", "").lower()
    return name if name in ("left", "right", "middle", "x1", "x2") else "left"


class Recorder:
    def __init__(self, on_event: Callable[[dict], None]) -> None:
        self.on_event = on_event
        self.record_trajectory = False
        self.move_interval_ms = 16
        self.ignore_vks: set[int] = set()
        self._mouse_listener = None
        self._kb_listener = None
        self._lock = threading.Lock()
        self._last_time = 0.0
        self._last_move = (0, 0)
        self._last_move_time = 0.0
        self.recording = False
        self.started_at = 0.0
        self.count = 0

    # ------------------------------------------------------------ lifecycle
    def start(self, record_trajectory: bool = False, move_interval_ms: int = 16) -> None:
        if self.recording:
            return
        self.record_trajectory = bool(record_trajectory)
        self.move_interval_ms = max(1, int(move_interval_ms))
        self._last_time = time.perf_counter()
        self._last_move = cursor_pos()
        self._last_move_time = self._last_time
        self.started_at = time.time()
        self.count = 0
        self.recording = True

        self._mouse_listener = mouse.Listener(
            on_move=self._on_move, on_click=self._on_click, on_scroll=self._on_scroll)
        self._kb_listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release)
        self._mouse_listener.start()
        self._kb_listener.start()
        log("录制开始 trajectory=%s interval=%dms" % (self.record_trajectory, self.move_interval_ms))

    def stop(self) -> None:
        if not self.recording:
            return
        self.recording = False
        for listener in (self._mouse_listener, self._kb_listener):
            try:
                if listener:
                    listener.stop()
            except Exception:
                pass
        self._mouse_listener = None
        self._kb_listener = None
        log("录制结束 events=%d" % self.count)

    # ------------------------------------------------------------ emit
    def _emit(self, event_type: str, action_type: str, action, delay_ms: float | None = None) -> None:
        now = time.perf_counter()
        if delay_ms is None:
            delay_ms = (now - self._last_time) * 1000.0
        self._last_time = now
        ev = {
            "type": "event",
            "event_type": event_type,
            "delay": round(max(0.0, delay_ms), 1),
            "action_type": action_type,
            "action": action,
        }
        self.count += 1
        try:
            self.on_event(ev)
        except Exception as exc:
            log("录制回调异常: %s" % exc, "ERROR")

    # ------------------------------------------------------------ mouse
    def _on_move(self, x, y) -> None:
        if not self.recording or not self.record_trajectory:
            return
        now = time.perf_counter()
        if (now - self._last_move_time) * 1000.0 < self.move_interval_ms:
            return
        lx, ly = self._last_move
        if abs(int(x) - lx) < 1 and abs(int(y) - ly) < 1:
            return
        self._last_move = (int(x), int(y))
        self._last_move_time = now
        # 轨迹点也参与延时序列
        self._emit("EM", "mouse move", [int(x), int(y)])

    def _on_click(self, x, y, button, pressed) -> None:
        if not self.recording:
            return
        name = _button_name(button)
        action = "mouse %s %s" % (name, "down" if pressed else "up")
        self._emit("EM", action, [int(x), int(y)])

    def _on_scroll(self, x, y, dx, dy) -> None:
        if not self.recording:
            return
        if dy == 0:
            return
        action = "mouse wheel up" if dy > 0 else "mouse wheel down"
        self._emit("EM", action, [int(x), int(y)])

    # ------------------------------------------------------------ keyboard
    def _on_press(self, key) -> None:
        if not self.recording:
            return
        vk, name, extended = _key_info(key)
        if vk in self.ignore_vks:
            return
        self._emit("EK", "key down", [vk, name, extended])

    def _on_release(self, key) -> None:
        if not self.recording:
            return
        vk, name, extended = _key_info(key)
        if vk in self.ignore_vks:
            return
        self._emit("EK", "key up", [vk, name, extended])
