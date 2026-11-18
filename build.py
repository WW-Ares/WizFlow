"""PyInstaller 打包脚本。

    python build.py           单文件
    python build.py --dir     文件夹模式（启动更快、误报更少）
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    mode = "--onedir" if "--dir" in sys.argv else "--onefile"
    args = [
        sys.executable, "-m", "PyInstaller", mode,
        "--noconfirm", "--clean", "--windowed",
        "--name", "WizFlow",
        "--icon", str(ROOT / "assets" / "logo.ico"),
        "--add-data", "%s;assets" % (ROOT / "assets"),
        "--collect-all", "customtkinter",
        "--collect-all", "cv2",
        "--collect-all", "numpy",
        "--hidden-import", "pynput.keyboard._win32",
        "--hidden-import", "pynput.mouse._win32",
        "--hidden-import", "PIL._tkinter_finder",
        str(ROOT / "main.py"),
    ]
    print(" ".join(args))
    rc = subprocess.call(args, cwd=str(ROOT))
    if rc == 0:
        if mode == "--onedir":
            runnable = ROOT / "dist" / "WizFlow" / "WizFlow.exe"
            # 中间产物 exe 没有 _internal，双击会报 python313.dll 找不到，构建后删掉避免误启动
            intermediate = ROOT / "build" / "WizFlow" / "WizFlow.exe"
            try:
                if intermediate.exists():
                    intermediate.unlink()
            except Exception:
                pass
        else:
            runnable = ROOT / "dist" / "WizFlow.exe"
        print("=" * 60)
        print("RUN THIS:", runnable)
        print("=" * 60)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
