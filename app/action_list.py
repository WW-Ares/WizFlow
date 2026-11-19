"""通用动作列表：鼠标 / 键盘 / 脚本，后台规则和脚本节点共用。"""
from __future__ import annotations

import customtkinter as ctk

from core.actions import action_summary
from . import theme
from .rules_window import ActionDialog
from .widgets import label


class ActionList(ctk.CTkFrame):
    def __init__(self, master, actions=None, height=110, inline: bool = False):
        super().__init__(master, fg_color="transparent")
        self.actions = [dict(a) for a in (actions or []) if isinstance(a, dict)]
        self.inline = inline
        self.box = ctk.CTkScrollableFrame(self, fg_color=theme.BG, corner_radius=8,
                                          height=height)
        self.box.pack(fill="x", expand=True)
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
            return
        for i, a in enumerate(self.actions):
            row = ctk.CTkFrame(self.box, fg_color="transparent")
            row.pack(fill="x", pady=2)
            label(row, action_summary(a), 9, theme.TEXT, anchor="w").pack(
                side="left", fill="x", expand=True)
            ctk.CTkButton(row, text="编辑", width=46, height=24, fg_color=theme.CARD2,
                          hover_color=theme.LINE, font=(theme.FONT, theme.fs(9)),
                          command=lambda idx=i: self._edit(idx)).pack(side="left", padx=2)
            ctk.CTkButton(row, text="×", width=26, height=24, fg_color=theme.CARD2,
                          hover_color=theme.DANGER, font=(theme.FONT, theme.fs(10)),
                          command=lambda idx=i: self._delete(idx)).pack(side="left")

    def _add(self):
        ActionDialog(self, {"type": "click"}, on_save=self._append)

    def _append(self, action):
        self.actions.append(dict(action))
        self._refresh()

    def _edit(self, idx):
        if 0 <= idx < len(self.actions):
            ActionDialog(self, self.actions[idx],
                         on_save=lambda a, i=idx: self._replace(i, a))

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
