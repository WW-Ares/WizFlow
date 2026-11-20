"""多图标规则管理：规则列表、动作编辑、关联组。"""
from __future__ import annotations

import copy
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image

from config import ICONS_DIR
from core.actions import action_summary
from core.monitor_rules import (match_rule, normalize_link,
                                normalize_monitor_group, normalize_rule)
from core.script import list_scripts
from core.screen import TemplateMatcher, grab, load_image, to_ctk_image
from . import theme
from .icon_crop_dialog import IconCropDialog
from .point_picker import PointPicker
from .area_select_dialog import AreaSelectDialog
from .window_icon import apply_window_icon
from .widgets import AutoScrollFrame, Card, Tooltip, center_window, entry, label

ACTION_TYPES = [
    ("click", "单击 / 双击 / 长按"),
    ("wheel", "滚轮滚动"),
    ("key", "按键"),
    ("script", "运行脚本"),
    ("wait", "等待"),
    ("text", "输入文本"),
    ("move", "移动到位置"),
    ("drag", "拖拽"),
    ("notify", "通知"),
    ("stop", "停止"),
]
TYPE_NAMES = dict(ACTION_TYPES)
MATCH_DISP = {"任意匹配": "any", "按优先级": "priority", "全部满足": "all", "顺序满足": "sequence"}
MATCH_REV = {v: k for k, v in MATCH_DISP.items()}
RUN_DISP = {"持续运行": "continuous", "执行一次": "once"}
RUN_REV = {v: k for k, v in RUN_DISP.items()}
LINK_DISP = {"互斥（只执行最高优先级）": "exclusive", "全部同帧命中": "all", "按顺序等待": "sequence"}
LINK_REV = {v: k for k, v in LINK_DISP.items()}
BTN_DISP = {"左键": "left", "右键": "right", "中键": "middle", "侧键 X1": "x1", "侧键 X2": "x2"}
BTN_REV = {v: k for k, v in BTN_DISP.items()}
POS_DISP = {"图标中心": "center", "图标框内随机": "rect_random", "图标左上角": "top_left",
            "偏移跟随": "follow", "固定坐标": "absolute"}
POS_REV = {v: k for k, v in POS_DISP.items()}
TO_DISP = {"相对偏移": "offset", "绝对坐标": "absolute"}
TO_REV = {v: k for k, v in TO_DISP.items()}
STOP_DISP = {"停止本监控": "monitor", "停止整个脚本": "script"}
STOP_REV = {v: k for k, v in STOP_DISP.items()}
OP_DISP = {"运行": "run", "暂停": "pause", "继续": "resume", "停止": "stop"}
OP_REV = {v: k for k, v in OP_DISP.items()}
TARGET_DISP = {"当前脚本": "current", "指定脚本": "script"}
TARGET_REV = {v: k for k, v in TARGET_DISP.items()}
BUSY_DISP = {"切换": "switch", "忽略": "ignore"}
BUSY_REV = {v: k for k, v in BUSY_DISP.items()}


FIELD_TIPS = {
    "hold_ms": "按住时长（ms）",
    "interval_ms": "多次执行之间的间隔（ms）",
    "amount": "滚轮滚动格数",
    "delay_ms": "动作前延迟（ms）",
    "clicks": "点击次数",
    "ms": "等待时长（ms）",
    "text": "要输入或通知的文本",
    "anchor_x": "脚本锚点 X 坐标",
    "anchor_y": "脚本锚点 Y 坐标",
}


