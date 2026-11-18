"""全屏取点：点击屏幕记录一个固定点击位置。"""
from __future__ import annotations

import tkinter as tk

from core.winapi import primary_size, virtual_screen_rect
from . import theme


class PointPicker(tk.Toplevel):
    def __init__(self, master, on_pick=None, on_cancel=None):
        super().__init__(master)
        self.on_pick = on_pick
        self.on_cancel = on_cancel
        self.vx, self.vy, self.vw, self.vh = virtual_screen_rect()
        self.overrideredirect(True)
        self.configure(bg="black")
        self.attributes("-alpha", 0.28)
        self.attributes("-topmost", True)
        self.geometry("%dx%d+%d+%d" % (self.vw, self.vh, self.vx, self.vy))
        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Button-1>", self._click)
        self.bind("<Escape>", lambda _e: self._cancel())

        self.hud = tk.Toplevel(self)
        self.hud.overrideredirect(True)
        self.hud.configure(bg="#0b1220")
        self.hud.attributes("-topmost", True)
        w, h = 460, 82
        pw, _ph = primary_size()
        self.hud.geometry("%dx%d+%d+%d" % (w, h, max(0, (pw - w) // 2), 30))
        frame = tk.Frame(self.hud, bg="#0b1220", highlightthickness=2,
                         highlightbackground="#22d3ee")
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="点击目标位置", bg="#0b1220", fg="#e6edf7",
                 font=(theme.FONT, theme.fs(12), "bold")).pack(anchor="w", padx=12, pady=(8, 2))
        self.info = tk.StringVar(value="移动鼠标，左键确定，Esc 取消")
        tk.Label(frame, textvariable=self.info, bg="#0b1220", fg="#67e8f9",
                 font=(theme.MONO, theme.fs(10))).pack(anchor="w", padx=12)
        self.after(60, self._raise)

    def _raise(self):
        try:
            self.lift()
            self.hud.lift()
            self.focus_force()
        except Exception:
            pass

    def _motion(self, e):
        c = self.canvas
        c.delete("all")
        c.create_line(e.x, 0, e.x, self.vh, fill="#22d3ee", dash=(4, 4))
        c.create_line(0, e.y, self.vw, e.y, fill="#22d3ee", dash=(4, 4))
        c.create_oval(e.x - 8, e.y - 8, e.x + 8, e.y + 8, outline="#22d3ee", width=2)
        self.info.set("当前：X=%d  Y=%d    左键确定，Esc 取消"
                      % (e.x_root, e.y_root))

    def _click(self, e):
        x, y = int(e.x_root), int(e.y_root)
        self._close()
        if self.on_pick:
            self.on_pick(x, y)

    def _cancel(self):
        self._close()
        if self.on_cancel:
            self.on_cancel()

    def _close(self):
        try:
            self.hud.destroy()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
