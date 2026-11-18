"""统一设置 Toplevel 窗口图标。"""
from __future__ import annotations

from config import asset_path


def apply_window_icon(win):
    try:
        ico = asset_path("logo.ico")
        if ico.exists():
            win.iconbitmap(str(ico))
    except Exception:
        pass
