"""后台规则编辑弹窗 + 脚本动作列表。"""
from __future__ import annotations

import datetime
import tkinter as tk
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from config import ICONS_DIR
from core.script import list_scripts
from core.screen import grab, load_image, to_ctk_image
from . import theme
from .icon_crop_dialog import IconCropDialog
from .region_picker import RegionPicker
from .widgets import label
from .window_icon import apply_window_icon

OPS = {"运行": "run", "暂停": "pause", "继续": "resume", "停止": "stop"}
OPS_REV = {v: k for k, v in OPS.items()}
TARGETS = {"当前脚本": "current", "指定脚本": "script"}
TARGETS_REV = {v: k for k, v in TARGETS.items()}
BUSY = {"切换": "switch", "忽略": "ignore"}
BUSY_REV = {v: k for k, v in BUSY.items()}


class ScriptActionList(ctk.CTkFrame):
    """一行一个脚本动作：操作 / 目标 / 脚本 / 忙碌策略。"""

    def __init__(self, master, actions=None, inline: bool = False, height=120):
        super().__init__(master, fg_color="transparent")
        self.inline = inline
        self.rows: list[dict] = []
        self._script_names = [p.name for p in list_scripts()]
        self.box = ctk.CTkScrollableFrame(self, fg_color=theme.BG, corner_radius=8,
                                          height=height)
        self.box.pack(fill="x", expand=True)
        for act in actions or []:
            if isinstance(act, dict):
                self._add_row(act)
        self.add_btn = ctk.CTkButton(self, text="+ 添加动作", width=96, height=26,
                                     fg_color=theme.CARD2, hover_color=theme.LINE,
                                     font=(theme.FONT, theme.fs(9)),
                                     command=lambda: self._add_row({}))
        self.add_btn.pack(anchor="w", pady=(4, 0))

    def _add_row(self, act: dict):
        frame = ctk.CTkFrame(self.box, fg_color="transparent")
        frame.pack(fill="x", pady=2)
        row = {"frame": frame}
        op = str(act.get("op", "run"))
        if self.inline and op in ("pause", "resume"):
            op = "run"
        ops = ["运行", "停止"] if self.inline else ["运行", "暂停", "继续", "停止"]
        row["op"] = tk.StringVar(value=OPS_REV.get(op, "运行"))
        ctk.CTkOptionMenu(frame, variable=row["op"], values=ops, width=76, height=26,
                          fg_color=theme.CARD2, button_color=theme.LINE,
                          font=(theme.FONT, theme.fs(9))).pack(side="left")

        row["target"] = tk.StringVar(value=TARGETS_REV.get(str(act.get("target", "current")), "当前脚本"))
        target_menu = ctk.CTkOptionMenu(frame, variable=row["target"],
                                        values=["当前脚本", "指定脚本"], width=94, height=26,
                                        fg_color=theme.CARD2, button_color=theme.LINE,
                                        font=(theme.FONT, theme.fs(9)))
        target_menu.pack(side="left", padx=4)

        script = str(act.get("script", "") or "")
        name = Path(script).name if script else "（选择脚本）"
        names = ["（选择脚本）"] + self._script_names
        if name and name not in names:
            names.append(name)
        row["script"] = tk.StringVar(value=name if name else "（选择脚本）")
        script_menu = ctk.CTkOptionMenu(frame, variable=row["script"], values=names,
                                        width=190, height=26, fg_color=theme.CARD2,
                                        button_color=theme.LINE, font=(theme.FONT, theme.fs(9)))
        script_menu.pack(side="left", padx=4)

        row["busy"] = tk.StringVar(value=BUSY_REV.get(str(act.get("busy", "switch")), "切换"))
        busy_menu = ctk.CTkOptionMenu(frame, variable=row["busy"], values=["切换", "忽略"],
                                      width=66, height=26, fg_color=theme.CARD2,
                                      button_color=theme.LINE, font=(theme.FONT, theme.fs(9)))
        busy_menu.pack(side="left", padx=4)

        def sync(*_a):
            opv = OPS.get(row["op"].get(), "run")
            is_run = opv == "run"
            target_menu.configure(state="normal" if is_run and not self.inline else "disabled")
            script_menu.configure(state="normal" if is_run else "disabled")
            busy_menu.configure(state="normal" if is_run else "disabled")
            if self.inline and is_run:
                row["target"].set("指定脚本")
        row["op"].trace_add("write", sync)
        sync()

        def remove():
            self.rows = [r for r in self.rows if r is not row]
            frame.destroy()
        ctk.CTkButton(frame, text="×", width=26, height=26, fg_color=theme.CARD2,
                      hover_color=theme.DANGER, font=(theme.FONT, theme.fs(11)),
                      command=remove).pack(side="left", padx=(4, 0))
        self.rows.append(row)

    def get(self) -> list[dict]:
        out = []
        for row in self.rows:
            op = OPS.get(row["op"].get(), "run")
            target = TARGETS.get(row["target"].get(), "current")
            if self.inline and op == "run":
                target = "script"
            script = row["script"].get()
            if op == "run":
                if not script or script == "（选择脚本）":
                    continue
                script = "scripts/" + script
            else:
                script = ""
                target = "current"
            out.append({"type": "script", "op": op, "target": target,
                        "script": script,
                        "busy": BUSY.get(row["busy"].get(), "switch")})
        return out


