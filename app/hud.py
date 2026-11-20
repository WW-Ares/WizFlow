"""迷你悬浮窗：310×88，右上角，状态胶囊 + F6/F7/F8 一键操作。

背景层与文字层分成两个 Toplevel：
- 背景层允许调透明度，只画卡片和色块；
- 文字层始终 100% 不透明，保证任何透明度下字体都清晰。
"""
from __future__ import annotations

import ctypes
import tkinter as tk
import tkinter.font as tkfont

from config import Config
from core.winapi import virtual_screen_rect
from core.window_geometry import primary_work_area, rect_visible_enough
from . import theme

TRANSPARENT = "#010203"
CARD_W = 310
CARD_H = 88
BG = "#14181f"
BORDER = "#2b3341"
CHIP_BG = "#1f2530"
CHIP_HOVER = "#252d3b"
CHIP_LINE = "#313a4a"

STATE_STYLE = {
    "idle": ("待机", "#1b2434", "#334155", "#94a3b8"),
    "running": ("运行中", "#12321f", "#22c55e", "#86efac"),
    "paused": ("已暂停", "#3a2a10", "#f59e0b", "#fcd34d"),
    "recording": ("录制中", "#3a1414", "#ef4444", "#fca5a5"),
}

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020


def _shadow_text(canvas, x, y, text, font, fill, anchor="w", tags=()):
    """文字加深色投影，背景调透明后仍然清晰。"""
    canvas.create_text(x + 1, y + 1, text=text, font=font, fill="#05080d",
                       anchor=anchor, tags=tags)
    return canvas.create_text(x, y, text=text, font=font, fill=fill,
                              anchor=anchor, tags=tags)


def _round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, splinesteps=24, **kw)


