"""录制 / 回放页。"""
from __future__ import annotations

import datetime
import copy
import json
import os
import time
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from config import LOGS_DIR, SCRIPTS_DIR
from . import theme
from core.hotkeys import parse_hotkey
from core.log import log
from core.script import ScriptDoc, describe_event, list_scripts, new_script_path
from .event_editor import EventEditorDialog
from .widgets import Card, Pill, entry, label


DRAFT_FILE = LOGS_DIR / "draft_autosave.json"


class RecordPageMixin:
    def _build_record_page(self):
        outer, page = self._new_page()
        self.pages["record"] = outer
        self._editing = False
        self._draft: list[dict] | None = None
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(page, fg_color=theme.CARD, corner_radius=12,
                           border_width=1, border_color=theme.LINE)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        inner = ctk.CTkFrame(top, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=10)
        label(inner, "当前脚本", 11).pack(side="left", padx=(0, 8))
        self.script_var = tk.StringVar(value="（未选择）")
        self.script_menu = ctk.CTkOptionMenu(inner, variable=self.script_var, values=["（未选择）"],
                                             width=250, height=32, fg_color=theme.CARD2,
                                             button_color=theme.LINE, button_hover_color=theme.ACCENT,
                                             dropdown_fg_color=theme.CARD2, font=(theme.FONT, theme.fs(11)),
                                             command=self._on_script_selected)
        self.script_menu.pack(side="left")
        self._btn(inner, "新建", self._new_script, width=64).pack(side="left", padx=(10, 0))
        self._btn(inner, "保存", self._save_any, width=64, color=theme.ACCENT,
                  text_color="#04121f").pack(side="left", padx=6)
        self._btn(inner, "刷新", self._refresh_scripts, width=64).pack(side="left")
        self._btn(inner, "目录", self._open_scripts_dir, width=64).pack(side="left", padx=6)
        self.event_count_label = label(inner, "0 个事件", 10)
        self.event_count_label.pack(side="right")

        cards = ctk.CTkFrame(page, fg_color="transparent")
        cards.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        cards.grid_columnconfigure(0, weight=1, uniform="cards")
        cards.grid_columnconfigure(1, weight=1, uniform="cards")

        rec = Card(cards, "录制", "全局键鼠钩子")
        rec.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.rec_pill = Pill(rec.body, "待机", theme.MUTED)
        self.rec_pill.pack(anchor="w", pady=(0, 8))
        self.btn_record = ctk.CTkButton(rec.body, text="●  开始录制    F8", height=46,
                                        fg_color=theme.DANGER, hover_color="#dc2626",
                                        font=(theme.FONT, theme.fs(14), "bold"), command=self._toggle_record)
        self.btn_record.pack(fill="x")
        self.btn_record_stop = ctk.CTkButton(rec.body, text="停止录制", height=32,
                                             fg_color=theme.CARD2, hover_color=theme.LINE,
                                             font=(theme.FONT, theme.fs(11)), command=self._stop_record)
        self.btn_record_stop.pack(fill="x", pady=(8, 10))

        self.traj_var = tk.BooleanVar(value=bool(self.cfg.get("record_trajectory", False)))
        ctk.CTkSwitch(rec.body, text="录制鼠标轨迹", variable=self.traj_var,
                      font=(theme.FONT, theme.fs(11)), progress_color=theme.ACCENT,
                      command=self._on_traj_change).pack(anchor="w")
        row = ctk.CTkFrame(rec.body, fg_color="transparent")
        row.pack(fill="x", pady=(6, 0))
        label(row, "轨迹采样间隔", 10).pack(side="left")
        self.traj_ms_var = tk.StringVar(value=str(self.cfg.get("trajectory_interval_ms", 16)))
        e = entry(row, width=62, textvariable=self.traj_ms_var)
        e.pack(side="left", padx=6)
        e.bind("<FocusOut>", lambda _e: self._on_traj_change())
        label(row, "ms（越小越密集）", 9).pack(side="left")
        self.rec_stats = label(rec.body, "轨迹关闭时只记录点击点，脚本更短、回放更稳", 9)
        self.rec_stats.pack(anchor="w", pady=(8, 0))

        play = Card(cards, "回放", "脚本执行 / 监控节点")
        play.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        head = ctk.CTkFrame(play.body, fg_color="transparent")
        head.pack(fill="x")
        label(head, "循环次数", 10).pack(side="left")
        self.loop_var = tk.StringVar(value=str(self.cfg.get("loop_count", 1)))
        entry(head, width=54, textvariable=self.loop_var).pack(side="left", padx=(6, 14))
        label(head, "速度", 10).pack(side="left")
        self.speed_var = tk.StringVar(value="1x")
        self.speed_menu = ctk.CTkOptionMenu(head, variable=self.speed_var,
                                            values=["0.5x", "1x", "1.5x", "2x", "3x"],
                                            width=76, height=28, fg_color=theme.CARD2,
                                            button_color=theme.LINE, font=(theme.MONO, theme.fs(10)))
        self.speed_menu.pack(side="left", padx=6)
        label(head, "0 = 无限循环", 9).pack(side="left", padx=(10, 0))

        btns = ctk.CTkFrame(play.body, fg_color="transparent")
        btns.pack(fill="x", pady=(10, 8))
        self.btn_play = ctk.CTkButton(btns, text="▶  启动    F6", height=40,
                                      fg_color=theme.OK, hover_color="#16a34a",
                                      text_color="#04121f", font=(theme.FONT, theme.fs(13), "bold"),
                                      command=self._toggle_play)
        self.btn_play.pack(side="left", fill="x", expand=True)
        self._btn(btns, "暂停", self._pause_play, width=64).pack(side="left", padx=6)
        self._btn(btns, "停止", self._stop_play, width=64, color=theme.DANGER,
                  text_color="white").pack(side="left")

        self.play_pill = Pill(play.body, "空闲", theme.MUTED)
        self.play_pill.pack(anchor="w", pady=(0, 6))
        self.progress = ctk.CTkProgressBar(play.body, height=8, progress_color=theme.ACCENT,
                                           fg_color=theme.BG2)
        self.progress.pack(fill="x")
        self.progress.set(0)
        self.play_log = ctk.CTkTextbox(play.body, height=54, fg_color=theme.BG2,
                                       text_color=theme.MUTED, font=(theme.MONO, theme.fs(9)),
                                       corner_radius=8)
        self.play_log.pack(fill="x", pady=(8, 0))
        self.play_log.insert("end", "等待启动…")
        self.play_log.configure(state="disabled")

        ev_card = Card(page, "事件列表", "默认只读 · 点「编辑事件」后修改需保存")
        ev_card.grid(row=2, column=0, sticky="nsew")
        bar = ctk.CTkFrame(ev_card.body, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 6))
        self.btn_edit_mode = self._btn(bar, "编辑事件", self._enter_edit, width=96,
                                       color=theme.ACCENT, text_color="#04121f")
        self.btn_edit_mode.pack(side="left")
        self.edit_bar = ctk.CTkFrame(bar, fg_color="transparent")
        self._btn(self.edit_bar, "＋ 新增节点", self._add_node, width=96,
                  color=theme.ACCENT, text_color="#04121f").pack(side="left")
        self._btn(self.edit_bar, "编辑", self._edit_event, width=56).pack(side="left", padx=5)
        self._btn(self.edit_bar, "删除", self._delete_events, width=56,
                  color=theme.DANGER, text_color="white").pack(side="left")
        self._btn(self.edit_bar, "复制", self._copy_events, width=56).pack(side="left", padx=5)
        self._btn(self.edit_bar, "上移", self._move_up, width=56).pack(side="left")
        self._btn(self.edit_bar, "下移", self._move_down, width=56).pack(side="left", padx=5)
        self._btn(self.edit_bar, "恢复更改", self._revert_edit, width=80).pack(side="left")
        self._btn(self.edit_bar, "保存", self._save_edit, width=64, color=theme.OK,
                  text_color="#04121f").pack(side="left", padx=5)
        self._btn(self.edit_bar, "退出编辑", self._exit_edit, width=76).pack(side="left")
        self.event_hint = label(ev_card.body, "点「编辑事件」后可修改节点", 9)
        self.event_hint.pack(anchor="w", pady=(0, 4))

        tables = ctk.CTkFrame(ev_card.body, fg_color=theme.BG2, corner_radius=8)
        tables.pack(fill="both", expand=True, pady=(2, 0))
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("WF.Treeview", background=theme.BG2, fieldbackground=theme.BG2,
                        foreground=theme.TEXT, rowheight=max(24, theme.fs(13)),
                        borderwidth=0, relief="flat", font=(theme.FONT, theme.fs(10)),
                        bordercolor=theme.BG2, lightcolor=theme.BG2, darkcolor=theme.BG2)
        style.configure("WF.Treeview.Heading", background=theme.CARD2, foreground=theme.MUTED,
                        relief="flat", font=(theme.FONT, theme.fs(10), "bold"), padding=(6, 4),
                        bordercolor=theme.CARD2, lightcolor=theme.CARD2, darkcolor=theme.CARD2)
        style.map("WF.Treeview", background=[("selected", "#1d4ed8")],
                  foreground=[("selected", "#ffffff")])
        style.map("WF.Treeview.Heading", background=[("active", theme.LINE)])
        style.layout("WF.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        for base in ("Treeview", "Treeview.Heading"):
            try:
                style.configure(base, lightcolor=theme.BG2, darkcolor=theme.BG2,
                                bordercolor=theme.BG2, borderwidth=0, relief="flat")
            except Exception:
                pass
        style.configure("WF.Vertical.TScrollbar", background=theme.CARD2, troughcolor=theme.BG2,
                        bordercolor=theme.BG2, arrowcolor=theme.MUTED,
                        darkcolor=theme.CARD2, lightcolor=theme.CARD2, relief="flat",
                        borderwidth=0, arrowsize=12)
        style.map("WF.Vertical.TScrollbar", background=[("active", theme.LINE)])
        cols = ("idx", "delay", "kind", "action", "params", "note")
        heads = {"idx": "序号", "delay": "延迟(ms)", "kind": "类型",
                 "action": "动作", "params": "坐标 / 参数", "note": "备注"}
        widths = {"idx": 64, "delay": 84, "kind": 80, "action": 160, "params": 330, "note": 160}
        self.event_tree = ttk.Treeview(tables, columns=cols, show="headings",
                                       style="WF.Treeview", selectmode="extended")
        for c in cols:
            self.event_tree.heading(c, text=heads[c])
            self.event_tree.column(c, width=widths[c], minwidth=48, anchor="w",
                                   stretch=(c in ("params", "note")))
        vs = ttk.Scrollbar(tables, orient="vertical", command=self.event_tree.yview,
                           style="WF.Vertical.TScrollbar")
        self.event_tree.configure(yscrollcommand=vs.set)
        self.event_tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.event_tree.tag_configure("even", background=theme.BG2)
        self.event_tree.tag_configure("odd", background="#111c30")
        self.event_tree.bind("<Double-1>", lambda _e: self._edit_event())
        self.event_tree.bind("<Delete>", lambda _e: self._delete_events())
        self.event_tree.bind("<Control-a>", self._select_all)
        self.event_tree.bind("<Control-A>", self._select_all)
        self.event_tree.bind("<<TreeviewSelect>>", lambda _e: self._update_hint())
        self.after(15000, self._autosave_tick)
        self.after(1400, self._maybe_restore_draft)

    # ------------------------------------------------------------ 脚本
    def _refresh_scripts(self, select: Path | None = None):
        files = list_scripts()
        names = [p.name for p in files] or ["（未选择）"]
        self.script_menu.configure(values=names)
        target = select or self.script_path
        if target and target.name in names:
            self.script_var.set(target.name)
        elif self.script_var.get() not in names:
            self.script_var.set(names[0])
        self._script_files = {p.name: p for p in files}
        if hasattr(self, "_refresh_monitor_script_menu"):
            self._refresh_monitor_script_menu()

    def _on_script_selected(self, name: str):
        if name == "（未选择）":
            return
        if getattr(self, "_editing", False):
            if not messagebox.askyesno("切换脚本", "事件列表正在编辑，切换脚本会丢弃未保存的修改。是否继续？"):
                self.script_var.set(self.script_path.name if self.script_path else "（未选择）")
                return
            self._discard_edit()
        path = getattr(self, "_script_files", {}).get(name)
        if not path:
            return
        try:
            doc = ScriptDoc.load(path)
        except Exception as exc:
            self._toast("脚本加载失败: %s" % exc, "error")
            return
        self.script_path = path
        self.events = doc.events
        self._rebuild_events()
        self._toast("已加载 %s（%d 个事件）" % (path.name, len(self.events)), "ok")
        self.cfg.set("last_script", str(path))
        self.cfg.save()

    def _load_last_script(self):
        last = self.cfg.get("last_script", "")
        if last and Path(last).exists():
            try:
                doc = ScriptDoc.load(last)
                self.script_path = Path(last)
                self.events = doc.events
                self._rebuild_events()
                self._refresh_scripts(select=self.script_path)
            except Exception:
                pass

    def _new_script(self):
        if getattr(self, "_editing", False):
            if not messagebox.askyesno("新建脚本", "事件列表正在编辑，未保存的修改将丢弃。是否继续？"):
                return
            self._discard_edit()
        if self.recording:
            self._stop_record()
        if self.events and not self.script_path:
            if not messagebox.askyesno("新建脚本", "当前录制的脚本尚未保存，继续将丢弃。是否继续？"):
                return
        if self.script_path and self.events:
            self._save_script(silent=True)
        self.script_path = None
        self.events = []
        self._rebuild_events()
        self.script_var.set("（未选择）")
        self._toast("已新建空白脚本，按 F8 开始录制", "info")

    def _save_any(self) -> bool:
        if getattr(self, "_editing", False):
            return self._save_edit()
        return self._save_script()

    def _save_script(self, silent: bool = False, allow_empty: bool = False) -> bool:
        if not self.events and not allow_empty:
            self._toast("没有可保存的事件", "warn")
            return False
        path = self.script_path or new_script_path()
        try:
            ScriptDoc(self.events, path).save()
        except Exception as exc:
            self._toast("保存失败: %s" % exc, "error")
            return False
        self.script_path = path
        self.cfg.set("last_script", str(path))
        self.cfg.save()
        self._refresh_scripts(select=path)
        if not silent:
            self._toast("已保存 %s" % path.name, "ok")
        return True

    def _delete_last_event(self):
        if self.events:
            self.events.pop()
            self._rebuild_events()

    def _clear_events(self):
        if not self._require_edit():
            return
        evs = self._current_events()
        if not evs:
            return
        if not messagebox.askyesno("清空", "确定清空当前脚本的全部事件？"):
            return
        evs.clear()
        self._rebuild_events()
        self._toast("已清空（未保存）", "warn")

    def _rebuild_events(self):
        try:
            for item in self.event_tree.get_children():
                self.event_tree.delete(item)
        except Exception:
            pass
        for i, ev in enumerate(self._current_events()):
            self._insert_tree_row(i, ev)
        self._update_counts()

    def _insert_tree_row(self, idx: int, ev: dict):
        d = describe_event(ev)
        try:
            self.event_tree.insert("", "end", iid=str(idx), values=(
                "%04d" % (idx + 1), "%g" % d["delay"], d["kind"], d["action"],
                d["params"], d["note"]), tags=("odd" if idx % 2 else "even",))
        except Exception:
            pass

    def _current_events(self) -> list[dict]:
        if getattr(self, "_editing", False) and self._draft is not None:
            return self._draft
        return self.events

    def _add_event(self, ev: dict):
        if getattr(self, "_editing", False):
            self._toast("编辑中不能录制，请先保存或退出编辑", "warn")
            return
        self.events.append(ev)
        self._insert_tree_row(len(self.events) - 1, ev)
        self._update_counts()
        try:
            iid = str(len(self.events) - 1)
            self.event_tree.see(iid)
            self.event_tree.selection_set(iid)
        except Exception:
            pass

    def _selected(self) -> list[int]:
        try:
            return sorted(int(i) for i in self.event_tree.selection())
        except Exception:
            return []

    def _select_all(self, _e=None):
        try:
            self.event_tree.selection_set(self.event_tree.get_children())
        except Exception:
            pass
        return "break"

    def _update_hint(self):
        n = len(self._selected())
        try:
            if not getattr(self, "_editing", False):
                self.event_hint.configure(text="点「编辑事件」后可修改节点")
            elif n:
                self.event_hint.configure(text="编辑中 · 已选 %d 个 · 修改需点保存" % n)
            else:
                self.event_hint.configure(text="编辑中 · 修改需点「保存」，可点「恢复更改」撤销")
        except Exception:
            pass

    def _update_counts(self):
        n = len(self._current_events())
        suffix = "（未保存）" if getattr(self, "_editing", False) else ""
        self.event_count_label.configure(text="%d 个事件%s" % (n, suffix))
        self.count_label.configure(text="事件 %d%s" % (n, suffix))
        self._update_hint()

    def _node_dialog(self, ev=None):
        sdir = self.script_path.parent if self.script_path else SCRIPTS_DIR
        dlg = EventEditorDialog(self, ev, script_dir=sdir)
        self.wait_window(dlg)
        return dlg.result

    # ------------------------------------------------------------ 编辑模式
    def _enter_edit(self):
        if getattr(self, "_editing", False):
            return
        if self.recording:
            self._stop_record()
        self._draft = copy.deepcopy(self.events)
        self._editing = True
        self.btn_edit_mode.pack_forget()
        self.edit_bar.pack(side="left", padx=(6, 0))
        self._rebuild_events()
        self._toast("已进入编辑模式：修改只在点「保存」后生效", "info")

    def _leave_edit_ui(self):
        try:
            self.edit_bar.pack_forget()
            self.btn_edit_mode.pack(side="left")
        except Exception:
            pass

    def _require_edit(self) -> bool:
        if not getattr(self, "_editing", False):
            self._toast("请先点「编辑事件」再修改", "warn")
            return False
        return True

    def _save_edit(self, silent: bool = False) -> bool:
        if not getattr(self, "_editing", False):
            if not silent:
                self._toast("当前不在编辑模式", "warn")
            return False
        old = self.events
        self.events = copy.deepcopy(self._draft or [])
        if not self._save_script(silent=True, allow_empty=True):
            self.events = old
            return False
        self._editing = False
        self._draft = None
        self._leave_edit_ui()
        self._rebuild_events()
        self._clear_draft_file()
        if not silent:
            self._toast("已保存 %d 个事件" % len(self.events), "ok")
        return True

    def _revert_edit(self):
        if not self._require_edit():
            return
        self._draft = copy.deepcopy(self.events)
        self._rebuild_events()
        self._toast("已恢复到最后一次保存的内容", "ok")

    def _discard_edit(self):
        self._editing = False
        self._draft = None
        self._leave_edit_ui()
        self._rebuild_events()
        self._clear_draft_file()

    def _clear_draft_file(self):
        try:
            DRAFT_FILE.unlink()
        except Exception:
            pass

    def _autosave_tick(self):
        try:
            if getattr(self, "_editing", False) and self._draft is not None:
                payload = {
                    "script": str(self.script_path or ""),
                    "events": self._draft,
                    "saved_at": time.time(),
                }
                tmp = DRAFT_FILE.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                               encoding="utf-8")
                os.replace(tmp, DRAFT_FILE)
        except Exception as exc:
            log("草稿自动保存失败: %s" % exc, "WARN")
        self.after(15000, self._autosave_tick)

    def _maybe_restore_draft(self):
        try:
            if not DRAFT_FILE.exists():
                return
            payload = json.loads(DRAFT_FILE.read_text(encoding="utf-8"))
            events = payload.get("events") or []
            if not isinstance(events, list) or not events:
                self._clear_draft_file()
                return
            stamp = datetime.datetime.fromtimestamp(float(payload.get("saved_at", 0)))
            if not messagebox.askyesno(
                    "恢复编辑草稿",
                    "检测到 %s 的未保存编辑草稿（%d 个事件）。\n是否恢复并继续编辑？"
                    % (stamp.strftime("%m-%d %H:%M"), len(events))):
                self._clear_draft_file()
                return
            script = str(payload.get("script", "") or "")
            if script and Path(script).exists():
                try:
                    doc = ScriptDoc.load(Path(script))
                    self.script_path = Path(script)
                    self.events = doc.events
                    self._refresh_scripts(select=self.script_path)
                except Exception:
                    pass
            self._draft = copy.deepcopy(events)
            self._editing = True
            self.btn_edit_mode.pack_forget()
            self.edit_bar.pack(side="left", padx=(6, 0))
            self._rebuild_events()
            self._toast("已恢复编辑草稿（未保存）", "ok")
        except Exception as exc:
            log("恢复草稿失败: %s" % exc, "WARN")

    def _exit_edit(self):
        if not self._require_edit():
            return
        if not messagebox.askyesno("退出编辑", "未保存的修改将全部丢弃，确定退出编辑？"):
            return
        self._discard_edit()
        self._toast("已退出编辑，未保存的修改已丢弃", "info")

    def _add_node(self):
        if not self._require_edit():
            return
        events = self._node_dialog(None)
        if not events:
            return
        evs = self._current_events()
        pos = self._selected()
        at = (pos[-1] + 1) if pos else len(evs)
        evs[at:at] = events
        self._rebuild_events()
        self._select_range(at, at + len(events) - 1)
        self._toast("已新增 %d 个节点（未保存）" % len(events), "ok")

    def _select_range(self, a: int, b: int):
        try:
            kids = self.event_tree.get_children()
            for i in range(max(0, a), min(b, len(kids) - 1) + 1):
                self.event_tree.selection_add(kids[i])
            self.event_tree.see(kids[min(b, len(kids) - 1)])
        except Exception:
            pass

    def _edit_event(self):
        if not self._require_edit():
            return
        sel = self._selected()
        if not sel:
            self._toast("请先选中一个节点", "warn")
            return
        if len(sel) > 1:
            self._toast("一次只能编辑一个节点（可批量删除、复制、移动）", "warn")
            return
        i = sel[0]
        evs = self._current_events()
        ev = evs[i]
        typ = str(ev.get("type", "")).lower()
        if typ == "monitor_group":
            self._show_page("monitor")
            if hasattr(self, "_open_rules_window"):
                try:
                    self._open_rules_window()
                except Exception:
                    pass
            self._toast("多图标监控组请在「区域监控」页编辑", "info")
            return
        if typ == "monitor":
            events = self._node_dialog(ev)
            if not events:
                return
            evs[i:i + 1] = events
            self._rebuild_events()
            self._select_range(i, i + len(events) - 1)
            self._toast("监控节点已更新（未保存）", "ok")
            return
        events = self._node_dialog(ev)
        if not events:
            return
        evs[i:i + 1] = events
        self._rebuild_events()
        self._select_range(i, i + len(events) - 1)
        self._toast("节点已更新（未保存）", "ok")

    def _delete_events(self):
        if not self._require_edit():
            return
        sel = self._selected()
        if not sel:
            self._toast("请先选中要删除的节点", "warn")
            return
        if not messagebox.askyesno("删除节点", "确定删除选中的 %d 个节点？" % len(sel)):
            return
        evs = self._current_events()
        for i in reversed(sel):
            del evs[i]
        self._rebuild_events()
        self._toast("已删除 %d 个节点（未保存）" % len(sel), "ok")

    def _copy_events(self):
        if not self._require_edit():
            return
        sel = self._selected()
        if not sel:
            self._toast("请先选中要复制的节点", "warn")
            return
        evs = self._current_events()
        items = [copy.deepcopy(evs[i]) for i in sel]
        at = sel[-1] + 1
        evs[at:at] = items
        self._rebuild_events()
        self._select_range(at, at + len(items) - 1)
        self._toast("已复制 %d 个节点（未保存）" % len(items), "ok")

    def _move_up(self):
        if not self._require_edit():
            return
        sel = self._selected()
        evs = self._current_events()
        if not sel or sel[0] == 0:
            return
        for i in sel:
            evs[i - 1], evs[i] = evs[i], evs[i - 1]
        self._rebuild_events()
        self._select_range(sel[0] - 1, sel[-1] - 1)

    def _move_down(self):
        if not self._require_edit():
            return
        sel = self._selected()
        evs = self._current_events()
        if not sel or sel[-1] >= len(evs) - 1:
            return
        for i in reversed(sel):
            evs[i + 1], evs[i] = evs[i], evs[i + 1]
        self._rebuild_events()
        self._select_range(sel[0] + 1, sel[-1] + 1)

    # ------------------------------------------------------------ 录制
    def _on_traj_change(self):
        on = bool(self.traj_var.get())
        self.cfg.set("record_trajectory", on)
        try:
            ms = max(1, int(float(self.traj_ms_var.get())))
            self.cfg.set("trajectory_interval_ms", ms)
            self.traj_ms_var.set(str(ms))
        except Exception:
            ms = 16
        self.cfg.save()
        # 录制中切换也立即生效
        self.recorder.record_trajectory = on
        self.recorder.move_interval_ms = ms

    def _toggle_record(self):
        if self.recording:
            self._stop_record()
        else:
            self._start_record()

    def _start_record(self):
        if getattr(self, "_editing", False):
            self._toast("请先保存或退出编辑，再开始录制", "warn")
            return
        if self.player.running:
            self.player.stop()
        if self.events and self.script_path:
            self._save_script(silent=True)
        if self.events and not self.script_path:
            if not messagebox.askyesno("开始录制", "当前脚本未保存，开始录制会清空。是否继续？"):
                return
        self.events = []
        self.script_path = None
        self._rebuild_events()
        self.script_var.set("（未选择）")
        self._on_traj_change()
        try:
            interval = max(1, int(float(self.traj_ms_var.get())))
        except Exception:
            interval = 16

        ignore = set()
        for key in ("start_pause", "stop", "record"):
            parsed = parse_hotkey(str(self.cfg.get("hotkeys.%s" % key, "")))
            if parsed:
                ignore.add(parsed[1])
        self.recorder.ignore_vks = ignore
        try:
            self.recorder.start(bool(self.traj_var.get()), interval)
        except Exception as exc:
            self._toast("录制启动失败: %s" % exc, "error")
            return
        self.recording = True
        self._rec_started = time.time()
        self.rec_pill.set("录制中", theme.DANGER)
        self.btn_record.configure(text="■  停止录制    F8", fg_color="#7f1d1d")
        self._set_header("录制中", theme.DANGER)
        self._toast("录制已开始，按 F8 或点击按钮结束", "ok")
        self.after(500, self._rec_tick)

    def _rec_tick(self):
        if not self.recording:
            return
        secs = int(time.time() - self._rec_started)
        self.rec_stats.configure(text="已录制 %02d:%02d · %d 个事件 · 轨迹%s"
                                 % (secs // 60, secs % 60, len(self.events),
                                    "开" if self.traj_var.get() else "关"))
        self.after(500, self._rec_tick)

    def _stop_record(self):
        if not self.recording:
            return
        self.recorder.stop()
        self.recording = False
        self.rec_pill.set("待机", theme.MUTED)
        self.btn_record.configure(text="●  开始录制    F8", fg_color=theme.DANGER)
        self._set_header("待机", theme.OK)
        if self.events:
            path = new_script_path()
            self.script_path = path
            self._save_script(silent=True)
            self.rec_stats.configure(text="已保存 %s · %d 个事件" % (path.name, len(self.events)))
            self._toast("录制完成，已保存 %s" % path.name, "ok")
        else:
            self._toast("录制结束，没有捕获到事件", "warn")

    # ------------------------------------------------------------ 回放
    def _speed(self) -> float:
        try:
            return float(self.speed_var.get().lower().rstrip("x"))
        except Exception:
            return 1.0

    def _toggle_play(self):
        if self.player.running:
            self._pause_play()
            return
        if not self.events:
            self._toast("当前脚本没有事件", "warn")
            return
        if self.recording:
            self._stop_record()
        try:
            loops = max(0, int(float(self.loop_var.get())))
        except Exception:
            loops = 1
        speed = self._speed()
        self.cfg.set("loop_count", loops)
        self.cfg.save()
        script_dir = self.script_path.parent if self.script_path else SCRIPTS_DIR
        self.player.current_script = self.script_path
        if self.player.start(self.events, loops, speed, script_dir=script_dir):
            self.btn_play.configure(text="⏸  暂停    F6")
            self.play_pill.set("运行中", theme.OK)
            self._set_header("回放中", theme.ACCENT)
            self._append_play_log("启动：%d 个事件，循环 %s，速度 %.2fx"
                                  % (len(self.events), loops or "∞", speed))
            self._toast("脚本开始运行", "ok")

    def _pause_play(self, rule: bool = False):
        if not self.player.running:
            return
        self._rule_paused = False
        if self.player.paused:
            self.player.resume(None)
            self.play_pill.set("运行中", theme.OK)
            self._set_header("回放中", theme.ACCENT)
            self._append_play_log("继续运行")
        else:
            self.player.pause("manual")
            self.play_pill.set("已暂停", theme.WARN)
            self._set_header("已暂停", theme.WARN)
            self._append_play_log("已暂停")

    def _resume_play(self):
        if not self.player.running:
            self._rule_paused = False
            return
        self.player.resume(None)
        self._rule_paused = False
        self.play_pill.set("运行中", theme.OK)
        self._set_header("回放中", theme.ACCENT)
        self._append_play_log("继续运行")

    def _pause_for_rule(self, reason: str):
        if not self.player.running or self.player.paused:
            return False
        self.player.pause(reason)
        self.play_pill.set("已暂停", theme.WARN)
        self._set_header("已暂停", theme.WARN)
        self._append_play_log("已暂停（规则）")
        return True

    def _resume_for_rule(self, reason: str):
        if not self.player.running:
            return False
        self.player.resume(reason)
        if not self.player.paused:
            self.play_pill.set("运行中", theme.OK)
            self._set_header("回放中", theme.ACCENT)
            self._append_play_log("继续运行（规则）")
        return True

    def start_script_by_path(self, path: Path, source: str = "规则") -> bool:
        """按路径启动脚本；已在跑会先停止，用于后台规则切换。"""
        if getattr(self, "_editing", False):
            self._toast("%s：编辑模式下不切换脚本" % source, "warn")
            return False
        try:
            doc = ScriptDoc.load(path)
        except Exception as exc:
            self._toast("%s脚本加载失败：%s" % (source, exc), "error")
            return False
        if self.player.running:
            self.player.stop()
            self.player.wait_stopped(3.0)
        self.script_path = path
        self.events = doc.events
        self.player.current_script = path
        self._rebuild_events()
        self._refresh_scripts(select=path)
        try:
            loops = max(0, int(float(self.cfg.get("loop_count", 1) or 1)))
        except Exception:
            loops = 1
        try:
            speed = float(self.cfg.get("speed", 1.0) or 1.0)
        except Exception:
            speed = 1.0
        if not self.player.start(self.events, loops, speed, script_dir=path.parent):
            self._toast("%s：脚本启动失败" % source, "error")
            return False
        self.btn_play.configure(text="⏸  暂停    F6")
        self.play_pill.set("运行中", theme.OK)
        self._set_header("回放中", theme.ACCENT)
        self._append_play_log("%s启动：%s" % (source, path.name))
        return True

    def _run_script_file(self, name: str):
        name = (name or "").strip()
        if not name:
            return
        if self.player.running:
            self._toast("后台监控：主脚本正在运行，忽略启动新脚本", "warn")
            return
        path = Path(name)
        if not path.is_absolute():
            path = SCRIPTS_DIR / name
        try:
            doc = ScriptDoc.load(path)
        except Exception as exc:
            self._toast("后台监控脚本加载失败：%s" % exc, "error")
            return
        self.script_path = path
        self.events = doc.events
        self._rebuild_events()
        self._toast("后台监控启动脚本：%s" % path.name, "ok")
        self._toggle_play()

    def _stop_play(self):
        self.player.stop()
        self._append_play_log("请求停止…")

    def _apply_player_status(self, text: str, progress):
        if progress is not None:
            try:
                self.progress.set(max(0.0, min(1.0, float(progress))))
            except Exception:
                pass
        if text:
            self.play_pill.set(text[:16], theme.OK if "运行" in text else theme.MUTED)

    def _apply_player_done(self, reason: str):
        self._rule_paused = False
        self.player.current_script = self.script_path
        self.btn_play.configure(text="▶  启动    F6")
        self._set_header("待机", theme.OK)
        if reason == "finished":
            self.play_pill.set("已完成", theme.OK)
            self.progress.set(1.0)
            self._append_play_log("回放完成")
        elif reason == "stopped":
            self.play_pill.set("已停止", theme.WARN)
            self._append_play_log("已停止")
        else:
            self.play_pill.set("异常", theme.DANGER)
            self._append_play_log("回放异常，详见日志")

    def _append_play_log(self, text: str):
        try:
            self.play_log.configure(state="normal")
            self.play_log.insert("end", "\n" + datetime.datetime.now().strftime("%H:%M:%S ") + text)
            lines = self.play_log.get("1.0", "end").strip().splitlines()
            if len(lines) > 40:
                self.play_log.delete("1.0", "end")
                self.play_log.insert("end", "\n".join(lines[-40:]))
            self.play_log.see("end")
            self.play_log.configure(state="disabled")
        except Exception:
            pass


