"""录制单键或组合键（录制期间拦截系统快捷键）。"""
from __future__ import annotations

import customtkinter as ctk

from core.keygrab import MOD_VKS, KeyGrabber
from . import theme
from .widgets import label
from .window_icon import apply_window_icon

_MODS = {
    "Control_L": "ctrl", "Control_R": "ctrl",
    "Alt_L": "alt", "Alt_R": "alt",
    "Shift_L": "shift", "Shift_R": "shift",
    "Super_L": "win", "Super_R": "win", "Win_L": "win", "Win_R": "win",
}
_NAMED = {
    "Return": "enter", "space": "space", "BackSpace": "backspace",
    "Tab": "tab", "Delete": "delete", "Insert": "insert",
    "Home": "home", "End": "end", "Prior": "pageup", "Next": "pagedown",
    "Up": "up", "Down": "down", "Left": "left", "Right": "right",
    "Caps_Lock": "capslock", "Num_Lock": "numlock",
}


class KeyRecordDialog(ctk.CTkToplevel):
    def __init__(self, master, on_done=None, initial=""):
        super().__init__(master)
        self.on_done = on_done
        self.mods = set()
        self.main = None
        self.result = ""
        self.grabber: KeyGrabber | None = None
        self._done = False
        self._poll_id = None
        self._timeout_id = None
        self.title("录制按键")
        self.geometry("560x330")
        self.resizable(False, False)
        self.configure(fg_color=theme.BG)
        self.transient(master)
        apply_window_icon(self)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        label(self, "录制键盘按键", 16, theme.TEXT).pack(anchor="w", padx=20, pady=(16, 2))
        label(self, "录制时会拦截系统快捷键；Alt+Tab / Win 等不会切换窗口。",
              10, theme.MUTED).pack(anchor="w", padx=20)
        self.preview = ctk.CTkLabel(self, text=initial or "等待按键…",
                                    font=(theme.MONO, theme.fs(22), "bold"),
                                    fg_color=theme.BG2, corner_radius=10, height=100)
        self.preview.pack(fill="x", padx=20, pady=16)
        self.status = label(self, "正在安装键盘拦截…", 9, theme.WARN)
        self.status.pack(anchor="w", padx=20)
        label(self, "按 Esc 取消；松开主键即完成录制；15 秒无操作自动取消。",
              9, theme.MUTED).pack(anchor="w", padx=20, pady=(2, 0))
        ctk.CTkButton(self, text="取消", width=90, height=34, fg_color=theme.CARD2,
                      hover_color=theme.LINE, command=self._cancel).pack(side="right", padx=20, pady=14)
        self.after(140, self._start_grab)

    def _start_grab(self):
        if self._done:
            return
        try:
            self.lift()
            self.focus_force()
            self.grab_set()
        except Exception:
            pass
        g = KeyGrabber()
        if g.start():
            self.grabber = g
            self.status.configure(text="已拦截系统快捷键，请按键…", text_color=theme.OK)
            self._poll_id = self.after(25, self._poll)
            self._timeout_id = self.after(15000, self._timeout)
        else:
            self.status.configure(text="键盘拦截不可用，退回普通录制：%s" % g.error,
                                  text_color=theme.WARN)
            self.bind("<KeyPress>", self._on_press_tk)
            self.bind("<KeyRelease>", self._on_release_tk)

    def _poll(self):
        if self._done or self.grabber is None:
            return
        for kind, vk, combo in self.grabber.drain():
            if vk == 0x1B and kind == "down":
                self._cancel()
                return
            if kind == "down":
                if vk in MOD_VKS:
                    self.preview.configure(text=combo + " + …")
                    continue
                self.main = vk
                self.result = combo
                self.preview.configure(text=combo)
            elif kind == "up" and self.main == vk and self.result:
                self._finish(self.result)
                return
        self._poll_id = self.after(25, self._poll)

    def _timeout(self):
        if not self._done:
            self.status.configure(text="15 秒未检测到按键，已取消", text_color=theme.WARN)
            self._cancel()

    # ------------------------------------------------------------ 兜底（钩子不可用）
    def _name_for(self, event):
        ks = event.keysym
        if ks in _MODS:
            return None
        if ks in _NAMED:
            return _NAMED[ks]
        if ks.startswith("F") and ks[1:].isdigit():
            return ks.lower()
        if len(ks) == 1 and ks.isalnum():
            return ks.lower()
        try:
            return "vk_%d" % int(event.keycode)
        except Exception:
            return None

    def _on_press_tk(self, event):
        if event.keysym == "Escape":
            self._cancel()
            return "break"
        if event.keysym in _MODS:
            self.mods.add(_MODS[event.keysym])
            self.preview.configure(text=" + ".join(sorted(self.mods)) + " + …")
            return "break"
        name = self._name_for(event)
        if name:
            self.main = name
            parts = [m for m in ("ctrl", "alt", "shift", "win") if m in self.mods]
            self.result = "+".join(parts + [name])
            self.preview.configure(text=self.result)
        return "break"

    def _on_release_tk(self, event):
        if self.main and event.keysym not in _MODS:
            if self.result:
                self._finish(self.result)
        elif event.keysym in _MODS:
            self.mods.discard(_MODS[event.keysym])
        return "break"

    # ------------------------------------------------------------ 结束
    def _stop_grab(self):
        if self._poll_id:
            try:
                self.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None
        if self._timeout_id:
            try:
                self.after_cancel(self._timeout_id)
            except Exception:
                pass
            self._timeout_id = None
        if self.grabber is not None:
            self.grabber.stop()
            self.grabber = None

    def _finish(self, result):
        if self._done:
            return
        self._done = True
        self._stop_grab()
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        if self.on_done:
            self.on_done(result)

    def _cancel(self):
        if self._done:
            return
        self._done = True
        self._stop_grab()
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        if self.on_done:
            self.on_done(None)