class HudWindow(tk.Toplevel):
    def __init__(self, app, cfg: Config):
        super().__init__(app)
        self.app = app
        self.cfg = cfg
        self.alpha = float(cfg.get("hud.alpha", 1.0) or 1.0)
        self.click_through = bool(cfg.get("hud.click_through", False))
        self._state = "idle"
        self._status_text = ""
        self._drag = None
        self._chip_tags = []

        self.bg = tk.Toplevel(app)
        for w in (self, self.bg):
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            w.configure(bg=TRANSPARENT)
            try:
                w.wm_attributes("-transparentcolor", TRANSPARENT)
            except Exception:
                pass
        try:
            self.bg.attributes("-alpha", max(0.35, min(1.0, self.alpha)))
        except Exception:
            pass

        self.bg_canvas = tk.Canvas(self.bg, width=CARD_W, height=CARD_H, bg=TRANSPARENT,
                                   highlightthickness=0, bd=0)
        self.bg_canvas.pack()
        self.canvas = tk.Canvas(self, width=CARD_W, height=CARD_H, bg=TRANSPARENT,
                                highlightthickness=0, bd=0)
        self.canvas.pack()

        self.f_title = tkfont.Font(family="Microsoft YaHei UI", size=-17, weight="bold")
        self.f_key = tkfont.Font(family="Consolas", size=-13, weight="bold")
        self.f_lbl = tkfont.Font(family="Microsoft YaHei UI", size=-14)
        self.f_pill = tkfont.Font(family="Microsoft YaHei UI", size=-14, weight="bold")

        self._build_menu()
        self._draw()
        self._place()
        self.after(120, self._sync_windows)
        self.after(200, lambda: self._apply_click_through(self.click_through))

        for c in (self.canvas, self.bg_canvas):
            c.bind("<Button-1>", self._drag_start)
            c.bind("<B1-Motion>", self._drag_move)
            c.bind("<ButtonRelease-1>", self._drag_end)
            c.bind("<Button-3>", self._menu_popup)
        self.bind("<Escape>", lambda _e: self.hide())

    def _chip_labels(self):
        sa = str(self.cfg.get("hotkeys.start_pause", "F6") or "F6").upper()
        st = str(self.cfg.get("hotkeys.stop", "F7") or "F7").upper()
        rc = str(self.cfg.get("hotkeys.record", "F8") or "F8").upper()
        return [(sa, "启动/暂停"), (st, "停止"), (rc, "录制")]

    def _chip_widths(self):
        pad, gap = 10, 6
        labels = self._chip_labels()
        widths = [pad + self.f_key.measure(k) + gap + self.f_lbl.measure(v) + pad
                  for k, v in labels]
        if sum(widths) + 40 > CARD_W:
            pad, gap = 7, 4
            widths = [pad + self.f_key.measure(k) + gap + self.f_lbl.measure(v) + pad
                      for k, v in labels]
        return widths

    def _chip_geometry(self):
        widths = self._chip_widths()
        space = (CARD_W - sum(widths)) / 4.0
        out = []
        x = space
        for w in widths:
            out.append((x, w))
            x += w + space
        return out

    def _draw(self):
        for c in (self.canvas, self.bg_canvas):
            c.delete("all")
        bc, fc = self.bg_canvas, self.canvas
        _round_rect(bc, 1, 1, CARD_W - 1, CARD_H - 1, 12, fill=BG, outline="")
        _round_rect(fc, 1, 1, CARD_W - 1, CARD_H - 1, 12, fill="", outline=BORDER, width=1)
        fc.create_line(18, 2, CARD_W - 18, 2, fill="#ffffff", stipple="gray25")

        fc.create_polygon([15, 14, 29, 14, 32, 17, 32, 30, 29, 33, 15, 33, 12, 30, 12, 17],
                          smooth=True, splinesteps=16, fill=theme.ACCENT, outline="")
        fc.create_line(17, 27, 21, 21, 25, 24, 29, 18, fill="#ffffff", width=2,
                       capstyle="round", joinstyle="round")
        _shadow_text(fc, 39, 26, "WizFlow", self.f_title, "#e8edf6", "w")

        text, fill, line, fg = STATE_STYLE.get(self._state, STATE_STYLE["idle"])
        if self._state == "running" and "无限" in self._status_text:
            text = "运行中[无限]"
        elif self._state == "running" and "第" in self._status_text:
            text = self._status_text.replace("运行中", "回放中")[:10]
        elif self._state == "recording":
            text = "录制中"
        elif self._state == "paused":
            text = "已暂停"
        elif self._state == "idle":
            text = "空闲"
        pw = 12 + 6 + 5 + self.f_pill.measure(text) + 10
        x2 = CARD_W - 12
        x1 = x2 - pw
        y1, y2 = 12, 40
        _round_rect(bc, x1, y1, x2, y2, (y2 - y1) / 2, fill=fill, outline="")
        _round_rect(fc, x1, y1, x2, y2, (y2 - y1) / 2, fill="", outline=line, width=1)
        fc.create_oval(x1 + 9, 19, x1 + 17, 27, fill=line, outline="")
        _shadow_text(fc, x1 + 21, (y1 + y2) / 2, text, self.f_pill, fg, "w")

        labels = self._chip_labels()
        geoms = self._chip_geometry()
        self._chip_tags = []
        for i, ((key, text), (x, w)) in enumerate(zip(labels, geoms)):
            tag = "chip%d" % i
            self._chip_tags.append(tag)
            y1, y2 = 46, 80
            _round_rect(bc, x, y1, x + w, y2, 7, fill=CHIP_BG, outline="", tags=(tag,))
            _round_rect(fc, x, y1, x + w, y2, 7, fill="", outline=CHIP_LINE, width=1, tags=(tag,))
            kw = self.f_key.measure(key)
            lw = self.f_lbl.measure(text)
            tx = x + (w - (kw + 6 + lw)) / 2
            _shadow_text(fc, tx, (y1 + y2) / 2, key, self.f_key, "#7dd3fc", "w", (tag,))
            _shadow_text(fc, tx + kw + 6, (y1 + y2) / 2, text, self.f_lbl, "#d8e0ec", "w", (tag,))
            action = ("start_pause", "stop", "record")[i]
            for c in (bc, fc):
                c.tag_bind(tag, "<Enter>", lambda _e, t=tag: self._chip_hover(t, True))
                c.tag_bind(tag, "<Leave>", lambda _e, t=tag: self._chip_hover(t, False))
                c.tag_bind(tag, "<Button-1>", lambda _e, a=action: self._chip_click(a))

    def _chip_hover(self, tag, on):
        if self.click_through:
            return
        try:
            self.bg_canvas.itemconfigure(tag, fill=CHIP_HOVER if on else CHIP_BG)
        except Exception:
            pass

    def _chip_click(self, action):
        if self.click_through:
            return "break"
        try:
            self.app._handle_hotkey(action)
        except Exception:
            pass
        return "break"

    def set_status(self, text: str, color: str | None = None):
        t = str(text or "")
        if "录制" in t:
            state = "recording"
        elif "暂停" in t:
            state = "paused"
        elif any(k in t for k in ("运行", "回放", "监控")):
            state = "running"
        else:
            state = "idle"
        changed = (state != self._state) or (t != self._status_text)
        self._state = state
        self._status_text = t
        if changed:
            self._draw()

    def refresh_hotkeys(self):
        self._draw()

    def _primary_top_right(self):
        px, py, pw, _ph = primary_work_area()
        return px + pw - CARD_W - 16, py + 16

    def _place(self):
        vx, vy, vw, vh = virtual_screen_rect()
        if bool(self.cfg.get("hud.saved", False)):
            x = int(self.cfg.get("hud.x", 0) or 0)
            y = int(self.cfg.get("hud.y", 0) or 0)
            if not rect_visible_enough(x, y, CARD_W, CARD_H):
                x, y = self._primary_top_right()
        else:
            x, y = self._primary_top_right()
        x = max(vx, min(x, vx + vw - CARD_W))
        y = max(vy, min(y, vy + vh - CARD_H))
        geo = "%dx%d+%d+%d" % (CARD_W, CARD_H, x, y)
        try:
            self.geometry(geo)
            self.bg.geometry(geo)
        except Exception:
            pass

    def _sync_windows(self):
        self._bind_owner()
        try:
            self.bg.lift()
            self.lift()
        except Exception:
            pass

    def _bind_owner(self):
        """把文字层设为背景层的 owned window，Windows 会始终把它保持在上方。"""
        try:
            u32 = ctypes.windll.user32
            u32.GetParent.restype = ctypes.c_void_p
            u32.SetWindowLongPtrW.restype = ctypes.c_void_p
            u32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            fg = u32.GetParent(self.winfo_id()) or self.winfo_id()
            bg = u32.GetParent(self.bg.winfo_id()) or self.bg.winfo_id()
            if fg and bg:
                u32.SetWindowLongPtrW(fg, -8, bg)
        except Exception:
            pass

    def _drag_start(self, e):
        if self.click_through:
            return
        self._drag = (e.x_root, e.y_root, self.winfo_x(), self.winfo_y())

    def _drag_move(self, e):
        if not self._drag or self.click_through:
            return
        sx, sy, wx, wy = self._drag
        x, y = wx + e.x_root - sx, wy + e.y_root - sy
        try:
            self.geometry("+%d+%d" % (x, y))
            self.bg.geometry("+%d+%d" % (x, y))
        except Exception:
            pass

    def _drag_end(self, _e):
        if not self._drag:
            return
        self._drag = None
        x, y = self.winfo_x(), self.winfo_y()
        vx, vy, vw, vh = virtual_screen_rect()
        if not rect_visible_enough(x, y, CARD_W, CARD_H):
            x, y = self._primary_top_right()
        if abs(x - vx) < 24:
            x = vx + 8
        if abs((x + CARD_W) - (vx + vw)) < 24:
            x = vx + vw - CARD_W - 8
        if abs(y - vy) < 24:
            y = vy + 8
        try:
            self.geometry("+%d+%d" % (x, y))
            self.bg.geometry("+%d+%d" % (x, y))
        except Exception:
            pass
        cfg_hud = dict(self.cfg.get("hud", {}) or {})
        cfg_hud.update({"enabled": bool(self.cfg.get("hud.enabled", True)),
                        "x": int(x), "y": int(y), "saved": True,
                        "alpha": self.alpha, "click_through": self.click_through})
        self.cfg.set("hud", cfg_hud)
        self.cfg.save()

    def _build_menu(self):
        self.menu = tk.Menu(self, tearoff=0, bg="#141a24", fg="#e6edf7",
                            activebackground="#1d4ed8", activeforeground="#ffffff",
                            bd=0, font=(theme.FONT, theme.fs(9)))
        self.menu.add_command(label="打开主界面", command=self._show_main)
        self.through_var = tk.BooleanVar(value=self.click_through)
        self.menu.add_checkbutton(label="鼠标穿透（只显示不挡点击）", command=self._toggle_through,
                                  variable=self.through_var)
        alpha_menu = tk.Menu(self.menu, tearoff=0, bg="#141a24", fg="#e6edf7",
                             activebackground="#1d4ed8", activeforeground="#ffffff")
        for v in (100, 90, 80, 70, 60, 50):
            alpha_menu.add_command(label="%d%%" % v, command=lambda a=v: self._set_alpha(a / 100.0))
        self.menu.add_cascade(label="背景透明度", menu=alpha_menu)
        self.menu.add_separator()
        self.menu.add_command(label="隐藏悬浮窗", command=self._hide_from_menu)

    def _menu_popup(self, e):
        if self.click_through:
            return
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            try:
                self.menu.grab_release()
            except Exception:
                pass

    def _show_main(self):
        try:
            self.app.deiconify()
            self.app.lift()
            self.app.focus_force()
        except Exception:
            pass

    def _hide_from_menu(self):
        try:
            self.app.hud_var.set(False)
            self.app._toggle_hud()
        except Exception:
            self.hide()

    def _toggle_through(self):
        self.set_click_through(not self.click_through)

    def set_click_through(self, on: bool):
        self.click_through = bool(on)
        self._apply_click_through(self.click_through)
        cfg_hud = dict(self.cfg.get("hud", {}) or {})
        cfg_hud["click_through"] = self.click_through
        self.cfg.set("hud", cfg_hud)
        self.cfg.save()
        try:
            self.through_var.set(self.click_through)
        except Exception:
            pass

    def _apply_click_through(self, on: bool):
        try:
            u32 = ctypes.windll.user32
            for win in (self, self.bg):
                for hwnd in {win.winfo_id(), u32.GetParent(win.winfo_id())}:
                    if not hwnd:
                        continue
                    style = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                    if on:
                        if style & WS_EX_LAYERED:
                            u32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT)
                    elif style & WS_EX_TRANSPARENT:
                        u32.SetWindowLongW(hwnd, GWL_EXSTYLE, style & ~WS_EX_TRANSPARENT)
        except Exception:
            pass

    def _set_alpha(self, value: float):
        self.alpha = max(0.35, min(1.0, float(value)))
        try:
            self.bg.attributes("-alpha", self.alpha)
        except Exception:
            pass
        cfg_hud = dict(self.cfg.get("hud", {}) or {})
        cfg_hud["alpha"] = self.alpha
        self.cfg.set("hud", cfg_hud)
        self.cfg.save()

    def show(self):
        try:
            self.bg.deiconify()
            self.deiconify()
            self._place()
            self.bg.attributes("-topmost", True)
            self.attributes("-topmost", True)
            self.after(60, self._sync_windows)
            self.after(120, lambda: self._apply_click_through(self.click_through))
        except Exception:
            pass

    def hide(self):
        for w in (self, self.bg):
            try:
                w.withdraw()
            except Exception:
                pass

    def destroy(self):
        try:
            self.bg.destroy()
        except Exception:
            pass
        try:
            super().destroy()
        except Exception:
            pass
