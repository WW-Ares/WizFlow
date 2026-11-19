"""监控命中后的动作执行器。

动作是普通 dict，便于直接写进脚本 JSON。所有键鼠动作都通过 Player 的
按压状态集合登记，停止时由 Player._release_all 统一释放。
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from pathlib import Path

from .hotkeys import parse_hotkey
from .log import log
from .screen import MatchResult
from .script import ScriptDoc
from .winapi import (cursor_pos, key_down, key_up, mouse_down, mouse_up,
                     mouse_wheel, set_cursor_pos)


@dataclass
class ActionContext:
    rule_name: str
    match: MatchResult
    region: tuple
    frame: object
    script_dir: Path
    rule: dict


def _num(v, default=0):
    try:
        return int(float(v))
    except Exception:
        return default


def _offset(action):
    off = action.get("offset") or [0, 0]
    try:
        return int(off[0]), int(off[1])
    except Exception:
        return 0, 0


def resolve_position(action, match: MatchResult) -> tuple[int, int]:
    mode = str(action.get("position", "center"))
    ox, oy = _offset(action)
    if mode == "absolute":
        p = action.get("point") or [0, 0]
        return _num(p[0]) + ox, _num(p[1]) + oy
    if mode == "rect_random":
        x, y, w, h = match.rect
        return x + random.randint(0, max(0, w - 1)) + ox, y + random.randint(0, max(0, h - 1)) + oy
    if mode == "top_left":
        return match.x + ox, match.y + oy
    cx, cy = match.center
    return cx + ox, cy + oy


def _do_click(player, action, match):
    button = str(action.get("button", "left")).lower()
    clicks = max(1, _num(action.get("clicks", 1), 1))
    interval = max(0, _num(action.get("interval_ms", 70), 70))
    hold = max(1, _num(action.get("hold_ms", 40), 40))
    tx, ty = resolve_position(action, match)
    if not player.dry_run:
        set_cursor_pos(tx, ty)
    for i in range(clicks):
        player._pressed_buttons.add(button)
        if not player.dry_run:
            mouse_down(button)
        if not player._wait(hold):
            return "stopped"
        player._pressed_buttons.discard(button)
        if not player.dry_run:
            mouse_up(button)
        if i < clicks - 1 and not player._wait(interval):
            return "stopped"
    return "ok"


def _do_wheel(player, action, match):
    direction = str(action.get("direction", "up")).lower()
    amount = max(1, _num(action.get("amount", 3), 3))
    interval = max(0, _num(action.get("interval_ms", 60), 60))
    delta = 120 if direction == "up" else -120
    tx, ty = resolve_position(action, match)
    if not player.dry_run:
        set_cursor_pos(tx, ty)
    for _i in range(amount):
        if player._stop.is_set():
            return "stopped"
        if player._paused.is_set():
            while player._paused.is_set() and not player._stop.is_set():
                time.sleep(0.05)
            if player._stop.is_set():
                return "stopped"
        if not player.dry_run:
            mouse_wheel(delta)
        if interval and not player._wait(interval):
            return "stopped"
    return "ok"


def _do_long_press(player, action, match):
    button = str(action.get("button", "left")).lower()
    hold = max(1, _num(action.get("hold_ms", 1000), 1000))
    tx, ty = resolve_position(action, match)
    if not player.dry_run:
        set_cursor_pos(tx, ty)
        player._pressed_buttons.add(button)
        mouse_down(button)
    ok = player._wait(hold)
    if not player.dry_run:
        player._pressed_buttons.discard(button)
        mouse_up(button)
    return "ok" if ok else "stopped"


def _do_move(player, action, match):
    tx, ty = resolve_position(action, match)
    player._move((tx, ty))
    return "ok"


def _do_drag(player, action, match):
    start = resolve_position(action, match)
    mode = str(action.get("to_mode", "offset"))
    ox, oy = _offset(action)
    if mode == "absolute":
        p = action.get("to") or [0, 0]
        end = (_num(p[0]), _num(p[1]))
    else:
        cx, cy = match.center
        end = (cx + ox, cy + oy)
    duration = max(10, _num(action.get("duration_ms", 300), 300))
    steps = max(2, min(120, _num(action.get("steps", 20), 20)))
    button = str(action.get("button", "left")).lower()
    if not player.dry_run:
        set_cursor_pos(start[0], start[1])
        player._pressed_buttons.add(button)
        mouse_down(button)
    for i in range(1, steps + 1):
        if player._stop.is_set():
            break
        if player._paused.is_set():
            time.sleep(0.05)
            continue
        t = i / steps
        x = int(start[0] + (end[0] - start[0]) * t)
        y = int(start[1] + (end[1] - start[1]) * t)
        if not player.dry_run:
            set_cursor_pos(x, y)
        time.sleep(max(0.005, duration / 1000.0 / steps))
    if not player.dry_run:
        player._pressed_buttons.discard(button)
        mouse_up(button)
    return "ok" if not player._stop.is_set() else "stopped"


def _do_key(player, action):
    keys = str(action.get("keys", "") or "").strip()
    if not keys:
        return "ok"
    hold = max(1, _num(action.get("hold_ms", 40), 40))
    parsed = parse_hotkey(keys)
    if not parsed:
        log("无效按键: %s" % keys, "WARN")
        return "bad_key"
    mods, vk = parsed
    mod_keys = []
    if mods & 0x0002:
        mod_keys.append(0x11)
    if mods & 0x0001:
        mod_keys.append(0x12)
    if mods & 0x0004:
        mod_keys.append(0x10)
    if mods & 0x0008:
        mod_keys.append(0x5B)
    if player.dry_run:
        return "ok"
    try:
        for mk in mod_keys:
            player._pressed_keys.add((mk, False))
            key_down(mk, False)
        player._pressed_keys.add((vk, False))
        key_down(vk, False)
        ok = player._wait(hold)
        key_up(vk, False)
        player._pressed_keys.discard((vk, False))
        for mk in reversed(mod_keys):
            key_up(mk, False)
            player._pressed_keys.discard((mk, False))
        return "ok" if ok else "stopped"
    except Exception as exc:
        log("按键动作失败: %s" % exc, "WARN")
        return "error"


def _do_script(player, action, ctx):
    op = str(action.get("op", "run") or "run").lower()
    if op == "stop":
        player.stop()
        return "stopped"
    if op in ("pause", "resume"):
        # 脚本内节点不能控制自身，忽略
        return "ok"
    name = str(action.get("path", "") or action.get("script", "") or "")
    if not name:
        return "ok"
    sp = player._resolve_path(name)
    if sp is None:
        log("动作脚本不存在: %s" % name, "WARN")
        return "missing_script"
    try:
        doc = ScriptDoc.load(sp)
    except Exception as exc:
        log("动作脚本加载失败: %s" % exc, "WARN")
        return "error"
    try:
        player.on_status("运行子脚本: %s" % sp.name, None)
    except Exception:
        pass
    anchor = action.get("anchor")
    if isinstance(anchor, (list, tuple)) and len(anchor) == 2:
        dx = ctx.match.center[0] - int(anchor[0])
        dy = ctx.match.center[1] - int(anchor[1])
        events = player._shift_events(doc.events, dx, dy)
    else:
        events = doc.events
    if action.get("wait", True):
        if not player._exec_events(events, ctx.match.center):
            return "stopped"
    else:
        import threading
        threading.Thread(target=player._exec_events, args=(events, ctx.match.center),
                         daemon=True).start()
    return "ok"


def execute_actions(player, actions, ctx: ActionContext) -> str:
    for action in actions or []:
        if not isinstance(action, dict):
            continue
        if player._stop.is_set():
            return "stopped"
        delay = max(0, _num(action.get("delay_ms", 0), 0))
        if delay and not player._wait(delay):
            return "stopped"
        typ = str(action.get("type", "")).lower()
        try:
            if typ == "click":
                r = _do_click(player, action, ctx.match)
            elif typ == "wheel":
                r = _do_wheel(player, action, ctx.match)
            elif typ == "long_press":
                r = _do_long_press(player, action, ctx.match)
            elif typ == "move":
                r = _do_move(player, action, ctx.match)
            elif typ == "drag":
                r = _do_drag(player, action, ctx.match)
            elif typ == "key":
                r = _do_key(player, action)
            elif typ == "text":
                player._type_text(str(action.get("text", "")))
                r = "ok"
            elif typ == "script":
                r = _do_script(player, action, ctx)
            elif typ == "wait":
                r = "ok" if player._wait(_num(action.get("ms", 200), 200)) else "stopped"
            elif typ == "notify":
                player.on_status("规则 %s: %s" % (ctx.rule_name, action.get("text", "")), None)
                log("notify rule=%s text=%s" % (ctx.rule_name, action.get("text", "")))
                r = "ok"
            elif typ == "stop":
                scope = str(action.get("scope", "monitor"))
                if scope == "script":
                    player._stop.set()
                return "stop_" + scope
            else:
                log("未知动作类型: %s" % typ, "WARN")
                r = "unknown"
            if r in ("stopped",):
                return r
        except Exception as exc:
            log("动作执行失败 %s: %s" % (typ, exc), "ERROR")
    return "ok"

def action_summary(a: dict) -> str:
    t = str(a.get("type", "")).lower()
    if t in ("click", "long_press"):
        b = {"left": "左键", "right": "右键", "middle": "滚轮", "x1": "X1", "x2": "X2"}.get(
            str(a.get("button", "left")), "左键")
        clicks = max(1, _num(a.get("clicks", 1), 1))
        style = "长按" if t == "long_press" else {1: "单击", 2: "双击", 3: "三击"}.get(clicks, "单击")
        pos = {"center": "图标中心", "rect_random": "图标内随机", "top_left": "左上角",
               "absolute": "固定位置", "follow": "偏移跟随"}.get(
            str(a.get("position", "center")), "图标中心")
        return "鼠标 %s%s · %s" % (b, style, pos)
    if t == "wheel":
        direction = "下滚" if str(a.get("direction", "up")) == "down" else "上滚"
        return "鼠标 %s %s格" % (direction, _num(a.get("amount", 3), 3))
    if t == "key":
        return "键盘 %s" % (a.get("keys", "") or "未设置")
    if t == "script":
        op = {"run": "运行", "pause": "暂停", "resume": "继续", "stop": "停止"}.get(
            str(a.get("op", "run")), "运行")
        name = str(a.get("path") or a.get("script") or "").replace("\\", "/").rsplit("/", 1)[-1]
        return "脚本 %s %s" % (op, name or "当前脚本")
    if t == "wait":
        return "等待 %dms" % _num(a.get("ms", 200), 200)
    if t == "notify":
        return "通知 %s" % (a.get("text", "") or "")
    if t == "stop":
        return "停止"
    if t == "text":
        return "输入文本"
    if t == "move":
        return "移动"
    if t == "drag":
        return "拖拽"
    return t or "动作"


def execute_input(action: dict, center=None, stop_event=None, dry_run: bool = False) -> str:
    """独立执行鼠标/键盘/等待动作（后台规则用），返回 ok/stopped/script。"""
    t = str(action.get("type", "")).lower()
    if t == "script":
        return "script"
    if t == "wait":
        return "ok" if _input_wait(_num(action.get("ms", 200), 200), stop_event) else "stopped"
    if t in ("click", "long_press", "wheel", "move", "drag", "key"):
        return _input_action(action, center, stop_event, dry_run)
    if t == "text":
        type_text(str(action.get("text", "") or ""))
        return "ok"
    if t == "notify":
        log("规则通知: %s" % (action.get("text", "") or ""))
        return "ok"
    return "ok"


def _input_wait(ms: int, stop_event=None) -> bool:
    end = time.time() + max(0, int(ms)) / 1000.0
    while time.time() < end:
        if stop_event is not None and stop_event.is_set():
            return False
        time.sleep(min(0.02, max(0.0, end - time.time())))
    return not (stop_event is not None and stop_event.is_set())


def _input_point(action: dict, center):
    pos = str(action.get("position", "center")).lower()
    off = action.get("offset") or [0, 0]
    try:
        ox, oy = int(off[0]), int(off[1])
    except Exception:
        ox, oy = 0, 0
    if pos == "absolute":
        p = action.get("point") or [0, 0]
        return _num(p[0]) + ox, _num(p[1]) + oy
    if not center:
        return _num(action.get("point_x", 0)) + ox, _num(action.get("point_y", 0)) + oy
    cx, cy = int(center[0]), int(center[1])
    if pos == "follow":
        return cx + ox, cy + oy
    if pos == "rect_random":
        rx = max(1, _num(action.get("rect_w", 10), 10))
        ry = max(1, _num(action.get("rect_h", 10), 10))
        return cx + random.randint(-rx // 2, rx // 2), cy + random.randint(-ry // 2, ry // 2)
    if pos == "top_left":
        return cx + ox, cy + oy
    rx = abs(_num(action.get("random_x", 0), 0))
    ry = abs(_num(action.get("random_y", 0), 0))
    x, y = cx + ox, cy + oy
    if rx:
        x += random.randint(-rx, rx)
    if ry:
        y += random.randint(-ry, ry)
    return x, y


def _input_action(action, center, stop_event, dry_run) -> str:
    t = str(action.get("type", "")).lower()
    if t == "key":
        keys = str(action.get("keys", "") or "").strip()
        if not keys:
            return "ok"
        hold = max(1, _num(action.get("hold_ms", 40), 40))
        parsed = parse_hotkey(keys)
        if not parsed:
            return "bad_key"
        mods, vk = parsed
        mod_keys = []
        if mods & 0x0002:
            mod_keys.append(0x11)
        if mods & 0x0001:
            mod_keys.append(0x12)
        if mods & 0x0004:
            mod_keys.append(0x10)
        if mods & 0x0008:
            mod_keys.append(0x5B)
        if dry_run:
            return "ok"
        try:
            for mk in mod_keys:
                key_down(mk, False)
            key_down(vk, False)
            ok = _input_wait(hold, stop_event)
            key_up(vk, False)
            for mk in reversed(mod_keys):
                key_up(mk, False)
            return "ok" if ok else "stopped"
        except Exception as exc:
            log("后台按键失败: %s" % exc, "WARN")
            return "error"
    x, y = _input_point(action, center)
    if not dry_run:
        set_cursor_pos(x, y)
    if t == "move":
        return "ok"
    if t == "drag":
        mode = str(action.get("to_mode", "offset"))
        if mode == "absolute":
            p = action.get("to") or [0, 0]
            end = (_num(p[0]), _num(p[1]))
        else:
            end = (x + _num(action.get("offset_x", 0)),
                   y + _num(action.get("offset_y", 0)))
        duration = max(10, _num(action.get("duration_ms", 300), 300))
        steps = max(2, min(120, _num(action.get("steps", 20), 20)))
        button = str(action.get("button", "left")).lower()
        if not dry_run:
            mouse_down(button)
        for i in range(1, steps + 1):
            if stop_event is not None and stop_event.is_set():
                if not dry_run:
                    mouse_up(button)
                return "stopped"
            tt = i / steps
            px = int(x + (end[0] - x) * tt)
            py = int(y + (end[1] - y) * tt)
            if not dry_run:
                set_cursor_pos(px, py)
            time.sleep(max(0.005, duration / 1000.0 / steps))
        if not dry_run:
            mouse_up(button)
        return "ok"
    if t == "wheel":
        direction = str(action.get("direction", "up")).lower()
        delta = 120 if direction == "up" else -120
        amount = max(1, _num(action.get("amount", 3), 3))
        interval = max(0, _num(action.get("interval_ms", 60), 60))
        for _ in range(amount):
            if stop_event is not None and stop_event.is_set():
                return "stopped"
            if not dry_run:
                mouse_wheel(delta)
            if interval and not _input_wait(interval, stop_event):
                return "stopped"
        return "ok"
    button = str(action.get("button", "left")).lower()
    if t == "long_press":
        hold = max(1, _num(action.get("hold_ms", 1000), 1000))
        clicks = 1
    else:
        clicks = max(1, _num(action.get("clicks", 1), 1))
        hold = max(1, _num(action.get("hold_ms", 40), 40))
    interval = max(0, _num(action.get("interval_ms", 70), 70))
    for i in range(clicks):
        if stop_event is not None and stop_event.is_set():
            return "stopped"
        if not dry_run:
            mouse_down(button)
        ok = _input_wait(hold, stop_event)
        if not dry_run:
            mouse_up(button)
        if not ok:
            return "stopped"
        if i + 1 < clicks and interval and not _input_wait(interval, stop_event):
            return "stopped"
    return "ok"

def type_text(text: str) -> None:
    """通过 SendInput 发送 Unicode 文本，支持中文和表情。"""
    if not text:
        return
    import ctypes
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class INPUT(ctypes.Structure):
        class _I(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]
        _anonymous_ = ("i",)
        _fields_ = [("type", wintypes.DWORD), ("i", _I)]

    units = text.encode("utf-16-le")
    seq = [int.from_bytes(units[i:i + 2], "little") for i in range(0, len(units), 2)]
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    for unit in seq:
        down = INPUT(type=1, i=INPUT._I(ki=KEYBDINPUT(0, unit, 0x0004, 0, 0)))
        up = INPUT(type=1, i=INPUT._I(ki=KEYBDINPUT(0, unit, 0x0004 | 0x0002, 0, 0)))
        u32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
        u32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
        time.sleep(0.005)