class ActionDialog(ctk.CTkToplevel):
    def __init__(self, master, action=None, on_save=None, inline: bool = False):
        super().__init__(master)
        self.on_save = on_save
        self.inline = inline
        self.action = copy.deepcopy(action or {"type": "click"})
        self.title("编辑动作")
        self.geometry("620x660")
        self.minsize(620, 620)
        self.resizable(False, True)
        self.configure(fg_color=theme.BG)
        self.transient(master)
        apply_window_icon(self)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.type_var = tk.StringVar(value=TYPE_NAMES.get(str(self.action.get("type", "click")), "单击 / 双击 / 长按"))
        self.vars = {}
        self._build()
        self.after(100, self._center)

    def _build(self):
        label(self, "动作类型", 11, theme.TEXT).pack(anchor="w", padx=18, pady=(14, 4))
        self.type_menu = ctk.CTkOptionMenu(self, variable=self.type_var,
                                           values=[n for _k, n in ACTION_TYPES],
                                           command=lambda _v: self._rebuild(),
                                           fg_color=theme.CARD2, button_color=theme.LINE)
        self.type_menu.pack(fill="x", padx=18)
        self.form = ctk.CTkScrollableFrame(self, fg_color=theme.CARD, corner_radius=10)
        self.form.pack(fill="both", expand=True, padx=18, pady=10)
        self._rebuild()
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=12)
        ctk.CTkButton(row, text="保存动作", height=36, fg_color=theme.ACCENT,
                      text_color="#04121f", command=self._save).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="取消", width=84, height=36, fg_color=theme.CARD2,
                      hover_color=theme.LINE, command=self.destroy).pack(side="left", padx=(8, 0))

    def _key_for_name(self, name):
        for k, n in ACTION_TYPES:
            if n == name:
                return k
        return "click"

    def _clear(self):
        for w in list(self.form.winfo_children()):
            w.destroy()
        self.vars.clear()

    def _field(self, key, text, default="", width=220, parent=None):
        row = ctk.CTkFrame(parent or self.form, fg_color="transparent")
        row.pack(fill="x", pady=3)
        label(row, text, 10, theme.MUTED, width=100, anchor="w").pack(side="left")
        var = tk.StringVar(value=str(default))
        e = entry(row, width=width, textvariable=var)
        e.pack(side="left")
        Tooltip(e, FIELD_TIPS.get(key, text))
        self.vars[key] = var
        return var

    def _combo(self, key, text, values, default, command=None):
        row = ctk.CTkFrame(self.form, fg_color="transparent")
        row.pack(fill="x", pady=3)
        label(row, text, 10, theme.MUTED, width=100, anchor="w").pack(side="left")
        var = tk.StringVar(value=str(default))
        m = ctk.CTkOptionMenu(row, variable=var, values=values, width=180, height=28,
                              fg_color=theme.CARD2, button_color=theme.LINE,
                              command=command)
        m.pack(side="left")
        Tooltip(m, FIELD_TIPS.get(key, text))
        self.vars[key] = var
        return var

    def _switch(self, key, text, default):
        var = tk.BooleanVar(value=bool(default))
        sw = ctk.CTkSwitch(self.form, text=text, variable=var, font=(theme.FONT, theme.fs(10)),
                           progress_color=theme.ACCENT)
        sw.pack(anchor="w", pady=4)
        Tooltip(sw, FIELD_TIPS.get(key, text))
        self.vars[key] = var
        return var

    def _rebuild(self):
        self._clear()
        t = self._key_for_name(self.type_var.get())
        a = dict(self.action)
        if "op" in self.vars:
            a["op"] = OP_DISP.get(self._v("op", "运行"), a.get("op", "run"))
        if "target" in self.vars:
            a["target"] = TARGET_DISP.get(self._v("target", "当前脚本"),
                                          a.get("target", "current"))
        if "path" in self.vars:
            name = self._v("path", "")
            if name and name != "（选择脚本）":
                a["path"] = "scripts/" + name
        self._field("delay_ms", "执行前延迟(ms)", a.get("delay_ms", 0), 100)
        if t == "click":
            self._combo("button", "鼠标键", list(BTN_DISP.keys()),
                        BTN_REV.get(a.get("button", "left"), "左键"))
            self._field("clicks", "点击次数", a.get("clicks", 1), 80)
            self._field("interval_ms", "双击间隔(ms)", a.get("interval_ms", 70), 80)
            self._field("hold_ms", "按住时长(ms)", a.get("hold_ms", 40), 80)
            self._position_fields(a)
            self._random_fields(a)
        elif t == "long_press":
            self._combo("button", "鼠标键", list(BTN_DISP.keys()),
                        BTN_REV.get(a.get("button", "left"), "左键"))
            self._field("hold_ms", "按住时长(ms)", a.get("hold_ms", 1000), 100)
            self._position_fields(a)
            self._random_fields(a)
        elif t == "wheel":
            self._combo("direction", "方向", ["向上", "向下"],
                        "向下" if str(a.get("direction", "up")) == "down" else "向上")
            self._field("amount", "滚动格数", a.get("amount", 3), 80)
            self._field("interval_ms", "每格间隔(ms)", a.get("interval_ms", 60), 80)
        elif t == "key":
            self.vars["keys"] = tk.StringVar(value=str(a.get("keys", "") or ""))
            krow = ctk.CTkFrame(self.form, fg_color="transparent")
            krow.pack(fill="x", pady=3)
            label(krow, "按键", 10, theme.MUTED, width=100, anchor="w").pack(side="left")
            self._key_result = label(krow, str(a.get("keys", "") or "未采集"), 10, theme.TEXT)
            self._key_result.pack(side="left", padx=(0, 10))
            ctk.CTkButton(krow, text="采集按键", width=110, height=28,
                          fg_color=theme.ACCENT, text_color="#04121f",
                          font=(theme.FONT, theme.fs(10)),
                          command=self._capture_key).pack(side="left")
            self._field("hold_ms", "按住时长(ms)", a.get("hold_ms", 40), 80)
        elif t == "script":
            if self.inline:
                self._combo("op", "操作", ["运行", "停止"],
                            "停止" if str(a.get("op")) == "stop" else "运行",
                            command=lambda _v: self._rebuild())
                self.vars["target"] = tk.StringVar(value="指定脚本")
                self.vars["busy"] = tk.StringVar(value="切换")
                self.vars["wait"] = tk.BooleanVar(value=True)
                if str(a.get("op")) != "stop":
                    names = ["（选择脚本）"] + [p.name for p in list_scripts()]
                    self._combo("path", "脚本", names,
                                Path(str(a.get("path", ""))).name or "（选择脚本）")
                    self._field("anchor_x", "锚点 X（可空）",
                                (a.get("anchor") or ["", ""])[0], 80)
                    self._field("anchor_y", "锚点 Y（可空）",
                                (a.get("anchor") or ["", ""])[1], 80)
            else:
                self._combo("op", "操作", list(OP_DISP.keys()),
                            OP_REV.get(a.get("op", "run"), "运行"),
                            command=lambda _v: self._rebuild())
                self._combo("target", "目标", list(TARGET_DISP.keys()),
                            TARGET_REV.get(a.get("target", "current"), "当前脚本"),
                            command=lambda _v: self._rebuild())
                op = str(a.get("op", "run"))
                target = str(a.get("target", "current"))
                if op == "run":
                    names = ["（选择脚本）"] + [p.name for p in list_scripts()]
                    self._combo("path", "脚本", names,
                                Path(str(a.get("path", ""))).name or "（选择脚本）")
                    self._combo("busy", "忙碌时", list(BUSY_DISP.keys()),
                                BUSY_REV.get(a.get("busy", "switch"), "切换"))
                    self._switch("wait", "等待脚本执行完", a.get("wait", True))
                    if target == "script":
                        self._field("anchor_x", "锚点 X（可空）",
                                    (a.get("anchor") or ["", ""])[0], 80)
                        self._field("anchor_y", "锚点 Y（可空）",
                                    (a.get("anchor") or ["", ""])[1], 80)
                elif target == "script":
                    names = ["（选择脚本）"] + [p.name for p in list_scripts()]
                    self._combo("path", "脚本", names,
                                Path(str(a.get("path", ""))).name or "（选择脚本）")
        elif t == "wait":
            self._field("ms", "等待毫秒", a.get("ms", 200), 100)
        elif t == "text":
            self._field("text", "文本", a.get("text", ""), 260)
        elif t == "move":
            self._position_fields(a)
            self._random_fields(a)
        elif t == "drag":
            self._combo("button", "鼠标键", list(BTN_DISP.keys()),
                        BTN_REV.get(a.get("button", "left"), "左键"))
            self._combo("to_mode", "终点模式", list(TO_DISP.keys()),
                        TO_REV.get(a.get("to_mode", "offset"), "相对偏移"))
            self._field("offset_x", "终点/偏移 X", (a.get("offset") or [0, 0])[0], 80)
            self._field("offset_y", "终点/偏移 Y", (a.get("offset") or [0, 0])[1], 80)
            ctk.CTkButton(self.form, text="屏幕取终点", width=110, height=28,
                          fg_color=theme.ACCENT, text_color="#04121f",
                          font=(theme.FONT, theme.fs(10)),
                          command=self._pick_drag_end).pack(anchor="w", pady=3)
            self._field("duration_ms", "时长(ms)", a.get("duration_ms", 300), 80)
            self._field("steps", "步数", a.get("steps", 20), 60)
        elif t == "notify":
            self._field("text", "通知文本", a.get("text", ""), 260)
        elif t == "stop":
            self._combo("scope", "停止范围", list(STOP_DISP.keys()),
                        STOP_REV.get(a.get("scope", "monitor"), "停止本监控"))

    def _position_fields(self, a):
        self._pos_action = a
        self._combo("position", "位置", list(POS_DISP.keys()),
                    POS_REV.get(a.get("position", "center"), "图标中心"),
                    command=lambda _v: self._refresh_position_fields())
        self.pos_box = ctk.CTkFrame(self.form, fg_color="transparent", height=1)
        self.pos_box.pack(fill="x")
        self._refresh_position_fields()

    def _refresh_position_fields(self):
        a = getattr(self, "_pos_action", {})
        try:
            for w in list(self.pos_box.winfo_children()):
                w.destroy()
        except Exception:
            return
        pos = POS_DISP.get(self._v("position", "图标中心"), "center")
        if pos == "absolute":
            p = a.get("point") or [0, 0]
            self._field("point_x", "固定坐标 X", self._v("point_x", p[0]), 80, self.pos_box)
            self._field("point_y", "固定坐标 Y", self._v("point_y", p[1]), 80, self.pos_box)
            prow = ctk.CTkFrame(self.pos_box, fg_color="transparent")
            prow.pack(fill="x", pady=3)
            label(prow, "定位", 10, theme.MUTED, width=100, anchor="w").pack(side="left")
            ctk.CTkButton(prow, text="屏幕取点", width=96, height=28, fg_color=theme.ACCENT,
                          text_color="#04121f", font=(theme.FONT, theme.fs(10)),
                          command=self._pick_point).pack(side="left")
        elif pos == "follow":
            off = a.get("offset") or [0, 0]
            self._field("offset_x", "偏移 X", self._v("offset_x", off[0]), 80, self.pos_box)
            self._field("offset_y", "偏移 Y", self._v("offset_y", off[1]), 80, self.pos_box)

    def _random_fields(self, a):
        self._rand_action = a
        self.random_on_var = tk.BooleanVar(
            value=bool(int(a.get("random_x", 0) or 0) or int(a.get("random_y", 0) or 0)))
        row = ctk.CTkFrame(self.form, fg_color="transparent")
        row.pack(fill="x", pady=3)
        label(row, "随机偏移", 10, theme.MUTED, width=100, anchor="w").pack(side="left")
        ctk.CTkSwitch(row, text="", variable=self.random_on_var,
                      progress_color=theme.ACCENT,
                      command=self._refresh_random_fields).pack(side="left", padx=(0, 10))
        self.rand_box = ctk.CTkFrame(row, fg_color="transparent", height=1)
        self.rand_box.pack(side="left", fill="x", expand=True)
        self._refresh_random_fields()

    def _refresh_random_fields(self):
        a = getattr(self, "_rand_action", {})
        try:
            for w in list(self.rand_box.winfo_children()):
                w.destroy()
        except Exception:
            return
        try:
            on = bool(self.random_on_var.get())
        except Exception:
            on = False
        if not on:
            return
        label(self.rand_box, "X ±", 9, theme.MUTED).pack(side="left")
        vx = tk.StringVar(value=str(self._v("random_x", abs(int(a.get("random_x", 0) or 0)))))
        self.vars["random_x"] = vx
        entry(self.rand_box, width=70, textvariable=vx).pack(side="left", padx=(4, 10))
        label(self.rand_box, "Y ±", 9, theme.MUTED).pack(side="left")
        vy = tk.StringVar(value=str(self._v("random_y", abs(int(a.get("random_y", 0) or 0)))))
        self.vars["random_y"] = vy
        entry(self.rand_box, width=70, textvariable=vy).pack(side="left", padx=4)

    def _random_dict(self):
        try:
            on = bool(self.random_on_var.get())
        except Exception:
            on = False
        if not on:
            return {"random_x": 0, "random_y": 0}
        return {"random_x": abs(int(float(self._v("random_x", 0) or 0))),
                "random_y": abs(int(float(self._v("random_y", 0) or 0)))}

    def _v(self, key, default=""):
        v = self.vars.get(key)
        if v is None:
            return default
        try:
            return v.get()
        except Exception:
            return default

    def _save(self):
        t = self._key_for_name(self.type_var.get())
        out = {"type": t, "delay_ms": int(float(self._v("delay_ms", 0) or 0))}
        if t in ("click", "long_press", "drag"):
            out["button"] = BTN_DISP.get(self._v("button", "左键"), "left")
        if t == "click":
            out["clicks"] = int(float(self._v("clicks", 1) or 1))
            out["interval_ms"] = int(float(self._v("interval_ms", 70) or 70))
            out["hold_ms"] = int(float(self._v("hold_ms", 40) or 40))
            out.update(self._pos_dict())
            out.update(self._random_dict())
        elif t == "long_press":
            out["hold_ms"] = int(float(self._v("hold_ms", 1000) or 1000))
            out.update(self._pos_dict())
            out.update(self._random_dict())
        elif t == "wheel":
            out["direction"] = "down" if self._v("direction", "向上") == "向下" else "up"
            out["amount"] = max(1, int(float(self._v("amount", 3) or 3)))
            out["interval_ms"] = max(0, int(float(self._v("interval_ms", 60) or 60)))
        elif t == "key":
            out["keys"] = self._v("keys", "")
            out["hold_ms"] = int(float(self._v("hold_ms", 40) or 40))
        elif t == "script":
            name = self._v("path", "")
            op = OP_DISP.get(self._v("op", "运行"), "run")
            if self.inline:
                op = "stop" if self._v("op", "运行") == "停止" else "run"
            out["op"] = op
            out["target"] = "current" if op != "run" else TARGET_DISP.get(
                self._v("target", "当前脚本"), "current")
            out["path"] = "" if name in ("", "（选择脚本）") else ("scripts/" + name)
            out["busy"] = BUSY_DISP.get(self._v("busy", "切换"), "switch")
            ax, ay = self._v("anchor_x", ""), self._v("anchor_y", "")
            if str(ax).strip() and str(ay).strip():
                out["anchor"] = [int(float(ax)), int(float(ay))]
            out["wait"] = bool(self._v("wait", True))
        elif t == "wait":
            out["ms"] = int(float(self._v("ms", 200) or 200))
        elif t == "text":
            out["text"] = self._v("text", "")
        elif t == "move":
            out.update(self._pos_dict())
            out.update(self._random_dict())
        elif t == "drag":
            out["to_mode"] = TO_DISP.get(self._v("to_mode", "相对偏移"), "offset")
            val = [int(float(self._v("offset_x", 0) or 0)),
                   int(float(self._v("offset_y", 0) or 0))]
            if out["to_mode"] == "absolute":
                out["to"] = val
            else:
                out["offset"] = val
            out["duration_ms"] = int(float(self._v("duration_ms", 300) or 300))
            out["steps"] = int(float(self._v("steps", 20) or 20))
        elif t == "notify":
            out["text"] = self._v("text", "")
        elif t == "stop":
            out["scope"] = STOP_DISP.get(self._v("scope", "停止本监控"), "monitor")
        if self.on_save:
            self.on_save(out)
        self.destroy()

    def _pos_dict(self):
        pos = POS_DISP.get(self._v("position", "图标中心"), "center")
        out = {"position": pos}
        if pos == "absolute":
            out["point"] = [int(float(self._v("point_x", 0) or 0)),
                            int(float(self._v("point_y", 0) or 0))]
        elif pos == "follow":
            out["offset"] = [int(float(self._v("offset_x", 0) or 0)),
                             int(float(self._v("offset_y", 0) or 0))]
        return out

    def _app_root(self):
        w = self.master
        while w is not None and not isinstance(w, ctk.CTk):
            w = getattr(w, "master", None)
        return w or self.master

    def _pick_with_minimize(self, on_pick):
        tops = []
        w = self
        root = None
        while w is not None:
            if isinstance(w, ctk.CTk):
                root = w
                break
            if isinstance(w, tk.Toplevel):
                tops.append(w)
            w = getattr(w, "master", None)
        for t in tops:
            try:
                t.withdraw()
            except Exception:
                pass
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
            for t in reversed(tops):
                try:
                    t.deiconify()
                    t.lift()
                    t.focus_force()
                except Exception:
                    pass

        def picked(x, y):
            on_pick(x, y)
            self.after(120, restore)

        def cancelled():
            self.after(120, restore)

        self.after(150, lambda: PointPicker(self, on_pick=picked, on_cancel=cancelled))

    def _pick_point(self):
        self._pick_with_minimize(self._on_point)

    def _on_point(self, x, y):
        if "point_x" in self.vars:
            self.vars["point_x"].set(str(int(x)))
        if "point_y" in self.vars:
            self.vars["point_y"].set(str(int(y)))
        if "position" in self.vars:
            self.vars["position"].set("固定坐标")
            try:
                self._refresh_position_fields()
            except Exception:
                pass

    def _capture_key(self):
        from .key_record_dialog import KeyRecordDialog

        def done(combo):
            self.vars["keys"].set(combo or "")
            try:
                self._key_result.configure(text=combo or "未采集")
            except Exception:
                pass

        KeyRecordDialog(self, on_done=done)

    def _pick_drag_end(self):
        self._pick_with_minimize(self._on_drag_point)

    def _on_drag_point(self, x, y):
        if "to_mode" in self.vars:
            self.vars["to_mode"].set("绝对坐标")
        if "offset_x" in self.vars:
            self.vars["offset_x"].set(str(int(x)))
        if "offset_y" in self.vars:
            self.vars["offset_y"].set(str(int(y)))

    def _center(self):
        center_window(self)


