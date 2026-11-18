"""录制 / 回放页。"""
from __future__ import annotations

import datetime
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from config import SCRIPTS_DIR
from . import theme
from core.hotkeys import parse_hotkey
from core.script import ScriptDoc, event_summary, list_scripts, new_script_path
from .widgets import Card, Pill, entry, label


class RecordPageMixin:
    def _build_record_page(self):
        outer, page = self._new_page()
        self.pages["record"] = outer
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
                                             width=330, height=32, fg_color=theme.CARD2,
                                             button_color=theme.LINE, button_hover_color=theme.ACCENT,
                                             dropdown_fg_color=theme.CARD2, font=(theme.FONT, theme.fs(11)),
                                             command=self._on_script_selected)
        self.script_menu.pack(side="left")
        self._btn(inner, "新建", self._new_script, width=64).pack(side="left", padx=(10, 0))
        self._btn(inner, "保存", self._save_script, width=64, color=theme.ACCENT,
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

        ev_card = Card(page, "事件列表", "含鼠标 / 键盘 / 监控节点")
        ev_card.grid(row=2, column=0, sticky="nsew")
        bar = ctk.CTkFrame(ev_card.body, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 6))
        self._btn(bar, "删除最后一条", self._delete_last_event, width=96).pack(side="left")
        self._btn(bar, "清空", self._clear_events, width=64).pack(side="left", padx=6)

        header = ctk.CTkFrame(ev_card.body, fg_color=theme.BG2, height=26, corner_radius=6)
        header.pack(fill="x")
        for text, width, anchor in (("#", 54, "w"), ("类型", 64, "w"),
                                    ("动作", 190, "w"), ("内容", 320, "w")):
            ctk.CTkLabel(header, text=text, width=width, anchor=anchor, font=(theme.MONO, theme.fs(9)),
                         text_color=theme.MUTED).pack(side="left", padx=(8, 0))

        self.events_scroll = ctk.CTkScrollableFrame(ev_card.body, fg_color=theme.BG2,
                                                    corner_radius=8)
        self.events_scroll.pack(fill="both", expand=True, pady=(4, 0))

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

    def _save_script(self, silent: bool = False) -> bool:
        if not self.events:
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
        if not self.events:
            return
        if not messagebox.askyesno("清空", "确定清空当前脚本的全部事件？"):
            return
        self.events = []
        self._rebuild_events()

    def _rebuild_events(self):
        for row in self._event_rows:
            try:
                row.destroy()
            except Exception:
                pass
        self._event_rows = []
        show = self.events[-400:]
        for i, ev in enumerate(show):
            self._add_event_row(ev, len(self.events) - len(show) + i + 1)
        self._update_counts()

    def _add_event(self, ev: dict):
        self.events.append(ev)
        if len(self._event_rows) >= 500:
            row = self._event_rows.pop(0)
            try:
                row.destroy()
            except Exception:
                pass
        self._add_event_row(ev, len(self.events))
        self._update_counts()
        try:
            self.events_scroll._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _add_event_row(self, ev: dict, idx: int):
        kind, action, detail = event_summary(ev)
        row = ctk.CTkFrame(self.events_scroll, fg_color="transparent", height=24)
        row.pack(fill="x", pady=1)
        color = {"鼠标": theme.ACCENT, "键盘": theme.PURPLE, "输入": theme.OK,
                 "监控": theme.WARN}.get(kind, theme.MUTED)
        cells = [
            ("%04d" % idx, 54, theme.LINE, theme.MONO),
            (kind, 64, color, theme.FONT),
            (action, 190, theme.TEXT, theme.FONT),
            (detail, 320, theme.MUTED, theme.MONO),
        ]
        for text, width, fg, font in cells:
            ctk.CTkLabel(row, text=text, width=width, anchor="w", text_color=fg,
                         font=(font, 9)).pack(side="left", padx=(8, 0))
        self._event_rows.append(row)

    def _update_counts(self):
        self.event_count_label.configure(text="%d 个事件" % len(self.events))
        self.count_label.configure(text="事件 %d" % len(self.events))

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
        if self.player.start(self.events, loops, speed, script_dir=script_dir):
            self.btn_play.configure(text="⏸  暂停    F6")
            self.play_pill.set("运行中", theme.OK)
            self._set_header("回放中", theme.ACCENT)
            self._append_play_log("启动：%d 个事件，循环 %s，速度 %.2fx"
                                  % (len(self.events), loops or "∞", speed))
            self._toast("脚本开始运行", "ok")

    def _pause_play(self):
        if not self.player.running:
            return
        paused = self.player.toggle_pause()
        if paused:
            self.play_pill.set("已暂停", theme.WARN)
            self._set_header("已暂停", theme.WARN)
            self._append_play_log("已暂停")
        else:
            self.play_pill.set("运行中", theme.OK)
            self._set_header("回放中", theme.ACCENT)
            self._append_play_log("继续运行")

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


