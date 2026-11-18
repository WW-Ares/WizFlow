"""设置页。"""
from __future__ import annotations

import os
import sys
import threading
import tkinter as tk

import customtkinter as ctk

from config import CONFIG_FILE, ICONS_DIR, LOGS_DIR, ROOT, SCRIPTS_DIR
from . import theme
from core.winapi import set_awake
from .widgets import Card, KeyCaptureEntry, label


class SettingsPageMixin:
    def _build_settings_page(self):
        outer, page = self._new_page()
        self.pages["settings"] = outer
        page.grid_columnconfigure(0, weight=1)

        hk = Card(page, "全局热键", "点输入框后直接按键")
        hk.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.hk_vars = {}
        for key, text in (("start_pause", "启动 / 暂停"), ("stop", "停止"), ("record", "录制")):
            row = ctk.CTkFrame(hk.body, fg_color="transparent")
            row.pack(fill="x", pady=3)
            label(row, text, 11, theme.TEXT, width=100, anchor="w").pack(side="left")
            var = tk.StringVar(value=str(self.cfg.get("hotkeys.%s" % key, "F6")))
            self.hk_vars[key] = var
            KeyCaptureEntry(row, var, width=120).pack(side="left", padx=8)
        self._btn(hk.body, "应用热键", self._apply_hotkeys, width=96, color=theme.ACCENT,
                  text_color="#04121f").pack(anchor="w", pady=(10, 0))
        label(hk.body, "热键被占用时会提示；修改后需点应用。", 9).pack(anchor="w", pady=(6, 0))

        ui = Card(page, "界面", "字体与控件缩放")
        ui.grid(row=1, column=0, sticky="ew", pady=8)
        row = ctk.CTkFrame(ui.body, fg_color="transparent")
        row.pack(fill="x", pady=2)
        label(row, "界面缩放", 11, theme.TEXT).pack(side="left")
        cur = float(self.cfg.get("ui_scale", 1.15) or 1.15)
        cur_txt = {1.0: "100%", 1.15: "115%", 1.30: "130%", 1.50: "150%"}.get(cur, "115%")
        self.scale_var = tk.StringVar(value=cur_txt)
        ctk.CTkOptionMenu(row, variable=self.scale_var,
                          values=["100%", "115%", "130%", "150%"],
                          width=100, height=28, fg_color=theme.CARD2,
                          button_color=theme.LINE,
                          command=self._on_scale_change).pack(side="left", padx=10)
        ctk.CTkButton(row, text="保存并重启", width=104, height=28,
                      fg_color=theme.ACCENT, text_color="#04121f",
                      command=self._restart_app).pack(side="left", padx=6)
        label(row, "字体、按钮和输入框同步缩放；重启后生效。", 9).pack(side="left", padx=8)

        vd = Card(page, "虚拟屏保活", "内置会话保活 · 无需安装驱动")
        vd.grid(row=2, column=0, sticky="ew", pady=8)
        self.vd_var = tk.BooleanVar(value=bool(self.cfg.get("vdisplay", False)))
        ctk.CTkSwitch(vd.body, text="启用虚拟屏保活（阻止睡眠/熄屏/屏保，空闲微动鼠标）",
                      variable=self.vd_var, font=(theme.FONT, theme.fs(11)), progress_color=theme.ACCENT,
                      command=self._toggle_vdisplay).pack(anchor="w")
        self.vd_status = label(vd.body, "状态：未启用", 10, theme.TEXT)
        self.vd_status.pack(anchor="w", pady=(8, 4))
        row = ctk.CTkFrame(vd.body, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        self._btn(row, "刷新状态", self._refresh_vd_status, width=84).pack(side="left")
        self._btn(row, "立即关闭", self._force_vd_off, width=84).pack(side="left", padx=6)
        label(row, "纯 Windows API：SetThreadExecutionState + 空闲输入微动，不依赖第三方驱动。", 9).pack(side="left", padx=8)

        about = Card(page, "路径与工具")
        about.grid(row=3, column=0, sticky="ew", pady=8)
        info = ctk.CTkTextbox(about.body, height=150, fg_color=theme.BG2, font=(theme.MONO, theme.fs(9)),
                              text_color=theme.MUTED)
        info.pack(fill="x")
        info.insert("end", "程序目录：%s\n脚本目录：%s\n图标目录：%s\n日志目录：%s\n配置：%s\n"
                    % (ROOT, SCRIPTS_DIR, ICONS_DIR, LOGS_DIR, CONFIG_FILE))
        info.configure(state="disabled")
        row = ctk.CTkFrame(about.body, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        self._btn(row, "打开程序目录", lambda: os.startfile(str(ROOT)), width=110).pack(side="left")
        self._btn(row, "打开脚本目录", self._open_scripts_dir, width=110).pack(side="left", padx=6)
        self._btn(row, "打开日志目录", lambda: os.startfile(str(LOGS_DIR)), width=110).pack(side="left")
        self._btn(row, "打开配置", lambda: os.startfile(str(CONFIG_FILE)), width=110).pack(side="left", padx=6)

    def _on_scale_change(self, value):
        mapping = {"100%": 1.0, "115%": 1.15, "130%": 1.30, "150%": 1.50}
        scale = mapping.get(value, 1.15)
        self.cfg.set("ui_scale", scale)
        self.cfg.save()
        self._toast("界面缩放已保存为 %s，点「保存并重启」生效" % value, "ok")

    def _restart_app(self):
        try:
            self.cfg.save()
            self.player.stop()
            self.recorder.stop()
            if self.hotkeys:
                self.hotkeys.stop()
            try:
                from core.vdisplay import keepalive
                if keepalive.enabled:
                    keepalive.disable()
            except Exception:
                pass
            self.destroy()
            if getattr(sys, "frozen", False):
                os.execl(sys.executable, sys.executable)
            else:
                os.execl(sys.executable, sys.executable, *sys.argv)
        except Exception as exc:
            self._toast("重启失败：%s" % exc, "error")

    def _apply_hotkeys(self):
        vals = {k: v.get().strip() for k, v in self.hk_vars.items()}
        if len(set(vals.values())) != len(vals):
            self._toast("热键不能重复", "warn")
            return
        self.cfg.set("hotkeys", vals)
        self.cfg.save()
        self._start_hotkeys()
        self._toast("热键已应用：启动=%s / 停止=%s / 录制=%s"
                    % (vals["start_pause"], vals["stop"], vals["record"]), "ok")

    def _toggle_vdisplay(self):
        on = bool(self.vd_var.get())
        self.cfg.set("vdisplay", on)
        self.cfg.save()
        if on:
            self.vd_status.configure(text="状态：正在开启会话保活…", text_color=theme.WARN)
            threading.Thread(target=self._enable_vd_bg, daemon=True).start()
        else:
            self.vd_status.configure(text="状态：正在关闭…", text_color=theme.MUTED)
            threading.Thread(target=self._disable_vd_bg, daemon=True).start()

    def _refresh_vd_status(self):
        try:
            from core.vdisplay import keepalive
            st = keepalive.status()
            if st.get("enabled"):
                self.vd_status.configure(
                    text="状态：内置会话保活运行中 · 已微动 %d 次 · 当前空闲 %.1f 秒"
                         % (st.get("nudges", 0), st.get("idle_ms", 0) / 1000.0),
                    text_color=theme.OK)
            else:
                self.vd_status.configure(text="状态：未启用（纯内置，不依赖第三方驱动）",
                                         text_color=theme.MUTED)
        except Exception as exc:
            self.vd_status.configure(text="状态读取失败：%s" % exc, text_color=theme.WARN)

    def _force_vd_off(self):
        self.vd_var.set(False)
        self.cfg.set("vdisplay", False)
        self.cfg.save()
        self.vd_status.configure(text="状态：正在关闭…", text_color=theme.MUTED)
        threading.Thread(target=self._disable_vd_bg, daemon=True).start()


