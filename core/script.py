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



_BUTTON_CN = {"left": "左键", "right": "右键", "middle": "中键", "x1": "侧键1", "x2": "侧键2"}

_KEY_CN = {
    "space": "空格", "enter": "回车", "return": "回车", "backspace": "退格",
    "tab": "Tab", "delete": "删除", "insert": "插入", "home": "Home", "end": "End",
    "pageup": "PageUp", "pagedown": "PageDown", "up": "↑", "down": "↓",
    "left": "←", "right": "→", "esc": "Esc", "escape": "Esc", "capslock": "CapsLock",
    "numlock": "NumLock", "scrolllock": "ScrollLock", "printscreen": "PrintScreen",
    "shift": "Shift", "ctrl": "Ctrl", "alt": "Alt", "win": "Win", "apps": "菜单键",
    "pause": "Pause",
}

_KEY_VK_CN = {
    0x08: "退格", 0x09: "Tab", 0x0D: "回车", 0x13: "Pause", 0x14: "CapsLock",
    0x1B: "Esc", 0x20: "空格", 0x21: "PageUp", 0x22: "PageDown", 0x23: "End",
    0x24: "Home", 0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓", 0x2C: "PrintScreen",
    0x2D: "插入", 0x2E: "删除", 0x5B: "Win", 0x5C: "Win", 0x5D: "菜单键",
    0x90: "NumLock", 0x91: "ScrollLock", 0xA0: "Shift", 0xA1: "Shift",
    0xA2: "Ctrl", 0xA3: "Ctrl", 0xA4: "Alt", 0xA5: "Alt",
}


def vk_label(vk: int, name: str = "") -> str:
    """虚拟键码 / 录制名 -> 中文按键名。"""
    try:
        vk = int(vk)
    except Exception:
        vk = 0
    n = (name or "").lower()
    if n in _KEY_CN:
        return _KEY_CN[n]
    if vk in _KEY_VK_CN:
        return _KEY_VK_CN[vk]
    if 0x70 <= vk <= 0x87:
        return "F%d" % (vk - 0x6F)
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x60 <= vk <= 0x69:
        return "小键盘%d" % (vk - 0x60)
    if len(n) == 1 and n.isalnum():
        return n.upper()
    if n.startswith("vk_") and n[3:].isdigit():
        return "键码%d" % int(n[3:])
    return n.upper() if n else "键码%d" % vk


def mouse_action_label(at: str) -> str:
    parts = (at or "").lower().split()
    if not parts or parts[0] != "mouse":
        return at or "未知"
    if "wheel" in parts:
        return "滚轮上滚" if parts[-1] == "up" else "滚轮下滚"
    if len(parts) > 1 and parts[1] == "move":
        return "鼠标移动"
    button = _BUTTON_CN.get(parts[1] if len(parts) > 1 else "left", "左键")
    phase = parts[2] if len(parts) > 2 else "move"
    return "%s%s" % (button, {"down": "按下", "up": "抬起"}.get(phase, "移动"))


def describe_event(ev: dict) -> dict:
    """统一的节点描述，供事件列表 / 编辑器使用。"""
    typ = str(ev.get("type", "event")).lower()
    delay = float(ev.get("delay", 0) or 0)
    note = str(ev.get("note", "") or "")
    out = {"kind": "事件", "action": "", "params": "", "delay": delay, "note": note}
    if typ == "monitor":
        region = ev.get("region") or [0, 0, 0, 0]
        out.update(kind="监控", action=str(ev.get("name", "单图标监控")),
                   params="[%d,%d,%d,%d] · 阈值 %.2f · %dms"
                          % (int(region[0]) if len(region) > 0 else 0,
                             int(region[1]) if len(region) > 1 else 0,
                             int(region[2]) if len(region) > 2 else 0,
                             int(region[3]) if len(region) > 3 else 0,
                             float(ev.get("threshold", 0.85)),
                             int(ev.get("poll_ms", 150) or 150)))
        return out
    if typ == "monitor_group":
        out.update(kind="监控组", action=str(ev.get("name", "多图标监控")),
                   params="%d 条规则 · %s · %dms"
                          % (len(ev.get("rules") or []),
                             {"any": "任一命中", "all": "全部命中",
                              "exclusive": "互斥", "sequence": "顺序"}.get(str(ev.get("match_mode", "any")), "任一命中"),
                             int(ev.get("poll_ms", 150) or 150)))
        return out
    if typ == "wait":
        out.update(kind="等待", action="等待", params="%d 毫秒" % int(ev.get("ms", 0) or 0))
        return out
    if typ == "script":
        out.update(kind="脚本", action="运行脚本", params=str(ev.get("path", "")))
        return out
    if typ == "sequence":
        out.update(kind="序列", action="节点序列", params="%d 个节点" % len(ev.get("events") or []))
        return out
    et = str(ev.get("event_type", "?")).upper()
    at = str(ev.get("action_type", "?"))
    action = ev.get("action")
    if et == "EM":
        out["kind"] = "鼠标"
        out["action"] = mouse_action_label(at)
        if isinstance(action, (list, tuple)) and len(action) >= 2:
            try:
                out["params"] = "X=%d  Y=%d" % (int(float(action[0])), int(float(action[1])))
            except Exception:
                out["params"] = str(action)[:60]
        return out
    if et == "EK":
        out["kind"] = "键盘"
        out["action"] = "按键按下" if at.endswith("down") else ("按键抬起" if at.endswith("up") else at)
        if isinstance(action, (list, tuple)) and len(action) >= 2:
            out["params"] = vk_label(action[0], str(action[1]))
        return out
    if et == "EX":
        out["kind"] = "输入"
        out["action"] = "文本输入"
        txt = str(action or "").replace("\n", " ")
        out["params"] = txt[:60] + ("…" if len(txt) > 60 else "")
        return out
    out.update(kind=et, action=at, params=str(action)[:60])
    return out


def event_summary(ev: dict) -> tuple[str, str, str]:
    """返回 (类型, 动作, 坐标/内容)。"""
    d = describe_event(ev)
    return d["kind"], d["action"], d["params"]