class RuleDialog(ctk.CTkToplevel):
    def __init__(self, master, rule=None, links=None, region_shot=None, region=None,
                 default_name="规则", on_save=None):
        super().__init__(master)
        self.on_save = on_save
        self.rule = normalize_rule(copy.deepcopy(rule or {}))
        if not rule:
            self.rule["name"] = default_name
        self.links = links or []
        self.region_shot = region_shot
        self.region = region or [0, 0, 0, 0]
        self.templates = [str(x) for x in (self.rule.get("templates") or []) if str(x)]
        if not self.templates and self.rule.get("template"):
            self.templates = [str(self.rule["template"])]
        self.template = self.templates[0] if self.templates else ""
        self.template_rect = list(self.rule.get("template_rect") or [])
        self.actions = list(self.rule.get("actions") or [])
        self.miss_actions = list(self.rule.get("miss_actions") or [])
        self._drag_kind = None
        self._drag_idx = None
        self._drag_motion_id = None
        self._drag_release_id = None
        self.title("编辑监控规则")
        self.geometry("1000x780")
        self.resizable(False, True)
        self.minsize(1000, 720)
        self.configure(fg_color=theme.BG)
        self.transient(master)
        apply_window_icon(self)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._build()
        self.after(100, self._center)

    def _build(self):
        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.pack(fill="both", expand=True, padx=0, pady=(10, 0))
        top = ctk.CTkFrame(self.scroll, fg_color=theme.CARD, corner_radius=12)
        top.pack(fill="x", padx=16, pady=(0, 8))
        grid = ctk.CTkFrame(top, fg_color="transparent")
        grid.pack(fill="x", padx=14, pady=10)
        for c in (1, 3, 5):
            grid.grid_columnconfigure(c, weight=0)

        label(grid, "规则名", 9, theme.MUTED).grid(row=0, column=0, sticky="w", pady=3)
        self.name_var = tk.StringVar(value=self.rule.get("name", "规则"))
        entry(grid, width=220, textvariable=self.name_var).grid(
            row=0, column=1, sticky="w", padx=(6, 28))
        label(grid, "优先级", 9, theme.MUTED).grid(row=0, column=2, sticky="w")
        self.priority_var = tk.StringVar(value=str(self.rule.get("priority", 50)))
        entry(grid, width=80, textvariable=self.priority_var).grid(
            row=0, column=3, sticky="w", padx=(6, 10))
        label(grid, "越大越先", 9).grid(row=0, column=4, sticky="w")

        label(grid, "阈值", 9, theme.MUTED).grid(row=1, column=0, sticky="w", pady=3)
        th = ctk.CTkFrame(grid, fg_color="transparent")
        th.grid(row=1, column=1, sticky="w", padx=(6, 28), pady=3)
        self.threshold_var = tk.DoubleVar(value=float(self.rule.get("threshold", 0.85)))
        self.threshold_slider = ctk.CTkSlider(th, from_=0.5, to=0.99, number_of_steps=49,
                                              width=200, variable=self.threshold_var,
                                              progress_color=theme.ACCENT,
                                              command=lambda _v: self.th_label.configure(
                                                  text="%.2f" % self.threshold_var.get()))
        self.threshold_slider.pack(side="left")
        Tooltip(self.threshold_slider, "相似度阈值：越高越严格，常用 0.85–0.95")
        self.th_label = ctk.CTkLabel(th, text="%.2f" % self.threshold_var.get(), width=42,
                                     font=(theme.MONO, theme.fs(10)), text_color=theme.ACCENT)
        self.th_label.pack(side="left", padx=6)

        label(grid, "命中冷却(ms)", 9, theme.MUTED).grid(row=1, column=2, sticky="w")
        self.cooldown_var = tk.StringVar(value=str(self.rule.get("cooldown_ms", 1000)))
        e_hit_cd = entry(grid, width=80, textvariable=self.cooldown_var)
        e_hit_cd.grid(row=1, column=3, sticky="w", padx=(6, 18))
        Tooltip(e_hit_cd, "命中冷却：两次命中动作之间的最小间隔")
        label(grid, "消失冷却(ms)", 9, theme.MUTED).grid(row=1, column=4, sticky="w")
        self.miss_cooldown_var = tk.StringVar(
            value=str(self.rule.get("miss_cooldown_ms", self.rule.get("cooldown_ms", 1000))))
        e_miss_cd = entry(grid, width=80, textvariable=self.miss_cooldown_var)
        e_miss_cd.grid(row=1, column=5, sticky="w", padx=(6, 10))
        Tooltip(e_miss_cd, "消失冷却：两次消失动作之间的最小间隔")

        label(grid, "持续时间(ms)", 9, theme.MUTED).grid(row=2, column=0, sticky="w", pady=3)
        self.present_ms_var = tk.StringVar(value=str(self.rule.get("present_ms", 300)))
        e_present = entry(grid, width=80, textvariable=self.present_ms_var)
        e_present.grid(row=2, column=1, sticky="w", padx=(6, 28))
        Tooltip(e_present, "连续命中超过该时长才算出现（ms）")
        label(grid, "消失时间(ms)", 9, theme.MUTED).grid(row=2, column=2, sticky="w")
        self.absent_ms_var = tk.StringVar(value=str(self.rule.get("absent_ms", 600)))
        e_absent = entry(grid, width=80, textvariable=self.absent_ms_var)
        e_absent.grid(row=2, column=3, sticky="w", padx=(6, 10))
        Tooltip(e_absent, "连续消失超过该时长才算消失（ms）；用于忽略短暂遮挡")
        label(grid, "轮询ms", 9, theme.MUTED).grid(row=2, column=4, sticky="w")
        self.poll_var = tk.StringVar(value=str(self.rule.get("poll_ms", 0)))
        e_poll = entry(grid, width=70, textvariable=self.poll_var)
        e_poll.grid(row=2, column=5, sticky="w", padx=6)
        Tooltip(e_poll, "该规则的检测间隔（ms）；0=跟随场景")
        label(grid, "0=跟随场景", 9).grid(row=2, column=6, sticky="w")

        self.enabled_var = tk.BooleanVar(value=bool(self.rule.get("enabled", True)))
        self.enabled_switch = ctk.CTkSwitch(grid, text="启用该规则", variable=self.enabled_var,
                                            font=(theme.FONT, theme.fs(9)),
                                            progress_color=theme.OK,
                                            command=self._on_enabled_switch)
        self.enabled_switch.grid(row=3, column=4, sticky="w", pady=(8, 2))
        self.resident_var = tk.BooleanVar(value=bool(self.rule.get("resident", False)))
        self.resident_switch = ctk.CTkSwitch(grid, text="常驻监控", variable=self.resident_var,
                                             font=(theme.FONT, theme.fs(9)),
                                             progress_color=theme.ACCENT,
                                             command=self._on_resident_switch)
        self.resident_switch.grid(row=3, column=5, columnspan=2, sticky="w",
                                  padx=(24, 0), pady=(8, 2))
        self._apply_switch_link()

        label(grid, "关联组", 9, theme.MUTED).grid(row=4, column=0, sticky="w", pady=(6, 3))
        cur_group = self.rule.get("group", "")
        cur_name = next((l.get("name", l.get("id", "")) for l in self.links
                         if l.get("id") == cur_group), "（独立）")
        self.group_var = tk.StringVar(value=cur_name)
        names = ["（独立）"] + [l.get("name", l.get("id", "")) for l in self.links]
        gm = ctk.CTkOptionMenu(grid, variable=self.group_var, values=names, width=200, height=28,
                               fg_color=theme.CARD2, button_color=theme.LINE,
                               font=(theme.FONT, theme.fs(10)),
                               dropdown_font=(theme.FONT, theme.fs(10)))
        gm.grid(row=4, column=1, columnspan=2, sticky="w", padx=(6, 0), pady=(6, 3))
        Tooltip(gm, "加入关联组后按组策略（互斥/全部/顺序）触发")

        label(grid, "二级搜索范围", 9, theme.MUTED).grid(
            row=5, column=0, sticky="w", pady=(6, 3))
        self.roi = list(self.rule.get("roi") or [0, 0, 0, 0])
        roi_btn = ctk.CTkFrame(grid, fg_color="transparent")
        roi_btn.grid(row=5, column=1, columnspan=2, sticky="w", padx=(6, 0), pady=(6, 3))
        self.btn_roi_all = ctk.CTkButton(roi_btn, text="整个监控区域", width=118, height=28,
                                         fg_color=theme.ACCENT, text_color="#04121f",
                                         hover_color="#0ea5e9", font=(theme.FONT, theme.fs(9)),
                                         command=self._clear_roi)
        self.btn_roi_all.pack(side="left")
        Tooltip(self.btn_roi_all, "默认：在整个监控区域内搜索")
        self.btn_roi_pick = ctk.CTkButton(roi_btn, text="框选二级范围", width=118, height=28,
                                          fg_color="transparent", text_color=theme.TEXT,
                                          hover_color=theme.CARD2, font=(theme.FONT, theme.fs(9)),
                                          command=self._select_roi)
        self.btn_roi_pick.pack(side="left", padx=8)
        Tooltip(self.btn_roi_pick, "只在框选的二级范围内搜索")
        self.roi_label = label(grid, "", 9, theme.MUTED, anchor="w")
        self.roi_label.grid(row=5, column=3, columnspan=4, sticky="w", padx=(16, 0))
        self._refresh_roi_label()

        icon_row = ctk.CTkFrame(top, fg_color="transparent")
        icon_row.pack(fill="x", padx=14, pady=(0, 6))
        self.icon_preview = ctk.CTkLabel(icon_row, text="未选择图标", fg_color=theme.BG2,
                                         corner_radius=8, width=96, height=64)
        self.icon_preview.pack(side="left")
        ctk.CTkButton(icon_row, text="添加模板（截图）", width=150, height=34,
                      fg_color=theme.OK, text_color="#04121f",
                      font=(theme.FONT, theme.fs(10)),
                      command=self._crop_icon).pack(side="left", padx=(10, 4))
        ctk.CTkButton(icon_row, text="从图标库添加", width=120, height=34,
                      fg_color=theme.PURPLE, text_color="white",
                      font=(theme.FONT, theme.fs(10)),
                      command=self._pick_from_library).pack(side="left", padx=4)
        self.tpl_label = label(icon_row, "", 9, theme.MUTED)
        self.tpl_label.pack(side="left", padx=6)
        label(top, "可添加多个图片模板，任意一个命中即算命中；建议只裁剪图标核心区域。",
              9, theme.MUTED, anchor="w", justify="left", wraplength=820).pack(
            anchor="w", padx=14, pady=(0, 4))
        self.tpl_box = AutoScrollFrame(top, height=150)
        self.tpl_box.pack(fill="x", padx=14, pady=(0, 8))
        self._refresh_templates()
        self._refresh_icon_preview()

        acts_row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        acts_row.pack(fill="x", padx=16, pady=(0, 8))
        acts_row.grid_columnconfigure(0, weight=1, uniform="acts")
        acts_row.grid_columnconfigure(1, weight=1, uniform="acts")

        act_card = Card(acts_row, "命中动作", "图标命中时按顺序执行")
        act_card.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        bar = ctk.CTkFrame(act_card.body, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 6))
        ctk.CTkButton(bar, text="添加动作", width=90, height=28, fg_color=theme.ACCENT,
                      text_color="#04121f", font=(theme.FONT, theme.fs(10)),
                      command=self._add_action).pack(side="left")
        label(bar, "执行次数", 9).pack(side="left", padx=(8, 2))
        self.fires_var = tk.StringVar(value=str(self.rule.get("fires_per_present", 1)))
        e_fires = entry(bar, width=44, textvariable=self.fires_var)
        e_fires.pack(side="left")
        Tooltip(e_fires, "每次出现执行次数：0=不限制，1=一次，N=最多N次")
        self.actions_scroll = ctk.CTkScrollableFrame(act_card.body, fg_color=theme.BG2,
                                                     corner_radius=8, height=190)
        self.actions_scroll.pack(fill="both", expand=True)
        self._refresh_actions()

        miss_card = Card(acts_row, "消失动作", "图标消失时按顺序执行")
        miss_card.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        mbar = ctk.CTkFrame(miss_card.body, fg_color="transparent")
        mbar.pack(fill="x", pady=(0, 6))
        ctk.CTkButton(mbar, text="添加消失动作", width=110, height=28, fg_color=theme.PURPLE,
                      text_color="white", font=(theme.FONT, theme.fs(10)),
                      command=self._add_miss_action).pack(side="left")
        label(mbar, "执行次数", 9).pack(side="left", padx=(8, 2))
        self.miss_fires_var = tk.StringVar(value=str(self.rule.get("fires_per_absence", 1)))
        e_mfires = entry(mbar, width=44, textvariable=self.miss_fires_var)
        e_mfires.pack(side="left")
        Tooltip(e_mfires, "每次消失执行次数：0=不限制，1=一次，N=最多N次")
        self.miss_scroll = ctk.CTkScrollableFrame(miss_card.body, fg_color=theme.BG2,
                                                   corner_radius=8, height=190)
        self.miss_scroll.pack(fill="both", expand=True)
        self._refresh_miss_actions()

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkButton(bottom, text="保存规则", height=38, fg_color=theme.OK,
                      text_color="#04121f", font=(theme.FONT, theme.fs(11)),
                      command=self._save).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(bottom, text="取消", width=90, height=38, fg_color=theme.CARD2,
                      hover_color=theme.LINE, font=(theme.FONT, theme.fs(11)),
                      command=self.destroy).pack(side="left", padx=(8, 0))

    def _on_enabled_switch(self):
        if not self.enabled_var.get():
            self.resident_var.set(False)
        self._apply_switch_link()

    def _on_resident_switch(self):
        if not self.enabled_var.get():
            self.resident_var.set(False)
        self._apply_switch_link()

    def _apply_switch_link(self):
        try:
            self.resident_switch.configure(
                state="normal" if self.enabled_var.get() else "disabled")
        except Exception:
            pass

    def _refresh_roi_label(self):
        roi = self.roi or [0, 0, 0, 0]
        custom = len(roi) == 4 and roi[2] > 0 and roi[3] > 0
        if custom:
            self.roi_label.configure(text="X=%d  Y=%d  宽=%d  高=%d" % tuple(roi[:4]),
                                     text_color=theme.TEXT)
        else:
            self.roi_label.configure(text="默认：覆盖整个监控区域", text_color=theme.MUTED)
        try:
            self.btn_roi_all.configure(
                fg_color=theme.ACCENT if not custom else "transparent",
                text_color="#04121f" if not custom else theme.TEXT,
                hover_color="#0ea5e9" if not custom else theme.CARD2)
            self.btn_roi_pick.configure(
                fg_color=theme.ACCENT if custom else "transparent",
                text_color="#04121f" if custom else theme.TEXT,
                hover_color="#0ea5e9" if custom else theme.CARD2)
        except Exception:
            pass

    def _select_roi(self):
        if self.region_shot is None:
            self.roi_label.configure(text="请先在主界面框选监控区域")
            return
        AreaSelectDialog(self, self.region_shot, self.region, self.roi, on_select=self._on_roi)

    def _on_roi(self, roi):
        self.roi = list(roi)
        self._refresh_roi_label()

    def _clear_roi(self):
        self.roi = [0, 0, 0, 0]
        self._refresh_roi_label()

    def _pick_from_library(self):
        IconPickerDialog(self, on_pick=self._on_library_pick)

    def _on_library_pick(self, path):
        self._add_template("icons/" + Path(path).name)
        self.template_rect = []
        self._refresh_icon_preview()
        self._refresh_templates()

    def _crop_icon(self):
        if self.region_shot is None:
            return
        default = "icon_%s_%d" % (self.rule.get("id", "1"), len(self.templates) + 1)
        IconCropDialog(self, self.region_shot, self.region, default,
                       default_crop=self.template_rect or None, on_save=self._on_crop)

    def _on_crop(self, path, rect):
        self._add_template("icons/" + Path(path).name)
        self.template_rect = list(rect)
        self._refresh_icon_preview()
        self._refresh_templates()

    def _add_template(self, path: str):
        path = str(path or "")
        if not path:
            return
        if path not in self.templates:
            self.templates.append(path)
        self.template = self.templates[0] if self.templates else ""

    def _remove_template(self, idx: int):
        if 0 <= idx < len(self.templates):
            self.templates.pop(idx)
        self.template = self.templates[0] if self.templates else ""
        self._refresh_icon_preview()
        self._refresh_templates()

    def _refresh_templates(self):
        try:
            for w in list(self.tpl_box.inner.winfo_children()):
                w.destroy()
        except Exception:
            return
        if not self.templates:
            label(self.tpl_box.inner, "未选择模板；可添加 1 个或多个，任意命中即算命中",
                  9, theme.MUTED).grid(row=0, column=0, padx=8, pady=8, sticky="w")
        else:
            imgs = []
            for i, t in enumerate(self.templates):
                cell = ctk.CTkFrame(self.tpl_box.inner, fg_color=theme.CARD, corner_radius=8)
                cell.grid(row=i // 5, column=i % 5, padx=4, pady=4, sticky="n")
                img = load_image(t)
                if img is not None:
                    pil = to_ctk_image(img)
                    pil.thumbnail((60, 60), Image.LANCZOS)
                    ci = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
                    imgs.append(ci)
                    ctk.CTkLabel(cell, text="", image=ci, width=64, height=64).pack(
                        padx=4, pady=(4, 2))
                label(cell, Path(t).stem[:12], 8, theme.TEXT, anchor="center",
                      width=84).pack(padx=2)
                ctk.CTkButton(cell, text="删除", width=78, height=20,
                              fg_color=theme.DANGER, text_color="white",
                              font=(theme.FONT, theme.fs(8)),
                              command=lambda idx=i: self._remove_template(idx)).pack(
                    padx=4, pady=(2, 4))
            self._tpl_imgs = imgs
        try:
            self.tpl_label.configure(
                text=("已选 %d 个模板" % len(self.templates)) if self.templates else "未选择")
        except Exception:
            pass

    def _refresh_icon_preview(self):
        img = load_image(self.template) if self.template else None
        if img is None:
            self.icon_preview.configure(text="未选择图标", image=None, width=96, height=64)
            return
        pil = to_ctk_image(img)
        pil.thumbnail((96, 64), Image.LANCZOS)
        self._icon_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
        self.icon_preview.configure(image=self._icon_img, text="")

    def _refresh_actions(self):
        for w in list(self.actions_scroll.winfo_children()):
            w.destroy()
        if not self.actions:
            label(self.actions_scroll, "还没有动作，选择类型后点「添加动作」", 10,
                  theme.MUTED).pack(anchor="w", padx=8, pady=8)
            return
        for i, a in enumerate(self.actions):
            row = ctk.CTkFrame(self.actions_scroll, fg_color="transparent")
            row.pack(fill="x", pady=2)
            handle = ctk.CTkLabel(row, text="≡", width=18, font=(theme.FONT, theme.fs(12)),
                                  text_color=theme.MUTED, cursor="fleur")
            handle.pack(side="left", padx=(0, 2))
            handle.bind("<ButtonPress-1>", lambda _e, idx=i: self._drag_start("hit", idx))
            label(row, "%02d" % (i + 1), 9, theme.LINE, width=30).pack(side="left")
            label(row, action_summary(a), 10, theme.TEXT, anchor="w").pack(side="left", fill="x", expand=True)
            _delay = int(a.get("delay_ms", 0) or 0)
            if _delay:
                label(row, "+%dms" % _delay, 8, theme.MUTED, width=58).pack(side="left")
            ctk.CTkButton(row, text="编辑", width=50, height=24, fg_color=theme.CARD2,
                      font=(theme.FONT, theme.fs(9)),
                          command=lambda idx=i: self._edit_action(idx)).pack(side="left", padx=3)
            ctk.CTkButton(row, text="删除", width=50, height=24, fg_color=theme.DANGER,
                      font=(theme.FONT, theme.fs(9)),
                          command=lambda idx=i: self._delete_action(idx)).pack(side="left")

    def _drag_start(self, kind, idx):
        self._drag_kind = kind
        self._drag_idx = idx
        top = self.winfo_toplevel()
        self._drag_motion_id = top.bind("<B1-Motion>", self._drag_motion, add="+")
        self._drag_release_id = top.bind("<ButtonRelease-1>", self._drag_end, add="+")

    def _drag_motion(self, e):
        if self._drag_kind is None:
            return
        box = self.actions_scroll if self._drag_kind == "hit" else self.miss_scroll
        rows = [w for w in box.winfo_children() if isinstance(w, ctk.CTkFrame)]
        if not rows:
            return
        target = len(rows) - 1
        for i, row in enumerate(rows):
            top = row.winfo_rooty()
            if e.y_root < top + row.winfo_height() / 2:
                target = i
                break
        items = self.actions if self._drag_kind == "hit" else self.miss_actions
        idx = self._drag_idx
        if target != idx and 0 <= idx < len(items):
            item = items.pop(idx)
            items.insert(max(0, min(target, len(items))), item)
            self._drag_idx = target
            if self._drag_kind == "hit":
                self._refresh_actions()
            else:
                self._refresh_miss_actions()

    def _drag_end(self, _e=None):
        top = self.winfo_toplevel()
        try:
            if self._drag_motion_id:
                top.unbind("<B1-Motion>", self._drag_motion_id)
            if self._drag_release_id:
                top.unbind("<ButtonRelease-1>", self._drag_release_id)
        except Exception:
            pass
        self._drag_kind = None
        self._drag_idx = None
        self._drag_motion_id = None
        self._drag_release_id = None

    def _add_action(self):
        ActionDialog(self, {"type": "click"}, on_save=self._append_action)

    def _append_action(self, action):
        self.actions.append(action)
        self._refresh_actions()

    def _edit_action(self, idx):
        if 0 <= idx < len(self.actions):
            ActionDialog(self, self.actions[idx], on_save=lambda a, i=idx: self._replace_action(i, a))

    def _replace_action(self, idx, action):
        if 0 <= idx < len(self.actions):
            self.actions[idx] = action
            self._refresh_actions()

    def _delete_action(self, idx):
        if 0 <= idx < len(self.actions):
            self.actions.pop(idx)
            self._refresh_actions()

    def _refresh_miss_actions(self):
        for w in list(self.miss_scroll.winfo_children()):
            w.destroy()
        if not self.miss_actions:
            label(self.miss_scroll, "还没有消失动作（可留空）", 10,
                  theme.MUTED).pack(anchor="w", padx=8, pady=8)
            return
        for i, a in enumerate(self.miss_actions):
            row = ctk.CTkFrame(self.miss_scroll, fg_color="transparent")
            row.pack(fill="x", pady=2)
            handle = ctk.CTkLabel(row, text="≡", width=18, font=(theme.FONT, theme.fs(12)),
                                  text_color=theme.MUTED, cursor="fleur")
            handle.pack(side="left", padx=(0, 2))
            handle.bind("<ButtonPress-1>", lambda _e, idx=i: self._drag_start("miss", idx))
            label(row, "%02d" % (i + 1), 9, theme.LINE, width=30).pack(side="left")
            label(row, action_summary(a), 10, theme.TEXT, anchor="w").pack(
                side="left", fill="x", expand=True)
            _delay = int(a.get("delay_ms", 0) or 0)
            if _delay:
                label(row, "+%dms" % _delay, 8, theme.MUTED, width=58).pack(side="left")
            ctk.CTkButton(row, text="编辑", width=50, height=24, fg_color=theme.CARD2,
                      font=(theme.FONT, theme.fs(9)),
                          command=lambda idx=i: self._edit_miss_action(idx)).pack(side="left", padx=3)
            ctk.CTkButton(row, text="删除", width=50, height=24, fg_color=theme.DANGER,
                      font=(theme.FONT, theme.fs(9)),
                          command=lambda idx=i: self._delete_miss_action(idx)).pack(side="left")

    def _add_miss_action(self):
        ActionDialog(self, {"type": "click"}, on_save=self._append_miss_action)

    def _append_miss_action(self, action):
        self.miss_actions.append(action)
        self._refresh_miss_actions()

    def _edit_miss_action(self, idx):
        if 0 <= idx < len(self.miss_actions):
            ActionDialog(self, self.miss_actions[idx],
                         on_save=lambda a, i=idx: self._replace_miss_action(i, a))

    def _replace_miss_action(self, idx, action):
        if 0 <= idx < len(self.miss_actions):
            self.miss_actions[idx] = action
            self._refresh_miss_actions()

    def _delete_miss_action(self, idx):
        if 0 <= idx < len(self.miss_actions):
            self.miss_actions.pop(idx)
            self._refresh_miss_actions()

    def _validate(self):
        issues = []
        if not self.templates:
            issues.append("没有选择图标模板")
        for t in self.templates:
            img = load_image(t)
            if img is None:
                issues.append("模板不存在：%s" % t)
                continue
            h, w = img.shape[:2]
            if w < 8 or h < 8:
                issues.append("模板过小：%s（%dx%d）" % (Path(t).name, w, h))
        try:
            if float(self.threshold_var.get()) < 0.6:
                issues.append("阈值低于 0.60，可能误判")
        except Exception:
            pass
        roi = self.roi
        if len(roi) == 4 and roi[2] > 0 and (roi[0] < 0 or roi[1] < 0):
            issues.append("二级范围坐标不能为负")
        for title, acts in (("命中动作", self.actions), ("消失动作", self.miss_actions)):
            for a in acts:
                if (str(a.get("type")) == "script" and str(a.get("op", "run")) == "run"
                        and not a.get("path")):
                    issues.append("%s 中有“运行脚本”未选择脚本" % title)
        return issues

    def _save(self):
        issues = self._validate()
        if issues:
            text = "\n".join(issues[:6])
            if not messagebox.askyesno("保存前检查", text + "\n\n仍然保存吗？"):
                return
        r = normalize_rule(self.rule)
        r["name"] = self.name_var.get().strip() or "规则"
        r["priority"] = int(float(self.priority_var.get() or 50))
        r["threshold"] = float(self.threshold_var.get())
        r["cooldown_ms"] = max(0, int(float(self.cooldown_var.get() or 0)))
        r["miss_cooldown_ms"] = max(0, int(float(self.miss_cooldown_var.get() or 0)))
        r["present_ms"] = max(0, int(float(self.present_ms_var.get() or 0)))
        r["absent_ms"] = max(0, int(float(self.absent_ms_var.get() or 0)))
        r["fires_per_present"] = max(0, int(float(self.fires_var.get() or 0)))
        r["fires_per_absence"] = max(0, int(float(self.miss_fires_var.get() or 0)))
        r["poll_ms"] = max(0, int(float(self.poll_var.get() or 0)))
        r["enabled"] = bool(self.enabled_var.get())
        r["templates"] = list(self.templates)
        r["template"] = self.templates[0] if self.templates else ""
        r["template_rect"] = list(self.template_rect)
        r["roi"] = list(self.roi)
        r["actions"] = list(self.actions)
        r["resident"] = bool(self.resident_var.get()) and bool(r["enabled"])
        r["miss_actions"] = list(self.miss_actions)
        gname = self.group_var.get()
        if gname == "（独立）":
            r["group"] = ""
        else:
            link = next((l for l in self.links if l.get("name") == gname), None)
            r["group"] = link.get("id", "") if link else ""
        if self.on_save:
            self.on_save(r)
        self.destroy()

    def _center(self):
        center_window(self)


class LinkDialog(ctk.CTkToplevel):
    def __init__(self, master, link=None, rules=None, on_save=None):
        super().__init__(master)
        self.on_save = on_save
        self.link = copy.deepcopy(link or {})
        self.rules = rules or []
        self.title("关联组")
        self.geometry("620x640")
        self.minsize(620, 600)
        self.resizable(False, True)
        self.configure(fg_color=theme.BG)
        self.transient(master)
        apply_window_icon(self)
        self._build()
        self.after(100, self._center)

    def _build(self):
        label(self, "关联组名称", 11, theme.TEXT).pack(anchor="w", padx=20, pady=(14, 4))
        self.name_var = tk.StringVar(value=self.link.get("name", "关联组"))
        entry(self, width=300, textvariable=self.name_var).pack(anchor="w", padx=20)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=8)
        label(row, "类型", 10, theme.MUTED).pack(side="left")
        self.type_var = tk.StringVar(value=LINK_REV.get(self.link.get("type", "exclusive"), "互斥（只执行最高优先级）"))
        ctk.CTkOptionMenu(row, variable=self.type_var,
                          values=list(LINK_DISP.keys()), width=200, height=28,
                          fg_color=theme.CARD2, button_color=theme.LINE).pack(side="left", padx=6)
        label(row, "优先级", 10, theme.MUTED).pack(side="left", padx=(14, 0))
        self.priority_var = tk.StringVar(value=str(self.link.get("priority", 50)))
        entry(row, width=70, textvariable=self.priority_var).pack(side="left", padx=4)
        label(row, "超时(ms)", 10, theme.MUTED).pack(side="left", padx=(14, 0))
        self.timeout_var = tk.StringVar(value=str(self.link.get("timeout_ms", 3000)))
        entry(row, width=80, textvariable=self.timeout_var).pack(side="left", padx=4)

        label(self, "成员规则", 11, theme.TEXT).pack(anchor="w", padx=20, pady=(8, 4))
        self.member_vars = {}
        box = ctk.CTkScrollableFrame(self, fg_color=theme.CARD, corner_radius=10)
        box.pack(fill="both", expand=True, padx=20, pady=6)
        old = set(self.link.get("members") or [])
        for r in self.rules:
            var = tk.BooleanVar(value=r.get("id") in old)
            ctk.CTkCheckBox(box, text="%s  (优先级 %d)" % (r.get("name", r.get("id")), r.get("priority", 50)),
                            variable=var, font=(theme.FONT, theme.fs(10)),
                            progress_color=theme.ACCENT).pack(anchor="w", pady=3, padx=6)
            self.member_vars[r.get("id")] = var
        row2 = ctk.CTkFrame(self, fg_color="transparent")
        row2.pack(fill="x", padx=20, pady=12)
        ctk.CTkButton(row2, text="保存关联组", height=36, fg_color=theme.ACCENT,
                      text_color="#04121f", command=self._save).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row2, text="取消", width=84, height=36, fg_color=theme.CARD2,
                      hover_color=theme.LINE, command=self.destroy).pack(side="left", padx=(8, 0))

    def _save(self):
        d = normalize_link(self.link)
        d["name"] = self.name_var.get().strip() or "关联组"
        d["type"] = LINK_DISP.get(self.type_var.get(), "exclusive")
        d["priority"] = int(float(self.priority_var.get() or 50))
        d["timeout_ms"] = max(0, int(float(self.timeout_var.get() or 3000)))
        d["members"] = [rid for rid, var in self.member_vars.items() if var.get()]
        if self.on_save:
            self.on_save(d)
        self.destroy()

    def _center(self):
        center_window(self)


