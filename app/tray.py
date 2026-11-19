"""系统托盘图标：关闭窗口时最小化到托盘，双击恢复。"""
from __future__ import annotations

import threading
from pathlib import Path

from PIL import Image

from core.log import log

try:
    import pystray
    from pystray import Menu, MenuItem
except Exception as exc:  # 打包缺依赖时不影响主程序
    pystray = None
    Menu = MenuItem = None
    _IMPORT_ERROR = str(exc)
else:
    _IMPORT_ERROR = ""


class TrayIcon:
    def __init__(self, app, icon_path: Path | str):
        self.app = app
        self.icon_path = Path(icon_path)
        self._icon = None
        self._thread: threading.Thread | None = None

    @property
    def available(self) -> bool:
        return pystray is not None

    def start(self) -> bool:
        if pystray is None:
            log("托盘不可用：%s" % _IMPORT_ERROR, "WARN")
            return False
        try:
            image = Image.open(self.icon_path).convert("RGBA")
            image.thumbnail((64, 64), Image.LANCZOS)
            menu = Menu(
                MenuItem("显示主界面", lambda: self._post("show"), default=True),
                MenuItem("显示 / 隐藏悬浮窗", lambda: self._post("toggle_hud")),
                MenuItem("取消悬浮窗鼠标穿透", lambda: self._post("hud_click_through_off")),
                Menu.SEPARATOR,
                MenuItem("退出 WizFlow", lambda: self._post("quit")),
            )
            self._icon = pystray.Icon("WizFlow", image, "WizFlow", menu)
            self._thread = threading.Thread(target=self._run, name="wizflow-tray", daemon=True)
            self._thread.start()
            log("托盘图标已启动")
            return True
        except Exception as exc:
            log("托盘启动失败：%s" % exc, "WARN")
            return False

    def _run(self) -> None:
        try:
            self._icon.run()
        except Exception as exc:
            log("托盘线程异常：%s" % exc, "WARN")

    def _post(self, action: str) -> None:
        try:
            self.app.ui_queue.put(("tray", action))
        except Exception:
            pass

    def stop(self) -> None:
        try:
            if self._icon is not None:
                self._icon.stop()
        except Exception:
            pass
        self._icon = None
