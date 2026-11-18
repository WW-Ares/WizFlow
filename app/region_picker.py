"""全屏框选监控区域。

遮罩层单独透明；操作按钮放在独立的不透明 HUD 窗口里，避免按钮跟着遮罩一起变淡。
"""
from __future__ import annotations

import tkinter as tk

from core.winapi import primary_size, virtual_screen_rect
from . import theme


class RegionPicker(tk.Toplevel):
    def __init__(self, master, rect=None, on_confirm=None, on_cancel=None):
        super().__init__(master)
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self.vx, self.vy, self.vw, self.vh = virtual_screen_rect()

        # 只让遮罩透明，HUD 是单独窗口，保持 100% 不透明
        self.overrideredirect(True)
        self.configure(bg="black")
        self.attributes("-alpha", 0.35)
        self.attributes("-topmost", True)
        self.geometry("%dx%d+%d+%d" % (self.vw, self.vh, self.vx, self.vy))

        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        if rect and len(rect) == 4 and int(rect[2]) >= 4 and int(rect[3]) >= 4:
            self.rect = [int(v) for v in rect]
        else:
            w = min(self.vw - 100, 420)
            h = min(self.vh - 100, 280)
            self.rect = [self.vx + (self.vw - w) // 2, self.vy + (self.vh - h) // 2, w, h]

        self._drag = False
        self._start = (0, 0)
        self._build_hud()
        self._bind_events()
        self.after(40, self._first_draw)

    # ------------------------------------------------------------ HUD
    def _build_hud(self):
        self.hud = tk.Toplevel(self)
        self.hud.overrideredirect(True)
        self.hud.configure(bg="#0b1220")
        self.hud.attributes("-topmost", True)
        try:
            self.hud.attributes("-alpha", 1.0)
        except Exception:
            pass

        w, h = 760, 118
        pw, _ph = primary_size()
        # 固定在主屏顶部，避免虚拟屏上方存在负坐标显示器时 HUD 跑到屏幕外
        x = max(0, (pw - w) // 2)
        y = 28
        self.hud.geometry("%dx%d+%d+%d" % (w, h, x, y))

        frame = tk.Frame(self.hud, bg="#0b1220", highlightthickness=2,
                         highlightbackground="#22d3ee")
        frame.pack(fill="both", expand=True)

        top = tk.Frame(frame, bg="#0b1220")
        top.pack(fill="x", padx=14, pady=(8, 2))
        tk.Label(top, text="框选监控区域", bg="#0b1220", fg="#e6edf7",
                 font=(theme.FONT, theme.fs(12), "bold")).pack(side="left")
        self.var_info = tk.StringVar(value="拖拽框选，Enter 确定，Esc 取消")
        tk.Label(top, textvariable=self.var_info, bg="#0b1220", fg="#67e8f9",
                 font=(theme.MONO, theme.fs(10))).pack(side="right")

        row = tk.Frame(frame, bg="#0b1220")
        row.pack(fill="x", padx=12, pady=(4, 6))

        def btn(text, cmd, bg, fg="#ffffff", width=10):
            b = tk.Button(row, text=text, command=cmd, bg=bg, fg=fg, relief="flat",
                          bd=0, padx=10, pady=6, width=width, cursor="hand2",
                          activebackground="#334155", activeforeground="white",
                          font=(theme.FONT, theme.fs(10), "bold"))
            b.pack(side="left", padx=4)
            return b

        btn("确定 (Enter)", lambda: self.confirm(False), "#2563eb", width=11)
        btn("取消 (Esc)", self.cancel, "#334155", width=10)
        btn("全屏", self._full, "#1e293b", width=6)


        self.hud.bind("<Escape>", lambda _e: self.cancel())
        self.hud.bind("<Return>", lambda _e: self.confirm(False))
        self.hud.protocol("WM_DELETE_WINDOW", self.cancel)

    def _bind_events(self):
        self.canvas.bind("<ButtonPress-1>", self._down)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._up)
        self.canvas.bind("<Double-Button-1>", lambda _e: self.confirm(False))
        self.bind("<Escape>", lambda _e: self.cancel())
        self.bind("<Return>", lambda _e: self.confirm(False))
        self.focus_force()

    def _first_draw(self):
        self.lift()
        try:
            self.hud.lift()
        except Exception:
            pass
        self.focus_force()
        self.draw()

    # ------------------------------------------------------------ 拖拽
    def _down(self, e):
        self._drag = True
        self._start = (e.x_root, e.y_root)
        self.rect = [e.x_root, e.y_root, 1, 1]
        self.draw()

    def _move(self, e):
        if not self._drag:
            return
        sx, sy = self._start
        x0, x1 = sorted((sx, e.x_root))
        y0, y1 = sorted((sy, e.y_root))
        self.rect = [int(x0), int(y0), max(1, int(x1 - x0)), max(1, int(y1 - y0))]
        self.draw()

    def _up(self, _e):
        self._drag = False

    def _full(self):
        self.rect = [self.vx, self.vy, self.vw, self.vh]
        self.draw()

    def _center(self):
        w, h = self.rect[2], self.rect[3]
        self.rect = [self.vx + (self.vw - w) // 2, self.vy + (self.vh - h) // 2, w, h]
        self.draw()

    # ------------------------------------------------------------ 绘制
    def draw(self):
        x, y, w, h = [int(v) for v in self.rect]
        lx, ly = x - self.vx, y - self.vy
        c = self.canvas
        c.delete("all")
        # 遮罩四块，中间掏空
        c.create_rectangle(0, 0, self.vw, ly, fill="black", outline="")
        c.create_rectangle(0, ly + h, self.vw, self.vh, fill="black", outline="")
        c.create_rectangle(0, ly, lx, ly + h, fill="black", outline="")
        c.create_rectangle(lx + w, ly, self.vw, ly + h, fill="black", outline="")
        c.create_rectangle(lx, ly, lx + w, ly + h, outline="#22d3ee", width=3)
        # 三分线
        for i in (1, 2):
            gx, gy = lx + w * i / 3, ly + h * i / 3
            c.create_line(gx, ly, gx, ly + h, fill="#22d3ee", dash=(4, 6))
            c.create_line(lx, gy, lx + w, gy, fill="#22d3ee", dash=(4, 6))
        # 尺寸标签
        tx = min(max(lx + 4, 4), max(4, self.vw - 260))
        ty = ly - 26 if ly > 34 else ly + h + 8
        c.create_text(tx, ty, anchor="nw", fill="#67e8f9",
                      font=(theme.MONO, theme.fs(12), "bold"),
                      text="%d x %d   @ (%d, %d)" % (w, h, x, y))
        self.var_info.set("%d x %d  @ (%d, %d)" % (w, h, x, y))
        try:
            self.hud.lift()
        except Exception:
            pass

    # ------------------------------------------------------------ 完成
    def _close(self):
        try:
            self.hud.destroy()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    def confirm(self, capture: bool = False):
        rect = [int(v) for v in self.rect]
        if rect[2] < 4 or rect[3] < 4:
            return
        self._close()
        if self.on_confirm:
            self.on_confirm(rect, bool(capture))

    def cancel(self):
        self._close()
        if self.on_cancel:
            self.on_cancel()