class RuleDialog(ctk.CTkToplevel):
    def __init__(self, master, rule: dict | None = None, on_save=None):
        super().__init__(master)
        self.master_app = master
        self.rule = dict(rule or {})
        self.on_save = on_save
        self.result = None
        self._thumb = None
        self._icon_map = {}
        self.title("编辑后台规则" if rule else "新建后台规则")
        self.geometry("820x760")
        self.minsize(760, 640)
        self.configure(fg_color=theme.BG)
        self.transient(master)
        apply_window_icon(self)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self.body = ctk.CTkScrollableFrame(self, fg_color=theme.BG2, corner_radius=10)
        self.body.pack(fill="both", expand=True, padx=16, pady=12)

        r = self.rule
        self.name_var = tk.StringVar(value=str(r.get("name", "规则%d" % (len(getattr(master, "cfg", {}).data.get("background_rules", [])) + 1))))
        self.enabled_var = tk.BooleanVar(value=bool(r.get("enabled", True)))
        self.prio_var = tk.StringVar(value=str(r.get("priority", 50)))
        self.threshold_var = tk.StringVar(value="%g" % float(r.get("threshold", 0.85)))
        self.stable_var = tk.StringVar(value=str(r.get("stable_frames", 2)))
        self.cooldown_var = tk.StringVar(value=str(r.get("cooldown_ms", 500)))
        self.poll_var = tk.StringVar(value=str(r.get("poll_ms", 150)))

        row = self._row()
        label(row, "规则名称", 10, theme.MUTED, width=76).pack(side="left")
        ctk.CTkEntry(row, textvariable=self.name_var, width=220, height=28,
                     fg_color=theme.CARD, border_color=theme.LINE,
                     font=(theme.FONT, theme.fs(10))).pack(side="left")
        ctk.CTkSwitch(row, text="启用", variable=self.enabled_var,
                      font=(theme.FONT, theme.fs(10)), progress_color=theme.ACCENT).pack(side="left", padx=12)
        label(row, "优先级", 10, theme.MUTED).pack(side="left")
        ctk.CTkEntry(row, textvariable=self.prio_var, width=56, height=28,
                     fg_color=theme.CARD, border_color=theme.LINE,
                     font=(theme.FONT, theme.fs(10))).pack(side="left", padx=4)

        region = list(r.get("region") or [0, 0, 0, 0])
        row = self._row()
        label(row, "监控区域", 10, theme.MUTED, width=76).pack(side="left")
        self.region_vars = {}
        for key, text, val in zip(("rx", "ry", "rw", "rh"), ("X", "Y", "宽", "高"), region):
            label(row, text, 9, theme.MUTED).pack(side="left", padx=(3, 1))
            self.region_vars[key] = tk.StringVar(value=str(int(val)))
            ctk.CTkEntry(row, textvariable=self.region_vars[key], width=58, height=28,
                         fg_color=theme.CARD, border_color=theme.LINE,
                         font=(theme.MONO, theme.fs(9))).pack(side="left")
        ctk.CTkButton(row, text="框选区域", width=84, height=28, fg_color=theme.ACCENT,
                      text_color="#04121f", font=(theme.FONT, theme.fs(9), "bold"),
                      command=self._pick_region).pack(side="left", padx=6)
        ctk.CTkButton(row, text="刷新预览", width=78, height=28, fg_color=theme.CARD2,
                      font=(theme.FONT, theme.fs(9)),
                      command=self._refresh_thumb).pack(side="left")

        row = self._row()
        label(row, "图标模板", 10, theme.MUTED, width=76).pack(side="left")
        names = []
        self._icon_map = {}
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.bmp"):
            for p in ICONS_DIR.glob(ext):
                names.append(p.name)
                self._icon_map[p.name] = "icons/" + p.name
        names.sort()
        cur = str(r.get("template", "") or "")
        cur_name = Path(cur).name if cur else "（未选择）"
        self.template_var = tk.StringVar(value=cur_name if cur_name in names else "（未选择）")
        self.template_menu = ctk.CTkOptionMenu(row, variable=self.template_var,
                                               values=["（未选择）"] + names,
                                               width=220, height=28, fg_color=theme.CARD2,
                                               button_color=theme.LINE,
                                               font=(theme.FONT, theme.fs(9)))
        self.template_menu.pack(side="left")
        ctk.CTkButton(row, text="从画面圈选", width=96, height=28, fg_color=theme.OK,
                      text_color="#04121f", font=(theme.FONT, theme.fs(9), "bold"),
                      command=self._capture_template).pack(side="left", padx=6)

        row = self._row()
        label(row, "识别参数", 10, theme.MUTED, width=76).pack(side="left")
        for text, var in (("阈值", self.threshold_var), ("稳定帧", self.stable_var),
                          ("冷却ms", self.cooldown_var), ("轮询ms", self.poll_var)):
            label(row, text, 9, theme.MUTED).pack(side="left", padx=(6, 2))
            ctk.CTkEntry(row, textvariable=var, width=64, height=28,
                         fg_color=theme.CARD, border_color=theme.LINE,
                         font=(theme.MONO, theme.fs(9))).pack(side="left")

        self.thumb = ctk.CTkLabel(self.body, text="区域预览", width=360, height=120,
                                  fg_color=theme.CARD, corner_radius=8,
                                  font=(theme.FONT, theme.fs(9)), text_color=theme.MUTED)
        self.thumb.pack(fill="x", padx=4, pady=(6, 4))
        self.after(200, self._refresh_thumb)

        label(self.body, "命中时动作（图案出现）", 10, theme.TEXT).pack(anchor="w", padx=4, pady=(8, 2))
        self.hit_actions = ScriptActionList(self.body, r.get("on_hit"), height=110)
        self.hit_actions.pack(fill="x", padx=4)

        label(self.body, "消失时动作（图案消失）", 10, theme.TEXT).pack(anchor="w", padx=4, pady=(8, 2))
        self.miss_actions = ScriptActionList(self.body, r.get("on_miss"), height=90)
        self.miss_actions.pack(fill="x", padx=4)

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=10)
        ctk.CTkButton(bottom, text="保存", width=110, height=34, fg_color=theme.ACCENT,
                      text_color="#04121f", font=(theme.FONT, theme.fs(11), "bold"),
                      command=self._save).pack(side="right")
        ctk.CTkButton(bottom, text="取消", width=90, height=34, fg_color=theme.CARD2,
                      font=(theme.FONT, theme.fs(10)),
                      command=self._cancel).pack(side="right", padx=8)

    def _row(self):
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x", pady=4)
        return row

    def _region(self):
        try:
            x = int(float(self.region_vars["rx"].get() or 0))
            y = int(float(self.region_vars["ry"].get() or 0))
            w = int(float(self.region_vars["rw"].get() or 0))
            h = int(float(self.region_vars["rh"].get() or 0))
        except Exception:
            return None
        if w <= 0 or h <= 0:
            return None
        return [x, y, w, h]

    def _pick_region(self):
        initial = self._region()
        try:
            self.withdraw()
        except Exception:
            pass

        def restore():
            try:
                self.deiconify(); self.lift(); self.focus_force()
            except Exception:
                pass

        def done(rect):
            restore()
            for key, val in zip(("rx", "ry", "rw", "rh"), rect):
                self.region_vars[key].set(str(int(val)))
            self._refresh_thumb()

        self.after(160, lambda: RegionPicker(self, rect=initial, on_confirm=done,
                                             on_cancel=restore))

    def _refresh_thumb(self):
        region = self._region()
        if not region:
            self.thumb.configure(text="未设置监控区域", image=None)
            return
        try:
            frame = grab(tuple(region))
            pil = to_ctk_image(frame)
            pil.thumbnail((360, 120), Image.LANCZOS)
            img = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
            self._thumb = img
            self.thumb.configure(image=img, text="")
        except Exception as exc:
            self.thumb.configure(text="预览失败：%s" % exc, image=None)

    def _capture_template(self):
        region = self._region()
        if not region:
            self._toast("请先框选监控区域")
            return
        try:
            frame = grab(tuple(region))
        except Exception as exc:
            self._toast("抓取失败：%s" % exc)
            return
        name = "icon_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        def on_save(path, crop_rect=None):
            self.template_var.set(Path(path).name)
            self._icon_map[Path(path).name] = "icons/" + Path(path).name
            names = ["（未选择）"] + sorted(self._icon_map)
            self.template_menu.configure(values=names)

        IconCropDialog(self, frame, region, name, default_crop=None, on_save=on_save)

    def _toast(self, text):
        try:
            self.master_app._toast(text, "warn")
        except Exception:
            pass

    def _current_template(self):
        name = self.template_var.get()
        if not name or name == "（未选择）":
            return ""
        return self._icon_map.get(name, "icons/" + name)

    def _save(self):
        region = self._region()
        if not region:
            self._toast("请先框选监控区域")
            return
        tpl = self._current_template()
        if not tpl:
            self._toast("请选择或抓取一个图标模板")
            return
        try:
            rule = {
                "id": str(self.rule.get("id") or ("rule_%d" % int(datetime.datetime.now().timestamp()))),
                "name": self.name_var.get().strip() or "规则",
                "enabled": bool(self.enabled_var.get()),
                "priority": max(0, min(999, int(float(self.prio_var.get() or 50)))),
                "template": tpl,
                "threshold": max(0.05, min(0.999, float(self.threshold_var.get() or 0.85))),
                "region": region,
                "capture_mode": str(self.rule.get("capture_mode", "screen")),
                "window_title": str(self.rule.get("window_title", "")),
                "window_class": str(self.rule.get("window_class", "")),
                "window_process": str(self.rule.get("window_process", "")),
                "window_hwnd": int(self.rule.get("window_hwnd", 0) or 0),
                "poll_ms": max(20, int(float(self.poll_var.get() or 150))),
                "stable_frames": max(1, int(float(self.stable_var.get() or 2))),
                "cooldown_ms": max(0, int(float(self.cooldown_var.get() or 500))),
                "multiscale": True,
                "on_hit": self.hit_actions.get(),
                "on_miss": self.miss_actions.get(),
            }
        except Exception as exc:
            self._toast("参数无效：%s" % exc)
            return
        self.result = rule
        if self.on_save:
            self.on_save(rule)
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()
