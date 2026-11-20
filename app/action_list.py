"""通用动作列表：鼠标 / 键盘 / 脚本，后台规则和脚本节点共用。"""
from __future__ import annotations

import customtkinter as ctk

from core.actions import action_summary
from . import theme
from .rules_window import ActionDialog
from .widgets import AutoScrollFrame, label


class ActionList(ctk.CTkFrame):
    def __init__(self, master, actions=None, height=110, inline: bool = False):
        super().__init__(master, fg_color="transparent")
        self.actions = [dict(a) for a in (actions or []) if isinstance(a, dict)]
        self.inline = inline
        self._drag_idx = None
        self._motion_id = None
        self._release_id = None
        self.scroll = AutoScrollFrame(self, height=height)
        self.scroll.pack(fill="x", expand=True)
        self.box = self.scroll.inner
        self._refresh()
        ctk.CTkButton(self, text="+ 添加动作", width=96, height=26,
                      fg_color=theme.ACCENT, text_color="#04121f",
                      font=(theme.FONT, theme.fs(9), "bold"),
                      command=self._add).pack(anchor="w", pady=(4, 0))

    def _refresh(self):
        for w in list(self.box.winfo_children()):
            w.destroy()
        if not self.actions:
            label(self.box, "还没有动作", 9, theme.MUTED).pack(anchor="w", padx=8, pady=6)
            self.scroll.capture_wheel()
            return
        for i, a in enumerate(self.actions):
            row = ctk.CTkFrame(self.box, fg_color="transparent")
            row.pack(fill="x", pady=2)
            handle = ctk.CTkLabel(row, text="≡", width=18, font=(theme.FONT, theme.fs(12)),
                                  text_color=theme.MUTED, cursor="fleur")
            handle.pack(side="left", padx=(0, 4))
            handle.bind("<ButtonPress-1>", lambda _e, idx=i: self._drag_start(idx))
            label(row, action_summary(a), 9, theme.TEXT, anchor="w").pack(
                side="left", fill="x", expand=True)
            ctk.CTkButton(row, text="编辑", width=46, height=24, fg_color=theme.CARD2,
                          hover_color=theme.LINE, font=(theme.FONT, theme.fs(9)),
                          command=lambda idx=i: self._edit(idx)).pack(side="left", padx=2)
            ctk.CTkButton(row, text="×", width=26, height=24, fg_color=theme.CARD2,
                          hover_color=theme.DANGER, font=(theme.FONT, theme.fs(10)),
                          command=lambda idx=i: self._delete(idx)).pack(side="left")
        self.scroll.capture_wheel()

    def _drag_start(self, idx):
        self._drag_idx = idx
        top = self.winfo_toplevel()
        self._motion_id = top.bind("<B1-Motion>", self._drag_motion, add="+")
        self._release_id = top.bind("<ButtonRelease-1>", self._drag_end, add="+")

    def _drag_motion(self, e):
        if self._drag_idx is None:
            return
        rows = [w for w in self.box.winfo_children() if isinstance(w, ctk.CTkFrame)]
        if not rows:
            return
        target = len(rows) - 1
        for i, row in enumerate(rows):
            top = row.winfo_rooty()
            if e.y_root < top + row.winfo_height() / 2:
                target = i
                break
        idx = self._drag_idx
        if target != idx and 0 <= idx < len(self.actions):
            item = self.actions.pop(idx)
            self.actions.insert(max(0, min(target, len(self.actions))), item)
            self._drag_idx = target
            self._refresh()

    def _drag_end(self, _e=None):
        top = self.winfo_toplevel()
        try:
            if self._motion_id:
                top.unbind("<B1-Motion>", self._motion_id)
            if self._release_id:
                top.unbind("<ButtonRelease-1>", self._release_id)
        except Exception:
            pass
        self._motion_id = None
        self._release_id = None
        self._drag_idx = None

    def _add(self):
        ActionDialog(self, {"type": "click"}, on_save=self._append, inline=self.inline)

    def _append(self, action):
        self.actions.append(dict(action))
        self._refresh()

    def _edit(self, idx):
        if 0 <= idx < len(self.actions):
            ActionDialog(self, self.actions[idx],
                         on_save=lambda a, i=idx: self._replace(i, a),
                         inline=self.inline)

    def _replace(self, idx, action):
        if 0 <= idx < len(self.actions):
            self.actions[idx] = dict(action)
            self._refresh()

    def _delete(self, idx):
        if 0 <= idx < len(self.actions):
            self.actions.pop(idx)
            self._refresh()

    def get(self) -> list[dict]:
        return [dict(a) for a in self.actions]
