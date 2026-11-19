"""选择要抓取的窗口。"""
from __future__ import annotations

import os
from tkinter import ttk

import customtkinter as ctk

from core.capture import WindowInfo, list_windows
from . import theme
from .widgets import label
from .window_icon import apply_window_icon


class WindowPicker(ctk.CTkToplevel):
    def __init__(self, master, on_pick=None):
        super().__init__(master)
        self.on_pick = on_pick
        self.rows: list[WindowInfo] = []
        self.title("选择窗口")
        self.geometry("900x560")
        self.minsize(720, 460)
        self.configure(fg_color=theme.BG)
        self.transient(master)
        apply_window_icon(self)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 6))
        label(head, "选择要监控的窗口（可抓被遮挡的窗口）", 11, theme.TEXT).pack(side="left")
        self._btn(head, "刷新", self.refresh, 70).pack(side="right")

        body = ctk.CTkFrame(self, fg_color=theme.BG2, corner_radius=10)
        body.pack(fill="both", expand=True, padx=16, pady=6)

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Win.Treeview", background=theme.BG2, fieldbackground=theme.BG2,
                        foreground=theme.TEXT, rowheight=26, borderwidth=0,
                        font=(theme.FONT, 11))
        style.configure("Win.Treeview.Heading", background=theme.CARD2, foreground=theme.MUTED,
                        relief="flat", font=(theme.FONT, 10, "bold"), padding=(6, 3))
        style.map("Win.Treeview", background=[("selected", "#1d4ed8")],
                  foreground=[("selected", "#ffffff")])
        cols = ("title", "cls", "proc", "size")
        self.tree = ttk.Treeview(body, columns=cols, show="headings", style="Win.Treeview",
                                 selectmode="browse")
        heads = {"title": "窗口标题", "cls": "类名", "proc": "进程", "size": "客户区"}
        widths = {"title": 360, "cls": 220, "proc": 160, "size": 110}
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c], minwidth=60, anchor="w",
                             stretch=(c in ("title", "cls")))
        vs = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        vs.pack(side="right", fill="y", pady=6)
        self.tree.bind("<Double-1>", lambda _e: self._pick())

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(4, 14))
        self.hint = label(bottom, "", 9)
        self.hint.pack(side="left")
        ctk.CTkButton(bottom, text="取消", width=88, height=30, fg_color=theme.CARD2,
                      hover_color=theme.LINE, font=(theme.FONT, 12),
                      command=self.destroy).pack(side="right")
        ctk.CTkButton(bottom, text="选择", width=104, height=30, fg_color=theme.ACCENT,
                      text_color="#04121f", font=(theme.FONT, 12),
                      command=self._pick).pack(side="right", padx=8)
        self.refresh()
        self.after(120, self._focus)

    def _btn(self, master, text, cmd, width=70):
        return ctk.CTkButton(master, text=text, command=cmd, width=width, height=28,
                             fg_color=theme.CARD2, hover_color=theme.LINE,
                             font=(theme.FONT, 11))

    def _focus(self):
        try:
            self.lift()
            self.focus_force()
            self.grab_set()
        except Exception:
            pass

    def refresh(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.rows = list_windows(exclude_pid=os.getpid())
        for idx, w in enumerate(self.rows):
            self.tree.insert("", "end", iid=str(idx),
                             values=(w.title, w.class_name, w.process,
                                     "%dx%d" % (w.width, w.height)))
        self.hint.configure(text="共 %d 个可见窗口；双击或选中后点「选择」" % len(self.rows))

    def _pick(self):
        sel = self.tree.selection()
        if not sel:
            self.hint.configure(text="请先选中一个窗口", text_color=theme.WARN)
            return
        try:
            w = self.rows[int(sel[0])]
        except Exception:
            return
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        if self.on_pick:
            self.on_pick(w)
