"""日志：文件 + 控制台，线程安全。"""
from __future__ import annotations

import datetime
import threading

from config import LOGS_DIR

_lock = threading.Lock()
_path = LOGS_DIR / ("app_%s.log" % datetime.datetime.now().strftime("%Y%m%d"))


def log(msg: str, level: str = "INFO") -> None:
    line = "%s [%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3], level, msg)
    with _lock:
        try:
            with open(_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
    try:
        print(line)
    except Exception:
        pass

