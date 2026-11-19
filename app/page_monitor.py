"""区域监控页。"""
from __future__ import annotations

import copy
import datetime
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
from core.capture import CaptureError, client_rect_screen, find_window, make_source
from core.monitor import LiveMonitor, MonitorProfile, ProbeResult, annotate, crop_match, probe
from core.monitor_rules import group_to_event, normalize_monitor_group
from core.screen import grab, load_image, to_ctk_image
from core.script import list_scripts, new_script_path
from .region_picker import RegionPicker
from .icon_crop_dialog import IconCropDialog
from .rules_window import RulesWindow
from .point_picker import PointPicker
from .key_record_dialog import KeyRecordDialog
from .widgets import Card, ImagePanel, entry, label
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
        self.mon_name_var.set(p.name)
        self.threshold_var.set(p.threshold)
        try:
            self.threshold_label.configure(text="%.2f" % p.threshold)
        except Exception:
            pass
        self.poll_var.set(str(p.poll_ms))
        self.timeout_var.set(str(p.timeout_ms))
        self.multiscale_var.set(bool(p.multiscale))
        mode_rev = {"screen": "屏幕", "window": "窗口", "window_gdi": "窗口(GDI)"}
        self.capture_var.set(mode_rev.get(p.capture_mode, "屏幕"))
        self.mon_tpl_label.configure(text=p.template or "未选择图标")
        self.region_label.configure(text="区域：未选择")
        hit = "脚本操作" if p.hit_action == "script" else ("键盘操作" if p.hit_action == "key" else "鼠标操作")
        self.hit_action_var.set(hit)
        self.key_combo_var.set(p.key_combo or "1")
        self.key_hold_var.set(str(p.key_hold_ms))
        self.button_var.set({"left": "左键", "right": "右键", "middle": "滚轮",
                             "x1": "侧键X1", "x2": "侧键X2"}.get(p.click_button, "左键"))
        style_rev = {"single": "单击", "double": "双击", "long": "长按",
                     "wheel_up": "上滚动", "wheel_down": "下滚动"}
        self.click_style_var.set(style_rev.get(p.click_style, "单击"))
        mode_map = {"center": "图标中心", "fixed": "固定位置", "follow": "偏移跟随"}
        self.click_mode_var.set(mode_map.get(p.click_mode, "图标中心"))
        self.fixed_x_var.set(str(p.fixed_x))
        self.fixed_y_var.set(str(p.fixed_y))
        self.offx_var.set(str(p.offset_x))
        self.offy_var.set(str(p.offset_y))
        self.rand_x_var.set(str(p.random_x))
        self.rand_y_var.set(str(p.random_y))
        self.hold_var.set(str(p.hold_ms))
        self.wheel_amount_var.set(str(p.wheel_amount))
        self.wheel_interval_var.set(str(p.wheel_interval_ms))
        rs = p.run_script or ""
        try:
            self.run_script_var.set(Path(rs).name if rs else "（不运行）")
        except Exception:
            pass
        try:
            self.anchor_x_var.set(str(p.script_anchor[0]) if len(p.script_anchor) == 2 else "")
            self.anchor_y_var.set(str(p.script_anchor[1]) if len(p.script_anchor) == 2 else "")
        except Exception:
            pass
        self._refresh_fixed_label()
        self._refresh_window_label()
        self._refresh_region_label()
        self._on_hit_action_change(initial=True)
        self._on_click_mode_change(initial=True)
        self._on_mouse_button_change(initial=True)
        self._refresh_multi_label()
        self._refresh_monitor_script_menu()

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

        region = Card(page, "监控区域", "框选后抓取图标")
        region.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        scheme_row = ctk.CTkFrame(region.body, fg_color="transparent")
        scheme_row.pack(fill="x", pady=(0, 6))
        label(scheme_row, "方案", 10).pack(side="left")
        self.scheme_var = tk.StringVar(value="")
        self.scheme_menu = ctk.CTkOptionMenu(scheme_row, variable=self.scheme_var, values=["默认方案"],
                                             width=130, height=26, fg_color=theme.CARD2,
                                             button_color=theme.LINE, font=(theme.FONT, theme.fs(9)),
                                             command=self._switch_scheme)
        self.scheme_menu.pack(side="left", padx=4)
        for text, cmd in (("新建", self._new_scheme), ("复制", self._copy_scheme),
                          ("改名", self._rename_scheme), ("删除", self._delete_scheme),
                          ("导入", self._import_scheme), ("导出", self._export_scheme)):
            ctk.CTkButton(scheme_row, text=text, width=44, height=26, fg_color=theme.CARD2,
                          hover_color=theme.LINE, font=(theme.FONT, theme.fs(9)),
                          command=cmd).pack(side="left", padx=2)
        self._refresh_scheme_menu()

        caprow = ctk.CTkFrame(region.body, fg_color="transparent")
        caprow.pack(fill="x", pady=(0, 6))
        label(caprow, "捕获方式", 10).pack(side="left")
        mode_map = {"屏幕": "screen", "窗口": "window", "窗口(GDI)": "window_gdi"}
        mode_rev = {v: k for k, v in mode_map.items()}
        self.capture_var = tk.StringVar(value=mode_rev.get(self.monitor_profile.capture_mode, "屏幕"))
        ctk.CTkOptionMenu(caprow, variable=self.capture_var, values=list(mode_map.keys()),
                          width=110, height=28, fg_color=theme.CARD2, button_color=theme.LINE,
                          font=(theme.FONT, theme.fs(10)),
                          command=lambda _v: self._on_capture_mode_change()).pack(side="left", padx=6)
        self.window_btn = ctk.CTkButton(caprow, text="选择窗口", width=84, height=28,
                                        fg_color=theme.CARD2, hover_color=theme.LINE,
                                        font=(theme.FONT, theme.fs(10)),
                                        command=self._choose_window)
        self.window_btn.pack(side="left", padx=4)
        self.window_label = label(caprow, "", 9, theme.TEXT, anchor="w")
        self.window_label.pack(side="left", padx=4, fill="x", expand=True)

        btns = ctk.CTkFrame(region.body, fg_color="transparent")
        btns.pack(fill="x")
        self._btn(btns, "框选区域", lambda: self._pick_region(False), width=88,
                  color=theme.ACCENT, text_color="#04121f").pack(side="left")
        self._btn(btns, "测试匹配", self._test_match, width=80).pack(side="left")
        self._btn(btns, "多图标规则", self._open_rules_window, width=96,
                  color=theme.PURPLE, text_color="white").pack(side="left", padx=6)

        self.region_label = label(region.body, "区域：未选择", 10, theme.TEXT)
        self.region_label.pack(anchor="w", pady=(10, 2))
        self.multi_label = label(region.body, "", 9, theme.PURPLE)
        self.multi_label.pack(anchor="w", pady=(0, 6))
        self._refresh_multi_label()
        self._refresh_window_label()
        self._refresh_region_label()
        self._on_capture_mode_change(initial=True)
        self.preview_panel = ImagePanel(region.body, size=(390, 190), text="框选区域后点击刷新预览")
        self.preview_panel.pack(fill="x")

        name_row = ctk.CTkFrame(region.body, fg_color="transparent")
        name_row.pack(fill="x", pady=(10, 0))
        label(name_row, "图标名", 10).pack(side="left")
        self.icon_name_var = tk.StringVar(value="")
        entry(name_row, width=180, textvariable=self.icon_name_var).pack(side="left", padx=6)
        self._btn(name_row, "抓取并保存图标", self._capture_icon, width=124,
                  color=theme.OK, text_color="#04121f").pack(side="left")
        label(name_row, "保存到 icons/", 9).pack(side="left", padx=6)

        icons = Card(page, "图标库", "点击选用模板")
        icons.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(6, 0))
        ih = ctk.CTkFrame(icons.body, fg_color="transparent")
        ih.pack(fill="x")
        self.icon_selected_label = label(ih, "当前模板：未选择", 10, theme.TEXT)
        self.icon_selected_label.pack(side="left")
        self._btn(ih, "刷新", self._refresh_icons, width=58).pack(side="right")
        self._btn(ih, "目录", self._open_icons_dir, width=58).pack(side="right", padx=6)
        self._btn(ih, "删除", self._delete_icon, width=58, color=theme.DANGER,
                  text_color="white").pack(side="right")
        self.icons_scroll = ctk.CTkScrollableFrame(icons.body, fg_color=theme.BG2,
                                                    corner_radius=8)
        self.icons_scroll.pack(fill="both", expand=True, pady=(8, 0))

        sup_card = Card(page, "后台监控", "图案出现 / 消失时控制主脚本")
        sup_card.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        sb = sup_card.body
        r1 = ctk.CTkFrame(sb, fg_color="transparent")
        r1.pack(fill="x")
        self.sup_var = tk.BooleanVar(value=bool(self.cfg.get("script_supervisor.enabled", False)))
        ctk.CTkSwitch(r1, text="启用后台监控", variable=self.sup_var,
                      font=(theme.FONT, theme.fs(10)), progress_color=theme.ACCENT,
                      command=self._toggle_supervisor).pack(side="left")
        label(r1, "图案", 9).pack(side="left", padx=(12, 0))
        self.sup_template_var = tk.StringVar(value="（未选择）")
        self.sup_template_menu = ctk.CTkOptionMenu(
            r1, variable=self.sup_template_var, values=["（未选择）"], width=150, height=26,
            fg_color=theme.CARD2, button_color=theme.LINE, font=(theme.FONT, theme.fs(9)),
            command=lambda _v: self._sup_sync())
        self.sup_template_menu.pack(side="left", padx=4)
        ctk.CTkButton(r1, text="同步当前方案", width=100, height=26, fg_color=theme.CARD2,
                      hover_color=theme.LINE, font=(theme.FONT, theme.fs(9)),
                      command=self._sup_sync_scheme).pack(side="left", padx=6)
        self.sup_status = label(r1, "状态：未启用", 9, theme.MUTED)
        self.sup_status.pack(side="left", padx=8)

        r2 = ctk.CTkFrame(sb, fg_color="transparent")
        r2.pack(fill="x", pady=(6, 0))
        label(r2, "出现时", 9).pack(side="left")
        self.sup_appear_var = tk.StringVar(value="无")
        ctk.CTkOptionMenu(r2, variable=self.sup_appear_var,
                          values=["无", "暂停主脚本", "停止主脚本", "运行脚本"],
                          width=110, height=26, fg_color=theme.CARD2, button_color=theme.LINE,
                          font=(theme.FONT, theme.fs(9)),
                          command=lambda _v: self._sup_sync()).pack(side="left", padx=4)
        label(r2, "消失时", 9).pack(side="left", padx=(10, 0))
        self.sup_disappear_var = tk.StringVar(value="无")
        ctk.CTkOptionMenu(r2, variable=self.sup_disappear_var,
                          values=["无", "继续主脚本", "停止主脚本", "运行脚本"],
                          width=110, height=26, fg_color=theme.CARD2, button_color=theme.LINE,
                          font=(theme.FONT, theme.fs(9)),
                          command=lambda _v: self._sup_sync()).pack(side="left", padx=4)
        label(r2, "脚本", 9).pack(side="left", padx=(10, 0))
        self.sup_script_var = tk.StringVar(value="（无脚本）")
        self.sup_script_menu = ctk.CTkOptionMenu(
            r2, variable=self.sup_script_var, values=["（无脚本）"], width=140, height=26,
            fg_color=theme.CARD2, button_color=theme.LINE, font=(theme.FONT, theme.fs(9)),
            command=lambda _v: self._sup_sync())
        self.sup_script_menu.pack(side="left", padx=4)
        label(r2, "稳定帧", 9).pack(side="left", padx=(8, 0))
        self.sup_stable_var = tk.StringVar(value="2")
        entry(r2, width=40, textvariable=self.sup_stable_var).pack(side="left", padx=2)
        label(r2, "冷却ms", 9).pack(side="left", padx=(8, 0))
        self.sup_cooldown_var = tk.StringVar(value="500")
        entry(r2, width=52, textvariable=self.sup_cooldown_var).pack(side="left", padx=2)
        self._refresh_supervisor_menus()
        self._load_supervisor_ui()
        self.after(1000, self._sup_status_tick)

        params = Card(page, "监控参数", "命中后位置同步")
        params.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(6, 0))
        body = params.body
        body.grid_columnconfigure(1, weight=1)

        r = 0
        label(body, "方案名", 10).grid(row=r, column=0, sticky="w", pady=3)
        self.mon_name_var = tk.StringVar(value=self.monitor_profile.name)
        e = entry(body, width=180, textvariable=self.mon_name_var)
        e.grid(row=r, column=1, sticky="ew", pady=3)
        e.bind("<FocusOut>", lambda _e: self._sync_profile())
        r += 1

        label(body, "模板", 10).grid(row=r, column=0, sticky="w", pady=3)
        self.mon_tpl_label = label(body, self.monitor_profile.template or "未选择图标", 9,
                                   theme.TEXT, anchor="w")
        self.mon_tpl_label.grid(row=r, column=1, sticky="ew", pady=3)
        r += 1

        label(body, "相似度", 10).grid(row=r, column=0, sticky="w", pady=3)
        th = ctk.CTkFrame(body, fg_color="transparent")
        th.grid(row=r, column=1, sticky="ew", pady=3)
        self.threshold_var = tk.DoubleVar(value=self.monitor_profile.threshold)
        self.threshold_slider = ctk.CTkSlider(th, from_=0.50, to=0.99, number_of_steps=49,
                                              variable=self.threshold_var,
                                              progress_color=theme.ACCENT,
                                              command=lambda _v: self._on_threshold())
        self.threshold_slider.pack(side="left", fill="x", expand=True)
        self.threshold_label = ctk.CTkLabel(th, text="%.2f" % self.monitor_profile.threshold,
                                            width=44, font=(theme.MONO, theme.fs(10)), text_color=theme.ACCENT)
        self.threshold_label.pack(side="left", padx=(8, 0))
        r += 1

        label(body, "轮询间隔", 10).grid(row=r, column=0, sticky="w", pady=3)
        rr = ctk.CTkFrame(body, fg_color="transparent")
        rr.grid(row=r, column=1, sticky="ew", pady=3)
        self.poll_var = tk.StringVar(value=str(self.monitor_profile.poll_ms))
        pe = entry(rr, width=64, textvariable=self.poll_var)
        pe.pack(side="left")
        pe.bind("<FocusOut>", lambda _e: self._sync_profile())
        label(rr, "ms", 9).pack(side="left", padx=(4, 12))
        label(rr, "超时", 10).pack(side="left")
        self.timeout_var = tk.StringVar(value=str(self.monitor_profile.timeout_ms))
        te = entry(rr, width=74, textvariable=self.timeout_var)
        te.pack(side="left", padx=4)
        te.bind("<FocusOut>", lambda _e: self._sync_profile())
        label(rr, "ms（0=一直等）", 9).pack(side="left")
        r += 1

        self.multiscale_var = tk.BooleanVar(value=self.monitor_profile.multiscale)
        ctk.CTkSwitch(body, text="多尺度匹配（图标缩放后仍可识别）", variable=self.multiscale_var,
                      font=(theme.FONT, theme.fs(10)), progress_color=theme.ACCENT,
                      command=self._sync_profile).grid(row=r, column=0, columnspan=2,
                                                       sticky="w", pady=(6, 2))
        r += 1
        BTN_DISP = {"左键": "left", "右键": "right", "滚轮": "middle", "侧键X1": "x1", "侧键X2": "x2"}
        BTN_REV = {v: k for k, v in BTN_DISP.items()}
        STYLE_DISP = {"单击": "single", "双击": "double", "长按": "long",
                      "上滚动": "wheel_up", "下滚动": "wheel_down"}
        STYLE_REV = {v: k for k, v in STYLE_DISP.items()}
        CLICK_STYLES = ["单击", "双击", "长按"]
        WHEEL_STYLES = ["上滚动", "下滚动"]
        MODE_DISP = {"图标中心": "center", "固定位置": "fixed", "偏移跟随": "follow"}
        MODE_REV = {v: k for k, v in MODE_DISP.items()}

        label(body, "命中后动作", 10).grid(row=r, column=0, sticky="w", pady=3)
        self.hit_action_var = tk.StringVar(
            value="脚本操作" if self.monitor_profile.hit_action == "script" else ("键盘操作" if self.monitor_profile.hit_action == "key" else "鼠标操作"))
        ctk.CTkOptionMenu(body, variable=self.hit_action_var,
                          values=["鼠标操作", "键盘操作", "脚本操作"], width=140, height=28,
                          fg_color=theme.CARD2, button_color=theme.LINE,
                          command=lambda _v: self._on_hit_action_change()).grid(
            row=r, column=1, sticky="w", pady=3)
        r += 1

        self.mouse_frame = ctk.CTkFrame(body, fg_color=theme.CARD, corner_radius=10)
        self.mouse_frame.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(2, 4))
        mf = self.mouse_frame
        row1 = ctk.CTkFrame(mf, fg_color="transparent")
        row1.pack(fill="x", padx=8, pady=(8, 2))
        label(row1, "鼠标键", 9).pack(side="left")
        self.button_var = tk.StringVar(value=BTN_REV.get(self.monitor_profile.click_button, "左键"))
        self.button_menu = ctk.CTkOptionMenu(row1, variable=self.button_var, values=list(BTN_DISP.keys()),
                                             width=70, height=26, fg_color=theme.CARD2,
                                             button_color=theme.LINE,
                                             font=(theme.FONT, theme.fs(9)),
                                             command=lambda _v: self._on_mouse_button_change())
        self.button_menu.pack(side="left", padx=4)
        label(row1, "操作方式", 9).pack(side="left", padx=(8, 0))
        self.click_style_var = tk.StringVar(value=STYLE_REV.get(self.monitor_profile.click_style, "单击"))
        self.click_style_menu = ctk.CTkOptionMenu(row1, variable=self.click_style_var,
                                                  values=list(STYLE_DISP.keys()), width=76, height=26,
                                                  fg_color=theme.CARD2, button_color=theme.LINE,
                                                  font=(theme.FONT, theme.fs(9)),
                                                  command=lambda _v: self._on_click_style_change())
        self.click_style_menu.pack(side="left", padx=4)

        self.wheel_frame = ctk.CTkFrame(mf, fg_color="transparent")
        self.wheel_frame.pack(fill="x", padx=8, pady=2)
        label(self.wheel_frame, "格数", 9).pack(side="left")
        self.wheel_amount_var = tk.StringVar(value=str(self.monitor_profile.wheel_amount))
        we = entry(self.wheel_frame, width=48, textvariable=self.wheel_amount_var)
        we.pack(side="left", padx=2)
        we.bind("<FocusOut>", lambda _e: self._sync_profile())
        label(self.wheel_frame, "间隔(ms)", 9).pack(side="left", padx=(8, 0))
        self.wheel_interval_var = tk.StringVar(value=str(self.monitor_profile.wheel_interval_ms))
        wi = entry(self.wheel_frame, width=52, textvariable=self.wheel_interval_var)
        wi.pack(side="left", padx=2)
        wi.bind("<FocusOut>", lambda _e: self._sync_profile())

        row2 = ctk.CTkFrame(mf, fg_color="transparent")
        row2.pack(fill="x", padx=8, pady=2)
        label(row2, "位置模式", 9).pack(side="left")
        self.click_mode_var = tk.StringVar(value=MODE_REV.get(self.monitor_profile.click_mode, "偏移跟随"))
        ctk.CTkOptionMenu(row2, variable=self.click_mode_var, values=list(MODE_DISP.keys()),
                          width=90, height=26, fg_color=theme.CARD2, button_color=theme.LINE,
                          font=(theme.FONT, theme.fs(9)),
                          command=lambda _v: self._on_click_mode_change()).pack(side="left", padx=4)

        self.fixed_frame = ctk.CTkFrame(row2, fg_color="transparent")
        self.fixed_frame.pack(side="left", padx=4)
        self.fixed_x_var = tk.StringVar(value=str(self.monitor_profile.fixed_x))
        self.fixed_y_var = tk.StringVar(value=str(self.monitor_profile.fixed_y))
        self.fixed_label = label(self.fixed_frame, "未选择位置", 9, theme.TEXT)
        self.fixed_label.pack(side="left", padx=(0, 4))
        ctk.CTkButton(self.fixed_frame, text="圈选点击位置", width=96, height=26,
                      fg_color=theme.ACCENT, text_color="#04121f",
                      command=self._pick_fixed_point).pack(side="left")
        self._refresh_fixed_label()

        self.follow_frame = ctk.CTkFrame(row2, fg_color="transparent")
        self.follow_frame.pack(side="left", padx=4)
        label(self.follow_frame, "偏移 X", 9).pack(side="left")
        self.offx_var = tk.StringVar(value=str(self.monitor_profile.offset_x))
        ox = entry(self.follow_frame, width=48, textvariable=self.offx_var)
        ox.pack(side="left", padx=2)
        ox.bind("<FocusOut>", lambda _e: self._sync_profile())
        label(self.follow_frame, "Y", 9).pack(side="left")
        self.offy_var = tk.StringVar(value=str(self.monitor_profile.offset_y))
        oy = entry(self.follow_frame, width=48, textvariable=self.offy_var)
        oy.pack(side="left", padx=2)
        oy.bind("<FocusOut>", lambda _e: self._sync_profile())

        row3 = ctk.CTkFrame(mf, fg_color="transparent")
        row3.pack(fill="x", padx=8, pady=(2, 8))
        label(row3, "按住(ms)", 9).pack(side="left")
        self.hold_var = tk.StringVar(value=str(self.monitor_profile.hold_ms))
        he = entry(row3, width=44, textvariable=self.hold_var)
        he.pack(side="left", padx=(2, 10))
        he.bind("<FocusOut>", lambda _e: self._sync_profile())
        label(row3, "随机 X", 9).pack(side="left")
        self.rand_x_var = tk.StringVar(value=str(self.monitor_profile.random_x))
        rx = entry(row3, width=48, textvariable=self.rand_x_var)
        rx.pack(side="left", padx=2)
        rx.bind("<FocusOut>", lambda _e: self._sync_profile())
        label(row3, "Y", 9).pack(side="left")
        self.rand_y_var = tk.StringVar(value=str(self.monitor_profile.random_y))
        ry = entry(row3, width=44, textvariable=self.rand_y_var)
        ry.pack(side="left", padx=2)
        ry.bind("<FocusOut>", lambda _e: self._sync_profile())
        label(row3, "±范围", 8).pack(side="left", padx=2)
        r += 1

        self.key_frame = ctk.CTkFrame(body, fg_color=theme.CARD, corner_radius=10)
        self.key_frame.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(2, 4))
        kf = self.key_frame
        krow = ctk.CTkFrame(kf, fg_color="transparent")
        krow.pack(fill="x", padx=8, pady=8)
        label(krow, "键盘操作", 9).pack(side="left")
        self.key_combo_var = tk.StringVar(value=self.monitor_profile.key_combo or "1")
        ke = entry(krow, width=150, textvariable=self.key_combo_var)
        ke.pack(side="left", padx=4)
        ke.configure(state="readonly")
        ctk.CTkButton(krow, text="录制按键", width=82, height=26,
                      fg_color=theme.PURPLE, text_color="white",
                      command=self._record_key_combo).pack(side="left", padx=4)
        krow2 = ctk.CTkFrame(kf, fg_color="transparent")
        krow2.pack(fill="x", padx=8, pady=(0, 8))
        label(krow2, "按住(ms)", 9).pack(side="left")
        self.key_hold_var = tk.StringVar(value=str(self.monitor_profile.key_hold_ms))
        kh = entry(krow2, width=44, textvariable=self.key_hold_var)
        kh.pack(side="left", padx=(2, 8))
        kh.bind("<FocusOut>", lambda _e: self._sync_profile())
        label(krow2, "点录制后直接按键；支持单键和 Ctrl/Alt/Shift/Win 组合键。", 8).pack(side="left")
        r += 1

        self.script_frame = ctk.CTkFrame(body, fg_color=theme.CARD, corner_radius=10)
        self.script_frame.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(2, 4))
        sf = self.script_frame
        srow = ctk.CTkFrame(sf, fg_color="transparent")
        srow.pack(fill="x", padx=8, pady=8)
        label(srow, "脚本操作", 9).pack(side="left")
        self.run_script_var = tk.StringVar(value="（不运行）")
        self.run_script_menu = ctk.CTkOptionMenu(srow, variable=self.run_script_var,
                                                 values=["（不运行）"], width=150, height=26,
                                                 fg_color=theme.CARD2, button_color=theme.LINE,
                                                 font=(theme.FONT, theme.fs(9)),
                                                 command=lambda _v: self._sync_profile())
        self.run_script_menu.pack(side="left", padx=4)
        label(srow, "锚点 X", 9).pack(side="left", padx=(8, 0))
        self.anchor_x_var = tk.StringVar(value="")
        ax = entry(srow, width=44, textvariable=self.anchor_x_var)
        ax.pack(side="left", padx=2)
        ax.bind("<FocusOut>", lambda _e: self._sync_profile())
        label(srow, "Y", 9).pack(side="left")
        self.anchor_y_var = tk.StringVar(value="")
        ay = entry(srow, width=44, textvariable=self.anchor_y_var)
        ay.pack(side="left", padx=2)
        ay.bind("<FocusOut>", lambda _e: self._sync_profile())
        label(sf, "锚点 = 录制脚本时图标所在屏幕坐标；留空则按脚本原坐标执行。",
              8, theme.MUTED).pack(anchor="w", padx=10, pady=(0, 8))
        r += 1

        self._refresh_monitor_script_menu()
        self._on_hit_action_change(initial=True)
        self._on_click_mode_change(initial=True)
        self._on_mouse_button_change(initial=True)


        ctk.CTkFrame(body, height=1, fg_color=theme.LINE).grid(row=r, column=0, columnspan=2,
                                                               sticky="ew", pady=10)
        r += 1

        live_head = ctk.CTkFrame(body, fg_color="transparent")
        live_head.grid(row=r, column=0, columnspan=2, sticky="ew")
        self.live_var = tk.BooleanVar(value=False)
        ctk.CTkSwitch(live_head, text="实时监控", variable=self.live_var,
                      font=(theme.FONT, theme.fs(11), "bold"), progress_color=theme.OK,
                      command=self._toggle_live).pack(side="left")
        self.live_score_label = ctk.CTkLabel(live_head, text="--", font=(theme.MONO, theme.fs(11), "bold"),
                                             text_color=theme.MUTED)
        self.live_score_label.pack(side="right")
        r += 1
        self.live_bar = ctk.CTkProgressBar(body, height=8, progress_color=theme.OK,
                                           fg_color=theme.BG2)
        self.live_bar.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        self.live_bar.set(0)
        r += 1
        self.live_status = label(body, "未启动实时监控", 9)
        self.live_status.grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        self.match_panel = ImagePanel(body, size=(300, 110), text="命中预览（不保存）")
        self.match_panel.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        r += 1

        self._btn(body, "插入到当前脚本", self._insert_monitor_event, height=38,
                  color=theme.ACCENT, text_color="#04121f",
                  font=(theme.FONT, theme.fs(12), "bold")).grid(row=r, column=0, columnspan=2,
                                                      sticky="ew", pady=(8, 4))
        r += 1
        label(body, "回放遇到该节点：轮询区域 → 命中即点击图标中心 → 继续脚本",
              9).grid(row=r, column=0, columnspan=2, sticky="w")

    # ------------------------------------------------------------ 后台监控
    def _refresh_supervisor_menus(self):
        if not hasattr(self, "sup_template_menu"):
            return
        names = []
        self._sup_icon_map = {}
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.bmp"):
            for p in ICONS_DIR.glob(ext):
                names.append(p.name)
                self._sup_icon_map[p.name] = self._icon_rel(p)
        names.sort()
        self.sup_template_menu.configure(values=["（未选择）"] + names)
        cur = str(self.cfg.get("script_supervisor.template", "") or "")
        if cur and Path(cur).name in names:
            self.sup_template_var.set(Path(cur).name)
        else:
            self.sup_template_var.set("（未选择）")

        snames = ["（无脚本）"] + [p.name for p in list_scripts()]
        self.sup_script_menu.configure(values=snames)
        cur_s = str(self.cfg.get("script_supervisor.run_script", "") or "")
        if cur_s and Path(cur_s).name in snames:
            self.sup_script_var.set(Path(cur_s).name)
        else:
            self.sup_script_var.set("（无脚本）")

    def _load_supervisor_ui(self):
        c = self.cfg.get("script_supervisor", {}) or {}
        self.sup_var.set(bool(c.get("enabled", False)))
        self.sup_appear_var.set(SUP_APPEAR_REV.get(str(c.get("appear", "none")), "无"))
        self.sup_disappear_var.set(SUP_DISAPPEAR_REV.get(str(c.get("disappear", "none")), "无"))
        self.sup_stable_var.set(str(c.get("stable_frames", 2)))
        self.sup_cooldown_var.set(str(c.get("cooldown_ms", 500)))

    def _sup_sync(self):
        if not hasattr(self, "sup_var"):
            return
        name = self.sup_template_var.get()
        template = ""
        if name and name not in ("（未选择）", "（无）"):
            template = getattr(self, "_sup_icon_map", {}).get(name, "icons/" + name)
        sname = self.sup_script_var.get()
        script = "scripts/" + sname if sname and sname != "（无脚本）" else ""
        try:
            stable = max(1, int(float(self.sup_stable_var.get() or 2)))
        except Exception:
            stable = 2
        try:
            cooldown = max(0, int(float(self.sup_cooldown_var.get() or 500)))
        except Exception:
            cooldown = 500
        patch = {
            "enabled": bool(self.sup_var.get()),
            "template": template,
            "appear": SUP_APPEAR.get(self.sup_appear_var.get(), "none"),
            "disappear": SUP_DISAPPEAR.get(self.sup_disappear_var.get(), "none"),
            "run_script": script,
            "stable_frames": stable,
            "cooldown_ms": cooldown,
        }
        for k, v in patch.items():
            self.cfg.set("script_supervisor." + k, v)
        self.cfg.save()

    def _toggle_supervisor(self):
        on = bool(self.sup_var.get())
        if on:
            c = self.cfg.get("script_supervisor", {}) or {}
            region = list(c.get("region") or [0, 0, 0, 0])
            if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
                self._sup_sync_scheme(silent=True)
                c = self.cfg.get("script_supervisor", {}) or {}
                region = list(c.get("region") or [0, 0, 0, 0])
            if not str(c.get("template", "") or "") and self.monitor_profile.template:
                self.cfg.set("script_supervisor.template", self.monitor_profile.template)
                self.cfg.save()
                self._refresh_supervisor_menus()
            if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
                self.sup_var.set(False)
                self._sup_sync()
                self._toast("先在左侧框选监控区域，再启用后台监控", "warn")
                return
        self._sup_sync()
        self._toast("后台监控已开启" if on else "后台监控已关闭", "ok" if on else "info")

    def _sup_sync_scheme(self, silent=False):
        p = self.monitor_profile
        region = list(p.region or [0, 0, 0, 0])
        if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
            if not silent:
                self._toast("当前方案还没有框选监控区域", "warn")
            return
        patch = {
            "region": region,
            "capture_mode": p.capture_mode,
            "window_title": p.window_title,
            "window_class": p.window_class,
            "window_process": p.window_process,
            "window_hwnd": int(p.window_hwnd or 0),
            "threshold": float(p.threshold),
            "poll_ms": max(20, int(p.poll_ms)),
        }
        if p.template:
            patch["template"] = p.template
        for k, v in patch.items():
            self.cfg.set("script_supervisor." + k, v)
        self.cfg.save()
        self._refresh_supervisor_menus()
        if not silent:
            self._toast("已同步当前方案：区域、抓屏方式与图案", "ok")

    def _sup_status_tick(self):
        try:
            sup = getattr(self, "supervisor", None)
            if sup is not None:
                st = sup.status()
                if not st.get("enabled"):
                    text, color = "状态：未启用", theme.MUTED
                elif st.get("error"):
                    text, color = "状态：%s" % str(st["error"])[:36], theme.WARN
                elif st.get("present"):
                    text, color = "状态：已出现 · %.2f" % float(st.get("score", 0)), theme.OK
                else:
                    text, color = "状态：监控中 · %.2f" % float(st.get("score", 0)), theme.ACCENT
                self.sup_status.configure(text=text, text_color=color)
        except Exception:
            pass
        self.after(1000, self._sup_status_tick)

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

    def _refresh_multi_label(self):
        n = len(self.multi_group.get("rules", []))
        links = len(self.multi_group.get("links", []))
        try:
            self.multi_label.configure(text="多图标规则：%d 条 · 关联组 %d 个（点击管理）" % (n, links))
        except Exception:
            pass

    def _save_multi_group(self, group):
        self.multi_group = normalize_monitor_group(group)
        self.cfg.set("multi_monitor", self.multi_group)
        try:
            self._update_active_scheme_memory()
            self.cfg.set("monitor_schemes", self.schemes)
        except Exception:
            pass
        self.cfg.save()
        self._refresh_multi_label()
        self._toast("多图标规则已保存：%d 条规则" % len(self.multi_group.get("rules", [])), "ok")

    def _open_rules_window(self):
        region = self.monitor_profile.region
        if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
            self._toast("请先框选监控区域", "warn")
            return
        self._sync_profile()
        if self.region_shot is None:
            try:
                self.region_shot = grab(region)
            except Exception as exc:
                self._toast("区域截图失败：%s" % exc, "error")
                return
        self.multi_group["region"] = [int(v) for v in region]
        RulesWindow(self, self.multi_group, self.region_shot, region,
                    on_save=self._save_multi_group,
                    on_insert=self._insert_multi_group)

    def _insert_multi_group(self, group):
        g = normalize_monitor_group(group)
        g["region"] = [int(v) for v in self.monitor_profile.region]
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
            self.match_panel.show_pil(to_ctk_image(img))
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
        self._refresh_supervisor_menus()

    def _select_icon(self, path: Path):
        self._selected_icon = str(path)
        self.monitor_profile.template = self._icon_rel(path)
        self.icon_selected_label.configure(text="当前模板：%s" % path.name)
        self.mon_tpl_label.configure(text=self.monitor_profile.template)
        self._sync_profile()
        self._refresh_icons()
        try:
            img = load_image(path)
            if img is not None:
                self.match_panel.show_pil(to_ctk_image(img))
        except Exception:
            pass

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
        self._refresh_supervisor_menus()

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
        try:
            self.monitor_profile.name = self.mon_name_var.get().strip() or "监控1"
            self.monitor_profile.capture_mode = self._capture_mode()
            self.monitor_profile.poll_ms = max(20, int(float(self.poll_var.get())))
            self.monitor_profile.timeout_ms = max(0, int(float(self.timeout_var.get())))
            self.monitor_profile.multiscale = bool(self.multiscale_var.get())
            if self.hit_action_var.get() == "脚本操作":
                hit = "script"
            elif self.hit_action_var.get() == "键盘操作":
                hit = "key"
            else:
                hit = "mouse"
            self.monitor_profile.hit_action = hit
            self.monitor_profile.click_sync = (hit == "mouse")
            self.monitor_profile.key_combo = self.key_combo_var.get().strip() or "1"
            self.monitor_profile.key_hold_ms = max(1, int(float(self.key_hold_var.get() or 40)))
            self.monitor_profile.click_button = BTN_MAP.get(self.button_var.get(), "left")
            self.monitor_profile.click_style = STYLE_MAP.get(self.click_style_var.get(), "single")
            self.monitor_profile.click_mode = MODE_MAP.get(self.click_mode_var.get(), "center")
            self.monitor_profile.wheel_dir = "up" if self.monitor_profile.click_style == "wheel_up" else (
                "down" if self.monitor_profile.click_style == "wheel_down" else self.monitor_profile.wheel_dir)
            self.monitor_profile.wheel_amount = max(1, int(float(self.wheel_amount_var.get() or 3)))
            self.monitor_profile.wheel_interval_ms = max(0, int(float(self.wheel_interval_var.get() or 60)))
            self.monitor_profile.fixed_x = int(float(self.fixed_x_var.get() or 0))
            self.monitor_profile.fixed_y = int(float(self.fixed_y_var.get() or 0))
            self.monitor_profile.offset_x = int(float(self.offx_var.get() or 0))
            self.monitor_profile.offset_y = int(float(self.offy_var.get() or 0))
            self.monitor_profile.random_x = abs(int(float(self.rand_x_var.get() or 0)))
            self.monitor_profile.random_y = abs(int(float(self.rand_y_var.get() or 0)))
            self.monitor_profile.hold_ms = max(1, int(float(self.hold_var.get() or 40)))
            rs = self.run_script_var.get().strip() if hasattr(self, "run_script_var") else ""
            self.monitor_profile.run_script = "" if rs in ("", "（不运行）") else ("scripts/" + rs)
            axs = self.anchor_x_var.get().strip() if hasattr(self, "anchor_x_var") else ""
            ays = self.anchor_y_var.get().strip() if hasattr(self, "anchor_y_var") else ""
            if axs and ays:
                self.monitor_profile.script_anchor = [int(float(axs)), int(float(ays))]
            else:
                self.monitor_profile.script_anchor = []
        except Exception:
            pass
        self.cfg.set("monitor", self.monitor_profile.to_dict())
        try:
            self._update_active_scheme_memory()
            self.cfg.set("monitor_schemes", self.schemes)
            self.cfg.set("active_scheme", self.active_scheme_id)
        except Exception:
            pass
        self.cfg.save()

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

