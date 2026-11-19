"""通用控件。"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

from core.keygrab import MOD_VKS, KeyGrabber
from . import theme


class Card(ctk.CTkFrame):
    def __init__(self, master, title: str = "", subtitle: str = "", **kwargs):
        kwargs.setdefault("fg_color", theme.CARD)
        kwargs.setdefault("corner_radius", 14)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", theme.LINE)
        super().__init__(master, **kwargs)
        self.body = self
        if title:
            head = ctk.CTkFrame(self, fg_color="transparent")
            head.pack(fill="x", padx=18, pady=(14, 0))
            ctk.CTkLabel(head, text=title, font=(theme.FONT, theme.fs(15), "bold"),
                         text_color=theme.TEXT).pack(side="left")
            if subtitle:
                ctk.CTkLabel(head, text=subtitle, font=(theme.FONT, theme.fs(10)),
                             text_color=theme.MUTED).pack(side="left", padx=(10, 0), pady=(3, 0))
            self.body = ctk.CTkFrame(self, fg_color="transparent")
            self.body.pack(fill="both", expand=True, padx=18, pady=(10, 14))
        else:
            self.body = self


class Pill(ctk.CTkLabel):
    def __init__(self, master, text: str, color: str = theme.OK, **kwargs):
        kwargs.setdefault("fg_color", color)
        kwargs.setdefault("text_color", "#04121f")
        kwargs.setdefault("corner_radius", 999)
        kwargs.setdefault("font", (theme.FONT, theme.fs(11), "bold"))
        kwargs.setdefault("padx", 0)
        super().__init__(master, text=text, **kwargs)
        self._base = color

    def set(self, text: str, color: str | None = None):
        self.configure(text=text, fg_color=color or self._base)


class KeyCaptureEntry(ctk.CTkEntry):
    """点进去按键，直接填热键名；聚焦期间拦截系统快捷键。"""

    def __init__(self, master, var: tk.StringVar, on_change: Callable[[str], None] | None = None, **kwargs):
        super().__init__(master, textvariable=var, justify="center", **kwargs)
        self.var = var
        self.on_change = on_change
        self.grabber: KeyGrabber | None = None
        self._poll_id = None
        self._main = 0
        self._result = ""
        self.bind("<FocusIn>", self._start)
        self.bind("<FocusOut>", lambda _e: self._stop())
        self.bind("<KeyPress>", self._on_key)

    def _start(self, _e=None):
        self.configure(border_color=theme.ACCENT)
        self._stop()
        g = KeyGrabber()
        if g.start():
            self.grabber = g
            self._main = 0
            self._result = ""
            self._poll_id = self.after(25, self._poll)
        else:
            self.grabber = None

    def _poll(self):
        if self.grabber is None:
            return
        for kind, vk, combo in self.grabber.drain():
            if vk == 0x1B and kind == "down":
                self._stop()
                self.configure(border_color=theme.LINE)
                return
            if kind == "down":
                if vk in MOD_VKS:
                    continue
                self._main = vk
                self._result = combo.upper()
                self.var.set(self._result)
            elif kind == "up" and self._main == vk and self._result:
                self.var.set(self._result)
                if self.on_change:
                    self.on_change(self._result)
                self._stop()
                self.configure(border_color=theme.LINE)
                return
        self._poll_id = self.after(25, self._poll)

    def _stop(self):
        if self._poll_id:
            try:
                self.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None
        if self.grabber is not None:
            self.grabber.stop()
            self.grabber = None

    # 钩子不可用时的兜底
    def _on_key(self, event):
        if self.grabber is not None:
            return "break"
        key = event.keysym
        name = None
        if key.startswith("F") and key[1:].isdigit():
            name = key.upper()
        elif len(key) == 1 and key.isprintable():
            name = key.upper()
        else:
            named = {
                "Escape": "esc", "space": "space", "Return": "enter", "Tab": "tab",
                "BackSpace": "backspace", "Insert": "insert", "Delete": "delete",
                "Home": "home", "End": "end", "Prior": "pageup", "Next": "pagedown",
                "Up": "up", "Down": "down", "Left": "left", "Right": "right",
                "Pause": "pause", "Print": "printscreen", "Caps_Lock": "capslock",
            }
            name = named.get(key)
        if name:
            name = name.upper()
            self.var.set(name)
            if self.on_change:
                self.on_change(name)
        return "break"


class ImagePanel(ctk.CTkLabel):
    """固定尺寸预览图，没图时显示占位文字。"""

    def __init__(self, master, size=(320, 180), text="暂无预览", **kwargs):
        super().__init__(master, text=text, width=size[0], height=size[1],
                         fg_color=theme.BG2, corner_radius=10,
                         text_color=theme.MUTED, font=(theme.FONT, theme.fs(11)), **kwargs)
        self.size = size
        self._img = None

    def show_pil(self, pil):
        if pil is None:
            self.clear()
            return
        from PIL import Image
        iw, ih = pil.size
        scale = min(self.size[0] / max(1, iw), self.size[1] / max(1, ih), 1.0)
        if scale < 1.0:
            pil = pil.resize((max(1, int(iw * scale)), max(1, int(ih * scale))), Image.LANCZOS)
        self._img = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
        self.configure(image=self._img, text="")

    def clear(self, text="暂无预览"):
        self._img = None
        self.configure(image=None, text=text)


def label(master, text: str, size=11, color=theme.MUTED, **kwargs):
    return ctk.CTkLabel(master, text=text, font=(theme.FONT, theme.fs(size)),
                        text_color=color, **kwargs)


def entry(master, width=90, **kwargs):
    kwargs.setdefault("fg_color", theme.BG2)
    kwargs.setdefault("border_color", theme.LINE)
    kwargs.setdefault("text_color", theme.TEXT)
    kwargs.setdefault("font", (theme.MONO, theme.fs(12)))
    return ctk.CTkEntry(master, width=width, **kwargs)

class ScrollPage(ctk.CTkFrame):
    """页面滚动容器：只有内容超出时才显示横/竖滚动条。"""

    MIN_WIDTH = 1000

    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, highlightthickness=0, bg=theme.BG)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar = ctk.CTkScrollbar(self, orientation="vertical",
                                     command=self.canvas.yview)
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar = ctk.CTkScrollbar(self, orientation="horizontal",
                                     command=self.canvas.xview)
        self.hbar.grid(row=1, column=0, sticky="ew")
        self.vbar.grid_remove()
        self.hbar.grid_remove()
        self.canvas.configure(yscrollcommand=self._yset,
                              xscrollcommand=self._xset)
        self.inner = ctk.CTkFrame(self.canvas, fg_color="transparent")
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self._job = None
        self._bars_job = None
        self._syncing = False
        self._last_sig = None
        self.canvas.bind("<Configure>", self._schedule)
        self.inner.bind("<Configure>", self._schedule)
        self.canvas.bind_all("<MouseWheel>", self._wheel, add="+")
        self.after(80, self._sync)

    def _schedule(self, _e=None):
        if self._job is not None:
            return
        self._job = self.after(50, self._sync)

    def _sync(self):
        self._job = None
        if self._syncing:
            return
        self._syncing = True
        try:
            cw = max(1, self.canvas.winfo_width())
            width = max(self.MIN_WIDTH, cw)
            try:
                cur = int(self.inner.cget("width"))
            except Exception:
                cur = 0
            if cur != width:
                self.inner.configure(width=width)
            self.update_idletasks()
            height = max(1, self.inner.winfo_reqheight())
            try:
                cur_w = int(self.canvas.itemcget(self.win, "width") or 0)
                if cur_w != width:
                    self.canvas.itemconfigure(self.win, width=width)
            except Exception:
                pass
            self.canvas.configure(scrollregion=(0, 0, width, height))
            self._apply_bars()
        except Exception:
            pass
        finally:
            self._syncing = False

    def fill_height(self):
        """把页面内容高度补到视口高度，让卡片铺满。"""
        try:
            ch = max(1, self.canvas.winfo_height())
            h = ch
            self.canvas.itemconfigure(self.win, width=self.canvas.winfo_width(), height=h)
            self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), h))
        except Exception:
            pass

    def _xset(self, first, last):
        self.hbar.set(first, last)
        self._schedule_bars()

    def _yset(self, first, last):
        self.vbar.set(first, last)
        self._schedule_bars()

    def _schedule_bars(self):
        if self._bars_job is not None:
            return
        self._bars_job = self.after_idle(self._apply_bars)

    def _apply_bars(self):
        self._bars_job = None
        try:
            self.hbar.grid_remove()
            self.vbar.grid_remove()
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
        except Exception:
            pass

    def _wheel(self, e):
        return

def autohide_scrollbar(scroll_frame):
    """CTkScrollableFrame 的滚动条：内容超出才显示。"""
    def update(*_a):
        try:
            sb = scroll_frame._scrollbar
            canvas = scroll_frame._parent_canvas
            bbox = canvas.bbox("all") or (0, 0, 0, 0)
            need = (bbox[3] - bbox[1]) > canvas.winfo_height() + 2
            if need:
                sb.grid()
            else:
                sb.grid_remove()
                canvas.yview_moveto(0)
        except Exception:
            pass
    scroll_frame.bind("<Configure>", lambda _e: update(), add="+")
    try:
        scroll_frame._parent_canvas.bind("<Configure>", lambda _e: update(), add="+")
    except Exception:
        pass
    scroll_frame.after(120, update)
    scroll_frame.after(450, update)
    return update

class AutoScrollFrame(ctk.CTkFrame):
    """竖向滚动容器：内容超出才显示滚动条，隐藏时不占宽度。"""

    def __init__(self, master, height=200, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, highlightthickness=0, bg=theme.BG2, height=height)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar = ctk.CTkScrollbar(self, orientation="vertical",
                                     command=self.canvas.yview)
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.vbar.grid_remove()
        self._bar_visible = False
        self._hide_job = None
        self.canvas.configure(yscrollcommand=self._yset)
        self.inner = ctk.CTkFrame(self.canvas, fg_color="transparent")
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self._job = None
        self.inner.bind("<Configure>", self._schedule)
        self.canvas.bind("<Configure>", self._canvas_cfg)
        self.canvas.bind_all("<MouseWheel>", self._wheel, add="+")
        self.after(80, self._sync)

    def _canvas_cfg(self, e):
        try:
            self.canvas.itemconfigure(self.win, width=e.width)
        except Exception:
            pass
        self._schedule()

    def _schedule(self, _e=None):
        if self._job is not None:
            return
        self._job = self.after(50, self._sync)

    def _sync(self):
        self._job = None
        try:
            h = self.inner.winfo_reqheight()
            self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), h))
            self._update_bar()
        except Exception:
            pass

    def _yset(self, first, last):
        self.vbar.set(first, last)
        self.after_idle(self._update_bar)

    def _update_bar(self):
        """按内容高度与可视高度比较决定滚动条显隐；到底后保持位置，不回弹顶部。"""
        try:
            ch = self.canvas.winfo_height()
            bbox = self.canvas.bbox("all")
            content_h = self.inner.winfo_reqheight()
            if bbox:
                content_h = max(content_h, bbox[3] - bbox[1])
            need = content_h > ch + 1
            if need:
                if self._hide_job is not None:
                    try:
                        self.after_cancel(self._hide_job)
                    except Exception:
                        pass
                    self._hide_job = None
                if not self._bar_visible:
                    self._bar_visible = True
                    self.vbar.grid()
            elif self._bar_visible and self._hide_job is None:
                # 内容重绘期间会短暂变矮，延迟确认，避免滚动条闪烁和视图跳回顶部
                self._hide_job = self.after(180, self._hide_bar_if_fit)
        except Exception:
            pass

    def _hide_bar_if_fit(self):
        self._hide_job = None
        try:
            if self.inner.winfo_reqheight() <= self.canvas.winfo_height() + 1:
                self._bar_visible = False
                self.vbar.grid_remove()
        except Exception:
            pass

    def _wheel(self, e):
        try:
            if not self.winfo_ismapped():
                return
            # 内容未超出可视区时禁止滚动，避免整块内容被滚出视野
            if self.inner.winfo_reqheight() <= self.canvas.winfo_height() + 1:
                self.canvas.yview_moveto(0)
                return
            x, y = self.winfo_pointerxy()
            cx, cy = self.canvas.winfo_rootx(), self.canvas.winfo_rooty()
            if not (cx <= x <= cx + self.canvas.winfo_width()
                    and cy <= y <= cy + self.canvas.winfo_height()):
                return
            step = int(-e.delta / 120) or (-1 if e.delta > 0 else 1)
            self.canvas.yview_scroll(step, "units")
        except Exception:
            pass
