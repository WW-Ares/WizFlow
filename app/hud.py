"""迷你悬浮窗：259×76，右上角，状态胶囊 + F6/F7/F8 一键操作。"""
from __future__ import annotations

import ctypes
import tkinter as tk
import tkinter.font as tkfont

from config import Config
from core.winapi import primary_size, virtual_screen_rect
from . import theme

TRANSPARENT = "#010203"
CARD_W = 259
CARD_H = 76
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
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000


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
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=TRANSPARENT)
        try:
            self.wm_attributes("-transparentcolor", TRANSPARENT)
        except Exception:
            pass
        self.alpha = float(cfg.get("hud.alpha", 1.0) or 1.0)
        try:
            self.attributes("-alpha", max(0.5, min(1.0, self.alpha)))
        except Exception:
            pass
        self.click_through = bool(cfg.get("hud.click_through", False))

        self.f_title = tkfont.Font(family="Microsoft YaHei UI", size=-14, weight="bold")
        self.f_key = tkfont.Font(family="Consolas", size=-11, weight="bold")
        self.f_lbl = tkfont.Font(family="Microsoft YaHei UI", size=-12)
        self.f_pill = tkfont.Font(family="Microsoft YaHei UI", size=-12, weight="bold")
        self.f_small = tkfont.Font(family="Microsoft YaHei UI", size=-10)

        self.canvas = tk.Canvas(self, width=CARD_W, height=CARD_H, bg=TRANSPARENT,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        self._drag = None
        self._state = "idle"
        self._build_menu()
        self._draw()
        self._place()
        self._apply_click_through(self.click_through)
        self.canvas.bind("<Button-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._drag_end)
        self.canvas.bind("<Button-3>", self._menu_popup)
        self.bind("<Escape>", lambda _e: self.hide())

    # ------------------------------------------------------------ 绘制
    def _chip_widths(self):
        pad, gap = 10, 6
        labels = self._chip_labels()
        widths = []
        for key, text in labels:
            widths.append(pad + self.f_key.measure(key) + gap + self.f_lbl.measure(text) + pad)
        if sum(widths) + 40 > CARD_W:
            pad, gap = 7, 4
            widths = []
            for key, text in labels:
                widths.append(pad + self.f_key.measure(key) + gap + self.f_lbl.measure(text) + pad)
        return widths

    def _chip_labels(self):
        sa = str(self.cfg.get("hotkeys.start_pause", "F6") or "F6").upper()
        st = str(self.cfg.get("hotkeys.stop", "F7") or "F7").upper()
        rc = str(self.cfg.get("hotkeys.record", "F8") or "F8").upper()
        return [(sa, "启动/暂停"), (st, "停止"), (rc, "录制")]

    def _draw(self):
        c = self.canvas
        c.delete("all")
        # 卡片 + 顶部高光
        _round_rect(c, 1, 1, CARD_W - 1, CARD_H - 1, 12, fill=BG, outline=BORDER, width=1)
        c.create_line(18, 1, CARD_W - 18, 1, fill="#ffffff", stipple="gray25")

        # Logo
        c.create_polygon([15, 14, 29, 14, 32, 17, 32, 30, 29, 33, 15, 33, 12, 30, 12, 17],
                         smooth=True, splinesteps=16, fill=theme.ACCENT, outline="")
        c.create_line(17, 27, 21, 21, 25, 24, 29, 18, fill="#ffffff", width=2,
                      capstyle="round", joinstyle="round")
        c.create_text(39, 23, text="WizFlow", anchor="w", fill="#e8edf6", font=self.f_title)

        # 状态胶囊
        self._draw_pill()

        # 三个键位：等间距
        labels = self._chip_labels()
        widths = self._chip_widths()
        total = sum(widths)
        space = (CARD_W - total) / 4.0
        x = space
        self._chip_tags = []
        for i, ((key, text), w) in enumerate(zip(labels, widths)):
            tag = "chip%d" % i
            self._chip_tags.append(tag)
            y1, y2 = 40, 66
            _round_rect(c, x, y1, x + w, y2, 7, fill=CHIP_BG, outline=CHIP_LINE, width=1, tags=(tag,))
            kw = self.f_key.measure(key)
            lw = self.f_lbl.measure(text)
            inner = kw + 6 + lw
            tx = x + (w - inner) / 2
            c.create_text(tx, (y1 + y2) / 2, text=key, anchor="w", fill="#7dd3fc",
                          font=self.f_key, tags=(tag,))
            c.create_text(tx + kw + 6, (y1 + y2) / 2, text=text, anchor="w", fill="#d8e0ec",
                          font=self.f_lbl, tags=(tag,))
            c.tag_bind(tag, "<Enter>", lambda _e, t=tag: self._chip_hover(t, True))
            c.tag_bind(tag, "<Leave>", lambda _e, t=tag: self._chip_hover(t, False))
            c.tag_bind(tag, "<Button-1>", lambda _e, a=("start_pause", "stop", "record")[i]:
                       self._chip_click(a))
            x += w + space

    def _chip_hover(self, tag, on):
        if self.click_through:
            return
        try:
            self.canvas.itemconfigure(tag, fill=CHIP_HOVER if on else CHIP_BG)
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

    def _draw_pill(self):
        c = self.canvas
        c.delete("pill")
        text, fill, line, fg = STATE_STYLE.get(self._state, STATE_STYLE["idle"])
        w = 12 + 6 + 5 + self.f_pill.measure(text) + 10
        x2 = CARD_W - 12
        x1 = x2 - w
        y1, y2 = 11, 32
        _round_rect(c, x1, y1, x2, y2, (y2 - y1) / 2, fill=fill, outline=line, width=1, tags=("pill",))
        c.create_oval(x1 + 10, 18, x1 + 16, 24, fill=line, outline="", tags=("pill",))
        c.create_text(x1 + 21, (y1 + y2) / 2, text=text, anchor="w", fill=fg,
                      font=self.f_pill, tags=("pill",))

    # ------------------------------------------------------------ 状态 / 位置
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
        if state != self._state:
            self._state = state
            self._draw_pill()

    def refresh_hotkeys(self):
        self._draw()

    def _place(self):
        vx, vy, vw, vh = virtual_screen_rect()
        if bool(self.cfg.get("hud.saved", False)):
            x = int(self.cfg.get("hud.x", 0) or 0)
            y = int(self.cfg.get("hud.y", 0) or 0)
        else:
            pw, _ph = primary_size()
            x = pw - CARD_W - 16
            y = 16
        x = max(vx, min(x, vx + vw - CARD_W))
        y = max(vy, min(y, vy + vh - CARD_H))
        self.geometry("%dx%d+%d+%d" % (CARD_W, CARD_H, x, y))

    def _drag_start(self, e):
        if self.click_through:
            return
        self._drag = (e.x_root, e.y_root, self.winfo_x(), self.winfo_y())

    def _drag_move(self, e):
        if not self._drag or self.click_through:
            return
        sx, sy, wx, wy = self._drag
        self.geometry("+%d+%d" % (wx + e.x_root - sx, wy + e.y_root - sy))

    def _drag_end(self, _e):
        if not self._drag:
            return
        self._drag = None
        x, y = self.winfo_x(), self.winfo_y()
        vx, vy, vw, vh = virtual_screen_rect()
        if abs(x - vx) < 24:
            x = vx + 8
        if abs((x + CARD_W) - (vx + vw)) < 24:
            x = vx + vw - CARD_W - 8
        if abs(y - vy) < 24:
            y = vy + 8
        self.geometry("+%d+%d" % (x, y))
        self.cfg.set("hud", {"enabled": bool(self.cfg.get("hud.enabled", True)),
                             "x": int(x), "y": int(y), "saved": True,
                             "alpha": self.alpha, "click_through": self.click_through})
        self.cfg.save()

    def _build_menu(self):
        self.menu = tk.Menu(self, tearoff=0, bg="#141a24", fg="#e6edf7",
                            activebackground="#1d4ed8", activeforeground="#ffffff",
                            bd=0, font=("Microsoft YaHei UI", 9))
        self.menu.add_command(label="打开主界面", command=self._show_main)
        self.menu.add_checkbutton(label="鼠标穿透（只显示不挡点击）", command=self._toggle_through,
                                  variable=tk.BooleanVar(value=self.click_through))
        alpha_menu = tk.Menu(self.menu, tearoff=0, bg="#141a24", fg="#e6edf7",
                             activebackground="#1d4ed8", activeforeground="#ffffff")
        for v in (100, 90, 80, 70):
            alpha_menu.add_command(label="%d%%" % v, command=lambda a=v: self._set_alpha(a / 100.0))
        self.menu.add_cascade(label="透明度", menu=alpha_menu)
        self.menu.add_separator()
        self.menu.add_command(label="隐藏悬浮窗", command=self._hide_from_menu)

    def _menu_popup(self, e):
        if self.click_through:
            return
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def _hide_from_menu(self):
        try:
            self.app.hud_var.set(False)
            self.app._toggle_hud()
        except Exception:
            self.hide()

    def _show_main(self):
        try:
            self.app.deiconify()
            self.app.lift()
            self.app.focus_force()
        except Exception:
            pass

    def _toggle_through(self):
        self.set_click_through(not self.click_through)

    def set_click_through(self, on: bool):
        self.click_through = bool(on)
        self._apply_click_through(self.click_through)
        self.cfg.set("hud.click_through", self.click_through)
        self.cfg.save()
        try:
            self.menu.entryconfigure(1, label=("鼠标穿透（已开启）" if on else "鼠标穿透（只显示不挡点击）"))
        except Exception:
            pass

    def _apply_click_through(self, on: bool):
        # 只改分层窗口的 WS_EX_TRANSPARENT，不碰 LAYERED / NOACTIVATE：
        # 给 Tk 的分层窗口重新设置扩展样式会把 alpha 清成 0，整窗变透明。
        try:
            u32 = ctypes.windll.user32
            hwnds = [self.winfo_id(), u32.GetParent(self.winfo_id())]
            for hwnd in hwnds:
                if not hwnd:
                    continue
                style = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                if on:
                    if style & WS_EX_LAYERED:
                        u32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT)
                elif style & WS_EX_TRANSPARENT:
                    u32.SetWindowLongW(hwnd, GWL_EXSTYLE, style & ~WS_EX_TRANSPARENT)
            if on:
                # 重新声明一次 Tk 的分层属性，确保颜色键和 alpha 没被系统重置
                self.attributes("-alpha", self.alpha)
        except Exception:
            pass

    def _set_alpha(self, value: float):
        self.alpha = max(0.5, min(1.0, float(value)))
        try:
            self.attributes("-alpha", self.alpha)
        except Exception:
            pass
        cfg_hud = dict(self.cfg.get("hud", {}) or {})
        cfg_hud["alpha"] = self.alpha
        self.cfg.set("hud", cfg_hud)
        self.cfg.save()

    def show(self):
        try:
            self.deiconify()
            self._place()
            self.attributes("-topmost", True)
            self._apply_click_through(self.click_through)
        except Exception:
            pass

    def hide(self):
        try:
            self.withdraw()
        except Exception:
            pass