class RulesWindow(ctk.CTkToplevel):
    def __init__(self, master, group, region_shot, region, on_save=None, on_insert=None):
        super().__init__(master)
        self.on_save = on_save
        self.on_insert = on_insert
        self.group = normalize_monitor_group(copy.deepcopy(group or {}))
        self.region_shot = region_shot
        self.region = [int(v) for v in (region or [0, 0, 0, 0])]
        self.group["region"] = list(self.region)
        self.selected_rule_id = self.group["rules"][0]["id"] if self.group["rules"] else ""
        self.title("多图标规则管理")
        self.geometry("1120x800")
        self.minsize(980, 680)
        self.configure(fg_color=theme.BG)
        self.transient(master)
        apply_window_icon(self)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._build()
        self._refresh_rules()
        self._refresh_links()
        self.after(100, self._center)

    def _build(self):
        top = ctk.CTkFrame(self, fg_color=theme.CARD, corner_radius=12)
        top.pack(fill="x", padx=16, pady=(14, 8))
        grid = ctk.CTkFrame(top, fg_color="transparent")
        grid.pack(fill="x", padx=14, pady=10)
        grid.grid_columnconfigure(1, weight=1)
        grid.grid_columnconfigure(3, weight=1)
        grid.grid_columnconfigure(5, weight=1)

        label(grid, "方案名", 10, theme.MUTED).grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar(value=self.group.get("name", "监控组"))
        entry(grid, width=150, textvariable=self.name_var).grid(row=0, column=1, sticky="ew", padx=6)
        label(grid, "区域", 10, theme.MUTED).grid(row=0, column=2, sticky="w", padx=(10, 0))
        self.region_label = label(grid, "X=%d Y=%d W=%d H=%d" % tuple(self.region), 9, theme.TEXT)
        self.region_label.grid(row=0, column=3, columnspan=2, sticky="w")
        label(grid, "轮询(ms)", 10, theme.MUTED).grid(row=0, column=5, sticky="w", padx=(10, 0))
        self.poll_var = tk.StringVar(value=str(self.group.get("poll_ms", 150)))
        entry(grid, width=70, textvariable=self.poll_var).grid(row=0, column=6, padx=6)

        label(grid, "匹配模式", 10, theme.MUTED).grid(row=1, column=0, sticky="w", pady=4)
        self.mode_var = tk.StringVar(value=MATCH_REV.get(self.group.get("match_mode", "any"), "任意匹配"))
        ctk.CTkOptionMenu(grid, variable=self.mode_var,
                          values=list(MATCH_DISP.keys()), width=110, height=28,
                          fg_color=theme.CARD2, button_color=theme.LINE).grid(
            row=1, column=1, sticky="w", padx=6, pady=4)
        label(grid, "每轮最多", 10, theme.MUTED).grid(row=1, column=2, sticky="w", padx=(10, 0))
        self.max_var = tk.StringVar(value=str(self.group.get("max_actions_per_tick", 1)))
        entry(grid, width=60, textvariable=self.max_var).grid(row=1, column=3, sticky="w", padx=6)
        label(grid, "0=不限", 9).grid(row=1, column=4, sticky="w")
        label(grid, "运行模式", 10, theme.MUTED).grid(row=1, column=5, sticky="w", padx=(10, 0))
        self.run_var = tk.StringVar(value=RUN_REV.get(self.group.get("run_mode", "continuous"), "持续运行"))
        ctk.CTkOptionMenu(grid, variable=self.run_var, values=list(RUN_DISP.keys()),
                          width=110, height=28, fg_color=theme.CARD2,
                          button_color=theme.LINE).grid(row=1, column=6, padx=6)
        label(grid, "超时(ms)", 10, theme.MUTED).grid(row=1, column=7, sticky="w", padx=(10, 0))
        self.timeout_var = tk.StringVar(value=str(self.group.get("timeout_ms", 0)))
        entry(grid, width=80, textvariable=self.timeout_var).grid(row=1, column=8, padx=6)
        self.stop_var = tk.BooleanVar(value=bool(self.group.get("stop_on_match", False)))
        ctk.CTkSwitch(grid, text="命中一次后停止", variable=self.stop_var,
                      font=(theme.FONT, theme.fs(10)), progress_color=theme.ACCENT).grid(
            row=1, column=9, padx=(10, 0))
        label(grid, "组宽限ms", 10, theme.MUTED).grid(row=1, column=10, sticky="w", padx=(10, 0))
        self.grace_var = tk.StringVar(value=str(self.group.get("grace_ms", 600)))
        entry(grid, width=70, textvariable=self.grace_var).grid(row=1, column=11, padx=6)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=6)
        body.grid_columnconfigure(0, weight=3, uniform="rw")
        body.grid_columnconfigure(1, weight=2, uniform="rw")
        body.grid_rowconfigure(0, weight=1)

        left = Card(body, "图标规则", "优先级从高到低执行")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        bar = ctk.CTkFrame(left.body, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 6))
        self._b(bar, "添加规则", self._add_rule, 92, theme.OK, "#04121f")
        self._b(bar, "编辑", self._edit_rule, 64)
        self._b(bar, "复制", self._copy_rule, 64)
        self._b(bar, "删除", self._delete_rule, 64, theme.DANGER, "white")
        self._b(bar, "上移", lambda: self._move_rule(-1), 64)
        self._b(bar, "下移", lambda: self._move_rule(1), 64)
        self.rules_scroll = ctk.CTkScrollableFrame(left.body, fg_color=theme.BG2,
                                                   corner_radius=8)
        self.rules_scroll.pack(fill="both", expand=True)
        self.rule_hint = label(left.body, "添加规则后，在此按优先级排列", 9)
        self.rule_hint.pack(anchor="w", pady=(6, 0))

        right = Card(body, "关联组", "可选：互斥 / 同时满足 / 顺序满足")
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        rb = ctk.CTkFrame(right.body, fg_color="transparent")
        rb.pack(fill="x", pady=(0, 6))
        self._b(rb, "新建关联组", self._add_link, 104, theme.ACCENT, "#04121f")
        self._b(rb, "编辑", self._edit_link, 64)
        self._b(rb, "删除", self._delete_link, 64, theme.DANGER, "white")
        self._b(rb, "全部消失动作", self._edit_none_actions, 104, theme.PURPLE, "white")
        self.links_scroll = ctk.CTkScrollableFrame(right.body, fg_color=theme.BG2,
                                                   corner_radius=8)
        self.links_scroll.pack(fill="both", expand=True)
        label(right.body, "互斥=只执行最高优先级成员；同时满足=全部同帧命中；顺序满足=按成员顺序等待。",
              9).pack(anchor="w", pady=(6, 0))

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(4, 12))
        self.status = label(bottom, "就绪", 9)
        self.status.pack(side="left")
        ctk.CTkButton(bottom, text="插入到当前脚本", width=150, height=38,
                      fg_color=theme.ACCENT, text_color="#04121f",
                      command=self._insert).pack(side="right", padx=6)
        ctk.CTkButton(bottom, text="预览执行顺序", width=120, height=38,
                      fg_color=theme.PURPLE, text_color="white",
                      command=self._preview_order).pack(side="right", padx=6)
        ctk.CTkButton(bottom, text="保存规则集", width=120, height=38,
                      fg_color=theme.OK, text_color="#04121f",
                      command=self._save).pack(side="right", padx=6)
        ctk.CTkButton(bottom, text="关闭", width=80, height=38, fg_color=theme.CARD2,
                      hover_color=theme.LINE, command=self.destroy).pack(side="right")

    def _b(self, parent, text, cmd, width, color=theme.CARD2, fg=theme.TEXT):
        return ctk.CTkButton(parent, text=text, command=cmd, width=width, height=28,
                             fg_color=color, hover_color=theme.LINE, text_color=fg,
                             font=(theme.FONT, theme.fs(9))).pack(side="left", padx=2)

    def _collect(self):
        g = normalize_monitor_group(self.group)
        g["name"] = self.name_var.get().strip() or "监控组"
        g["region"] = list(self.region)
        g["poll_ms"] = max(20, int(float(self.poll_var.get() or 150)))
        g["match_mode"] = MATCH_DISP.get(self.mode_var.get(), "any")
        g["max_actions_per_tick"] = max(0, int(float(self.max_var.get() or 0)))
        g["run_mode"] = RUN_DISP.get(self.run_var.get(), "continuous")
        g["timeout_ms"] = max(0, int(float(self.timeout_var.get() or 0)))
        g["stop_on_match"] = bool(self.stop_var.get())
        g["grace_ms"] = max(0, int(float(self.grace_var.get() or 600)))
        g["rules"] = self.group["rules"]
        g["links"] = self.group["links"]
        return normalize_monitor_group(g)

    def _refresh_rules(self):
        for w in list(self.rules_scroll.winfo_children()):
            w.destroy()
        rules = list(self.group["rules"])
        if not rules:
            label(self.rules_scroll, "还没有规则，点「添加规则」", 10, theme.MUTED).pack(
                anchor="w", padx=8, pady=8)
            self.rule_hint.configure(text="0 条规则")
            return
        for i, r in enumerate(rules):
            row = ctk.CTkFrame(self.rules_scroll,
                               fg_color=theme.CARD2 if r["id"] == self.selected_rule_id else "transparent",
                               corner_radius=8)
            row.pack(fill="x", pady=2, padx=2)
            var = tk.BooleanVar(value=bool(r.get("enabled", True)))
            ctk.CTkCheckBox(row, text="", variable=var, width=24,
                            command=lambda rid=r["id"], v=var: self._toggle_rule(rid, v.get())).pack(
                side="left", padx=(6, 2))
            img = load_image((r.get("templates") or [r.get("template")])[0]) if (r.get("templates") or r.get("template")) else None
            if img is not None:
                pil = to_ctk_image(img)
                pil.thumbnail((36, 36), Image.LANCZOS)
                ci = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
                lbl = ctk.CTkLabel(row, text="", image=ci, width=40)
                lbl._img_ref = ci
                lbl.pack(side="left", padx=2)
            else:
                ctk.CTkLabel(row, text="无图", width=40, text_color=theme.MUTED,
                             font=(theme.FONT, theme.fs(8))).pack(side="left", padx=2)
            ctk.CTkButton(row, text="%s  P%d" % (r.get("name", "规则"), r.get("priority", 50)),
                          anchor="w", fg_color="transparent", hover_color=theme.LINE,
                          text_color=theme.TEXT, font=(theme.FONT, theme.fs(10)),
                          command=lambda rid=r["id"]: self._select(rid)).pack(
                side="left", fill="x", expand=True, padx=2)
            summary = " → ".join(action_summary(a) for a in (r.get("actions") or [])[:2]) or "无动作"
            ctk.CTkLabel(row, text=summary[:42], font=(theme.MONO, theme.fs(8)),
                         text_color=theme.MUTED, width=230, anchor="w").pack(side="left")
            ctk.CTkButton(row, text="编辑", width=44, height=24, fg_color=theme.CARD2,
                          command=lambda rid=r["id"]: self._edit_rule(rid)).pack(side="left", padx=2)
        self.rule_hint.configure(text="%d 条规则；数值越大越先执行" % len(rules))

    def _edit_none_actions(self):
        from .action_list import ActionList
        win = ctk.CTkToplevel(self)
        win.title("全部规则消失时动作")
        win.geometry("620x560")
        win.minsize(620, 520)
        win.resizable(False, True)
        win.configure(fg_color=theme.BG)
        win.transient(self)
        apply_window_icon(win)
        label(win, "所有常驻规则都不存在时执行（带 grace 宽限）", 11, theme.TEXT).pack(
            anchor="w", padx=16, pady=(14, 4))
        al = ActionList(win, self.group.get("on_none_present") or [], height=260)
        al.pack(fill="both", expand=True, padx=16, pady=6)
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=10)

        def save():
            self.group["on_none_present"] = al.get()
            self.status.configure(text="全部消失动作：%d 条" % len(al.get()))
            win.destroy()

        ctk.CTkButton(row, text="保存", height=34, fg_color=theme.OK,
                      text_color="#04121f", command=save).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="取消", width=80, height=34, fg_color=theme.CARD2,
                      hover_color=theme.LINE, command=win.destroy).pack(side="left", padx=8)

    def _refresh_links(self):
        for w in list(self.links_scroll.winfo_children()):
            w.destroy()
        links = self.group["links"]
        if not links:
            label(self.links_scroll, "没有关联组；规则默认互相独立", 10, theme.MUTED).pack(
                anchor="w", padx=8, pady=8)
            return
        by_id = {r["id"]: r for r in self.group["rules"]}
        for l in links:
            row = ctk.CTkFrame(self.links_scroll, fg_color=theme.CARD2, corner_radius=8)
            row.pack(fill="x", pady=3, padx=2)
            members = "、".join(by_id[x]["name"] for x in l.get("members", []) if x in by_id)
            label(row, "%s  [%s]" % (l.get("name", l.get("id")), LINK_REV.get(l.get("type"), l.get("type"))),
                  10, theme.TEXT, anchor="w").pack(anchor="w", padx=8, pady=(4, 0))
            label(row, "成员：%s   P%d" % (members or "无", l.get("priority", 50)),
                  9, theme.MUTED, anchor="w").pack(anchor="w", padx=8, pady=(0, 4))
            ctk.CTkButton(row, text="编辑", width=44, height=22, fg_color=theme.CARD,
                          command=lambda lid=l["id"]: self._edit_link(lid)).pack(
                side="right", padx=4, pady=2)

    def _select(self, rid):
        self.selected_rule_id = rid
        self._refresh_rules()

    def _toggle_rule(self, rid, val):
        for r in self.group["rules"]:
            if r["id"] == rid:
                r["enabled"] = bool(val)

    def _add_rule(self):
        RuleDialog(self, None, self.group["links"], self.region_shot, self.region,
                   default_name="规则%d" % (len(self.group["rules"]) + 1),
                   on_save=self._save_rule)

    def _edit_rule(self, rid=None):
        rid = rid or self.selected_rule_id
        r = next((x for x in self.group["rules"] if x["id"] == rid), None)
        if not r:
            return
        RuleDialog(self, r, self.group["links"], self.region_shot, self.region,
                   on_save=lambda nr: self._save_rule(nr, rid))

    def _save_rule(self, r, old_id=None):
        r = normalize_rule(r)
        if old_id:
            for i, x in enumerate(self.group["rules"]):
                if x["id"] == old_id:
                    r["id"] = old_id
                    self.group["rules"][i] = r
                    break
        else:
            used = {x["id"] for x in self.group["rules"]}
            base = r["id"] or ("r%d" % (len(used) + 1))
            rid = base
            i = 1
            while rid in used:
                rid = base + "_%d" % i
                i += 1
            r["id"] = rid
            self.group["rules"].append(r)
        self._apply_membership(r["id"], r.get("group", ""))
        self.selected_rule_id = r["id"]
        self.group = normalize_monitor_group(self.group)
        self._refresh_rules()
        self._refresh_links()

    def _copy_rule(self):
        r = next((x for x in self.group["rules"] if x["id"] == self.selected_rule_id), None)
        if not r:
            return
        nr = copy.deepcopy(r)
        nr["id"] = ""
        nr["name"] = r.get("name", "规则") + " 副本"
        self._save_rule(nr)

    def _delete_rule(self):
        rid = self.selected_rule_id
        self.group["rules"] = [r for r in self.group["rules"] if r["id"] != rid]
        for l in self.group["links"]:
            if rid in l["members"]:
                l["members"].remove(rid)
        self.selected_rule_id = self.group["rules"][0]["id"] if self.group["rules"] else ""
        self._refresh_rules()
        self._refresh_links()

    def _move_rule(self, delta):
        rules = self.group["rules"]
        idx = next((i for i, r in enumerate(rules) if r["id"] == self.selected_rule_id), -1)
        if idx < 0:
            return
        j = idx + delta
        if 0 <= j < len(rules):
            rules[idx], rules[j] = rules[j], rules[idx]
            self._refresh_rules()

    def _apply_membership(self, rid, gid):
        for l in self.group["links"]:
            if l["id"] != gid and rid in l["members"]:
                l["members"].remove(rid)
            if l["id"] == gid and rid not in l["members"]:
                l["members"].append(rid)
        for r in self.group["rules"]:
            if r["id"] == rid:
                r["group"] = gid

    def _add_link(self):
        LinkDialog(self, None, self.group["rules"], on_save=self._save_link)

    def _edit_link(self, lid=None):
        if lid is None:
            return
        l = next((x for x in self.group["links"] if x["id"] == lid), None)
        if not l:
            return
        LinkDialog(self, l, self.group["rules"], on_save=lambda nl: self._save_link(nl, lid))

    def _save_link(self, link, old_id=None):
        link = normalize_link(link)
        if old_id:
            link["id"] = old_id
            for i, x in enumerate(self.group["links"]):
                if x["id"] == old_id:
                    self.group["links"][i] = link
                    break
        else:
            used = {x["id"] for x in self.group["links"]}
            base = link["id"] or ("g%d" % (len(used) + 1))
            lid = base
            i = 1
            while lid in used:
                lid = base + "_%d" % i
                i += 1
            link["id"] = lid
            self.group["links"].append(link)
        members = set(link["members"])
        for r in self.group["rules"]:
            if r["id"] in members:
                r["group"] = link["id"]
            elif r.get("group") == link["id"]:
                r["group"] = ""
        self.group = normalize_monitor_group(self.group)
        self._refresh_rules()
        self._refresh_links()

    def _delete_link(self):
        # 当前选中规则所在组优先；否则删第一个
        lid = None
        r = next((x for x in self.group["rules"] if x["id"] == self.selected_rule_id), None)
        if r and r.get("group"):
            lid = r["group"]
        elif self.group["links"]:
            lid = self.group["links"][0]["id"]
        if not lid:
            return
        self.group["links"] = [l for l in self.group["links"] if l["id"] != lid]
        for r in self.group["rules"]:
            if r.get("group") == lid:
                r["group"] = ""
        self._refresh_rules()
        self._refresh_links()

    def _preview_order(self):
        self.group = self._collect()
        g = self.group
        region = g["region"]
        if len(region) != 4 or region[2] <= 0:
            self.status.configure(text="区域无效", text_color=theme.DANGER)
            return
        try:
            frame = grab(region)
        except Exception as exc:
            self.status.configure(text="抓屏失败：%s" % exc, text_color=theme.DANGER)
            return
        matcher = TemplateMatcher()
        hits = []
        for i, r in enumerate(g["rules"]):
            if not r.get("enabled", True) or not (r.get("templates") or r.get("template")):
                continue
            res = match_rule(matcher, frame, region, r)
            if res.score >= r["threshold"]:
                hits.append((r, res))
        hits.sort(key=lambda x: (-x[0]["priority"], -x[1].score))
        limit = g.get("max_actions_per_tick", 1)
        if limit and limit > 0:
            hits = hits[:limit]
        if not hits:
            self.status.configure(text="当前帧未命中任何规则（阈值不足）", text_color=theme.WARN)
            return
        text = " → ".join("%s(P%d,%.2f)" % (r["name"], r["priority"], s.score) for r, s in hits)
        self.status.configure(text="本轮顺序：" + text, text_color=theme.OK)

    def _save(self):
        self.group = self._collect()
        if self.on_save:
            self.on_save(self.group)
        self.status.configure(text="规则集已保存：%d 条规则" % len(self.group["rules"]),
                              text_color=theme.OK)

    def _insert(self):
        self.group = self._collect()
        if self.on_insert:
            self.on_insert(self.group)
        self.status.configure(text="已插入当前脚本", text_color=theme.OK)

    def _center(self):
        center_window(self)



