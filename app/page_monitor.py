"""区域监控页。"""
from __future__ import annotations

import copy
import datetime
import cv2
import shutil
import json
import threading
import time
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog

import customtkinter as ctk
from PIL import Image

from config import ICONS_DIR, ROOT
from . import theme
from core.capture import (CaptureError, client_rect_screen, find_window, make_source,
                          window_is_fullscreen)
from core.log import log
from core.monitor import LiveMonitor, MonitorProfile, ProbeResult, annotate, crop_match, probe
from core.monitor_rules import group_to_event, match_rule, normalize_monitor_group
from core.screen import grab, load_image, to_ctk_image
from core.script import list_scripts, new_script_path
from .region_picker import RegionPicker
from .icon_crop_dialog import IconCropDialog
from .rules_window import RulesWindow
from .point_picker import PointPicker
from .key_record_dialog import KeyRecordDialog
from .widgets import Card, ImagePanel, autohide_scrollbar, entry, label
from .window_picker import WindowPicker


BTN_MAP = {"左键": "left", "右键": "right", "滚轮": "middle", "侧键X1": "x1", "侧键X2": "x2"}
STYLE_MAP = {"单击": "single", "双击": "double", "长按": "long",
             "上滚动": "wheel_up", "下滚动": "wheel_down"}
MODE_MAP = {"图标中心": "center", "固定位置": "fixed", "偏移跟随": "follow"}
CLICK_STYLES = ["单击", "双击", "长按"]
WHEEL_STYLES = ["上滚动", "下滚动"]
SUP_APPEAR = {"无": "none", "暂停主脚本": "pause", "停止主脚本": "stop", "运行脚本": "run"}
SUP_APPEAR_REV = {v: k for k, v in SUP_APPEAR.items()}
SUP_DISAPPEAR = {"无": "none", "继续主脚本": "resume", "停止主脚本": "stop", "运行脚本": "run"}
SUP_DISAPPEAR_REV = {v: k for k, v in SUP_DISAPPEAR.items()}



