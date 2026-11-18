"""通用控件。"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

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
    """点进去按键，直接填热键名。"""

    def __init__(self, master, var: tk.StringVar, on_change: Callable[[str], None] | None = None, **kwargs):
        super().__init__(master, textvariable=var, justify="center", **kwargs)
        self.var = var
        self.on_change = on_change
        self.bind("<KeyPress>", self._on_key)
        self.bind("<FocusIn>", lambda _e: self.configure(border_color=theme.ACCENT))
        self.bind("<FocusOut>", lambda _e: self.configure(border_color=theme.LINE))

    def _on_key(self, event):
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
    return ctk.CTkLabel(master, text=text, font=(theme.FONT, size),
                        text_color=color, **kwargs)


def entry(master, width=90, **kwargs):
    kwargs.setdefault("fg_color", theme.BG2)
    kwargs.setdefault("border_color", theme.LINE)
    kwargs.setdefault("text_color", theme.TEXT)
    kwargs.setdefault("font", (theme.MONO, theme.fs(12)))
    return ctk.CTkEntry(master, width=width, **kwargs)

