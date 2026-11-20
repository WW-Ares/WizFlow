"""主窗口：装配页面、热键、队列与生命周期。"""
from __future__ import annotations

import os
import queue
import threading
import time
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image

from config import (APP_NAME, APP_VERSION, ICONS_DIR, LOGS_DIR,
                    ROOT, SCRIPTS_DIR, Config, asset_path)
from . import theme
from .hud import HudWindow
from .tray import TrayIcon
from core.actions import execute_input
from core.hotkeys import HotkeyManager, hotkey_conflicts, normalize_hotkey
from core.log import log
from core.monitor import MonitorProfile
from core.player import Player
from core.recorder import Recorder
from core.screen import TemplateMatcher
from core.rules import BackgroundRuleEngine
from core.vdisplay import keepalive
from core.window_geometry import center_on_primary, primary_work_area, rect_visible_enough
from core.winapi import is_admin, set_awake
from .page_monitor import MonitorPageMixin
from .page_record import RecordPageMixin
from .page_settings import SettingsPageMixin
from .widgets import Pill, ScrollPage


class App(RecordPageMixin, MonitorPageMixin, SettingsPageMixin, ctk.CTk):
    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        # 统一默认字体族与字号：未显式写 font 的控件也跟随界面缩放
        ctk.ThemeManager.theme["CTkFont"]["family"] = theme.FONT
        ctk.ThemeManager.theme["CTkFont"]["size"] = theme.fs(13)

        # 下拉列表字体跟随当前显示值，避免列表项被默认字号放大
        if not getattr(ctk.CTkOptionMenu, "_wf_font_patched", False):
            _orig_menu_init = ctk.CTkOptionMenu.__init__

            def _wf_menu_init(self, *args, **kwargs):
                _f = kwargs.get("font")
                if _f is not None and kwargs.get("dropdown_font") is None:
                    kwargs["dropdown_font"] = _f
                _orig_menu_init(self, *args, **kwargs)

            ctk.CTkOptionMenu.__init__ = _wf_menu_init
            ctk.CTkOptionMenu._wf_font_patched = True
        super().__init__(fg_color=theme.BG)
        self.title("%s  v%s" % (APP_NAME, APP_VERSION))
        self._set_window_icon()
        self.geometry("1200x860")
        self.minsize(1200, 860)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.cfg = Config()
        self._restore_window_rect()
        self.matcher = TemplateMatcher()
        self.recorder = Recorder(self._record_callback)
        self.player = Player(self.matcher, self._player_status, self._player_finished)
        self.hotkeys: HotkeyManager | None = None
        self.rules_engine = BackgroundRuleEngine(
            lambda: self.cfg.data,
            lambda rule_id, act: self.ui_queue.put(("rule_action", rule_id, act)))
        self._last_dpi = None
        self._script_warn_at = {}

        self.script_path = None
        self.events: list[dict] = []
        self.recording = False
        self._rec_started = 0.0
        self._event_rows: list = []

        self.monitor_profile = MonitorProfile.from_dict(self.cfg.get("monitor", {}))
        self.live_monitor = None
        self.live_stop: threading.Event | None = None
        self._last_live_preview = 0.0
        self._icon_imgs: list = []
        self._selected_icon = ""

        self.ui_queue: queue.Queue = queue.Queue()
        self.input_queue: queue.Queue = queue.Queue()
        self.input_stop = threading.Event()
        threading.Thread(target=self._input_worker, name="wizflow-input",
                         daemon=True).start()

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self._build_header()
        self._build_sidebar()
        self._build_content()
        self._build_statusbar()
        self.hud: HudWindow | None = None
        self.tray: TrayIcon | None = None
        self._quitting = False
        self.recording = False
        self._build_hud()
        self._build_tray()

        self._refresh_scripts()
        self._refresh_icons()
        self._load_last_script()
        self.after(80, self._pump)
        self.after(350, self._startup)
        self.after(3000, self._watch_dpi)

        if os.environ.get("KMGPRO_UITEST") == "1":
            self.after(2400, self._ui_smoke)

    def _set_window_icon(self):
        try:
            ico = asset_path("logo.ico")
            if ico.exists():
                self.iconbitmap(str(ico))
        except Exception:
            pass

    # ================================================================ 布局
    def _build_header(self):
        head = ctk.CTkFrame(self, fg_color=theme.BG2, corner_radius=0, height=66)
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        head.grid_propagate(False)
        head.grid_columnconfigure(1, weight=1)

        logo = ctk.CTkFrame(head, fg_color="transparent")
        logo.grid(row=0, column=0, padx=20, pady=9, sticky="w")
        try:
            _lp = asset_path("logo_header.png")
            _img = Image.open(_lp).convert("RGBA")
            _img.thumbnail((48, 48), Image.LANCZOS)
            self._logo_img = ctk.CTkImage(light_image=_img, dark_image=_img, size=_img.size)
            ctk.CTkLabel(logo, text="", image=self._logo_img).pack(side="left", padx=(0, 10))
        except Exception:
            ctk.CTkLabel(logo, text="W", font=(theme.FONT, theme.fs(28), "bold"),
                         text_color=theme.ACCENT).pack(side="left", padx=(0, 10))
        box = ctk.CTkFrame(logo, fg_color="transparent")
        box.pack(side="left")
        ctk.CTkLabel(box, text="WizFlow", font=(theme.FONT, theme.fs(20), "bold"),
                     text_color=theme.TEXT).pack(anchor="w")
        ctk.CTkLabel(box, text="键鼠录制 · 图片识别 · 区域监控 · 自动化任务",
                     font=(theme.FONT, theme.fs(10)), text_color=theme.MUTED).pack(anchor="w")

        right = ctk.CTkFrame(head, fg_color="transparent")
        right.grid(row=0, column=2, padx=20, sticky="e")
        self.hud_var = tk.BooleanVar(value=bool(self.cfg.get("hud.enabled", True)))
        ctk.CTkSwitch(right, text="悬浮窗", variable=self.hud_var, width=58,
                      font=(theme.FONT, theme.fs(12)), progress_color=theme.ACCENT,
                      command=self._toggle_hud).pack(side="left", padx=(0, 8))
        self.hotkey_hint = ctk.CTkLabel(right, text="", font=(theme.MONO, theme.fs(12)),
                                        text_color=theme.MUTED)
        self.hotkey_hint.pack(side="left", padx=(0, 8))
        self._refresh_header_hotkeys()
        self.header_pill = Pill(right, "空闲", theme.OK, width=116, height=30,
                                font=(theme.FONT, theme.fs(13), "bold"))
        self.header_pill.pack(side="left")

    def _refresh_header_hotkeys(self):
        try:
            self.hotkey_hint.configure(
                text="%s 启动/暂停 · %s 停止 · %s 录制" % (
                    str(self.cfg.get("hotkeys.start_pause", "F6")).upper(),
                    str(self.cfg.get("hotkeys.stop", "F7")).upper(),
                    str(self.cfg.get("hotkeys.record", "F8")).upper()))
        except Exception:
            pass

    def _build_sidebar(self):
        holder = ctk.CTkFrame(self, fg_color=theme.BG2, corner_radius=0, width=124)
        holder.grid(row=1, column=0, sticky="nsw")
        holder.grid_propagate(False)
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)
        side = ctk.CTkFrame(holder, fg_color=theme.BG2, corner_radius=0)
        side.place(x=0, y=0, relwidth=1, relheight=1)

        self.nav: dict[str, ctk.CTkButton] = {}
        items = [("record", "▣ 录制回放"), ("monitor", "◎ 区域监控"), ("settings", "⚙ 设置")]
        for i, (key, text) in enumerate(items):
            b = ctk.CTkButton(side, text=text, anchor="w", height=38, width=112,
                              fg_color="transparent", hover_color=theme.CARD2,
                              text_color=theme.MUTED, font=(theme.FONT, theme.fs(11.5)),
                              command=lambda k=key: self._show_page(k))
            b.pack(padx=6, pady=(10 if i == 0 else 2, 0))
            self.nav[key] = b

        ctk.CTkFrame(side, height=1, fg_color=theme.LINE).pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(side, text="v%s" % APP_VERSION, font=(theme.MONO, theme.fs(9)),
                     text_color=theme.MUTED).pack(side="bottom", pady=(0, 10))

        info = ctk.CTkFrame(side, fg_color=theme.CARD, corner_radius=10)
        info.pack(side="bottom", fill="x", padx=5, pady=(0, 6))
        ctk.CTkLabel(info, text="运行环境", font=(theme.FONT, theme.fs(10), "bold"),
                     text_color=theme.MUTED).pack(anchor="center", pady=(8, 2))
        admin = is_admin()
        pill = Pill(info, "管理员" if admin else "普通权限",
                    theme.OK if admin else theme.WARN)
        pill.pack(anchor="center", pady=(0, 4))
        ctk.CTkLabel(info, text="游戏管理员运行\n需右键管理员",
                     font=(theme.FONT, theme.fs(9)), text_color=theme.MUTED,
                     justify="center").pack(anchor="center", padx=2, pady=(0, 6))

    def _build_content(self):
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=1, column=1, sticky="nsew", padx=10, pady=10)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        self.pages: dict[str, ctk.CTkFrame] = {}
        self._build_record_page()
        self._build_monitor_page()
        self._build_settings_page()
        self._show_page("record")

    def _new_page(self):
        """页面容器：内容超出时才显示横 / 竖滚动条。"""
        page = ScrollPage(self.content)
        page.grid(row=0, column=0, sticky="nsew")
        return page, page.inner

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self, fg_color=theme.BG2, corner_radius=0, height=28)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew")
        bar.grid_propagate(False)
        self.status_label = ctk.CTkLabel(bar, text="就绪", font=(theme.FONT, theme.fs(10)),
                                         text_color=theme.MUTED, anchor="w")
        self.status_label.pack(side="left", padx=16)
        self.count_label = ctk.CTkLabel(bar, text="事件 0", font=(theme.MONO, theme.fs(10)),
                                        text_color=theme.MUTED)
        self.count_label.pack(side="right", padx=16)

    # ================================================================ 工具
    def _btn(self, parent, text, cmd, width=80, height=30, color=theme.CARD2,
             text_color=theme.TEXT, font=None):
        return ctk.CTkButton(parent, text=text, command=cmd, width=width, height=height,
                             fg_color=color, hover_color=theme.LINE, text_color=text_color,
                             corner_radius=8, font=font or (theme.FONT, theme.fs(10)))

    def _input_worker(self):
        while not self.input_stop.is_set():
            try:
                item = self.input_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                break
            act, center = item
            try:
                execute_input(act, center, dry_run=False, stop_event=self.input_stop)
            except Exception as exc:
                log("后台输入动作失败: %s" % exc, "WARN")

    def _show_page(self, key: str):
        if getattr(self, "page", "") == "monitor" and key != "monitor":
            try:
                self._pause_scene_preview()
            except Exception:
                pass
        for k, b in self.nav.items():
            b.configure(fg_color=theme.CARD2 if k == key else "transparent",
                        text_color=theme.ACCENT if k == key else theme.MUTED)
        for k, frame in self.pages.items():
            try:
                if k == key:
                    frame.grid()
                else:
                    frame.grid_remove()
            except Exception:
                pass
        self.page = key
        try:
            frame = self.pages.get(key)
            if frame is not None:
                self.after(120, frame.fill_height)
        except Exception:
            pass

    def _toast(self, msg: str, level: str = "info"):
        color = {"info": theme.MUTED, "ok": theme.OK, "warn": theme.WARN,
                 "error": theme.DANGER}.get(level, theme.MUTED)
        try:
            self.status_label.configure(text=msg, text_color=color)
        except Exception:
            pass
        log(msg, level.upper())

    def _sync_rule_engine_pause(self):
        try:
            if bool(self.cfg.get("pause_rules_on_play", False)) and self.player.running:
                self.rules_engine.pause()
            else:
                self.rules_engine.resume()
        except Exception:
            pass

    def _notify_user(self, text: str):
        text = str(text or "规则通知")
        self._toast(text, "info")
        try:
            if self.tray is not None:
                self.tray.notify(text)
        except Exception:
            pass
        self._show_notify_popup(text)

    def _show_notify_popup(self, text: str):
        try:
            old = getattr(self, "_notify_win", None)
            if old is not None:
                old.destroy()
        except Exception:
            pass
        try:
            win = tk.Toplevel(self)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.configure(bg=theme.LINE)
            box = ctk.CTkFrame(win, fg_color=theme.CARD2, corner_radius=8)
            box.pack(fill="both", expand=True, padx=1, pady=1)
            ctk.CTkLabel(box, text=text, justify="left", anchor="w", wraplength=320,
                         text_color=theme.TEXT,
                         font=(theme.FONT, theme.fs(10))).pack(padx=14, pady=10)
            win.update_idletasks()
            w = min(360, max(180, win.winfo_reqwidth()))
            h = win.winfo_reqheight()
            px, py, pw, _ph = primary_work_area()
            x = px + pw - w - 24
            # 固定放在悬浮窗下方，避免盖住 HUD 状态与键位
            y = py + 116
            win.geometry("%dx%d+%d+%d" % (w, h, x, y))
            self._notify_win = win
            win.after(3500, win.destroy)
        except Exception:
            self._notify_win = None

    def _set_header(self, text: str, color: str):
        self.header_pill.set(text, color)
        try:
            if self.hud is not None:
                self.hud.set_status(text, color)
        except Exception:
            pass

    # ================================================================ 悬浮窗
    def _build_hud(self):
        try:
            self.hud = HudWindow(self, self.cfg)
            if bool(self.cfg.get("hud.enabled", True)):
                self.hud.show()
        except Exception as exc:
            log("悬浮窗创建失败: %s" % exc, "WARN")
            self.hud = None

    def _toggle_hud(self):
        on = bool(self.hud_var.get())
        cfg_hud = dict(self.cfg.get("hud", {}) or {})
        cfg_hud["enabled"] = on
        self.cfg.set("hud", cfg_hud)
        self.cfg.save()
        if self.hud is None:
            self._build_hud()
            return
        # 重新开关悬浮窗时自动取消穿透，避免用户无法再点回设置
        try:
            self.hud.set_click_through(False)
        except Exception:
            pass
        if on:
            self.hud.show()
        else:
            self.hud.hide()

    # ================================================================ 托盘
    def _build_tray(self):
        try:
            self.tray = TrayIcon(self, asset_path("logo.png"))
            if not self.tray.start():
                self.tray = None
        except Exception as exc:
            log("托盘创建失败: %s" % exc, "WARN")
            self.tray = None

    def _restore_window(self):
        try:
            self.deiconify()
            self.state("normal")
            self.lift()
            self.focus_force()
            self.attributes("-topmost", True)
            self.after(200, lambda: self.attributes("-topmost", False))
        except Exception:
            pass

    def _handle_tray(self, action: str):
        if action == "show":
            self._restore_window()
        elif action == "toggle_hud":
            self.hud_var.set(not bool(self.hud_var.get()))
            self._toggle_hud()
        elif action == "hud_click_through_off":
            if self.hud is not None:
                self.hud.set_click_through(False)
                self._toast("已取消悬浮窗鼠标穿透", "ok")
        elif action == "quit":
            self._quit_app()

    def _restore_window_rect(self):
        w, h = 1200, 860
        rect = self.cfg.get("window_rect", None) or {}
        x = y = None
        try:
            x, y = int(rect.get("x")), int(rect.get("y"))
        except Exception:
            x = y = None
        if x is None or not rect_visible_enough(x, y, w, h):
            x, y = center_on_primary(w, h)
        self.geometry("%dx%d+%d+%d" % (w, h, x, y))

    def _save_window_rect(self):
        try:
            if self.state() != "normal":
                return
            self.cfg.set("window_rect", {"x": int(self.winfo_x()), "y": int(self.winfo_y())})
            self.cfg.save()
        except Exception:
            pass

    def _quit_app(self):
        self._quitting = True
        self.on_close()

    def _open_scripts_dir(self):
        os.startfile(str(SCRIPTS_DIR))

    def _open_icons_dir(self):
        os.startfile(str(ICONS_DIR))

    # ================================================================ 队列
    def _record_callback(self, ev: dict):
        self.ui_queue.put(("record_event", ev))

    def _player_status(self, text: str, progress):
        self.ui_queue.put(("player_status", text, progress))

    def _player_finished(self, reason: str):
        self.ui_queue.put(("player_done", reason))

    def _pump(self):
        try:
            for _ in range(200):
                msg = self.ui_queue.get_nowait()
                kind = msg[0]
                if kind == "record_event":
                    self._add_event(msg[1])
                elif kind == "player_status":
                    self._apply_player_status(msg[1], msg[2])
                elif kind == "player_done":
                    self._apply_player_done(msg[1])
                elif kind == "hotkey":
                    self._handle_hotkey(msg[1])
                elif kind == "live":
                    self._on_live(msg[1])
                elif kind == "vd":
                    self._on_vd_result(msg[1])
                elif kind == "vdd":
                    self._on_parsec_result(msg[1], msg[2] if len(msg) > 2 else "")
                elif kind == "toast":
                    self._toast(msg[1], msg[2] if len(msg) > 2 else "info")
                elif kind == "tray":
                    self._handle_tray(msg[1])
                elif kind == "rule_action":
                    self._handle_rule_action(msg[1], msg[2])
        except queue.Empty:
            pass
        except Exception as exc:
            log("UI 队列异常: %s" % exc, "ERROR")
        self.after(60, self._pump)

    # ================================================================ 热键
    def _start_hotkeys(self):
        if self.hotkeys:
            try:
                self.hotkeys.stop()
                self.hotkeys.join(timeout=0.5)
            except Exception:
                pass
        entries = [
            ("启动/暂停", normalize_hotkey(self.cfg.get("hotkeys.start_pause", "F6")), "start_pause"),
            ("停止", normalize_hotkey(self.cfg.get("hotkeys.stop", "F7")), "stop"),
            ("录制", normalize_hotkey(self.cfg.get("hotkeys.record", "F8")), "record"),
        ]
        for disp, key, _act in entries:
            if not key:
                self._toast("热键不能为空：%s" % disp, "warn")
                return
        dup = hotkey_conflicts([(disp, key) for disp, key, _act in entries])
        if dup:
            key, labels = next(iter(dup.items()))
            self._toast("热键冲突：%s 同时用于「%s」，请先改键"
                        % (key, "」「".join(labels)), "warn")
            return
        bindings = {key: (lambda a=act: self.ui_queue.put(("hotkey", a)))
                    for _disp, key, act in entries}
        self.hotkeys = HotkeyManager(bindings, on_error=lambda m: self.ui_queue.put(("toast", m, "warn")))
        self.hotkeys.start()
        if self.hotkeys.wait_ready(1.2) and self.hotkeys.failures:
            act_by_key = {key: disp for disp, key, _act in entries}
            fails = ["%s（%s）" % (k, act_by_key[k]) if k in act_by_key else k
                     for k in self.hotkeys.failures]
            log("热键冲突: %s" % ", ".join(fails), "WARN")
            self.after(400, lambda: self._prompt_hotkey_conflict(fails))

    def _prompt_hotkey_conflict(self, fails):
        try:
            text = " / ".join(fails)
            if messagebox.askyesno(
                    "热键冲突",
                    "以下热键无法注册：%s\n\n与系统或其他程序的全局热键冲突。\n"
                    "点「是」打开设置页修改热键，点「否」稍后手动处理。" % text):
                self._show_page("settings")
                self._toast("请在「全局热键」里改键后点「应用热键」重试", "info")
        except Exception:
            pass

    def _watch_dpi(self):
        try:
            cur = round(float(self.winfo_fpixels("1i")) / 96.0, 2)
            if self._last_dpi is None:
                self._last_dpi = cur
            elif abs(cur - self._last_dpi) >= 0.1:
                log("检测到 DPI 变化 %.2f -> %.2f（界面尺寸固定，不做调整）"
                    % (self._last_dpi, cur), "WARN")
                self._last_dpi = cur
        except Exception:
            pass
        self.after(3000, self._watch_dpi)

    def _handle_rule_action(self, rule_id: str, act: dict):
        try:
            self._execute_rule_action(rule_id, act or {})
        except Exception as exc:
            log("后台规则动作执行失败: %s" % exc, "ERROR")

    def _resolve_action_script(self, name: str):
        name = str(name or "").strip()
        if not name:
            return None
        p = Path(name)
        if p.is_absolute():
            return p
        if name.replace("\\", "/").startswith("scripts/"):
            return ROOT / Path(name)
        return SCRIPTS_DIR / name

    def _execute_rule_action(self, rule_id: str, act: dict):
        typ = str(act.get("type", "script")).lower()
        if typ in ("click", "long_press", "wheel", "key", "wait", "move", "drag", "text"):
            center = act.get("_center") or (0, 0)
            item = dict(act)
            item.pop("_center", None)
            self.input_queue.put((item, center))
            return
        if typ == "notify":
            self._notify_user(str(act.get("text", "") or "规则通知"))
            return
        if typ == "stop":
            if self.player.running:
                self._stop_play()
            return
        if typ != "script":
            return
        op = str(act.get("op", "run")).lower()
        target = str(act.get("target", "current")).lower()
        reason = "rule:%s" % (rule_id or "group")
        path = self._resolve_action_script(act.get("script") or act.get("path") or "")
        if target == "script" and op in ("pause", "resume", "stop"):
            if not path or not self.script_path:
                return
            try:
                if Path(self.script_path).resolve() != path.resolve():
                    return
            except Exception:
                return
        if op == "run":
            if not path or not Path(path).exists():
                key = (rule_id, str(act.get("script") or act.get("path") or ""))
                now = time.time()
                if now - self._script_warn_at.get(key, 0.0) > 5.0:
                    self._script_warn_at[key] = now
                    self._toast("后台规则：脚本不存在 %s"
                                % (act.get("script") or ""), "warn")
                return
            if self.player.running:
                same = False
                try:
                    same = bool(self.script_path) and Path(self.script_path).resolve() == path.resolve()
                except Exception:
                    same = False
                if same:
                    if self.player.paused:
                        self._resume_for_rule(reason)
                    return
                if str(act.get("busy", "switch")).lower() == "ignore":
                    return
            self.start_script_by_path(path, source="规则")
        elif op == "pause":
            self._pause_for_rule(reason)
        elif op == "resume":
            self._resume_for_rule(reason)
        elif op == "stop":
            if self.player.running:
                self._stop_play()
            elif self.recording:
                self._stop_record()

    def _handle_hotkey(self, action: str):
        if action == "start_pause":
            if self.player.running:
                self._pause_play()
            else:
                self._toggle_play()
        elif action == "stop":
            if self.player.running:
                self._stop_play()
            elif self.recording:
                self._stop_record()
        elif action == "record":
            self._toggle_record()

    # ================================================================ 会话保活
    def _startup(self):
        self._start_hotkeys()
        if not self.rules_engine.is_alive():
            self.rules_engine.start()
        if bool(self.cfg.get("vdisplay_parsec", False)):
            self.parsec_status.configure(text="状态：正在启动虚拟屏…", text_color=theme.WARN)
            threading.Thread(target=self._enable_parsec_bg, daemon=True).start()
        else:
            self._refresh_parsec_status()
        if bool(self.cfg.get("vdisplay", False)):
            self.vd_status.configure(text="状态：正在开启会话保活…", text_color=theme.WARN)
            threading.Thread(target=self._enable_vd_bg, daemon=True).start()
        else:
            self._refresh_vd_status()

    def _enable_vd_bg(self):
        err = keepalive.enable()
        self.ui_queue.put(("vd", err or ""))

    def _disable_vd_bg(self):
        keepalive.disable()
        self.ui_queue.put(("vd", "__off__"))

    def _on_vd_result(self, payload: str):
        if payload == "__off__":
            self.vd_status.configure(text="状态：未启用（内置会话保活已停止）",
                                     text_color=theme.MUTED)
            return
        if payload:
            self.vd_status.configure(text="状态：启动失败：%s" % payload,
                                     text_color=theme.WARN)
        else:
            st = keepalive.status()
            self.vd_status.configure(text="状态：内置会话保活运行中 · 已微动 %d 次"
                                     % st.get("nudges", 0), text_color=theme.OK)
        self.after(1500, self._refresh_vd_status)

    # ================================================================ 退出
    def _shutdown_backends(self):
        """停止热键 / 录屏 / 回放 / 保活，不销毁窗口。重启和退出共用。"""
        try:
            if self.live_stop:
                self.live_stop.set()
            self.player.shutdown()
            self.recorder.stop()
            if self.hotkeys:
                self.hotkeys.stop()
                self.hotkeys = None
            try:
                self.rules_engine.stop()
            except Exception:
                pass
            try:
                self.input_stop.set()
                self.input_queue.put(None)
            except Exception:
                pass
            if keepalive.enabled:
                keepalive.disable()
            else:
                set_awake(False)
            try:
                from core import vdisplay_parsec as vp
                if vp.virtual.enabled or vp.active_count() > 0:
                    vp.ensure_off(wait_ms=1200)
            except Exception:
                pass
        except Exception:
            log("后台清理异常: %s" % traceback.format_exc(), "ERROR")

    def on_close(self):
        if getattr(self, "_editing", False):
            ans = messagebox.askyesnocancel(
                "未保存的修改",
                "事件列表还有未保存的修改。\n\n是：保存后继续\n否：放弃修改并继续\n取消：返回继续编辑")
            if ans is None:
                self._quitting = False
                return
            if ans:
                if not self._save_edit(silent=True):
                    self._quitting = False
                    return
            else:
                self._discard_edit()

        self._save_window_rect()
        if not self._quitting:
            if self.tray is None:
                # 托盘不可用时直接退出，避免窗口消失后无法恢复
                self._quitting = True
            else:
                # 关闭按钮 -> 最小化到托盘，不退出
                try:
                    self.withdraw()
                except Exception:
                    pass
                return

        self._shutdown_backends()
        try:
            if not getattr(self, "_editing", False):
                self._save_script(silent=True) if self.events and self.script_path else None
            self._sync_profile()
        except Exception:
            log("退出保存异常: %s" % traceback.format_exc(), "ERROR")
        try:
            if self.hud is not None:
                self.hud.destroy()
        except Exception:
            pass
        try:
            if self.tray is not None:
                self.tray.stop()
                self.tray = None
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    def report_callback_exception(self, exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        log("UI 异常: %s" % text, "ERROR")
        try:
            (LOGS_DIR / "ui_error.log").write_text(text, encoding="utf-8")
        except Exception:
            pass
        try:
            self.status_label.configure(text="界面异常：%s（详见 logs/ui_error.log）" % val,
                                        text_color=theme.DANGER)
        except Exception:
            pass

    def _move_to_monitor2(self):
        """把测试窗口放到第二台显示器，避免占用主显示器。"""
        try:
            import ctypes
            import mss
            with mss.mss() as sct:
                mons = sct.monitors
            mon = mons[2] if len(mons) > 2 else mons[1]
            w = min(1200, int(mon["width"]))
            h = min(860, int(mon["height"]))
            x = int(mon["left"])
            y = int(mon["top"]) + (int(mon["height"]) - h) // 2
            self.geometry("%dx%d+0+0" % (w, h))
            self.update_idletasks()
            u32 = ctypes.windll.user32
            hwnd = int(self.winfo_id())
            parent = u32.GetParent(hwnd)
            target = parent or hwnd
            u32.SetWindowPos(target, 0, x, y, w, h, 0x0004)
            self.update()
        except Exception:
            pass

    def _ui_smoke(self):
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.geometry("1200x860+40+40")
            self.focus_force()
            self.update()
            self._move_to_monitor2()
            self.update()
        except Exception:
            pass
        self._smoke_pages = ["record", "monitor", "settings"]
        self.after(600, self._ui_smoke_step)

    def _ui_smoke_step(self):
        if not self._smoke_pages:
            self.after(400, self._quit_app)
            return
        page = self._smoke_pages.pop(0)
        try:
            self._show_page(page)
            self.update()
        except Exception:
            pass
        self.after(500, lambda p=page: self._ui_smoke_capture(p))

    def _ui_smoke_capture(self, page: str):
        try:
            import mss
            from PIL import Image
            self.update_idletasks()
            x, y = self.winfo_rootx(), self.winfo_rooty()
            w, h = self.winfo_width(), self.winfo_height()
            with mss.mss() as sct:
                shot = sct.grab({"left": int(x), "top": int(y),
                                 "width": int(w), "height": int(h)})
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                img.save(str(LOGS_DIR / ("ui_smoke_%s.png" % page)))
            log("UI smoke screenshot saved %s %dx%d at %d,%d" % (page, w, h, x, y))
            if self.hud is not None and self.hud.winfo_viewable():
                hx, hy = self.hud.winfo_rootx(), self.hud.winfo_rooty()
                hw, hh = self.hud.winfo_width(), self.hud.winfo_height()
                with mss.mss() as sct:
                    shot = sct.grab({"left": int(hx - 8), "top": int(hy - 8),
                                     "width": int(hw + 16), "height": int(hh + 16)})
                    himg = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    himg.save(str(LOGS_DIR / "hud_smoke.png"))
        except Exception as exc:
            log("UI smoke 截图失败(%s): %s" % (page, exc), "WARN")
        self.after(300, self._ui_smoke_step)
