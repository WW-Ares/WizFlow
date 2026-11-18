"""脚本回放 + 监控节点执行。

在 KeymouseGo 事件基础上增加 type=monitor：
轮询屏幕区域，匹配图标模板；命中后可点击匹配中心（位置同步），
也可执行 on_match 子事件（坐标为相对匹配中心的偏移）。
"""
from __future__ import annotations

import copy
import datetime
import random
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

from config import ROOT
from .hotkeys import parse_hotkey
from .log import log
from .monitor_rules import MonitorEngine
from .script import ScriptDoc, normalize_action
from .screen import MatchResult, TemplateMatcher, grab
from .winapi import (key_down, key_up, mouse_down, mouse_up, mouse_wheel,
                     set_cursor_pos)


class Player:
    def __init__(self, matcher: TemplateMatcher,
                 on_status: Callable[[str, float | None], None],
                 on_finished: Callable[[str], None]) -> None:
        self.matcher = matcher
        self.on_status = on_status
        self.on_finished = on_finished
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._lock = threading.Lock()
        self._pressed_buttons: set[str] = set()
        self._pressed_keys: set[tuple[int, bool]] = set()
        self.running = False
        self.paused = False
        self.speed = 1.0
        self.dry_run = False
        self._script_dir = ROOT
        self._progress = 0.0
        self._total = 1

    # ------------------------------------------------------------ public
    def start(self, events: list[dict], loops: int = 1, speed: float = 1.0,
              script_dir: str | Path | None = None) -> bool:
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            self._paused.clear()
            self.paused = False
            self.speed = min(10.0, max(0.1, float(speed or 1.0)))
            self._script_dir = Path(script_dir) if script_dir else ROOT
            self._progress = 0.0
            self._total = max(1, len(events))
            self.running = True
            self._thread = threading.Thread(target=self._run, args=(list(events), int(loops)),
                                            name="kmg-player", daemon=True)
            self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        self._paused.clear()

    def toggle_pause(self) -> bool:
        if not self.running:
            return False
        if self._paused.is_set():
            self._paused.clear()
            self.paused = False
        else:
            self._paused.set()
            self.paused = True
        return self.paused

    def shutdown(self) -> None:
        self.stop()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=2.0)

    # ------------------------------------------------------------ thread
    def _run(self, events: list[dict], loops: int) -> None:
        reason = "finished"
        try:
            total_loops = loops if loops and loops > 0 else 10 ** 9
            for idx in range(total_loops):
                if self._stop.is_set():
                    reason = "stopped"
                    break
                self.on_status("运行中  第 %d%s 轮" % (idx + 1, "" if loops > 0 else " 轮（无限）"), 0.0)
                if not self._exec_events(events, None, top_level=True):
                    reason = "stopped"
                    break
            if reason == "finished" and self._stop.is_set():
                reason = "stopped"
        except Exception as exc:
            reason = "error"
            log("回放异常: %s\n%s" % (exc, traceback.format_exc()), "ERROR")
            self.on_status("回放异常: %s" % exc, None)
        finally:
            self._release_all()
            with self._lock:
                self.running = False
                self.paused = False
            if reason == "finished":
                self.on_status("回放完成", 1.0)
            elif reason == "stopped":
                self.on_status("已停止", None)
            self.on_finished(reason)

    def _exec_events(self, events: list[dict], anchor: tuple[int, int] | None,
                     top_level: bool = False) -> bool:
        for i, ev in enumerate(events):
            if self._stop.is_set():
                return False
            if not isinstance(ev, dict):
                continue
            delay = float(ev.get("delay", 0) or 0)
            if delay > 0 and not self._wait(delay):
                return False
            typ = str(ev.get("type", "event")).lower()
            try:
                if typ == "event":
                    self._exec_event(ev, anchor)
                elif typ == "monitor":
                    if not self._exec_monitor(ev):
                        return False
                elif typ == "monitor_group":
                    if not self._exec_monitor_group(ev):
                        return False
                elif typ == "sequence":
                    if not self._exec_events(ev.get("events") or [], anchor):
                        return False
                else:
                    log("跳过未知事件类型: %s" % typ, "WARN")
            except Exception as exc:
                log("事件执行失败 %s: %s" % (ev, exc), "ERROR")
                self.on_status("事件执行失败: %s" % exc, None)
            if top_level:
                self._progress = min(1.0, (i + 1) / self._total)
                self.on_status("运行中  %d/%d" % (i + 1, self._total), self._progress)
        return True

    # ------------------------------------------------------------ event
    def _exec_event(self, ev: dict, anchor) -> None:
        et = str(ev.get("event_type", "")).upper()
        at = str(ev.get("action_type", "")).lower()
        action = ev.get("action")
        if et == "EM":
            pos = normalize_action(action, anchor, bool(ev.get("anchor")))
            self._mouse(at, pos)
        elif et == "EK":
            self._key(at, action)
        elif et == "EX":
            if at == "input":
                self._type_text(str(action))
        else:
            log("未知 event_type: %s" % et, "WARN")

    def _mouse(self, at: str, pos: tuple[int, int] | None) -> None:
        parts = at.split()
        if not parts or parts[0] != "mouse":
            return
        if "wheel" in parts:
            if pos:
                self._move(pos)
            delta = 120 if parts[-1] == "up" else -120
            if not self.dry_run:
                mouse_wheel(delta)
            return
        button = parts[1] if len(parts) > 1 else "left"
        phase = parts[2] if len(parts) > 2 else "move"
        if phase == "move":
            if pos:
                self._move(pos)
            return
        if pos:
            self._move(pos)
        if phase == "down":
            self._pressed_buttons.add(button)
            if not self.dry_run:
                mouse_down(button)
        elif phase == "up":
            self._pressed_buttons.discard(button)
            if not self.dry_run:
                mouse_up(button)

    def _move(self, pos: tuple[int, int]) -> None:
        if not self.dry_run:
            set_cursor_pos(pos[0], pos[1])

    def _key(self, at: str, action) -> None:
        try:
            vk = int(action[0])
            extended = bool(action[2]) if len(action) > 2 else False
        except Exception:
            return
        key = (vk, extended)
        if at == "key down":
            self._pressed_keys.add(key)
            if not self.dry_run:
                key_down(vk, extended)
        elif at == "key up":
            self._pressed_keys.discard(key)
            if not self.dry_run:
                key_up(vk, extended)

    def _type_text(self, text: str) -> None:
        if self.dry_run:
            return
        try:
            import pyperclip
            pyperclip.copy(text)
            time.sleep(0.02)
            key_down(0x11)
            key_down(0x56)
            key_up(0x56)
            key_up(0x11)
        except Exception as exc:
            log("文本输入失败: %s" % exc, "WARN")

    # ------------------------------------------------------------ monitor
    def _resolve_template(self, name: str) -> str:
        p = Path(name)
        if p.is_absolute():
            return str(p)
        candidate = self._script_dir / p
        if candidate.exists():
            return str(candidate)
        return str(ROOT / p)

    def _exec_monitor(self, ev: dict) -> bool:
        region = ev.get("region") or [0, 0, 0, 0]
        if len(region) != 4 or int(region[2]) <= 0 or int(region[3]) <= 0:
            self.on_status("监控事件区域无效: %s" % (region,), None)
            return True
        tpl = self._resolve_template(str(ev.get("template", "")))
        name = str(ev.get("name", "监控"))
        threshold = float(ev.get("threshold", 0.85))
        poll_ms = int(ev.get("poll_ms", 150))
        timeout_ms = int(ev.get("timeout_ms", 8000))
        self.on_status("监控等待: %s  (%.2f 阈值)" % (name, threshold), self._progress)
        matched = False
        best_score = 0.0
        frame = None
        res = MatchResult()
        remaining_ms = None if timeout_ms <= 0 else float(timeout_ms)
        multiscale = bool(ev.get("multiscale", True))
        rgn = [int(v) for v in region]
        while not self._stop.is_set():
            if self._paused.is_set():
                time.sleep(0.05)
                continue
            t0 = time.perf_counter()
            frame = grab(rgn)
            res = self.matcher.find(frame, tpl, threshold, multiscale)
            res.x += rgn[0]
            res.y += rgn[1]
            best_score = max(best_score, res.score)
            if res.score >= threshold:
                matched = True
                break
            cost_ms = (time.perf_counter() - t0) * 1000.0
            if remaining_ms is not None:
                remaining_ms -= cost_ms
                if remaining_ms <= 0:
                    break
            if not self._wait(poll_ms):
                break
        if self._stop.is_set():
            return False
        if matched:
            cx, cy = res.center
            ox = int(ev.get("offset_x", 0))
            oy = int(ev.get("offset_y", 0))
            tx, ty = cx + ox, cy + oy
            if "hit_action" in ev:
                hit_action = str(ev.get("hit_action", "mouse"))
            else:
                hit_action = "mouse" if bool(ev.get("click_sync", True)) else "script"
            if hit_action == "mouse":
                mode = str(ev.get("click_mode", "follow"))
                if "hit_action" not in ev:
                    mode = "follow"
                if mode == "fixed":
                    tx = int(ev.get("fixed_x", 0))
                    ty = int(ev.get("fixed_y", 0))
                elif mode == "follow":
                    tx = cx + ox
                    ty = cy + oy
                else:
                    tx, ty = cx, cy
                rx = abs(int(ev.get("random_x", 0)))
                ry = abs(int(ev.get("random_y", 0)))
                if rx:
                    tx += random.randint(-rx, rx)
                if ry:
                    ty += random.randint(-ry, ry)
                button = str(ev.get("click_button", "left"))
                style = str(ev.get("click_style", "single"))
                hold = max(1, int(ev.get("hold_ms", 40)))
                self.on_status("监控命中: %s  score=%.3f  %s  @(%d,%d)"
                               % (name, res.score, style, tx, ty), self._progress)
                log("monitor hit name=%s score=%.3f action=%s at=%d,%d"
                    % (name, res.score, style, tx, ty))
                if self.dry_run:
                    self._move((tx, ty))
                else:
                    set_cursor_pos(tx, ty)
                    if style == "double":
                        for i in range(2):
                            mouse_down(button)
                            time.sleep(max(1, hold) / 1000.0)
                            mouse_up(button)
                            if i == 0:
                                time.sleep(0.07)
                    elif style == "long":
                        mouse_down(button)
                        time.sleep(max(1000, hold) / 1000.0)
                        mouse_up(button)
                    else:
                        mouse_down(button)
                        time.sleep(max(1, hold) / 1000.0)
                        mouse_up(button)
            elif hit_action == "key":
                combo = str(ev.get("key_combo", "") or "")
                hold = max(1, int(ev.get("key_hold_ms", 40)))
                self.on_status("监控命中: %s  score=%.3f  按键 %s"
                               % (name, res.score, combo), self._progress)
                log("monitor hit name=%s score=%.3f action=key combo=%s"
                    % (name, res.score, combo))
                if not self._press_key_combo(combo, hold):
                    return False
            else:
                self.on_status("监控命中: %s  score=%.3f  执行脚本" % (name, res.score),
                               self._progress)
                log("monitor hit name=%s score=%.3f action=script" % (name, res.score))
            nested = ev.get("on_match") or []
            if nested:
                if not self._exec_events(nested, (cx, cy)):
                    return False
            run_script = str(ev.get("run_script", "") or "")
            if run_script and (hit_action == "script" or "hit_action" not in ev):
                sp = self._resolve_path(run_script)
                if sp is None:
                    self.on_status("同步脚本不存在: %s" % run_script, self._progress)
                else:
                    try:
                        doc = ScriptDoc.load(sp)
                        anchor = ev.get("script_anchor") or []
                        if len(anchor) == 2:
                            dx, dy = cx - int(anchor[0]), cy - int(anchor[1])
                            events = self._shift_events(doc.events, dx, dy)
                            self.on_status("运行同步脚本: %s  偏移(%d,%d)" % (sp.name, dx, dy),
                                           self._progress)
                        else:
                            events = doc.events
                            self.on_status("运行脚本: %s" % sp.name, self._progress)
                        if not self._exec_events(events, (cx, cy)):
                            return False
                    except Exception as exc:
                        self.on_status("同步脚本执行失败: %s" % exc, None)
        else:
            self.on_status("监控超时: %s  best=%.3f" % (name, best_score), self._progress)
            nested = ev.get("on_timeout") or []
            if nested:
                if not self._exec_events(nested, None):
                    return False
        return True

    def _press_key_combo(self, combo: str, hold_ms: int = 40) -> bool:
        combo = (combo or "").strip()
        if not combo:
            return True
        parsed = parse_hotkey(combo)
        if not parsed:
            self.on_status("无效按键: %s" % combo, None)
            return True
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
        if self.dry_run:
            return True
        try:
            for mk in mod_keys:
                self._pressed_keys.add((mk, False))
                key_down(mk, False)
            self._pressed_keys.add((vk, False))
            key_down(vk, False)
            ok = self._wait(hold_ms)
            key_up(vk, False)
            self._pressed_keys.discard((vk, False))
            for mk in reversed(mod_keys):
                key_up(mk, False)
                self._pressed_keys.discard((mk, False))
            return ok
        except Exception as exc:
            log("按键执行失败: %s" % exc, "WARN")
            return True

    def _exec_monitor_group(self, ev: dict) -> bool:
        engine = MonitorEngine(self.matcher)
        try:
            result = engine.run(ev, self, self._script_dir, self._stop, self._paused,
                                self.on_status)
        except Exception as exc:
            log("多图标监控异常: %s | %s" % (exc, traceback.format_exc()), "ERROR")
            self.on_status("多图标监控异常: %s" % exc, None)
            return True
        if result == "stopped":
            return False
        if result == "stop_script":
            self._stop.set()
            return False
        return True

    def _resolve_path(self, name: str) -> Path | None:
        p = Path(name)
        if p.is_absolute():
            return p if p.exists() else None
        for base in (self._script_dir, ROOT):
            cand = base / p
            if cand.exists():
                return cand
        return None

    def _shift_events(self, events, dx: int, dy: int):
        """把脚本里的绝对坐标整体平移，用于把录制好的子脚本贴到监控命中位置。"""
        out = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            ev = copy.deepcopy(ev)
            typ = str(ev.get("type", "event")).lower()
            if typ == "event" and str(ev.get("event_type", "")).upper() == "EM":
                a = ev.get("action")
                if isinstance(a, (list, tuple)) and len(a) >= 2:
                    if isinstance(a[0], int) and isinstance(a[1], int) and not (a[0] == -1 and a[1] == -1):
                        ev["action"] = [a[0] + dx, a[1] + dy] + list(a[2:])
            elif typ == "monitor":
                r = ev.get("region")
                if isinstance(r, (list, tuple)) and len(r) == 4:
                    ev["region"] = [int(r[0]) + dx, int(r[1]) + dy, r[2], r[3]]
                for key in ("script_anchor",):
                    v = ev.get(key)
                    if isinstance(v, (list, tuple)) and len(v) == 2:
                        ev[key] = [int(v[0]) + dx, int(v[1]) + dy]
            for key in ("on_match", "on_timeout"):
                if isinstance(ev.get(key), list):
                    ev[key] = self._shift_events(ev[key], dx, dy)
            if typ == "sequence" and isinstance(ev.get("events"), list):
                ev["events"] = self._shift_events(ev["events"], dx, dy)
            out.append(ev)
        return out

    # ------------------------------------------------------------ helpers
    def _wait(self, ms: float) -> bool:
        remaining = max(0.0, float(ms)) / 1000.0 / self.speed
        while remaining > 0:
            if self._stop.is_set():
                return False
            if self._paused.is_set():
                time.sleep(0.05)
                continue
            step = min(0.02, remaining)
            time.sleep(step)
            remaining -= step
        return not self._stop.is_set()

    def _release_all(self) -> None:
        if self.dry_run:
            self._pressed_buttons.clear()
            self._pressed_keys.clear()
            return
        for b in list(self._pressed_buttons):
            try:
                mouse_up(b)
            except Exception:
                pass
        for vk, ext in list(self._pressed_keys):
            try:
                key_up(vk, ext)
            except Exception:
                pass
        self._pressed_buttons.clear()
        self._pressed_keys.clear()



