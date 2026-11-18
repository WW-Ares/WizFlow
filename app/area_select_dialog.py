"""在监控区域截图内框选二级搜索范围。"""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk
from PIL import Image, ImageTk

from core.screen import to_ctk_image
from . import theme
from .widgets import label
from .window_icon import apply_window_icon


class AreaSelectDialog(ctk.CTkToplevel):
    def __init__(self, master, region_shot_bgr, region_rect, current_roi=None, on_select=None):
        super().__init__(master)
        self.on_select = on_select
        self.pil = to_ctk_image(region_shot_bgr)
        self.iw, self.ih = self.pil.size
        self.scale = 1.0
        self.ox = 0
        self.oy = 0
        self.crop = list(current_roi) if current_roi and len(current_roi) == 4 and current_roi[2] > 0 else [0, 0, self.iw, self.ih]
        self._drag = None
        self._start = (0, 0)
        self._pan_start = (0, 0)
        self._photo = None
        self.title("框选二级搜索范围")
        self.geometry("980x700")
        self.minsize(820, 580)
        self.configure(fg_color=theme.BG)
        self.transient(master)
        apply_window_icon(self)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _e: self._cancel())
        self._build()
        self.after(80, self._fit)

    def _build(self):
        label(self, "框选二级搜索范围", 15, theme.TEXT).pack(anchor="w", padx=16, pady=(12, 2))
        label(self, "只在监控区域内搜索图标；不框选或点「整个区域」表示搜索整个监控区域。",
              10, theme.MUTED).pack(anchor="w", padx=16, pady=(0, 8))
        bar = ctk.CTkFrame(self, fg_color=theme.CARD, corner_radius=10)
        bar.pack(fill="x", padx=16, pady=(0, 8))
        for text, cmd in (("－ 缩小", lambda: self._zoom(1 / 1.25)), ("＋ 放大", lambda: self._zoom(1.25)),
                          ("适应窗口", self._fit), ("整个区域", self._select_all)):
            ctk.CTkButton(bar, text=text, command=cmd, width=82, height=28,
                          fg_color=theme.CARD2, hover_color=theme.LINE,
                          font=(theme.FONT, theme.fs(10))).pack(side="left", padx=5, pady=6)
        self.size_label = ctk.CTkLabel(bar, text="", font=(theme.MONO, theme.fs(10)),
                                       text_color=theme.ACCENT)
        self.size_label.pack(side="right", padx=10)

        self.canvas = tk.Canvas(self, bg="#060b16", highlightthickness=1,
                                highlightbackground=theme.LINE, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        self.canvas.bind("<ButtonPress-1>", self._down)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._up)
        self.canvas.bind("<ButtonPress-3>", self._pan_down)
        self.canvas.bind("<B3-Motion>", self._pan_move)
        self.canvas.bind("<ButtonRelease-3>", lambda _e: setattr(self, "_drag", None))
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Configure>", lambda _e: self._redraw())

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkButton(bottom, text="确定范围", height=36, fg_color=theme.OK,
                      text_color="#04121f", command=self._confirm).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(bottom, text="取消", width=90, height=36, fg_color=theme.CARD2,
                      hover_color=theme.LINE, command=self._cancel).pack(side="left", padx=(8, 0))

    def _fit(self):
        try:
            self.update_idletasks()
            cw = max(100, self.canvas.winfo_width())
            ch = max(100, self.canvas.winfo_height())
            self.scale = min(cw / max(1, self.iw), ch / max(1, self.ih)) * 0.96
            self.ox = (cw - self.iw * self.scale) / 2
            self.oy = (ch - self.ih * self.scale) / 2
            self._redraw()
        except Exception:
            pass

    def _zoom(self, factor):
        try:
            self._zoom_at(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2, factor)
        except Exception:
            pass

    def _zoom_at(self, cx, cy, factor):
        old = self.scale
        new = max(0.05, min(8.0, old * factor))
        if abs(new - old) < 1e-6:
            return
        ix = (cx - self.ox) / old
        iy = (cy - self.oy) / old
        self.scale = new
        self.ox = cx - ix * new
        self.oy = cy - iy * new
        self._redraw()

    def _wheel(self, e):
        self._zoom_at(e.x, e.y, 1.15 if e.delta > 0 else 1 / 1.15)

    def _canvas_to_img(self, x, y):
        return (x - self.ox) / max(1e-6, self.scale), (y - self.oy) / max(1e-6, self.scale)

    def _img_to_canvas(self, x, y):
        return self.ox + x * self.scale, self.oy + y * self.scale

    def _down(self, e):
        self._drag = "crop"
        ix, iy = self._canvas_to_img(e.x, e.y)
        ix = min(max(0, ix), self.iw)
        iy = min(max(0, iy), self.ih)
        self._start = (ix, iy)
        self.crop = [int(ix), int(iy), 1, 1]
        self._redraw()

    def _move(self, e):
        if self._drag != "crop":
            return
        x0, y0 = self._start
        x1, y1 = self._canvas_to_img(e.x, e.y)
        x1 = min(max(0, x1), self.iw)
        y1 = min(max(0, y1), self.ih)
        self.crop = [int(min(x0, x1)), int(min(y0, y1)),
                     max(1, int(abs(x1 - x0))), max(1, int(abs(y1 - y0)))]
        self._redraw()

    def _up(self, _e):
        self._drag = None

    def _pan_down(self, e):
        self._drag = "pan"
        self._pan_start = (e.x, e.y)
        self._pan_origin = (self.ox, self.oy)

    def _pan_move(self, e):
        if self._drag != "pan":
            return
        self.ox = self._pan_origin[0] + (e.x - self._pan_start[0])
        self.oy = self._pan_origin[1] + (e.y - self._pan_start[1])
        self._redraw()

    def _select_all(self):
        self.crop = [0, 0, self.iw, self.ih]
        self._redraw()

    def _redraw(self):
        c = self.canvas
        c.delete("all")
        try:
            w = max(1, int(self.iw * self.scale))
            h = max(1, int(self.ih * self.scale))
            img = self.pil.resize((w, h), Image.LANCZOS)
            self._photo = ImageTk.PhotoImage(img)
            c.create_image(self.ox, self.oy, anchor="nw", image=self._photo)
        except Exception:
            return
        x, y, cw, ch = self.crop
        lx, ly = self._img_to_canvas(x, y)
        lx2, ly2 = self._img_to_canvas(x + cw, y + ch)
        c.create_rectangle(lx, ly, lx2, ly2, outline="#22d3ee", width=2)
        for i in (1, 2):
            gx = lx + (lx2 - lx) * i / 3
            gy = ly + (ly2 - ly) * i / 3
            c.create_line(gx, ly, gx, ly2, fill="#22d3ee", dash=(3, 5))
            c.create_line(lx, gy, lx2, gy, fill="#22d3ee", dash=(3, 5))
        self.size_label.configure(text="范围 %d x %d  @ (%d, %d)" % (cw, ch, x, y))

    def _confirm(self):
        x, y, w, h = self.crop
        if x <= 0 and y <= 0 and w >= self.iw and h >= self.ih:
            roi = [0, 0, 0, 0]
        else:
            roi = [int(x), int(y), int(w), int(h)]
        self.destroy()
        if self.on_select:
            self.on_select(roi)

    def _cancel(self):
        self.destroy()
