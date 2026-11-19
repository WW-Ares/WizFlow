"""设置页。"""
from __future__ import annotations

import os
import subprocess
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

        top = Card(page, "全局热键 / 路径与工具", "热键与应用目录")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        inner = ctk.CTkFrame(top.body, fg_color="transparent")
        inner.pack(fill="x")
        inner.grid_columnconfigure(0, weight=1, uniform="set")
        inner.grid_columnconfigure(1, weight=1, uniform="set")

        left = ctk.CTkFrame(inner, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
        self.hk_vars = {}
        for key, text in (("start_pause", "启动 / 暂停"), ("stop", "停止"), ("record", "录制")):
            row = ctk.CTkFrame(left, fg_color="transparent")
            row.pack(fill="x", pady=3)
            label(row, text, 11, theme.TEXT, width=100, anchor="w").pack(side="left")
            var = tk.StringVar(value=str(self.cfg.get("hotkeys.%s" % key, "F6")))
            self.hk_vars[key] = var
            KeyCaptureEntry(row, var, width=120).pack(side="left", padx=8)
        self._btn(left, "应用热键", self._apply_hotkeys, width=96, color=theme.ACCENT,
                  text_color="#04121f").pack(anchor="w", pady=(10, 0))
        label(left, "热键被占用时会提示；修改后需点应用。", 9).pack(anchor="w", pady=(6, 0))

        right = ctk.CTkFrame(inner, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(18, 0))
        pathbox = ctk.CTkFrame(right, fg_color=theme.BG2, corner_radius=8)
        pathbox.pack(fill="both", expand=True)
        label(pathbox, "程序目录：%s" % ROOT, 9, theme.MUTED, anchor="w",
              justify="left", wraplength=430).pack(anchor="w", padx=10, pady=(8, 0))
        label(pathbox, "脚本：scripts/    图标：icons/    日志：logs/    配置：config.json",
              9, theme.MUTED, anchor="w").pack(anchor="w", padx=10, pady=(4, 0))
        btns = ctk.CTkFrame(pathbox, fg_color="transparent")
        btns.pack(side="bottom", anchor="w", padx=8, pady=(4, 8))
        for i, (txt, cmd) in enumerate([
            ("打开程序目录", lambda: os.startfile(str(ROOT))),
            ("打开脚本目录", self._open_scripts_dir),
            ("打开日志目录", lambda: os.startfile(str(LOGS_DIR))),
            ("打开图标目录", lambda: os.startfile(str(ICONS_DIR))),
            ("打开配置", lambda: os.startfile(str(CONFIG_FILE))),
        ]):
            self._btn(btns, txt, cmd, width=104).grid(row=i // 3, column=i % 3,
                                                      padx=(0, 6), pady=3, sticky="w")

        keep = ctk.CTkFrame(page, fg_color="transparent")
        keep.grid(row=1, column=0, sticky="ew", pady=8)
        keep.grid_columnconfigure(0, weight=1, uniform="keep")
        keep.grid_columnconfigure(1, weight=1, uniform="keep")

        vp = Card(keep, "虚拟屏保活", "Parsec VDD · 真正多一块虚拟显示器")
        vp.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.parsec_var = tk.BooleanVar(value=bool(self.cfg.get("vdisplay_parsec", False)))
        ctk.CTkSwitch(vp.body, text="启用虚拟显示器（关闭显示器后监控区域仍然有画面）",
                      variable=self.parsec_var, font=(theme.FONT, theme.fs(11)),
                      progress_color=theme.ACCENT,
                      command=self._toggle_parsec).pack(anchor="w")
        self.parsec_status = label(vp.body, "状态：检测中…", 10, theme.TEXT)
        self.parsec_status.pack(anchor="w", pady=(8, 4))
        row = ctk.CTkFrame(vp.body, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        self._btn(row, "安装驱动", self._install_driver, width=84,
                  color=theme.CARD2).pack(side="left")
        self._btn(row, "卸载驱动", self._uninstall_driver, width=84).pack(side="left", padx=6)
        self._btn(row, "刷新状态", self._refresh_parsec_status, width=84).pack(side="left")
        self._btn(row, "立即关闭", self._force_parsec_off, width=84).pack(side="left", padx=6)
        label(vp.body, "驱动随软件打包（MIT，已签名），首次启用会弹一次 UAC 管理员授权。", 9).pack(
            anchor="w", pady=(8, 0))
        label(vp.body, "原理：创建一块 Parsec 虚拟显示器并以 50ms 心跳保活。", 9).pack(anchor="w")

        vd = Card(keep, "会话保活", "内置兜底 · 无需驱动")
        vd.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.vd_var = tk.BooleanVar(value=bool(self.cfg.get("vdisplay", False)))
        ctk.CTkSwitch(vd.body, text="阻止睡眠 / 熄屏 / 屏保，空闲时微动鼠标",
                      variable=self.vd_var, font=(theme.FONT, theme.fs(11)),
                      progress_color=theme.ACCENT,
                      command=self._toggle_vdisplay).pack(anchor="w")
        self.vd_status = label(vd.body, "状态：未启用", 10, theme.TEXT)
        self.vd_status.pack(anchor="w", pady=(8, 4))
        row = ctk.CTkFrame(vd.body, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        self._btn(row, "刷新状态", self._refresh_vd_status, width=84).pack(side="left")
        self._btn(row, "立即关闭", self._force_vd_off, width=84).pack(side="left", padx=6)
        label(vd.body, "纯 Windows API：SetThreadExecutionState + 空闲输入微动。", 9).pack(
            anchor="w", pady=(8, 0))
        label(vd.body, "组策略强制锁屏时无法绕过。", 9).pack(anchor="w")

        about = Card(page, "关于 WizFlow", "项目介绍")
        about.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        body = ctk.CTkFrame(about.body, fg_color="transparent")
        body.pack(fill="x")
        texts = ctk.CTkFrame(body, fg_color=theme.BG2, corner_radius=8)
        texts.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=4)
        label(texts, "WizFlow 是面向 Windows 的工作流自动化工具：键鼠录制回放、图片识别、区域监控、脚本编排与虚拟屏保活。",
              9, theme.MUTED, anchor="w", justify="left", wraplength=740).pack(
            anchor="w", padx=10, pady=(6, 0))
        label(texts, "适合办公重复录入、表单登记、批量修改与界面巡检，也可用于游戏自动化；建议以管理员身份运行。",
              9, theme.MUTED, anchor="w", justify="left", wraplength=740).pack(
            anchor="w", padx=10, pady=(2, 0))
        label(texts, "项目地址：https://github.com/WW-Ares/WizFlow    开源协议：GPL-2.0",
              9, theme.MUTED, anchor="w").pack(anchor="w", padx=10, pady=(2, 6))
        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.pack(side="right", padx=(10, 0))
        self._btn(btns, "打开项目主页", self._open_project, width=118,
                  color=theme.ACCENT, text_color="#04121f").pack(pady=(0, 6))

    def _open_project(self):
        import webbrowser
        try:
            webbrowser.open("https://github.com/WW-Ares/WizFlow")
        except Exception as exc:
            self._toast("打开项目主页失败：%s" % exc, "warn")

    def _apply_hotkeys(self):
        vals = {k: v.get().strip() for k, v in self.hk_vars.items()}
        if len(set(vals.values())) != len(vals):
            self._toast("热键不能重复", "warn")
            return
        self.cfg.set("hotkeys", vals)
        self.cfg.save()
        self._start_hotkeys()
        try:
            if self.hud is not None:
                self.hud.refresh_hotkeys()
        except Exception:
            pass
        try:
            self._refresh_header_hotkeys()
        except Exception:
            pass
        try:
            self._refresh_hotkey_button_text()
        except Exception:
            pass
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



    # ------------------------------------------------------------ Parsec 虚拟屏
    def _toggle_parsec(self):
        on = bool(self.parsec_var.get())
        self.cfg.set("vdisplay_parsec", on)
        self.cfg.save()
        if on:
            self.parsec_status.configure(text="状态：正在安装驱动 / 启动虚拟屏…", text_color=theme.WARN)
            threading.Thread(target=self._enable_parsec_bg, daemon=True).start()
        else:
            self.parsec_status.configure(text="状态：正在关闭…", text_color=theme.MUTED)
            threading.Thread(target=self._disable_parsec_bg, daemon=True).start()

    def _enable_parsec_bg(self):
        from core import vdisplay_parsec as vp
        try:
            if not vp.driver_installed():
                err = vp.install_driver()
                if err:
                    self.ui_queue.put(("vdd", "install", err))
                    return
            err = vp.ensure_on()
            self.ui_queue.put(("vdd", "on", err))
        except Exception as exc:
            self.ui_queue.put(("vdd", "on", str(exc)))

    def _disable_parsec_bg(self):
        from core import vdisplay_parsec as vp
        try:
            vp.ensure_off()
            self.ui_queue.put(("vdd", "off", ""))
        except Exception as exc:
            self.ui_queue.put(("vdd", "off", str(exc)))

    def _on_parsec_result(self, kind: str, err: str):
        if kind == "off":
            self.parsec_status.configure(text="状态：已关闭", text_color=theme.MUTED)
            return
        if err:
            self.parsec_status.configure(text="状态：失败：%s" % err, text_color=theme.WARN)
            if kind == "install":
                self.parsec_var.set(False)
                self.cfg.set("vdisplay_parsec", False)
                self.cfg.save()
            self._toast("虚拟屏操作失败：%s" % err, "warn")
            return
        self._refresh_parsec_status()
        if kind == "on":
            self._toast("虚拟屏已启动，监控区域现在有独立画面", "ok")
        elif kind == "install":
            self._toast("Parsec 虚拟屏驱动安装完成", "ok")

    def _refresh_parsec_status(self):
        try:
            from core import vdisplay_parsec as vp
            st = vp.status()
            if st.get("enabled") and st.get("active", 0) > 0:
                text = "状态：虚拟屏运行中 · 当前 %d 块 · 心跳 50ms" % st.get("active", 0)
                color = theme.OK
            elif st.get("enabled"):
                text = "状态：已启用，虚拟屏尚未激活（请检查设备管理器）"
                color = theme.WARN
            elif st.get("installed"):
                text = "状态：驱动已安装 · 未启用"
                color = theme.MUTED
            else:
                text = "状态：驱动未安装（启用时会自动安装）"
                color = theme.MUTED
            self.parsec_status.configure(text=text, text_color=color)
        except Exception as exc:
            self.parsec_status.configure(text="状态读取失败：%s" % exc, text_color=theme.WARN)

    def _force_parsec_off(self):
        self.parsec_var.set(False)
        self.cfg.set("vdisplay_parsec", False)
        self.cfg.save()
        self.parsec_status.configure(text="状态：正在关闭…", text_color=theme.MUTED)
        threading.Thread(target=self._disable_parsec_bg, daemon=True).start()

    def _install_driver(self):
        from tkinter import messagebox
        if not messagebox.askyesno("安装驱动", "将安装内置的 Parsec 虚拟显示器驱动（已签名）。\n"
                                              "首次安装会弹出 UAC 管理员授权，是否继续？"):
            return
        self.parsec_status.configure(text="状态：正在安装驱动，请在 UAC 窗口点「是」…", text_color=theme.WARN)

        def work():
            from core import vdisplay_parsec as vp
            err = vp.install_driver()
            self.ui_queue.put(("vdd", "install", err))

        threading.Thread(target=work, daemon=True).start()

    def _uninstall_driver(self):
        from tkinter import messagebox
        if not messagebox.askyesno("卸载驱动", "将停止虚拟屏并卸载驱动。\n"
                                              "卸载后「虚拟屏保活」不可用，是否继续？"):
            return
        self.parsec_var.set(False)
        self.cfg.set("vdisplay_parsec", False)
        self.cfg.save()

        def work():
            from core import vdisplay_parsec as vp
            try:
                vp.ensure_off()
            except Exception:
                pass
            err = vp.remove_driver()
            self.ui_queue.put(("vdd", "uninstall", err))

        threading.Thread(target=work, daemon=True).start()
