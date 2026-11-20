"""节点编辑器：新增 / 编辑脚本节点，全部中文。"""
from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from config import ICONS_DIR, SCRIPTS_DIR
from core.hotkeys import parse_hotkey
from core.script import list_scripts
from core.winapi import EXTENDED_VKS
from . import theme
from .key_record_dialog import KeyRecordDialog
from .point_picker import PointPicker
from .region_picker import RegionPicker
from .icon_crop_dialog import IconCropDialog
from .action_list import ActionList
from core.screen import grab, to_ctk_image, load_image
from .widgets import AutoScrollFrame, label
from .window_icon import apply_window_icon

KIND_CLICK = "鼠标点击"
KIND_MOVE = "鼠标移动"
KIND_WHEEL = "滚轮"
KIND_KEY = "键盘按键"
KIND_TEXT = "文本输入"
KIND_WAIT = "等待"
KIND_MONITOR = "监控节点"
KIND_GROUP = "监控组"
KIND_SCRIPT = "运行脚本"
KIND_RAW = "原始 JSON"

KINDS = [KIND_CLICK, KIND_MOVE, KIND_WHEEL, KIND_KEY, KIND_TEXT,
         KIND_WAIT, KIND_MONITOR, KIND_GROUP, KIND_SCRIPT, KIND_RAW]

BUTTONS = [("左键", "left"), ("右键", "right"), ("中键", "middle"),
           ("侧键1", "x1"), ("侧键2", "x2")]
BUTTON_NAMES = {v: k for k, v in BUTTONS}

MODS = [("ctrl", 0x11), ("alt", 0x12), ("shift", 0x10), ("win", 0x5B)]
MOD_MASKS = {"ctrl": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}
MOD_VKS = dict(MODS)


