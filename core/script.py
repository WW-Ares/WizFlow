"""脚本读写。

兼容 KeymouseGo 的 json5 事件格式，同时扩展 type=monitor 节点。

事件：
  {"type":"event","event_type":"EM","delay":120,"action_type":"mouse left down","action":[x,y]}
  {"type":"event","event_type":"EK","delay":80,"action_type":"key down","action":[vk,"a",0]}
  {"type":"event","event_type":"EX","delay":100,"action_type":"input","action":"text"}
监控：
  {"type":"monitor","name":"...","region":[x,y,w,h],"template":"icons/x.png",
   "threshold":0.85,"poll_ms":150,"timeout_ms":8000,"multiscale":true,
   "click_sync":true,"click_button":"left","offset_x":0,"offset_y":0,"hold_ms":40,
   "on_match":[...],"on_timeout":[...]}
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from config import SCRIPTS_DIR
from .winapi import primary_size


def _strip_json5(text: str) -> str:
    """去掉 // 与 /* */ 注释、尾逗号，给没装 json5 的环境兜底。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(^|[^:])//.*?$", r"\1", text, flags=re.M)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def loads_relaxed(text: str):
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        import json5  # type: ignore
        return json5.loads(text)
    except Exception:
        return json.loads(_strip_json5(text))


def loads_file(path: str | Path):
    p = Path(path)
    last = None
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return loads_relaxed(p.read_text(encoding=enc))
        except Exception as exc:
            last = exc
    raise ValueError("脚本解析失败: %s" % last)


def _legacy_to_events(raw) -> list[dict]:
    out = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 4:
            out.append({
                "type": "event",
                "event_type": str(item[1]).upper(),
                "delay": item[0],
                "action_type": str(item[2]).lower(),
                "action": item[3],
            })
        elif isinstance(item, dict):
            out.append(item)
    return out


@dataclass
class ScriptDoc:
    events: list[dict] = field(default_factory=list)
    path: Path | None = None
    meta: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.path.name if self.path else "未命名脚本"

    @property
    def duration_ms(self) -> float:
        return sum(float(e.get("delay", 0) or 0) for e in self.events)

    @classmethod
    def load(cls, path: str | Path) -> "ScriptDoc":
        raw = loads_file(path)
        meta: dict = {}
        if isinstance(raw, dict):
            meta = {k: v for k, v in raw.items() if k not in ("scripts",)}
            events = raw.get("scripts") or []
        else:
            events = _legacy_to_events(raw)
        if not isinstance(events, list):
            raise ValueError("scripts 字段必须是数组")
        return cls(events=[e for e in events if isinstance(e, dict)], path=Path(path), meta=meta)

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("没有保存路径")
        target.parent.mkdir(parents=True, exist_ok=True)
        self.path = target
        w, h = primary_size()
        payload = {
            "format": "wizflow/1",
            "screen": [w, h],
            "meta": self.meta,
            "scripts": self.events,
        }
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        tmp.replace(target)
        return target

    def add(self, event: dict) -> None:
        self.events.append(event)


def list_scripts() -> list[Path]:
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    files = [p for p in SCRIPTS_DIR.iterdir()
             if p.is_file() and p.suffix.lower() in (".json", ".json5", ".txt")]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def new_script_path() -> Path:
    import datetime
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCRIPTS_DIR / ("rec_%s.json" % stamp)
    i = 1
    while path.exists():
        path = SCRIPTS_DIR / ("rec_%s_%d.json" % (stamp, i))
        i += 1
    return path


def normalize_action(action, anchor: tuple[int, int] | None = None,
                     use_anchor: bool = False):
    """把脚本坐标解析成绝对像素。

    - [-1,-1] 表示不移动
    - int 绝对坐标
    - float 0..1 主屏归一化
    - "0.052%" 主屏百分比（KeymouseGo 老格式）
    - anchor 模式下 action 是相对匹配中心的偏移
    """
    try:
        ax, ay = action[0], action[1]
    except Exception:
        return None
    if ax == -1 and ay == -1:
        return None

    def one(v):
        if isinstance(v, str):
            s = v.strip()
            if s.endswith("%"):
                s = s[:-1]
            return float(s)
        return v

    x, y = one(ax), one(ay)
    if isinstance(x, float) and 0.0 <= x <= 1.0:
        x = round(x * primary_size()[0])
    if isinstance(y, float) and 0.0 <= y <= 1.0:
        y = round(y * primary_size()[1])
    x, y = int(x), int(y)
    if use_anchor and anchor is not None:
        x += int(anchor[0])
        y += int(anchor[1])
    return x, y


def event_summary(ev: dict) -> tuple[str, str, str]:
    """返回 (类型, 动作, 坐标/内容)。"""
    typ = str(ev.get("type", "event"))
    if typ == "monitor":
        region = ev.get("region") or [0, 0, 0, 0]
        return ("监控", str(ev.get("name", "监控")),
                "区域 %s  阈值 %.2f" % (region, float(ev.get("threshold", 0.85))))
    et = str(ev.get("event_type", "?")).upper()
    at = str(ev.get("action_type", "?"))
    action = ev.get("action")
    if et == "EM":
        if isinstance(action, (list, tuple)) and len(action) >= 2:
            return ("鼠标", at, "%s, %s" % (action[0], action[1]))
        return ("鼠标", at, "")
    if et == "EK":
        if isinstance(action, (list, tuple)) and len(action) >= 2:
            return ("键盘", at, str(action[1]))
        return ("键盘", at, "")
    if et == "EX":
        return ("输入", at, str(action)[:60])
    return (typ, at, str(action)[:60])

