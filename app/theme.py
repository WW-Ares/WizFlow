"""配色与字体。"""
BG = "#0a0f1e"
BG2 = "#0f172a"
CARD = "#121c33"
CARD2 = "#182642"
LINE = "#22304f"
TEXT = "#e6edf7"
MUTED = "#8fa3c4"
ACCENT = "#38bdf8"
ACCENT2 = "#22d3ee"
OK = "#22c55e"
WARN = "#f59e0b"
DANGER = "#ef4444"
PURPLE = "#a78bfa"

import os

FONT = "Microsoft YaHei UI"
MONO = "Consolas"
FONT_SCALE = float(os.environ.get("WIZFLOW_UI_SCALE", "1.30"))


def fs(size):
    return max(8, int(round(float(size) * FONT_SCALE)))

