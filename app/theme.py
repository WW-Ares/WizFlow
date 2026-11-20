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

FONT = "Microsoft YaHei UI"
MONO = "Consolas"

# 全项目唯一一套排版系数：字号与控件尺寸固定，不提供用户缩放
FONT_SCALE = 1.30
WIDGET_SCALE = 1.06


def fs(size):
    return max(8, int(round(float(size) * FONT_SCALE)))