def pretty_combo(text: str) -> str:
    parts = [p for p in (text or "").split("+") if p]
    out = []
    for p in parts:
        out.append({"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}.get(p.lower(), p.upper()))
    return " + ".join(out) if out else "（未设置）"


def combo_from_action(action) -> str:
    try:
        vk = int(action[0])
        name = str(action[1] or "").lower()
    except Exception:
        return ""
    name = {"ctrl_l": "ctrl", "ctrl_r": "ctrl", "shift_l": "shift", "shift_r": "shift",
            "alt_l": "alt", "alt_r": "alt", "win_l": "win", "win_r": "win",
            "escape": "esc", "return": "enter", "super_l": "win", "super_r": "win"}.get(name, name)
    if name:
        return name
    return "vk_%d" % vk


class EventEditorDialog(ctk.CTkToplevel):
    def __init__(self, master, event: dict | None = None, script_dir: Path | None = None):
        super().__init__(master)
        self.master_app = master
        self.event = dict(event or {})
        self.script_dir = Path(script_dir) if script_dir else SCRIPTS_DIR
        self.result: list[dict] | None = None
        self.vars: dict[str, tk.StringVar] = {}
        self._auto_vars: dict[str, tk.BooleanVar] = {}
        self._key_combo = ""
        self._monitor_names = [p.name for p in sorted(ICONS_DIR.glob("*.png"))]
        self._thumb_img = None
        self._tpl_thumb_img = None

        self.title("编辑节点" if event else "新增节点")
        self.geometry("760x720")
        self.minsize(760, 680)
        self.resizable(False, True)
        self.configure(fg_color=theme.BG)
        self.transient(master)
        apply_window_icon(self)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(16, 6))
        label(head, "节点类型", 11, theme.MUTED).pack(side="left")
        self.kind_var = tk.StringVar(value=self._detect_kind())
        ctk.CTkOptionMenu(head, variable=self.kind_var, values=KINDS, width=150, height=30,
                          fg_color=theme.CARD2, button_color=theme.LINE,
                          dropdown_fg_color=theme.CARD2, font=(theme.FONT, theme.fs(11)),
                          command=lambda _v: self._rebuild()).pack(side="left", padx=8)
        label(head, "延迟", 11, theme.MUTED).pack(side="left", padx=(14, 0))
        self.delay_var = tk.StringVar(value="%g" % float(self.event.get("delay", 0) or 0))
        ctk.CTkEntry(head, textvariable=self.delay_var, width=80, height=30,
                     fg_color=theme.BG2, border_color=theme.LINE, text_color=theme.TEXT,
                     font=(theme.MONO, theme.fs(11))).pack(side="left", padx=6)
        label(head, "毫秒", 10, theme.MUTED).pack(side="left")

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=18, pady=(6, 14))
        note_row = ctk.CTkFrame(bottom, fg_color="transparent")
        note_row.pack(fill="x", pady=(0, 10))
        label(note_row, "备注", 11, theme.MUTED, width=60, anchor="w").pack(side="left")
        self.note_var = tk.StringVar(value=str(self.event.get("note", "") or ""))
        ctk.CTkEntry(note_row, textvariable=self.note_var, height=30,
                     fg_color=theme.BG2, border_color=theme.LINE,
                     placeholder_text="可选，仅用于列表说明",
                     font=(theme.FONT, theme.fs(11))).pack(side="left", fill="x", expand=True)
        btns = ctk.CTkFrame(bottom, fg_color="transparent")
        btns.pack(fill="x")
        ctk.CTkButton(btns, text="取消", width=92, height=36, fg_color=theme.CARD2,
                      hover_color=theme.LINE, font=(theme.FONT, theme.fs(12)),
                      command=self._cancel).pack(side="right")
        ctk.CTkButton(btns, text="确定", width=110, height=36, fg_color=theme.ACCENT,
                      hover_color="#0ea5e9", text_color="#04121f",
                      font=(theme.FONT, theme.fs(12), "bold"),
                      command=self._confirm).pack(side="right", padx=8)

        self.scroll = AutoScrollFrame(self, height=520)
        self.scroll.pack(fill="both", expand=True, padx=18, pady=6)
        self.body = self.scroll.inner
        self._rebuild()
        self.after(120, self._focus)

    # ------------------------------------------------------------ helpers
    def _focus(self):
        try:
            self.lift()
            self.focus_force()
            self.grab_set()
        except Exception:
            pass

    def _row(self) -> ctk.CTkFrame:
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x", pady=5)
        return row

    def _field_label(self, row, text: str, width=92):
        lab = label(row, text, 11, theme.MUTED, width=width, anchor="w")
        lab.pack(side="left")
        return lab

    def _entry(self, row, width=110, var=None, mono=True):
        e = ctk.CTkEntry(row, width=width, height=30, textvariable=var,
                         fg_color=theme.CARD, border_color=theme.LINE,
                         text_color=theme.TEXT,
                         font=((theme.MONO if mono else theme.FONT), theme.fs(11)))
        return e

    def _detect_kind(self) -> str:
        ev = self.event
        if not ev:
            return KIND_CLICK
        typ = str(ev.get("type", "event")).lower()
        if typ == "monitor":
            return KIND_MONITOR
        if typ == "monitor_group":
            return KIND_GROUP
        if typ == "wait":
            return KIND_WAIT
        if typ == "script":
            return KIND_SCRIPT
        if typ != "event":
            return KIND_RAW
        et = str(ev.get("event_type", "")).upper()
        at = str(ev.get("action_type", "")).lower()
        if et == "EM":
            if "wheel" in at:
                return KIND_WHEEL
            if "move" in at:
                return KIND_MOVE
            return KIND_CLICK
        if et == "EK":
            return KIND_KEY
        if et == "EX":
            return KIND_TEXT
        return KIND_RAW

    # ------------------------------------------------------------ 表单
    def _rebuild(self):
        kind = self.kind_var.get()
        for w in self.body.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        self.vars = {}
        self._auto_vars = {}
        if kind == KIND_CLICK:
            self._build_click()
        elif kind == KIND_MOVE:
            self._build_move()
        elif kind == KIND_WHEEL:
            self._build_wheel()
        elif kind == KIND_KEY:
            self._build_key()
        elif kind == KIND_TEXT:
            self._build_text()
        elif kind == KIND_WAIT:
            self._build_wait()
        elif kind == KIND_MONITOR:
            self._build_monitor()
        elif kind == KIND_GROUP:
            self._build_group()
        elif kind == KIND_SCRIPT:
            self._build_script()
        else:
            self._build_raw()
        self.scroll.capture_wheel()

    def _coords(self, row, x_default=None, y_default=None):
        x = x_default if x_default is not None else self._xy()[0]
        y = y_default if y_default is not None else self._xy()[1]
        self.vars["x"] = tk.StringVar(value=str(x))
        self.vars["y"] = tk.StringVar(value=str(y))
        self._field_label(row, "坐标")
        label(row, "X", 10, theme.MUTED).pack(side="left")
        self._entry(row, 72, self.vars["x"]).pack(side="left", padx=(4, 8))
        label(row, "Y", 10, theme.MUTED).pack(side="left")
        self._entry(row, 72, self.vars["y"]).pack(side="left", padx=(4, 8))
        ctk.CTkButton(row, text="屏幕取点", width=80, height=28, fg_color=theme.CARD2,
                      hover_color=theme.LINE, font=(theme.FONT, theme.fs(10)),
                      command=lambda: self._pick_point(self.vars["x"], self.vars["y"])).pack(side="left")

    def _xy(self):
        action = self.event.get("action")
        if isinstance(action, (list, tuple)) and len(action) >= 2:
            try:
                return int(float(action[0])), int(float(action[1]))
            except Exception:
                pass
        from core.winapi import cursor_pos
        try:
            return cursor_pos()
        except Exception:
            return 0, 0


    def _pick_point(self, x_var, y_var):
        try:
            self.grab_release()
        except Exception:
            pass
        root = getattr(self, "master_app", None)
        self.withdraw()
        try:
            if root is not None:
                root.iconify()
        except Exception:
            pass

        def restore():
            try:
                if root is not None:
                    root.deiconify()
                    root.lift()
            except Exception:
                pass
            self.deiconify()
            self.lift()
            try:
                self.grab_set()
            except Exception:
                pass

        def done(x, y):
            x_var.set(str(int(x)))
            y_var.set(str(int(y)))
            self.after(120, restore)

        def cancel():
            self.after(120, restore)

        self.after(150, lambda: PointPicker(self, on_pick=done, on_cancel=cancel))

    def _build_click(self):
        at = str(self.event.get("action_type", "mouse left down")).lower().split()
        button = BUTTON_NAMES.get(at[1] if len(at) > 1 else "left", "左键")
        phase = at[2] if len(at) > 2 else "down"
        mode = "单击" if not self.event else {"down": "按下", "up": "抬起"}.get(phase, "单击")

        row = self._row()
        self._field_label(row, "鼠标按键")
        self.vars["button"] = tk.StringVar(value=button)
        ctk.CTkOptionMenu(row, variable=self.vars["button"], values=[b[0] for b in BUTTONS],
                          width=110, height=30, fg_color=theme.CARD,
                          button_color=theme.LINE, dropdown_fg_color=theme.CARD2,
                          font=(theme.FONT, theme.fs(11))).pack(side="left")

        row = self._row()
        self._field_label(row, "动作方式")
        self.vars["mode"] = tk.StringVar(value=mode)
        ctk.CTkOptionMenu(row, variable=self.vars["mode"], values=["单击", "按下", "抬起"],
                          width=110, height=30, fg_color=theme.CARD,
                          button_color=theme.LINE, dropdown_fg_color=theme.CARD2,
                          font=(theme.FONT, theme.fs(11))).pack(side="left")
        label(row, "单击 = 自动生成按下 + 抬起两个节点", 9).pack(side="left", padx=10)

        self._coords(self._row())
        self._hint("提示：屏幕取点会临时隐藏本窗口，点一下目标位置即可。")

    def _build_move(self):
        self._coords(self._row(), *self._xy())
        self._hint("鼠标移动节点只改变指针位置，不点击。")

    def _build_wheel(self):
        at = str(self.event.get("action_type", "mouse wheel up")).lower()
        row = self._row()
        self._field_label(row, "滚轮方向")
        self.vars["direction"] = tk.StringVar(value="上滚" if at.endswith("up") else "下滚")
        ctk.CTkOptionMenu(row, variable=self.vars["direction"], values=["上滚", "下滚"],
                          width=110, height=30, fg_color=theme.CARD,
                          button_color=theme.LINE, dropdown_fg_color=theme.CARD2,
                          font=(theme.FONT, theme.fs(11))).pack(side="left")
        label(row, "每格 120 单位", 9).pack(side="left", padx=10)
        self._coords(self._row())
        self._hint("滚轮滚动前会先把鼠标移动到上面的坐标。")

    def _build_key(self):
        action = self.event.get("action")
        combo = combo_from_action(action) if action else ""
        self._key_combo = combo
        row = self._row()
        self._field_label(row, "按键")
        self.vars["key"] = tk.StringVar(value=pretty_combo(combo))
        ctk.CTkEntry(row, textvariable=self.vars["key"], width=210, height=30, state="readonly",
                     fg_color=theme.BG2, border_color=theme.LINE, text_color=theme.TEXT,
                     font=(theme.MONO, theme.fs(11))).pack(side="left")
        ctk.CTkButton(row, text="录制按键", width=96, height=30, fg_color=theme.ACCENT,
                      hover_color="#0ea5e9", text_color="#04121f",
                      font=(theme.FONT, theme.fs(10), "bold"),
                      command=self._record_key).pack(side="left", padx=8)
        ctk.CTkButton(row, text="清空", width=64, height=30, fg_color=theme.CARD2,
                      hover_color=theme.LINE, font=(theme.FONT, theme.fs(10)),
                      command=self._clear_key).pack(side="left")

        row = self._row()
        self._field_label(row, "动作方式")
        self.vars["mode"] = tk.StringVar(value="按下并抬起")
        if action:
            at = str(self.event.get("action_type", "")).lower()
            self.vars["mode"].set("抬起" if at.endswith("up") else "按下")
        ctk.CTkOptionMenu(row, variable=self.vars["mode"], values=["按下并抬起", "按下", "抬起"],
                          width=130, height=30, fg_color=theme.CARD,
                          button_color=theme.LINE, dropdown_fg_color=theme.CARD2,
                          font=(theme.FONT, theme.fs(11))).pack(side="left")
        label(row, "按住", 10, theme.MUTED).pack(side="left", padx=(12, 0))
        self.vars["hold"] = tk.StringVar(value="40")
        self._entry(row, 66, self.vars["hold"]).pack(side="left", padx=4)
        label(row, "毫秒", 10, theme.MUTED).pack(side="left")
        self._hint("支持单键和组合键（Ctrl / Alt / Shift / Win）。录制时会临时停用 F6~F8 热键。")

    def _clear_key(self):
        self._key_combo = ""
        self.vars["key"].set("（未设置）")

    def _record_key(self):
        app = self.master_app
        try:
            if getattr(app, "hotkeys", None):
                app.hotkeys.stop()
                app.hotkeys.join(timeout=0.4)
        except Exception:
            pass
        KeyRecordDialog(self, on_done=self._on_key_recorded, initial=self._key_combo)

    def _on_key_recorded(self, combo):
        if combo:
            self._key_combo = combo
            self.vars["key"].set(pretty_combo(combo))
        try:
            self.master_app._start_hotkeys()
        except Exception:
            pass

    def _build_text(self):
        row = self._row()
        self._field_label(row, "输入内容")
        label(row, "回放时用剪贴板粘贴，支持中文", 9).pack(side="left")
        self.text_box = ctk.CTkTextbox(self.body, height=150, fg_color=theme.CARD,
                                       border_color=theme.LINE, border_width=1,
                                       text_color=theme.TEXT,
                                       font=(theme.FONT, theme.fs(11)))
        self.text_box.pack(fill="x", padx=4, pady=6)
        action = self.event.get("action")
        if action:
            self.text_box.insert("1.0", str(action))

    def _build_wait(self):
        row = self._row()
        self._field_label(row, "等待时长")
        self.vars["ms"] = tk.StringVar(value=str(int(self.event.get("ms", 500) or 500)))
        self._entry(row, 100, self.vars["ms"]).pack(side="left")
        label(row, "毫秒", 10, theme.MUTED).pack(side="left", padx=6)
        self._hint("等待节点执行期间仍可通过 F7 / 停止按钮中断。")

    def _build_monitor(self):
        ev = self.event
        self.vars["name"] = tk.StringVar(value=str(ev.get("name", "监控1")))
        row = self._row()
        self._field_label(row, "节点名称")
        self._entry(row, 220, self.vars["name"], mono=False).pack(side="left")
        row = self._row()
        self._field_label(row, "图标")
        tpl = str(ev.get("template", "") or "")
        shown = Path(tpl).name if tpl else "（未选择）"
        self.vars["template"] = tk.StringVar(value=shown)
        self._tpl_path = tpl
        ctk.CTkOptionMenu(row, variable=self.vars["template"],
                          values=["（未选择）"] + self._monitor_names,
                          width=240, height=30, fg_color=theme.CARD,
                          button_color=theme.LINE, dropdown_fg_color=theme.CARD2,
                          font=(theme.FONT, theme.fs(11)),
                          command=self._on_template_pick).pack(side="left")
        ctk.CTkButton(row, text="浏览…", width=74, height=30, fg_color=theme.CARD2,
                      hover_color=theme.LINE, font=(theme.FONT, theme.fs(10)),
                      command=self._browse_template).pack(side="left", padx=6)
        ctk.CTkButton(row, text="从画面圈选", width=96, height=30, fg_color=theme.OK,
                      hover_color="#16a34a", text_color="#04121f",
                      font=(theme.FONT, theme.fs(10), "bold"),
                      command=self._capture_monitor_template).pack(side="left")
        region = ev.get("region") or [0, 0, 0, 0]
        row = self._row()
        self._field_label(row, "监控区域")
        for key, val in zip(("rx", "ry", "rw", "rh"), region):
            label(row, {"rx": "X", "ry": "Y", "rw": "宽", "rh": "高"}[key], 10, theme.MUTED).pack(side="left", padx=(4, 2))
            self.vars[key] = tk.StringVar(value=str(int(val)))
            e = self._entry(row, 62, self.vars[key])
            e.pack(side="left")
            e.bind("<FocusOut>", lambda _e: self._refresh_monitor_thumb())
        ctk.CTkButton(row, text="框选区域", width=84, height=30, fg_color=theme.ACCENT,
                      hover_color=theme.ACCENT2, text_color="#04121f",
                      font=(theme.FONT, theme.fs(10), "bold"),
                      command=self._pick_monitor_region).pack(side="left", padx=6)
        ctk.CTkButton(row, text="刷新预览", width=78, height=30, fg_color=theme.CARD2,
                      hover_color=theme.LINE, font=(theme.FONT, theme.fs(10)),
                      command=self._refresh_monitor_thumb).pack(side="left")
        row = self._row()
        self._field_label(row, "识别参数")
        for key, text, default, src_key in (("threshold", "阈值", 0.85, "threshold"),
                                             ("poll", "轮询", 150, "poll_ms"),
                                             ("timeout", "超时", 8000, "timeout_ms")):
            label(row, text, 10, theme.MUTED).pack(side="left", padx=(4, 2))
            self.vars[key] = tk.StringVar(value="%g" % float(ev.get(src_key, default) or default))
            self._entry(row, 70, self.vars[key]).pack(side="left", padx=(0, 10))
        row = self._row()
        self._field_label(row, "区域缩略图")
        self.thumb_label = ctk.CTkLabel(row, text="点击「刷新预览」查看监控区域",
                                        font=(theme.FONT, theme.fs(10)),
                                        text_color=theme.MUTED, width=320, height=120,
                                        fg_color=theme.CARD, corner_radius=8)
        self.thumb_label.pack(side="left")
        row = self._row()
        self._field_label(row, "图标预览")
        self.tpl_thumb_label = ctk.CTkLabel(row, text="未选择图标",
                                            font=(theme.FONT, theme.fs(10)),
                                            text_color=theme.MUTED, width=120, height=90,
                                            fg_color=theme.CARD, corner_radius=8)
        self.tpl_thumb_label.pack(side="left")
        row = self._row()
        self._field_label(row, "执行方式")
        wm = str(ev.get("wait_mode", "timeout") or "timeout")
        wm_rev = {"once": "单次判断", "timeout": "限时等待", "forever": "一直等待"}
        self.wait_mode_var = tk.StringVar(value=wm_rev.get(wm, "限时等待"))
        ctk.CTkOptionMenu(row, variable=self.wait_mode_var,
                          values=["单次判断", "限时等待", "一直等待"], width=120, height=28,
                          fg_color=theme.CARD2, button_color=theme.LINE,
                          font=(theme.FONT, theme.fs(10))).pack(side="left")
        label(row, "等待", 10, theme.MUTED).pack(side="left", padx=(10, 2))
        self.wait_ms_var = tk.StringVar(value=str(int(ev.get("wait_ms", ev.get("timeout_ms", 8000)) or 0)))
        self._entry(row, 78, self.wait_ms_var).pack(side="left")
        label(row, "ms（单次判断忽略）", 9, theme.MUTED).pack(side="left", padx=4)

        label(self.body, "命中时动作", 10, theme.TEXT).pack(anchor="w", padx=4, pady=(8, 2))
        self.hit_actions = ActionList(self.body, ev.get("on_hit"), height=90, inline=True)
        self.hit_actions.pack(fill="x", padx=4)
        label(self.body, "未命中 / 超时动作", 10, theme.TEXT).pack(anchor="w", padx=4, pady=(8, 2))
        self.miss_actions = ActionList(self.body, ev.get("on_miss"), height=90, inline=True)
        self.miss_actions.pack(fill="x", padx=4)
        self._hint("脚本内节点不能暂停/停止当前脚本，只能运行子脚本或停止整个回放。"
                   "「框选区域」直接在屏幕上拖拽；「从画面圈选」在当前区域截图里裁剪图标。")
        self.after(150, self._refresh_monitor_thumb)
        self.after(180, self._refresh_tpl_thumb)

    def _monitor_region(self):
        try:
            x = int(float(self.vars["rx"].get() or 0))
            y = int(float(self.vars["ry"].get() or 0))
            w = int(float(self.vars["rw"].get() or 0))
            h = int(float(self.vars["rh"].get() or 0))
        except Exception:
            return None
        if w <= 0 or h <= 0:
            return None
        return [x, y, w, h]

    def _set_monitor_region(self, rect):
        for key, val in zip(("rx", "ry", "rw", "rh"), rect):
            self.vars[key].set(str(int(val)))
        self._refresh_monitor_thumb()

    def _pick_monitor_region(self):
        initial = self._monitor_region()
        root = getattr(self, "master_app", None)
        try:
            self.withdraw()
            if root is not None:
                root.iconify()
        except Exception:
            pass

        def restore():
            try:
                if root is not None:
                    root.deiconify()
                    root.lift()
            except Exception:
                pass
            try:
                self.deiconify()
                self.lift()
                self.focus_force()
            except Exception:
                pass

        def done(rect, capture=False):
            self._set_monitor_region(rect)
            self.after(120, restore)

        def cancelled():
            self.after(120, restore)

        self.after(200, lambda: RegionPicker(self, rect=initial,
                                             on_confirm=done, on_cancel=cancelled))

    def _refresh_monitor_thumb(self):
        if not hasattr(self, "thumb_label"):
            return
        region = self._monitor_region()
        if not region:
            self.thumb_label.configure(text="未设置监控区域", image=None)
            return
        try:
            frame = grab(tuple(region))
            if frame is None or frame.size == 0:
                raise ValueError("空画面")
            from PIL import Image
            pil = to_ctk_image(frame)
            pil.thumbnail((320, 120), Image.LANCZOS)
            cimg = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
            self._thumb_img = cimg
            self.thumb_label.configure(image=cimg, text="")
        except Exception as exc:
            self.thumb_label.configure(text="预览失败：%s" % exc, image=None)

    def _refresh_tpl_thumb(self):
        if not hasattr(self, "tpl_thumb_label"):
            return
        path = str(self._tpl_path or "")
        if not path:
            self.tpl_thumb_label.configure(text="未选择图标", image=None)
            return
        try:
            img = load_image(Path(path))
            if img is None:
                raise ValueError("无法读取")
            from PIL import Image
            pil = to_ctk_image(img)
            pil.thumbnail((120, 90), Image.LANCZOS)
            cimg = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
            self._tpl_thumb_img = cimg
            self.tpl_thumb_label.configure(image=cimg, text="")
        except Exception:
            self.tpl_thumb_label.configure(text="图标读取失败", image=None)

    def _capture_monitor_template(self):
        region = self._monitor_region()
        if not region:
            self._warn("请先框选监控区域，再抓取图标")
            return
        try:
            frame = grab(tuple(region))
        except Exception as exc:
            self._warn("抓取区域失败：%s" % exc)
            return
        if frame is None or frame.size == 0:
            self._warn("抓到空画面，请检查窗口是否最小化")
            return
        import datetime
        name = "icon_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        def on_save(path, crop_rect=None):
            self._tpl_path = "icons/%s" % Path(path).name
            self.vars["template"].set(Path(path).name)
            self._monitor_names = [p.name for p in sorted(ICONS_DIR.glob("*.png"))]
            self._refresh_tpl_thumb()

        IconCropDialog(self, frame, region, name, default_crop=None, on_save=on_save)

    def _on_template_pick(self, name):
        if name == "（未选择）":
            self._tpl_path = ""
        else:
            self._tpl_path = "icons/%s" % name
        self._refresh_tpl_thumb()

    def _browse_template(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(title="选择图标图片",
                                          filetypes=[("图片", "*.png;*.jpg;*.jpeg;*.bmp")])
        if path:
            self._tpl_path = path
            self.vars["template"].set(Path(path).name)
            self._refresh_tpl_thumb()

    def _build_group(self):
        n = len(self.event.get("rules") or [])
        self._hint("这是一个多图标监控组节点，共 %d 条规则。" % n)
        self._hint("请到「区域监控」页 →「多图标规则」里编辑；列表里双击该节点可直接跳转。")
        self._hint("本窗口只修改延迟和备注。")

    def _build_script(self):
        names = [p.name for p in list_scripts()]
        cur = str(self.event.get("path", "") or "")
        row = self._row()
        self._field_label(row, "脚本")
        self.vars["path"] = tk.StringVar(value=Path(cur).name if cur else (names[0] if names else "（无脚本）"))
        ctk.CTkOptionMenu(row, variable=self.vars["path"], values=names or ["（无脚本）"],
                          width=260, height=30, fg_color=theme.CARD,
                          button_color=theme.LINE, dropdown_fg_color=theme.CARD2,
                          font=(theme.FONT, theme.fs(11))).pack(side="left")
        row = self._row()
        self._field_label(row, "执行方式")
        self._auto_vars["wait"] = tk.BooleanVar(value=bool(self.event.get("wait", True)))
        ctk.CTkSwitch(row, text="等待子脚本执行完再继续", variable=self._auto_vars["wait"],
                      font=(theme.FONT, theme.fs(11)), progress_color=theme.ACCENT).pack(side="left")
        self._hint("脚本节点会按当前脚本目录解析相对路径，可直接调用 scripts 里的其他脚本。")

    def _build_raw(self):
        self._hint("未识别的节点类型，直接编辑 JSON。保存时必须是一个对象。")
        self.text_box = ctk.CTkTextbox(self.body, height=300, fg_color=theme.CARD,
                                       border_color=theme.LINE, border_width=1,
                                       text_color=theme.TEXT,
                                       font=(theme.MONO, theme.fs(10)))
        self.text_box.pack(fill="both", expand=True, padx=4, pady=6)
        self.text_box.insert("1.0", json.dumps(self.event or {
            "type": "event", "event_type": "EM", "delay": 100,
            "action_type": "mouse left down", "action": [100, 100]},
            ensure_ascii=False, indent=2))

    def _hint(self, text: str):
        label(self.body, text, 9, theme.MUTED, anchor="w", justify="left",
              wraplength=560).pack(fill="x", padx=4, pady=(6, 0))

    # ------------------------------------------------------------ 提交
    def _warn(self, text: str):
        messagebox.showwarning("节点编辑", text, parent=self)

    def _num(self, key: str, default=0):
        try:
            return float(self.vars[key].get())
        except Exception:
            raise ValueError(key)

    def _confirm(self):
        kind = self.kind_var.get()
        try:
            delay = max(0.0, float(self.delay_var.get() or 0))
        except Exception:
            self._warn("延迟必须是数字（毫秒）")
            return
        note = self.note_var.get().strip()
        try:
            events = self._build_events(kind, delay)
        except ValueError as exc:
            self._warn(str(exc))
            return
        if events is None:
            return
        for ev in events:
            if note:
                ev["note"] = note
        self.result = events
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def _build_events(self, kind: str, delay: float) -> list[dict] | None:
        if kind == KIND_CLICK:
            button = dict((cn, en) for cn, en in BUTTONS)[self.vars["button"].get()]
            mode = self.vars["mode"].get()
            x = int(self._num("x"))
            y = int(self._num("y"))
            down = {"type": "event", "event_type": "EM", "action_type": "mouse %s down" % button,
                    "action": [x, y], "delay": delay}
            up = {"type": "event", "event_type": "EM", "action_type": "mouse %s up" % button,
                  "action": [x, y], "delay": 0}
            if mode == "单击":
                return [down, up]
            return [down if mode == "按下" else up]

        if kind == KIND_MOVE:
            x = int(self._num("x"))
            y = int(self._num("y"))
            return [{"type": "event", "event_type": "EM", "action_type": "mouse move",
                     "action": [x, y], "delay": delay}]

        if kind == KIND_WHEEL:
            direction = "up" if self.vars["direction"].get() == "上滚" else "down"
            x = int(self._num("x"))
            y = int(self._num("y"))
            return [{"type": "event", "event_type": "EM",
                     "action_type": "mouse wheel %s" % direction,
                     "action": [x, y], "delay": delay}]

        if kind == KIND_KEY:
            combo = (self._key_combo or "").strip()
            if not combo:
                self._warn("请先录制一个按键")
                return None
            parsed = parse_hotkey(combo)
            if not parsed:
                self._warn("按键格式无效：%s" % combo)
                return None
            mods, vk = parsed
            main_name = combo.split("+")[-1].strip().lower()
            if main_name.startswith("vk_") or main_name in ("ctrl", "alt", "shift", "win"):
                name = main_name
            else:
                name = main_name
            mode = self.vars["mode"].get()
            hold = max(0, int(self._num("hold")))
            mod_keys = [m for m in ("ctrl", "alt", "shift", "win") if mods & MOD_MASKS[m]]
            out: list[dict] = []

            def key_ev(k, n, phase, d):
                return {"type": "event", "event_type": "EK", "action_type": "key %s" % phase,
                        "action": [int(k), n, 1 if int(k) in EXTENDED_VKS else 0], "delay": d}

            first = delay
            if mode in ("按下", "按下并抬起"):
                for m in mod_keys:
                    out.append(key_ev(MOD_VKS[m], m, "down", first))
                    first = 0
                out.append(key_ev(vk, name, "down", first))
                first = 0
                if mode == "按下并抬起":
                    out.append(key_ev(vk, name, "up", hold))
                    for m in reversed(mod_keys):
                        out.append(key_ev(MOD_VKS[m], m, "up", 0))
            else:
                out.append(key_ev(vk, name, "up", first))
                for m in reversed(mod_keys):
                    out.append(key_ev(MOD_VKS[m], m, "up", 0))
            return out

        if kind == KIND_TEXT:
            text = self.text_box.get("1.0", "end-1c")
            if not text:
                self._warn("请输入要输入的文本")
                return None
            return [{"type": "event", "event_type": "EX", "action_type": "input",
                     "action": text, "delay": delay}]

        if kind == KIND_WAIT:
            ms = int(self._num("ms"))
            if ms <= 0:
                self._warn("等待时长必须大于 0")
                return None
            return [{"type": "wait", "ms": ms, "delay": delay}]

        if kind == KIND_MONITOR:
            name = self.vars["name"].get().strip() or "监控1"
            tpl = self._tpl_path
            shown = self.vars["template"].get()
            if shown and shown != "（未选择）" and Path(shown).name != Path(tpl or "").name:
                tpl = "icons/%s" % shown
            if not tpl:
                self._warn("请选择图标")
                return None
            region = []
            for k in ("rx", "ry", "rw", "rh"):
                region.append(int(self._num(k)))
            if region[2] <= 0 or region[3] <= 0:
                self._warn("监控区域的宽和高必须大于 0")
                return None
            thr = max(0.05, min(0.999, float(self._num("threshold"))))
            poll = max(20, int(self._num("poll")))
            mode = {"单次判断": "once", "限时等待": "timeout",
                    "一直等待": "forever"}.get(self.wait_mode_var.get(), "timeout")
            try:
                wait_ms = max(0, int(float(self.wait_ms_var.get() or 0)))
            except Exception:
                wait_ms = 8000
            on_hit = self.hit_actions.get() if hasattr(self, "hit_actions") else []
            on_miss = self.miss_actions.get() if hasattr(self, "miss_actions") else []
            ev = {"type": "monitor", "name": name, "region": region, "template": tpl,
                  "threshold": round(thr, 3), "poll_ms": poll,
                  "timeout_ms": wait_ms if mode == "timeout" else 0,
                  "wait_mode": mode, "wait_ms": wait_ms,
                  "multiscale": True, "delay": delay,
                  "on_hit": on_hit, "on_miss": on_miss, "on_timeout": on_miss}
            return [ev]

        if kind == KIND_GROUP:
            if not self.event:
                self._warn("监控组节点请从「区域监控」页插入")
                return None
            ev = dict(self.event)
            ev["delay"] = delay
            return [ev]

        if kind == KIND_SCRIPT:
            name = self.vars["path"].get()
            if not name or name == "（无脚本）":
                self._warn("请选择脚本")
                return None
            path = "scripts/%s" % name if (SCRIPTS_DIR / name).exists() else name
            return [{"type": "script", "path": path,
                     "wait": bool(self._auto_vars.get("wait", tk.BooleanVar(value=True)).get()),
                     "delay": delay}]

        raw = self.text_box.get("1.0", "end-1c").strip()
        try:
            obj = json.loads(raw)
        except Exception as exc:
            self._warn("JSON 解析失败：%s" % exc)
            return None
        if not isinstance(obj, dict):
            self._warn("JSON 顶层必须是对象 { }")
            return None
        obj.setdefault("delay", delay)
        return [obj]

    def _cancel(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.result = None
        self.destroy()