class MonitorPageMixin:
    # ------------------------------------------------------------ 方案库
    def _init_scheme_library(self):
        schemes = self.cfg.get("monitor_schemes") or []
        if not isinstance(schemes, list) or not schemes:
            schemes = [{
                "id": "scheme_default",
                "name": "默认方案",
                "profile": self.monitor_profile.to_dict(),
                "group": normalize_monitor_group(self.cfg.get("multi_monitor", {})),
            }]
        self.schemes = schemes
        active = str(self.cfg.get("active_scheme", "") or "")
        if not any(s.get("id") == active for s in schemes):
            active = schemes[0]["id"]
        self.active_scheme_id = active
        s = self._scheme_by_id(active) or schemes[0]
        self.monitor_profile = MonitorProfile.from_dict(s.get("profile", {}))
        self.multi_group = normalize_monitor_group(s.get("group", {}))
        g = self.multi_group
        region = list(g.get("region") or [0, 0, 0, 0])
        if len(region) == 4 and region[2] > 0 and region[3] > 0:
            self.monitor_profile.region = region
        if g.get("capture_mode"):
            self.monitor_profile.capture_mode = g.get("capture_mode")
        for key in ("window_title", "window_class", "window_process"):
            if g.get(key):
                setattr(self.monitor_profile, key, g.get(key))
        if g.get("window_hwnd"):
            self.monitor_profile.window_hwnd = int(g.get("window_hwnd") or 0)
        if g.get("poll_ms"):
            self.monitor_profile.poll_ms = int(g.get("poll_ms") or 150)
        self._save_scheme_library(save=False)

    def _scheme_by_id(self, sid):
        for s in getattr(self, "schemes", []):
            if s.get("id") == sid:
                return s
        return None

    def _active_scheme(self):
        return self._scheme_by_id(getattr(self, "active_scheme_id", ""))

    def _update_active_scheme_memory(self):
        s = self._active_scheme()
        if not s:
            return
        s["profile"] = self.monitor_profile.to_dict()
        s["group"] = copy.deepcopy(self.multi_group)

    def _save_scheme_library(self, save=True):
        if not getattr(self, "schemes", None):
            return
        self.cfg.set("monitor_schemes", self.schemes)
        self.cfg.set("active_scheme", self.active_scheme_id)
        self.cfg.set("monitor", self.monitor_profile.to_dict())
        self.cfg.set("multi_monitor", self.multi_group)
        if save:
            self.cfg.save()

    def _refresh_scheme_menu(self):
        names = [s.get("name", "未命名") for s in getattr(self, "schemes", [])]
        try:
            self.scheme_menu.configure(values=names)
            s = self._active_scheme()
            self.scheme_var.set(s.get("name", "未命名") if s else (names[0] if names else ""))
        except Exception:
            pass

    def _switch_scheme(self, name):
        s = next((x for x in self.schemes if x.get("name") == name), None)
        if not s or s.get("id") == self.active_scheme_id:
            return
        self._update_active_scheme_memory()
        self.active_scheme_id = s["id"]
        self.monitor_profile = MonitorProfile.from_dict(s.get("profile", {}))
        self.multi_group = normalize_monitor_group(s.get("group", {}))
        self.region_shot = None
        self._apply_scheme_to_ui()
        self._save_scheme_library()
        self._toast("已切换监控方案：%s" % s.get("name"), "ok")

    def _apply_scheme_to_ui(self):
        p = self.monitor_profile
        mode_rev = {"screen": "屏幕", "window": "窗口", "window_gdi": "窗口(GDI)"}
        self.capture_var.set(mode_rev.get(p.capture_mode, "屏幕"))
        try:
            self.scene_poll_var.set(str(p.poll_ms))
        except Exception:
            pass
        self.region_shot = None
        self._refresh_window_label()
        self._refresh_region_label()
        self._refresh_rules_list()
        self._refresh_multi_label()
        try:
            self.after(80, self._rules_autohide)
        except Exception:
            pass

    def _ask_scheme_name(self, title, initial=""):
        return simpledialog.askstring(title, "方案名称：", initialvalue=initial, parent=self)

    def _new_scheme(self):
        name = self._ask_scheme_name("新建方案", "新方案")
        if not name:
            return
        self._update_active_scheme_memory()
        scheme = {"id": "scheme_" + uuid.uuid4().hex[:8], "name": name.strip(),
                  "profile": MonitorProfile().to_dict(),
                  "group": normalize_monitor_group({})}
        self.schemes.append(scheme)
        self.active_scheme_id = scheme["id"]
        self.monitor_profile = MonitorProfile.from_dict(scheme["profile"])
        self.multi_group = normalize_monitor_group(scheme["group"])
        self._apply_scheme_to_ui()
        self._refresh_scheme_menu()
        self._save_scheme_library()
        self._toast("已新建方案：%s" % name, "ok")

    def _copy_scheme(self):
        s = self._active_scheme()
        if not s:
            return
        self._update_active_scheme_memory()
        name = self._ask_scheme_name("复制方案", s.get("name", "方案") + " 副本")
        if not name:
            return
        scheme = {"id": "scheme_" + uuid.uuid4().hex[:8], "name": name.strip(),
                  "profile": copy.deepcopy(s.get("profile", {})),
                  "group": copy.deepcopy(s.get("group", {}))}
        self.schemes.append(scheme)
        self.active_scheme_id = scheme["id"]
        self.monitor_profile = MonitorProfile.from_dict(scheme["profile"])
        self.multi_group = normalize_monitor_group(scheme["group"])
        self._apply_scheme_to_ui()
        self._refresh_scheme_menu()
        self._save_scheme_library()
        self._toast("已复制方案：%s" % name, "ok")

    def _rename_scheme(self):
        s = self._active_scheme()
        if not s:
            return
        name = self._ask_scheme_name("重命名方案", s.get("name", ""))
        if not name:
            return
        s["name"] = name.strip()
        self._refresh_scheme_menu()
        self._save_scheme_library()
        self._toast("方案已重命名", "ok")

    def _delete_scheme(self):
        if len(self.schemes) <= 1:
            self._toast("至少保留一个监控方案", "warn")
            return
        s = self._active_scheme()
        if not s:
            return
        if not messagebox.askyesno("删除方案", "确定删除「%s」？" % s.get("name")):
            return
        self.schemes = [x for x in self.schemes if x.get("id") != s.get("id")]
        self.active_scheme_id = self.schemes[0]["id"]
        t = self._active_scheme()
        self.monitor_profile = MonitorProfile.from_dict(t.get("profile", {}))
        self.multi_group = normalize_monitor_group(t.get("group", {}))
        self._apply_scheme_to_ui()
        self._refresh_scheme_menu()
        self._save_scheme_library()
        self._toast("方案已删除", "ok")

    def _export_scheme(self):
        s = self._active_scheme()
        if not s:
            return
        self._update_active_scheme_memory()
        path = filedialog.asksaveasfilename(title="导出监控方案", defaultextension=".json",
                                            filetypes=[("JSON", "*.json")],
                                            initialfile="%s.json" % s.get("name", "scheme"))
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
            self._toast("方案已导出：%s" % Path(path).name, "ok")
        except Exception as exc:
            self._toast("导出失败：%s" % exc, "error")

    def _import_scheme(self):
        path = filedialog.askopenfilename(title="导入监控方案",
                                          filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            self._toast("导入失败：%s" % exc, "error")
            return
        if not isinstance(data, dict) or not isinstance(data.get("profile", {}), dict):
            self._toast("文件格式不正确", "warn")
            return
        name = data.get("name") or Path(path).stem
        scheme = {"id": "scheme_" + uuid.uuid4().hex[:8], "name": name,
                  "profile": data.get("profile", {}),
                  "group": data.get("group", {})}
        self._update_active_scheme_memory()
        self.schemes.append(scheme)
        self.active_scheme_id = scheme["id"]
        self.monitor_profile = MonitorProfile.from_dict(scheme["profile"])
        self.multi_group = normalize_monitor_group(scheme["group"])
        self._apply_scheme_to_ui()
        self._refresh_scheme_menu()
        self._save_scheme_library()
        self._toast("已导入方案：%s" % name, "ok")

    def _build_monitor_page(self):
        outer, page = self._new_page()
        self.pages["monitor"] = outer
        page.grid_columnconfigure(0, weight=3, uniform="mon")
        page.grid_columnconfigure(1, weight=2, uniform="mon")
        page.grid_rowconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        self._init_scheme_library()
        self.region_shot = None

        region = Card(page, "监控场景", "抓屏源 + 场景区域")
        region.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        srow = ctk.CTkFrame(region.body, fg_color="transparent")
        srow.pack(fill="x")
        label(srow, "方案", 10).pack(side="left")
        self.scheme_var = tk.StringVar(value="")
        self.scheme_menu = ctk.CTkOptionMenu(
            srow, variable=self.scheme_var, values=["默认方案"], width=170, height=28,
            fg_color=theme.CARD2, button_color=theme.LINE, font=(theme.FONT, theme.fs(10)),
            command=self._switch_scheme)
        self.scheme_menu.pack(side="left", padx=4)
        self._btn(srow, "新建", self._new_scheme, width=48, height=28).pack(side="left")
        self._btn(srow, "复制", self._copy_scheme, width=48, height=28).pack(side="left", padx=3)
        self._btn(srow, "改名", self._rename_scheme, width=48, height=28).pack(side="left")
        self._btn(srow, "删除", self._delete_scheme, width=48, height=28).pack(side="left", padx=3)
        self._btn(srow, "导入", self._import_scheme, width=48, height=28,
                  color=theme.PURPLE, text_color="white").pack(side="left")
        self._btn(srow, "导出", self._export_scheme, width=48, height=28).pack(side="left", padx=3)
        self._refresh_scheme_menu()

        crow = ctk.CTkFrame(region.body, fg_color="transparent")
        crow.pack(fill="x", pady=(6, 0))
        label(crow, "抓屏方式", 10).pack(side="left")
        self.capture_var = tk.StringVar(value="屏幕")
        ctk.CTkOptionMenu(crow, variable=self.capture_var,
                          values=["屏幕", "窗口", "窗口(GDI)"], width=110, height=28,
                          fg_color=theme.CARD2, button_color=theme.LINE,
                          font=(theme.FONT, theme.fs(10)),
                          command=lambda _v: self._on_capture_mode_change()).pack(side="left", padx=4)
        self.window_btn = self._btn(crow, "选择窗口", self._choose_window, width=84, height=28)
        self.window_btn.pack(side="left", padx=2)
        self.window_label = label(crow, "使用整个屏幕", 9, theme.MUTED)
        self.window_label.pack(side="left", padx=6)

        rrow = ctk.CTkFrame(region.body, fg_color="transparent")
        rrow.pack(fill="x", pady=(6, 0))
        self.region_label = label(rrow, "区域：未选择", 10, theme.TEXT)
        self.region_label.pack(side="left")
        self._btn(rrow, "框选区域", lambda: self._pick_region(False), width=80, height=28,
                  color=theme.ACCENT, text_color="#04121f").pack(side="left", padx=6)
        self._btn(rrow, "刷新预览", self._grab_preview, width=76, height=28).pack(side="left")
        self._btn(rrow, "实时预览", self._toggle_scene_preview, width=76, height=28,
                  color=theme.PURPLE, text_color="white").pack(side="left", padx=6)
        label(rrow, "轮询", 9).pack(side="left", padx=(10, 2))
        self.scene_poll_var = tk.StringVar(value=str(self.monitor_profile.poll_ms))
        pe = entry(rrow, width=52, textvariable=self.scene_poll_var)
        pe.pack(side="left")
        pe.bind("<FocusOut>", lambda _e: self._sync_profile())
        label(rrow, "ms", 9).pack(side="left", padx=2)

        self.preview_panel = ImagePanel(region.body, size=(390, 190), text="框选区域后点击刷新预览")
        self.preview_panel.pack(fill="x", pady=(8, 0))
        self.preview_stop = None
        self.preview_thread = None
        self.preview_latest = None

        icons = Card(page, "图标库", "点击查看，规则里选择模板")
        icons.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(6, 0))
        ih = ctk.CTkFrame(icons.body, fg_color="transparent")
        ih.pack(fill="x")
        self.icon_selected_label = label(ih, "当前图标：未选择", 10, theme.TEXT)
        self.icon_selected_label.pack(side="left")
        self._btn(ih, "删除", self._delete_icon, width=58, color=theme.DANGER,
                  text_color="white").pack(side="left", padx=6)
        self._btn(ih, "刷新", self._refresh_icons, width=58).pack(side="right")
        self._btn(ih, "目录", self._open_icons_dir, width=58).pack(side="right", padx=6)
        self._btn(ih, "添加", self._add_icons, width=58, color=theme.OK,
                  text_color="#04121f").pack(side="right")
        self.icons_scroll = ctk.CTkScrollableFrame(icons.body, fg_color=theme.BG2,
                                                    corner_radius=8)
        self.icons_scroll.pack(fill="both", expand=True, pady=(8, 0))
        self._icons_autohide = autohide_scrollbar(self.icons_scroll)

        rules = Card(page, "监控规则", "常驻 = 脚本外运行；也可插入脚本节点")
        rules.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(6, 0))
        top = ctk.CTkFrame(rules.body, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkButton(top, text="+ 新建规则", width=92, height=28, fg_color=theme.ACCENT,
                      text_color="#04121f", font=(theme.FONT, theme.fs(10), "bold"),
                      command=self._new_rule).pack(side="left")
        ctk.CTkButton(top, text="管理组", width=64, height=28, fg_color=theme.PURPLE,
                      text_color="white", font=(theme.FONT, theme.fs(9)),
                      command=self._open_rules_window).pack(side="left", padx=6)
        ctk.CTkButton(top, text="测试全部", width=68, height=28, fg_color=theme.CARD2,
                      hover_color=theme.LINE, font=(theme.FONT, theme.fs(9)),
                      command=self._test_all_rules).pack(side="left")
        ctk.CTkButton(top, text="插入整组", width=68, height=28, fg_color=theme.CARD2,
                      hover_color=theme.LINE, font=(theme.FONT, theme.fs(9)),
                      command=self._insert_multi_group).pack(side="left", padx=6)
        self.rules_status = label(rules.body, "状态：未启用", 9, theme.MUTED)
        self.rules_status.pack(anchor="w", pady=(6, 2))
        self.multi_label = label(rules.body, "", 9, theme.PURPLE)
        self.multi_label.pack(anchor="w", pady=(0, 4))
        tools = ctk.CTkFrame(rules.body, fg_color="transparent")
        tools.pack(fill="x", pady=(0, 4))
        ctk.CTkButton(tools, text="导入规则", width=72, height=24, fg_color=theme.CARD2,
                      hover_color=theme.LINE, font=(theme.FONT, theme.fs(9)),
                      command=self._import_rules).pack(side="left")
        ctk.CTkButton(tools, text="导出规则", width=72, height=24, fg_color=theme.CARD2,
                      hover_color=theme.LINE, font=(theme.FONT, theme.fs(9)),
                      command=self._export_rules).pack(side="left", padx=6)
        tools2 = ctk.CTkFrame(rules.body, fg_color="transparent")
        tools2.pack(fill="x", pady=(0, 4))
        self.rule_filter_var = tk.StringVar(value="")
        fe = ctk.CTkEntry(tools2, textvariable=self.rule_filter_var, width=120, height=24,
                          placeholder_text="搜索规则", fg_color=theme.CARD,
                          border_color=theme.LINE, font=(theme.FONT, theme.fs(9)))
        fe.pack(side="left")
        self.rule_filter_var.trace_add("write", lambda *_a: self._refresh_rules_list())
        ctk.CTkButton(tools2, text="全部常驻", width=72, height=24, fg_color=theme.CARD2,
                      hover_color=theme.LINE, font=(theme.FONT, theme.fs(9)),
                      command=lambda: self._set_all_resident(True)).pack(side="left", padx=6)
        ctk.CTkButton(tools2, text="全部取消", width=72, height=24, fg_color=theme.CARD2,
                      hover_color=theme.LINE, font=(theme.FONT, theme.fs(9)),
                      command=lambda: self._set_all_resident(False)).pack(side="left")
        self.rules_scroll = ctk.CTkScrollableFrame(rules.body, fg_color=theme.BG2,
                                                    corner_radius=8)
        self.rules_scroll.pack(fill="both", expand=True)
        self._rules_autohide = autohide_scrollbar(self.rules_scroll)
        self._rule_status_labels = {}
        self._refresh_rules_list()
        self.after(1000, self._rules_status_tick)

    # ------------------------------------------------------------ 规则列表
    def _rules(self) -> list[dict]:
        return list(self.multi_group.get("rules") or [])

    def _refresh_rules_list(self):
        if not hasattr(self, "rules_scroll"):
            return
        from core.actions import action_summary
        for child in list(self.rules_scroll.winfo_children()):
            child.destroy()
        self._rule_status_labels = {}
        rules = self._rules()
        flt = ""
        try:
            flt = self.rule_filter_var.get().strip().lower()
        except Exception:
            pass
        if flt:
            rules = [r for r in rules
                     if flt in str(r.get("name", "")).lower()
                     or flt in str(r.get("template", "")).lower()]
        if not rules:
            ctk.CTkLabel(self.rules_scroll,
                         text="还没有监控规则\n点「新建规则」或「管理组」添加",
                         font=(theme.FONT, theme.fs(10)), text_color=theme.MUTED,
                         justify="left").pack(anchor="w", padx=10, pady=10)
            self._refresh_multi_label()
            return
        for rule in rules:
            rid = str(rule.get("id", ""))
            row = ctk.CTkFrame(self.rules_scroll, fg_color=theme.CARD, corner_radius=8)
            row.pack(fill="x", padx=4, pady=3)
            row.bind("<Double-1>", lambda _e, r=rid: self._edit_rule(r))

            head = ctk.CTkFrame(row, fg_color="transparent")
            head.pack(fill="x", padx=8, pady=(6, 0))
            en_var = tk.BooleanVar(value=bool(rule.get("enabled", True)))
            ctk.CTkSwitch(head, text="", width=36, variable=en_var,
                          progress_color=theme.OK,
                          command=lambda r=rid, v=en_var: self._set_rule_flag(r, "enabled", bool(v.get()))
                          ).pack(side="left", padx=(0, 4))
            label(head, "%s  ·  优先级 %s" % (rule.get("name", "规则"), rule.get("priority", 50)),
                  10, theme.TEXT, anchor="w").pack(side="left")
            ctk.CTkButton(head, text="删除", width=44, height=24, fg_color=theme.DANGER,
                          text_color="white", font=(theme.FONT, theme.fs(9)),
                          command=lambda r=rid: self._delete_rule(r)).pack(side="right", padx=2)
            ctk.CTkButton(head, text="插入", width=44, height=24, fg_color=theme.CARD2,
                          hover_color=theme.LINE, font=(theme.FONT, theme.fs(9)),
                          command=lambda r=rid: self._insert_rule(r)).pack(side="right", padx=2)
            ctk.CTkButton(head, text="编辑", width=44, height=24, fg_color=theme.CARD2,
                          hover_color=theme.LINE, font=(theme.FONT, theme.fs(9)),
                          command=lambda r=rid: self._edit_rule(r)).pack(side="right", padx=2)

            acts = rule.get("actions") or []
            miss = rule.get("miss_actions") or []
            line = "命中：" + ("、".join(action_summary(a) for a in acts[:2]) or "无")
            if miss:
                line += "    消失：" + "、".join(action_summary(a) for a in miss[:1])
            body = ctk.CTkFrame(row, fg_color="transparent")
            body.pack(fill="x", padx=8, pady=(2, 0))
            label(body, line, 9, theme.MUTED, anchor="w", wraplength=300,
                  justify="left").pack(side="left", fill="x", expand=True)

            srow = ctk.CTkFrame(row, fg_color="transparent")
            srow.pack(fill="x", padx=8, pady=(2, 6))
            st = label(srow, "状态：等待检测", 9, theme.MUTED, anchor="w")
            st.pack(side="left")
            self._rule_status_labels[rid] = st
            resbox = ctk.CTkFrame(srow, fg_color="transparent")
            resbox.pack(side="right")
            label(resbox, "常驻", 9, theme.MUTED).pack(side="left", padx=(0, 2))
            res_var = tk.BooleanVar(value=bool(rule.get("resident", False)))
            ctk.CTkSwitch(resbox, text="", variable=res_var, width=40,
                          progress_color=theme.ACCENT,
                          command=lambda r=rid, v=res_var: self._set_rule_flag(r, "resident", bool(v.get()))
                          ).pack(side="left")
        self._refresh_multi_label()
        try:
            self.after(80, self._rules_autohide)
        except Exception:
            pass

    def _refresh_multi_label(self):
        try:
            rules = self._rules()
            resident = sum(1 for r in rules if r.get("resident"))
            links = len(self.multi_group.get("links") or [])
            self.multi_label.configure(
                text="规则 %d 条 · 常驻 %d 条 · 关联组 %d 个（点「管理组」）"
                     % (len(rules), resident, links))
        except Exception:
            pass

    def _ensure_region_shot(self) -> bool:
        region = self.monitor_profile.region
        if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
            self._toast("请先框选监控场景区域", "warn")
            return False
        self._sync_profile()
        if self.region_shot is None:
            try:
                self.region_shot = grab(region)
            except Exception as exc:
                self._toast("区域截图失败：%s" % exc, "error")
                return False
        return True

    def _new_rule(self):
        if not self._ensure_region_shot():
            return
        from .rules_window import RuleDialog
        RuleDialog(self, None, links=self.multi_group.get("links") or [],
                   region_shot=self.region_shot, region=self.monitor_profile.region,
                   default_name="规则%d" % (len(self._rules()) + 1),
                   on_save=self._append_rule)

    def _append_rule(self, rule):
        g = normalize_monitor_group(self.multi_group)
        g["rules"] = list(g.get("rules") or []) + [rule]
        self._save_multi_group(g)
        self._toast("已新增规则：%s" % rule.get("name"), "ok")

    def _edit_rule(self, rid: str):
        rule = next((r for r in self._rules() if str(r.get("id")) == rid), None)
        if rule is None:
            return
        if not self._ensure_region_shot():
            return
        from .rules_window import RuleDialog
        def save(new):
            g = normalize_monitor_group(self.multi_group)
            g["rules"] = [new if str(r.get("id")) == rid else r for r in g["rules"]]
            self._save_multi_group(g)
            self._toast("规则已保存：%s" % new.get("name"), "ok")
        RuleDialog(self, rule, links=self.multi_group.get("links") or [],
                   region_shot=self.region_shot, region=self.monitor_profile.region,
                   on_save=save)

    def _delete_rule(self, rid: str):
        rule = next((r for r in self._rules() if str(r.get("id")) == rid), None)
        if not rule:
            return
        if not messagebox.askyesno("删除规则", "确定删除「%s」？" % rule.get("name", "规则")):
            return
        g = normalize_monitor_group(self.multi_group)
        g["rules"] = [r for r in g["rules"] if str(r.get("id")) != rid]
        for link in g.get("links", []):
            link["members"] = [m for m in link.get("members", []) if m != rid]
        self._save_multi_group(g)

    def _set_all_resident(self, value: bool):
        g = normalize_monitor_group(self.multi_group)
        for r in g.get("rules", []):
            r["resident"] = bool(value)
        self._save_multi_group(g, silent=True)
        self._toast("已%s全部规则常驻" % ("开启" if value else "取消"), "ok")

    def _set_rule_flag(self, rid: str, key: str, value: bool):
        g = normalize_monitor_group(self.multi_group)
        for r in g.get("rules", []):
            if str(r.get("id")) == rid:
                r[key] = bool(value)
                break
        self._save_multi_group(g, silent=True)

    def _insert_rule(self, rid: str):
        rule = next((r for r in self._rules() if str(r.get("id")) == rid), None)
        if rule is None:
            return
        g = normalize_monitor_group(self.multi_group)
        g["name"] = rule.get("name", "监控规则")
        g["rules"] = [dict(rule)]
        g["links"] = []
        self._insert_multi_group(g)

    def _export_rules(self):
        path = filedialog.asksaveasfilename(
            title="导出监控规则", defaultextension=".json",
            initialfile="wizflow_rules.json",
            filetypes=[("JSON", "*.json")])
        if not path:
            return
        data = {
            "app": "WizFlow",
            "version": 2,
            "scene": {
                "region": list(self.monitor_profile.region),
                "capture_mode": self.monitor_profile.capture_mode,
                "poll_ms": int(self.monitor_profile.poll_ms),
            },
            "rules": self.multi_group.get("rules") or [],
            "links": self.multi_group.get("links") or [],
            "on_none_present": self.multi_group.get("on_none_present") or [],
        }
        try:
            Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
            self._toast("已导出 %d 条规则" % len(data["rules"]), "ok")
        except Exception as exc:
            self._toast("导出失败：%s" % exc, "error")

    def _import_rules(self):
        path = filedialog.askopenfilename(title="导入监控规则",
                                          filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            self._toast("读取失败：%s" % exc, "error")
            return
        rules = [r for r in (data.get("rules") or []) if isinstance(r, dict)]
        if not rules:
            self._toast("文件里没有规则", "warn")
            return
        replace = messagebox.askyesno(
            "导入监控规则",
            "检测到 %d 条规则。\n\n是 = 覆盖当前规则\n否 = 追加到当前规则" % len(rules))
        g = normalize_monitor_group(self.multi_group)
        if replace:
            g["rules"] = []
            g["links"] = []
            g["on_none_present"] = []
        id_map = {}
        existing = {str(r.get("id")) for r in g.get("rules", [])}
        for raw in rules:
            r = dict(raw)
            old = str(r.get("id", ""))
            rid = "rule_" + uuid.uuid4().hex[:8]
            while rid in existing:
                rid = "rule_" + uuid.uuid4().hex[:8]
            existing.add(rid)
            r["id"] = rid
            if old:
                id_map[old] = rid
            group_id = str(r.get("group", "") or "")
            if group_id:
                r["group"] = ""
            g.setdefault("rules", []).append(r)
        link_map = {}
        for raw in (data.get("links") or []):
            if not isinstance(raw, dict):
                continue
            l = dict(raw)
            old = str(l.get("id", ""))
            lid = "g_" + uuid.uuid4().hex[:8]
            l["id"] = lid
            l["members"] = [id_map[m] for m in (l.get("members") or []) if m in id_map]
            if not l["members"]:
                continue
            if old:
                link_map[old] = lid
            g.setdefault("links", []).append(l)
        # 重新绑定规则到新组
        for raw in rules:
            old_rule = str(raw.get("id", ""))
            old_group = str(raw.get("group", "") or "")
            if old_rule in id_map and old_group in link_map:
                for r in g["rules"]:
                    if r.get("id") == id_map[old_rule]:
                        r["group"] = link_map[old_group]
                        break
        if replace and data.get("on_none_present"):
            g["on_none_present"] = [a for a in data["on_none_present"] if isinstance(a, dict)]
        self._save_multi_group(g)
        self._toast("已导入 %d 条规则" % len(rules), "ok")

    def _test_all_rules(self):
        region = self.monitor_profile.region
        if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
            self._toast("请先框选监控场景区域", "warn")
            return
        try:
            frame = grab(region)
            source = make_source(self.multi_group)
            origin = (0, 0) if getattr(source, "mode", "screen") == "screen" else source.origin
            try:
                frame = source.grab(region)
            finally:
                source.close()
            from core.monitor_rules import match_rule
            lines = []
            for rule in self._rules():
                if not rule.get("template"):
                    continue
                res = match_rule(self.matcher, frame, region, rule, origin)
                lines.append("%s %.2f" % (rule.get("name", "规则"), res.score))
            if not lines:
                self._toast("没有可测试的规则（需要图标模板）", "warn")
            else:
                self._toast("测试：" + "  ".join(lines[:4]), "ok")
        except Exception as exc:
            self._toast("测试失败：%s" % exc, "error")

    def _rules_status_tick(self):
        try:
            engine = getattr(self, "rules_engine", None)
            rules = self._rules()
            resident = [r for r in rules if r.get("resident") and r.get("enabled", True)]
            statuses = {s.get("id"): s for s in (engine.status() if engine else [])}
            present = sum(1 for s in statuses.values() if s.get("present"))
            errors = [s for s in statuses.values() if s.get("error")]
            if not resident:
                text, color = "状态：没有常驻规则", theme.MUTED
            elif errors:
                text, color = "状态：常驻 %d 条 · %d 条异常" % (len(resident), len(errors)), theme.WARN
            else:
                text, color = ("状态：常驻 %d 条 · %d 个图案存在 · 抓屏 %.1fms"
                               % (len(resident), present,
                                  float(getattr(engine, "grab_ms", 0.0)))), theme.ACCENT
            self.rules_status.configure(text=text, text_color=color)
            for rid, lab in list(getattr(self, "_rule_status_labels", {}).items()):
                st = statuses.get(rid)
                if not st:
                    lab.configure(text="状态：未常驻", text_color=theme.MUTED)
                elif st.get("error"):
                    lab.configure(text="状态：%s" % str(st["error"])[:48], text_color=theme.WARN)
                elif st.get("present"):
                    lab.configure(text="状态：已出现 · %.2f · %.1fms" % (
                        float(st.get("score", 0)), float(st.get("cost_ms", 0))),
                        text_color=theme.OK)
                else:
                    lab.configure(text="状态：监控中 · %.2f · %.1fms" % (
                        float(st.get("score", 0)), float(st.get("cost_ms", 0))),
                        text_color=theme.MUTED)
        except Exception:
            pass
        self.after(1000, self._rules_status_tick)

    # ------------------------------------------------------------ 区域/图标
    def _capture_mode(self) -> str:
        return {"屏幕": "screen", "窗口": "window", "窗口(GDI)": "window_gdi"}.get(
            self.capture_var.get(), "screen")

    def _on_capture_mode_change(self, initial=False):
        mode = self._capture_mode()
        old = self.monitor_profile.capture_mode
        if mode != old and ("screen" in (mode, old)):
            # 屏幕坐标与窗口客户区坐标不能混用，切换时清空区域
            self.monitor_profile.region = [0, 0, 0, 0]
            self.region_shot = None
        self.monitor_profile.capture_mode = mode
        try:
            self.window_btn.configure(state="normal" if mode != "screen" else "disabled")
        except Exception:
            pass
        self._refresh_window_label()
        self._refresh_region_label()
        if not initial:
            self._sync_profile()

    def _choose_window(self):
        WindowPicker(self, on_pick=self._on_window_picked)

    def _on_window_picked(self, w):
        p = self.monitor_profile
        if p.capture_mode == "screen":
            p.capture_mode = "window"
            self.capture_var.set("窗口")
        p.window_title = w.title
        p.window_class = w.class_name
        p.window_process = w.process
        p.window_hwnd = w.hwnd
        p.region = [0, 0, w.width, w.height]
        self.region_shot = None
        self._refresh_window_label()
        self._refresh_region_label()
        self._sync_profile()
        self._toast("已绑定窗口：%s" % w.title, "ok")
        if window_is_fullscreen(int(w.hwnd)):
            self._toast("检测到无边框全屏窗口，独占全屏可能抓到黑屏；建议改用窗口化 / 无边框窗口模式", "warn")
        self._grab_preview()

    def _refresh_window_label(self):
        p = self.monitor_profile
        mode = self._capture_mode()
        if mode == "screen":
            self.window_label.configure(text="使用整个屏幕", text_color=theme.MUTED)
            return
        if p.window_title or p.window_process:
            self.window_label.configure(text="%s (%s)" % (p.window_title or "(无标题)",
                                                          p.window_process or "未知进程"),
                                        text_color=theme.TEXT)
        else:
            self.window_label.configure(text="未选择窗口", text_color=theme.WARN)

    def _refresh_region_label(self):
        r = self.monitor_profile.region
        if len(r) == 4 and r[2] > 0 and r[3] > 0:
            prefix = "窗口内" if self._capture_mode() != "screen" else "区域"
            self.region_label.configure(text="%s：X=%d  Y=%d  宽=%d  高=%d"
                                        % (prefix, r[0], r[1], r[2], r[3]))
        else:
            self.region_label.configure(text="区域：未选择")

    def _new_source(self):
        return make_source(self.monitor_profile.to_dict())

    def _pick_region(self, capture: bool = False):
        def restore():
            try:
                self.deiconify()
                self.lift()
                self.focus_force()
                self.attributes("-topmost", True)
                self.after(200, lambda: self.attributes("-topmost", False))
            except Exception:
                pass

        mode = self._capture_mode()
        origin = (0, 0)
        client = None
        if mode != "screen":
            hwnd = find_window(int(self.monitor_profile.window_hwnd or 0),
                               self.monitor_profile.window_title,
                               self.monitor_profile.window_class,
                               self.monitor_profile.window_process)
            if not hwnd:
                self._toast("请先选择窗口", "warn")
                return
            client = client_rect_screen(hwnd)
            if client is None:
                self._toast("窗口客户区无效", "warn")
                return
            origin = (client[0], client[1])
        r = self.monitor_profile.region
        initial = None
        if len(r) == 4 and r[2] > 0 and r[3] > 0:
            initial = [origin[0] + r[0], origin[1] + r[1], r[2], r[3]]

        def done(rect, do_capture=False):
            restore()
            if mode != "screen" and client:
                rel = [int(rect[0]) - origin[0], int(rect[1]) - origin[1],
                       int(rect[2]), int(rect[3])]
                rel[0] = max(0, min(rel[0], client[2] - 1))
                rel[1] = max(0, min(rel[1], client[3] - 1))
                rel[2] = max(1, min(rel[2], client[2] - rel[0]))
                rel[3] = max(1, min(rel[3], client[3] - rel[1]))
                self.monitor_profile.region = rel
            else:
                self.monitor_profile.region = [int(v) for v in rect]
            self._refresh_region_label()
            self._sync_profile()
            self._grab_preview()
            if do_capture:
                self.after(350, self._capture_icon)

        def cancelled():
            restore()

        try:
            self.iconify()
        except Exception:
            pass
        self.after(160, lambda: RegionPicker(self, rect=initial,
                                             on_confirm=done, on_cancel=cancelled))

    def _save_multi_group(self, group, silent: bool = False):
        g = normalize_monitor_group(group)
        p = self.monitor_profile
        g["region"] = list(p.region)
        g["capture_mode"] = p.capture_mode
        g["window_title"] = p.window_title
        g["window_class"] = p.window_class
        g["window_process"] = p.window_process
        g["window_hwnd"] = int(p.window_hwnd or 0)
        g["poll_ms"] = max(20, int(p.poll_ms or 150))
        self.multi_group = g
        self.cfg.set("multi_monitor", self.multi_group)
        self.cfg.set("monitor", p.to_dict())
        try:
            self._update_active_scheme_memory()
            self.cfg.set("monitor_schemes", self.schemes)
            self.cfg.set("active_scheme", self.active_scheme_id)
        except Exception:
            pass
        self.cfg.save()
        self._refresh_multi_label()
        self._refresh_rules_list()
        if not silent:
            self._toast("监控规则已保存：%d 条规则" % len(self.multi_group.get("rules", [])), "ok")

    def _open_rules_window(self):
        if not self._ensure_region_shot():
            return
        region = self.monitor_profile.region
        self.multi_group["region"] = [int(v) for v in region]
        RulesWindow(self, self.multi_group, self.region_shot, region,
                    on_save=self._save_multi_group,
                    on_insert=self._insert_multi_group)

    def _insert_multi_group(self, group=None):
        g = normalize_monitor_group(group or self.multi_group)
        g["region"] = [int(v) for v in self.monitor_profile.region]
        g["capture_mode"] = self.monitor_profile.capture_mode
        if not g.get("rules"):
            self._toast("规则集为空，请先添加图标规则", "warn")
            return
        if not self.script_path:
            self.script_path = new_script_path()
            self._refresh_scripts(select=self.script_path)
        ev = group_to_event(g)
        s = self._active_scheme()
        if s:
            ev["scheme_id"] = s.get("id", "")
            ev["scheme_name"] = s.get("name", "")
        self._add_event(ev)
        self._save_multi_group(g)
        if not self._save_script(silent=True):
            return
        self._show_page("record")
        self._toast("多图标监控节点已插入：%d 条规则" % len(g["rules"]), "ok")

    def _toggle_scene_preview(self):
        if self.preview_stop is not None and not self.preview_stop.is_set():
            self.preview_stop.set()
            self.preview_stop = None
            self.preview_thread = None
            self._toast("实时预览已停止", "info")
            return
        region = self.monitor_profile.region
        if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
            self._toast("请先框选监控场景区域", "warn")
            return
        self._sync_profile()
        self.preview_stop = threading.Event()
        self.preview_thread = threading.Thread(target=self._scene_preview_loop,
                                               args=(self.preview_stop,), daemon=True)
        self.preview_thread.start()
        self._toast("实时预览已启动", "ok")
        self.after(120, self._scene_preview_tick)

    def _scene_preview_loop(self, stop):
        source = None
        region = [int(v) for v in self.monitor_profile.region]
        try:
            source = make_source(self.multi_group)
            origin = (0, 0) if getattr(source, "mode", "screen") == "screen" else source.origin
            while not stop.is_set():
                try:
                    frame = source.grab(region)
                except Exception:
                    stop.wait(0.5)
                    continue
                vis = frame.copy()
                for rule in self._rules():
                    if not rule.get("enabled", True) or not rule.get("template"):
                        continue
                    try:
                        res = match_rule(self.matcher, frame, region, rule, origin)
                        if res.score >= float(rule.get("threshold", 0.85)):
                            x = int(res.x) - region[0]
                            y = int(res.y) - region[1]
                            cv2.rectangle(vis, (x, y), (x + res.w, y + res.h),
                                          (0, 220, 255), 2)
                            cv2.putText(vis, "%s %.2f" % (rule.get("name", "规则"), res.score),
                                        (x, max(14, y - 6)), cv2.FONT_HERSHEY_SIMPLEX,
                                        0.5, (0, 220, 255), 1, cv2.LINE_AA)
                    except Exception:
                        continue
                self.preview_latest = vis
                stop.wait(max(0.05, int(self.multi_group.get("poll_ms", 150)) / 1000.0))
        except Exception as exc:
            log("实时预览失败: %s" % exc, "WARN")
        finally:
            if source is not None:
                try:
                    source.close()
                except Exception:
                    pass

    def _scene_preview_tick(self):
        if self.preview_latest is not None:
            try:
                self.preview_panel.show_pil(to_ctk_image(self.preview_latest))
            except Exception:
                pass
            self.preview_latest = None
        if self.preview_thread is not None and self.preview_thread.is_alive():
            self.after(120, self._scene_preview_tick)

    def _grab_preview(self):
        region = self.monitor_profile.region
        if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
            self._toast("请先框选监控区域", "warn")
            return
        source = None
        try:
            source = self._new_source()
            frame = source.grab(region)
            self.region_shot = frame.copy()
            if self.monitor_profile.template:
                pr = probe(self.matcher, self.monitor_profile, source)
                frame = annotate(frame, self.monitor_profile, pr)
                self.live_score_label.configure(text="%.3f" % pr.score,
                                                text_color=theme.OK if pr.matched else theme.MUTED)
            self.preview_panel.show_pil(to_ctk_image(frame))
            self._toast("预览已刷新", "ok")
        except Exception as exc:
            self._toast("预览失败: %s" % exc, "error")
        finally:
            if source is not None:
                source.close()

    def _capture_icon(self):
        region = self.monitor_profile.region
        if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
            self._toast("请先框选监控区域", "warn")
            return
        if self.region_shot is None:
            source = None
            try:
                source = self._new_source()
                self.region_shot = source.grab(region)
            except Exception as exc:
                self._toast("抓取区域截图失败: %s" % exc, "error")
                return
            finally:
                if source is not None:
                    source.close()
        name = self.icon_name_var.get().strip()
        if not name:
            name = "icon_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        IconCropDialog(self, self.region_shot, region, name,
                       default_crop=None, on_save=self._on_icon_saved)

    def _on_icon_saved(self, path: Path, crop_rect=None):
        self.icon_name_var.set(path.name)
        img = load_image(path)
        if img is not None:
            self.preview_panel.show_pil(to_ctk_image(self.region_shot if self.region_shot is not None else img))
        self._select_icon(path)
        self._refresh_icons()
        self._toast("图标已保存：%s（裁剪 %s）" % (path.name, crop_rect or ""), "ok")

    def _icon_rel(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
        except Exception:
            return str(path)

    def _refresh_icons(self):
        for child in list(self.icons_scroll.winfo_children()):
            child.destroy()
        self._icon_imgs.clear()
        files = []
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.bmp"):
            files.extend(ICONS_DIR.glob(ext))
        files = sorted(set(files), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            ctk.CTkLabel(self.icons_scroll,
                         text="还没有图标\n在左侧框选区域后点「抓取并保存图标」",
                         font=(theme.FONT, theme.fs(10)), text_color=theme.MUTED,
                         justify="left").grid(row=0, column=0, columnspan=3,
                                              sticky="w", padx=8, pady=8)
            return
        for i, path in enumerate(files):
            try:
                img = Image.open(path).convert("RGB")
                img.thumbnail((64, 64), Image.LANCZOS)
                cimg = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
                self._icon_imgs.append(cimg)
            except Exception:
                cimg = None
            selected = bool(self.monitor_profile.template) and \
                Path(self.monitor_profile.template).name == path.name
            b = ctk.CTkButton(self.icons_scroll, text=path.stem[:12], image=cimg,
                              compound="top", width=92, height=88, fg_color=theme.CARD2,
                              hover_color=theme.LINE, font=(theme.FONT, theme.fs(9)),
                              border_width=1,
                              border_color=theme.ACCENT if selected else theme.LINE,
                              command=lambda p=path: self._select_icon(p))
            b.grid(row=i // 3, column=i % 3, padx=5, pady=5, sticky="nsew")
            if selected:
                self.icon_selected_label.configure(text="当前模板：%s" % path.name)
        for c in range(3):
            self.icons_scroll.grid_columnconfigure(c, weight=1)
        try:
            self.after(80, self._icons_autohide)
        except Exception:
            pass


    def _select_icon(self, path: Path):
        self._selected_icon = str(path)
        self.icon_selected_label.configure(text="当前图标：%s" % path.name)
        self._refresh_icons()

    def _add_icons(self):
        if not self._ensure_region_shot():
            return
        name = "icon_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        IconCropDialog(self, self.region_shot, self.monitor_profile.region, name,
                       default_crop=None, on_save=self._on_icon_saved)

    def _delete_icon(self):
        if not self._selected_icon:
            self._toast("先在图标库选中一个图标", "warn")
            return
        p = Path(self._selected_icon)
        if p.exists() and messagebox.askyesno("删除图标", "确定删除 %s？" % p.name):
            try:
                p.unlink()
            except Exception as exc:
                self._toast("删除失败: %s" % exc, "error")
                return
            self._selected_icon = ""
            self.monitor_profile.template = ""
            self.mon_tpl_label.configure(text="未选择图标")
            self.icon_selected_label.configure(text="当前模板：未选择")
            self._sync_profile()
            self._refresh_icons()
            self._toast("图标已删除", "ok")

    # ------------------------------------------------------------ 参数/监控
    def _refresh_monitor_script_menu(self):
        if not hasattr(self, "run_script_menu"):
            return
        names = ["（不运行）"] + [p.name for p in list_scripts()]
        self.run_script_menu.configure(values=names)
        cur = self.monitor_profile.run_script
        if cur:
            name = Path(cur).name
            self.run_script_var.set(name if name in names else "（不运行）")
        else:
            self.run_script_var.set("（不运行）")
        if self.monitor_profile.script_anchor:
            self.anchor_x_var.set(str(self.monitor_profile.script_anchor[0]))
            self.anchor_y_var.set(str(self.monitor_profile.script_anchor[1]))


    def _on_threshold(self):
        v = float(self.threshold_var.get())
        self.threshold_label.configure(text="%.2f" % v)
        self.monitor_profile.threshold = v
        self._sync_profile()

    def _on_hit_action_change(self, initial=False):
        if not hasattr(self, "mouse_frame"):
            return
        action = self.hit_action_var.get()
        self.mouse_frame.grid_remove()
        self.key_frame.grid_remove()
        self.script_frame.grid_remove()
        if action == "脚本操作":
            self.script_frame.grid()
        elif action == "键盘操作":
            self.key_frame.grid()
        else:
            self.mouse_frame.grid()
        if not initial:
            self._sync_profile()

    def _refresh_fixed_label(self):
        try:
            x = int(float(self.fixed_x_var.get() or 0))
            y = int(float(self.fixed_y_var.get() or 0))
            if x == 0 and y == 0:
                self.fixed_label.configure(text="未选择位置")
            else:
                self.fixed_label.configure(text="X=%d Y=%d" % (x, y))
        except Exception:
            pass

    def _pick_fixed_point(self):
        PointPicker(self, on_pick=self._on_fixed_point)

    def _on_fixed_point(self, x, y):
        self.fixed_x_var.set(str(int(x)))
        self.fixed_y_var.set(str(int(y)))
        self._refresh_fixed_label()
        self._sync_profile()

    def _record_key_combo(self):
        try:
            if self.hotkeys:
                self.hotkeys.stop()
                self.hotkeys.join(timeout=0.4)
        except Exception:
            pass
        KeyRecordDialog(self, on_done=self._on_key_recorded,
                        initial=self.key_combo_var.get())

    def _on_key_recorded(self, combo):
        if combo:
            self.key_combo_var.set(combo)
        try:
            self._start_hotkeys()
        except Exception:
            pass
        self._sync_profile()

    def _on_mouse_button_change(self, initial=False):
        if not hasattr(self, "click_style_menu"):
            return
        is_wheel = self.button_var.get() == "滚轮"
        values = CLICK_STYLES + WHEEL_STYLES if is_wheel else CLICK_STYLES
        self.click_style_menu.configure(values=values)
        if self.click_style_var.get() not in values:
            self.click_style_var.set(values[0])
        if self.click_style_var.get() in WHEEL_STYLES:
            self.wheel_frame.pack(fill="x", padx=8, pady=2)
        else:
            self.wheel_frame.pack_forget()
        if not initial:
            self._sync_profile()

    def _on_click_style_change(self, initial=False):
        if not hasattr(self, "wheel_frame"):
            return
        style = self.click_style_var.get()
        if style in WHEEL_STYLES:
            self.button_var.set("滚轮")
            self.click_style_menu.configure(values=CLICK_STYLES + WHEEL_STYLES)
            self.wheel_frame.pack(fill="x", padx=8, pady=2)
        else:
            values = CLICK_STYLES + WHEEL_STYLES if self.button_var.get() == "滚轮" else CLICK_STYLES
            self.click_style_menu.configure(values=values)
            self.wheel_frame.pack_forget()
        if not initial:
            self._sync_profile()

    def _on_click_mode_change(self, initial=False):
        if not hasattr(self, "fixed_frame"):
            return
        mode = self.click_mode_var.get()
        self.fixed_frame.pack_forget()
        self.follow_frame.pack_forget()
        if mode == "固定位置":
            self.fixed_frame.pack(side="left", padx=4)
        elif mode == "偏移跟随":
            self.follow_frame.pack(side="left", padx=4)
        if not initial:
            self._sync_profile()

    def _sync_profile(self):
        p = self.monitor_profile
        p.capture_mode = self._capture_mode()
        try:
            p.poll_ms = max(20, int(float(self.scene_poll_var.get() or 150)))
        except Exception:
            pass
        g = dict(self.multi_group or {})
        g["region"] = list(p.region)
        g["capture_mode"] = p.capture_mode
        g["window_title"] = p.window_title
        g["window_class"] = p.window_class
        g["window_process"] = p.window_process
        g["window_hwnd"] = int(p.window_hwnd or 0)
        g["poll_ms"] = p.poll_ms
        self.multi_group = normalize_monitor_group(g)
        self.cfg.set("monitor", p.to_dict())
        self.cfg.set("multi_monitor", self.multi_group)
        try:
            self._update_active_scheme_memory()
            self.cfg.set("monitor_schemes", self.schemes)
            self.cfg.set("active_scheme", self.active_scheme_id)
        except Exception:
            pass
        self.cfg.save()
        self._refresh_window_label()
        self._refresh_region_label()

    def _test_match(self):
        self._sync_profile()
        ok, err = self.monitor_profile.valid()
        if not ok:
            self._toast(err, "warn")
            return
        try:
            pr = probe(self.matcher, self.monitor_profile)
        except Exception as exc:
            self._toast("匹配失败: %s" % exc, "error")
            return
        self.live_score_label.configure(text="%.3f" % pr.score,
                                        text_color=theme.OK if pr.matched else theme.WARN)
        self.live_bar.set(pr.score)
        if pr.frame is not None:
            self.preview_panel.show_pil(to_ctk_image(annotate(pr.frame, self.monitor_profile, pr)))
            crop = crop_match(pr, self.monitor_profile)
            if crop is not None:
                self.match_panel.show_pil(to_ctk_image(crop))
        self.live_status.configure(text="测试：%s  score=%.3f  耗时 %.1fms"
                                   % ("命中" if pr.matched else "未命中", pr.score, pr.cost_ms))
        self._toast("匹配得分 %.3f（阈值 %.2f）" % (pr.score, self.monitor_profile.threshold),
                    "ok" if pr.matched else "warn")

    def _toggle_live(self):
        if self.live_monitor is not None:
            if self.live_stop:
                self.live_stop.set()
            self.live_monitor = None
            self.live_stop = None
            self.live_var.set(False)
            self.live_bar.set(0)
            self.live_status.configure(text="实时监控已停止")
            return
        self._sync_profile()
        ok, err = self.monitor_profile.valid()
        if not ok:
            self._toast(err, "warn")
            self.live_var.set(False)
            return
        profile = MonitorProfile.from_dict(self.monitor_profile.to_dict())
        self.live_stop = threading.Event()
        self.live_monitor = LiveMonitor(self.matcher, profile,
                                        lambda pr: self.ui_queue.put(("live", pr)),
                                        self.live_stop)
        self.live_monitor.start()
        self.live_status.configure(text="实时监控中（使用当前参数快照）")
        self._toast("实时监控已启动", "ok")

    def _on_live(self, pr: ProbeResult):
        if self.live_monitor is None:
            return
        self.live_bar.set(max(0.0, min(1.0, pr.score)))
        self.live_score_label.configure(text="%.3f" % pr.score,
                                        text_color=theme.OK if pr.matched else theme.MUTED)
        now = time.time()
        if pr.matched:
            self.live_status.configure(text="命中 @ (%d, %d)  score=%.3f"
                                       % (pr.center[0], pr.center[1], pr.score),
                                       text_color=theme.OK)
        else:
            self.live_status.configure(text="监控中…  score=%.3f  耗时 %.1fms"
                                       % (pr.score, pr.cost_ms), text_color=theme.MUTED)
        if now - self._last_live_preview > 0.45 and pr.frame is not None:
            self._last_live_preview = now
            try:
                frame = annotate(pr.frame, self.live_monitor.profile, pr)
                self.preview_panel.show_pil(to_ctk_image(frame))
                crop = crop_match(pr, self.live_monitor.profile)
                if crop is not None:
                    self.match_panel.show_pil(to_ctk_image(crop))
            except Exception:
                pass

    def _insert_monitor_event(self):
        self._sync_profile()
        ok, err = self.monitor_profile.valid()
        if not ok:
            self._toast(err, "warn")
            return
        if not self.script_path:
            self.script_path = new_script_path()
            self._refresh_scripts(select=self.script_path)
        ev = self.monitor_profile.to_event()
        s = self._active_scheme()
        if s:
            ev["scheme_id"] = s.get("id", "")
            ev["scheme_name"] = s.get("name", "")
        self._add_event(ev)
        if not self._save_script(silent=True):
            return
        self._show_page("record")
        self._toast("监控节点已插入脚本：%s（命中后点击图标中心）" % ev.get("name"), "ok")

