"""录制单键或组合键。"""
from __future__ import annotations

import customtkinter as ctk

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
        self.title("录制按键")
        self.geometry("540x300")
        self.resizable(False, False)
        self.configure(fg_color=theme.BG)
        self.transient(master)
        apply_window_icon(self)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<KeyPress>", self._on_press)
        self.bind("<KeyRelease>", self._on_release)
        label(self, "录制键盘按键", 16, theme.TEXT).pack(anchor="w", padx=20, pady=(16, 2))
        label(self, "点击下方按钮后直接按键；支持 Ctrl/Alt/Shift/Win 组合键。",
              10, theme.MUTED).pack(anchor="w", padx=20)
        self.preview = ctk.CTkLabel(self, text=initial or "等待按键…",
                                    font=(theme.MONO, theme.fs(22), "bold"),
                                    fg_color=theme.BG2, corner_radius=10, height=100)
        self.preview.pack(fill="x", padx=20, pady=18)
        label(self, "按 Esc 取消；松开主键即完成录制。", 9).pack(anchor="w", padx=20)
        ctk.CTkButton(self, text="取消", width=90, height=34, fg_color=theme.CARD2,
                      hover_color=theme.LINE, command=self._cancel).pack(side="right", padx=20, pady=14)
        self.after(120, self._focus)

    def _focus(self):
        try:
            self.lift()
            self.focus_force()
            self.grab_set()
        except Exception:
            pass

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

    def _on_press(self, event):
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

    def _on_release(self, event):
        if self.main and event.keysym not in _MODS:
            if self.result:
                self._finish(self.result)
        elif event.keysym in _MODS:
            self.mods.discard(_MODS[event.keysym])
        return "break"

    def _finish(self, result):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        if self.on_done:
            self.on_done(result)

    def _cancel(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        if self.on_done:
            self.on_done(None)
