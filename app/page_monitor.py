"""区域监控页。"""
from __future__ import annotations

import datetime
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image

from config import ICONS_DIR, ROOT
from . import theme
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


BTN_MAP = {"左键": "left", "右键": "right", "中键": "middle", "侧键X1": "x1", "侧键X2": "x2"}
STYLE_MAP = {"单击": "single", "双击": "double", "长按": "long"}
MODE_MAP = {"图标中心": "center", "固定位置": "fixed", "偏移跟随": "follow"}


class MonitorPageMixin:
    def _build_monitor_page(self):
        outer, page = self._new_page()
        self.pages["monitor"] = outer
        page.grid_columnconfigure(0, weight=3, uniform="mon")
        page.grid_columnconfigure(1, weight=2, uniform="mon")
        page.grid_rowconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        self.region_shot = None

        region = Card(page, "监控区域", "框选后抓取图标")
        region.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        btns = ctk.CTkFrame(region.body, fg_color="transparent")
        btns.pack(fill="x")
        self._btn(btns, "框选区域", lambda: self._pick_region(False), width=88,
                  color=theme.ACCENT, text_color="#04121f").pack(side="left")
        self._btn(btns, "测试匹配", self._test_match, width=80).pack(side="left")
        self._btn(btns, "多图标规则", self._open_rules_window, width=96,
                  color=theme.PURPLE, text_color="white").pack(side="left", padx=6)

        self.region_label = label(region.body, "区域：未选择", 10, theme.TEXT)
        self.region_label.pack(anchor="w", pady=(10, 2))
        self.multi_group = normalize_monitor_group(self.cfg.get("multi_monitor", {}))
        self.multi_label = label(region.body, "", 9, theme.PURPLE)
        self.multi_label.pack(anchor="w", pady=(0, 6))
        self._refresh_multi_label()
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
        BTN_DISP = {"左键": "left", "右键": "right", "中键": "middle", "侧键X1": "x1", "侧键X2": "x2"}
        BTN_REV = {v: k for k, v in BTN_DISP.items()}
        STYLE_DISP = {"单击": "single", "双击": "double", "长按": "long"}
        STYLE_REV = {v: k for k, v in STYLE_DISP.items()}
        MODE_DISP = {"图标中心": "center", "固定位置": "fixed", "偏移跟随": "follow"}
        MODE_REV = {v: k for k, v in MODE_DISP.items()}

        label(body, "命中后动作", 10).grid(row=r, column=0, sticky="w", pady=3)
        self.hit_action_var = tk.StringVar(
            value="脚本" if self.monitor_profile.hit_action == "script" else ("键盘按键" if self.monitor_profile.hit_action == "key" else "鼠标点击"))
        ctk.CTkOptionMenu(body, variable=self.hit_action_var,
                          values=["鼠标点击", "键盘按键", "脚本"], width=140, height=28,
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
        ctk.CTkOptionMenu(row1, variable=self.button_var, values=list(BTN_DISP.keys()),
                          width=70, height=26, fg_color=theme.CARD2, button_color=theme.LINE,
                          font=(theme.FONT, theme.fs(9)),
                          command=lambda _v: self._sync_profile()).pack(side="left", padx=4)
        label(row1, "点击方式", 9).pack(side="left", padx=(8, 0))
        self.click_style_var = tk.StringVar(value=STYLE_REV.get(self.monitor_profile.click_style, "单击"))
        ctk.CTkOptionMenu(row1, variable=self.click_style_var, values=list(STYLE_DISP.keys()),
                          width=66, height=26, fg_color=theme.CARD2, button_color=theme.LINE,
                          font=(theme.FONT, theme.fs(9)),
                          command=lambda _v: self._sync_profile()).pack(side="left", padx=4)

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
        label(krow, "键盘按键", 9).pack(side="left")
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
        label(srow, "脚本", 9).pack(side="left")
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

    # ------------------------------------------------------------ 区域/图标
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

        def done(rect, do_capture=False):
            restore()
            self.monitor_profile.region = [int(v) for v in rect]
            self.region_label.configure(text="区域：X=%d  Y=%d  宽=%d  高=%d"
                                        % (rect[0], rect[1], rect[2], rect[3]))
            self._sync_profile()
            self._grab_preview()
            if do_capture:
                self.after(350, self._capture_icon)

        def cancelled():
            restore()

        # 先把主窗口最小化，把屏幕让出来，再创建全屏遮罩
        try:
            self.iconify()
        except Exception:
            pass
        self.after(160, lambda: RegionPicker(self, rect=self.monitor_profile.region,
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
        try:
            frame = grab(region)
            self.region_shot = frame.copy()
            if self.monitor_profile.template:
                pr = probe(self.matcher, self.monitor_profile)
                frame = annotate(frame, self.monitor_profile, pr)
                self.live_score_label.configure(text="%.3f" % pr.score,
                                                text_color=theme.OK if pr.matched else theme.MUTED)
            self.preview_panel.show_pil(to_ctk_image(frame))
            self._toast("预览已刷新", "ok")
        except Exception as exc:
            self._toast("预览失败: %s" % exc, "error")

    def _capture_icon(self):
        region = self.monitor_profile.region
        if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
            self._toast("请先框选监控区域", "warn")
            return
        if self.region_shot is None:
            try:
                self.region_shot = grab(region)
            except Exception as exc:
                self._toast("抓取区域截图失败: %s" % exc, "error")
                return
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
        if action == "脚本":
            self.script_frame.grid()
        elif action == "键盘按键":
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
            self.monitor_profile.poll_ms = max(20, int(float(self.poll_var.get())))
            self.monitor_profile.timeout_ms = max(0, int(float(self.timeout_var.get())))
            self.monitor_profile.multiscale = bool(self.multiscale_var.get())
            if self.hit_action_var.get() == "脚本":
                hit = "script"
            elif self.hit_action_var.get() == "键盘按键":
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
        self._add_event(ev)
        if not self._save_script(silent=True):
            return
        self._show_page("record")
        self._toast("监控节点已插入脚本：%s（命中后点击图标中心）" % ev.get("name"), "ok")

