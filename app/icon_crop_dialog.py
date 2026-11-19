"""从监控区域截图中圈选图标：缩放、平移、拖拽裁剪、保存。"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path

import customtkinter as ctk
from PIL import Image, ImageTk

from config import ICONS_DIR
from core.screen import save_png, to_ctk_image
from . import theme
from .widgets import center_window, entry, label
from .window_icon import apply_window_icon


class IconCropDialog(ctk.CTkToplevel):
    def __init__(self, master, region_shot_bgr, region_rect, default_name: str,
                 default_crop=None, on_save=None):
        super().__init__(master)
        self.on_save = on_save
        self.default_name = default_name
        self.region_rect = [int(v) for v in region_rect]
        self.pil = to_ctk_image(region_shot_bgr)
        self.iw, self.ih = self.pil.size
        self.scale = 1.0
        self.ox = 0
        self.oy = 0
        self.crop = list(default_crop) if default_crop and len(default_crop) == 4 else [0, 0, self.iw, self.ih]
        self._drag = None
        self._space = False
        self._drag_start = (0, 0)
        self._pan_start = (0, 0)
        self._photo = None
        self._preview_img = None

        self.title("从监控区域截图中圈选图标")
        apply_window_icon(self)
        self.geometry("1040x720")
        self.minsize(900, 620)
        self.configure(fg_color=theme.BG)
        self.transient(master)
        try:
            self.grab_set()
        except Exception:
            pass
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _e: self._cancel())
        self._build()
        self.after(60, self._fit)
        self.after(100, self._center)
        self.after(140, self._raise)

    def _raise(self):
        try:
            self.lift()
            self.attributes("-topmost", True)
            self.focus_force()
            self.after(300, lambda: self.attributes("-topmost", False))
        except Exception:
            pass

    # ------------------------------------------------------------ UI
    def _build(self):
        label(self, "从监控区域截图中圈选匹配图标", 16, theme.TEXT).pack(anchor="w", padx=18, pady=(14, 2))
        label(self, "左键拖拽框选；滚轮缩放；按住空格+左键拖动平移；右键拖动也可平移。",
              10, theme.MUTED).pack(anchor="w", padx=18, pady=(0, 8))

        bar = ctk.CTkFrame(self, fg_color=theme.CARD, corner_radius=10)
        bar.pack(fill="x", padx=18, pady=(0, 8))
        self._tool_btn(bar, "－ 缩小", lambda: self._zoom(1 / 1.25)).pack(side="left", padx=6, pady=6)
        self._tool_btn(bar, "＋ 放大", lambda: self._zoom(1.25)).pack(side="left", padx=6, pady=6)
        self._tool_btn(bar, "适应窗口", self._fit).pack(side="left", padx=6, pady=6)
        self._tool_btn(bar, "全选区域", self._select_all).pack(side="left", padx=6, pady=6)
        self.size_label = ctk.CTkLabel(bar, text="", font=(theme.MONO, theme.fs(11)), text_color=theme.ACCENT)
        self.size_label.pack(side="right", padx=12)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)
        body.grid_rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(body, bg="#060b16", highlightthickness=1,
                                highlightbackground=theme.LINE, cursor="crosshair")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<ButtonPress-1>", self._down)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._up)
        self.canvas.bind("<ButtonPress-3>", self._pan_down)
        self.canvas.bind("<B3-Motion>", self._pan_move)
        self.canvas.bind("<ButtonRelease-3>", lambda _e: setattr(self, "_drag", None))
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Configure>", lambda _e: self._redraw())
        for w in (self, self.canvas):
            w.bind("<KeyPress-space>", self._space_down)
            w.bind("<KeyRelease-space>", self._space_up)

        side = ctk.CTkFrame(body, fg_color=theme.CARD, corner_radius=12, width=280)
        side.grid(row=0, column=1, sticky="ns", padx=(10, 0))
        side.grid_propagate(False)
        label(side, "裁剪预览（1:1）", 11, theme.TEXT).pack(anchor="w", padx=12, pady=(12, 6))
        self.preview = ctk.CTkLabel(side, text="", fg_color=theme.BG2, corner_radius=8,
                                    width=250, height=250)
        self.preview.pack(padx=12)
        label(side, "文件名", 10, theme.MUTED).pack(anchor="w", padx=12, pady=(14, 2))
        self.name_var = tk.StringVar(value=self.default_name or "icon")
        self.name_entry = entry(side, width=240, textvariable=self.name_var)
        self.name_entry.pack(padx=12, pady=(0, 8))
        label(side, "保存到 icons/", 9, theme.MUTED).pack(anchor="w", padx=12)
        ctk.CTkButton(side, text="保存图标", height=38, fg_color=theme.OK,
                      hover_color="#16a34a", text_color="#04121f",
                      font=(theme.FONT, theme.fs(12), "bold"),
                      command=self._save).pack(fill="x", padx=12, pady=(14, 6))
        ctk.CTkButton(side, text="取消", height=34, fg_color=theme.CARD2,
                      hover_color=theme.LINE, command=self._cancel).pack(fill="x", padx=12)

    def _tool_btn(self, parent, text, cmd):
        return ctk.CTkButton(parent, text=text, command=cmd, width=82, height=28,
                             fg_color=theme.CARD2, hover_color=theme.LINE,
                             font=(theme.FONT, theme.fs(10)))

    # ------------------------------------------------------------ 坐标
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

    def _select_all(self):
        self.crop = [0, 0, self.iw, self.ih]
        self._redraw()

    def _zoom(self, factor):
        try:
            cx = self.canvas.winfo_width() / 2
            cy = self.canvas.winfo_height() / 2
            self._zoom_at(cx, cy, factor)
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

    # ------------------------------------------------------------ 鼠标
    def _space_down(self, _e=None):
        self._space = True
        try:
            self.canvas.configure(cursor="fleur")
        except Exception:
            pass
        return "break"

    def _space_up(self, _e=None):
        self._space = False
        try:
            self.canvas.configure(cursor="crosshair")
        except Exception:
            pass
        return "break"

    def _down(self, e):
        try:
            self.canvas.focus_set()
        except Exception:
            pass
        if self._space:
            self._pan_down(e)
            return
        self._drag = "crop"
        ix, iy = self._canvas_to_img(e.x, e.y)
        ix = min(max(0, ix), self.iw)
        iy = min(max(0, iy), self.ih)
        self._drag_start = (ix, iy)
        self.crop = [int(ix), int(iy), 1, 1]
        self._redraw()

    def _move(self, e):
        if self._drag == "pan":
            self._pan_move(e)
            return
        if self._drag != "crop":
            return
        x0, y0 = self._drag_start
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
        dx = e.x - self._pan_start[0]
        dy = e.y - self._pan_start[1]
        self.ox = self._pan_origin[0] + dx
        self.oy = self._pan_origin[1] + dy
        self._redraw()

    # ------------------------------------------------------------ 绘制
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
        # 遮罩四块
        try:
            c.create_rectangle(0, 0, max(0, lx), self.canvas.winfo_height(),
                               fill="#000000", stipple="gray50", outline="")
            c.create_rectangle(min(lx2, self.canvas.winfo_width()), 0,
                               self.canvas.winfo_width(), self.canvas.winfo_height(),
                               fill="#000000", stipple="gray50", outline="")
            c.create_rectangle(lx, 0, lx2, ly, fill="#000000", stipple="gray50", outline="")
            c.create_rectangle(lx, ly2, lx2, self.canvas.winfo_height(),
                               fill="#000000", stipple="gray50", outline="")
        except Exception:
            pass
        c.create_rectangle(lx, ly, lx2, ly2, outline="#22d3ee", width=2)
        for i in (1, 2):
            gx = lx + (lx2 - lx) * i / 3
            gy = ly + (ly2 - ly) * i / 3
            c.create_line(gx, ly, gx, ly2, fill="#22d3ee", dash=(3, 5))
            c.create_line(lx, gy, lx2, gy, fill="#22d3ee", dash=(3, 5))
        self.size_label.configure(text="裁剪 %d x %d  @ (%d, %d)" % (cw, ch, x, y))
        self._update_preview()

    def _update_preview(self):
        try:
            x, y, w, h = self.crop
            x = max(0, min(x, self.iw - 1))
            y = max(0, min(y, self.ih - 1))
            w = max(1, min(w, self.iw - x))
            h = max(1, min(h, self.ih - y))
            img = self.pil.crop((x, y, x + w, y + h))
            img.thumbnail((250, 250), Image.LANCZOS)
            self._preview_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self.preview.configure(image=self._preview_img, text="")
        except Exception:
            pass

    # ------------------------------------------------------------ 保存
    def _save(self):
        x, y, w, h = self.crop
        x = max(0, min(x, self.iw - 1))
        y = max(0, min(y, self.ih - 1))
        w = max(1, min(w, self.iw - x))
        h = max(1, min(h, self.ih - y))
        name = self.name_var.get().strip() or "icon"
        name = "".join(c for c in name if c not in r'\/:*?"<>|')[:48] or "icon"
        if not name.lower().endswith(".png"):
            name += ".png"
        path = ICONS_DIR / name
        i = 1
        while path.exists():
            path = ICONS_DIR / ("%s_%d.png" % (Path(name).stem, i))
            i += 1
        crop_img = self.pil.crop((x, y, x + w, y + h))
        try:
            save_png(to_bgr(crop_img), path)
        except Exception as exc:
            self.size_label.configure(text="保存失败：%s" % exc)
            return
        rect = [x, y, w, h]
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        if self.on_save:
            self.on_save(path, rect)

    def _cancel(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def _center(self):
        center_window(self)


def to_bgr(pil):
    import numpy as np
    import cv2
    arr = np.array(pil.convert("RGB"))
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