class IconPickerDialog(ctk.CTkToplevel):
    def __init__(self, master, on_pick=None):
        super().__init__(master)
        self.on_pick = on_pick
        self.title("从图标库选择")
        self.geometry("820x640")
        self.minsize(820, 600)
        self.resizable(False, True)
        self.configure(fg_color=theme.BG)
        self.transient(master)
        apply_window_icon(self)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._imgs = []
        self._build()
        self.after(100, self._center)

    def _build(self):
        label(self, "从图标库选择图片", 15, theme.TEXT).pack(anchor="w", padx=16, pady=(12, 2))
        label(self, "图标库路径：icons/", 9, theme.MUTED).pack(anchor="w", padx=16, pady=(0, 8))
        scroll = ctk.CTkScrollableFrame(self, fg_color=theme.BG2, corner_radius=8)
        scroll.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        files = []
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.bmp"):
            files.extend(ICONS_DIR.glob(ext))
        files = sorted(set(files), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            label(scroll, "图标库为空，请先在区域监控里抓取图标", 10, theme.MUTED).pack(
                anchor="w", padx=8, pady=8)
            return
        for i, path in enumerate(files):
            try:
                img = Image.open(path).convert("RGB")
                img.thumbnail((110, 80), Image.LANCZOS)
                ci = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
                self._imgs.append(ci)
            except Exception:
                ci = None
            b = ctk.CTkButton(scroll, text=path.stem[:16], image=ci, compound="top",
                              width=140, height=120, fg_color=theme.CARD2,
                              hover_color=theme.LINE, font=(theme.FONT, theme.fs(9)),
                              command=lambda p=path: self._pick(p))
            b.grid(row=i // 5, column=i % 5, padx=6, pady=6, sticky="nsew")
        for c in range(5):
            scroll.grid_columnconfigure(c, weight=1)
        ctk.CTkButton(self, text="取消", width=90, height=34, fg_color=theme.CARD2,
                      hover_color=theme.LINE, command=self.destroy).pack(
            side="right", padx=16, pady=(0, 12))

    def _pick(self, path):
        self.destroy()
        if self.on_pick:
            self.on_pick(path)

    def _center(self):
        center_window(self)
